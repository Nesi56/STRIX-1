"""Composite telemetry snapshots and WebSocket broadcasting.

Bandwidth: IMU/orientation is sent at ``STRIX_TELEMETRY_HZ`` (default 20 Hz);
slower subsystems (environment, system, AI, mapping, recording) are included
once per second (``full: true``). Each client has a small bounded queue; if a
client falls behind, its oldest queued messages are dropped so it always
catches up to live data instead of lagging.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from typing import Any

from fastapi import WebSocket

log = logging.getLogger(__name__)


def clean_json(obj: Any, nd: int = 5) -> Any:
    """Replace NaN/inf with None and trim float precision for compact JSON."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return round(obj, nd)
    if isinstance(obj, dict):
        return {k: clean_json(v, nd) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean_json(v, nd) for v in obj]
    return obj


class Client:
    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=10)
        self.dropped = 0
        self.connected_at = time.time()

    def offer(self, text: str) -> None:
        if self.queue.full():
            try:
                self.queue.get_nowait()
                self.dropped += 1
            except asyncio.QueueEmpty:
                pass
        self.queue.put_nowait(text)


class ConnectionManager:
    def __init__(self) -> None:
        self.clients: set[Client] = set()

    def add(self, ws: WebSocket) -> Client:
        c = Client(ws)
        self.clients.add(c)
        return c

    def remove(self, c: Client) -> None:
        self.clients.discard(c)

    def broadcast(self, msg: dict[str, Any]) -> None:
        if not self.clients:
            return
        text = json.dumps(clean_json(msg), separators=(",", ":"))
        for c in list(self.clients):
            c.offer(text)

    @staticmethod
    async def sender(c: Client) -> None:
        while True:
            text = await c.queue.get()
            await c.ws.send_text(text)


class TelemetryService:
    def __init__(self, container: Any) -> None:
        self.c = container
        self.manager = ConnectionManager()
        self._seq = 0
        self._task: asyncio.Task | None = None
        self._last_full = 0.0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_det_seq = -1

    def demo_mode(self) -> bool:
        c = self.c
        return bool(c.settings.mock or any(s.synthetic for s in (c.imu.status, c.dht.status, c.camera.status, c.servo.status)))

    def fast_part(self) -> dict[str, Any]:
        c = self.c
        out: dict[str, Any] = {"imu": {"status": c.imu.status.to_dict(), "latest": c.imu.latest()}, "agent": c.agent.snapshot()}
        det = c.detector.latest()
        if det is not None and det["seq"] != self._last_det_seq:
            self._last_det_seq = det["seq"]
            out["detections"] = det
        return out

    def slow_part(self) -> dict[str, Any]:
        c = self.c
        cam = c.camera.camera_status()
        cam.pop("info", None)  # static; fetched via REST
        imu_cal = c.imu.calibration_status()
        return {
            "imu_health": c.imu.health(),
            "imu_calibration": {"state": imu_cal.get("state"), "yaw_drift_bound_deg": imu_cal.get("yaw_drift_bound_deg"),
                                "calibrated_at": imu_cal["calibration"].get("calibrated_at")},
            "environment": c.dht.summary(),
            "servo": c.servo.state(),
            "camera": cam,
            "system": c.system.latest(),
            "ai": {"qwen": _ai_brief(c.qwen.status())},
            "detector": c.detector.state(),
            "mapping": c.mapping.state(include_trajectory=False),
            "recording": c.recording.status(),
        }

    def snapshot(self) -> dict[str, Any]:
        """Full composite snapshot (used by the recorder and REST)."""
        snap = {"ts": time.time(), "demo_mode": self.demo_mode(), **self.fast_part(), **self.slow_part()}
        snap["detections"] = self.c.detector.latest()  # always include the newest result
        return snap

    def recorder_snapshot(self) -> dict[str, Any]:
        """Lightweight snapshot for the 10 Hz recorder (avoids heavy calls)."""
        c = self.c
        return {
            "ts": time.time(),
            "imu": {"status": c.imu.status.to_dict(), "latest": c.imu.latest()},
            "environment": {"status": c.dht.status.to_dict(), "latest": c.dht.latest()},
            "servo": {"status": c.servo.status.to_dict(), "commanded_angle": c.servo.current},
            "camera": {"status": c.camera.status.to_dict(), "measured_fps": c.camera.measured_fps},
            "system": c.system.latest(),
        }

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._run(), name="telemetry-broadcast")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        period = 1.0 / self.c.settings.telemetry_hz
        next_t = loop.time()
        while True:
            try:
                if self.manager.clients:
                    self._seq += 1
                    now = time.time()
                    msg: dict[str, Any] = {"type": "telemetry", "ts": now, "seq": self._seq, "demo_mode": self.demo_mode(),
                                           **self.fast_part()}
                    if now - self._last_full >= 1.0:
                        self._last_full = now
                        msg.update(self.slow_part())
                        msg["full"] = True
                    self.manager.broadcast(msg)
            except Exception:
                log.exception("telemetry broadcast failed")
            next_t += period
            delay = next_t - loop.time()
            if delay < -1.0:
                next_t = loop.time()
            await asyncio.sleep(max(0.0, delay))

    def broadcast_chat(self, msg: dict[str, Any]) -> None:
        """Push an unsolicited agent message (e.g. a YOLO watch comment). Call on the loop."""
        self.manager.broadcast({"type": "chat", "message": msg})

    def broadcast_event(self, ev: dict[str, Any]) -> None:
        """Thread-safe: events may originate from device threads."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        msg = {"type": "event", "event": ev}
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self.manager.broadcast(msg)
        else:
            try:
                loop.call_soon_threadsafe(self.manager.broadcast, msg)
            except RuntimeError:
                pass


def _ai_brief(s: dict[str, Any]) -> dict[str, Any]:
    keep = ("provider", "label", "enabled", "state", "detail", "busy", "queued", "vision_capable", "model_id")
    out = {k: s.get(k) for k in keep if k in s}
    stats = s.get("stats") or {}
    out["last"] = stats.get("last")
    return out
