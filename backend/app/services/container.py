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
from app.services.memory_service import MemoryService
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
                                        session_id=lambda: (self.recording.active_session or {}).get("id"),
                                        pose=self.camera_pose_now)
        self.qwen = QwenService(settings, self.events, register_process=self.system.register_process)
        self.memory = MemoryService(self)
        self.agent = AgentService(self)
        self.events.listeners.append(self.telemetry.broadcast_event)
        self.started = False

    def _camera_pose(self) -> tuple[float, float, float | None, float]:
        """Pose for rendering the synthetic camera only: roll, pitch, servo angle, yaw. Uses the simulated true
        attitude when available, so the uncalibrated gyro's yaw drift shows up in the memory (as on real
        hardware) instead of slowly turning the synthetic view away from the table."""
        truth = self.imu.true_attitude()
        if truth is not None:
            return truth[0], truth[1], self.servo.current, truth[2]
        o = (self.imu.latest() or {}).get("orientation") or {}
        return o.get("roll", 0.0), o.get("pitch", 0.0), self.servo.current, o.get("yaw_relative", 0.0)

    def camera_pose_now(self) -> dict[str, float]:
        """Camera pose when a frame is grabbed: IMU yaw (relative)/pitch/roll and servo tilt (+ up), degrees."""
        o = (self.imu.latest() or {}).get("orientation") or {}
        return {"yaw": float(o.get("yaw_relative", 0.0)), "pitch": float(o.get("pitch", 0.0)),
                "roll": float(o.get("roll", 0.0)), "tilt": float(self.servo.tilt_deg() or 0.0)}

    DEMO_PROMPTS = {"cups": ["cup", "sports ball", "person"], "outdoor": ["person", "car"]}

    def set_demo_scene(self, name: str) -> dict[str, Any]:
        """Synthetic mode only: switch the demo scene, matching IMU motion and YOLO prompts; clears the memory."""
        out = self.camera.set_scene(name)
        self.imu.set_mock_profile(name)
        self.detector.set_prompts(self.DEMO_PROMPTS[name])
        self.memory.clear()
        return {**out, "prompts": list(self.detector.prompts)}

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
        if self.camera.is_synthetic and self.settings.mock_scene != "outdoor":
            self.imu.set_mock_profile(self.settings.mock_scene)
            self.detector.set_prompts(self.DEMO_PROMPTS[self.settings.mock_scene], quiet=True)
        await self._safe("memory", self.memory.start)
        await self._safe("recording", self.recording.start)
        await self._safe("mapping", self.mapping.start)
        await self._safe("telemetry", self.telemetry.start)
        await self._safe("qwen", self.qwen.start)
        await self._safe("agent", self.agent.start)
        self.started = True
        mode = "SYNTHETIC DEMO MODE" if self.settings.mock else "hardware mode"
        self.events.add("info", "system", f"STRIX-1 backend started ({mode})")

    async def stop(self) -> None:
        for name, fn in (
            ("telemetry", self.telemetry.stop), ("recording", self.recording.stop), ("mapping", self.mapping.stop),
            ("qwen", self.qwen.stop), ("memory", self.memory.stop),
        ):
            try:
                await fn()
            except Exception:
                log.exception("%s shutdown error", name)
        self.agent.stop("backend shutting down")
        await self.agent.shutdown()
        for name, fn in (("detector", self.detector.stop), ("servo", self.servo.stop), ("camera", self.camera.stop), ("dht11", self.dht.stop),
                         ("imu", self.imu.stop), ("system", self.system.stop)):
            try:
                await asyncio.to_thread(fn)
            except Exception:
                log.exception("%s shutdown error", name)
        self.db.close()
        self.started = False
