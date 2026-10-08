# Production install — Issabel 5 (Rocky 8)

Target: Issabel 5 on Rocky 8 with Asterisk 16/18, co-located on the PBX,
exposed only at `https://<pbx>/ccmon/` through the existing httpd TLS proxy.
Issabel 4 is **untested** — deltas are noted at the end.

## 1. System packages

```bash
sudo dnf install -y python3 python3-pip git httpd mod_proxy mod_proxy_http
# PostgreSQL (app database; MariaDB is already present for Issabel)
sudo dnf install -y postgresql-server
sudo postgresql-setup --initdb
sudo systemctl enable --now postgresql
```

Python ≥ 3.11 required (tested on 3.13). If Rocky's python is older, add
the EPEL/SCL python you prefer and use its `python3 -m venv` below.

## 2. Application user and code

```bash
sudo useradd --system --home /opt/ccmon --shell /usr/sbin/nologin ccmon
sudo mkdir -p /opt/ccmon /etc/ccmon
sudo git clone <this-repo> /opt/ccmon/src
sudo cp -r /opt/ccmon/src/{ccmon,dashboard,pyproject.toml,requirements.lock} /opt/ccmon/
cd /opt/ccmon
sudo python3 -m venv .venv
sudo .venv/bin/pip install --no-cache-dir -r requirements.lock
sudo .venv/bin/pip install --no-cache-dir .
sudo chown -R ccmon:ccmon /opt/ccmon
```

## 3. App database (PostgreSQL)

```bash
sudo -u postgres psql <<'SQL'
CREATE ROLE ccmon LOGIN PASSWORD 'CHANGE_ME_APP_DB_PASSWORD';
CREATE DATABASE ccmon OWNER ccmon;
SQL
```

## 4. Read-only source user (MariaDB)

```bash
sudo mysql < /opt/ccmon/src/deploy/sql/readonly_grants.sql   # edit password first!
```

## 5. Read-only AMI user

Append `deploy/ami/ccmon_manager.conf.example` to
`/etc/asterisk/manager.conf` (set a strong secret), then:

```bash
asterisk -rx "reload manager"
```

## 6. Configuration

```bash
sudo cp /opt/ccmon/src/.env.example /etc/ccmon/ccmon.env
sudo chmod 600 /etc/ccmon/ccmon.env
sudo chown ccmon:ccmon /etc/ccmon/ccmon.env
sudo vi /etc/ccmon/ccmon.env
```

Set at minimum: `APP_MODE=production`, `DATABASE_URL`
(`postgresql+psycopg://ccmon:...@127.0.0.1:5432/ccmon`), `ISSABEL_DB_PASSWORD`,
`AMI_SECRET`, and a strong `ADMIN_PASSWORD` (or leave empty and create the
admin with the CLI below).

## 7. Database schema + admin

Load the env file safely (the `env $(grep … | xargs)` variant word-splits
values containing spaces or quotes; this loader passes the file through
bash's `set -a` instead). Paste once per shell:

```bash
run_ccmon() {
  sudo -u ccmon bash -c 'set -a; . /etc/ccmon/ccmon.env; set +a; exec /opt/ccmon/.venv/bin/ccmon "$@"' _ "$@"
}
```

```bash
run_ccmon init-db
run_ccmon create-admin --username admin --password 'STRONG_PASSWORD'
```

Verify source connectivity:

```bash
run_ccmon check   # must exit 0 with source_ok=True
```

## 8. Dashboard build (optional but recommended)

```bash
cd /opt/ccmon && sudo -u ccmon bash -c \
  'cd dashboard && npm ci && npm run build'
```

Without it the API runs in API-only mode (a warning is logged).

## 9. systemd

```bash
sudo cp /opt/ccmon/src/deploy/systemd/{ccmon.service,ccmon-collector.service,ccmon.target} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ccmon.target
systemctl status ccmon ccmon-collector
journalctl -u ccmon-collector -f      # collector logs (JSON, redacted)
```

## 10. httpd TLS reverse proxy

```bash
sudo cp /opt/ccmon/src/deploy/httpd/ccmon-proxy.conf /etc/httpd/conf.d/ccmon.conf
sudo httpd -t && sudo systemctl reload httpd
```

Open `https://<pbx>/ccmon/` — you should see the ccmon login page. The app
still binds `127.0.0.1:8080` only; verify with `ss -tlnp | grep 8080`.

## 11. Optional hardening extras

- `sudo mysql < /opt/ccmon/src/deploy/sql/optimize_issabel_indexes.sql`
  (report performance on busy systems).
- Tune `RATE_LIMIT_IP_PER_MINUTE` if many operators share one NAT IP.
- Firewall: no new ports needed (loopback + httpd already open).

## Issabel 4 deltas (UNTESTED — documented only)

- Paths/units may reference `issabel` instead of module names above.
- `chan_sip` is the default channel driver → AMI falls back to `SIPshowpeers`.
- The `call_center` schema differs in some releases; capabilities probing
  degrades features instead of failing. Validate with `ccmon check`.
