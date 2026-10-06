# Standalone install — Ubuntu / Debian

Use this when you want ccmon on a separate host, pointed at a remote
Issabel/Asterisk box (or just to develop). Same code, different plumbing:
no Issabel httpd — run behind any TLS reverse proxy (Caddy/nginx example
below) or keep it on loopback.

## 1. Packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip postgresql nginx
```

Python ≥ 3.11 required (Ubuntu 22.04 ships 3.10 — use 23.04+/24.04 or
deadsnakes for 3.11+). Node.js 20+ only if you build the dashboard.

## 2. Code + venv

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

## 3. PostgreSQL app DB

```bash
sudo -u postgres psql <<'SQL'
CREATE ROLE ccmon LOGIN PASSWORD 'CHANGE_ME_APP_DB_PASSWORD';
CREATE DATABASE ccmon OWNER ccmon;
SQL
```

## 4. Source DB + AMI on the remote PBX

Same as Issabel: run `deploy/sql/readonly_grants.sql` on the PBX MariaDB and
append `deploy/ami/ccmon_manager.conf.example` to its `manager.conf`. Point
`.env` at it:

```
ISSABEL_DB_HOST=<pbx-ip>
AMI_HOST=<pbx-ip>
```

> Note: MySQL/AMI should still be reachable only from trusted networks —
> bind them to the LAN/VPN and firewall accordingly. Loopback defaults only
> apply when co-located.

## 5. Configure + init

```bash
sudo cp /opt/ccmon/src/.env.example /etc/ccmon/ccmon.env
sudo chmod 600 /etc/ccmon/ccmon.env && sudo vi /etc/ccmon/ccmon.env
sudo -u ccmon env $(grep -v '^#' /etc/ccmon/ccmon.env | xargs) /opt/ccmon/.venv/bin/ccmon init-db
sudo -u ccmon env $(grep -v '^#' /etc/ccmon/ccmon.env | xargs) /opt/ccmon/.venv/bin/ccmon create-admin --username admin --password 'STRONG_PASSWORD'
sudo -u ccmon env $(grep -v '^#' /etc/ccmon/ccmon.env | xargs) /opt/ccmon/.venv/bin/ccmon check
```

## 6. Dashboard + systemd

```bash
cd /opt/ccmon && sudo -u ccmon bash -c 'cd dashboard && npm ci && npm run build'
sudo cp /opt/ccmon/src/deploy/systemd/{ccmon.service,ccmon-collector.service,ccmon.target} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ccmon.target
```

## 7. TLS reverse proxy (nginx example)

```nginx
server {
    listen 443 ssl;
    server_name monitor.example.com;
    # ssl_certificate ...; ssl_certificate_key ...;

    location / {
        proxy_pass http://127.0.0.1:8080/;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Caddy one-liner equivalent: `monitor.example.com { reverse_proxy / * 127.0.0.1:8080 }`.

The dashboard uses relative asset URLs and hash routing, so any prefix works.

## 8. Try without a PBX

```bash
APP_MODE=demo ccmon seed-demo --yes
APP_MODE=demo ccmon serve      # + ccmon collect in another shell
```
