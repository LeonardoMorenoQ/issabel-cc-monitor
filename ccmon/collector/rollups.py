"""Hourly rollups (idempotent delete+insert) and retention pruning."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ccmon.config import Settings
from ccmon.db import utcnow
from ccmon.models import (
    AgentRollup,
    AgentSnapshot,
    AlertEvent,
    AuditLog,
    CallSnapshot,
    CampaignSnapshot,
    QueueRollup,
    QueueSnapshot,
    ServiceHealth,
    TrunkState,
)

log = logging.getLogger(__name__)

_HOUR_FMT = "%Y-%m-%d %H:00"


def build_queue_rollups(source, session: Session, day, sla_seconds: int) -> int:
    """Recompute all hourly queue buckets for `day` (safe to re-run)."""
    try:
        rows = source.report_series(day, day, "hour", sla_seconds, None)
    except Exception as exc:
        log.warning("queue rollup source failed: %s", exc)
        return 0
    count = 0
    for row in rows:
        try:
            bucket = datetime.strptime(str(row["bucket"]), _HOUR_FMT)
        except (KeyError, ValueError):
            continue
        session.query(QueueRollup).filter(
            QueueRollup.bucket_start == bucket, QueueRollup.queue == str(row["queue"])
        ).delete(synchronize_session=False)
        session.add(
            QueueRollup(
                bucket_start=bucket, queue=str(row["queue"]),
                offered=int(row.get("offered", 0)), answered=int(row.get("answered", 0)),
                abandoned=int(row.get("abandoned", 0)), sla_pct=float(row.get("sla_pct", 0.0)),
                avg_wait_seconds=float(row.get("avg_wait_seconds", 0.0)), talk_seconds=0.0,
            )
        )
        count += 1
    return count


def build_agent_rollups(source, session: Session, hour_start: datetime, limit: int) -> int:
    """Recompute agent stats for one hour bucket (idempotent)."""
    try:
        rows = source.agent_report_between(hour_start, hour_start + timedelta(hours=1), limit)
    except Exception as exc:
        log.warning("agent rollup source failed: %s", exc)
        return 0
    count = 0
    for row in rows:
        session.query(AgentRollup).filter(
            AgentRollup.bucket_start == hour_start, AgentRollup.agent == str(row["agent"])
        ).delete(synchronize_session=False)
        session.add(
            AgentRollup(
                bucket_start=hour_start, agent=str(row["agent"]),
                handled=int(row.get("answered", 0)),
                talk_seconds=float(row.get("talk_seconds", 0.0)),
                avg_wait_seconds=float(row.get("avg_wait_seconds", 0.0)),
            )
        )
        count += 1
    return count


def build_rollups(source, session: Session, settings: Settings, now: datetime | None = None) -> int:
    """Roll up the last complete hour + today's queue buckets."""
    now = now or utcnow()
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    prev_hour = current_hour - timedelta(hours=1)
    built = build_queue_rollups(source, session, prev_hour.date(), settings.sla_seconds)
    built += build_agent_rollups(source, session, prev_hour, settings.report_max_rows)
    return built


def prune(session: Session, settings: Settings, now: datetime | None = None) -> int:
    """Delete rows past their retention window (hourly maintenance task)."""
    now = now or utcnow()

    def cutoff(days: int) -> datetime:
        return now - timedelta(days=days)

    snap_cutoff = cutoff(settings.retention_snapshots_days)
    deleted = 0
    snapshot_models = (
        QueueSnapshot,
        AgentSnapshot,
        CampaignSnapshot,
        CallSnapshot,
        ServiceHealth,
        TrunkState,
    )
    for model in snapshot_models:
        deleted += session.query(model).filter(model.ts < snap_cutoff).delete(
            synchronize_session=False
        )
    rollup_cutoff = cutoff(settings.retention_rollups_days).replace(tzinfo=None)
    deleted += session.query(QueueRollup).filter(QueueRollup.bucket_start < rollup_cutoff).delete(
        synchronize_session=False
    )
    deleted += session.query(AgentRollup).filter(AgentRollup.bucket_start < rollup_cutoff).delete(
        synchronize_session=False
    )
    deleted += session.query(AlertEvent).filter(
        AlertEvent.ts < cutoff(settings.retention_alert_events_days)
    ).delete(synchronize_session=False)
    deleted += session.query(AuditLog).filter(
        AuditLog.ts < cutoff(settings.retention_audit_days)
    ).delete(synchronize_session=False)
    if deleted:
        log.info("pruned %d expired rows", deleted)
    return deleted
