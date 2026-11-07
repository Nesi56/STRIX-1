"""Environment observation and (experimental) visual mapping.

Mode A — sensor orientation: drone at a fixed origin, orientation from IMU.
Mode B — visual observations: OpenCV scene analysis log (image-relative).
Mode C — experimental mapping: pluggable localization sources. A monocular
         visual-odometry source is provided as an *experimental* relative
         trajectory (unknown scale, unbounded drift). It is never marked
         validated, so the system position remains:
         "Position unavailable — no validated localization source."

IMU acceleration is never integrated into a position.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config.settings import Settings
from app.database.db import Database
from app.services.base import StoppableThread
from app.services.camera_service import CameraService
from app.services.hub import EventLog

log = logging.getLogger(__name__)

POSITION_UNAVAILABLE = "Position unavailable — no validated localization source."


# ----------------------------------------------------------------------
# Localization source interface (future GPS / depth / range / VIO / SLAM)
# ----------------------------------------------------------------------
@dataclass
class LocalizationSource:
    id: str
    name: str
    kind: str  # "visual_odometry" | "gps" | "depth_camera" | "range_sensor" | "slam"
    installed: bool = False
    running: bool = False
    validated: bool = False  # only a validated source may provide "position"
    experimental: bool = False
    note: str = ""
    frame: str = ""  # coordinate frame of the estimate
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "kind": self.kind, "installed": self.installed,
            "running": self.running, "validated": self.validated, "experimental": self.experimental,
            "note": self.note, "frame": self.frame, **self.extra,
        }


# ----------------------------------------------------------------------
# Experimental monocular visual odometry
# ----------------------------------------------------------------------
class MonocularVO:
    """ORB features + essential matrix + recoverPose, frame to frame.

    Monocular VO cannot observe metric scale: each accepted step is given
    unit length, so the trajectory is in *arbitrary units*. Error accumulates
    without bound; the wide-angle lens is uncalibrated (approximate pinhole
    from HFOV). Treat the output as a qualitative motion trace only.
    """

    def __init__(self, hfov_deg: float, max_points: int = 1500) -> None:
        import cv2
        import numpy as np

        self.cv2, self.np = cv2, np
        self.hfov = hfov_deg
        self.orb = cv2.ORB_create(nfeatures=1200)
        self.matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        self.reset()
        self.max_points = max_points

    def reset(self) -> None:
        np = self.np
        self.R = np.eye(3)
        self.t = np.zeros(3)
        self.prev_kp = None
        self.prev_des = None
        self.trajectory: deque[tuple[float, float, float, float]] = deque(maxlen=1500)
        self.trajectory.append((time.time(), 0.0, 0.0, 0.0))
        self.state = "initializing"
        self.frames = 0
        self.accepted = 0
        self.lost_count = 0
        self.last_quality: dict[str, Any] = {}
        self.path_length = 0.0
        self.started_at = time.time()

    def _K(self, w: int, h: int):
        f = (w / 2.0) / math.tan(math.radians(self.hfov) / 2.0)
        return self.np.array([[f, 0, w / 2.0], [0, f, h / 2.0], [0, 0, 1.0]])

    def process(self, gray, ts: float) -> dict[str, Any]:
        cv2, np = self.cv2, self.np
        self.frames += 1
        kp, des = self.orb.detectAndCompute(gray, None)
        q: dict[str, Any] = {"features": len(kp), "matches": 0, "inliers": 0, "inlier_ratio": 0.0, "parallax_px": 0.0}
        if des is None or len(kp) < 50:
            self.state = "lost (too few features)"
            self.lost_count += 1
            self.last_quality = q
            return q
        if self.prev_des is None:
            self.prev_kp, self.prev_des = kp, des
            self.state = "initializing"
            self.last_quality = q
            return q
        matches = sorted(self.matcher.match(self.prev_des, des), key=lambda m: m.distance)[:600]
        q["matches"] = len(matches)
        if len(matches) < 40:
            self.state = "lost (few matches)"
            self.lost_count += 1
            self.prev_kp, self.prev_des = kp, des
            self.last_quality = q
            return q
        p1 = np.float32([self.prev_kp[m.queryIdx].pt for m in matches])
        p2 = np.float32([kp[m.trainIdx].pt for m in matches])
        parallax = float(np.median(np.linalg.norm(p2 - p1, axis=1)))
        q["parallax_px"] = round(parallax, 2)
        if parallax < 1.5:
            # Keep the old keyframe: not enough motion to triangulate a direction.
            self.state = "stationary (insufficient parallax)"
            self.last_quality = q
            return q
        h, w = gray.shape[:2]
        K = self._K(w, h)
        E, mask = cv2.findEssentialMat(p2, p1, K, method=cv2.RANSAC, prob=0.999, threshold=1.0)
        if E is None or E.shape != (3, 3):
            self.state = "lost (no essential matrix)"
            self.lost_count += 1
            self.prev_kp, self.prev_des = kp, des
            self.last_quality = q
            return q
        n_inl, R, t, _ = cv2.recoverPose(E, p2, p1, K, mask=mask)
        q["inliers"] = int(n_inl)
        q["inlier_ratio"] = round(n_inl / max(1, len(matches)), 3)
        if n_inl < 25 or q["inlier_ratio"] < 0.3:
            self.state = "weak tracking"
            self.prev_kp, self.prev_des = kp, des
            self.last_quality = q
            return q
        step = self.R @ t.ravel()  # unit-length step (scale unobservable)
        self.t = self.t + step
        self.R = R @ self.R
        self.path_length += float(np.linalg.norm(step))
        self.accepted += 1
        self.trajectory.append((ts, float(self.t[0]), float(self.t[1]), float(self.t[2])))
        self.prev_kp, self.prev_des = kp, des
        self.state = "tracking"
        self.last_quality = q
        return q

    def to_dict(self, max_points: int = 600) -> dict[str, Any]:
        traj = list(self.trajectory)
        stride = max(1, math.ceil(len(traj) / max_points))
        return {
            "state": self.state,
            "frames": self.frames,
            "accepted_steps": self.accepted,
            "lost_events": self.lost_count,
            "quality": self.last_quality,
            "path_length_units": round(self.path_length, 2),
            "running_s": time.time() - self.started_at,
            "trajectory": [{"ts": p[0], "x": p[1], "y": p[2], "z": p[3]} for p in traj[::stride]],
            "units": "arbitrary (monocular scale unknown)",
            "drift": "unbounded; grows with path length and time",
        }


# ----------------------------------------------------------------------
class MappingService:
    def __init__(self, settings: Settings, camera: CameraService, db: Database, events: EventLog,
                 active_session_id=lambda: None) -> None:
        self.settings = settings
        self.camera = camera
        self.db = db
        self.events = events
        self._session_id = active_session_id
        self.observations: deque[dict[str, Any]] = deque(maxlen=200)
        self._analyzer = None
        self._analyzer_error: str | None = None
        self._lock = threading.Lock()
        self.auto_interval_s = settings.vision_auto_interval_s
        self._auto_task: asyncio.Task | None = None
        self._vo: MonocularVO | None = None
        self._vo_thread: StoppableThread | None = None
        self.vo_error: str | None = None
        self._busy = asyncio.Lock()
        self.sources: dict[str, LocalizationSource] = {
            "visual_odometry": LocalizationSource(
                "visual_odometry", "Monocular visual odometry (OpenCV ORB)", "visual_odometry",
                installed=True, experimental=True, validated=False,
                note="Relative trajectory, unknown scale, unbounded drift. Never used as a position fix.",
                frame="camera start pose, arbitrary units"),
            "gps": LocalizationSource("gps", "GNSS receiver", "gps", note="Not installed. Interface reserved (NMEA/gpsd)."),
            "depth_camera": LocalizationSource("depth_camera", "Depth camera", "depth_camera", note="Not installed."),
            "range_sensor": LocalizationSource("range_sensor", "Range sensor (LiDAR/ToF/ultrasonic)", "range_sensor", note="Not installed."),
            "slam": LocalizationSource("slam", "Visual / visual-inertial SLAM", "slam",
                                       note="Not installed. Future integration point (e.g. ORB-SLAM3, OpenVINS)."),
        }

    # ---------------------------------------------------------- lifecycle
    def _get_analyzer(self):
        if self._analyzer is None and self._analyzer_error is None:
            try:
                from app.services.vision import SceneAnalyzer

                self._analyzer = SceneAnalyzer(self.settings.vision_onnx_model, self.settings.vision_onnx_labels,
                                               self.settings.vision_conf_threshold)
            except Exception as exc:  # OpenCV missing
                self._analyzer_error = f"vision pipeline unavailable: {exc}"
                log.warning(self._analyzer_error)
        return self._analyzer

    async def start(self) -> None:
        self._load_recent()
        self._auto_task = asyncio.create_task(self._auto_loop(), name="vision-auto")
        if self.settings.vo_enabled:
            self.set_vo(True)

    async def stop(self) -> None:
        if self._auto_task:
            self._auto_task.cancel()
        self.set_vo(False)

    def _load_recent(self) -> None:
        try:
            rows = self.db.query("SELECT ts, source, kind, data FROM observations ORDER BY ts DESC LIMIT 50")
        except Exception:
            return
        for r in reversed(rows):
            try:
                d = json.loads(r["data"])
                d.setdefault("ts", r["ts"])
                self.observations.append(d)
            except Exception:
                continue

    # ---------------------------------------------------------- Mode B
    def _analyze_sync(self, trigger: str, image_path: Path | None) -> dict[str, Any]:
        analyzer = self._get_analyzer()
        if analyzer is None:
            raise RuntimeError(self._analyzer_error or "vision unavailable")
        import cv2

        seq, frame = self.camera.hub.latest()
        meta: dict[str, Any] = {}
        if image_path is not None:
            bgr = cv2.imread(str(image_path))
            if bgr is None:
                raise RuntimeError("could not read snapshot image")
            meta = {"source": "snapshot", "snapshot": image_path.name, "synthetic": "_synthetic" in image_path.name}
            ts = image_path.stat().st_mtime
            w = self.settings.camera_analysis_width
            if bgr.shape[1] > w:
                bgr = cv2.resize(bgr, (w, int(w * bgr.shape[0] / bgr.shape[1])), interpolation=cv2.INTER_AREA)
        else:
            bgr = self.camera.analysis_frame()
            ts = time.time()
            meta = {"source": "live", "frame_seq": seq, "synthetic": bool(frame.synthetic) if frame else self.camera.status.synthetic}
        result = analyzer.analyze(bgr, meta)
        result.update({"ts": ts, "trigger": trigger, "id": f"obs-{int(ts * 1000)}"})
        return result

    async def analyze_now(self, trigger: str = "manual", image_path: Path | None = None) -> dict[str, Any]:
        async with self._busy:
            obs = await asyncio.to_thread(self._analyze_sync, trigger, image_path)
        self.observations.append(obs)
        self.db.enqueue("INSERT INTO observations(session_id, ts, source, kind, data) VALUES (?,?,?,?,?)",
                        (self._session_id(), obs["ts"], "opencv", obs["kind"], json.dumps(obs)))
        return obs

    async def _auto_loop(self) -> None:
        while True:
            interval = self.auto_interval_s
            if interval and interval > 0 and self.camera.available:
                try:
                    await self.analyze_now("auto")
                except Exception as exc:
                    log.debug("auto analysis skipped: %s", exc)
                await asyncio.sleep(interval)
            else:
                await asyncio.sleep(1.0)

    def latest_observations(self, n: int = 20) -> list[dict[str, Any]]:
        return list(self.observations)[-n:]

    # ---------------------------------------------------------- Mode C
    def set_vo(self, enabled: bool) -> dict[str, Any]:
        src = self.sources["visual_odometry"]
        if enabled and self._vo_thread is None:
            if not self.camera.available:
                self.vo_error = "camera unavailable"
                return self.vo_state()
            try:
                self._vo = MonocularVO(self.settings.vo_camera_hfov_deg)
            except Exception as exc:
                self.vo_error = f"OpenCV unavailable: {exc}"
                return self.vo_state()
            self.vo_error = None
            self._vo_thread = StoppableThread(self._vo_loop, "visual-odometry")
            self._vo_thread.start()
            src.running = True
            self.events.add("info", "mapping", "experimental visual odometry started")
        elif not enabled and self._vo_thread is not None:
            self._vo_thread.stop()
            self._vo_thread = None
            src.running = False
            self.events.add("info", "mapping", "visual odometry stopped")
        return self.vo_state()

    def reset_vo(self) -> dict[str, Any]:
        if self._vo:
            with self._lock:
                self._vo.reset()
        return self.vo_state()

    def _vo_loop(self, stop: threading.Event) -> None:
        import cv2

        period = 0.2  # 5 Hz keeps CPU modest on the Pi 5
        while not stop.is_set():
            t0 = time.monotonic()
            try:
                bgr = self.camera.analysis_frame()
                gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
                with self._lock:
                    if self._vo:
                        self._vo.process(gray, time.time())
                self.vo_error = None
            except Exception as exc:
                self.vo_error = str(exc)
                stop.wait(1.0)
            stop.wait(max(0.0, period - (time.monotonic() - t0)))

    def vo_state(self, max_points: int = 600) -> dict[str, Any]:
        with self._lock:
            vo = self._vo.to_dict(max_points) if self._vo else None
        return {
            "enabled": self._vo_thread is not None,
            "error": self.vo_error,
            "experimental": True,
            "validated": False,
            "estimate": vo,
            "synthetic_input": self.camera.status.synthetic,
        }

    def position(self) -> dict[str, Any]:
        validated = [s for s in self.sources.values() if s.validated and s.running]
        if not validated:
            return {"available": False, "message": POSITION_UNAVAILABLE, "source": None}
        # Reserved for future validated sources (e.g. GNSS fix with HDOP).
        return {"available": False, "message": POSITION_UNAVAILABLE, "source": None}

    def state(self, include_trajectory: bool = True) -> dict[str, Any]:
        return {
            "position": self.position(),
            "sources": [s.to_dict() for s in self.sources.values()],
            "visual_odometry": self.vo_state() if include_trajectory else {"enabled": self._vo_thread is not None},
            "vision": {
                "available": self._get_analyzer() is not None,
                "error": self._analyzer_error,
                "methods": self._analyzer.methods if self._analyzer else [],
                "auto_interval_s": self.auto_interval_s,
                "observations": len(self.observations),
                "latest": self.observations[-1] if self.observations else None,
            },
            "imu_position": "not computed — accelerometer double integration is not a valid localization source",
        }
