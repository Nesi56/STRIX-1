"""Continuous object detection on the camera's analysis stream ("YOLO stream").

Runs in its own thread at up to ``STRIX_DETECTOR_HZ`` (inference time is the real
limit), publishes the newest result for the WebSocket, the video overlay, the radar
view and the Qwen agent. Prompts (``person``, ``car`` …) choose which classes are
shown; the agent can still see every class.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from app.config.settings import REPO_ROOT, Settings
from app.services.base import DeviceState, DeviceStatus, StoppableThread
from app.services.camera_service import CameraService
from app.services.detector import (
    COCO80,
    SIZE_PRIORS_M,
    Detection,
    HogPersonDetector,
    OnnxDetector,
    estimate_geometry,
    find_default_model,
    labels_for_model,
    normalize_label,
    parse_size_priors,
)
from app.services.hub import EventLog

log = logging.getLogger(__name__)


class DetectorError(ValueError):
    pass


class DetectorService:
    def __init__(self, settings: Settings, camera: CameraService, events: EventLog, db=None,
                 session_id=lambda: None, pose=None, load=None) -> None:
        self.settings = settings
        self._pose = pose  # () -> {"yaw", "pitch", "roll", "tilt"} sampled when a frame is grabbed
        self.camera = camera
        self.events = events
        self.db = db
        self._session_id = session_id
        self.status = DeviceStatus("detector")
        self._engine: Any = None
        self.engine_kind = "none"  # onnx | hog | synthetic
        self.description = ""
        self.vocabulary: list[str] = list(COCO80)
        self._thread: StoppableThread | None = None
        self._lock = threading.Lock()
        self._latest: dict[str, Any] | None = None
        self._seq = 0
        self._fps = 0.0
        self._infer_ms: float | None = None
        self._last_logged: tuple[str, ...] = ()
        self._last_log_t = 0.0
        self.prompts: list[str] = []
        self._requested = [p for p in settings.detector_prompts.split(",") if p.strip()]
        self.set_prompts(self._requested, quiet=True)
        self._load = load  # () -> (cpu_temp_c | None, qwen_busy): slow down when the Pi is hot or Qwen is thinking
        self.slowed: str | None = None

    # ------------------------------------------------------------ setup
    def _labels(self, model: str = "") -> list[str] | None:
        if self.settings.detector_labels and Path(self.settings.detector_labels).is_file():
            return [l.strip() for l in Path(self.settings.detector_labels).read_text().splitlines() if l.strip()]
        return labels_for_model(Path(model)) if model else None

    def _choose_engine(self) -> None:
        mode = self.settings.detector_backend
        if mode == "disabled":
            self.status.set(DeviceState.DISABLED, "disabled by configuration")
            return
        synthetic_cam = self.camera.status.synthetic
        if mode == "synthetic" or (mode == "auto" and synthetic_cam):
            self.engine_kind, self.status.synthetic = "synthetic", True
            self.status.backend = "synthetic-ground-truth"
            self.description = "Synthetic ground truth from the mock camera (demo only)"
            return
        errors = []
        if mode in ("auto", "onnx"):
            model = self.settings.detector_model or str(find_default_model(REPO_ROOT / "models") or "")
            try:
                if not model:
                    raise FileNotFoundError("no ONNX model in models/ (run scripts/get_detector_model.sh)")
                self._engine = OnnxDetector(model, self.settings.detector_format, self._labels(model),
                                            self.settings.detector_conf, self.settings.detector_iou,
                                            self.settings.detector_imgsz)
                self.engine_kind, self.status.backend = "onnx", f"onnx:{self._engine.format}"
                self.description = self._engine.description
                self.vocabulary = list(dict.fromkeys(self._engine.labels))
                return
            except Exception as exc:
                errors.append(str(exc))
                if mode == "onnx":
                    self.status.set(DeviceState.ERROR, f"ONNX detector failed: {exc}")
                    return
        try:
            self._engine = HogPersonDetector()
            self.engine_kind, self.status.backend = "hog", "opencv-hog"
            self.description = self._engine.description
            self.vocabulary = ["person"]
            if errors:
                self.events.add("warning", "detector", f"YOLO model unavailable ({errors[0]}); using HOG person detector")
        except Exception as exc:
            self.status.set(DeviceState.ERROR, f"no detector available: {exc}")

    def start(self) -> None:
        self._choose_engine()
        if self.status.state in (DeviceState.DISABLED, DeviceState.ERROR):
            return
        # Re-apply the configured prompts against this engine's classes (e.g. "pen" needs the everyday model);
        # drop the ones it cannot detect (HOG only knows "person").
        self.set_prompts(self._requested, quiet=True)
        self.prompts = [p for p in self.prompts if p in self.vocabulary] or [self.vocabulary[0]]
        self._thread = StoppableThread(self._run, "detector")
        self._thread.start()

    def stop(self) -> None:
        if self._thread:
            self._thread.stop()

    # ------------------------------------------------------------ prompts
    def set_prompts(self, prompts: list[str], quiet: bool = False) -> list[str]:
        cleaned: list[str] = []
        unknown: list[str] = []
        for p in prompts:
            lab = normalize_label(p, self.vocabulary)
            if lab is None:
                unknown.append(p.strip())
            elif lab not in cleaned:
                cleaned.append(lab)
        if unknown and not quiet:
            raise DetectorError(
                f"not in the detector's vocabulary: {', '.join(unknown)} "
                f"({'person only (basic detector)' if self.engine_kind == 'hog' else 'not one of the objects this YOLO model knows'})"
            )
        if len(cleaned) > 12:
            raise DetectorError("at most 12 prompts")
        self.prompts = cleaned
        if not quiet:
            self.events.add("info", "detector", f"prompts: {', '.join(cleaned) or '(all classes)'}")
        return self.prompts

    def size_priors(self) -> dict[str, float]:
        """Typical object sizes for distance estimates: defaults, then .env overrides, then the synthetic scene's
        true sizes (demo only, so the synthetic table-top ball isn't sized like a football)."""
        out = dict(SIZE_PRIORS_M)
        out.update(parse_size_priors(self.settings.detector_size_priors))
        if self.engine_kind == "synthetic":
            out.update(self.camera.scene_size_priors())
        return out

    # ------------------------------------------------------------ loop
    def _sample_pose(self) -> dict[str, float] | None:
        if self._pose is None:
            return None
        try:
            return self._pose()
        except Exception:
            return None

    def _detect_once(self) -> tuple[int, int, list[tuple[str, float, tuple[float, float, float, float]]], int | None]:
        seq, frame = self.camera.hub.latest()
        if self.engine_kind == "synthetic":
            gt = self.camera.ground_truth()
            if gt is None:
                raise RuntimeError("no synthetic frame yet")
            w, h, dets = gt
            return w, h, dets, seq
        bgr = self.camera.analysis_frame()
        h, w = bgr.shape[:2]
        return w, h, self._engine.detect(bgr), seq

    def period(self) -> float:
        """Seconds between detections. Slower when the CPU is hot or Qwen is generating, to keep the Pi cool."""
        hz, reason = self.settings.detector_hz, None
        if self._load is not None:
            try:
                temp, busy = self._load()
            except Exception:
                temp, busy = None, False
            if temp is not None and temp >= 80:
                hz, reason = min(hz, 1.0), f"CPU {temp:.0f} °C"
            elif temp is not None and temp >= 75:
                hz, reason = min(hz, 1.5), f"CPU {temp:.0f} °C"
            elif busy:
                hz, reason = min(hz, 2.0), "Qwen is thinking"
        self.slowed = reason if hz < self.settings.detector_hz else None
        return 1.0 / hz

    def _run(self, stop: threading.Event) -> None:
        times: list[float] = []
        while not stop.is_set():
            period = self.period()
            t0 = time.monotonic()
            if not self.camera.available:
                self.status.set(DeviceState.DISCONNECTED, "camera unavailable")
                stop.wait(1.0)
                continue
            try:
                pose = self._sample_pose()
                w, h, raw, seq = self._detect_once()
                infer_ms = (time.monotonic() - t0) * 1000.0
            except Exception as exc:
                self.status.set(DeviceState.DEGRADED, f"detection failed: {exc}")
                stop.wait(0.5)
                continue
            dets = []
            priors = self.size_priors()
            for label, conf, (x, y, bw, bh) in raw:
                dist, kind, bearing, elev = estimate_geometry(label, (x, y, bw, bh), w, h, self.settings.camera_hfov_deg,
                                                              priors)
                dets.append(Detection(
                    label=label, confidence=round(conf, 3),
                    bbox=[round(x / w, 4), round(y / h, 4), round(bw / w, 4), round(bh / h, 4)],
                    distance_m=dist, distance_kind=kind, bearing_deg=round(bearing, 1), elevation_deg=round(elev, 1),
                    source=self.engine_kind,
                ).to_dict())
            times.append(time.monotonic())
            times = [t for t in times if times[-1] - t <= 3.0]
            self._fps = (len(times) - 1) / (times[-1] - times[0]) if len(times) > 1 and times[-1] > times[0] else 0.0
            self._infer_ms = infer_ms
            with self._lock:
                self._seq += 1
                self._latest = {
                    "seq": self._seq, "ts": time.time(), "frame_seq": seq, "frame_w": w, "frame_h": h,
                    "all": dets, "synthetic": self.status.synthetic, "pose": pose,
                }
            self.status.touch()
            self.status.set(DeviceState.SYNTHETIC if self.status.synthetic else DeviceState.OK)
            self._log_detections(dets)
            stop.wait(max(0.0, period - (time.monotonic() - t0)))

    def _log_detections(self, dets: list[dict[str, Any]]) -> None:
        """Persist a compact detection record when the set of labels changes (≤ 1 per 2 s)."""
        if self.db is None:
            return
        labels = tuple(sorted(d["label"] for d in dets))
        now = time.time()
        if labels == self._last_logged or now - self._last_log_t < 2.0:
            return
        self._last_logged, self._last_log_t = labels, now
        brief = [{k: d[k] for k in ("label", "confidence", "distance_m", "bearing_deg")} for d in dets[:20]]
        self.db.enqueue("INSERT INTO observations(session_id, ts, source, kind, data) VALUES (?,?,?,?,?)",
                        (self._session_id(), now, self.engine_kind, "detections", json.dumps(brief)))

    # ------------------------------------------------------------ readers
    def latest(self, prompted_only: bool = True) -> dict[str, Any] | None:
        with self._lock:
            res = self._latest
        if res is None:
            return None
        out = dict(res)
        out["detections"] = [d for d in res["all"] if not self.prompts or d["label"] in self.prompts] if prompted_only else res["all"]
        if prompted_only:
            out.pop("all")
        out["prompts"] = list(self.prompts)
        return out

    def find(self, label: str) -> list[dict[str, Any]]:
        res = self.latest(prompted_only=False)
        if not res or time.time() - res["ts"] > 2.0:
            return []
        return sorted((d for d in res["detections"] if d["label"] == label), key=lambda d: -d["confidence"])

    def state(self) -> dict[str, Any]:
        return {
            "status": self.status.to_dict(),
            "engine": self.engine_kind,
            "description": self.description,
            "prompts": list(self.prompts),
            "vocabulary_size": len(self.vocabulary),
            "fps": round(self._fps, 2),
            "infer_ms": round(self._infer_ms, 1) if self._infer_ms is not None else None,
            "hfov_deg": self.settings.camera_hfov_deg,
            "slowed": self.slowed,
            "classes": len(self.vocabulary),
            "distance_method": "pinhole size prior (typical object height); ±30 % or worse",
        }
