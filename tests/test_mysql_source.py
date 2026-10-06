"""MysqlSource logic tests against a strict fake reader (no Docker needed).

The fake only answers the exact rendered SQL the source issues — an unexpected
query fails the test — so every q.<module>.SQL_* reference, join, and result
mapping is exercised. Real-server behaviour is covered separately by
tests/integration (docker-compose, skips when no server is set).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ccmon.config import Settings
from ccmon.sources import queries as q
from ccmon.sources.mysql_ro import render
from ccmon.sources.mysql_source import MysqlSource

PROD_DB = "postgresql+psycopg://ccmon_test:secret@127.0.0.1:5432/ccmon_test"


def make_settings() -> Settings:
    return Settings(
        app_mode="production",
        database_url=PROD_DB,
        ami_enabled=False,
        issabel_db_password="test-only",
    )


class FakeReader:
    """Dispatches on distinctive SQL fragments; unknown queries fail loudly."""

    TABLES = {
        ("asteriskcdrdb", "cdr"),
        ("asterisk", "queue_log"),
        ("asterisk", "queue_member"),
        ("asterisk", "ps_contacts"),
        ("asterisk", "sippeers"),
        ("call_center", "call_entry"),
        ("call_center", "audit"),
        ("call_center", "campaign"),
        ("call_center", "calls"),
    }
    COLS = {
        "campaign": {"id", "name", "status"},
        "calls": {"id", "id_campaign", "start_time", "duration", "status"},
    }

    def __init__(self, settings: Settings, *, drop: set[str] | None = None):
        self.settings = settings
        self.tables = set(self.TABLES)
        self.drop = drop or set()
        self.tables -= {t for t in self.drop if isinstance(t, tuple)}
        self.sql_log: list[str] = []
        now = datetime.now(UTC)
        self.responses: list[tuple[str, list[dict]]] = [
            # --- queues
            ("WHERE ce.datetime_init IS NOT NULL AND ce.datetime_end IS NULL",
             [{"queue": "800", "active": 2}, {"queue": "801", "active": 0}]),
            ("ce.datetime_init IS NULL AND ce.datetime_entry_queue IS NOT NULL",
             [{"queue": "800", "waiting": 3}]),
            ("SELECT queue_name AS queue, COUNT(*) AS members",
             [{"queue": "800", "members": 3, "paused": 1},
              {"queue": "801", "members": 1, "paused": 0}]),
            ("SELECT queue_name AS queue, name AS device",
             [{"queue": "800", "device": "PJSIP/103", "paused": 1}]),
            ("SELECT DISTINCT q.queue AS queue",
             [{"queue": "800"}, {"queue": "801"}]),
            # --- agents
            ("au.id_break IS NULL AND au.datetime_end IS NULL",
             [{"agent": "101", "name": "Alice", "extension": "101",
               "session_start": (now - timedelta(hours=4)).isoformat()},
              {"agent": "103", "name": "Carol", "extension": "103",
               "session_start": (now - timedelta(hours=5)).isoformat()}]),
            ("au.id_break IS NOT NULL AND au.datetime_end IS NULL",
             [{"agent": "103",
               "pause_start": (now - timedelta(minutes=42)).isoformat(),
               "reason": "Lunch"}]),
            ("ce.datetime_init >= CURDATE()",
             [{"agent": "101", "handled": 7}]),
            ("a.estatus AS estatus",
             [{"agent": "101", "name": "Alice", "estatus": "Active"},
              {"agent": "104", "name": "Dave", "estatus": "Active"}]),
            # --- campaigns
            ("ORDER BY 2",
             [{"campaign": "Promo Dialer", "status": "Active",
               "calls_today": 12, "connected_today": 8}]),
            # --- trunks
            ("ps_contacts",
             [{"peer": "trunk1", "status": "Registered", "detail": "ok"}]),
            ("sippeers",
             [{"peer": "siptrunk", "status": "Registered", "detail": "ok"}]),
            # --- queue_log aggregates
            ("AS answered_within_sla",
             [{"queue": "800", "offered": 10, "answered": 7, "abandoned": 3,
               "talk_total": 600, "avg_wait": 12.5, "answered_within_sla": 6},
              {"queue": "801", "offered": 4, "answered": 3, "abandoned": 1,
               "talk_total": 150, "avg_wait": 8.0, "answered_within_sla": 3}]),
            ("q.event = 'ABANDON'",
             [{"ts": "2026-10-06 10:00:00", "queue": "800",
               "wait_seconds": 60, "caller": "5511003"},
              {"ts": "2026-10-06 11:00:00", "queue": "800",
               "wait_seconds": 90, "caller": "5511004"}]),
            # --- cdr fallback
            ("AS avg_duration",
             [{"total": 4, "answered": 3, "avg_duration": 90.0}]),
            # --- agent report
            ("ORDER BY answered DESC",
             [{"agent": "101", "answered": 7, "avg_wait": 11.0, "talk": 600}]),
            # --- campaign report
            ("campaign camp",
             [{"campaign": "Promo Dialer", "calls": 12, "connected": 8}]),
        ]

    # -- CapabilityProbe interface ------------------------------------------
    def table_exists(self, db: str, table: str) -> bool:
        if (db, table) in self.drop:
            return False
        return (db, table) in self.TABLES

    def columns(self, db: str, table: str) -> set[str]:
        return set(self.COLS.get(table, set()))

    # -- query interface ------------------------------------------------------
    def query(self, sql: str, params: dict | None = None) -> list[dict]:
        assert sql.lstrip().upper().startswith("SELECT"), f"non-SELECT issued: {sql}"
        self.sql_log.append(sql)
        if "AS bucket" in sql:  # time-series query needs bucket keys
            return [{"bucket": "2026-10-05", "queue": "800", "offered": 4,
                     "answered": 3, "abandoned": 1, "avg_wait": 9.0,
                     "answered_within_sla": 3}]
        for fragment, rows in self.responses:
            if fragment in sql and not self._dropped(sql):
                return [dict(r) for r in rows]
        raise AssertionError(f"unexpected query: {sql}")

    def _dropped(self, sql: str) -> bool:
        for drop in self.drop:
            if isinstance(drop, str) and drop in sql:
                return True
        return False

    def close(self) -> None:  # MysqlSource.close() contract
        pass


def source(drop: set[str] | None = None) -> tuple[MysqlSource, FakeReader]:
    settings = make_settings()
    reader = FakeReader(settings, drop=drop)
    return MysqlSource(settings, reader=reader), reader


# ---------------------------------------------------------------- capability
def test_capabilities_probe_everything():
    src, _ = source()
    caps = src.capabilities()
    assert caps.source_ok
    assert caps.cdr and caps.queue_log_table and caps.queue_member
    assert caps.call_center and caps.audit and caps.campaigns
    assert caps.campaign_name_col == "name"
    assert caps.campaign_status_col == "status"
    assert caps.campaign_calls_time_col == "start_time"
    assert caps.ps_contacts
    assert not caps.ami  # ami_enabled=False


def test_capabilities_without_queue_log():
    src, _ = source(drop={("asterisk", "queue_log")})
    caps = src.capabilities()
    assert caps.source_ok and not caps.queue_log_table


# ---------------------------------------------------------------- live state
def test_live_snapshot_merges_queues_agents_campaigns_trunks():
    src, reader = source()
    snap = src.live_snapshot()
    assert snap["source_ok"] is True
    assert snap["errors"] == []

    queues = {row["queue"]: row for row in snap["queues"]}
    assert set(queues) == {"800", "801"}
    assert queues["800"]["calls_waiting"] == 3
    assert queues["800"]["calls_active"] == 2
    assert queues["800"]["agents_online"] == 3
    assert queues["800"]["agents_paused"] == 1
    assert queues["801"]["agents_online"] == 1

    agents = {row["agent"]: row for row in snap["agents"]}
    assert {"101", "103", "104"} <= set(agents)
    assert agents["101"]["state"] == "online"
    assert agents["103"]["state"] == "paused"
    assert agents["103"]["pause_seconds"] >= 41 * 60
    assert agents["104"]["state"] == "offline"
    assert agents["101"]["calls_handled_today"] == 7
    assert agents["103"]["queue_paused"] is True

    campaigns = {row["campaign"]: row for row in snap["campaigns"]}
    assert campaigns["Promo Dialer"]["calls_today"] == 12
    assert campaigns["Promo Dialer"]["connected_today"] == 8

    assert snap["trunks"][0]["peer"] == "trunk1"
    assert snap["trunks"][0]["tech"] == "PJSIP"
    # AMI disabled -> no live channel rows, but DB-only mode still healthy
    assert snap["calls"] == []
    # every issued SQL was a SELECT
    assert reader.sql_log and all(s.lstrip().upper().startswith("SELECT") for s in reader.sql_log)


def test_live_snapshot_trunk_fallback_to_sippeers():
    src, _ = source(drop={("asterisk", "ps_contacts")})
    snap = src.live_snapshot()
    assert snap["source_ok"] is True
    assert snap["errors"] == []
    trunks = {row["peer"]: row for row in snap["trunks"]}
    assert trunks["siptrunk"]["tech"] == "SIP"


def test_live_snapshot_missing_tables_degrade():
    src, _ = source(
        drop={("asterisk", "queue_log"), ("asterisk", "queue_member"),
              ("asterisk", "ps_contacts"), ("call_center", "campaign"),
              ("call_center", "calls")}
    )
    snap = src.live_snapshot()
    assert snap["source_ok"] is True
    assert snap["errors"] == []
    # queues still listed via call_center.queue_call_entry fallback fragment
    assert snap["campaigns"] == []


# ------------------------------------------------------------------- reports
def test_rolling_queue_stats():
    src, _ = source()
    stats = {r["queue"]: r for r in src.rolling_queue_stats(15, 20)}
    assert stats["800"]["handled_rolling"] == 7
    assert stats["800"]["abandoned_rolling"] == 3
    assert stats["800"]["sla_pct"] == 60.0  # 6 within SLA / 10 offered
    assert stats["800"]["avg_wait_seconds"] == 12.5
    assert stats["801"]["handled_rolling"] == 3


def test_report_summary_from_queue_log():
    src, _ = source()
    result = src.report_summary(
        datetime.now(UTC).date() - timedelta(days=1),
        datetime.now(UTC).date(),
        20,
    )
    assert result["source"] == "queue_log"
    assert result["offered"] == 14
    assert result["answered"] == 10
    assert result["abandoned"] == 4
    assert result["abandoned_pct"] == round(4 / 14 * 100, 2)
    assert 11.0 < result["avg_wait_seconds"] < 11.3
    assert result["sla_pct"] == round(9 / 14 * 100, 1)


def test_report_summary_cdr_fallback_without_queue_log():
    src, reader = source(drop={("asterisk", "queue_log")})
    day = datetime.now(UTC).date()
    result = src.report_summary(day, day, 20)
    assert result["source"] == "cdr"
    assert result["offered"] == 4
    assert result["answered"] == 3
    assert result["avg_wait_seconds"] == 90.0
    assert result["sla_pct"] == 0.0  # CDR cannot compute SLA/abandon
    assert any("AS avg_duration" in s for s in reader.sql_log)


def test_report_series_bucketed():
    src, _ = source()
    day = datetime.now(UTC).date()
    points = src.report_series(day - timedelta(days=1), day, "day", 20, None)
    assert len(points) == 1
    assert points[0]["bucket"] == "2026-10-05"
    assert points[0]["offered"] == 4


def test_report_series_queue_filter():
    src, _ = source()
    day = datetime.now(UTC).date()
    assert src.report_series(day - timedelta(days=1), day, "hour", 20, "999") == []


def test_abandoned_detail():
    src, _ = source()
    day = datetime.now(UTC).date()
    data = src.abandoned_detail(day, None, 100)
    assert data["total"] == 2
    assert data["by_queue"] == {"800": 2}
    assert data["avg_wait_seconds"] == 75.0
    assert data["rows"][0]["caller"] in {"5511003", "5511004"}


def test_agent_report_and_campaign_report():
    src, _ = source()
    day = datetime.now(UTC).date()
    agents = src.agent_report(day - timedelta(days=1), day, 100)
    assert agents == [{"agent": "101", "answered": 7, "abandoned": 0,
                       "avg_wait_seconds": 11.0, "talk_seconds": 600.0}]
    campaigns = src.campaign_report(day - timedelta(days=1), day, 100)
    assert campaigns == [{"campaign": "Promo Dialer", "calls": 12,
                          "connected": 8, "connect_rate_pct": 66.7}]


def test_live_snapshot_without_audit_has_no_agents():
    src, _ = source(drop={("call_center", "audit")})
    snap = src.live_snapshot()
    assert snap["source_ok"] is True
    assert snap["errors"] == []
    assert snap["agents"] == []  # no sessions/pauses/roster without audit


@pytest.mark.parametrize("bucket", ["hour", "day"])
def test_series_bucket_expressions_render(bucket):
    settings = make_settings()
    expr = q.queue_log.BUCKET_HOUR if bucket == "hour" else q.queue_log.BUCKET_DAY
    sql = render(
        q.queue_log.SQL_QUEUE_STATS_BY_BUCKET, settings, bucket_expr=expr
    )
    assert "FROM asterisk.queue_log" in sql
    assert "{bucket_expr}" not in sql  # format applied, no leftover placeholder
    assert "%Y" in sql if bucket == "hour" else "DATE(FROM_UNIXTIME" in sql
    assert "%(t0)s" in sql  # time bound preserved for PyMySQL
