"""Every source query must parse as MySQL (sqlglot) and be read-only (SELECT)."""
from __future__ import annotations

import re

import pytest
import sqlglot

from ccmon.config import Settings
from ccmon.sources.mysql_ro import render
from ccmon.sources.queries import agents, campaigns, cdr, health, queue_log, queues

SETTINGS = Settings(app_mode="development")
PARAM_RE = re.compile(r"%\([a-z0-9_]+\)s")


def _prepare(sql: str) -> str:
    """Fill db-name placeholders and neutralize bind params for parsing."""
    filled = render(sql, SETTINGS)
    return PARAM_RE.sub("1", filled)


def _parse(sql: str):
    statement = sqlglot.parse_one(_prepare(sql), dialect="mysql")
    assert statement is not None, "unparseable SQL"
    assert _prepare(sql).strip().upper().startswith("SELECT"), "non-SELECT statement"
    return statement


def test_queue_queries_parse():
    for sql in (
        queues.SQL_ACTIVE_CALLS_PER_QUEUE,
        queues.SQL_WAITING_CALLS_PER_QUEUE,
        queues.SQL_QUEUE_MEMBER_COUNTS,
        queues.SQL_QUEUE_MEMBER_PAUSED,
        queues.SQL_QUEUE_NAMES,
    ):
        _parse(sql)


def test_agent_queries_parse():
    for sql in (
        agents.SQL_OPEN_SESSIONS, agents.SQL_OPEN_PAUSES, agents.SQL_HANDLED_TODAY,
        agents.SQL_ALL_AGENTS, agents.SQL_AGENT_STATS_RANGE,
    ):
        _parse(sql)


def test_queue_log_queries_parse_with_buckets():
    _parse(queue_log.SQL_QUEUE_STATS)
    _parse(queue_log.SQL_ABANDONED_DETAIL)
    for expr in (queue_log.BUCKET_HOUR, queue_log.BUCKET_DAY):
        sql = queue_log.SQL_QUEUE_STATS_BY_BUCKET.replace("{bucket_expr}", expr)
        _parse(sql)


def test_cdr_queries_parse():
    for sql in (cdr.SQL_CDR_SUMMARY, cdr.SQL_CDR_BY_DISPOSITION, cdr.SQL_CDR_HOURLY):
        _parse(sql)


def test_health_and_campaign_queries_parse():
    _parse(health.SQL_PS_CONTACTS)
    _parse(health.SQL_SIP_PEERS)
    _parse(campaigns.SQL_CAMPAIGN_ENTRY_TODAY)
    _parse(campaigns.build_campaign_query("name", "status", "start_time"))
    _parse(campaigns.build_campaign_query("name", None, None))


def test_queue_log_aggregation_has_time_bounds():
    """Unbounded queue_log scans are forbidden (performance guard)."""
    for sql in (queue_log.SQL_QUEUE_STATS, queue_log.SQL_ABANDONED_DETAIL):
        prepared = _prepare(sql)
        assert "CAST(time AS UNSIGNED) >=" in prepared or "q.time AS UNSIGNED) >=" in prepared
        assert "LIMIT" in prepared.upper() or "GROUP BY" in prepared.upper()


def test_sla_window_is_parametrized_not_hardcoded():
    assert "%(sla_seconds)s" in queue_log.SQL_QUEUE_STATS
    assert "%(sla_seconds)s" in queue_log.SQL_QUEUE_STATS_BY_BUCKET


def _all_sql() -> list[str]:
    modules = (queues, agents, queue_log, cdr, campaigns, health)
    constants = [
        getattr(mod, name)
        for mod in modules
        for name in dir(mod)
        if name.startswith("SQL_") and "{bucket_expr}" not in getattr(mod, name)
    ]
    dynamic = [
        campaigns.build_campaign_query("name", "status", "start_time"),
        campaigns.build_campaign_query("name", None, None),
        campaigns.build_campaign_report_query("start_time"),
        queue_log.SQL_QUEUE_STATS_BY_BUCKET.replace("{bucket_expr}", queue_log.BUCKET_HOUR),
        queue_log.SQL_QUEUE_STATS_BY_BUCKET.replace("{bucket_expr}", queue_log.BUCKET_DAY),
    ]
    return constants + dynamic


def test_sql_survives_pymysql_percent_substitution():
    """PyMySQL runs `query % args` on EVERY executed statement (even with an
    empty params dict). A literal '%' that is not doubled crashes at runtime
    with "unsupported format character" — invisible to parse-level tests.
    """
    for sql in _all_sql():
        filled = render(sql, SETTINGS)
        values = {m: "1" for m in re.findall(r"%\(([a-z0-9_]+)\)s", filled)}
        try:
            filled % values
        except (ValueError, KeyError) as exc:
            pytest.fail(f"SQL breaks under PyMySQL %-substitution: {exc}\n{sql[:200]}")
