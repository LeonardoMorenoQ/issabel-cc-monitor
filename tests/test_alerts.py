"""Alert rules, state machine (sustain/cooldown/stale-data), Telegram sender."""
from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
from pydantic import ValidationError

from ccmon.alerts.engine import evaluate_rules
from ccmon.alerts.rules import (
    AgentMetrics,
    EvaluationContext,
    QueueMetrics,
    TrunkMetrics,
    evaluate_condition,
    parse_params,
)
from ccmon.alerts.telegram import TelegramSender, format_event
from ccmon.config import Settings
from ccmon.db import session_scope
from ccmon.models import AlertEvent, AlertRule


# ------------------------------------------------------------------ params
def test_params_require_threshold():
    with pytest.raises(ValidationError):
        parse_params("queue_waiting_gt", {"queue": "800"})


def test_params_reject_unknown_keys_and_types():
    with pytest.raises(ValidationError):
        parse_params("queue_waiting_gt", {"queue": "800", "threshold": 5, "extra": 1})
    with pytest.raises(ValidationError):
        parse_params("service_down", {"component": "asterisk; rm -rf /"})


def test_unknown_rule_type_rejected():
    with pytest.raises(ValueError):
        parse_params("definitely_not_a_rule", {})


# -------------------------------------------------------------- conditions
def ctx(**kwargs) -> EvaluationContext:
    return EvaluationContext(source_ok=True, **kwargs)


def test_waiting_condition_and_missing_queue():
    c = ctx(queues={"800": QueueMetrics(queue="800", waiting=7)})
    hit, msg, payload = evaluate_condition("queue_waiting_gt", {"queue": "800", "threshold": 5}, c)
    assert hit is True and payload["waiting"] == 7
    miss, _, _ = evaluate_condition("queue_waiting_gt", {"queue": "801", "threshold": 5}, c)
    assert miss is None  # 801 unknown -> insufficient data, NOT false


def test_stale_source_never_fires():
    c = EvaluationContext(source_ok=False, queues={"800": QueueMetrics(queue="800", waiting=99)})
    assert evaluate_condition("queue_waiting_gt", {"queue": "800", "threshold": 5}, c)[0] is None


def test_abandoned_rolling_window():
    c = ctx(queues={"800": QueueMetrics(queue="800", abandoned_rolling=4)})
    hit, _, _ = evaluate_condition("queue_abandoned_gt", {"threshold": 3, "window_minutes": 15}, c)
    assert hit is True


def test_paused_agent_condition():
    c = ctx(agents=[AgentMetrics(agent="103", state="paused", pause_seconds=2400)])
    hit, msg, payload = evaluate_condition("agent_paused_minutes_gt", {"min_minutes": 15}, c)
    assert hit is True and payload["agent"] == "103"
    hit2, _, _ = evaluate_condition("agent_paused_minutes_gt", {"min_minutes": 60}, c)
    assert hit2 is False


def test_no_agents_available():
    c = ctx(
        agents=[AgentMetrics(agent="1", state="paused"), AgentMetrics(agent="2", state="offline")]
    )
    assert evaluate_condition("no_agents_available", {}, c)[0] is True
    c2 = ctx(agents=[AgentMetrics(agent="1", state="online")])
    assert evaluate_condition("no_agents_available", {}, c2)[0] is False
    assert evaluate_condition("no_agents_available", {}, ctx())[0] is None  # no data


def test_trunk_condition():
    c = ctx(trunks=[TrunkMetrics(peer="t1", status="registered"),
                    TrunkMetrics(peer="t2", status="unregistered")])
    hit, _, payload = evaluate_condition("trunk_not_registered", {}, c)
    assert hit is True and payload["unregistered"] == ["t2"]
    assert evaluate_condition("trunk_not_registered", {"peer": "t1"}, c)[0] is False
    assert evaluate_condition("trunk_not_registered", {}, ctx())[0] is None  # AMI off


def test_service_condition():
    c = ctx(services={"asterisk": "down"})
    assert evaluate_condition("service_down", {"component": "asterisk"}, c)[0] is True
    c_ok = ctx(services={"asterisk": "ok"})
    assert evaluate_condition("service_down", {"component": "asterisk"}, c_ok)[0] is False
    assert evaluate_condition("service_down", {"component": "issabeldialer"}, c_ok)[0] is None


# ----------------------------------------------------------------- engine
class Recorder:
    def __init__(self):
        self.messages = []

    async def send(self, text: str) -> bool:
        self.messages.append(text)
        return True


def _rule(rule_type="queue_waiting_gt", params=None, for_seconds=0, cooldown=10) -> int:
    with session_scope() as s:
        rule = AlertRule(
            type=rule_type, name="test rule", params=params or {"queue": "800", "threshold": 5},
            for_seconds=for_seconds, cooldown_minutes=cooldown,
        )
        s.add(rule)
        s.flush()
        return rule.id


def _events(rule_id):
    with session_scope() as s:
        return [
            (e.state, e.message)
            for e in s.query(AlertEvent).filter(AlertEvent.rule_id == rule_id)
            .order_by(AlertEvent.id)
        ]


@pytest.mark.asyncio
async def test_engine_fires_after_sustain_and_never_twice(env):
    rule_id = _rule(for_seconds=3600)  # long sustain
    rec = Recorder()

    def ctx_waiting(waiting):
        qm = QueueMetrics(queue="800", waiting=waiting)
        return EvaluationContext(source_ok=True, queues={"800": qm})

    with session_scope() as s:
        await evaluate_rules(s, ctx_waiting(7), rec)  # starts pending
    assert _events(rule_id) == [] and rec.messages == []

    with session_scope() as s:
        rule = s.get(AlertRule, rule_id)
        rule.for_seconds = 0  # sustain satisfied
        await evaluate_rules(s, ctx_waiting(7), rec)  # fires
    events = _events(rule_id)
    assert [e[0] for e in events] == ["firing"]
    assert len(rec.messages) == 1 and "ALERT" in rec.messages[0]

    with session_scope() as s:  # still true -> no duplicate
        await evaluate_rules(s, ctx_waiting(7), rec)
    assert len(_events(rule_id)) == 1 and len(rec.messages) == 1


@pytest.mark.asyncio
async def test_engine_resolves_only_on_false_not_on_missing_data(env):
    rule_id = _rule(for_seconds=0)
    rec = Recorder()
    firing = EvaluationContext(source_ok=True, queues={"800": QueueMetrics(queue="800", waiting=9)})
    with session_scope() as s:
        await evaluate_rules(s, firing, rec)
    assert [e[0] for e in _events(rule_id)] == ["firing"]

    # stale source: must NOT resolve, must NOT re-fire
    stale = EvaluationContext(source_ok=False, queues={})
    with session_scope() as s:
        await evaluate_rules(s, stale, rec)
    assert [e[0] for e in _events(rule_id)] == ["firing"]
    assert len(rec.messages) == 1

    # condition false -> resolve with notification
    clear = EvaluationContext(source_ok=True, queues={"800": QueueMetrics(queue="800", waiting=0)})
    with session_scope() as s:
        await evaluate_rules(s, clear, rec)
    states = [e[0] for e in _events(rule_id)]
    assert states == ["firing", "resolved"]
    assert len(rec.messages) == 2 and "RESOLVED" in rec.messages[1]


@pytest.mark.asyncio
async def test_engine_restart_does_not_refire(env):
    rule_id = _rule(for_seconds=0, cooldown=60)
    rec = Recorder()
    firing = EvaluationContext(source_ok=True, queues={"800": QueueMetrics(queue="800", waiting=9)})
    with session_scope() as s:
        await evaluate_rules(s, firing, rec)
    # simulate restart: new evaluation cycle with same persisted state
    with session_scope() as s:
        await evaluate_rules(s, firing, rec)
    assert len(_events(rule_id)) == 1 and len(rec.messages) == 1


# ---------------------------------------------------------------- telegram
def _sender(handler) -> TelegramSender:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    s = Settings(
        app_mode="development", telegram_enabled=True,
        telegram_bot_token="tok-123", telegram_chat_id="42",
    )
    return TelegramSender(s, client=client)


def test_telegram_success_and_failures():
    async def run():
        calls = {"n": 0}

        def ok(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json={"ok": True})

        assert await _sender(ok).send("<b>hello</b>") is True
        assert calls["n"] == 1

        def server_error(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(500, json={})

        assert await _sender(server_error).send("retry me") is False
        assert calls["n"] == 4  # initial attempt + 3 bounded retries

        def rejected(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"ok": False})

        assert await _sender(rejected).send("bad") is False  # no retry on 4xx

    asyncio.run(run())


def test_telegram_disabled_sends_nothing():
    async def run():
        s = Settings(app_mode="development", telegram_enabled=False)

        def fail(request: httpx.Request) -> httpx.Response:
            raise AssertionError("must not be called")

        sender = TelegramSender(s, client=httpx.AsyncClient(transport=httpx.MockTransport(fail)))
        assert await sender.send("x") is False

    asyncio.run(run())


def test_telegram_sender_never_logs_token(caplog):
    async def run():
        def explode(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=request)

        s = Settings(app_mode="development", telegram_enabled=True,
                     telegram_bot_token="secret-tok-999", telegram_chat_id="1")
        sender = TelegramSender(s, client=httpx.AsyncClient(transport=httpx.MockTransport(explode)))
        with caplog.at_level(logging.DEBUG):
            assert await sender.send("msg") is False

    asyncio.run(run())
    assert "secret-tok-999" not in caplog.text


def test_format_event_escapes_html():
    msg = format_event("<script>alert(1)</script>", "critical", "firing", "queue <b>800</b>")
    assert "<script>" not in msg
    assert "&lt;script&gt;" in msg
