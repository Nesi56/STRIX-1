"""The simulated drone's camera, from Gazebo (STRIX_SIM=px4-gz).

Gazebo's Python bindings live in Gazebo's own Python (apt or conda), so frames come from a small bridge
process (``app/sim/gz_camera_bridge.py``) over a pipe. Each streamed frame carries a "SIMULATION" banner,
the same way the synthetic demo camera labels itself; YOLO analyses the clean frame.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import struct
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Callable

from app.config.settings import Settings
from app.services.base import DeviceState, StoppableThread

log = logging.getLogger(__name__)

BRIDGE = Path(__file__).resolve().parents[1] / "sim" / "gz_camera_bridge.py"
MAGIC = b"STRX"
KIND_JSON, KIND_IMAGE = 1, 2
IMAGE_HEADER = struct.Struct("<IIIId")
CHANNELS = {1: 1, 3: 3, 4: 4, 13: 3, 14: 4}


def gz_python_candidates(configured: str) -> list[str]:
    out = [configured] if configured else []
    for prefix in (os.environ.get("CONDA_PREFIX"), os.environ.get("STRIX_GZ_PREFIX")):
        if prefix:
            out.append(str(Path(prefix) / "bin" / "python3"))
    out += ["/usr/bin/python3", shutil.which("python3") or ""]
    seen: list[str] = []
    for c in out:
        if c and c not in seen:
            seen.append(c)
    return seen


def find_gz_python(configured: str = "") -> str | None:
    """First python that can import Gazebo's transport + message bindings."""
    for py in gz_python_candidates(configured):
        if not Path(py).exists():
            continue
        try:
            r = subprocess.run([py, "-c", "import gz.transport13, gz.msgs10"], capture_output=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            return py
    return None


class PacketReader:
    """Reads ``STRX | kind | len | payload`` packets; resynchronises on stray bytes."""

    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream

    def _exact(self, n: int) -> bytes | None:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.stream.read(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return bytes(buf)

    def read(self) -> tuple[int, bytes] | None:
        head = self._exact(4)
        if head is None:
            return None
        while head != MAGIC:  # resync (e.g. a library printed to stdout)
            b = self._exact(1)
            if b is None:
                return None
            head = head[1:] + b
        rest = self._exact(5)
        if rest is None:
            return None
        kind = rest[0]
        (n,) = struct.unpack("<I", rest[1:])
        if n > 64 * 1024 * 1024:
            raise ValueError("camera packet too large")
        payload = self._exact(n)
        if payload is None:
            return None
        return kind, payload


def decode_image(payload: bytes):
    """Bridge image payload → (BGR numpy array, sim time in seconds)."""
    import cv2
    import numpy as np

    w, h, step, fmt, stamp = IMAGE_HEADER.unpack_from(payload)
    ch = CHANNELS.get(fmt)
    if ch is None:
        raise ValueError(f"unsupported pixel format {fmt}")
    data = np.frombuffer(payload, np.uint8, offset=IMAGE_HEADER.size)
    if data.size < h * step or step < w * ch:
        raise ValueError("truncated image")
    img = data[: h * step].reshape(h, step)[:, : w * ch].reshape(h, w, ch)
    if fmt == 3:
        bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    elif fmt == 4:
        bgr = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
    elif fmt == 1:
        bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif fmt == 14:
        bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    else:
        bgr = img.copy()
    return bgr, stamp


def sim_banner(bgr, label: str):
    """Copy of the frame with a 'SIMULATION' banner (what the operator and recordings see)."""
    import cv2

    view = bgr.copy()
    h, w = view.shape[:2]
    fs = max(0.45, h / 900)
    bar = int(36 * fs)
    cv2.rectangle(view, (0, 0), (w, bar), (0, 0, 0), -1)
    cv2.putText(view, f"SIMULATION - GAZEBO - NOT REAL IMAGERY  {label}", (int(10 * fs), int(25 * fs)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7 * fs, (0, 200, 255), max(1, int(2 * fs)), cv2.LINE_AA)
    stamp = datetime.now().strftime("%H:%M:%S")
    cv2.putText(view, stamp, (w - int(130 * fs), int(25 * fs)), cv2.FONT_HERSHEY_SIMPLEX, 0.7 * fs,
                (230, 230, 230), max(1, int(2 * fs)), cv2.LINE_AA)
    return view


class GazeboCameraBackend:
    name = "gazebo-camera"
    synthetic = True
    reports_status = True  # the service shows our status (waiting for Gazebo, live, bridge error)

    def __init__(self, settings: Settings, publish: Callable[[Any], None],
                 on_status: Callable[..., None], command: list[str] | None = None) -> None:
        import cv2  # noqa: F401  (fail early if OpenCV is missing)

        self.settings = settings
        self._publish = publish
        self._on_status = on_status
        self._command = command  # tests inject a fake bridge
        self.model = settings.sim_gz_model_name
        self.size = (settings.camera_width, settings.camera_height)
        self.fps = min(settings.camera_fps, 30.0)
        self.sensor_modes: list[dict[str, Any]] = [{"size": self.size, "fps": self.fps, "bit_depth": 8}]
        self.topic: str | None = None
        self.hfov_deg: float | None = None
        self.sim_time_s: float | None = None
        self._proc: subprocess.Popen | None = None
        self._thread: StoppableThread | None = None
        self._last_bgr = None
        self._seq = 0
        self._writer = None
        self._writer_lock = threading.Lock()
        self._live = False
        self.last_error: str | None = None

    # ------------------------------------------------------------ process
    def _bridge_command(self) -> list[str]:
        if self._command is not None:
            return self._command
        py = find_gz_python(self.settings.sim_gz_python)
        if py is None:
            raise RuntimeError("no Python with Gazebo's bindings found: install Gazebo Harmonic "
                               "(python3-gz-transport13, python3-gz-msgs10) or set STRIX_SIM_GZ_PYTHON")
        cmd = [py, str(BRIDGE), "--model", self.model, "--max-fps", f"{self.fps:g}"]
        if self.settings.sim_camera_topic:
            cmd += ["--topic", self.settings.sim_camera_topic]
        return cmd

    def _spawn(self) -> subprocess.Popen:
        log_path = self.settings.data_dir / "logs" / "gz_camera_bridge.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        err = open(log_path, "ab")
        try:
            return subprocess.Popen(self._bridge_command(), stdout=subprocess.PIPE, stderr=err, stdin=subprocess.DEVNULL)
        finally:
            err.close()

    def start(self, size: tuple[int, int] | None = None, fps: float | None = None) -> None:
        self._on_status(DeviceState.STARTING, f"starting the Gazebo camera for {self.model}")
        self._thread = StoppableThread(self._run, "gazebo-camera")
        self._thread.start()

    def _run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self._proc = self._spawn()
            except Exception as exc:
                self.last_error = str(exc)
                self._on_status(DeviceState.DISCONNECTED, str(exc))
                stop.wait(5.0)
                continue
            self._pump(stop, self._proc)
            if stop.is_set():
                break
            code = self._proc.poll()
            self._live = False
            self._on_status(DeviceState.DISCONNECTED, self.last_error or f"camera bridge exited ({code}); restarting")
            stop.wait(3.0)

    def _pump(self, stop: threading.Event, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        reader = PacketReader(proc.stdout)
        while not stop.is_set():
            try:
                pkt = reader.read()
            except Exception as exc:
                self.last_error = f"camera bridge: {exc}"
                break
            if pkt is None:
                break
            kind, payload = pkt
            if kind == KIND_JSON:
                self._on_json(payload)
            elif kind == KIND_IMAGE:
                try:
                    self._on_image(payload)
                except Exception as exc:
                    self.last_error = f"bad frame: {exc}"

    def _on_json(self, payload: bytes) -> None:
        try:
            msg = json.loads(payload)
        except ValueError:
            return
        t = msg.get("type")
        if t == "waiting":
            self._live = False
            self._on_status(DeviceState.STARTING, msg.get("message"))
        elif t == "error":
            self.last_error = msg.get("message")
            self._on_status(DeviceState.DISCONNECTED, self.last_error)
        elif t == "status":
            self.topic = msg.get("topic") or self.topic
            h = msg.get("hfov_deg")
            if h:
                self.hfov_deg = float(h)
                self.settings.camera_hfov_deg = self.hfov_deg  # bearings and distance estimates use the real FOV

    def _on_image(self, payload: bytes) -> None:
        import cv2

        bgr, stamp = decode_image(payload)
        self.sim_time_s = stamp
        h, w = bgr.shape[:2]
        if (w, h) != self.size:
            self.size = (w, h)
            self.sensor_modes = [{"size": self.size, "fps": self.fps, "bit_depth": 8}]
        self._last_bgr = bgr
        view = sim_banner(bgr, self.model)
        with self._writer_lock:
            if self._writer is not None:
                self._writer.write(view)
        ok, buf = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, self.settings.camera_jpeg_quality])
        if not ok:
            return
        from app.services.camera_service import Frame

        self._seq += 1
        self._publish(Frame(buf.tobytes(), self._seq, time.time(), w, h, True, "simulator"))
        if not self._live:
            self._live = True
            self.last_error = None
            self._on_status(DeviceState.SYNTHETIC, None)

    def stop(self) -> None:
        if self._thread:
            self._thread.stop_event.set()
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self._thread:
            self._thread.stop()
            self._thread = None

    def close(self) -> None:
        self.stop_recording()
        self.stop()

    def reconfigure(self, size: tuple[int, int], fps: float) -> None:
        from app.services.camera_service import CameraError

        raise CameraError("the simulated camera's resolution is set by its Gazebo model")

    # ------------------------------------------------------------ recording / analysis
    def start_recording(self, path: Path) -> Path:
        import cv2

        from app.services.camera_service import CameraError

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

        from app.services.camera_service import CameraError

        bgr = self._last_bgr
        if bgr is None:
            raise CameraError("no frame from the simulator yet")
        w = self.settings.camera_analysis_width
        h = max(2, int(round(w * bgr.shape[0] / bgr.shape[1] / 2)) * 2)
        return cv2.resize(bgr, (w, h), interpolation=cv2.INTER_AREA)

    def info(self) -> dict[str, Any]:
        from app.services.camera_service import supported_mode_matrix

        return {
            "model": f"Gazebo camera ({self.model})",
            "sensor_mode": None,
            "topic": self.topic,
            "hfov_deg": self.hfov_deg,
            "sim_time_s": self.sim_time_s,
            "lores_size": [self.settings.camera_analysis_width, None],
            "modes": supported_mode_matrix(self.sensor_modes),
            "raw_sensor_modes": [{"size": list(m["size"]), "fps": m["fps"], "bit_depth": m["bit_depth"]} for m in self.sensor_modes],
        }
