"""Alert rules CRUD (write scope) + event feed (read scope)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ccmon.auth import Principal, audit, require_scope
from ccmon.db import get_db, utcnow
from ccmon.models import AlertEvent, AlertRule
from ccmon.schemas import AlertEventOut, AlertRuleCreate, AlertRuleOut, AlertRuleUpdate

router = APIRouter(prefix="/api/v1", tags=["alerts"])
read_scope = require_scope("read")
write_scope = require_scope("rules:write")


@router.get("/alert-rules", response_model=list[AlertRuleOut])
def list_rules(session: Session = Depends(get_db), _=Depends(read_scope)):
    return session.query(AlertRule).order_by(AlertRule.id).all()


@router.post("/alert-rules", response_model=AlertRuleOut, status_code=201)
def create_rule(
    payload: AlertRuleCreate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(write_scope),
):
    rule = AlertRule(
        type=payload.type, name=payload.name, params=payload.params,
        severity=payload.severity, for_seconds=payload.for_seconds,
        cooldown_minutes=payload.cooldown_minutes, enabled=payload.enabled,
    )
    session.add(rule)
    session.flush()
    audit(session, "alert_rule.create", principal, f"{rule.type}: {rule.name}", request)
    return rule


@router.get("/alert-rules/{rule_id}", response_model=AlertRuleOut)
def get_rule(rule_id: int, session: Session = Depends(get_db), _=Depends(read_scope)):
    rule = session.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="rule not found")
    return rule


@router.patch("/alert-rules/{rule_id}", response_model=AlertRuleOut)
def update_rule(
    rule_id: int,
    payload: AlertRuleUpdate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(write_scope),
):
    rule = session.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="rule not found")
    data = payload.model_dump(exclude_unset=True)
    if "params" in data:
        from ccmon.alerts.rules import parse_params

        try:
            data["params"] = parse_params(rule.type, data["params"])
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"invalid params: {exc}") from None
    for key, value in data.items():
        setattr(rule, key, value)
    rule.updated_at = utcnow()
    audit(session, "alert_rule.update", principal, f"id={rule_id} {sorted(data)}", request)
    return rule


@router.delete("/alert-rules/{rule_id}", status_code=204)
def delete_rule(
    rule_id: int,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(write_scope),
):
    rule = session.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="rule not found")
    label = f"{rule.type}: {rule.name}"
    session.delete(rule)
    audit(session, "alert_rule.delete", principal, label, request)


@router.get("/alerts/events", response_model=list[AlertEventOut])
def list_events(
    state: str | None = Query(default=None, pattern="^(firing|resolved|insufficient_data)$"),
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_db),
    _=Depends(read_scope),
):
    query = session.query(AlertEvent).order_by(AlertEvent.ts.desc(), AlertEvent.id.desc())
    if state:
        query = query.filter(AlertEvent.state == state)
    return query.limit(limit).all()
