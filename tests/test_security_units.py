"""Config fail-fast, secret redaction, rate limiter, AMI parsing (unit level)."""
from __future__ import annotations

import io
import logging

import pytest

from ccmon.config import Settings
from ccmon.logging_setup import setup_logging
from ccmon.ratelimit import SlidingWindowLimiter
from ccmon.sources.ami import (
    AmiClient,
    classify_registration,
    extract_channels,
    extract_queue_members,
    extract_queue_summaries,
    extract_trunks,
    parse_packets,
)

PROD_DB = "postgresql+psycopg://ccmon:sup3rs3cret@127.0.0.1:5432/ccmon"


def test_production_rejects_placeholder_db():
    with pytest.raises(ValueError, match="placeholder"):
        Settings(app_mode="production", database_url="postgresql+psycopg://ccmon:CHANGE_ME@127.0.0.1/ccmon")


def test_production_rejects_placeholder_ami_secret():
    with pytest.raises(ValueError, match="AMI_SECRET"):
        Settings(
            app_mode="production", database_url=PROD_DB, ami_enabled=True, ami_secret="CHANGE_ME"
        )


def test_production_requires_telegram_config_when_enabled():
    with pytest.raises(ValueError, match="TELEGRAM"):
        Settings(
            app_mode="production", database_url=PROD_DB,
            ami_enabled=True, ami_secret="real-ami-secret",
            telegram_enabled=True, telegram_bot_token="", telegram_chat_id="",
        )


def test_production_accepts_real_secrets_and_lists_them():
    s = Settings(
        app_mode="production", database_url=PROD_DB,
        ami_enabled=True, ami_secret="real-ami-secret", telegram_enabled=False,
    )
    redaction = s.redaction_strings()
    assert PROD_DB in redaction
    assert "real-ami-secret" in redaction


def test_development_mode_allows_placeholders():
    assert Settings(app_mode="development").app_mode == "development"


def test_log_filter_masks_secret_in_message():
    setup_logging("INFO", ["hunter2-secret"])
    from ccmon.logging_setup import RedactionFilter

    f = RedactionFilter(["hunter2-secret"])
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "pw=hunter2-secret", None, None)
    assert f.filter(record) is True
    assert "hunter2-secret" not in record.getMessage()
    assert "***REDACTED***" in record.getMessage()


def test_json_logs_mask_secrets_in_tracebacks():
    root = logging.getLogger()
    setup_logging("INFO", ["telegram-bot-token-XYZ"])
    buf = io.StringIO()
    root.handlers[0].stream = buf
    try:
        url = "https://api.telegram.org/bottelegram-bot-token-XYZ/sendMessage"
        raise RuntimeError(f"POST {url} failed")
    except RuntimeError:
        logging.getLogger("test").exception("telegram error")
    out = buf.getvalue()
    assert "telegram-bot-token-XYZ" not in out
    assert "***REDACTED***" in out


def test_rate_limiter_window_and_retry():
    limiter = SlidingWindowLimiter(limit_per_minute=3)
    assert limiter.check("ip:1") == (True, 0)
    assert limiter.check("ip:1") == (True, 0)
    assert limiter.check("ip:1") == (True, 0)
    allowed, retry = limiter.check("ip:1")
    assert allowed is False and retry > 0
    assert limiter.check("ip:2") == (True, 0)  # other key unaffected


def test_rate_limiter_is_thread_safe_and_resets():
    import threading

    limiter = SlidingWindowLimiter(limit_per_minute=100)
    results = []

    def worker():
        for _ in range(50):
            results.append(limiter.check("k")[0])

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 200
    limiter.reset()
    assert limiter.check("k") == (True, 0)


AMI_RAW = (
    "Response: Success\r\nActionID: 1\r\n\r\n"
    "Event: Queue\r\nQueue: 800\r\nCalls: 7\r\nCallsTaken: 3\r\nStrategy: ringall\r\n\r\n"
    "Event: QueueMember\r\nQueue: 800\r\nName: PJSIP/101\r\nPaused: Yes\r\n\r\n"
    "Event: QueueMember\r\nQueue: 800\r\nName: PJSIP/102\r\nPaused: No\r\n\r\n"
    "Event: QueueStatusComplete\r\n\r\n"
)


def test_parse_packets_and_queue_extractors():
    packets = parse_packets(AMI_RAW)
    assert len(packets) == 5
    queues = extract_queue_summaries(packets)
    assert queues == [{"queue": "800", "calls": 7, "calls_taken": 3, "strategy": "ringall"}]
    members = extract_queue_members(packets)
    assert members[0]["paused"] is True and members[1]["paused"] is False


def test_channel_extractor():
    raw = (
        "Event: CoreShowChannel\r\nChannel: PJSIP/101-00000001\r\nChannelStateDesc: Up\r\n"
        "Exten: 800\r\nCallerIDnum: 5551234\r\n\r\n"
        "Event: CoreShowChannelsComplete\r\nEventList: Complete\r\n\r\n"
    )
    channels = extract_channels(parse_packets(raw))
    assert channels[0]["state"] == "Up"
    assert channels[0]["caller"] == "5551234"


def test_trunk_extractor_classifies_statuses():
    raw = (
        "Event: Endpoint\r\nEndpoint: trunk-sipflow\r\nStatus: OK (12 ms)\r\n\r\n"
        "Event: Endpoint\r\nEndpoint: trunk-backup\r\nStatus: Unreachable\r\n\r\n"
        "Event: PJSIPShowEndpointsComplete\r\n\r\n"
    )
    trunks = extract_trunks(parse_packets(raw))
    by_peer = {t["peer"]: t["status"] for t in trunks}
    assert by_peer["trunk-sipflow"] == "registered"
    assert by_peer["trunk-backup"] == "unregistered"


def test_classify_registration_ordering():
    assert classify_registration("OK (5 ms)") == "registered"
    assert classify_registration("unregistered") == "unregistered"
    assert classify_registration("NONEXISTENT") == "unregistered"
    assert classify_registration("some odd state") == "unknown"


def test_ami_client_refuses_when_disabled():
    s = Settings(app_mode="development", ami_enabled=False)
    with pytest.raises(Exception, match="disabled"):
        AmiClient(s).connect()


def test_ami_stops_on_complete_or_response():
    s = Settings(app_mode="development")
    client = AmiClient(s)
    assert client._stopped({"event": "QueueStatusComplete"}, "*complete") is True
    assert client._stopped({"event": "Queue", "queue": "800"}, "*complete") is False
    assert client._stopped({"response": "Success"}, None) is True
    assert client._stopped({"response": "Error"}, None) is False
