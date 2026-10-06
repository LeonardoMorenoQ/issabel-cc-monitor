"""Live+historical source backed by the read-only Issabel DB and AMI.

AMI is preferred for live state (queue counts, channels, trunks); DB fills in
sessions/pauses/campaigns and is the fallback when AMI is off or unreachable.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta

from ccmon.config import Settings
from ccmon.sources import queries as q
from ccmon.sources.ami import AmiClient, AmiError
from ccmon.sources.capabilities import Capabilities, CapabilityProbe
from ccmon.sources.mysql_ro import MysqlReader, render

log = logging.getLogger(__name__)


def _epoch_start(day: date) -> int:
    return int(datetime.combine(day, time.min, tzinfo=UTC).timestamp())


def _epoch_end(day: date) -> int:
    return int(datetime.combine(day + timedelta(days=1), time.min, tzinfo=UTC).timestamp())


def _dt(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


class MysqlSource:
    def __init__(
        self,
        settings: Settings,
        reader: MysqlReader | None = None,
        ami_factory=AmiClient,
    ) -> None:
        self._settings = settings
        self._reader = reader if reader is not None else MysqlReader(settings)
        self._ami_factory = ami_factory
        self._probe = CapabilityProbe(settings, self._reader)

    def capabilities(self) -> Capabilities:
        return self._probe.get()

    def close(self) -> None:
        self._reader.close()

    # ------------------------------------------------------------------ live
    def _ami_snapshot(self) -> dict | None:
        if not self._settings.ami_enabled:
            return None
        try:
            return self._ami_factory(self._settings).snapshot()
        except AmiError as exc:
            log.warning("AMI snapshot failed: %s", exc)
            return None
        except Exception as exc:  # unexpected: keep collection alive
            log.warning("AMI snapshot unexpected error: %s", exc)
            return None

    def _collect_queues(
        self, caps: Capabilities, ami: dict | None, errors: list[str]
    ) -> list[dict]:

        queues: dict[str, dict] = {}

        def bucket(name: str) -> dict:
            return queues.setdefault(
                name,
                {
                    "queue": name, "calls_waiting": 0, "calls_active": 0,
                    "agents_online": 0, "agents_available": 0, "agents_paused": 0,
                    "agents_talking": 0, "handled_rolling": 0, "abandoned_rolling": 0,
                    "avg_wait_seconds": 0.0, "sla_pct": 0.0, "source": "db",
                },
            )

        # 1) AMI live counts
        if ami:
            for item in ami["queues"]:
                row = bucket(str(item["queue"]))
                row["calls_waiting"] = int(item["calls"])
                row["source"] = "ami"

        # 2) DB fallback / supplement
        if caps.source_ok and caps.call_center:
            try:
                for row in self._reader.query(
                    render(q.queues.SQL_ACTIVE_CALLS_PER_QUEUE, self._settings)
                ):
                    bucket(str(row["queue"]))["calls_active"] = int(row["active"] or 0)
                for row in self._reader.query(
                    render(q.queues.SQL_WAITING_CALLS_PER_QUEUE, self._settings)
                ):
                    target = bucket(str(row["queue"]))
                    if ami is None:
                        target["calls_waiting"] = int(row["waiting"] or 0)
            except Exception as exc:
                errors.append(f"queue db: {exc}")
                log.warning("queue db queries failed: %s", exc)
        if caps.source_ok and caps.queue_member:
            try:
                for row in self._reader.query(
                    render(q.queues.SQL_QUEUE_MEMBER_COUNTS, self._settings)
                ):
                    target = bucket(str(row["queue"]))
                    target["agents_online"] = int(row["members"] or 0)
                    target["agents_paused"] = int(row["paused"] or 0)
            except Exception as exc:
                errors.append(f"queue_member: {exc}")
        elif caps.source_ok and caps.call_center:
            # list queues from call center even without queue_member
            try:
                for row in self._reader.query(render(q.queues.SQL_QUEUE_NAMES, self._settings)):
                    bucket(str(row["queue"]))
            except Exception as exc:
                errors.append(f"queue names: {exc}")
        return list(queues.values())

    def _collect_agents(self, caps: Capabilities, ami: dict | None) -> list[dict]:
        if not (caps.source_ok and caps.audit):
            return []
        now = datetime.now(UTC)
        try:
            sessions = {
                str(r["agent"]): r
                for r in self._reader.query(render(q.agents.SQL_OPEN_SESSIONS, self._settings))
            }
            pauses = {
                str(r["agent"]): r
                for r in self._reader.query(render(q.agents.SQL_OPEN_PAUSES, self._settings))
            }
            handled = {
                str(r["agent"]): int(r["handled"] or 0)
                for r in self._reader.query(render(q.agents.SQL_HANDLED_TODAY, self._settings))
            }
            roster = {
                str(r["agent"]): r
                for r in self._reader.query(render(q.agents.SQL_ALL_AGENTS, self._settings))
            }
            member_devices: dict[str, bool] = {}
            if caps.queue_member:
                for r in self._reader.query(
                    render(q.queues.SQL_QUEUE_MEMBER_PAUSED, self._settings)
                ):
                    dev = str(r.get("device") or "")
                    ext = dev.split("/")[-1]
                    member_devices[ext] = bool(int(r.get("paused") or 0))
        except Exception as exc:
            log.warning("agent queries failed: %s", exc)
            return []

        talking_exts: set[str] = set()
        for ch in (ami or {}).get("channels", []):
            if str(ch.get("state", "")).lower() == "up":
                name = str(ch.get("channel", ""))
                ext = name.split("/")[-1].split("-")[0]
                if ext.isdigit():
                    talking_exts.add(ext)

        rows: list[dict] = []
        for agent_id in sorted(set(sessions) | set(pauses) | set(roster)):
            session = sessions.get(agent_id)
            pause = pauses.get(agent_id)
            info = roster.get(agent_id, {})
            extension = str((session or {}).get("extension") or "")
            ext_digits = extension.split("/")[-1]
            if session is None:
                state = "offline"
            elif pause is not None:
                state = "paused"
            elif ext_digits and ext_digits in talking_exts:
                state = "talking"
            else:
                state = "online"
            pause_seconds = 0
            if pause is not None:
                started = _dt(pause.get("pause_start"))
                if started:
                    pause_seconds = max(0, int((now - started).total_seconds()))
            rows.append(
                {
                    "agent": agent_id,
                    "name": str(info.get("name") or (session or {}).get("name") or ""),
                    "extension": extension,
                    "state": state,
                    "pause_seconds": pause_seconds,
                    "calls_handled_today": handled.get(agent_id, 0),
                    "queue_paused": member_devices.get(ext_digits, False),
                    "ts": now.isoformat(),
                }
            )
        return rows

    def _collect_campaigns(self, caps: Capabilities) -> list[dict]:
        if not (caps.source_ok and caps.campaigns and caps.campaign_name_col):
            return []
        try:
            sql = q.campaigns.build_campaign_query(
                caps.campaign_name_col, caps.campaign_status_col, caps.campaign_calls_time_col
            )
            rows = self._reader.query(render(sql, self._settings))
        except Exception as exc:
            log.warning("campaign query failed: %s", exc)
            return []
        return [
            {
                "campaign": str(r.get("campaign") or ""),
                "status": str(r.get("status") or ""),
                "agents": 0,
                "calls_today": int(r.get("calls_today") or 0),
                "connected_today": int(r.get("connected_today") or 0),
                "ts": datetime.now(UTC).isoformat(),
            }
            for r in rows
        ]

    def _collect_trunks(self, caps: Capabilities, ami: dict | None) -> list[dict]:
        if ami and ami.get("trunks"):
            return list(ami["trunks"])
        if not caps.source_ok:
            return []
        try:
            if caps.ps_contacts:
                rows = self._reader.query(render(q.health.SQL_PS_CONTACTS, self._settings))
            elif caps.sippeers:
                rows = self._reader.query(render(q.health.SQL_SIP_PEERS, self._settings))
            else:
                return []
        except Exception as exc:
            log.warning("trunk query failed: %s", exc)
            return []
        return [
            {
                "peer": str(r.get("peer") or ""),
                "status": str(r.get("status") or "unknown"),
                "detail": str(r.get("detail") or "")[:120],
                "tech": "PJSIP" if caps.ps_contacts else "SIP",
            }
            for r in rows
        ]

    def live_snapshot(self) -> dict:
        caps = self.capabilities()
        errors: list[str] = []
        ami = self._ami_snapshot()
        if ami and ami.get("error"):
            errors.append(str(ami["error"]))
        snapshot = {
            "queues": self._collect_queues(caps, ami, errors),
            "agents": self._collect_agents(caps, ami),
            "campaigns": self._collect_campaigns(caps),
            "calls": [
                {
                    "channel": c["channel"], "exten": c.get("exten", ""),
                    "caller": c.get("caller", ""), "state": c.get("state", ""),
                    "queue": "", "agent": "", "ts": datetime.now(UTC).isoformat(),
                }
                for c in (ami or {}).get("channels", [])
            ],
            "trunks": self._collect_trunks(caps, ami),
            "source_ok": caps.source_ok or ami is not None,
            "errors": errors,
        }
        return snapshot

    # --------------------------------------------------------------- reports
    def _has_queue_log(self) -> bool:
        caps = self.capabilities()
        return caps.source_ok and caps.queue_log_table

    def rolling_queue_stats(self, window_minutes: int, sla_seconds: int) -> list[dict]:
        """Rolling handled/abandoned/SLA per queue (feeds snapshots + alerts)."""
        if not self._has_queue_log():
            return []
        now_ts = int(datetime.now(UTC).timestamp())
        rows = self._reader.query(
            render(q.queue_log.SQL_QUEUE_STATS, self._settings),
            {"t0": now_ts - window_minutes * 60, "t1": now_ts + 60, "sla_seconds": sla_seconds},
        )
        out = []
        for r in rows:
            offered = int(r["offered"] or 0)
            answered = int(r["answered"] or 0)
            within = int(r["answered_within_sla"] or 0)
            out.append(
                {
                    "queue": str(r["queue"]),
                    "handled_rolling": answered,
                    "abandoned_rolling": int(r["abandoned"] or 0),
                    "avg_wait_seconds": round(float(r["avg_wait"] or 0), 1),
                    "sla_pct": round((within / offered) * 100, 1) if offered else 0.0,
                    "talk_rolling": int(r["talk_total"] or 0),
                }
            )
        return out

    def report_summary(self, day_from: date, day_to: date, sla_seconds: int) -> dict:
        result = {
            "date_from": day_from, "date_to": day_to, "offered": 0, "answered": 0,
            "abandoned": 0, "abandoned_pct": 0.0, "avg_wait_seconds": 0.0,
            "sla_pct": 0.0, "sla_seconds": sla_seconds, "source": "none",
        }
        if not self._has_queue_log():
            return self._cdr_summary(day_from, day_to, result)
        rows = self._reader.query(
            render(q.queue_log.SQL_QUEUE_STATS, self._settings),
            {"t0": _epoch_start(day_from), "t1": _epoch_end(day_to), "sla_seconds": sla_seconds},
        )
        offered = sum(int(r["offered"] or 0) for r in rows)
        answered = sum(int(r["answered"] or 0) for r in rows)
        abandoned = sum(int(r["abandoned"] or 0) for r in rows)
        within = sum(int(r["answered_within_sla"] or 0) for r in rows)
        wait_sum = sum(float(r["avg_wait"] or 0) * int(r["answered"] or 0) for r in rows)
        result.update(
            offered=offered,
            answered=answered,
            abandoned=abandoned,
            abandoned_pct=round((abandoned / offered) * 100, 2) if offered else 0.0,
            avg_wait_seconds=round(wait_sum / answered, 1) if answered else 0.0,
            sla_pct=round((within / offered) * 100, 1) if offered else 0.0,
            source="queue_log",
        )
        return result

    def _cdr_summary(self, day_from: date, day_to: date, result: dict) -> dict:
        """Traffic-only fallback when queue_log is unavailable (CDR schema)."""
        caps = self.capabilities()
        if not (caps.source_ok and caps.cdr):
            return result
        try:
            rows = self._reader.query(
                render(q.cdr.SQL_CDR_SUMMARY, self._settings),
                {
                    "t0": datetime.combine(day_from, time.min),
                    "t1": datetime.combine(day_to + timedelta(days=1), time.min),
                },
            )
        except Exception as exc:
            log.warning("cdr summary failed: %s", exc)
            return result
        if not rows:
            return result
        row = rows[0]
        offered = int(row.get("total") or 0)
        answered = int(row.get("answered") or 0)
        result.update(
            offered=offered,
            answered=answered,
            avg_wait_seconds=round(float(row.get("avg_duration") or 0), 1),
            source="cdr",
        )
        return result

    def report_series(
        self, day_from: date, day_to: date, bucket: str, sla_seconds: int, queue: str | None
    ) -> list[dict]:
        if not self._has_queue_log():
            return []
        bucket_expr = q.queue_log.BUCKET_HOUR if bucket == "hour" else q.queue_log.BUCKET_DAY
        # single format pass: render() substitutes {asterisk} and {bucket_expr}
        sql = render(q.queue_log.SQL_QUEUE_STATS_BY_BUCKET, self._settings, bucket_expr=bucket_expr)
        rows = self._reader.query(
            render(sql, self._settings),
            {"t0": _epoch_start(day_from), "t1": _epoch_end(day_to), "sla_seconds": sla_seconds},
        )
        out = []
        for r in rows:
            if queue and str(r["queue"]) != queue:
                continue
            offered = int(r["offered"] or 0)
            within = int(r["answered_within_sla"] or 0)
            out.append(
                {
                    "bucket": str(r["bucket"]), "queue": str(r["queue"]),
                    "offered": offered, "answered": int(r["answered"] or 0),
                    "abandoned": int(r["abandoned"] or 0),
                    "avg_wait_seconds": round(float(r["avg_wait"] or 0), 1),
                    "sla_pct": round((within / offered) * 100, 1) if offered else 0.0,
                }
            )
        return out

    def abandoned_detail(self, day: date, queue: str | None, limit: int) -> dict:
        empty = {"rows": [], "total": 0, "by_queue": {}, "by_hour": [], "avg_wait_seconds": 0.0}
        if not self._has_queue_log():
            return empty
        rows = self._reader.query(
            render(q.queue_log.SQL_ABANDONED_DETAIL, self._settings),
            {"t0": _epoch_start(day), "t1": _epoch_end(day), "queue": queue, "lim": limit},
        )
        parsed = [
            {
                "ts": str(r["ts"]), "queue": str(r["queue"] or ""),
                "wait_seconds": int(r["wait_seconds"] or 0), "caller": str(r["caller"] or ""),
            }
            for r in rows
        ]
        by_queue: dict[str, int] = {}
        by_hour: dict[str, int] = {}
        for row in parsed:
            by_queue[row["queue"]] = by_queue.get(row["queue"], 0) + 1
            hour = row["ts"][:13]
            by_hour[hour] = by_hour.get(hour, 0) + 1
        total = len(parsed)
        avg_wait = round(sum(r["wait_seconds"] for r in parsed) / total, 1) if total else 0.0
        return {
            "rows": parsed, "total": total, "by_queue": by_queue,
            "by_hour": [{"hour": h, "count": c} for h, c in sorted(by_hour.items())],
            "avg_wait_seconds": avg_wait,
        }

    def agent_report_between(self, t0: datetime, t1: datetime, limit: int) -> list[dict]:
        if not (self.capabilities().source_ok and self.capabilities().call_center):
            return []
        rows = self._reader.query(
            render(q.agents.SQL_AGENT_STATS_RANGE, self._settings),
            {"t0": t0.isoformat(), "t1": t1.isoformat(), "lim": limit},
        )
        return [
            {
                "agent": str(r["agent"]), "answered": int(r["answered"] or 0),
                "abandoned": 0, "avg_wait_seconds": round(float(r["avg_wait"] or 0), 1),
                "talk_seconds": round(float(r["talk"] or 0), 1),
            }
            for r in rows
        ]

    def agent_report(self, day_from: date, day_to: date, limit: int) -> list[dict]:
        return self.agent_report_between(
            datetime.combine(day_from, time.min, tzinfo=UTC),
            datetime.combine(day_to + timedelta(days=1), time.min, tzinfo=UTC),
            limit,
        )

    def campaign_report(self, day_from: date, day_to: date, limit: int) -> list[dict]:
        caps = self.capabilities()
        if not (caps.source_ok and caps.campaigns and caps.campaign_calls_time_col):
            return []
        time_col = caps.campaign_calls_time_col
        sql = q.campaigns.build_campaign_report_query(time_col)
        rows = self._reader.query(
            render(sql, self._settings),
            {
                "t0": day_from.isoformat(),
                "t1": (day_to + timedelta(days=1)).isoformat(),
                "lim": limit,
            },
        )
        return [
            {
                "campaign": str(r["campaign"]), "calls": int(r["calls"] or 0),
                "connected": int(r["connected"] or 0),
                "connect_rate_pct": round(
                    (int(r["connected"] or 0) / int(r["calls"] or 1)) * 100, 1
                ),
            }
            for r in rows
        ]
