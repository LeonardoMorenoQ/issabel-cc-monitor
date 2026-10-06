"""ccmon CLI: serve | collect | init-db | create-admin | create-token | ..."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from ccmon.config import Settings

log = logging.getLogger("ccmon")


def build_source(settings: Settings):
    if settings.app_mode == "demo":
        from ccmon.sources.demo_source import DemoSource

        return DemoSource(settings)
    from ccmon.sources.mysql_source import MysqlSource

    return MysqlSource(settings)


def bootstrap(settings: Settings):
    """Configure engine, apply migrations, return nothing."""
    from ccmon.db import configure_engine
    from ccmon.migrations import migrate

    engine = configure_engine(settings.database_url)
    applied = migrate(engine)
    if applied:
        log.info("migrations applied: %s", ", ".join(applied))


def _ensure_admin(settings: Settings, username: str, password: str) -> bool:
    from ccmon.auth import hash_password
    from ccmon.db import session_scope
    from ccmon.models import User

    with session_scope() as session:
        if session.query(User).filter(User.username == username).first():
            return False
        session.add(
            User(username=username, full_name="Administrator",
                 password_hash=hash_password(password), active=True, is_superadmin=True)
        )
    log.info("admin user created: %s", username)
    return True


def cmd_init_db(settings: Settings, args) -> int:
    bootstrap(settings)
    if settings.admin_password.get_secret_value():
        _ensure_admin(settings, settings.admin_username, settings.admin_password.get_secret_value())
    else:
        count = _user_count(settings)
        if count == 0:
            log.warning("no users exist; run `ccmon create-admin` or set ADMIN_PASSWORD")
    print("database ready")
    return 0


def _user_count(settings: Settings) -> int:
    from ccmon.db import session_scope
    from ccmon.models import User

    with session_scope() as session:
        return session.query(User).count()


def cmd_create_admin(settings: Settings, args) -> int:
    bootstrap(settings)
    password = args.password or settings.admin_password.get_secret_value()
    if not password:
        print("error: provide --password or ADMIN_PASSWORD", file=sys.stderr)
        return 2
    if len(password) < 8:
        print("error: password too short (min 8)", file=sys.stderr)
        return 2
    created = _ensure_admin(settings, args.username, password)
    if not created:
        print(f"user {args.username} already exists (not modified)")
    return 0


def cmd_create_token(settings: Settings, args) -> int:
    from ccmon.auth import new_api_token
    from ccmon.db import session_scope
    from ccmon.models import ApiToken

    bootstrap(settings)
    raw, token_hash = new_api_token()
    with session_scope() as session:
        session.add(
            ApiToken(name=args.name, token_hash=token_hash,
                     scopes=",".join(s.strip() for s in args.scopes.split(",") if s.strip()))
        )
    print(raw)
    return 0


def cmd_serve(settings: Settings, args) -> int:
    import uvicorn

    from ccmon.api.app import create_app

    bootstrap(settings)
    source = build_source(settings)
    app = create_app(settings, source)
    uvicorn.run(app, host=settings.app_host, port=settings.app_port, log_config=None)
    return 0


def cmd_collect(settings: Settings, args) -> int:
    from ccmon.alerts.telegram import TelegramSender
    from ccmon.collector.runner import run

    bootstrap(settings)
    source = build_source(settings)
    asyncio.run(run(settings, source, TelegramSender(settings)))
    return 0


def cmd_prune(settings: Settings, args) -> int:
    from ccmon.collector.rollups import prune
    from ccmon.db import session_scope

    bootstrap(settings)
    with session_scope() as session:
        print(f"pruned {prune(session, settings)} rows")
    return 0


def cmd_seed_demo(settings: Settings, args) -> int:
    if not args.yes:
        print("Refusing: seed-demo writes sample users/rules to the APP database only.\n"
              "Re-run with --yes after reading docs/install-*.md. It never touches\n"
              "Issabel/Asterisk databases (the source user is read-only anyway).")
        return 2
    from ccmon.db import session_scope
    from ccmon.models import AlertRule

    bootstrap(settings)
    admin_password = settings.admin_password.get_secret_value() or "demo-change-me-now"
    _ensure_admin(settings, settings.admin_username, admin_password)
    samples = [
        ("queue_waiting_gt", "Queue 800 overloaded", {"queue": "800", "threshold": 5}),
        ("agent_paused_minutes_gt", "Agent paused too long", {"min_minutes": 15}),
        ("no_agents_available", "No agents ready", {"queue": "800"}),
        ("service_down", "Asterisk service problem", {"component": "asterisk"}),
    ]
    with session_scope() as session:
        existing = {r.type for r in session.query(AlertRule).all()}
        for rule_type, name, params in samples:
            if rule_type in existing:
                continue
            session.add(AlertRule(type=rule_type, name=name, params=params,
                                  severity="warning", for_seconds=60, cooldown_minutes=10))
    print(f"demo seeded. login: {settings.admin_username} / {admin_password}")
    print("start with:  APP_MODE=demo ccmon serve   and   APP_MODE=demo ccmon collect")
    return 0


def cmd_check(settings: Settings, args) -> int:
    source = build_source(settings)
    caps = source.capabilities()
    for key, value in caps.as_dict().items():
        print(f"{key}: {value}")
    snap = source.live_snapshot()
    print(f"live_snapshot: queues={len(snap['queues'])} agents={len(snap['agents'])} "
          f"campaigns={len(snap['campaigns'])} trunks={len(snap['trunks'])} "
          f"source_ok={snap['source_ok']} errors={snap['errors']}")
    return 0 if snap["source_ok"] else 1


def main(argv: list[str] | None = None) -> int:
    from ccmon.logging_setup import setup_logging

    parser = argparse.ArgumentParser(
        prog="ccmon", description="Issabel/Asterisk call-center monitor"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the API + dashboard (uvicorn)")
    sub.add_parser("collect", help="run the collector loops")
    sub.add_parser("init-db", help="create schema (+ bootstrap admin from env)")
    sub.add_parser("prune", help="run retention pruning once")
    sub.add_parser("check", help="probe sources and print capabilities")
    p_admin = sub.add_parser("create-admin", help="create a superadmin user")
    p_admin.add_argument("--username", required=True)
    p_admin.add_argument("--password", default=None)
    p_token = sub.add_parser("create-token", help="create an API token (prints raw once)")
    p_token.add_argument("--name", required=True)
    p_token.add_argument("--scopes", default="read")
    p_seed = sub.add_parser("seed-demo", help="create demo admin + sample alert rules")
    p_seed.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    settings = Settings()
    setup_logging(settings.log_level, settings.redaction_strings())
    handlers = {
        "serve": cmd_serve, "collect": cmd_collect, "init-db": cmd_init_db,
        "create-admin": cmd_create_admin, "create-token": cmd_create_token,
        "seed-demo": cmd_seed_demo, "prune": cmd_prune, "check": cmd_check,
    }
    return handlers[args.command](settings, args)


if __name__ == "__main__":
    sys.exit(main())
