"""Live endpoints: latest collector snapshots + staleness metadata."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ccmon.auth import require_scope
from ccmon.db import as_utc, get_db, utcnow
from ccmon.models import (
    AgentSnapshot,
    CallSnapshot,
    CampaignSnapshot,
    CollectorHeartbeat,
    QueueSnapshot,
    ServiceHealth,
    TrunkState,
)
from ccmon.schemas import (
    AgentList,
    AgentLive,
    CallList,
    CallLive,
    CampaignList,
    CampaignLive,
    HealthComponent,
    HealthOut,
    QueueList,
    QueueLive,
    TrunkLive,
)

router = APIRouter(prefix="/api/v1/live", tags=["live"])
read_scope = require_scope("read")


def _stale_meta(session: Session, request: Request) -> tuple[datetime | None, bool]:
    hb = session.get(CollectorHeartbeat, "live")
    collected = as_utc(hb.ts) if hb else None
    stale = (
        collected is None
        or (utcnow() - collected).total_seconds()
        > request.app.state.settings.live_stale_after_seconds
    )
    return collected, stale


def _latest(session: Session, model, key_fn, limit: int = 600) -> list:
    rows = session.query(model).order_by(model.ts.desc()).limit(limit).all()
    seen: set = set()
    out = []
    for row in rows:
        key = key_fn(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


@router.get("/queues", response_model=QueueList)
def queues(request: Request, session: Session = Depends(get_db), _=Depends(read_scope)):
    collected, stale = _stale_meta(session, request)
    rows = _latest(session, QueueSnapshot, lambda r: r.queue)
    return QueueList(
        collected_at=collected,
        stale=stale,
        items=[
            QueueLive(
                queue=r.queue, calls_waiting=r.calls_waiting, calls_active=r.calls_active,
                agents_online=r.agents_online, agents_available=r.agents_available,
                agents_paused=r.agents_paused, agents_talking=r.agents_talking,
                handled_rolling=r.handled_rolling, abandoned_rolling=r.abandoned_rolling,
                avg_wait_seconds=r.avg_wait_seconds, sla_pct=r.sla_pct,
                source=r.source, ts=as_utc(r.ts),
            )
            for r in rows
        ],
    )


@router.get("/agents", response_model=AgentList)
def agents(request: Request, session: Session = Depends(get_db), _=Depends(read_scope)):
    collected, stale = _stale_meta(session, request)
    rows = _latest(session, AgentSnapshot, lambda r: r.agent)
    return AgentList(
        collected_at=collected,
        stale=stale,
        items=[
            AgentLive(
                agent=r.agent, name=r.name, extension=r.extension, state=r.state,
                pause_seconds=r.pause_seconds, calls_handled_today=r.calls_handled_today,
                queue_paused=r.queue_paused, ts=as_utc(r.ts),
            )
            for r in rows
        ],
    )


@router.get("/campaigns", response_model=CampaignList)
def campaigns(request: Request, session: Session = Depends(get_db), _=Depends(read_scope)):
    collected, stale = _stale_meta(session, request)
    rows = _latest(session, CampaignSnapshot, lambda r: r.campaign)
    return CampaignList(
        collected_at=collected,
        stale=stale,
        items=[
            CampaignLive(
                campaign=r.campaign, status=r.status, agents=r.agents,
                calls_today=r.calls_today, connected_today=r.connected_today,
                ts=as_utc(r.ts),
            )
            for r in rows
        ],
    )


@router.get("/calls", response_model=CallList)
def calls(request: Request, session: Session = Depends(get_db), _=Depends(read_scope)):
    collected, stale = _stale_meta(session, request)
    rows = _latest(session, CallSnapshot, lambda r: r.channel, limit=400)
    return CallList(
        collected_at=collected,
        stale=stale,
        items=[
            CallLive(
                channel=r.channel, exten=r.exten, caller=r.caller, state=r.state,
                queue=r.queue, agent=r.agent, ts=as_utc(r.ts),
            )
            for r in rows
        ],
    )


@router.get("/health", response_model=HealthOut)
def health(request: Request, session: Session = Depends(get_db), _=Depends(read_scope)):
    collected, stale = _stale_meta(session, request)
    components = _latest(session, ServiceHealth, lambda r: r.component)
    trunks = _latest(session, TrunkState, lambda r: r.peer)
    heartbeats = {
        h.key: as_utc(h.ts) for h in session.query(CollectorHeartbeat).all()
    }
    return HealthOut(
        collected_at=collected,
        stale=stale,
        components=[
            HealthComponent(
                component=c.component, status=c.status, detail=c.detail, ts=as_utc(c.ts)
            )
            for c in components
        ],
        trunks=[
            TrunkLive(tech=t.tech, peer=t.peer, status=t.status, detail=t.detail)
            for t in trunks
        ],
        collector=heartbeats,
    )
