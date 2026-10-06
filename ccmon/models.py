"""SQLAlchemy models for the ccmon application database (PostgreSQL target).

Issabel/Asterisk databases are NEVER written to; all tables here belong to
ccmon's own database.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ccmon.db import utcnow


class Base(DeclarativeBase):
    pass


def _ts() -> datetime:
    return utcnow()


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    applied_at: Mapped[str] = mapped_column(String(40))


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ts)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ts)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ts)


class ApiToken(Base):
    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scopes: Mapped[str] = mapped_column(String(200), default="read")  # comma separated
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ts)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)

    def has_scope(self, scope: str) -> bool:
        return scope in {s.strip() for s in self.scopes.split(",") if s.strip()}


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ts, index=True)
    username: Mapped[str | None] = mapped_column(String(80), nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ts)


class AlertRule(Base):
    __tablename__ = "alert_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(50), index=True)
    name: Mapped[str] = mapped_column(String(120))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    severity: Mapped[str] = mapped_column(String(20), default="warning")
    for_seconds: Mapped[int] = mapped_column(Integer, default=60)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=10)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ts)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ts, onupdate=_ts)
    last_state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    pending_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AlertEvent(Base):
    __tablename__ = "alert_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(Integer, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ts, index=True)
    state: Mapped[str] = mapped_column(String(24))  # firing | resolved | insufficient_data
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class QueueSnapshot(Base):
    __tablename__ = "queue_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ts, index=True)
    queue: Mapped[str] = mapped_column(String(64), index=True)
    calls_waiting: Mapped[int] = mapped_column(Integer, default=0)
    calls_active: Mapped[int] = mapped_column(Integer, default=0)
    agents_online: Mapped[int] = mapped_column(Integer, default=0)
    agents_available: Mapped[int] = mapped_column(Integer, default=0)
    agents_paused: Mapped[int] = mapped_column(Integer, default=0)
    agents_talking: Mapped[int] = mapped_column(Integer, default=0)
    handled_rolling: Mapped[int] = mapped_column(Integer, default=0)
    abandoned_rolling: Mapped[int] = mapped_column(Integer, default=0)
    avg_wait_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    sla_pct: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(24), default="ami")


class AgentSnapshot(Base):
    __tablename__ = "agent_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ts, index=True)
    agent: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    extension: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[str] = mapped_column(String(24))  # online|paused|talking|idle|offline
    pause_seconds: Mapped[int] = mapped_column(Integer, default=0)
    calls_handled_today: Mapped[int] = mapped_column(Integer, default=0)
    queue_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(24), default="db")


class CampaignSnapshot(Base):
    __tablename__ = "campaign_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ts, index=True)
    campaign: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(32), default="")
    agents: Mapped[int] = mapped_column(Integer, default=0)
    calls_today: Mapped[int] = mapped_column(Integer, default=0)
    connected_today: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(24), default="db")


class CallSnapshot(Base):
    __tablename__ = "call_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ts, index=True)
    channel: Mapped[str] = mapped_column(String(128), default="")
    exten: Mapped[str] = mapped_column(String(64), default="")
    caller: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[str] = mapped_column(String(32), default="")
    queue: Mapped[str] = mapped_column(String(64), default="")
    agent: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(24), default="ami")


class ServiceHealth(Base):
    __tablename__ = "service_health"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ts, index=True)
    component: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16))  # ok|degraded|down|unknown
    detail: Mapped[str] = mapped_column(String(255), default="")


class TrunkState(Base):
    __tablename__ = "trunk_states"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ts, index=True)
    tech: Mapped[str] = mapped_column(String(24), default="PJSIP")
    peer: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(24))  # registered|unregistered|unknown
    detail: Mapped[str] = mapped_column(String(255), default="")


class CollectorHeartbeat(Base):
    __tablename__ = "collector_heartbeat"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)  # live|history|rollup
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ts)


class QueueRollup(Base):
    __tablename__ = "queue_rollups"
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    queue: Mapped[str] = mapped_column(String(64), primary_key=True)
    offered: Mapped[int] = mapped_column(Integer, default=0)
    answered: Mapped[int] = mapped_column(Integer, default=0)
    abandoned: Mapped[int] = mapped_column(Integer, default=0)
    sla_pct: Mapped[float] = mapped_column(Float, default=0.0)
    avg_wait_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    talk_seconds: Mapped[float] = mapped_column(Float, default=0.0)


class AgentRollup(Base):
    __tablename__ = "agent_rollups"
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    agent: Mapped[str] = mapped_column(String(64), primary_key=True)
    handled: Mapped[int] = mapped_column(Integer, default=0)
    talk_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    avg_wait_seconds: Mapped[float] = mapped_column(Float, default=0.0)
