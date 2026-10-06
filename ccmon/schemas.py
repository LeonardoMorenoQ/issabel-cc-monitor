"""Pydantic request/response models — every API input passes through here."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

QUEUE_PATTERN = r"^[A-Za-z0-9_.\-]{1,64}$"
USERNAME_PATTERN = r"^[A-Za-z0-9_.@\- ]{1,80}$"
Severity = Literal["info", "warning", "critical"]
RuleType = Literal[
    "queue_waiting_gt",
    "queue_abandoned_gt",
    "agent_paused_minutes_gt",
    "no_agents_available",
    "trunk_not_registered",
    "service_down",
]
Scope = Literal["read", "rules:write"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80, pattern=USERNAME_PATTERN)
    password: str = Field(min_length=1, max_length=256)


class UserOut(ORMModel):
    id: int
    username: str
    full_name: str
    is_superadmin: bool


class MeOut(UserOut):
    scopes: list[str] = ["read"]


class TokenCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[Scope] = ["read"]
    expires_days: int | None = Field(default=None, ge=1, le=3650)


class TokenOut(BaseModel):
    id: int
    name: str
    scopes: list[str]
    created_at: datetime
    expires_at: datetime | None = None
    revoked: bool = False
    token: str | None = None  # only present ONCE, on creation


class StaleMeta(BaseModel):
    collected_at: datetime | None = None
    stale: bool = True


class QueueLive(BaseModel):
    queue: str
    calls_waiting: int = 0
    calls_active: int = 0
    agents_online: int = 0
    agents_available: int = 0
    agents_paused: int = 0
    agents_talking: int = 0
    handled_rolling: int = 0
    abandoned_rolling: int = 0
    avg_wait_seconds: float = 0.0
    sla_pct: float = 0.0
    source: str = "ami"
    ts: datetime | None = None


class QueueList(StaleMeta):
    items: list[QueueLive] = []


class AgentLive(BaseModel):
    agent: str
    name: str = ""
    extension: str = ""
    state: str = "offline"
    pause_seconds: int = 0
    calls_handled_today: int = 0
    queue_paused: bool = False
    ts: datetime | None = None


class AgentList(StaleMeta):
    items: list[AgentLive] = []


class CampaignLive(BaseModel):
    campaign: str
    status: str = ""
    agents: int = 0
    calls_today: int = 0
    connected_today: int = 0
    ts: datetime | None = None


class CampaignList(StaleMeta):
    items: list[CampaignLive] = []


class CallLive(BaseModel):
    channel: str = ""
    exten: str = ""
    caller: str = ""
    state: str = ""
    queue: str = ""
    agent: str = ""
    ts: datetime | None = None


class CallList(StaleMeta):
    items: list[CallLive] = []


class HealthComponent(BaseModel):
    component: str
    status: str  # ok|degraded|down|unknown
    detail: str = ""
    ts: datetime | None = None


class TrunkLive(BaseModel):
    tech: str = "PJSIP"
    peer: str
    status: str  # registered|unregistered|unknown
    detail: str = ""


class HealthOut(StaleMeta):
    components: list[HealthComponent] = []
    trunks: list[TrunkLive] = []
    collector: dict[str, datetime | None] = {}


class ReportSummary(BaseModel):
    date_from: date
    date_to: date
    offered: int = 0
    answered: int = 0
    abandoned: int = 0
    abandoned_pct: float = 0.0
    avg_wait_seconds: float = 0.0
    sla_pct: float = 0.0
    sla_seconds: int = 20
    sla_target_pct: float = 80.0
    source: str = "queue_log"


class SeriesPoint(BaseModel):
    bucket: str
    queue: str | None = None
    offered: int = 0
    answered: int = 0
    abandoned: int = 0
    avg_wait_seconds: float = 0.0
    sla_pct: float = 0.0


class SeriesOut(BaseModel):
    points: list[SeriesPoint] = []
    truncated: bool = False


class AbandonedRow(BaseModel):
    ts: str
    queue: str = ""
    wait_seconds: int = 0
    caller: str = ""


class AbandonedOut(BaseModel):
    date: date
    total: int = 0
    by_queue: dict[str, int] = {}
    by_hour: list[dict[str, Any]] = []
    avg_wait_seconds: float = 0.0
    rows: list[AbandonedRow] = []
    truncated: bool = False


class AgentReportRow(BaseModel):
    agent: str
    answered: int = 0
    abandoned: int = 0
    avg_wait_seconds: float = 0.0
    talk_seconds: float = 0.0


class AgentReportOut(BaseModel):
    rows: list[AgentReportRow] = []
    truncated: bool = False


class CampaignReportRow(BaseModel):
    campaign: str
    calls: int = 0
    connected: int = 0
    connect_rate_pct: float = 0.0


class CampaignReportOut(BaseModel):
    rows: list[CampaignReportRow] = []
    truncated: bool = False


class AlertRuleCreate(BaseModel):
    type: RuleType
    name: str = Field(min_length=1, max_length=120)
    params: dict[str, Any] = Field(default_factory=dict)
    severity: Severity = "warning"
    for_seconds: int = Field(default=60, ge=0, le=86400)
    cooldown_minutes: int = Field(default=10, ge=1, le=1440)
    enabled: bool = True

    @field_validator("params")
    @classmethod
    def _params_known(cls, v: dict[str, Any], info) -> dict[str, Any]:
        from ccmon.alerts.rules import parse_params

        rule_type = info.data.get("type")
        if rule_type:
            parse_params(rule_type, v)  # raises ValueError -> 422
        return v


class AlertRuleOut(ORMModel):
    id: int
    type: str
    name: str
    params: dict[str, Any]
    severity: str
    for_seconds: int
    cooldown_minutes: int
    enabled: bool
    created_at: datetime
    updated_at: datetime
    last_state: str | None = None
    last_fired_at: datetime | None = None
    last_resolved_at: datetime | None = None


class AlertRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    params: dict[str, Any] | None = None
    severity: Severity | None = None
    for_seconds: int | None = Field(default=None, ge=0, le=86400)
    cooldown_minutes: int | None = Field(default=None, ge=1, le=1440)
    enabled: bool | None = None


class AlertEventOut(ORMModel):
    id: int
    rule_id: int
    ts: datetime
    state: str
    message: str
    payload: dict[str, Any] | None = None


class HermesQueueHealthQueue(BaseModel):
    queue: str
    waiting: int = 0
    active: int = 0
    agents_available: int = 0
    agents_paused: int = 0
    sla_pct: float = 0.0
    avg_wait_seconds: float = 0.0
    abandoned_rolling: int = 0
    status: str = "ok"  # ok | warn | critical


class HermesQueueHealthOut(BaseModel):
    generated_at: datetime
    stale: bool = True
    sla_seconds: int = 20
    sla_target_pct: float = 80.0
    queues: list[HermesQueueHealthQueue] = []
    summary_text: str = ""


class HermesPausedAgentOut(BaseModel):
    agent: str
    name: str = ""
    extension: str = ""
    pause_minutes: float = 0.0
    state: str = "paused"


class HermesPausedOut(BaseModel):
    generated_at: datetime
    stale: bool = True
    min_minutes: float = 15.0
    agents: list[HermesPausedAgentOut] = []
    summary_text: str = ""


class HermesAbandonedOut(BaseModel):
    date: date
    stale: bool = True
    total: int = 0
    by_queue: dict[str, int] = {}
    avg_wait_seconds: float = 0.0
    summary_text: str = ""


class CapabilitiesOut(BaseModel):
    mode: str
    databases: dict[str, bool] = {}
    ami: bool = False
    systemd: bool = False
    telegram: bool = False
    openapi: bool = False


class ErrorOut(BaseModel):
    detail: str
