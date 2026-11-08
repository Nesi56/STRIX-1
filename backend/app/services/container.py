"""Service wiring and graceful startup/shutdown.

Every service starts independently: a failure in one (e.g. no camera) is
logged and reported as that device's status, never preventing the rest of
the system — in particular sensor acquisition — from running.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config.settings import Settings
from app.database.db import Database
from app.services.agent_service import AgentService
from app.services.ai_qwen_service import QwenService
from app.services.camera_service import CameraService
from app.services.detector_service import DetectorService
from app.services.dht_service import DHTService
from app.services.hub import EventLog
from app.services.imu_service import IMUService
from app.services.mapping_service import MappingService
from app.services.recording_service import RecordingService
from app.services.servo_service import ServoService
from app.services.system_monitor import SystemMonitor
from app.services.telemetry_service import TelemetryService

log = logging.getLogger(__name__)


class Container:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.events = EventLog()
        self.db = Database(settings.db_path)
        self.system = SystemMonitor(settings, self.events)
        self.imu = IMUService(settings, self.events)
        self.dht = DHTService(settings, self.events)
        self.servo = ServoService(settings, self.events)
        self.camera = CameraService(settings, self.events, pose=self._camera_pose)
        self.telemetry = TelemetryService(self)
        self.recording = RecordingService(settings, self.db, self.events, self.telemetry.recorder_snapshot,
                                          self.telemetry.demo_mode)
        self.mapping = MappingService(settings, self.camera, self.events)
        self.detector = DetectorService(settings, self.camera, self.events, self.db,
                                        session_id=lambda: (self.recording.active_session or {}).get("id"))
        self.qwen = QwenService(settings, self.events, register_process=self.system.register_process)
        self.agent = AgentService(self)
        self.events.listeners.append(self.telemetry.broadcast_event)
        self.started = False

    def _camera_pose(self) -> tuple[float, float, float | None]:
        """Pose hint for the synthetic camera only (makes the demo feed react)."""
        latest = self.imu.latest()
        o = (latest or {}).get("orientation") or {}
        return o.get("roll", 0.0), o.get("pitch", 0.0), self.servo.current

    async def _safe(self, name: str, fn: Any) -> None:
        try:
            res = fn()
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            log.exception("%s failed to start (continuing without it)", name)
            self.events.add("error", name, f"{name} failed to start; see logs")

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self.camera.hub.bind_loop(loop)
        # Sensors first: they must run regardless of camera/AI state.
        await self._safe("system", self.system.start)
        await self._safe("imu", self.imu.start)
        await self._safe("dht11", self.dht.start)
        await self._safe("servo", self.servo.start)
        await self._safe("camera", lambda: asyncio.to_thread(self.camera.start))
        await self._safe("detector", self.detector.start)
        await self._safe("recording", self.recording.start)
        await self._safe("mapping", self.mapping.start)
        await self._safe("telemetry", self.telemetry.start)
        await self._safe("qwen", self.qwen.start)
        self.started = True
        mode = "SYNTHETIC DEMO MODE" if self.settings.mock else "hardware mode"
        self.events.add("info", "system", f"STRIX-1 backend started ({mode})")

    async def stop(self) -> None:
        for name, fn in (
            ("telemetry", self.telemetry.stop), ("recording", self.recording.stop), ("mapping", self.mapping.stop),
            ("qwen", self.qwen.stop),
        ):
            try:
                await fn()
            except Exception:
                log.exception("%s shutdown error", name)
        self.agent.stop("backend shutting down")
        for name, fn in (("detector", self.detector.stop), ("servo", self.servo.stop), ("camera", self.camera.stop), ("dht11", self.dht.stop),
                         ("imu", self.imu.stop), ("system", self.system.stop)):
            try:
                await asyncio.to_thread(fn)
            except Exception:
                log.exception("%s shutdown error", name)
        self.db.close()
        self.started = False
