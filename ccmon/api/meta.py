"""Uptime probe (unauthenticated) + capability discovery (authenticated)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ccmon.auth import require_scope
from ccmon.db import get_db
from ccmon.schemas import CapabilitiesOut

router = APIRouter(tags=["meta"])


@router.get("/healthz")
def healthz(request: Request):
    return {"status": "ok", "mode": request.app.state.settings.app_mode}


@router.get("/api/v1/meta/capabilities", response_model=CapabilitiesOut)
def capabilities(
    request: Request, session: Session = Depends(get_db), _=Depends(require_scope("read"))
):
    settings = request.app.state.settings
    caps = request.app.state.source.capabilities()
    return CapabilitiesOut(
        mode=settings.app_mode,
        databases={
            "cdr": caps.cdr, "queue_log_table": caps.queue_log_table,
            "queue_log_file": caps.queue_log_file, "call_center": caps.call_center,
            "campaigns": caps.campaigns, "queue_member": caps.queue_member,
            "ps_contacts": caps.ps_contacts, "sippeers": caps.sippeers,
            "source_ok": caps.source_ok,
        },
        ami=caps.ami, systemd=caps.systemd, telegram=caps.telegram,
        openapi=settings.openapi_enabled,
    )
