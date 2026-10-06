"""Forward-only numbered migrations tracked in schema_version."""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from ccmon.models import Base

log = logging.getLogger(__name__)


def _0001_initial(conn: Connection) -> None:
    Base.metadata.create_all(conn)


MIGRATIONS: list[tuple[str, object]] = [
    ("0001_initial_schema", _0001_initial),
]

_VERSION_DDL = (
    "CREATE TABLE IF NOT EXISTS schema_version ("
    "version VARCHAR(64) PRIMARY KEY, applied_at VARCHAR(40) NOT NULL)"
)


def migrate(engine: Engine) -> list[str]:
    """Apply pending migrations; returns the list of versions applied now."""
    from ccmon.db import utcnow

    applied_now: list[str] = []
    with engine.begin() as conn:
        conn.exec_driver_sql(_VERSION_DDL)
        rows = conn.execute(text("SELECT version FROM schema_version")).fetchall()
        applied = {row[0] for row in rows}
        for version, fn in MIGRATIONS:
            if version in applied:
                continue
            log.info("applying migration %s", version)
            fn(conn)  # type: ignore[operator]
            conn.execute(
                text("INSERT INTO schema_version (version, applied_at) VALUES (:v, :t)"),
                {"v": version, "t": utcnow().isoformat()},
            )
            applied_now.append(version)
    return applied_now
