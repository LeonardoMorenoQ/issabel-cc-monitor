"""MySQL-dialect read-only queries against Issabel databases (validated by sqlglot in tests).

Submodules are imported eagerly: callers use `queries.<module>.SQL_*`, and
package attribute access does not auto-import submodules.
"""
from . import agents, campaigns, cdr, health, queue_log, queues

__all__ = ["agents", "campaigns", "cdr", "health", "queue_log", "queues"]
