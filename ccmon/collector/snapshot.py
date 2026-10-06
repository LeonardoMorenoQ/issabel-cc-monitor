"""Persist live snapshots, heartbeats and build the alert context."""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ccmon.alerts.rules import (
    AgentMetrics,
    EvaluationContext,
    QueueMetrics,
    TrunkMetrics,
)
from ccmon.db import as_utc, utcnow
from ccmon.models import (
    AgentSnapshot,
    CallSnapshot,
    CampaignSnapshot,
    CollectorHeartbeat,
    QueueSnapshot,
    ServiceHealth,
    TrunkState,
)

log = logging.getLogger(__name__)

ROLLING_WINDOW_MINUTES = 15


def merge_rolling(queues: list[dict], rolling: list[dict]) -> list[dict]:
    by_queue = {r["queue"]: r for r in rolling}
    for row in queues:
        roll = by_queue.get(row.get("queue"))
        if roll:
            row["handled_rolling"] = roll.get("handled_rolling", 0)
            row["abandoned_rolling"] = roll.get("abandoned_rolling", 0)
            row["avg_wait_seconds"] = roll.get("avg_wait_seconds", 0.0)
            row["sla_pct"] = roll.get("sla_pct", 0.0)
    return queues


def persist_live(
    session: Session,
    snap: dict,
    rolling: list[dict],
    services: list[dict],
) -> None:
    now = utcnow()
    queues = merge_rolling(list(snap.get("queues", [])), rolling)
    for row in queues:
        session.add(
            QueueSnapshot(
                ts=now, queue=str(row.get("queue", ""))[:64],
                calls_waiting=int(row.get("calls_waiting", 0)),
                calls_active=int(row.get("calls_active", 0)),
                agents_online=int(row.get("agents_online", 0)),
                agents_available=int(row.get("agents_available", 0)),
                agents_paused=int(row.get("agents_paused", 0)),
                agents_talking=int(row.get("agents_talking", 0)),
                handled_rolling=int(row.get("handled_rolling", 0)),
                abandoned_rolling=int(row.get("abandoned_rolling", 0)),
                avg_wait_seconds=float(row.get("avg_wait_seconds", 0.0)),
                sla_pct=float(row.get("sla_pct", 0.0)),
                source=str(row.get("source", ""))[:24],
            )
        )
    for row in snap.get("agents", []):
        session.add(
            AgentSnapshot(
                ts=now, agent=str(row.get("agent", ""))[:64],
                name=str(row.get("name", ""))[:120],
                extension=str(row.get("extension", ""))[:64],
                state=str(row.get("state", "offline"))[:24],
                pause_seconds=int(row.get("pause_seconds", 0)),
                calls_handled_today=int(row.get("calls_handled_today", 0)),
                queue_paused=bool(row.get("queue_paused", False)),
                source="demo" if row.get("state") else "db",
            )
        )
    for row in snap.get("campaigns", []):
        session.add(
            CampaignSnapshot(
                ts=now, campaign=str(row.get("campaign", ""))[:120],
                status=str(row.get("status", ""))[:32],
                agents=int(row.get("agents", 0)),
                calls_today=int(row.get("calls_today", 0)),
                connected_today=int(row.get("connected_today", 0)),
            )
        )
    for row in snap.get("calls", [])[:200]:
        session.add(
            CallSnapshot(
                ts=now, channel=str(row.get("channel", ""))[:128],
                exten=str(row.get("exten", ""))[:64],
                caller=str(row.get("caller", ""))[:64],
                state=str(row.get("state", ""))[:32],
                queue=str(row.get("queue", ""))[:64],
                agent=str(row.get("agent", ""))[:64],
                source="ami",
            )
        )
    for row in snap.get("trunks", []):
        session.add(
            TrunkState(
                ts=now, tech=str(row.get("tech", "PJSIP"))[:24],
                peer=str(row.get("peer", ""))[:128],
                status=str(row.get("status", "unknown"))[:24],
                detail=str(row.get("detail", ""))[:255],
            )
        )
    for row in services:
        session.add(
            ServiceHealth(
                ts=now, component=str(row.get("component", ""))[:64],
                status=str(row.get("status", "unknown"))[:16],
                detail=str(row.get("detail", ""))[:255],
            )
        )
    _heartbeat(session, "live", now)


def _heartbeat(session: Session, key: str, now) -> None:
    row = session.get(CollectorHeartbeat, key)
    if row is None:
        session.add(CollectorHeartbeat(key=key, ts=now))
    else:
        row.ts = now


def touch_heartbeat(session: Session, key: str) -> None:
    _heartbeat(session, key, utcnow())


def build_context(snap: dict, services: list[dict], source_ok: bool) -> EvaluationContext:
    queues = {
        str(q.get("queue")): QueueMetrics(
            queue=str(q.get("queue")),
            waiting=int(q.get("calls_waiting", 0)),
            active=int(q.get("calls_active", 0)),
            agents_online=int(q.get("agents_online", 0)),
            agents_available=int(q.get("agents_available", 0)),
            agents_paused=int(q.get("agents_paused", 0)),
            agents_talking=int(q.get("agents_talking", 0)),
            abandoned_rolling=int(q.get("abandoned_rolling", 0)),
            sla_pct=float(q.get("sla_pct", 0.0)),
            avg_wait_seconds=float(q.get("avg_wait_seconds", 0.0)),
        )
        for q in snap.get("queues", [])
    }
    agents = [
        AgentMetrics(
            agent=str(a.get("agent", "")),
            name=str(a.get("name", "")),
            extension=str(a.get("extension", "")),
            state=str(a.get("state", "offline")),
            pause_seconds=int(a.get("pause_seconds", 0)),
            calls_handled_today=int(a.get("calls_handled_today", 0)),
            queue_paused=bool(a.get("queue_paused", False)),
        )
        for a in snap.get("agents", [])
    ]
    trunks = [
        TrunkMetrics(
            peer=str(t.get("peer", "")),
            tech=str(t.get("tech", "PJSIP")),
            status=str(t.get("status", "unknown")),
        )
        for t in snap.get("trunks", [])
    ]
    services_map = {str(s.get("component")): str(s.get("status")) for s in services}
    return EvaluationContext(
        queues=queues, agents=agents, trunks=trunks,
        services=services_map, source_ok=source_ok,
    )


def latest_snapshot_ts(session: Session) -> object | None:
    from ccmon.models import QueueSnapshot

    row = session.query(QueueSnapshot.ts).order_by(QueueSnapshot.ts.desc()).first()
    return as_utc(row[0]) if row else None
