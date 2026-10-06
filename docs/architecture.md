# Architecture

## Overview

```
                       ┌────────────────────────────────────────────┐
                       │ Issabel 5 PBX (Rocky 8)                    │
  operators ──HTTPS──▶ │ httpd (TLS) ──proxy──▶ 127.0.0.1:8080      │
                       │   /ccmon/          FastAPI app  (serve)    │
                       │                        │                   │
                       │                        ▼                   │
                       │              app DB (PostgreSQL)           │
                       │                 snapshots · rollups        │
                       │                 rules · events · audit     │
                       │                                            │
                       │  ccmon collect (systemd, separate proc)    │
                       │     │ 5s live / 60s history / hourly rollup│
                       │     ├─▶ MySQL read-only ── queue_log, CDR, │
                       │     │                   └ call_center      │
                       │     ├─▶ AMI read-only ──── QueueStatus,    │
                       │     │                     CoreShowChannels │
                       │     ├─▶ systemctl is-active ─ services     │
                       │     └─▶ Telegram ────────── alerts (opt.)  │
                       └────────────────────────────────────────────┘
```

Two long-running processes share one codebase and one app database:

| Process | Command | Cadence |
| --- | --- | --- |
| API + dashboard | `ccmon serve` | on demand (uvicorn, single worker) |
| Collector | `ccmon collect` | live 5 s, history 60 s, rollups + prune hourly |

They never share memory — only the database — so either can restart
independently; the API reports data **staleness** from the collector
heartbeat instead of guessing.

## Data flow

1. **Collect** (`ccmon/collector/`): each live cycle asks the configured
   source (demo / MySQL+AMI) for a normalized snapshot — queues, agents,
   campaigns, calls, trunks, service health — and upserts the newest row per
   entity into `*_snapshots`, touching `collector_heartbeat`.
2. **Roll up** (`collector/rollups.py`): hourly buckets (queue + agent) are
   rebuilt idempotently (delete bucket + insert), so a crash mid-write never
   corrupts history; retention pruning runs in the same maintenance cycle.
3. **Alert** (`ccmon/alerts/`): rules are evaluated against the latest
   snapshots with an explicit three-valued result — *true*, *false*, or
   **insufficient data**. Insufficient data (stale source, unknown component)
   causes no transition: alerts cannot fire or resolve on missing data.
   Firing respects `for_seconds` sustain and `cooldown_minutes`.
4. **Serve** (`ccmon/api/`): FastAPI reads only from the app DB (never from
   MySQL/AMI directly), so browser traffic cannot touch PBX data sources.

## Source layer (`ccmon/sources/`)

- `capabilities.py` probes DBs/tables/columns/AMI every 300 s → feature flags
  + query choice (column-tolerant SQL for schema drift between releases).
- `mysql_ro.py` — dedicated read-only pool, row caps, timeouts, backoff.
- `ami.py` — asyncio client; only Login/QueueStatus/CoreShowChannels/
  PJSIPShowEndpoints/SIPshowpeers/Logoff are ever sent.
- `demo_source.py` — deterministic synthetic 30-day history for tests/demo.
- `queries/` — MySQL-dialect SQL with bound parameters (sqlglot-verified in
  `tests/test_sql_dialect.py`: SELECT-only + time bounds on every query).

## Security architecture (summary — see docs/security.md)

- App binds `127.0.0.1`; exposure is httpd TLS proxy only.
- Two DB users: **PostgreSQL app owner** (rw, own schema) and **MySQL
  ccmon_reader** (SELECT-only on the three source databases).
- Interactive auth = argon2 + DB sessions (httpOnly cookie, hashed at rest);
  machine auth = scoped bearer tokens (hashed at rest). Cookie mutations
  require the `X-CCMon-CSRF` header; bearer calls are exempt (no ambient
  credential).
- Rate limits: per-IP and per-token sliding windows, stricter login limiter.
- Logs are JSON and pass through a redaction filter applied to the *final*
  message, so tracebacks cannot leak DSNs/tokens/secrets.

## Repository layout

```
ccmon/            Python package (config, db, models, auth, sources,
                  collector, alerts, api) + CLI (__main__.py)
dashboard/        React + Vite SPA (npm run build -> dashboard/dist,
                  served by the API with an SPA fallback)
deploy/           systemd units, httpd proxy conf, SQL (grants, indexes),
                  AMI example, optional Docker
docs/             install, architecture, data-sources, security, backup
hermes/           tool definitions + example prompts for an AI assistant
tests/            pytest: security units, alert engine, SQL dialect, API
```

## Design choices

- **Forward-only migrations** (`migrations.py`): the schema is created with
  `Base.metadata.create_all` + a `schema_version` row; no downgrades, no
  Alembic dependency. Safe for single-instance installs.
- **Single uvicorn worker**: rate limiters are in-process
  (`ratelimit.py`); scaling out would need a shared store — documented, not
  silently wrong.
- **No ORM on the source side**: sources are read-only SQL over a pool;
  the app DB uses SQLAlchemy models. Keeps PBX access auditable.
- **Subpath-safe SPA**: relative asset URLs + hash routing, so the app works
  at `/` and `/ccmon/` without rebuilds.
