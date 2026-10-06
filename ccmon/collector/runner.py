"""Async collector loops (`ccmon collect`): live, history, maintenance.

Every cycle is exception-isolated with exponential backoff + jitter, so an
unreachable AMI/DB never kills the process and a restart never double-fires
alerts (alert state lives in the database).
"""
from __future__ import annotations

import asyncio
import logging
import random
import time

from ccmon.alerts.engine import evaluate_rules
from ccmon.alerts.telegram import TelegramSender
from ccmon.collector import health, rollups
from ccmon.collector.snapshot import build_context, merge_rolling, persist_live, touch_heartbeat
from ccmon.db import session_scope

log = logging.getLogger("ccmon.collector")


class RollingCache:
    """Last computed rolling stats (refreshed every history cycle)."""

    def __init__(self) -> None:
        self._rows: list[dict] = []
        self._ts: float = 0.0

    def set(self, rows: list[dict]) -> None:
        self._rows = list(rows)
        self._ts = time.monotonic()

    def get(self) -> list[dict]:
        return list(self._rows)

    def age_seconds(self) -> float:
        return time.monotonic() - self._ts if self._ts else float("inf")


async def live_cycle(settings, source, sender, cache: RollingCache) -> None:
    snap = await asyncio.to_thread(source.live_snapshot)
    rolling = cache.get()
    merge_rolling(snap.setdefault("queues", []), rolling)
    services = await health.probe_services(settings.probe_service_list)
    services.append(await asyncio.to_thread(health.source_health, source))
    with session_scope() as session:
        persist_live(session, snap, rolling, services)
        context = build_context(snap, services, bool(snap.get("source_ok")))
        await evaluate_rules(session, context, sender)
    if snap.get("errors"):
        log.debug("live snapshot warnings: %s", snap["errors"])


async def history_cycle(settings, source, cache: RollingCache) -> None:
    rows = await asyncio.to_thread(
        source.rolling_queue_stats, 15, settings.sla_seconds
    )
    cache.set(rows)
    with session_scope() as session:
        touch_heartbeat(session, "history")


async def maintenance_cycle(settings, source) -> None:
    with session_scope() as session:
        built = await asyncio.to_thread(rollups.build_rollups, source, session, settings)
        deleted = rollups.prune(session, settings)
        touch_heartbeat(session, "maintenance")
    log.info("maintenance: %d rollup rows rebuilt, %d rows pruned", built, deleted)


async def _loop(name: str, interval: float, fn, *args) -> None:
    delay = 2.0
    while True:
        started = time.monotonic()
        try:
            await fn(*args)
            delay = 2.0
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s cycle failed", name)
            await asyncio.sleep(min(delay + random.uniform(0, 1), 60.0))
            delay = min(delay * 2, 60.0)
            continue
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(0.5, interval - elapsed))


async def run(settings, source, sender=None) -> None:
    """Run all collector loops forever (until cancelled)."""
    sender = sender or TelegramSender(settings)
    cache = RollingCache()
    try:
        cache.set(await asyncio.to_thread(source.rolling_queue_stats, 15, settings.sla_seconds))
    except Exception as exc:
        log.warning("initial rolling stats failed (will retry): %s", exc)
    log.info(
        "collector started: live=%ss history=%ss rollup=%smin",
        settings.collect_live_seconds,
        settings.collect_history_seconds,
        settings.collect_rollup_minutes,
    )
    await asyncio.gather(
        _loop("live", settings.collect_live_seconds, live_cycle, settings, source, sender, cache),
        _loop(
            "history", settings.collect_history_seconds, history_cycle,
            settings, source, cache,
        ),
        _loop(
            "maintenance", settings.collect_rollup_minutes * 60, maintenance_cycle,
            settings, source,
        ),
    )
