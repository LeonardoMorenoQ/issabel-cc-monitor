"""Auth: DB-backed sessions (HttpOnly cookie) + scoped bearer tokens (hashed).

* Humans: POST /auth/login -> server-side session row, cookie is a random
  token; only its SHA-256 is stored.
* Machines (Hermes): Authorization: Bearer ccmon_<token>; scopes: read,
  rules:write, admin. Tokens are hashed at rest and revocable.
* Cookie-authenticated mutations additionally require the X-CCMon-CSRF header
  (checked in middleware) so cross-site forms cannot act.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ccmon.db import as_utc, get_db, utcnow
from ccmon.models import ApiToken, AuthSession, User

_hasher = PasswordHasher()
SESSION_COOKIE = "ccmon_session"
CSRF_HEADER = "X-CCMon-CSRF"
TOKEN_PREFIX = "ccmon_"
SCOPES = ("read", "rules:write", "admin")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(stored_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except (InvalidHashError, ValueError):
        return True


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def new_api_token() -> tuple[str, str]:
    """Returns (raw_token, sha256_hash). Raw is shown to the user ONCE."""
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return raw, sha256(raw)


@dataclass
class Principal:
    user: User | None = None
    token: ApiToken | None = None
    scopes: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.user is not None:
            return self.user.username
        if self.token is not None:
            return f"token:{self.token.name}"
        return "anonymous"


def user_scopes(user: User) -> list[str]:
    return list(SCOPES) if user.is_superadmin else ["read", "rules:write"]


# Real hash so unknown-user logins do the same argon2 work as real ones
# (blunts username enumeration via timing).
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(24))


def authenticate(session: Session, username: str, password: str) -> User | None:
    user = session.query(User).filter(User.username == username).first()
    if user is None or not user.active:
        verify_password(_DUMMY_HASH, password)
        return None
    if not verify_password(user.password_hash, password):
        return None
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


def create_login_session(session: Session, user: User, ttl_minutes: int) -> str:
    raw = new_session_token()
    session.add(
        AuthSession(
            token_hash=sha256(raw), user_id=user.id,
            created_at=utcnow(), expires_at=utcnow() + timedelta(minutes=ttl_minutes),
            last_seen_at=utcnow(),
        )
    )
    user.last_login_at = utcnow()
    session.flush()
    return raw


def set_session_cookie(response: Response, raw: str, settings) -> None:
    response.set_cookie(
        SESSION_COOKIE, raw,
        max_age=settings.session_ttl_minutes * 60,
        httponly=True, samesite="strict",
        secure=settings.app_mode == "production", path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


# --------------------------------------------------------------- dependencies
def get_principal(request: Request, session: Session = Depends(get_db)) -> Principal:
    """Bearer token first (machines), then session cookie (humans)."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        raw = auth_header[7:].strip()
        if not raw.startswith(TOKEN_PREFIX):
            raise HTTPException(status_code=401, detail="invalid token format")
        token = (
            session.query(ApiToken).filter(ApiToken.token_hash == sha256(raw)).first()
        )
        now = utcnow()
        if token is None or token.revoked:
            raise HTTPException(status_code=401, detail="invalid or revoked token")
        if token.expires_at and (as_utc(token.expires_at) or now) < now:
            raise HTTPException(status_code=401, detail="token expired")
        token.last_used_at = now
        scopes = [s.strip() for s in token.scopes.split(",") if s.strip()]
        return Principal(user=None, token=token, scopes=scopes)

    raw_cookie = request.cookies.get(SESSION_COOKIE)
    if raw_cookie:
        row = (
            session.query(AuthSession)
            .filter(AuthSession.token_hash == sha256(raw_cookie))
            .first()
        )
        if row is not None and (as_utc(row.expires_at) or utcnow()) > utcnow():
            user = session.get(User, row.user_id)
            if user is not None and user.active:
                row.last_seen_at = utcnow()
                return Principal(user=user, scopes=user_scopes(user))
    raise HTTPException(status_code=401, detail="authentication required")


def require_scope(scope: str):
    def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if scope not in principal.scopes:
            raise HTTPException(status_code=403, detail=f"missing scope: {scope}")
        return principal

    return dependency


def require_session_user(
    principal: Principal = Depends(get_principal),
) -> Principal:
    """Admin endpoints: human session only (tokens never carry the admin scope)."""
    if principal.user is None:
        raise HTTPException(status_code=403, detail="interactive session required")
    if not principal.user.is_superadmin:
        raise HTTPException(status_code=403, detail="superadmin required")
    return principal


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def audit(session: Session, action: str, principal: Principal | None = None,
          detail: str = "", request: Request | None = None) -> None:
    from ccmon.models import AuditLog

    session.add(
        AuditLog(
            username=principal.label if principal else None,
            action=action[:100], detail=(detail or "")[:500],
            ip=client_ip(request) if request else None,
        )
    )
