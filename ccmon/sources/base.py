"""SourceService interface shared by the live (MySQL+AMI) and demo sources."""
from __future__ import annotations

from datetime import date
from typing import Protocol

from ccmon.sources.capabilities import Capabilities


class SourceService(Protocol):
    def capabilities(self) -> Capabilities: ...

    def live_snapshot(self) -> dict:
        """{queues, agents, campaigns, calls, trunks, source_ok, errors}"""
        ...

    def report_summary(self, day_from: date, day_to: date, sla_seconds: int) -> dict: ...

    def report_series(
        self, day_from: date, day_to: date, bucket: str, sla_seconds: int, queue: str | None
    ) -> list[dict]: ...

    def abandoned_detail(
        self, day: date, queue: str | None, limit: int
    ) -> dict: ...

    def agent_report(self, day_from: date, day_to: date, limit: int) -> list[dict]: ...

    def campaign_report(self, day_from: date, day_to: date, limit: int) -> list[dict]: ...

    def close(self) -> None: ...
