# Hermes — ccmon tools for an AI assistant

`tools.json` defines four function-calling tools that map 1:1 to the ccmon
read-only endpoints. Give them to your assistant together with a system note
like:

> You are a call-center operations assistant. Use the ccmon_* tools for any
> question about queues, agents, SLA or abandoned calls. Always relay the
> `summary_text` faithfully and mention the `stale` flag when it is true.
> Never invent numbers that the tools did not return.

## Endpoint mapping & auth

| Tool | Endpoint | Method |
| --- | --- | --- |
| `ccmon_queue_health` | `/api/v1/hermes/queue-health` | GET |
| `ccmon_agents_paused` | `/api/v1/hermes/agents/paused?min_minutes=…` | GET |
| `ccmon_abandoned_calls` | `/api/v1/hermes/abandoned/today?date=…` | GET |
| `ccmon_report_summary` | `/api/v1/reports/summary?date_from=…&date_to=…` | GET |

Authenticate with a read-scoped API token (create one in *Settings → API
tokens* or with `ccmon create-token --name hermes --scopes read`):

```bash
curl -H "Authorization: Bearer ccmon_..." \
  "https://pbx/ccmon/api/v1/hermes/queue-health"
```

Tokens are shown once and can be revoked any time. Read scope cannot modify
rules, users, or tokens.

## Example prompts

### 1. "How are the queues right now?"

Assistant calls `ccmon_queue_health`, then answers with the returned
`summary_text`, e.g.:

> 800: 4 waiting, 2 active, 3 agents free, SLA 85% — 801: 0 waiting,
> 0 active, 1 agent free, SLA 100%. *(data collected 3 s ago)*

### 2. "Which agents have been parked on break for more than 20 minutes?"

Assistant calls `ccmon_agents_paused` with `min_minutes: 20`, then:

> One agent has been paused ≥ 20 min: agent 103 (Carol) at 42.3 minutes.
> Consider checking in with them.

### 3. "How many calls did we abandon yesterday?"

Assistant computes yesterday's date and calls `ccmon_abandoned_calls` with
that `date`, then:

> 12 abandoned call(s) on 2026-10-05 (800: 8, 801: 4), average wait 47.3s.

### 4. "Summarize call volume and SLA for the last 7 days."

Assistant calls `ccmon_report_summary` with `date_from` = seven days ago and
`date_to` = today, then:

> Over the last 7 days: 412 offered, 366 answered (88.8%), 31 abandoned
> (7.5%), average wait 11.4s, SLA 82.1% against the 20s/80% target.

## Guardrails the API enforces regardless of the assistant

- Ranges ≤ 92 days, no future dates → `422` with a precise message.
- `stale: true` on live data when the collector heartbeat is old — the
  assistant should surface this instead of repeating stale numbers as live.
- Rate limits apply per token (`RATE_LIMIT_TOKEN_PER_MINUTE`); back off on
  `429` and respect `Retry-After`.
