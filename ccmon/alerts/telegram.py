"""Telegram notifier: retries with backoff, HTML escaping, token never logged.

The HTTP URL contains the bot token; any exception text that reaches logging
passes through the redaction filter seeded with settings.redaction_strings(),
so the token cannot leak into logs even on transport errors.
"""
from __future__ import annotations

import asyncio
import html
import logging

import httpx

from ccmon.config import Settings

log = logging.getLogger("ccmon.telegram")


class TelegramSender:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client  # injectable for tests

    @property
    def enabled(self) -> bool:
        return bool(self._settings.telegram_enabled)

    async def send(self, text: str) -> bool:
        if not self.enabled:
            log.debug("telegram disabled; suppressed message: %s", text)
            return False
        token = self._settings.telegram_bot_token.get_secret_value()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": self._settings.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=10.0)
        try:
            for attempt in range(3):
                try:
                    response = await client.post(url, json=payload)
                except httpx.HTTPError as exc:
                    log.warning(
                        "telegram transport error (attempt %d/3): %s", attempt + 1, exc
                    )
                    await asyncio.sleep(0.5 * 2**attempt)
                    continue
                if response.status_code == 200:
                    try:
                        if response.json().get("ok"):
                            return True
                    except ValueError:
                        pass
                if response.status_code == 429:
                    retry_after = 2
                    try:
                        retry_after = int(
                            response.json().get("parameters", {}).get("retry_after", 2)
                        )
                    except (ValueError, TypeError, AttributeError):
                        pass
                    await asyncio.sleep(min(max(retry_after, 1), 10))
                    continue
                if response.status_code >= 500:
                    log.warning(
                        "telegram server error (attempt %d/3): HTTP %d",
                        attempt + 1, response.status_code,
                    )
                    await asyncio.sleep(0.5 * 2**attempt)
                    continue
                log.error("telegram rejected message: HTTP %d", response.status_code)
                return False  # 4xx: do not retry
            log.error("telegram delivery failed after 3 attempts")
            return False
        finally:
            if own_client:
                await client.aclose()


def format_event(rule_name: str, severity: str, state: str, message: str) -> str:
    """Build a compact HTML message for Telegram."""
    icon = {"critical": "\U0001f6a8", "warning": "⚠️", "info": "ℹ️"}.get(severity, "⚠️")
    label = "ALERT" if state == "firing" else "RESOLVED"
    return (
        f"{icon} <b>{label}</b> · <b>{html.escape(rule_name)}</b>\n"
        f"{html.escape(message)}"
    )
