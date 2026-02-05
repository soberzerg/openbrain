"""Per-user rate limiting with sliding window."""

from __future__ import annotations

import time
from collections import deque


class RateLimiter:
    """In-memory sliding window rate limiter keyed by user ID."""

    def __init__(self, max_per_minute: int) -> None:
        self.max_per_minute = max_per_minute
        self._windows: dict[int, deque[float]] = {}

    def check(self, user_id: int) -> bool:
        """
        Record a request and return True if under limit, False if exceeded.

        Prunes timestamps older than 60 seconds before checking.
        """
        now = time.monotonic()
        window = self._windows.setdefault(user_id, deque())

        # Prune expired entries
        cutoff = now - 60.0
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= self.max_per_minute:
            return False

        window.append(now)
        return True
