"""In-process sliding-window rate limiter (single uvicorn worker)."""
from __future__ import annotations

import threading
import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self, limit_per_minute: int, max_keys: int = 10_000) -> None:
        self.limit = limit_per_minute
        self.max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, int]:
        """Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            window = self._hits.get(key)
            if window is None:
                if len(self._hits) >= self.max_keys:
                    self._evict(now)
                window = deque()
                self._hits[key] = window
            while window and now - window[0] > 60.0:
                window.popleft()
            if len(window) >= self.limit:
                retry_after = max(1, int(60.0 - (now - window[0])) + 1)
                return False, retry_after
            window.append(now)
            return True, 0

    def _evict(self, now: float) -> None:
        stale = [k for k, w in self._hits.items() if not w or now - w[-1] > 60.0]
        for key in stale:
            self._hits.pop(key, None)
        if len(self._hits) >= self.max_keys:  # still full: drop arbitrary oldest
            for key in list(self._hits)[: self.max_keys // 10]:
                self._hits.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
