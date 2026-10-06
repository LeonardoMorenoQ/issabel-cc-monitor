"""Shared FastAPI dependencies."""
from __future__ import annotations

from fastapi import Depends, Request

from ccmon.config import Settings
from ccmon.db import get_db


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_source(request: Request):
    """Live/demo source — replaceable in tests via app.state.source."""
    return request.app.state.source


SessionDep = Depends(get_db)
