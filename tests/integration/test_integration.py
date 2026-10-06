"""Real-server integration tests (MariaDB + PostgreSQL).

These run only when both environment variables point at live servers, e.g.
from the compose stack in this directory:

    docker compose -f tests/integration/docker-compose.test.yml up -d --wait
    export CCMON_IT_MYSQL_URL="mysql://ccmon_it:ccmon_it@127.0.0.1:3307"
    export CCMON_IT_PG_URL="postgresql+psycopg://ccmon_it:ccmon_it@127.0.0.1:5433/ccmon_it"
    pytest tests/integration

Without them the module skips — a skip is reported as a skip, never a pass.
"""
from __future__ import annotations

import os
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import unquote, urlparse

import pymysql
import pytest

from ccmon.config import Settings
from ccmon.db import configure_engine, session_scope
from ccmon.models import Base, QueueSnapshot
from ccmon.sources import queries as q
from ccmon.sources.mysql_ro import MysqlReader, render
from ccmon.sources.mysql_source import MysqlSource

MYSQL_URL = os.environ.get("CCMON_IT_MYSQL_URL", "")
PG_URL = os.environ.get("CCMON_IT_PG_URL", "")

pytestmark = pytest.mark.skipif(
    not (MYSQL_URL and PG_URL),
    reason=(
        "integration servers not available — set CCMON_IT_MYSQL_URL and "
        "CCMON_IT_PG_URL (tests/integration/docker-compose.test.yml)"
    ),
)


def _settings() -> Settings:
    u = urlparse(MYSQL_URL)
    return Settings(
        app_mode="production",
        database_url=PG_URL,
        ami_enabled=False,
        issabel_db_host=u.hostname or "127.0.0.1",
        issabel_db_port=u.port or 3306,
        issabel_db_user=unquote(u.username or "ccmon_it"),
        issabel_db_password=unquote(u.password or "ccmon_it"),
    )


# ---------------------------------------------------------------------- MySQL
def test_reader_executes_all_source_queries():
    settings = _settings()
    reader = MysqlReader(settings)
    day = date.today()
    t0 = int(datetime.combine(day, time.min, tzinfo=UTC).timestamp())
    t1 = int(datetime.combine(day + timedelta(days=1), time.min, tzinfo=UTC).timestamp())

    stats = reader.query(
        render(q.queue_log.SQL_QUEUE_STATS, settings),
        {"t0": t0, "t1": t1, "sla_seconds": settings.sla_seconds},
    )
    by_queue = {str(r["queue"]): r for r in stats}
    assert "800" in by_queue, "seed.sql queue_log rows missing"
    assert int(by_queue["800"]["offered"] or 0) == 5
    assert int(by_queue["800"]["answered"] or 0) == 3
    assert int(by_queue["800"]["abandoned"] or 0) == 2

    sessions = reader.query(render(q.agents.SQL_OPEN_SESSIONS, settings))
    assert {"101", "102", "103"} <= {str(r["agent"]) for r in sessions}

    pauses = reader.query(render(q.agents.SQL_OPEN_PAUSES, settings))
    assert any(str(r["agent"]) == "103" for r in pauses)


def test_reader_session_is_read_only():
    settings = _settings()
    reader = MysqlReader(settings)
    with reader.connection() as conn:
        with conn.cursor() as cur:
            with pytest.raises(pymysql.err.MySQLError):
                cur.execute("CREATE TABLE ccmon_should_not_exist (id INT)")


def test_mysql_source_live_snapshot():
    settings = _settings()
    source = MysqlSource(settings)
    try:
        snap = source.live_snapshot()
        assert snap["source_ok"] is True, snap["errors"]
        queues = {row["queue"]: row for row in snap["queues"]}
        assert "800" in queues
        assert queues["800"]["agents_online"] == 3
        assert queues["800"]["agents_paused"] == 1

        agents = {row["agent"]: row for row in snap["agents"]}
        assert agents["103"]["state"] == "paused"
        assert agents["103"]["pause_seconds"] >= 40 * 60
        assert agents["101"]["state"] == "online"

        campaigns = {row["campaign"]: row for row in snap["campaigns"]}
        assert campaigns["Promo Dialer"]["calls_today"] == 2  # today's `calls` rows

        trunks = {row["peer"]: row for row in snap["trunks"]}
        assert trunks["siptrunk"]["status"] == "registered"  # SQL_SIP_PEERS path

        stats = {r["queue"]: r for r in source.rolling_queue_stats(15, 20)}
        assert stats["800"]["handled_rolling"] == 3

        day = date.today()
        summary = source.report_summary(day, day, 20)
        assert summary["source"] == "queue_log"
        assert summary["offered"] == 7  # 5 (800) + 2 (801) seeded today
        assert summary["answered"] == 4
        assert summary["abandoned"] == 3

        abandoned = source.abandoned_detail(day, None, 100)
        assert abandoned["total"] == 3
        assert abandoned["by_queue"]["800"] == 2
    finally:
        source.close()


# -------------------------------------------------------------------- Postgres
def test_postgres_app_db_roundtrip():
    settings = _settings()
    engine = configure_engine(settings.database_url)
    Base.metadata.create_all(engine)
    try:
        with session_scope() as session:
            session.add(
                QueueSnapshot(
                    queue="it-queue",
                    calls_waiting=2,
                    calls_active=1,
                    agents_online=3,
                    agents_available=1,
                    avg_wait_seconds=9.5,
                    sla_pct=75.0,
                    source="integration",
                    ts=datetime.now(UTC),
                )
            )
        with session_scope() as session:
            row = (
                session.query(QueueSnapshot)
                .filter(QueueSnapshot.queue == "it-queue")
                .order_by(QueueSnapshot.ts.desc())
                .first()
            )
            assert row is not None
            assert row.calls_waiting == 2
            assert row.sla_pct == 75.0
    finally:
        Base.metadata.drop_all(engine)
