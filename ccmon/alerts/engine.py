"""Alert state machine: pending -> firing -> resolved, with sustain + cooldown.

Semantics (QA-approved):
  * condition True  -> starts/continues `pending_since`; fires only after
    `for_seconds` sustained AND last_state != firing AND cooldown elapsed.
  * condition False -> clears pending; resolves an active alert (resolve is
    never cooldown-gated, but is only sent on the firing->resolved transition).
  * condition None  -> NO transition at all: stale/unreachable source data can
    neither fire nor resolve an alert.
All transitions are persisted, so a collector restart never re-sends an alert.
"""
from __future__ import annotations

import logging
from typing import Protocol

from sqlalchemy.orm import Session

from ccmon.alerts.rules import EvaluationContext, evaluate_condition, parse_params
from ccmon.alerts.telegram import format_event
from ccmon.db import as_utc, utcnow
from ccmon.models import AlertEvent, AlertRule

log = logging.getLogger(__name__)


class Sender(Protocol):
    async def send(self, text: str) -> bool: ...


async def evaluate_rules(
    session: Session, ctx: EvaluationContext, sender: Sender
) -> list[AlertEvent]:
    rules = session.query(AlertRule).filter(AlertRule.enabled.is_(True)).all()
    now = utcnow()
    created: list[AlertEvent] = []
    for rule in rules:
        try:
            params = parse_params(rule.type, rule.params or {})
            condition, message, payload = evaluate_condition(rule.type, params, ctx)
        except Exception as exc:
            log.warning("rule id=%s type=%s skipped (bad config): %s", rule.id, rule.type, exc)
            continue
        if condition is None:
            continue  # insufficient data: never fire, never resolve

        if condition:
            if rule.pending_since is None:
                rule.pending_since = now  # persisted: sustain survives restarts
            pending = as_utc(rule.pending_since) or now
            if (now - pending).total_seconds() < rule.for_seconds:
                continue
            if rule.last_state == "firing":
                continue
            last_fired = as_utc(rule.last_fired_at)
            if last_fired and (now - last_fired).total_seconds() < rule.cooldown_minutes * 60:
                continue  # cooldown suppresses re-fire spam
            rule.last_state = "firing"
            rule.last_fired_at = now
            event = _record(session, rule, "firing", message, payload, now)
            created.append(event)
            await _notify(sender, rule, "firing", message)
        else:
            if rule.pending_since is not None:
                rule.pending_since = None
            if rule.last_state == "firing":
                rule.last_state = "resolved"
                rule.last_resolved_at = now
                event = _record(session, rule, "resolved", message, payload, now)
                created.append(event)
                await _notify(sender, rule, "resolved", message)
    session.commit()
    return created


def _record(
    session: Session,
    rule: AlertRule,
    state: str,
    message: str,
    payload: dict,
    now,
) -> AlertEvent:
    event = AlertEvent(
        rule_id=rule.id,
        ts=now,
        state=state,
        message=message,
        payload={**payload, "rule": rule.name, "type": rule.type, "severity": rule.severity},
    )
    session.add(event)
    session.flush()
    return event


async def _notify(sender: Sender, rule: AlertRule, state: str, message: str) -> None:
    try:
        await sender.send(format_event(rule.name, rule.severity, state, message))
    except Exception as exc:  # notifications must never break collection
        log.error("alert notification failed for rule id=%s: %s", rule.id, exc)
