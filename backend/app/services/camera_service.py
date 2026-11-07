"""Camera Module 3 Wide capture, MJPEG streaming, snapshots and recording.

Architecture (Picamera2 backend)
--------------------------------
One libcamera pipeline with two streams:

* ``main`` (selected resolution, YUV420) → ``JpegEncoder`` → browser MJPEG
  stream; and, on demand, → ``H264Encoder`` → MP4 recording (and optionally
  an RTSP push to MediaMTX for WebRTC).
* ``lores`` (≈640 px wide, YUV420) → ``capture_array`` for computer vision
  and visual odometry at a low rate.

Every encoder runs in Picamera2's own threads, so streaming, recording and AI
analysis never block each other or the asyncio event loop. On the Pi 5 there
is no hardware video encoder: both encoders are software (libav/simplejpeg)
and consume CPU — see docs/PERFORMANCE.md.

Frame timestamps: each JPEG carries the sensor capture time converted to wall
clock (via CLOCK_BOOTTIME, which libcamera's SensorTimestamp uses). The
browser combines this with a measured clock offset to estimate
capture-to-display latency.
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from app.config.settings import Settings
from app.services.base import DeviceState, DeviceStatus, StoppableThread
from app.services.hub import EventLog, LatestValueHub

log = logging.getLogger(__name__)

RESOLUTION_PRESETS: list[tuple[int, int]] = [(640, 360), (1280, 720), (1920, 1080), (2304, 1296)]
FPS_OPTIONS = [10, 15, 24, 30, 50, 60]


class CameraError(RuntimeError):
    pass


@dataclass
class Frame:
    jpeg: bytes
    seq: int
    capture_ts: float  # wall-clock seconds
    width: int
    height: int
    synthetic: bool
    ts_method: str = "sensor"


@dataclass
class RecordingState:
    active: bool = False
    path: Path | None = None
    started_at: float | None = None
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def choose_sensor_mode(modes: list[dict[str, Any]], size: tuple[int, int], fps: float) -> dict[str, Any] | None:
    """Pick the smallest sensor mode that covers ``size`` without upscaling at ``fps``."""
    ok = [m for m in modes if m["size"][0] >= size[0] and m["size"][1] >= size[1] and m.get("fps", 0) >= fps - 0.5]
    if not ok:
        return None
    return min(ok, key=lambda m: (m["size"][0] * m["size"][1], -m.get("bit_depth", 0)))


def supported_mode_matrix(modes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolution presets and the frame rates the sensor modes can deliver.

    These are *capabilities reported by libcamera*; the FPS indicator shows
    what is actually achieved, which is the real validation.
    """
    out = []
    for w, h in RESOLUTION_PRESETS:
        fps = [f for f in FPS_OPTIONS if choose_sensor_mode(modes, (w, h), f) is not None]
        if fps:
            out.append({"width": w, "height": h, "fps": fps})
    return out


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


class FpsMeter:
    def __init__(self) -> None:
        self._times: list[float] = []

    def tick(self, t: float) -> None:
        self._times.append(t)
        cutoff = t - 2.0
        while self._times and self._times[0] < cutoff:
            self._times.pop(0)

    def fps(self) -> float:
        if len(self._times) < 2:
            return 0.0
        span = self._times[-1] - self._times[0]
        return (len(self._times) - 1) / span if span > 0 else 0.0


# --------------------------------------------------------------------------
# Picamera2 backend (real hardware) — requires validation on the Pi 5.
# --------------------------------------------------------------------------
class Picamera2Backend:
    name = "picamera2"
    synthetic = False

    def __init__(self, settings: Settings, publish: Callable[[Frame], None]) -> None:
        from picamera2 import Picamera2  # type: ignore[import-not-found]

        if not Picamera2.global_camera_info():
            raise CameraError("no camera detected by libcamera (check ribbon cable, run 'rpicam-hello --list-cameras')")
        self.settings = settings
        self._publish = publish
        self.picam2 = Picamera2()
        self.camera_info = Picamera2.global_camera_info()[0]
        self.sensor_modes = self.picam2.sensor_modes
        self._seq = 0
        self._jpeg = None
        self._h264 = None
        self._h264_outputs: dict[str, Any] = {}
        self._lock = threading.RLock()
        self.size = (settings.camera_width, settings.camera_height)
        self.fps = settings.camera_fps
        self.lores_size = (0, 0)
        self.sensor_mode: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    def _make_stream_output(self):
        from picamera2.outputs import Output  # type: ignore[import-not-found]

        backend = self

        class _StreamOutput(Output):
            def outputframe(self, frame, keyframe=True, timestamp=None, packet=None, audio=False):
                backend._on_jpeg(frame, timestamp)

        return _StreamOutput()

    def _on_jpeg(self, frame: bytes, timestamp_us: int | None) -> None:
        now = time.time()
        capture_ts, method = now, "encoder-output"
        enc = self._jpeg
        first = getattr(enc, "firsttimestamp", None)
        if first is not None and timestamp_us is not None:
            sensor_us = first + timestamp_us
            boot_us = time.clock_gettime_ns(time.CLOCK_BOOTTIME) // 1000
            age = (boot_us - sensor_us) / 1e6
            if 0.0 <= age < 5.0:
                capture_ts, method = now - age, "sensor"
        self._seq += 1
        self._publish(Frame(bytes(frame), self._seq, capture_ts, self.size[0], self.size[1], False, method))

    def start(self, size: tuple[int, int] | None = None, fps: float | None = None) -> None:
        from libcamera import Transform, controls  # type: ignore[import-not-found]
        from picamera2.encoders import JpegEncoder  # type: ignore[import-not-found]

        with self._lock:
            if size:
                self.size = size
            if fps:
                self.fps = fps
            w, h = self.size
            mode = choose_sensor_mode(self.sensor_modes, (w, h), self.fps)
            if mode is None:
                raise CameraError(f"no sensor mode supports {w}x{h} @ {self.fps:g} fps")
            self.sensor_mode = {k: mode[k] for k in ("size", "fps", "bit_depth") if k in mode}
            lw = min(self.settings.camera_analysis_width, w)
            self.lores_size = (_even(lw), _even(lw * h / w))
            cfg = self.picam2.create_video_configuration(
                main={"size": (w, h), "format": "YUV420"},
                lores={"size": self.lores_size, "format": "YUV420"},
                sensor={"output_size": mode["size"], "bit_depth": mode["bit_depth"]},
                controls={"FrameRate": float(self.fps)},
                transform=Transform(hflip=self.settings.camera_hflip, vflip=self.settings.camera_vflip),
                buffer_count=6,
            )
            self.picam2.configure(cfg)
            self.picam2.start()
            if self.settings.camera_autofocus and "AfMode" in self.picam2.camera_controls:
                self.picam2.set_controls({"AfMode": controls.AfModeEnum.Continuous})
            # 2 encoder threads leave headroom for recording + local LLM.
            self._jpeg = JpegEncoder(num_threads=2, q=self.settings.camera_jpeg_quality)
            self.picam2.start_encoder(self._jpeg, self._make_stream_output(), name="main")
            if self.settings.webrtc_enabled:
                try:
                    self._attach_h264("rtsp", self._make_pyav_output(self.settings.mediamtx_rtsp_url, "rtsp"))
                except Exception as exc:  # optional path, never fatal
                    log.warning("WebRTC/RTSP push disabled: %s", exc)

    def stop(self) -> None:
        with self._lock:
            try:
                self.picam2.stop_encoder()
            except Exception:
                pass
            self._h264, self._h264_outputs, self._jpeg = None, {}, None
            try:
                self.picam2.stop()
            except Exception:
                pass

    def close(self) -> None:
        self.stop()
        try:
            self.picam2.close()
        except Exception:
            pass

    def reconfigure(self, size: tuple[int, int], fps: float) -> None:
        with self._lock:
            self.stop()
            self.start(size, fps)

    # ------------------------------------------------------------ H.264
    @staticmethod
    def _make_pyav_output(target: str, fmt: str | None = None):
        from picamera2.outputs import PyavOutput  # type: ignore[import-not-found]

        return PyavOutput(target, format=fmt)

    def _attach_h264(self, key: str, out: Any) -> None:
        from picamera2.encoders import H264Encoder  # type: ignore[import-not-found]

        with self._lock:
            if self._h264 is None:
                self._h264 = H264Encoder(bitrate=self.settings.camera_record_bitrate, iperiod=int(self.fps))
                self._h264_outputs = {key: out}
                self.picam2.start_encoder(self._h264, out, name="main")
            else:
                # Encoder already running (e.g. RTSP): share it. The container must
                # be open before the encoder announces its streams to the output.
                out.start()
                self._h264_outputs[key] = out
                self._h264.output = list(self._h264_outputs.values())

    def _detach_h264(self, key: str) -> None:
        with self._lock:
            out = self._h264_outputs.pop(key, None)
            if out is None or self._h264 is None:
                return
            if not self._h264_outputs:
                self.picam2.stop_encoder(self._h264)
                self._h264 = None
            else:
                self._h264.output = list(self._h264_outputs.values())
                out.stop()

    def start_recording(self, path: Path) -> Path:
        try:
            out = self._make_pyav_output(str(path))
        except ImportError:
            from picamera2.outputs import FileOutput  # type: ignore[import-not-found]

            path = path.with_suffix(".h264")
            out = FileOutput(str(path))
        self._attach_h264("record", out)
        return path

    def stop_recording(self) -> None:
        self._detach_h264("record")

    # ----------------------------------------------------------- vision
    def analysis_frame(self):
        import cv2  # type: ignore[import-not-found]

        yuv = self.picam2.capture_array("lores")  # blocks until next frame (worker thread only)
        return cv2.cvtColor(yuv, cv2.COLOR_YUV420p2BGR)

    def info(self) -> dict[str, Any]:
        return {
            "model": self.camera_info.get("Model"),
            "sensor_mode": self.sensor_mode,
            "lores_size": list(self.lores_size),
            "modes": supported_mode_matrix(self.sensor_modes),
            "raw_sensor_modes": [
                {"size": list(m["size"]), "fps": m.get("fps"), "bit_depth": m.get("bit_depth")} for m in self.sensor_modes
            ],
        }


# --------------------------------------------------------------------------
# Synthetic camera (demo/testing). Every frame is stamped SYNTHETIC.
# --------------------------------------------------------------------------
class MockCameraBackend:
    name = "mock-camera"
    synthetic = True
    # Pretend sensor modes resembling the IMX708 Wide so the UI logic is exercised.
    sensor_modes = [
        {"size": (1536, 864), "fps": 120.13, "bit_depth": 10},
        {"size": (2304, 1296), "fps": 56.03, "bit_depth": 10},
        {"size": (4608, 2592), "fps": 14.35, "bit_depth": 10},
    ]

    def __init__(
        self,
        settings: Settings,
        publish: Callable[[Frame], None],
        pose: Callable[[], tuple[float, float, float | None]] | None = None,
    ) -> None:
        import cv2  # noqa: F401  (fail early if OpenCV is missing)
        import numpy  # noqa: F401

        self.settings = settings
        self._publish = publish
        self._pose = pose
        self.size = (settings.camera_width, settings.camera_height)
        self.fps = settings.camera_fps
        self._thread: StoppableThread | None = None
        self._seq = 0
        self._writer = None
        self._writer_lock = threading.Lock()
        self._last_bgr = None
        self._bg = None
        self._rng = random.Random(7)

    def _render_background(self, w: int, h: int):
        import cv2
        import numpy as np

        H = int(h * 1.8)  # taller canvas so tilt can pan vertically
        W = int(w * 1.4)
        img = np.zeros((H, W, 3), np.uint8)
        horizon = H // 2
        for y in range(horizon):  # sky gradient
            t = y / horizon
            img[y, :] = (int(235 - 60 * t), int(190 - 40 * t), int(140 - 50 * t))
        img[horizon:, :] = (60, 110, 70)
        # path receding to the horizon
        pts = np.array([[W // 2 - W // 30, horizon], [W // 2 + W // 30, horizon], [W // 2 + W // 4, H], [W // 2 - W // 4, H]])
        cv2.fillPoly(img, [pts], (120, 135, 140))
        # building
        bx, by, bw, bh = int(W * 0.62), horizon - int(H * 0.17), int(W * 0.14), int(H * 0.17)
        cv2.rectangle(img, (bx, by), (bx + bw, horizon), (110, 110, 118), -1)
        for wx in range(bx + 10, bx + bw - 20, max(20, bw // 6)):
            for wy in range(by + 12, horizon - 20, max(24, bh // 5)):
                cv2.rectangle(img, (wx, wy), (wx + max(8, bw // 14), wy + max(10, bh // 10)), (170, 200, 215), -1)
        # trees
        for i, tx in enumerate([0.12, 0.2, 0.3, 0.42, 0.85]):
            x = int(W * tx)
            r = int(H * (0.04 + 0.01 * (i % 3)))
            cv2.rectangle(img, (x - r // 6, horizon - r), (x + r // 6, horizon + r // 3), (40, 60, 90), -1)
            cv2.circle(img, (x, horizon - r - r // 2), r, (40, 120 + 10 * i, 50), -1)
        return img

    def _frame(self, t: float):
        import cv2

        w, h = self.size
        if self._bg is None or self._bg.shape[1] != int(w * 1.4):
            self._bg = self._render_background(w, h)
        bg = self._bg
        H, W = bg.shape[:2]
        roll, pitch, servo = (0.0, 0.0, None)
        if self._pose:
            try:
                roll, pitch, servo = self._pose()
            except Exception:
                pass
        tilt = (servo - 90.0) if servo is not None else 0.0
        # Vertical pan from servo tilt + pitch (≈ pixels per degree for a ~100° HFOV).
        ppd = w / 100.0
        cy = H / 2 + (tilt + pitch) * ppd
        cx = W / 2 + 40 * math.sin(t * 0.07)
        x0 = int(max(0, min(W - w, cx - w / 2)))
        y0 = int(max(0, min(H - h, cy - h / 2)))
        view = bg[y0 : y0 + h, x0 : x0 + w].copy()
        # A walking "person" for the detector to find.
        px = int((0.5 + 0.35 * math.sin(t * 0.2)) * w)
        py = int(h * 0.62)
        s = max(6, h // 30)
        cv2.circle(view, (px, py - 5 * s), s, (60, 80, 200), -1)
        cv2.rectangle(view, (px - s, py - 4 * s), (px + s, py), (50, 50, 160), -1)
        cv2.line(view, (px - s // 2, py), (px - s, py + 3 * s), (40, 40, 40), max(2, s // 3))
        cv2.line(view, (px + s // 2, py), (px + s, py + 3 * s), (40, 40, 40), max(2, s // 3))
        if abs(roll) > 0.01:
            M = cv2.getRotationMatrix2D((w / 2, h / 2), -roll, 1.0)
            view = cv2.warpAffine(view, M, (w, h), borderMode=cv2.BORDER_REPLICATE)
        noise = self._rng.randint(0, 3)
        if noise:
            view = cv2.add(view, noise)
        fs = max(0.5, h / 900)
        cv2.rectangle(view, (0, 0), (w, int(44 * fs)), (0, 0, 0), -1)
        cv2.putText(view, "SYNTHETIC CAMERA FEED - NOT REAL IMAGERY", (int(12 * fs), int(30 * fs)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9 * fs, (0, 200, 255), max(1, int(2 * fs)), cv2.LINE_AA)
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        cv2.putText(view, f"{stamp}  #{self._seq}", (w - int(330 * fs), int(30 * fs)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8 * fs, (230, 230, 230), max(1, int(2 * fs)), cv2.LINE_AA)
        return view

    def _run(self, stop: threading.Event) -> None:
        import cv2

        next_t = time.monotonic()
        t0 = time.monotonic()
        while not stop.is_set():
            capture_ts = time.time()
            bgr = self._frame(time.monotonic() - t0)
            ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, self.settings.camera_jpeg_quality])
            self._last_bgr = bgr
            with self._writer_lock:
                if self._writer is not None:
                    self._writer.write(bgr)
            if ok:
                self._seq += 1
                self._publish(Frame(buf.tobytes(), self._seq, capture_ts, self.size[0], self.size[1], True, "synthetic"))
            next_t += 1.0 / self.fps
            d = next_t - time.monotonic()
            if d > 0:
                stop.wait(d)
            else:
                next_t = time.monotonic()

    def start(self, size: tuple[int, int] | None = None, fps: float | None = None) -> None:
        if size:
            self.size = size
        if fps:
            self.fps = fps
        if choose_sensor_mode(self.sensor_modes, self.size, self.fps) is None:
            raise CameraError(f"no sensor mode supports {self.size[0]}x{self.size[1]} @ {self.fps:g} fps")
        self._bg = None
        self._thread = StoppableThread(self._run, "mock-camera")
        self._thread.start()

    def stop(self) -> None:
        if self._thread:
            self._thread.stop()
            self._thread = None

    def close(self) -> None:
        self.stop_recording()
        self.stop()

    def reconfigure(self, size: tuple[int, int], fps: float) -> None:
        self.stop()
        self.start(size, fps)

    def start_recording(self, path: Path) -> Path:
        import cv2

        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, self.size)
        if not writer.isOpened():
            path = path.with_suffix(".avi")
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), self.fps, self.size)
            if not writer.isOpened():
                raise CameraError("OpenCV VideoWriter unavailable")
        with self._writer_lock:
            self._writer = writer
        return path

    def stop_recording(self) -> None:
        with self._writer_lock:
            if self._writer is not None:
                self._writer.release()
                self._writer = None

    def analysis_frame(self):
        import cv2

        bgr = self._last_bgr
        if bgr is None:
            raise CameraError("no frame yet")
        w = self.settings.camera_analysis_width
        h = _even(w * bgr.shape[0] / bgr.shape[1])
        return cv2.resize(bgr, (w, h), interpolation=cv2.INTER_AREA)

    def info(self) -> dict[str, Any]:
        return {
            "model": "synthetic (mock)",
            "sensor_mode": None,
            "lores_size": [self.settings.camera_analysis_width, None],
            "modes": supported_mode_matrix(self.sensor_modes),
            "raw_sensor_modes": [{"size": list(m["size"]), "fps": m["fps"], "bit_depth": m["bit_depth"]} for m in self.sensor_modes],
        }


# --------------------------------------------------------------------------
class CameraService:
    def __init__(
        self,
        settings: Settings,
        events: EventLog,
        pose: Callable[[], tuple[float, float, float | None]] | None = None,
    ) -> None:
        self.settings = settings
        self.events = events
        self.status = DeviceStatus("camera")
        self.hub: LatestValueHub[Frame] = LatestValueHub()
        self._pose = pose
        self._backend: Any = None
        self._fps = FpsMeter()
        self._lock = threading.Lock()
        self.recording = RecordingState()
        self.snap_dir = settings.media_dir / "snapshots"
        self.video_dir = settings.media_dir / "videos"
        self._analysis_lock = threading.Lock()

    def _publish(self, frame: Frame) -> None:
        self._fps.tick(time.monotonic())
        self.status.touch()
        self.hub.publish(frame)

    def start(self) -> None:
        mode = self.settings.backend_for(self.settings.camera_backend)
        if mode == "disabled":
            self.status.backend = "disabled"
            self.status.set(DeviceState.DISABLED, "disabled by configuration")
            return
        try:
            if mode == "mock":
                self._backend = MockCameraBackend(self.settings, self._publish, self._pose)
            else:
                self._backend = Picamera2Backend(self.settings, self._publish)
            self.status.backend = self._backend.name
            self.status.synthetic = self._backend.synthetic
            self._backend.start()
            self.status.set(DeviceState.SYNTHETIC if self.status.synthetic else DeviceState.OK)
        except Exception as exc:
            self.status.backend = self.status.backend if self.status.backend != "none" else "picamera2"
            self.status.set(DeviceState.DISCONNECTED, f"camera unavailable: {exc}")
            log.warning("camera unavailable: %s", exc)
            self._backend = None

    def stop(self) -> None:
        if self._backend is not None:
            try:
                if self.recording.active:
                    self.stop_recording()
                self._backend.close()
            except Exception:
                log.exception("camera shutdown error")

    @property
    def available(self) -> bool:
        return self._backend is not None

    def _require(self) -> Any:
        if self._backend is None:
            raise CameraError(self.status.error or "camera unavailable")
        return self._backend

    # --------------------------------------------------------- operations
    def snapshot(self) -> dict[str, Any]:
        self._require()
        seq, frame = self.hub.latest()
        if frame is None:
            raise CameraError("no frame available yet")
        self.snap_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.fromtimestamp(frame.capture_ts).strftime("%Y%m%d_%H%M%S_%f")[:-3]
        name = f"snap_{stamp}_{frame.seq}{'_synthetic' if frame.synthetic else ''}.jpg"
        path = self.snap_dir / name
        path.write_bytes(frame.jpeg)
        return {
            "id": name,
            "path": str(path),
            "ts": frame.capture_ts,
            "width": frame.width,
            "height": frame.height,
            "bytes": len(frame.jpeg),
            "synthetic": frame.synthetic,
        }

    def start_recording(self) -> dict[str, Any]:
        backend = self._require()
        with self._lock:
            if self.recording.active:
                raise CameraError("recording already in progress")
            self.video_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = self.video_dir / f"rec_{stamp}{'_synthetic' if backend.synthetic else ''}.mp4"
            try:
                path = backend.start_recording(path)
            except Exception as exc:
                self.recording = RecordingState(error=str(exc))
                raise CameraError(f"could not start recording: {exc}") from exc
            self.recording = RecordingState(active=True, path=path, started_at=time.time())
        self.events.add("info", "camera", f"video recording started: {path.name}")
        return self.recording_status()

    def stop_recording(self) -> dict[str, Any]:
        backend = self._require()
        with self._lock:
            if not self.recording.active:
                raise CameraError("not recording")
            try:
                backend.stop_recording()
            finally:
                rec = self.recording
                self.recording = RecordingState(active=False, path=rec.path, started_at=rec.started_at,
                                                extra={"stopped_at": time.time()})
        self.events.add("info", "camera", f"video recording saved: {rec.path.name if rec.path else '?'}")
        return self.recording_status()

    def recording_status(self) -> dict[str, Any]:
        r = self.recording
        size = None
        if r.path and r.path.exists():
            size = r.path.stat().st_size
        end = r.extra.get("stopped_at") or time.time()
        return {
            "active": r.active,
            "file": r.path.name if r.path else None,
            "started_at": r.started_at,
            "duration_s": (end - r.started_at) if r.started_at else None,
            "bytes": size,
            "error": r.error,
        }

    def reconfigure(self, width: int, height: int, fps: float) -> dict[str, Any]:
        backend = self._require()
        if self.recording.active:
            raise CameraError("stop recording before changing resolution/frame rate")
        if choose_sensor_mode(backend.sensor_modes, (width, height), fps) is None:
            raise CameraError(f"{width}x{height} @ {fps:g} fps is not supported by any sensor mode")
        backend.reconfigure((width, height), fps)
        self.events.add("info", "camera", f"stream set to {width}x{height} @ {fps:g} fps")
        return self.camera_status()

    def analysis_frame(self):
        """BGR frame (≈640 px wide) for vision. Call from a worker thread only."""
        backend = self._require()
        with self._analysis_lock:
            return backend.analysis_frame()

    def camera_status(self) -> dict[str, Any]:
        seq, frame = self.hub.latest()
        b = self._backend
        return {
            "status": self.status.to_dict(),
            "available": b is not None,
            "width": b.size[0] if b else None,
            "height": b.size[1] if b else None,
            "target_fps": b.fps if b else None,
            "measured_fps": round(self._fps.fps(), 2),
            "frame_seq": seq,
            "last_frame_ts": frame.capture_ts if frame else None,
            "last_frame_bytes": len(frame.jpeg) if frame else None,
            "timestamp_method": frame.ts_method if frame else None,
            "recording": self.recording_status(),
            "stream": {
                "mjpeg_url": "/api/camera/stream.mjpg",
                "webrtc_enabled": bool(self.settings.webrtc_enabled and b is not None and not b.synthetic),
            },
            "info": b.info() if b else None,
            "fps_note": "Frame-rate options are derived from libcamera sensor modes; the measured FPS is the validation.",
        }

    @property
    def measured_fps(self) -> float:
        return self._fps.fps()


def list_media(directory: Path, suffixes: tuple[str, ...]) -> list[dict[str, Any]]:
    if not directory.exists():
        return []
    out = []
    for p in sorted(directory.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if p.is_file() and p.suffix.lower() in suffixes:
            st = p.stat()
            out.append({"name": p.name, "bytes": st.st_size, "modified": st.st_mtime, "synthetic": "_synthetic" in p.name})
    return out


def safe_media_path(directory: Path, name: str) -> Path:
    """Resolve ``name`` inside ``directory`` and refuse path traversal."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise CameraError("invalid file name")
    p = (directory / name).resolve()
    if p.parent != directory.resolve() or not p.is_file():
        raise CameraError("file not found")
    return p

