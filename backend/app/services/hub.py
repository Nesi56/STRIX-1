"""Thread → asyncio fan-out primitives.

Hardware acquisition runs in plain threads; HTTP/WebSocket consumers live on
the asyncio loop. ``LatestValueHub`` stores only the newest item, so a slow
browser never builds a backlog — it simply skips to the latest frame, which
keeps video latency bounded.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class LatestValueHub(Generic[T]):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value: T | None = None
        self._seq = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._waiters: set[asyncio.Event] = set()  # touched only on the loop thread

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, value: T) -> None:
        with self._lock:
            self._value = value
            self._seq += 1
        loop = self._loop
        if loop is not None and not loop.is_closed() and self._waiters:
            try:
                loop.call_soon_threadsafe(self._notify)
            except RuntimeError:  # loop shutting down
                pass

    def _notify(self) -> None:
        for ev in list(self._waiters):
            ev.set()

    def latest(self) -> tuple[int, T | None]:
        with self._lock:
            return self._seq, self._value

    async def wait_newer(self, seq: int, timeout: float = 5.0) -> tuple[int, T | None]:
        """Wait until a value newer than ``seq`` is published (or timeout)."""
        cur, val = self.latest()
        if cur != seq:
            return cur, val
        ev = asyncio.Event()
        self._waiters.add(ev)
        try:
            if self.latest()[0] == seq:  # re-check after registering
                await asyncio.wait_for(ev.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            self._waiters.discard(ev)
        return self.latest()


class EventLog:
    """Bounded in-memory event log (sensor faults, operator actions, ...)."""

    def __init__(self, capacity: int = 300) -> None:
        from collections import deque

        self._events: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self.listeners: list[Any] = []

    def add(self, level: str, source: str, message: str, **data: Any) -> dict[str, Any]:
        import time

        ev = {"ts": time.time(), "level": level, "source": source, "message": message, **data}
        with self._lock:
            self._events.append(ev)
        for cb in list(self.listeners):
            try:
                cb(ev)
            except Exception:
                pass
        return ev

    def recent(self, n: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)[-n:]
