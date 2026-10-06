"""Alert rule parameter schemas and pure condition evaluation.

evaluate_condition() returns (condition, message, payload):
  * condition=True/False  -> enough data, normal sustain/cooldown handling
  * condition=None       -> insufficient data (source unreachable, unknown
                            component, queue not present...). The engine treats
    this as "no transition": stale data never fires AND never resolves.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ccmon.schemas import QUEUE_PATTERN

PEER_PATTERN = r"^[A-Za-z0-9_.\-]{1,128}$"
COMPONENT_PATTERN = r"^[A-Za-z0-9_.\-]{1,64}$"
AGENT_PATTERN = r"^[A-Za-z0-9_.\-]{1,64}$"


class StrictParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QueueWaitingParams(StrictParams):
    queue: str = Field(pattern=QUEUE_PATTERN)
    threshold: int = Field(ge=1, le=100_000)


class QueueAbandonedParams(StrictParams):
    queue: str | None = Field(default=None, pattern=QUEUE_PATTERN)
    threshold: int = Field(ge=1, le=100_000)
    window_minutes: int = Field(default=15, ge=1, le=1440)


class AgentPausedParams(StrictParams):
    min_minutes: float = Field(default=15.0, ge=0, le=1440)
    agent: str | None = Field(default=None, pattern=AGENT_PATTERN)


class NoAgentsParams(StrictParams):
    queue: str | None = Field(default=None, pattern=QUEUE_PATTERN)


class TrunkParams(StrictParams):
    peer: str | None = Field(default=None, pattern=PEER_PATTERN)


class ServiceParams(StrictParams):
    component: str = Field(pattern=COMPONENT_PATTERN)


PARAM_MODELS: dict[str, type[StrictParams]] = {
    "queue_waiting_gt": QueueWaitingParams,
    "queue_abandoned_gt": QueueAbandonedParams,
    "agent_paused_minutes_gt": AgentPausedParams,
    "no_agents_available": NoAgentsParams,
    "trunk_not_registered": TrunkParams,
    "service_down": ServiceParams,
}


def parse_params(rule_type: str, params: dict[str, Any]) -> dict[str, Any]:
    """Validate params for a rule type; raises pydantic ValidationError."""
    model = PARAM_MODELS.get(rule_type)
    if model is None:
        raise ValueError(f"unknown rule type: {rule_type}")
    return model.model_validate(params).model_dump()


@dataclass
class QueueMetrics:
    queue: str
    waiting: int = 0
    active: int = 0
    agents_online: int = 0
    agents_available: int = 0
    agents_paused: int = 0
    agents_talking: int = 0
    abandoned_rolling: int = 0
    sla_pct: float = 0.0
    avg_wait_seconds: float = 0.0


@dataclass
class AgentMetrics:
    agent: str
    name: str = ""
    extension: str = ""
    state: str = "offline"
    pause_seconds: int = 0
    calls_handled_today: int = 0
    queue_paused: bool = False


@dataclass
class TrunkMetrics:
    peer: str
    tech: str = "PJSIP"
    status: str = "unknown"


@dataclass
class EvaluationContext:
    queues: dict[str, QueueMetrics] = field(default_factory=dict)
    agents: list[AgentMetrics] = field(default_factory=list)
    trunks: list[TrunkMetrics] = field(default_factory=list)
    services: dict[str, str] = field(default_factory=dict)  # component -> status
    source_ok: bool = False


ConditionResult = tuple[bool | None, str, dict[str, Any]]


def evaluate_condition(
    rule_type: str, params: dict[str, Any], ctx: EvaluationContext
) -> ConditionResult:
    if not ctx.source_ok:
        return None, "source data unreachable; evaluation skipped", {}
    model = PARAM_MODELS.get(rule_type)
    if model is None:
        return None, f"unknown rule type {rule_type}", {}
    p = model.model_validate(params)

    if isinstance(p, QueueWaitingParams):
        q = ctx.queues.get(p.queue)
        if q is None:
            return None, f"queue {p.queue} not present in live data", {}
        hit = q.waiting > p.threshold
        return hit, f"Queue {p.queue}: {q.waiting} waiting (threshold {p.threshold})", {
            "queue": p.queue, "waiting": q.waiting, "threshold": p.threshold
        }

    if isinstance(p, QueueAbandonedParams):
        targets = [ctx.queues[p.queue]] if p.queue else list(ctx.queues.values())
        if not targets:
            return None, "no queue data available", {}
        if p.queue and p.queue not in ctx.queues:
            return None, f"queue {p.queue} not present in live data", {}
        worst = max(targets, key=lambda q: q.abandoned_rolling)
        hit = worst.abandoned_rolling > p.threshold
        return hit, (
            f"Abandoned calls: {worst.abandoned_rolling} in {p.window_minutes}m "
            f"on queue {worst.queue} (threshold {p.threshold})"
        ), {"queue": worst.queue, "abandoned": worst.abandoned_rolling, "threshold": p.threshold}

    if isinstance(p, AgentPausedParams):
        if not ctx.agents:
            return None, "no agent data available", {}
        paused = [
            a for a in ctx.agents
            if (a.state == "paused" or a.queue_paused) and (p.agent is None or a.agent == p.agent)
        ]
        if p.agent and not any(a.agent == p.agent for a in ctx.agents):
            return None, f"agent {p.agent} not present in live data", {}
        over = [a for a in paused if a.pause_seconds >= p.min_minutes * 60]
        if not over:
            return False, "no agent paused beyond threshold", {}
        worst = max(over, key=lambda a: a.pause_seconds)
        return True, (
            f"Agent {worst.agent} paused {worst.pause_seconds // 60}m "
            f"(limit {p.min_minutes:g}m)"
        ), {"agent": worst.agent, "pause_seconds": worst.pause_seconds, "count": len(over)}

    if isinstance(p, NoAgentsParams):
        if p.queue:
            q = ctx.queues.get(p.queue)
            if q is None:
                return None, f"queue {p.queue} not present in live data", {}
            hit = q.agents_available == 0
            return hit, f"Queue {p.queue}: no agents available", {"queue": p.queue}
        if not ctx.agents:
            return None, "no agent data available", {}
        available = [
            a for a in ctx.agents
            if (
                a.state in ("online", "idle", "talking")
                and not a.queue_paused
                and a.state != "paused"
            )
        ]
        hit = len(available) == 0
        return hit, (
            f"No agents available ({len(ctx.agents)} known, 0 ready)"
            if hit else f"{len(available)} agents available"
        ), {"agents_total": len(ctx.agents), "available": len(available)}

    if isinstance(p, TrunkParams):
        if not ctx.trunks:
            return None, "no trunk/registration data available (AMI off or unsupported)", {}
        if p.peer:
            trunk = next((t for t in ctx.trunks if t.peer == p.peer), None)
            if trunk is None:
                return None, f"trunk {p.peer} not present in data", {}
            hit = trunk.status != "registered"
            return hit, f"Trunk {p.peer}: {trunk.status}", {"peer": p.peer, "status": trunk.status}
        down = [t for t in ctx.trunks if t.status == "unregistered"]
        hit = len(down) > 0
        names = ", ".join(t.peer for t in down[:5]) if down else ""
        return hit, (f"Unregistered trunks: {names}" if hit else "All trunks registered"), {
            "unregistered": [t.peer for t in down]
        }

    if isinstance(p, ServiceParams):
        status = ctx.services.get(p.component)
        if status is None:
            return None, f"service {p.component} not probed", {}
        if status == "unknown":
            return None, f"service {p.component} state unknown", {}
        hit = status != "ok"
        return hit, f"Service {p.component}: {status}", {"component": p.component, "status": status}

    return None, f"unhandled rule type {rule_type}", {}


Outcome = Literal["firing", "resolved", "none"]
