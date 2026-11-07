"""Shared building blocks for hardware services."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DeviceState(str, Enum):
    STARTING = "starting"
    OK = "ok"  # real hardware, healthy
    DEGRADED = "degraded"  # real hardware, intermittent errors
    SYNTHETIC = "synthetic"  # mock backend: clearly labeled demo data
    DISCONNECTED = "disconnected"  # hardware expected but not responding
    DISABLED = "disabled"  # disabled by configuration or operator
    ERROR = "error"


@dataclass
class DeviceStatus:
    name: str
    state: DeviceState = DeviceState.STARTING
    backend: str = "none"
    synthetic: bool = False
    error: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    last_update: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set(self, state: DeviceState, error: str | None = None, **detail: Any) -> None:
        with self._lock:
            self.state = state
            self.error = error
            if detail:
                self.detail.update(detail)

    def touch(self) -> None:
        self.last_update = time.time()

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "name": self.name,
                "state": self.state.value,
                "backend": self.backend,
                "synthetic": self.synthetic,
                "error": self.error,
                "detail": dict(self.detail),
                "last_update": self.last_update,
            }


class StoppableThread(threading.Thread):
    """Daemon thread with a cooperative stop event."""

    def __init__(self, target, name: str) -> None:
        super().__init__(name=name, daemon=True)
        self._target_fn = target
        self.stop_event = threading.Event()

    def run(self) -> None:  # pragma: no cover - exercised via services
        self._target_fn(self.stop_event)

    def stop(self, timeout: float = 2.0) -> None:
        self.stop_event.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout)
