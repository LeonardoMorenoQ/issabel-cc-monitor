"""Admin: API tokens, users, audit trail (superadmin interactive session only)."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ccmon.auth import (
    Principal,
    audit,
    hash_password,
    new_api_token,
    require_session_user,
)
from ccmon.db import get_db, utcnow
from ccmon.models import ApiToken, AuditLog, User
from ccmon.schemas import USERNAME_PATTERN, TokenCreateRequest, TokenOut, UserOut

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
admin_scope = require_session_user


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=2, max_length=80, pattern=USERNAME_PATTERN)
    password: str = Field(min_length=12, max_length=256)
    full_name: str = Field(default="", max_length=160)
    is_superadmin: bool = False


@router.get("/tokens", response_model=list[TokenOut])
def list_tokens(session: Session = Depends(get_db), _=Depends(admin_scope)):
    rows = session.query(ApiToken).order_by(ApiToken.id.desc()).all()
    return [
        TokenOut(
            id=t.id, name=t.name, scopes=[s for s in t.scopes.split(",") if s],
            created_at=t.created_at, expires_at=t.expires_at, revoked=t.revoked,
        )
        for t in rows
    ]


@router.post("/tokens", response_model=TokenOut, status_code=201)
def create_token(
    payload: TokenCreateRequest,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(admin_scope),
):
    raw, token_hash = new_api_token()
    token = ApiToken(
        name=payload.name, token_hash=token_hash, scopes=",".join(payload.scopes),
        expires_at=utcnow() + timedelta(days=payload.expires_days)
        if payload.expires_days else None,
    )
    session.add(token)
    session.flush()
    audit(session, "token.create", principal, f"{payload.name} scopes={payload.scopes}", request)
    return TokenOut(
        id=token.id, name=token.name, scopes=payload.scopes,
        created_at=token.created_at, expires_at=token.expires_at,
        revoked=False, token=raw,  # shown exactly once
    )


@router.delete("/tokens/{token_id}", status_code=204)
def revoke_token(
    token_id: int,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(admin_scope),
):
    token = session.get(ApiToken, token_id)
    if token is None:
        raise HTTPException(status_code=404, detail="token not found")
    token.revoked = True
    audit(session, "token.revoke", principal, token.name, request)


@router.get("/users", response_model=list[UserOut])
def list_users(session: Session = Depends(get_db), _=Depends(admin_scope)):
    return session.query(User).order_by(User.id).all()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreateRequest,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(admin_scope),
):
    exists = session.query(User).filter(User.username == payload.username).first()
    if exists is not None:
        raise HTTPException(status_code=409, detail="username already exists")
    user = User(
        username=payload.username, full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        is_superadmin=payload.is_superadmin, active=True,
    )
    session.add(user)
    session.flush()
    audit(session, "user.create", principal, payload.username, request)
    return user


@router.get("/audit")
def audit_log(
    limit: int = 200,
    session: Session = Depends(get_db),
    _=Depends(admin_scope),
):
    rows = (
        session.query(AuditLog)
        .order_by(AuditLog.ts.desc(), AuditLog.id.desc())
        .limit(min(limit, 1000))
        .all()
    )
    return [
        {
            "ts": r.ts.isoformat() if r.ts else None, "username": r.username,
            "action": r.action, "detail": r.detail, "ip": r.ip,
        }
        for r in rows
    ]
