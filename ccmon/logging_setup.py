"""JSON logging with a tested secret-redaction filter.

Every handler installs a RedactionFilter seeded with the configured secret
values (DSN, DB/AMI/Telegram passwords), so a secret can never be emitted even
if a library interpolates it into a message or exception.
"""
from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterable
from datetime import UTC, datetime

REDACTED = "***REDACTED***"


class RedactionFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._secrets = [s for s in secrets if s and len(s) >= 4]

    def add_secret(self, secret: str) -> None:
        if secret and len(secret) >= 4 and secret not in self._secrets:
            self._secrets.append(secret)

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # malformed log call must not break logging
            message = str(record.msg)
        record.msg = self.redact(message)
        record.args = None
        return True


class JsonFormatter(logging.Formatter):
    """Renders one JSON line and redacts the COMPLETE output, including
    exception tracebacks (which bypass record-level filters)."""

    def __init__(self, redactor: RedactionFilter | None = None) -> None:
        super().__init__()
        self._redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        text = json.dumps(payload, ensure_ascii=False, default=str)
        if self._redactor is not None:
            text = self._redactor.redact(text)
        return text


def setup_logging(level: str = "INFO", secrets: Iterable[str] = ()) -> RedactionFilter:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    redaction = RedactionFilter(secrets)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(redaction))
    handler.addFilter(redaction)
    root.addHandler(handler)
    root.setLevel(level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    return redaction
