"""FastAPI app factory: rate limiting, CSRF, security headers, SPA hosting."""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ccmon.auth import CSRF_HEADER, SESSION_COOKIE
from ccmon.config import Settings
from ccmon.ratelimit import SlidingWindowLimiter

log = logging.getLogger("ccmon.api")
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def create_app(settings: Settings, source) -> FastAPI:
    app = FastAPI(
        title="ccmon — Issabel/Asterisk call-center monitor",
        version="0.1.0",
        docs_url="/docs" if settings.openapi_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.openapi_enabled else None,
    )
    app.state.settings = settings
    app.state.source = source
    app.state.ip_limiter = SlidingWindowLimiter(settings.rate_limit_ip_per_minute)
    app.state.token_limiter = SlidingWindowLimiter(settings.rate_limit_token_per_minute)
    app.state.login_limiter = SlidingWindowLimiter(settings.login_rate_per_minute)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        is_api = path.startswith("/api/")

        if is_api:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.lower().startswith("bearer "):
                key = "tok:" + hashlib.sha256(auth_header.encode()).hexdigest()
                allowed, retry_after = app.state.token_limiter.check(key)
            else:
                allowed, retry_after = app.state.ip_limiter.check(f"ip:{_client_ip(request)}")
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "rate limit exceeded"},
                    headers={"Retry-After": str(retry_after)},
                )
            # CSRF: only cookie-authenticated mutations need the header.
            # Bearer requests carry no ambient credential; /auth/login REPLACES
            # credentials so it must work before any header exists.
            has_bearer = request.headers.get("Authorization", "").lower().startswith("bearer ")
            if (
                request.method in MUTATING
                and SESSION_COOKIE in request.cookies
                and not has_bearer
                and path != "/api/v1/auth/login"
                and request.headers.get(CSRF_HEADER) is None
            ):
                return JSONResponse(
                    status_code=403, content={"detail": "missing CSRF header"}
                )

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error on %s %s", request.method, path)
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        if is_api:
            log.info("%s %s -> %d (%.0fms)", request.method, path, response.status_code, elapsed_ms)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'"
        )
        return response

    from ccmon.api import (
        admin,
        alerts,
        auth_routes,
        hermes,
        live,
        meta,
        reports,
    )

    for router in (meta.router, auth_routes.router, live.router, reports.router,
                   alerts.router, hermes.router, admin.router):
        app.include_router(router)

    _mount_spa(app)
    return app


def _mount_spa(app: FastAPI) -> None:
    """Serve the built React dashboard (dashboard/dist) with an SPA fallback."""
    dist = Path(__file__).resolve().parents[2] / "dashboard" / "dist"
    if not (dist / "index.html").is_file():
        log.warning("dashboard build not found at %s (API-only mode)", dist)
        return
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
    dist_resolved = dist.resolve()
    reserved = ("api/", "docs", "openapi.json", "redoc", "healthz")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        # Never let the SPA fallback answer API/docs paths: with OpenAPI
        # disabled these must 404, and /api/* must return JSON errors.
        if full_path.startswith(reserved):
            return JSONResponse(status_code=404, content={"detail": "not found"})
        if full_path:
            candidate = (dist / full_path).resolve()
            if candidate.is_relative_to(dist_resolved) and candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(dist / "index.html")
