# ccmon — hardened Issabel / Asterisk call-center monitor

Real-time monitoring, historical reports and alerting for the **Issabel 5**
call center (Asterisk 16/18, Rocky 8): live queues, agents, campaigns,
trunks, SLA/abandoned KPIs, Telegram alerts — served as a FastAPI backend
plus a small React dashboard, bound to loopback and exposed through the
existing Issabel httpd TLS reverse proxy at `https://<pbx>/ccmon/`.

Inspired by the *idea* of
[orlandopy31/issabel-callcenter-monitor](https://github.com/orlandopy31/issabel-callcenter-monitor);
this is an independent rewrite with different architecture and a hardened
security posture (read-only sources, no secrets in the repo, no `chmod 777`,
no `curl | bash` installer).

## Features

- **Live view** (5 s polling): queues (waiting/active/SLA/wait/rolling
  counters), agents (state, pause age, handled today), campaigns, active
  calls, service + trunk health, collector heartbeats — with an explicit
  **staleness banner** when the collector stops.
- **Reports**: summary, time series (hour/day buckets), per-agent, per-campaign
  and abandoned-call detail from `asterisk.queue_log` / `asteriskcdrdb.cdr` /
  `call_center.*` — validated ranges, parameterized SQL, SELECT-only DB user.
- **Alerts**: 6 rule types (queue waiting/abandoned, agent paused too long,
  no agents available, trunk not registered, service down) with strict param
  schemas, sustain window, cooldown, insufficient-data safety (stale data can
  neither fire nor resolve), Telegram notifications with retry/backoff.
- **Security**: argon2 password hashes, DB-backed sessions (cookie) + scoped
  API tokens, CSRF header on cookie mutations, per-IP/token sliding-window
  rate limits, login throttling, audit trail, JSON logs with secret redaction
  (including tracebacks), production fail-fast on placeholder secrets.
- **Sources**: MySQL/MariaDB read-only, AMI read-only actions only, and a
  built-in `demo` mode with synthetic data so you can try everything without
  a PBX. Capabilities probing degrades gracefully per schema variant.

## Quickstart (demo mode — no PBX needed)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.lock
cp .env.example .env            # then edit: APP_MODE=demo
ccmon seed-demo --yes           # demo admin + sample alert rules (APP DB only)
APP_MODE=demo ccmon serve       # API + dashboard on http://127.0.0.1:8080/
APP_MODE=demo ccmon collect     # in a second terminal: the collector loops
```

Open <http://127.0.0.1:8080/> and sign in with the credentials printed by
`seed-demo` (default `admin` / `demo-change-me-now` — change it).

> Windows note: use `.venv/Scripts/python -m ccmon ...` if the console entry
> point is not on PATH.

## Production install

See **[docs/install-issabel5.md](docs/install-issabel5.md)** for the full
Issabel 5 (Rocky 8) walkthrough: read-only DB user, AMI user, PostgreSQL app
database, hardened systemd units and the httpd TLS proxy at `/ccmon/`.
Ubuntu/Debian operators: [docs/install-ubuntu-debian.md](docs/install-ubuntu-debian.md).

## Documentation

| Doc | Contents |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | Components, data flow, collector loops, repo layout |
| [docs/install-issabel5.md](docs/install-issabel5.md) | Production install on Issabel 5 |
| [docs/install-ubuntu-debian.md](docs/install-ubuntu-debian.md) | Standalone install on Ubuntu/Debian |
| [docs/data-sources.md](docs/data-sources.md) | Which table/AMI action feeds which feature (+ fallbacks) |
| [docs/security.md](docs/security.md) | Threat model, hardening checklist, what is deliberately out of scope |
| [docs/backup-restore.md](docs/backup-restore.md) | What to back up, how to restore, upgrade notes |
| [hermes/](hermes/) | Tool definitions + example prompts for the Hermes assistant |

## Development

```bash
pytest                      # 51 tests: security units, alerts, SQL dialect, API
ruff check ccmon tests      # lint
cd dashboard && npm install && npm run build   # React SPA -> dashboard/dist
```

Configuration: [.env.example](.env.example) documents every key. The app
binds `127.0.0.1` only; never expose `APP_HOST` beyond loopback without a
TLS reverse proxy in front.

## License / status

Personal/internal project. Docker files under `deploy/docker/` are an
optional secondary artifact; the supported install is bare systemd.
