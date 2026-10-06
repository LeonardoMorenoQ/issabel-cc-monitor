"""API behavior: auth, scopes, CSRF, validation, rate limits, Hermes endpoints."""
from __future__ import annotations

from tests.conftest import CSRF


def test_healthz_is_public(env):
    assert env.client.get("/healthz").status_code == 200


def test_api_requires_auth(env):
    assert env.client.get("/api/v1/live/queues").status_code == 401
    assert env.client.get("/api/v1/reports/summary").status_code == 401


def test_login_bad_credentials_rate_limited(env):
    codes = [
        env.client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "wrong"}
        ).status_code
        for _ in range(6)
    ]
    # LOGIN_RATE_PER_MINUTE=5 -> exactly five 401s, then 429 with Retry-After
    assert codes == [401, 401, 401, 401, 401, 429]


def test_login_and_me(logged_in):
    me = logged_in.client.get("/api/v1/auth/me").json()
    assert me["username"] == "admin" and me["is_superadmin"] is True
    assert "admin" in me["scopes"]


def test_live_endpoints_serve_collected_data(logged_in):
    queues = logged_in.client.get("/api/v1/live/queues").json()
    assert queues["stale"] is False
    assert {q["queue"] for q in queues["items"]} == {"800", "801"}
    agents = logged_in.client.get("/api/v1/live/agents").json()
    assert len(agents["items"]) == 6
    paused = [a for a in agents["items"] if a["state"] == "paused"]
    assert paused and paused[0]["pause_seconds"] >= 15 * 60
    health = logged_in.client.get("/api/v1/live/health").json()
    assert health["collector"]["live"] is not None


def test_report_validation(logged_in):
    c = logged_in.client
    base = "/api/v1/reports/summary"
    assert c.get(f"{base}?date_from=2020-01-01&date_to=2026-12-31").status_code == 422
    assert c.get(f"{base}?date_from=2026-10-05&date_to=2026-10-01").status_code == 422
    assert c.get(f"{base}?date_from=2026-10-01&date_to=2030-01-01").status_code == 422
    assert c.get("/api/v1/reports/series?queue=800;DROP%20TABLE").status_code == 422
    assert c.get("/api/v1/reports/series?bucket=minute").status_code == 422
    ok = c.get("/api/v1/reports/summary").json()
    assert ok["source"] == "demo" and ok["offered"] > 0
    series = c.get("/api/v1/reports/series?bucket=day&date_from=2026-10-01").json()
    assert series["points"], "expected daily points from demo source"


def test_cookie_mutation_requires_csrf(logged_in):
    payload = {"type": "service_down", "name": "asterisk", "params": {"component": "asterisk"}}
    assert logged_in.client.post("/api/v1/alert-rules", json=payload).status_code == 403
    response = logged_in.client.post("/api/v1/alert-rules", json=payload, headers=CSRF)
    assert response.status_code == 201


def test_rule_params_are_validated(logged_in):
    bad = {"type": "queue_waiting_gt", "name": "bad", "params": {"queue": "800"}}
    assert logged_in.client.post("/api/v1/alert-rules", json=bad, headers=CSRF).status_code == 422
    bad_type = {"type": "nope", "name": "bad", "params": {}}
    resp = logged_in.client.post("/api/v1/alert-rules", json=bad_type, headers=CSRF)
    assert resp.status_code == 422


def test_token_scopes_and_admin_is_session_only(logged_in, api_token):
    c = logged_in.client
    bearer = {"Authorization": f"Bearer {api_token}"}
    assert c.get("/api/v1/live/queues", headers=bearer).status_code == 200
    assert c.get("/api/v1/admin/users", headers=bearer).status_code == 403  # tokens: no admin
    rule = {
        "type": "queue_waiting_gt",
        "name": "via token",
        "params": {"queue": "800", "threshold": 5},
    }
    assert c.post("/api/v1/alert-rules", json=rule, headers=bearer).status_code == 201
    bad_auth = {"Authorization": "Bearer ccmon_invalid"}
    assert c.get("/api/v1/live/queues", headers=bad_auth).status_code == 401
    # non-bearer schemes don't authenticate; without a session cookie -> 401
    from fastapi.testclient import TestClient

    anonymous = TestClient(logged_in.app)
    basic = {"Authorization": "Basic xx"}
    assert anonymous.get("/api/v1/live/queues", headers=basic).status_code == 401
    assert anonymous.get("/api/v1/live/queues").status_code == 401
    anonymous.close()


def test_hermes_endpoints(logged_in, api_token):
    headers = {"Authorization": f"Bearer {api_token}"}
    c = logged_in.client
    health = c.get("/api/v1/hermes/queue-health", headers=headers).json()
    assert health["stale"] is False and len(health["queues"]) == 2
    assert "800:" in health["summary_text"]
    paused = c.get("/api/v1/hermes/agents/paused?min_minutes=15", headers=headers).json()
    assert paused["agents"] and paused["agents"][0]["agent"] == "103"
    assert "103" in paused["summary_text"]
    abandoned = c.get("/api/v1/hermes/abandoned/today", headers=headers).json()
    assert abandoned["total"] > 0 and abandoned["by_queue"]
    assert "abandoned" in abandoned["summary_text"]
    assert c.get("/api/v1/hermes/agents/paused?min_minutes=-5", headers=headers).status_code == 422


def test_admin_user_and_token_lifecycle(logged_in):
    c = logged_in.client
    created = c.post(
        "/api/v1/admin/users",
        json={"username": "viewer", "password": "long-enough-pw", "full_name": "Read Only"},
        headers=CSRF,
    )
    assert created.status_code == 201
    duplicate = c.post(
        "/api/v1/admin/users",
        json={"username": "viewer", "password": "long-enough-pw"},
        headers=CSRF,
    )
    assert duplicate.status_code == 409
    body = {"name": "t1", "scopes": ["read"]}
    token = c.post("/api/v1/admin/tokens", json=body, headers=CSRF).json()
    assert token["token"].startswith("ccmon_")
    listed = c.get("/api/v1/admin/tokens").json()
    assert all(t["token"] is None for t in listed)  # raw never re-exposed
    assert c.delete(f"/api/v1/admin/tokens/{token['id']}", headers=CSRF).status_code == 204
    dead = c.get("/api/v1/live/queues", headers={"Authorization": f"Bearer {token['token']}"})
    assert dead.status_code == 401
    audit = c.get("/api/v1/admin/audit").json()
    assert any(entry["action"] == "token.create" for entry in audit)


def test_openapi_disabled_by_default(settings):
    from fastapi.testclient import TestClient

    from ccmon.api.app import create_app

    strict = settings.model_copy(update={"openapi_enabled": False})
    app = create_app(strict, env_source(strict))
    client = TestClient(app)
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404


def env_source(settings):
    from ccmon.sources.demo_source import DemoSource

    return DemoSource(settings, days=2)
