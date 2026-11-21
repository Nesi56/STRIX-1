"""Simulation mode (STRIX_SIM=px4-gz): the Gazebo camera bridge protocol, and the whole backend wired to a
fake PX4 over real MAVLink. Gazebo itself is not needed: a fake bridge script stands in for it."""

from __future__ import annotations

import importlib.util
import io
import json
import struct
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.base import DeviceState
from app.services.sim_camera import BRIDGE, IMAGE_HEADER, MAGIC, GazeboCameraBackend, PacketReader, decode_image
from tests.conftest import TOKEN, make_settings, wait_for
from tests.fake_px4 import FakePX4, free_udp_port


def _load_bridge():
    spec = importlib.util.spec_from_file_location("gz_camera_bridge", BRIDGE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # stdlib only at import time: gz is imported inside main()
    return mod


def packet(kind: int, payload: bytes) -> bytes:
    return MAGIC + bytes([kind]) + struct.pack("<I", len(payload)) + payload


def image_payload(w: int, h: int, fmt: int, pixels: np.ndarray, pad: int = 0) -> bytes:
    ch = pixels.shape[2] if pixels.ndim == 3 else 1
    step = w * ch + pad
    rows = pixels.reshape(h, w * ch)
    data = np.zeros((h, step), np.uint8)
    data[:, : w * ch] = rows
    return IMAGE_HEADER.pack(w, h, step, fmt, 12.5) + data.tobytes()


def test_packet_reader_resyncs_and_decodes():
    stream = io.BytesIO(b"gz log noise\n" + packet(1, b'{"type": "status"}') + packet(2, b"xy"))
    r = PacketReader(stream)
    assert r.read() == (1, b'{"type": "status"}')
    assert r.read() == (2, b"xy")
    assert r.read() is None


@pytest.mark.parametrize("fmt,ch,expect", [(3, 3, (30, 20, 10)), (4, 4, (30, 20, 10)), (13, 3, (10, 20, 30)), (1, 1, (10, 10, 10))])
def test_decode_image_formats(fmt, ch, expect):
    px = np.zeros((4, 6, ch), np.uint8)
    px[..., 0] = 10
    if ch >= 3:
        px[..., 1], px[..., 2] = 20, 30
    bgr, stamp = decode_image(image_payload(6, 4, fmt, px, pad=2))  # padded rows (step > width × channels)
    assert bgr.shape == (4, 6, 3) and stamp == 12.5
    assert tuple(int(v) for v in bgr[1, 2]) == expect


def test_bridge_helpers():
    b = _load_bridge()
    topics = ["/clock", "/world/default/model/x500_gimbal_2/link/camera_link/sensor/camera/image",
              "/world/default/model/x500_gimbal_1/link/camera_link/sensor/camera/image",
              "/world/default/model/x500_gimbal_1/link/camera_link/sensor/camera/camera_info"]
    assert b.find_camera_topics(topics, "x500_gimbal_1") == [topics[2]]
    assert b.find_camera_topics(topics, "x500_gimbal_3") == []

    class Info:
        width = 1280

        class intrinsics:  # noqa: N801 - mimics the protobuf field
            k = [1280 / (2 * np.tan(1.0)), 0, 640, 0, 0, 0, 0, 0, 1]

    assert b.hfov_from_camera_info(Info) == pytest.approx(np.degrees(2.0), abs=0.01)  # x500_gimbal: 2.0 rad


FAKE_BRIDGE = r'''
import json, struct, sys, time
out = sys.stdout.buffer
def send(kind, payload):
    out.write(b"STRX" + bytes([kind]) + struct.pack("<I", len(payload)) + payload); out.flush()
send(1, json.dumps({"type": "waiting", "message": "waiting for the camera of Gazebo model x500_gimbal_1"}).encode())
send(1, json.dumps({"type": "status", "topic": "/world/default/model/x500_gimbal_1/link/camera_link/sensor/camera/image", "hfov_deg": 114.59}).encode())
w, h = 320, 180
row = bytes([200, 120, 40]) * w
for i in range(int(sys.argv[1]) if len(sys.argv) > 1 else 40):
    send(2, struct.pack("<IIIId", w, h, w * 3, 3, i * 0.1) + row * h)
    time.sleep(0.03)
'''


def test_gazebo_backend_streams_frames_from_a_bridge(tmp_path):
    script = tmp_path / "fake_bridge.py"
    script.write_text(FAKE_BRIDGE)
    s = make_settings(tmp_path, sim="px4-gz", camera_fps=15)
    frames, states = [], []
    be = GazeboCameraBackend(s, frames.append, lambda st, err=None: states.append((st, err)),
                             command=[sys.executable, str(script), "60"])
    be.start()
    try:
        assert wait_for(lambda: len(frames) >= 5, timeout=10)
        assert states[0][0] == DeviceState.STARTING and (DeviceState.SYNTHETIC, None) in states
        f = frames[-1]
        assert (f.width, f.height, f.synthetic, f.ts_method) == (320, 180, True, "simulator")
        assert s.camera_hfov_deg == pytest.approx(114.59) and be.topic.endswith("/camera/image")
        a = be.analysis_frame()
        assert a.shape[1] == s.camera_analysis_width and tuple(int(v) for v in a[a.shape[0] - 1, 5]) == (40, 120, 200)
        assert be.info()["hfov_deg"] == pytest.approx(114.59)
        # The bridge exits after its frames: the backend reports it and restarts it.
        assert wait_for(lambda: any(st == DeviceState.DISCONNECTED for st, _ in states), timeout=10)
    finally:
        be.close()


def test_backend_runs_against_fake_px4(tmp_path):
    """The full backend in simulation mode: IMU, GPS, battery and gimbal all come from (fake) PX4."""
    port = free_udp_port()
    s = make_settings(tmp_path, mock=False, sim="px4-gz", sim_instance=1, mavlink_url=f"udpin:127.0.0.1:{port}",
                      sim_gz_python="/nonexistent/python")  # no Gazebo here: the camera reports why
    fake = FakePX4(port).start()
    try:
        with TestClient(create_app(s)) as c:
            c.headers["Authorization"] = f"Bearer {TOKEN}"
            health = c.get("/api/health").json()
            assert health["sim"]["kind"] == "px4-gz" and health["sim"]["model"] == "x500_gimbal_1" and health["demo_mode"]
            assert wait_for(lambda: c.get("/api/flight").json().get("connected"), timeout=10)
            fl = c.get("/api/flight").json()
            assert fl["configured"] and fl["autopilot"] == "PX4" and fl["position"]["available"]
            # The vehicle IMU drives STRIX-1's own orientation filter (level → roll/pitch ≈ 0).
            assert wait_for(lambda: (c.get("/api/imu").json().get("latest") or {}).get("seq", 0) > 20, timeout=10)
            imu = c.get("/api/imu").json()
            assert imu["status"]["backend"] == "mavlink-imu" and imu["status"]["synthetic"]
            assert abs(imu["latest"]["orientation"]["roll"]) < 1 and abs(imu["latest"]["orientation"]["pitch"]) < 1
            # Position and battery reach the mapping and system views, labelled simulated.
            m = c.get("/api/mapping").json()
            assert m["position"]["available"] and m["position"]["synthetic"] and "simulated" in m["position"]["source"]
            gps = next(x for x in m["sources"] if x["id"] == "gps")
            assert gps["installed"] and gps["validated"]
            assert wait_for(lambda: (c.get("/api/system").json().get("power") or {}).get("battery", {}).get("available"),
                            timeout=10)
            bat = c.get("/api/system").json()["power"]["battery"]
            assert bat["percent"] == 77 and bat["synthetic"]
            # The camera "servo" is the PX4 gimbal: enable control and tilt down 20°.
            sv = c.get("/api/servo").json()
            assert sv["status"]["backend"] == "mavlink-gimbal" and sv["stabilized"] and sv["gpio"] is None
            assert c.post("/api/servo/arm").status_code == 200
            assert c.post("/api/servo/nudge", json={"up_deg": -20}).status_code == 200
            assert wait_for(lambda: fake.commands(1000) and abs(fake.commands(1000)[-1].param1 + 20) < 0.5, timeout=10)
            # No camera without Gazebo, and it says why; YOLO is not faked with demo boxes.
            cam = c.get("/api/camera/status").json()
            assert cam["status"]["backend"] == "gazebo-camera" and cam["status"]["state"] == "disconnected"
            assert "Gazebo" in (cam["status"]["error"] or "")
            assert c.get("/api/detector").json()["engine"] != "synthetic"
            assert c.get("/api/demo/scene").json()["scene"] is None
    finally:
        fake.stop()
    sent = {m.command for m in fake.received if m.get_type() == "COMMAND_LONG"}
    assert sent <= {511, 1000, 1001}
