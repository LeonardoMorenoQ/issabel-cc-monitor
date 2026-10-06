"""Deterministic synthetic Issabel-like data (demo/dev/tests, no PBX needed).

Generates 30 days of queue events for two queues, a small agent roster and two
campaigns, and keeps extending them to "now" so live views never go stale.
Used when APP_MODE=demo — it never touches any real database.
"""
from __future__ import annotations

import random
import time
from datetime import UTC, date, datetime, timedelta

from ccmon.config import Settings
from ccmon.sources.capabilities import Capabilities

QUEUES = ("800", "801")
AGENTS = (
    {"agent": "101", "name": "Ana Ruiz", "extension": "PJSIP/101"},
    {"agent": "102", "name": "Luis Vera", "extension": "PJSIP/102"},
    {"agent": "103", "name": "Maria Sosa", "extension": "PJSIP/103"},
    {"agent": "104", "name": "Pedro Leon", "extension": "PJSIP/104"},
    {"agent": "105", "name": "Sofia Diaz", "extension": "PJSIP/105"},
    {"agent": "106", "name": "Hugo Paz", "extension": "PJSIP/106"},
)
PAUSED_AGENT = "103"        # paused since 42 minutes ago -> triggers paused rule in demo
PAUSED_SECONDS_BASE = 42 * 60
CAMPAIGN = "Promo Dialer"
TRUNKS = (
    {"peer": "trunk-sipflow", "tech": "PJSIP", "status": "registered", "detail": "Online"},
    {"peer": "trunk-backup", "tech": "PJSIP", "status": "registered", "detail": "Online"},
)


class DemoSource:
    def __init__(self, settings: Settings, seed: int = 42, days: int = 30) -> None:
        self._settings = settings
        self._rng = random.Random(seed)
        self._days = days
        self._events: list[dict] = []
        self._generated_until = 0
        self._caps = Capabilities(
            source_ok=True, cdr=True, queue_log_table=True, call_center=True,
            audit=True, queue_member=True, campaigns=True, ami=True,
            systemd=True, telegram=settings.telegram_enabled,
            campaign_name_col="name", campaign_status_col="status",
            campaign_calls_time_col="start_time",
        )

    def capabilities(self) -> Capabilities:
        return self._caps

    def close(self) -> None:
        pass

    # ------------------------------------------------------------- generation
    def _hourly_weight(self, hour: int) -> float:
        if 8 <= hour <= 18:
            return 1.0
        if 6 <= hour <= 20:
            return 0.35
        return 0.05

    def _gen_range(self, start: int, end: int) -> None:
        rng = self._rng
        day = datetime.fromtimestamp(start, tz=UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        while int(day.timestamp()) < end:
            day_start = int(day.timestamp())
            for hour in range(24):
                h_start = day_start + hour * 3600
                if h_start < start - 3600 or h_start >= end:
                    continue
                for queue in QUEUES:
                    n = int(4 * self._hourly_weight(hour)) + rng.randint(0, 3)
                    for _ in range(n):
                        ts = h_start + rng.randint(0, 3599)
                        if ts < start or ts >= end:
                            continue
                        answered = rng.random() < 0.87
                        wait = int(abs(rng.gauss(18, 14))) + 1
                        talk = int(abs(rng.gauss(210, 90))) + 20 if answered else 0
                        if not answered and ts > end - 600:
                            # keep a few callers genuinely in-queue right now so the
                            # live view reflects real occupancy instead of a lull
                            wait = max(wait, rng.randint(120, 420))
                        self._events.append(
                            {
                                "ts": ts, "queue": queue,
                                "caller": f"555{rng.randint(1000, 9999)}",
                                "wait": wait, "talk": talk, "answered": answered,
                                "agent": rng.choice(AGENTS)["agent"] if answered else None,
                            }
                        )
            day += timedelta(days=1)
        self._events.sort(key=lambda e: e["ts"])
        self._generated_until = max(self._generated_until, end)

    def _extend_to(self, now: int) -> None:
        if self._generated_until == 0:
            start = now - self._days * 86400
            self._gen_range(start, now + 60)
        elif now + 60 > self._generated_until:
            self._gen_range(self._generated_until, now + 60)

    # ------------------------------------------------------------------ live
    def _events_between(self, t0: int, t1: int) -> list[dict]:
        self._extend_to(t1)
        return [e for e in self._events if t0 <= e["ts"] < t1]

    @staticmethod
    def _day_bounds(day: date) -> tuple[int, int]:
        start = int(datetime.combine(day, datetime.min.time(), tzinfo=UTC).timestamp())
        return start, start + 86400

    def _stats_for(self, events: list[dict], sla_seconds: int) -> dict:
        offered = len(events)
        answered = [e for e in events if e["answered"]]
        abandoned = offered - len(answered)
        within = sum(1 for e in answered if e["wait"] <= sla_seconds)
        avg_wait = sum(e["wait"] for e in answered) / len(answered) if answered else 0.0
        return {
            "offered": offered,
            "answered": len(answered),
            "abandoned": abandoned,
            "avg_wait_seconds": round(avg_wait, 1),
            "sla_pct": round((within / offered) * 100, 1) if offered else 0.0,
            "talk_total": sum(e["talk"] for e in answered),
            "answered_within_sla": within,
        }

    def live_snapshot(self) -> dict:
        now = int(time.time())
        sla = self._settings.sla_seconds
        recent = self._events_between(now - 300, now + 1)
        started_at = getattr(self, "_started_at", now)
        self._started_at = started_at
        pause_seconds = PAUSED_SECONDS_BASE + max(0, int(time.time() - started_at))

        queues = []
        for name in QUEUES:
            q_events = [e for e in recent if e["queue"] == name]
            waiting = sum(
                1
                for e in q_events
                if not e["answered"] and e["ts"] <= now < e["ts"] + e["wait"]
            )
            active = sum(
                1
                for e in q_events
                if e["answered"] and e["ts"] + e["wait"] <= now < e["ts"] + e["wait"] + e["talk"]
            )
            roll = self._rolling_one(name, 15, sla)
            paused_agents = 1 if name == QUEUES[0] else 0  # 103 sits on queue 800
            online = 3
            queues.append(
                {
                    "queue": name, "calls_waiting": waiting, "calls_active": active,
                    "agents_online": online,
                    "agents_available": max(0, online - paused_agents - active),
                    "agents_paused": paused_agents, "agents_talking": active,
                    "handled_rolling": roll["handled_rolling"],
                    "abandoned_rolling": roll["abandoned_rolling"],
                    "avg_wait_seconds": roll["avg_wait_seconds"], "sla_pct": roll["sla_pct"],
                    "source": "demo",
                }
            )

        today_start = int(
            datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        )
        today = self._events_between(today_start, now + 1)
        handled_today: dict[str, int] = {}
        for e in today:
            if e["answered"] and e["agent"]:
                handled_today[e["agent"]] = handled_today.get(e["agent"], 0) + 1

        talking_agents = {
            e["agent"]
            for e in recent
            if e["answered"]
            and e["agent"]
            and e["ts"] + e["wait"] <= now < e["ts"] + e["wait"] + e["talk"]
        }
        agents = []
        for info in AGENTS:
            aid = info["agent"]
            if aid == PAUSED_AGENT:
                state, paused = "paused", True
            elif aid in talking_agents:
                state, paused = "talking", False
            else:
                state, paused = "online", False
            agents.append(
                {
                    "agent": aid, "name": info["name"], "extension": info["extension"],
                    "state": state,
                    "pause_seconds": pause_seconds if paused else 0,
                    "calls_handled_today": handled_today.get(aid, 0),
                    "queue_paused": paused, "ts": datetime.now(UTC).isoformat(),
                }
            )

        active_rows = [
            e for e in recent
            if e["answered"] and e["ts"] + e["wait"] <= now < e["ts"] + e["wait"] + e["talk"]
        ]
        calls = [
            {
                "channel": f"PJSIP/{e['agent'] or '101'}-{i:04d}", "exten": e["queue"],
                "caller": e["caller"], "state": "Up", "queue": e["queue"],
                "agent": e["agent"] or "", "ts": datetime.now(UTC).isoformat(),
            }
            for i, e in enumerate(active_rows)
        ]

        campaigns = [
            {
                "campaign": CAMPAIGN, "status": "active", "agents": 2,
                "calls_today": len(today),
                "connected_today": sum(1 for e in today if e["answered"]),
                "ts": datetime.now(UTC).isoformat(),
            }
        ]
        return {
            "queues": queues, "agents": agents, "campaigns": campaigns, "calls": calls,
            "trunks": [dict(t) for t in TRUNKS], "source_ok": True, "errors": [],
        }

    def _rolling_one(self, queue: str, window_minutes: int, sla_seconds: int) -> dict:
        now = int(time.time())
        events = [e for e in self._events_between(now - window_minutes * 60, now + 1)
                  if e["queue"] == queue]
        stats = self._stats_for(events, sla_seconds)
        return {
            "queue": queue, "handled_rolling": stats["answered"],
            "abandoned_rolling": stats["abandoned"],
            "avg_wait_seconds": stats["avg_wait_seconds"], "sla_pct": stats["sla_pct"],
            "talk_rolling": stats["talk_total"],
        }

    def rolling_queue_stats(self, window_minutes: int, sla_seconds: int) -> list[dict]:
        return [self._rolling_one(name, window_minutes, sla_seconds) for name in QUEUES]

    # --------------------------------------------------------------- reports
    def report_summary(self, day_from: date, day_to: date, sla_seconds: int) -> dict:
        t0 = self._day_bounds(day_from)[0]
        t1 = self._day_bounds(day_to)[1]
        stats = self._stats_for(self._events_between(t0, t1), sla_seconds)
        offered = stats["offered"]
        return {
            "date_from": day_from, "date_to": day_to,
            "offered": stats["offered"], "answered": stats["answered"],
            "abandoned": stats["abandoned"],
            "abandoned_pct": round((stats["abandoned"] / offered) * 100, 2) if offered else 0.0,
            "avg_wait_seconds": stats["avg_wait_seconds"], "sla_pct": stats["sla_pct"],
            "sla_seconds": sla_seconds, "source": "demo",
        }

    def report_series(
        self, day_from: date, day_to: date, bucket: str, sla_seconds: int, queue: str | None
    ) -> list[dict]:
        t0 = self._day_bounds(day_from)[0]
        t1 = self._day_bounds(day_to)[1]
        events = self._events_between(t0, t1)
        if queue:
            events = [e for e in events if e["queue"] == queue]
        grouped: dict[tuple[str, str], list[dict]] = {}
        for e in events:
            dt = datetime.fromtimestamp(e["ts"], tz=UTC)
            key = dt.strftime("%Y-%m-%d %H:00") if bucket == "hour" else dt.strftime("%Y-%m-%d")
            grouped.setdefault((key, e["queue"]), []).append(e)
        out = []
        for (key, event_queue) in sorted(grouped):
            stats = self._stats_for(grouped[(key, event_queue)], sla_seconds)
            out.append(
                {
                    "bucket": key, "queue": event_queue,
                    "offered": stats["offered"], "answered": stats["answered"],
                    "abandoned": stats["abandoned"],
                    "avg_wait_seconds": stats["avg_wait_seconds"], "sla_pct": stats["sla_pct"],
                }
            )
        return out

    def abandoned_detail(self, day: date, queue: str | None, limit: int) -> dict:
        t0, t1 = self._day_bounds(day)
        events = [e for e in self._events_between(t0, t1) if not e["answered"]]
        if queue:
            events = [e for e in events if e["queue"] == queue]
        events.sort(key=lambda e: e["ts"], reverse=True)
        rows = [
            {
                "ts": datetime.fromtimestamp(e["ts"], tz=UTC).strftime("%Y-%m-%d %H:%M:%S"),
                "queue": e["queue"], "wait_seconds": e["wait"], "caller": e["caller"],
            }
            for e in events[:limit]
        ]
        total = len(events)
        by_queue: dict[str, int] = {}
        by_hour: dict[str, int] = {}
        for e in events:
            by_queue[e["queue"]] = by_queue.get(e["queue"], 0) + 1
            hour = datetime.fromtimestamp(e["ts"], tz=UTC).strftime("%Y-%m-%d %H")
            by_hour[hour] = by_hour.get(hour, 0) + 1
        avg_wait = sum(e["wait"] for e in events) / total if total else 0.0
        return {
            "rows": rows, "total": total, "by_queue": by_queue,
            "by_hour": [{"hour": h, "count": c} for h, c in sorted(by_hour.items())],
            "avg_wait_seconds": round(avg_wait, 1),
        }

    def agent_report_between(self, t0: datetime, t1: datetime, limit: int) -> list[dict]:
        events = [
            e
            for e in self._events_between(int(t0.timestamp()), int(t1.timestamp()))
            if e["answered"] and e["agent"]
        ]
        grouped: dict[str, list[dict]] = {}
        for e in events:
            grouped.setdefault(e["agent"], []).append(e)
        rows = []
        for agent, evts in grouped.items():
            rows.append(
                {
                    "agent": agent, "answered": len(evts), "abandoned": 0,
                    "avg_wait_seconds": round(sum(e["wait"] for e in evts) / len(evts), 1),
                    "talk_seconds": float(sum(e["talk"] for e in evts)),
                }
            )
        rows.sort(key=lambda r: r["answered"], reverse=True)
        return rows[:limit]

    def agent_report(self, day_from: date, day_to: date, limit: int) -> list[dict]:
        t0 = self._day_bounds(day_from)[0]
        t1 = self._day_bounds(day_to)[1]
        return self.agent_report_between(
            datetime.fromtimestamp(t0, tz=UTC),
            datetime.fromtimestamp(t1, tz=UTC),
            limit,
        )

    def campaign_report(self, day_from: date, day_to: date, limit: int) -> list[dict]:
        t0 = self._day_bounds(day_from)[0]
        t1 = self._day_bounds(day_to)[1]
        events = self._events_between(t0, t1)
        calls = len(events)
        connected = sum(1 for e in events if e["answered"])
        return [
            {
                "campaign": CAMPAIGN, "calls": calls, "connected": connected,
                "connect_rate_pct": round((connected / calls) * 100, 1) if calls else 0.0,
            }
        ][:limit]
