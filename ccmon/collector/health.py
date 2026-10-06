"""Service/source health probes — all unprivileged, all failure-tolerant."""
from __future__ import annotations

import asyncio
import logging
import shutil

from ccmon.db import utcnow

log = logging.getLogger(__name__)


async def probe_services(names: list[str], timeout: float = 3.0) -> list[dict]:
    now = utcnow().isoformat()
    if not shutil.which("systemctl"):
        return [
            {"component": n, "status": "unknown", "detail": "systemctl unavailable", "ts": now}
            for n in names
        ]
    results = []
    for name in names:
        status, detail = "down", ""
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", "--quiet", name,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            rc = await asyncio.wait_for(proc.wait(), timeout=timeout)
            if rc == 0:
                status, detail = "ok", "active"
            else:
                detail = "not active"
        except FileNotFoundError:
            status, detail = "unknown", "systemctl unavailable"
        except TimeoutError:
            status, detail = "down", "probe timeout"
        except Exception as exc:  # never let a probe kill the loop
            status, detail = "unknown", f"probe error: {exc}"[:80]
        results.append({"component": name, "status": status, "detail": detail, "ts": now})
    return results


def source_health(source) -> dict:
    """DB reachability component row."""
    try:
        caps = source.capabilities()
        ok = caps.source_ok
        return {
            "component": "source-db",
            "status": "ok" if ok else "down",
            "detail": "; ".join(caps.errors) if caps.errors else "read-only source reachable",
            "ts": utcnow().isoformat(),
        }
    except Exception as exc:
        return {
            "component": "source-db", "status": "down",
            "detail": f"probe error: {exc}"[:80], "ts": utcnow().isoformat(),
        }
