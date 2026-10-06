# Security

## Threat model (what this defends against)

| Threat | Control |
| --- | --- |
| Attacker reaches the app from the network | Binds `127.0.0.1` only; exposure = httpd TLS proxy at `/ccmon/` |
| Stolen app DB / backup leaks credentials | argon2 password hashes; session + API-token *hashes* at rest; secrets never stored |
| SQL injection via report filters | All SQL parameterized; ranges/queue/bucket validated by Pydantic; sqlglot tests assert SELECT-only + time bounds |
| Compromised app process → PBX data | MySQL user is `SELECT`-only on 3 DBs; AMI user has **no write** privileges, loopback-only |
| CSRF against cookie sessions | Mutations require `X-CCMon-CSRF` custom header (cross-origin JS cannot set it without a preflight the app never grants); bearer requests exempt by design |
| Credential stuffing | argon2 + per-IP login limiter (`LOGIN_RATE_PER_MINUTE=5`) + audit trail |
| API abuse | Sliding-window limits per IP (`RATE_LIMIT_IP_PER_MINUTE`) and per token (`RATE_LIMIT_TOKEN_PER_MINUTE`) |
| Secret leakage through logs | JSON logging with a redaction filter applied to the **formatted** message — including tracebacks (`tests/test_security_units.py`) |
| Placeholder secrets shipped to prod | `Settings` fails fast in `APP_MODE=production` on placeholder DSNs/AMI secrets |
| Clickjacking / MIME sniffing | `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, CSP `default-src 'self'`, `Referrer-Policy: no-referrer` |
| Dashboard XSS from PBX data | React escapes by default; no `dangerouslySetInnerHTML`; Telegram messages HTML-escaped server-side |

## Hardening checklist (already in the repo)

- [x] No secrets in the repo; `.env` git-ignored; `.env.example` placeholders only.
- [x] systemd units: `ccmon` unprivileged user, `NoNewPrivileges`, `ProtectSystem=strict`,
      `CapabilityBoundingSet=` (empty), `SystemCallFilter=@system-service`, `UMask=0077`.
- [x] No `curl | bash` installer, no `chmod -R 0777` anywhere.
- [x] Rate limiting + CSRF + security headers in one middleware (`ccmon/api/app.py`).
- [x] Admin mutations (users/tokens) require an **interactive superadmin session** —
      bearer tokens (even with `rules:write`) get `403` on `/api/v1/admin/*`.
- [x] OpenAPI docs disabled by default in production (`OPENAPI_ENABLED=false`).
- [x] Row caps (`ISSABEL_DB_MAX_ROWS`) and report guards (`REPORT_MAX_DAYS=92`).

## Deliberately out of scope

- **Supervision / ChanSpy / call recording** — AMI is read-only by design;
  no eavesdropping surface exists.
- **Multi-instance horizontal scaling** — rate limiters are in-process;
  run one instance (documented in architecture.md).
- **SSO/LDAP** — local users + API tokens only (keep scope small).

## Operational advice

- Keep `/etc/ccmon/ccmon.env` mode `600`, owned by `ccmon`.
- Rotate: AMI secret + MySQL password together with `ccmon_` API tokens
  (tokens are shown once, revocable in *Settings → API tokens*).
- Watch the audit trail (*Settings → Audit trail*) for repeated failed
  logins or unexpected token creation — every auth event is recorded with IP.
- Terminate TLS at httpd with a valid certificate; the app speaks plain
  HTTP on loopback only.
- `TELEGRAM_BOT_TOKEN` grants message-send rights to your bot — restrict the
  bot via `/setprivacy` and do not reuse the token elsewhere.

## Reporting

This is an internal project; report issues to the repository owner.
