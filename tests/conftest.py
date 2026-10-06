"""Shared fixtures: fresh in-memory app DB + demo source + seeded collector data."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ccmon import __main__ as cli
from ccmon.config import Settings
from ccmon.db import configure_engine, session_scope
from ccmon.migrations import migrate
from ccmon.models import AlertRule


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        app_mode="demo", database_url="sqlite://", openapi_enabled=True,
        login_rate_per_minute=5, rate_limit_ip_per_minute=1000,
        live_stale_after_seconds=30,
    )


@pytest.fixture()
def env(settings, monkeypatch):
    """Fresh DB, admin user, demo source, one collector pass, app + client."""
    from ccmon.api.app import create_app
    from ccmon.collector.runner import RollingCache, history_cycle, live_cycle
    from ccmon.sources.demo_source import DemoSource

    migrate(configure_engine(settings.database_url))
    cli._ensure_admin(settings, "admin", "test-password-123")
    source = DemoSource(settings, days=5)
    with session_scope() as session:
        session.add(
            AlertRule(
                type="agent_paused_minutes_gt", name="Paused too long",
                params={"min_minutes": 15}, for_seconds=0, cooldown_minutes=10,
            )
        )

    class NullSender:
        def __init__(self):
            self.messages = []

        async def send(self, text: str) -> bool:
            self.messages.append(text)
            return True

    sender = NullSender()
    cache = RollingCache()

    async def seed():
        await history_cycle(settings, source, cache)
        await live_cycle(settings, source, sender, cache)

    asyncio.run(seed())
    app = create_app(settings, source)
    client = TestClient(app)
    yield SimpleNamespace(
        settings=settings, app=app, client=client, source=source,
        sender=sender, cache=cache,
    )
    client.close()


@pytest.fixture()
def logged_in(env):
    response = env.client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "test-password-123"},
    )
    assert response.status_code == 200, response.text
    return env


CSRF = {"X-CCMon-CSRF": "1"}


@pytest.fixture()
def api_token(logged_in):
    response = logged_in.client.post(
        "/api/v1/admin/tokens",
        json={"name": "hermes", "scopes": ["read", "rules:write"]},
        headers=CSRF,
    )
    assert response.status_code == 201, response.text
    return response.json()["token"]
