"""Login (rate-limited per IP), current principal, logout."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ccmon.auth import (
    Principal,
    audit,
    authenticate,
    clear_session_cookie,
    client_ip,
    create_login_session,
    get_principal,
    set_session_cookie,
    sha256,
    user_scopes,
)
from ccmon.db import get_db
from ccmon.models import AuthSession
from ccmon.schemas import LoginRequest, MeOut

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=MeOut)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
):
    settings = request.app.state.settings
    allowed, retry_after = request.app.state.login_limiter.check(
        f"login:{client_ip(request)}"
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="too many login attempts",
            headers={"Retry-After": str(retry_after)},
        )
    user = authenticate(session, payload.username, payload.password)
    if user is None:
        audit(session, "auth.login_failed", None, payload.username, request)
        session.commit()
        raise HTTPException(status_code=401, detail="invalid credentials")
    raw = create_login_session(session, user, settings.session_ttl_minutes)
    set_session_cookie(response, raw, settings)
    audit(session, "auth.login", Principal(user=user, scopes=[]), "", request)
    return MeOut(
        id=user.id, username=user.username, full_name=user.full_name,
        is_superadmin=user.is_superadmin,
        scopes=user_scopes(user),
    )


@router.get("/me", response_model=MeOut)
def me(principal: Principal = Depends(get_principal)):
    if principal.user is not None:
        user = principal.user
        return MeOut(
            id=user.id, username=user.username, full_name=user.full_name,
            is_superadmin=user.is_superadmin, scopes=principal.scopes,
        )
    return MeOut(
        id=0, username=principal.label, full_name="API token",
        is_superadmin=False, scopes=principal.scopes,
    )


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    raw_cookie = request.cookies.get("ccmon_session")
    if raw_cookie:
        session.query(AuthSession).filter(
            AuthSession.token_hash == sha256(raw_cookie)
        ).delete(synchronize_session=False)
        audit(session, "auth.logout", principal, "", request)
    clear_session_cookie(response)
