# Backup & restore

## What matters

| Asset | Why | Where |
| --- | --- | --- |
| **App DB (PostgreSQL)** | users, sessions, alert rules/events, snapshots + rollups (history) | `ccmon` database |
| **`/etc/ccmon/ccmon.env`** | all configuration/secrets | mode 600 |
| Dashboard build | rebuildable from git (`npm run build`) | `dashboard/dist` |
| Source DBs (MariaDB) | *owned by Issabel/Asterisk* — back them up with your normal PBX backup, not by ccmon | `asteriskcdrdb`, `asterisk`, `call_center` |

ccmon's own code is in git; the demo/app data is the only unique state.

## Backup (app DB)

```bash
sudo -u postgres pg_dump -Fc ccmon > /var/backups/ccmon-$(date +%F-%H%M).dump
sudo cp -a /etc/ccmon/ccmon.env /var/backups/ccmon-env-$(date +%F)
```

Example root cron (nightly, keep 14 days):

```cron
15 3 * * * sudo -u postgres pg_dump -Fc ccmon > /var/backups/ccmon-$(date +\%F).dump && find /var/backups -name 'ccmon-*' -mtime +14 -delete
```

Snapshots older than `RETENTION_SNAPSHOTS_DAYS` (default 7) are pruned hourly
— so the dump stays small. Rollups are kept `RETENTION_ROLLUP_DAYS` (400).

## Restore

```bash
sudo systemctl stop ccmon ccmon-collector
sudo -u postgres dropdb --if-exists ccmon
sudo -u postgres createdb -O ccmon ccmon
sudo -u postgres pg_restore -d ccmon --no-owner /var/backups/ccmon-YYYY-MM-DD.dump
sudo systemctl start ccmon.target
```

Schema changes are forward-only and idempotent: on startup `ccmon init-db`
re-applies anything the older dump is missing (`schema_version` tracked).

## Disaster rebuild (from scratch)

1. Fresh Issabel 5 host + this repo (install docs).
2. Restore `ccmon.env`, run `readonly_grants.sql`, AMI snippet, systemd, proxy.
3. `ccmon init-db` + restore the dump (or start empty — only history/rules
   are lost; live data reappears within one 5 s cycle).

## Upgrades

```bash
sudo systemctl stop ccmon ccmon-collector
cd /opt/ccmon/src && sudo -u ccmon git pull
sudo -u ccmon .venv/bin/pip install -r requirements.lock && sudo -u ccmon .venv/bin/pip install .
cd dashboard && sudo -u ccmon bash -c 'npm ci && npm run build'
sudo -u ccmon env $(grep -v '^#' /etc/ccmon/ccmon.env | xargs) /opt/ccmon/.venv/bin/ccmon init-db
sudo systemctl start ccmon.target
```

Take a DB dump before upgrading (cheap insurance). Migrations never rewrite
existing rows; an upgrade is only ever forward.
