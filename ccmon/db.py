"""Application database engine/session (PostgreSQL in production).

Storage is always UTC. SQLite is supported for development/tests only.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """Normalize DB datetimes (SQLite returns naive) to aware UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def configure_engine(url: str) -> Engine:
    global _engine, _factory
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        is_memory = ":memory:" in url or url in ("sqlite://", "sqlite:///:memory:")
        if is_memory:
            # One shared connection: worker threads must see the same schema.
            kwargs["poolclass"] = StaticPool
    else:
        kwargs.update(pool_pre_ping=True, pool_size=5, max_overflow=5)
    _engine = create_engine(url, **kwargs)
    _factory = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("database engine not configured; call configure_engine() first")
    return _engine


def create_session() -> Session:
    if _factory is None:
        raise RuntimeError("database engine not configured; call configure_engine() first")
    return _factory()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = create_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as session:
        yield session
