"""Capability probing: what does THIS Issabel install actually have?

Probed on startup and refreshed every few minutes. Every feature that depends
on a probed capability degrades gracefully (empty result / insufficient_data)
instead of erroring — this is what keeps the app decoupled from one specific
Issabel install.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field

from ccmon.config import Settings
from ccmon.sources.mysql_ro import MysqlReader
from ccmon.sources.queries.campaigns import (
    CALLS_TIME_COLS,
    CAMPAIGN_NAME_COLS,
    CAMPAIGN_STATUS_COLS,
)

log = logging.getLogger(__name__)
QUEUE_LOG_FILE = "/var/log/asterisk/queue_log"
_REFRESH_SECONDS = 300


@dataclass
class Capabilities:
    source_ok: bool = False
    cdr: bool = False
    queue_log_table: bool = False
    queue_log_file: bool = False
    call_center: bool = False
    audit: bool = False
    queue_member: bool = False
    campaigns: bool = False
    ps_contacts: bool = False
    sippeers: bool = False
    ami: bool = False
    systemd: bool = False
    telegram: bool = False
    campaign_name_col: str | None = None
    campaign_status_col: str | None = None
    campaign_calls_time_col: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def has_queue_metrics(self) -> bool:
        return self.queue_log_table or self.queue_log_file

    def as_dict(self) -> dict:
        return asdict(self)


class CapabilityProbe:
    def __init__(self, settings: Settings, reader: MysqlReader | None) -> None:
        self._settings = settings
        self._reader = reader
        self._lock = threading.Lock()
        self._caps: Capabilities | None = None
        self._checked_at = 0.0

    def get(self, force: bool = False) -> Capabilities:
        with self._lock:
            expired = time.monotonic() - self._checked_at > _REFRESH_SECONDS
            if self._caps is None or force or expired:
                self._caps = self._probe()
                self._checked_at = time.monotonic()
            return self._caps

    def _probe(self) -> Capabilities:
        caps = Capabilities(
            ami=self._settings.ami_enabled,
            systemd=shutil.which("systemctl") is not None,
            telegram=self._settings.telegram_enabled,
            queue_log_file=os.path.exists(QUEUE_LOG_FILE),
        )
        if self._reader is None:
            caps.errors.append("no source reader configured")
            return caps
        try:
            cdr_db, ast_db, cc_db = (
                self._settings.issabel_db_cdr,
                self._settings.issabel_db_asterisk,
                self._settings.issabel_db_call_center,
            )
            caps.source_ok = True
            caps.cdr = self._reader.table_exists(cdr_db, "cdr")
            caps.queue_log_table = self._reader.table_exists(ast_db, "queue_log")
            caps.queue_member = self._reader.table_exists(ast_db, "queue_member")
            caps.ps_contacts = self._reader.table_exists(ast_db, "ps_contacts")
            caps.sippeers = self._reader.table_exists(ast_db, "sippeers")
            caps.call_center = self._reader.table_exists(cc_db, "call_entry")
            caps.audit = self._reader.table_exists(cc_db, "audit")
            caps.campaigns = self._reader.table_exists(cc_db, "campaign")
            if caps.campaigns:
                cols = self._reader.columns(cc_db, "campaign")
                caps.campaign_name_col = next((c for c in CAMPAIGN_NAME_COLS if c in cols), None)
                caps.campaign_status_col = next(
                    (c for c in CAMPAIGN_STATUS_COLS if c in cols), None
                )
                if not caps.campaign_name_col:
                    caps.campaigns = False
                if self._reader.table_exists(cc_db, "calls"):
                    calls_cols = self._reader.columns(cc_db, "calls")
                else:
                    calls_cols = set()
                caps.campaign_calls_time_col = next(
                    (c for c in CALLS_TIME_COLS if c in calls_cols), None
                )
        except Exception as exc:
            caps.source_ok = False
            caps.errors.append(f"probe failed: {exc}")
            log.warning("capability probe failed: %s", exc)
        return caps
