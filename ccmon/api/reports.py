"""Historical reports — every parameter validated (dates, range, queue, bucket)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ccmon.auth import require_scope
from ccmon.db import get_db
from ccmon.schemas import (
    QUEUE_PATTERN,
    AbandonedOut,
    AgentReportOut,
    CampaignReportOut,
    ReportSummary,
    SeriesOut,
)

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])
read_scope = require_scope("read")


def _resolve_range(
    date_from: date | None, date_to: date | None, settings
) -> tuple[date, date]:
    end = date_to or date.today()
    start = date_from or (end - timedelta(days=6))
    if start > end:
        raise HTTPException(status_code=422, detail="date_from must be <= date_to")
    if end > date.today():
        raise HTTPException(status_code=422, detail="date_to cannot be in the future")
    if (end - start).days + 1 > settings.report_max_days:
        raise HTTPException(
            status_code=422,
            detail=f"range exceeds REPORT_MAX_DAYS={settings.report_max_days}",
        )
    if start.year < 2000:
        raise HTTPException(status_code=422, detail="date_from too old")
    return start, end


@router.get("/summary", response_model=ReportSummary)
def summary(
    request: Request,
    date_from: date | None = None,
    date_to: date | None = None,
    session: Session = Depends(get_db),
    _=Depends(read_scope),
):
    settings = request.app.state.settings
    start, end = _resolve_range(date_from, date_to, settings)
    data = request.app.state.source.report_summary(start, end, settings.sla_seconds)
    return ReportSummary(
        date_from=data["date_from"], date_to=data["date_to"],
        offered=data["offered"], answered=data["answered"],
        abandoned=data["abandoned"], abandoned_pct=data["abandoned_pct"],
        avg_wait_seconds=data["avg_wait_seconds"], sla_pct=data["sla_pct"],
        sla_seconds=settings.sla_seconds, sla_target_pct=settings.sla_target_pct,
        source=data["source"],
    )


@router.get("/series", response_model=SeriesOut)
def series(
    request: Request,
    date_from: date | None = None,
    date_to: date | None = None,
    bucket: Literal["hour", "day"] = "hour",
    queue: str | None = Query(default=None, pattern=QUEUE_PATTERN),
    _=Depends(read_scope),
):
    settings = request.app.state.settings
    start, end = _resolve_range(date_from, date_to, settings)
    rows = request.app.state.source.report_series(
        start, end, bucket, settings.sla_seconds, queue
    )
    truncated = len(rows) >= settings.report_max_rows
    return SeriesOut(points=rows[: settings.report_max_rows], truncated=truncated)


@router.get("/abandoned", response_model=AbandonedOut)
def abandoned(
    request: Request,
    on: date | None = Query(default=None, alias="date"),
    queue: str | None = Query(default=None, pattern=QUEUE_PATTERN),
    limit: int = Query(default=500, ge=1, le=5000),
    _=Depends(read_scope),
):
    settings = request.app.state.settings
    day = on or date.today()
    if day > date.today() or day.year < 2000:
        raise HTTPException(status_code=422, detail="invalid date")
    data = request.app.state.source.abandoned_detail(
        day, queue, min(limit, settings.report_max_rows)
    )
    return AbandonedOut(
        date=day, total=data["total"], by_queue=data["by_queue"],
        by_hour=data["by_hour"], avg_wait_seconds=data["avg_wait_seconds"],
        rows=data["rows"], truncated=len(data["rows"]) >= limit,
    )


@router.get("/agents", response_model=AgentReportOut)
def agents(
    request: Request,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(default=200, ge=1, le=2000),
    _=Depends(read_scope),
):
    settings = request.app.state.settings
    start, end = _resolve_range(date_from, date_to, settings)
    rows = request.app.state.source.agent_report(start, end, min(limit, settings.report_max_rows))
    return AgentReportOut(rows=rows, truncated=len(rows) >= limit)


@router.get("/campaigns", response_model=CampaignReportOut)
def campaigns(
    request: Request,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    _=Depends(read_scope),
):
    settings = request.app.state.settings
    start, end = _resolve_range(date_from, date_to, settings)
    rows = request.app.state.source.campaign_report(start, end, limit)
    return CampaignReportOut(rows=rows, truncated=len(rows) >= limit)
