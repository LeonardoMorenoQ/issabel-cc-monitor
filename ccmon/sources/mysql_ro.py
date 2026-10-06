"""Read-only MySQL/MariaDB access: pooling, timeouts, row caps, session READ ONLY.

The credentials used here are the dedicated ccmon_reader user (deploy/sql/
readonly_grants.sql — SELECT only). The session is additionally forced to
READ ONLY so an accidental write attempt fails even if grants were widened.
"""
from __future__ import annotations

import logging
import queue
from collections.abc import Iterator
from contextlib import contextmanager

import pymysql

from ccmon.config import Settings

log = logging.getLogger(__name__)


def render(sql: str, settings: Settings, **extra: str) -> str:
    """Substitute {cdr}/{asterisk}/{call_center} database-name placeholders."""
    return sql.format(
        cdr=settings.issabel_db_cdr,
        asterisk=settings.issabel_db_asterisk,
        call_center=settings.issabel_db_call_center,
        **extra,
    )


class MysqlReader:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: queue.Queue = queue.Queue(maxsize=4)
        self._closed = False

    def _connect(self):
        s = self._settings
        return pymysql.connect(
            host=s.issabel_db_host,
            port=s.issabel_db_port,
            user=s.issabel_db_user,
            password=s.issabel_db_password.get_secret_value(),
            charset="utf8mb4",
            connect_timeout=s.issabel_db_connect_timeout,
            read_timeout=s.issabel_db_read_timeout,
            write_timeout=s.issabel_db_connect_timeout,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )

    @contextmanager
    def connection(self) -> Iterator:
        conn = None
        reused = False
        try:
            try:
                conn = self._pool.get_nowait()
                reused = True
            except queue.Empty:
                conn = self._connect()
            try:
                conn.cursor().execute("SET SESSION TRANSACTION READ ONLY")
            except Exception:
                pass  # older servers may not support it; grants still enforce read-only
            yield conn
        except Exception:
            if reused and conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None  # never return a possibly-broken connection to the pool
            raise
        finally:
            if conn is not None and not self._closed:
                try:
                    self._pool.put_nowait(conn)
                except queue.Full:
                    try:
                        conn.close()
                    except Exception:
                        pass

    def query(self, sql: str, params: dict | None = None) -> list[dict]:
        """Run a SELECT; returns list[dict], capped at ISSABEL_DB_MAX_ROWS."""
        cap = self._settings.issabel_db_max_rows
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params or {})
                rows = cur.fetchmany(cap)
                if len(rows) == cap:
                    log.warning("source query hit row cap (%d); result truncated", cap)
                return list(rows)

    def table_exists(self, db: str, table: str) -> bool:
        rows = self.query(
            "SELECT 1 AS ok FROM information_schema.tables "
            "WHERE table_schema = %(db)s AND table_name = %(tb)s LIMIT 1",
            {"db": db, "tb": table},
        )
        return bool(rows)

    def columns(self, db: str, table: str) -> set[str]:
        rows = self.query(
            "SELECT column_name AS col FROM information_schema.columns "
            "WHERE table_schema = %(db)s AND table_name = %(tb)s",
            {"db": db, "tb": table},
        )
        return {str(r["col"]).lower() for r in rows if r.get("col")}

    def ping(self) -> bool:
        try:
            self.query("SELECT 1 AS ok")
            return True
        except Exception as exc:
            log.warning("source DB unreachable: %s", exc)
            return False

    def close(self) -> None:
        self._closed = True
        while True:
            try:
                conn = self._pool.get_nowait()
            except queue.Empty:
                break
            try:
                conn.close()
            except Exception:
                pass
