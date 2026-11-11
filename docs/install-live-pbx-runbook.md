# Live PBX runbook — step-by-step install on Issabel 5 (Rocky 8)

This is the **gated, copy-paste version** of
[install-issabel5.md](install-issabel5.md): same steps, but every step ends
with a **Verify** command whose expected output is spelled out. Run them in
order; **stop at the first failing Verify** and use
[Troubleshooting](#troubleshooting) before continuing.

- **Target:** Issabel 5 (Rocky 8, Asterisk 16/18), app co-located on the PBX.
- **Exposure:** loopback only + existing httpd TLS proxy at `https://<pbx>/ccmon/`.
- **Time:** ~45–60 min. **Risk:** ccmon is additive — it creates read-only
  accounts and one httpd snippet; it never writes to Issabel/Asterisk data.
- **Issabel 4:** untested (deltas at the end).

```bash
set -euo pipefail
REPO=https://github.com/LeonardoMorenoQ/issabel-cc-monitor.git
PBX=<the PBX hostname/IP>
```

---

## Phase 0 — pre-flight, secrets, backup

```bash
# Baseline — record it (you'll compare after install)
cat /etc/os-release | head -2
asterisk -rx "core show version" | head -2
systemctl is-active asterisk mariadb httpd
df -h /opt /var | tail -2

# Backups (insurance; ccmon does not modify these)
sudo mkdir -p /root/ccmon-preinstall
sudo tar czf /root/ccmon-preinstall/etc-asterisk.tgz /etc/asterisk
sudo cp -a /etc/httpd/conf.d /root/ccmon-preinstall/httpd-conf.d
sudo mysqldump --databases asterisk asteriskcdrdb call_center 2>/dev/null \
  | gzip > /root/ccmon-preinstall/source-dbs.sql.gz

# Generate all three secrets up front; Phase 4/5/6 read them back from here.
umask 077
{
  echo "APPDB_PW=$(openssl rand -hex 16)"
  echo "RO_PW=$(openssl rand -hex 16)"      # MariaDB read-only user
  echo "AMI_PW=$(openssl rand -hex 16)"     # Asterisk manager user
} | sudo tee /root/ccmon-preinstall/secrets.env > /dev/null
chmod 600 /root/ccmon-preinstall/secrets.env
```

**Verify**

```bash
ls -l /root/ccmon-preinstall/            # etc-asterisk.tgz, httpd-conf.d, source-dbs.sql.gz, secrets.env
grep -c '_PW=' /root/ccmon-preinstall/secrets.env   # → 3
```

---

## Phase 1 — packages and Python ≥ 3.11

> **Rocky 8 gotcha (the #1 blocker):** the system `python3` is 3.6 — ccmon
> needs **≥ 3.11** (`requires-python = ">=3.11"`).

```bash
sudo dnf install -y git httpd openssl-devel
sudo dnf install -y postgresql-server
sudo postgresql-setup --initdb
sudo systemctl enable --now postgresql

# Prefer a distro Python ≥ 3.11:
dnf module list python3 2>/dev/null | grep -E 'python3\.(1[1-9])' || true
```

- If a `python3.11` (or newer) stream/package is listed:

  ```bash
  sudo dnf module install -y python3.11/common 2>/dev/null \
    || sudo dnf install -y python3.11
  PY=$(command -v python3.11 || command -v python3.12)
  ```

- **If none is available**, build from the official source tarball (no
  third-party repos, hardened posture):

  ```bash
  sudo dnf install -y gcc make openssl-devel bzip2-devel libffi-devel \
    zlib-devel readline-devel sqlite-devel xz-devel
  curl -fsSLO https://www.python.org/ftp/python/3.12.7/Python-3.12.7.tgz
  tar xf Python-3.12.7.tgz && cd Python-3.12.7
  ./configure --prefix=/opt/python3.12 --with-ensurepip=install
  make -j"$(nproc)" && sudo make install && cd ..
  PY=/opt/python3.12/bin/python3
  ```

**Verify**

```bash
$PY -c 'import sys; assert sys.version_info >= (3,11), sys.version; print(sys.version)'  # 3.11+
systemctl is-active postgresql                                   # active
httpd -M 2>/dev/null | grep -E 'proxy_module|proxy_http_module'  # both listed (bundled with httpd)
```

---

## Phase 2 — application user, code, virtualenv

```bash
sudo useradd --system --home /opt/ccmon --shell /usr/sbin/nologin ccmon
sudo mkdir -p /opt/ccmon /etc/ccmon
sudo git clone "$REPO" /opt/ccmon/src
sudo cp -r /opt/ccmon/src/{ccmon,dashboard,pyproject.toml,requirements.lock,.env.example} /opt/ccmon/
cd /opt/ccmon
sudo "$PY" -m venv .venv
sudo .venv/bin/pip install --no-cache-dir --upgrade pip
sudo .venv/bin/pip install --no-cache-dir -r requirements.lock
sudo .venv/bin/pip install --no-cache-dir .
sudo chown -R ccmon:ccmon /opt/ccmon
```

> **No-git alternative** (from your workstation, when the PBX has no outbound git access):
> `git archive --format=tar.gz -o ccmon-src.tgz HEAD` then
> `scp ccmon-src.tgz root@$PBX:/tmp/` and on the PBX:
> `sudo mkdir -p /opt/ccmon/src && sudo tar -C /opt/ccmon/src -xzf /tmp/ccmon-src.tgz`

**Verify**

```bash
sudo -u ccmon /opt/ccmon/.venv/bin/ccmon --help | head -1        # "usage: ccmon [-h]"
sudo -u ccmon /opt/ccmon/.venv/bin/python -c 'import ccmon, fastapi, pymysql, psycopg; print("IMPORTS_OK")'
```

*(Note: only `--help` is safe before Phase 6 — any other command fails fast
until real secrets replace the `CHANGE_ME` placeholders. That fail-fast is by
design.)*

---

## Phase 3 — app database (PostgreSQL)

```bash
source /root/ccmon-preinstall/secrets.env
sudo -u postgres psql -v pw="$APPDB_PW" <<'SQL'
CREATE ROLE ccmon LOGIN PASSWORD :'pw';
CREATE DATABASE ccmon OWNER ccmon;
SQL
```

**Verify**

```bash
sudo -u postgres psql -c "\du ccmon" | grep ccmon     # ccmon | Limit user | {ccmon}
sudo -u postgres psql -l | grep -w ccmon              # ccmon | ccmon | UTF8 ...
source /root/ccmon-preinstall/secrets.env
PGPASSWORD=$APPDB_PW psql -h 127.0.0.1 -U ccmon -d ccmon -c 'SELECT 1'  # ?column? / 1 row
```

---

## Phase 4 — read-only MariaDB user (Issabel data source)

```bash
source /root/ccmon-preinstall/secrets.env
sudo sed -i "s/CHANGE_ME_READ_ONLY_PASSWORD/${RO_PW}/" /opt/ccmon/src/deploy/sql/readonly_grants.sql
sudo mysql < /opt/ccmon/src/deploy/sql/readonly_grants.sql
```

**Verify — and prove it cannot write:**

```bash
source /root/ccmon-preinstall/secrets.env
mysql -u ccmon_reader -p"$RO_PW" -h 127.0.0.1 -e "SHOW GRANTS;" | grep -c SELECT   # grants are SELECT-only
mysql -u ccmon_reader -p"$RO_PW" -h 127.0.0.1 asterisk -e "SELECT COUNT(*) FROM queue_log;"  # returns a count
# Must FAIL (expected: ERROR 1142 = read-only proof):
mysql -u ccmon_reader -p"$RO_PW" -h 127.0.0.1 asterisk -e "DELETE FROM queue_log LIMIT 1;" && echo "UNEXPECTED WRITE!" || echo "READONLY_OK"
```

---

## Phase 5 — read-only AMI user (Asterisk)

```bash
source /root/ccmon-preinstall/secrets.env
sudo sed "s/CHANGE_ME_AMI_SECRET/${AMI_PW}/" /opt/ccmon/src/deploy/ami/ccmon_manager.conf.example \
  | sudo tee -a /etc/asterisk/manager.conf > /dev/null
asterisk -rx "reload manager"
```

**Verify**

```bash
asterisk -rx "manager show user ccmon_read" | grep -E 'read|write|Permit'
#   Read: system,call,agent,reporting   (or the read= line)
#   Write:                               (EMPTY — no write privileges)
#   Permit: 127.0.0.1
```

---

## Phase 6 — configuration + first source probe (HARD GATE)

```bash
sudo cp /opt/ccmon/src/.env.example /etc/ccmon/ccmon.env
sudo chown ccmon:ccmon /etc/ccmon/ccmon.env
sudo chmod 600 /etc/ccmon/ccmon.env

source /root/ccmon-preinstall/secrets.env
sudo sed -i \
  -e "s#^DATABASE_URL=.*#DATABASE_URL=postgresql+psycopg://ccmon:${APPDB_PW}@127.0.0.1:5432/ccmon#" \
  -e "s#^ISSABEL_DB_PASSWORD=.*#ISSABEL_DB_PASSWORD=${RO_PW}#" \
  -e "s#^AMI_SECRET=.*#AMI_SECRET=${AMI_PW}#" \
  /etc/ccmon/ccmon.env
sudo vi /etc/ccmon/ccmon.env     # review everything; set TELEGRAM_* now or later
```

Keys that **must** be right before proceeding: `APP_MODE=production`,
`DATABASE_URL`, `ISSABEL_DB_PASSWORD`, `AMI_SECRET`. (`ADMIN_PASSWORD` may
stay empty — the admin is created in Phase 7.)

Paste this helper once per shell — it sources the env file with `set -a`
(the `env $(grep … | xargs)` variant word-splits values containing spaces or
quotes):

```bash
run_ccmon() {
  sudo -u ccmon bash -c 'set -a; . /etc/ccmon/ccmon.env; set +a; exec /opt/ccmon/.venv/bin/ccmon "$@"' _ "$@"
}
```

**Verify (gate — exit code must be 0):**

```bash
run_ccmon check; echo "EXIT=$?"
# prints a capability map (db/table/column flags) and a line like:
# live_snapshot: queues=N agents=N campaigns=N trunks=N source_ok=True errors=[]
# EXIT=0
```

`source_ok=True` proves MariaDB grants **and** AMI login **and** schema
probing all work. If `EXIT=1` → [Troubleshooting](#troubleshooting) A/B/C.

---

## Phase 7 — schema + first admin

```bash
run_ccmon init-db                    # → "database ready"
run_ccmon create-admin --username admin --password 'CHOOSE-A-STRONG-PASSWORD'   # min 8 chars
```

**Verify**

```bash
sudo -u postgres psql ccmon -c '\dt' | grep -E 'schema_version|users|alert_rules|queue_snapshots|collector_heartbeat'  # all listed
run_ccmon create-admin --username admin --password 'CHOOSE-A-STRONG-PASSWORD'
# → "user admin already exists (not modified)" (idempotent, safe)
```

---

## Phase 8 — dashboard build (Node ≥ 18)

```bash
dnf module list nodejs 2>/dev/null | grep -E 'nodejs' || true
sudo dnf module install -y nodejs:20/common 2>/dev/null \
  || sudo dnf module install -y nodejs:18/common 2>/dev/null \
  || echo 'No Node >= 18 module available — use the fallback below'
node --version        # must be ≥ 18 (Vite 6 requirement)
cd /opt/ccmon && sudo -u ccmon bash -c 'cd dashboard && npm ci && npm run build'
```

**Fallback** if no Node ≥ 18 is available on the box: build on your
workstation (`cd dashboard && npm ci && npm run build`) and ship the result:

```bash
scp -r dashboard/dist root@$PBX:/opt/ccmon/dashboard/
sudo chown -R ccmon:ccmon /opt/ccmon/dashboard
```

**Verify**

```bash
test -f /opt/ccmon/dashboard/dist/index.html && echo DASHBOARD_OK    # DASHBOARD_OK
sudo -u ccmon grep -o 'src="[^"]*"' /opt/ccmon/dashboard/dist/index.html | head -2   # relative ./assets/... paths
```

---

## Phase 9 — systemd units

```bash
sudo cp /opt/ccmon/src/deploy/systemd/{ccmon.service,ccmon-collector.service,ccmon.target} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ccmon.target
```

**Verify**

```bash
systemctl is-active ccmon ccmon-collector              # active active
ss -tlnp | grep 8080                                   # 127.0.0.1:8080 ONLY (never 0.0.0.0)
curl -fsS http://127.0.0.1:8080/healthz                # {"status":"ok","mode":"production"}
journalctl -u ccmon -u ccmon-collector --since '-2 min' | grep -iE 'error|traceback' || echo NO_ERRORS   # NO_ERRORS
```

Wait 10 s for one collector cycle (live loop = 5 s), then confirm the
heartbeat is fresh:

```bash
run_ccmon check >/dev/null 2>&1; echo "EXIT=$?"        # still 0
```

---

## Phase 10 — httpd TLS reverse proxy

```bash
sudo cp /opt/ccmon/src/deploy/httpd/ccmon-proxy.conf /etc/httpd/conf.d/ccmon.conf
getenforce 2>/dev/null || true
# If enforcing, allow httpd to talk to the loopback app (required, else 502/403):
[ "$(getenforce)" = "Enforcing" ] && sudo setsebool -P httpd_can_network_connect 1 || true
sudo httpd -t && sudo systemctl reload httpd
```

**Verify**

```bash
curl -kI https://127.0.0.1/ccmon   | head -1     # HTTP/1.1 301 → /ccmon/ (canonical slash)
curl -kI https://127.0.0.1/ccmon/  | head -1     # HTTP/1.1 200
curl -ks  https://127.0.0.1/ccmon/  | grep -o '<title>[^<]*'  # the dashboard title, proves SPA served
```

From a workstation on the LAN: `https://<pbx>/ccmon/` must reach the login
page (certificate warning is httpd's existing Issabel cert — expected).

---

## Phase 11 — HTTP smoke test (exact expected codes)

```bash
BASE=http://127.0.0.1:8080
JAR=$(mktemp)

# 1) API is closed to anonymous callers → 401
curl -s -o /dev/null -w '%{http_code}\n' $BASE/api/v1/live/queues                 # 401

# 2) health + SPA → 200
curl -fsS $BASE/healthz                                                           # {"status":"ok",...}
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' $BASE/                   # 200 text/html

# 3) login (cookie session)
curl -fsS -c "$JAR" -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"CHOOSE-A-STRONG-PASSWORD"}' \
  $BASE/api/v1/auth/login                                                         # {"username":"admin",...}

# 4) authed read; after ≥1 collector cycle stale must be false
curl -fsS -b "$JAR" $BASE/api/v1/live/queues | head -c 300                        # ..."stale":false...

# 5) CSRF: cookie mutation WITHOUT header → 403; WITH header → 201
RULE='{"type":"service_down","name":"smoke probe","params":{"component":"asterisk"},"severity":"warning","for_seconds":60,"cooldown_minutes":10}'
curl -s -o /dev/null -w '%{http_code}\n' -b "$JAR" -X POST -H 'Content-Type: application/json' \
  -d "$RULE" $BASE/api/v1/alert-rules                                             # 403
RID=$(curl -fsS -b "$JAR" -X POST -H 'Content-Type: application/json' -H 'X-CCMon-CSRF: smoke' \
  -d "$RULE" $BASE/api/v1/alert-rules | grep -o '"id":[0-9]*' | cut -d: -f2)
echo "created rule id=$RID"                                                       # non-empty

# 6) cleanup the probe rule (DELETE also needs the CSRF header)
curl -s -o /dev/null -w '%{http_code}\n' -b "$JAR" -X DELETE -H 'X-CCMon-CSRF: smoke' \
  $BASE/api/v1/alert-rules/$RID                                                   # 204

# 7) capabilities (proves the UI health page will have data)
curl -fsS -b "$JAR" $BASE/api/v1/meta/capabilities | head -c 300                   # JSON feature flags
rm -f "$JAR"
```

Expected: `401 · 200 · 200 text/html · login ok · "stale":false · 403 · created · 204 · JSON`.
Any deviation → [Troubleshooting](#troubleshooting).

---

## Phase 12 — browser sign-off

Open `https://<pbx>/ccmon/` and tick every box:

- [ ] `/ccmon` redirects to `/ccmon/`; login page renders (no blank page)
- [ ] Sign in as `admin`; wrong password is rejected (and 5 attempts hit the rate limit)
- [ ] **Live**: queues/agents/campaigns populate within ~10 s; **no red staleness banner**
- [ ] **Reports**: 7-day summary + series render; switch buckets hour/day
- [ ] **Alerts**: create a rule, edit nothing, delete it (works with no console errors)
- [ ] **Health**: service probes (asterisk, mariadb, httpd…) and trunk states shown
- [ ] **Settings**: SLA settings load and save
- [ ] Browser devtools → Console: **no errors**; Network: requests stay same-origin
- [ ] Logout works; reloading while logged out returns to the login page

---

## Phase 13 — optional extras

```bash
# Telegram alerts: edit /etc/ccmon/ccmon.env
#   TELEGRAM_ENABLED=true, TELEGRAM_BOT_TOKEN=..., TELEGRAM_CHAT_ID=...
# then restart (production fail-fast requires both when enabled):
sudo systemctl restart ccmon ccmon-collector

# Hermes / automation token (raw value printed ONCE, store it safely):
run_ccmon create-token --name hermes --scopes read

# Report performance on busy systems (index tuning, idempotent):
sudo mysql < /opt/ccmon/src/deploy/sql/optimize_issabel_indexes.sql
```

**Verify:** `sudo systemctl restart ccmon && journalctl -u ccmon -n 20 --no-pager`
shows no startup `ValueError`; `run_ccmon create-token --name t2 --scopes read`
prints a token.

---

## Day-2 operations

**Upgrade**

```bash
sudo systemctl stop ccmon.target
sudo -u ccmon git -C /opt/ccmon/src pull --ff-only
sudo cp -r /opt/ccmon/src/{ccmon,dashboard,pyproject.toml,requirements.lock} /opt/ccmon/
sudo chown -R ccmon:ccmon /opt/ccmon
sudo .venv/bin/pip install --no-cache-dir -r requirements.lock
sudo .venv/bin/pip install --no-cache-dir .
sudo -u ccmon bash -c 'cd dashboard && npm ci && npm run build'
sudo systemctl start ccmon.target && run_ccmon check   # post-upgrade gate
```

**Uninstall / rollback** (PBX returns to pre-install state; Issabel data was
never touched):

```bash
sudo systemctl disable --now ccmon.target
sudo rm /etc/httpd/conf.d/ccmon.conf && sudo httpd -t && sudo systemctl reload httpd
# remove the appended [ccmon_read] block from /etc/asterisk/manager.conf; asterisk -rx "reload manager"
# optional full cleanup:
sudo rm -rf /opt/ccmon /etc/ccmon /etc/systemd/system/ccmon*.service /etc/systemd/system/ccmon.target
sudo userdel ccmon
sudo -u postgres psql -c 'DROP DATABASE ccmon' -c 'DROP ROLE ccmon'
sudo mysql -e "DROP USER 'ccmon_reader'@'localhost'; DROP USER 'ccmon_reader'@'127.0.0.1'; FLUSH PRIVILEGES;"
```

**Backup:** see [backup-restore.md](backup-restore.md) (app DB + `/etc/ccmon`).

---

## Troubleshooting

| # | Symptom | Cause | Fix |
|---|---------|-------|-----|
| A | `run_ccmon check` → `ValueError: DATABASE_URL still contains a placeholder` | `ccmon.env` still has `CHANGE_ME…`, or sed edited the wrong file | Re-run Phase 6 sed; confirm with `grep DATABASE_URL /etc/ccmon/ccmon.env` |
| B | `check` exit 1, MySQL errors in output | wrong `ISSABEL_DB_PASSWORD`, grants not applied, or MariaDB down | Re-run Phase 4 verify; `systemctl is-active mariadb` |
| C | `check` exit 1, AMI/auth errors | manager secret mismatch or `reload manager` skipped | Diff `AMI_SECRET` in `ccmon.env` vs `[ccmon_read]` in `manager.conf`; re-`reload manager` |
| D | `https://…/ccmon/` → 502/403, app fine on loopback | SELinux blocks httpd→loopback proxying | `sudo setsebool -P httpd_can_network_connect 1` (Phase 10) |
| E | `/ccmon/` shows "dashboard build not found (API-only mode)" warning | Phase 8 not done or dist not at `/opt/ccmon/dashboard/dist` | Build/ship `dist`, `chown -R ccmon:ccmon /opt/ccmon`, restart `ccmon` |
| F | Dashboard assets 404, page unstyled | requested `/ccmon` without trailing slash | The 301 redirect in `ccmon.conf` handles it — ensure the file was copied and httpd reloaded |
| G | UI banner "stale data" | collector stopped | `systemctl status ccmon-collector`; `journalctl -u ccmon-collector -n 50` |
| H | Login → 429 | `LOGIN_RATE_PER_MINUTE` (5/min) tripped by retries | Wait a minute; raise the key in `ccmon.env` if many operators share one NAT IP |
| I | UI mutation → 403 "missing CSRF header" | cookie POST without `X-CCMon-CSRF` (curl/scripts) | Send the header (browser does this automatically) |
| J | `pip install` syntax/version errors | Python < 3.11 | Phase 1; recreate `.venv` with the ≥3.11 interpreter |
| K | `npm` engine error / Vite crash | Node < 18 | Phase 8 module install or build-elsewhere fallback |
| L | `ccmon` refuses to start: `TELEGRAM_ENABLED=true requires…` | Telegram enabled with empty token/chat | Fill both keys or set `TELEGRAM_ENABLED=false` |

---

## Sign-off checklist

- [ ] Phase 0 Verify: backups + 3 generated secrets
- [ ] Phase 1 Verify: Python ≥ 3.11, postgresql active, proxy modules listed
- [ ] Phase 2 Verify: `ccmon --help`, imports OK
- [ ] Phase 3 Verify: role/database exist, password login works
- [ ] Phase 4 Verify: SELECT-only grants, **write attempt rejected** (READONLY_OK)
- [ ] Phase 5 Verify: `manager show user ccmon_read` — read classes, empty write
- [ ] Phase 6 **Gate:** `run_ccmon check` → `source_ok=True`, EXIT=0
- [ ] Phase 7 Verify: schema tables present, admin idempotent
- [ ] Phase 8 Verify: `DASHBOARD_OK`, relative asset paths
- [ ] Phase 9 Verify: both units active, `127.0.0.1:8080` only, healthz OK, no errors
- [ ] Phase 10 Verify: 301 → 200 → `<title>` via https, LAN browser reaches login
- [ ] Phase 11: full curl suite codes as expected
- [ ] Phase 12: all browser boxes ticked, console clean

**Issabel 4 deltas (UNTESTED):** paths/units may differ, `chan_sip` makes AMI
fall back to `SIPshowpeers`, `call_center` schema varies — capabilities
probing degrades features instead of failing; always finish at the Phase 6
gate before trusting anything downstream.
