"""Hermes Agent endpoints: plain-language answers with human summary_text."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from ccmon.api.live import _latest, _stale_meta
from ccmon.auth import require_scope
from ccmon.db import get_db, utcnow
from ccmon.models import AgentSnapshot, QueueSnapshot
from ccmon.schemas import (
    HermesAbandonedOut,
    HermesPausedAgentOut,
    HermesPausedOut,
    HermesQueueHealthOut,
    HermesQueueHealthQueue,
)

router = APIRouter(prefix="/api/v1/hermes", tags=["hermes"])
read_scope = require_scope("read")


def _queue_status(row: QueueSnapshot) -> str:
    if row.agents_available == 0 or row.calls_waiting >= 5:
        return "critical"
    if row.calls_waiting >= 3 or (row.handled_rolling and row.sla_pct < 80):
        return "warn"
    return "ok"


@router.get("/queue-health", response_model=HermesQueueHealthOut)
def queue_health(request: Request, session: Session = Depends(get_db), _=Depends(read_scope)):
    settings = request.app.state.settings
    collected, stale = _stale_meta(session, request)
    rows = _latest(session, QueueSnapshot, lambda r: r.queue)
    items = [
        HermesQueueHealthQueue(
            queue=r.queue, waiting=r.calls_waiting, active=r.calls_active,
            agents_available=r.agents_available, agents_paused=r.agents_paused,
            sla_pct=r.sla_pct, avg_wait_seconds=r.avg_wait_seconds,
            abandoned_rolling=r.abandoned_rolling, status=_queue_status(r),
        )
        for r in sorted(rows, key=lambda r: r.queue)
    ]
    if not items:
        summary = "No queue data collected yet (collector may still be starting)."
    else:
        summary = "; ".join(
            f"{i.queue}: {i.waiting} waiting, {i.active} active, "
            f"{i.agents_available} agents free, SLA {i.sla_pct:g}%"
            for i in items
        )
    return HermesQueueHealthOut(
        generated_at=utcnow(), stale=stale,
        sla_seconds=settings.sla_seconds, sla_target_pct=settings.sla_target_pct,
        queues=items, summary_text=summary,
    )


@router.get("/agents/paused", response_model=HermesPausedOut)
def agents_paused(
    request: Request,
    min_minutes: float = Query(default=15.0, ge=0, le=1440),
    session: Session = Depends(get_db),
    _=Depends(read_scope),
):
    collected, stale = _stale_meta(session, request)
    rows = _latest(session, AgentSnapshot, lambda r: r.agent)
    limit_seconds = min_minutes * 60
    paused = [
        HermesPausedAgentOut(
            agent=r.agent, name=r.name, extension=r.extension,
            pause_minutes=round(r.pause_seconds / 60, 1), state=r.state,
        )
        for r in rows
        if (r.state == "paused" or r.queue_paused) and r.pause_seconds >= limit_seconds
    ]
    paused.sort(key=lambda a: a.pause_minutes, reverse=True)
    summary = (
        f"{len(paused)} agent(s) paused ≥ {min_minutes:g} min: "
        + ", ".join(f"{a.agent} ({a.pause_minutes:g}m)" for a in paused[:10])
        if paused
        else f"No agents paused for {min_minutes:g} minutes or more."
    )
    return HermesPausedOut(
        generated_at=utcnow(), stale=stale, min_minutes=min_minutes,
        agents=paused, summary_text=summary,
    )


@router.get("/abandoned/today", response_model=HermesAbandonedOut)
def abandoned_today(
    request: Request,
    on: date | None = Query(default=None, alias="date"),
    _=Depends(read_scope),
):
    settings = request.app.state.settings
    day = on or date.today()
    stale = False
    data = request.app.state.source.abandoned_detail(day, None, settings.report_max_rows)
    by_queue = data["by_queue"]
    parts = ", ".join(f"{q}: {n}" for q, n in sorted(by_queue.items(), key=lambda kv: -kv[1]))
    summary = (
        f"{data['total']} abandoned call(s) on {day.isoformat()}"
        + (f" ({parts})" if parts else "")
        + f", average wait {data['avg_wait_seconds']:g}s."
        if data["total"]
        else f"No abandoned calls on {day.isoformat()}."
    )
    return HermesAbandonedOut(
        date=day, stale=stale, total=data["total"], by_queue=by_queue,
        avg_wait_seconds=data["avg_wait_seconds"], summary_text=summary,
    )
