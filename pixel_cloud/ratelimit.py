"""In-process token buckets (CLOUD.md §8). Best effort per warm instance, no storage cost."""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from typing import Callable


class RateLimiter:
    """Keyed token buckets: `rate` tokens/second refill, capacity `burst`. Bounded key table (LRU)."""

    def __init__(self, rate: float, burst: int, clock: Callable[[], float] = time.monotonic,
                 max_keys: int = 10_000) -> None:
        self.rate = float(rate)
        self.burst = float(burst)
        self._clock = clock
        self._max = max_keys
        self._lock = threading.Lock()
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()   # key → (tokens, last)

    def _refill(self, key: str, now: float) -> float:
        tokens, last = self._buckets.get(key, (self.burst, now))
        return min(self.burst, tokens + max(0.0, now - last) * self.rate)

    def check(self, key: str, cost: float = 1.0) -> float:
        """Take `cost` tokens. Returns 0 when allowed, else the seconds to wait (nothing is taken)."""
        now = self._clock()
        with self._lock:
            tokens = self._refill(key, now)
            if tokens >= cost:
                self._buckets[key] = (tokens - cost, now)
                self._buckets.move_to_end(key)
                while len(self._buckets) > self._max:
                    self._buckets.popitem(last=False)
                return 0.0
            self._buckets[key] = (tokens, now)
            self._buckets.move_to_end(key)
            return (cost - tokens) / self.rate if self.rate > 0 else math.inf

    def refund(self, key: str, cost: float = 1.0) -> None:
        now = self._clock()
        with self._lock:
            if key in self._buckets:
                self._buckets[key] = (min(self.burst, self._refill(key, now) + cost), now)


def retry_after_seconds(wait: float) -> int:
    return max(1, math.ceil(wait)) if math.isfinite(wait) else 3600
