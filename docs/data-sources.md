# Data sources — what feeds what

ccmon never assumes a fixed Issabel schema. `ccmon/sources/capabilities.py`
probes databases/tables/columns (and AMI) every 300 s; queries are selected
from the probe result, and anything missing degrades to an empty panel with
the feature flagged in `/api/v1/meta/capabilities` (visible under
*Settings → Capabilities* in the dashboard).

## Matrix

| Feature | Primary source | Fallback / degradation |
| --- | --- | --- |
| Queue waiting/active counts | AMI `QueueStatus` | `call_center` queue tables when AMI off |
| Queue rolling SLA/wait/abandoned (15 min) | `asterisk.queue_log` | demo source; panel shows 0 + stale flag |
| Queue membership, paused flags | AMI `QueueStatus` members | `asterisk.queue_member.paused` merged by device |
| Agent sessions (logged-in) | `call_center.audit` (`id_break IS NULL` = session, `NOT NULL` = pause) | AMI member state only |
| Agent pauses + break names | `call_center.audit` + `call_center.break` | `queue_member.paused` boolean only |
| Active calls / channels | AMI `CoreShowChannels` | empty list |
| Campaigns (status, today counters) | `call_center.campaign`, `campaign_entry`, `calls` (column-tolerant probes) | campaign panel hidden/empty |
| Historical reports (summary/series) | `asterisk.queue_log` | — |
| Abandoned detail | `asterisk.queue_log` (`ABANDONED`/`ABANDON`) | — |
| Report summary (CDR fallback) | `asteriskcdrdb.cdr` — used only when `queue_log` is missing (traffic-only: no SLA/abandon) | empty summary, `source: "none"` |
| Trunk registration | AMI `PJSIPShowEndpoints` | `SIPshowpeers` (chan_sip) |
| Service health | `systemctl is-active` (unprivileged) | status `unknown` if systemd absent |
| Collector freshness | `collector_heartbeat` in app DB | API sets `stale: true` |

## Schema drift

Issabel releases vary column names (`calls.start_time` vs `fecha_llamada`,
`campaign.name` vs `title`, `queue_log.time` as int vs varchar). ccmon:

1. probes `information_schema` for candidate columns,
2. selects only known-good columns,
3. otherwise disables that feature (capability flag) instead of erroring.

`queue_log.time` is always compared through `CAST(time AS UNSIGNED)` so both
storage variants work (at the cost of index use — see
`deploy/sql/optimize_issabel_indexes.sql`).

## AMI usage (read-only)

Sent actions: `Login`, `QueueStatus`, `CoreShowChannels`,
`PJSIPShowEndpoints` / `SIPshowpeers`, `Logoff`. No `Originate`, no
`Command`, no `Write` privileges in `manager.conf` — see
`deploy/ami/ccmon_manager.conf.example`.

## MySQL access rules

- User `ccmon_reader`, `GRANT SELECT` on `asteriskcdrdb`, `asterisk`,
  `call_center` only (`deploy/sql/readonly_grants.sql`).
- Every query is a single SELECT with bound parameters and explicit time
  bounds; row caps (`ISSABEL_DB_MAX_ROWS`) apply to every result set.
- Tests (`tests/test_sql_dialect.py`) parse every query with sqlglot in
  MySQL mode and fail the build if any is not SELECT-only or loses its time
  bound.

## App database

PostgreSQL in production (`DATABASE_URL`), SQLite for dev/tests. It stores
only ccmon's own data: snapshots, rollups, rules, events, users, sessions,
tokens, audit — never a copy of CDR rows beyond what reports return.
