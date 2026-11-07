"""Simple, thread-safe rate limiting primitives."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass
class RateLimitDecision:
    allowed: bool
    retry_after_s: float = 0.0
    reason: str = ""


class SlidingWindowLimiter:
    """Allow at most ``limit`` events in any ``window_s`` second window."""

    def __init__(self, limit: int, window_s: float, clock=time.monotonic) -> None:
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    def check(self, consume: bool = True) -> RateLimitDecision:
        now = self._clock()
        with self._lock:
            while self._events and now - self._events[0] >= self.window_s:
                self._events.popleft()
            if len(self._events) >= self.limit:
                retry = self.window_s - (now - self._events[0])
                return RateLimitDecision(False, max(0.0, retry), "rate limit exceeded")
            if consume:
                self._events.append(now)
            return RateLimitDecision(True)

    def remaining(self) -> int:
        return max(0, self.limit - len(self._events))


class DailyCounter:
    """Counts events per local calendar day (resets at midnight)."""

    def __init__(self, limit: int, clock=time.time) -> None:
        self.limit = limit
        self._clock = clock
        self._day = self._today()
        self.count = 0
        self._lock = threading.Lock()

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self._clock()))

    def check(self, consume: bool = True) -> RateLimitDecision:
        with self._lock:
            day = self._today()
            if day != self._day:
                self._day, self.count = day, 0
            if self.count >= self.limit:
                return RateLimitDecision(False, 0.0, "daily request limit reached")
            if consume:
                self.count += 1
            return RateLimitDecision(True)
