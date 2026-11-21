#!/usr/bin/env python3
"""Gazebo camera → STRIX-1 bridge (simulation only).

Runs as its own process with the Python that has Gazebo's bindings (``gz.transport13``/``gz.msgs10``,
from apt or conda-forge), so their protobuf version never clashes with STRIX-1's virtualenv. It finds the
simulated drone's camera topic, and writes frames to stdout:

    b"STRX" | kind (1 byte) | length (uint32 LE) | payload
    kind 1: JSON status, e.g. {"type": "status", "topic": "...", "hfov_deg": 114.6}
    kind 2: image: <IIIId> width, height, step, format, sim_time_s  followed by the raw pixels

Formats: 3 = RGB, 4 = RGBA, 1 = mono, 13 = BGR, 14 = BGRA. Standard library only (plus Gazebo).
Usage: gz_camera_bridge.py --model x500_gimbal_1 [--topic /world/.../image] [--max-fps 15]
       gz_camera_bridge.py --list   (print the camera topics Gazebo has, then exit)
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import threading
import time

MAGIC = b"STRX"
KIND_JSON, KIND_IMAGE = 1, 2
IMAGE_HEADER = struct.Struct("<IIIId")
FORMATS = {"RGB_INT8": 3, "RGBA_INT8": 4, "L_INT8": 1, "BGR_INT8": 13, "BGRA_INT8": 14}

_out = sys.stdout.buffer
_out_lock = threading.Lock()


def write_packet(kind: int, payload: bytes) -> None:
    with _out_lock:
        _out.write(MAGIC + bytes([kind]) + struct.pack("<I", len(payload)) + payload)
        _out.flush()


def write_json(obj: dict) -> None:
    write_packet(KIND_JSON, json.dumps(obj).encode())


def pixel_format(msg) -> int:
    """Map a gz.msgs Image to our format code (by enum name when available, else by bytes per pixel)."""
    name = None
    try:
        from gz.msgs10 import image_pb2  # noqa: F401

        field = msg.DESCRIPTOR.fields_by_name["pixel_format_type"]
        name = field.enum_type.values_by_number[msg.pixel_format_type].name
    except Exception:
        pass
    if name in FORMATS:
        return FORMATS[name]
    bpp = msg.step // msg.width if msg.width else 3
    return {1: 1, 3: 3, 4: 4}.get(bpp, 3)


def find_camera_topics(topics: list[str], model: str) -> list[str]:
    """Image topics that belong to ``model`` (e.g. /world/default/model/x500_gimbal_1/link/camera_link/sensor/camera/image)."""
    out = [t for t in topics if t.endswith("/image") and f"/model/{model}/" in t]
    # Prefer a sensor called "camera" (PX4's models), then anything else.
    return sorted(out, key=lambda t: ("/sensor/camera/" not in t, t))


def hfov_from_camera_info(msg) -> float | None:
    try:
        fx = msg.intrinsics.k[0]
        return math.degrees(2 * math.atan(msg.width / (2 * fx))) if fx > 0 and msg.width else None
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", default="", help="Gazebo model name, e.g. x500_gimbal_1")
    ap.add_argument("--topic", default="", help="image topic (default: find the model's camera)")
    ap.add_argument("--max-fps", type=float, default=15.0)
    ap.add_argument("--list", action="store_true", help="print camera topics and exit")
    args = ap.parse_args()

    try:
        from gz.msgs10.image_pb2 import Image
        from gz.transport13 import Node
    except Exception as exc:  # pragma: no cover - depends on the machine
        msg = ("Gazebo's Python bindings are missing (" + str(exc) + "). Install Gazebo Harmonic with "
               "python3-gz-transport13 and python3-gz-msgs10, or set STRIX_SIM_GZ_PYTHON to a python that has them.")
        if args.list:
            print(msg, file=sys.stderr)
        else:
            write_json({"type": "error", "message": msg})
        return 3

    node = Node()
    if args.list:
        for t in sorted(t for t in node.topic_list() if t.endswith("/image")):
            print(t)
        return 0

    topic = args.topic
    waited = 0.0
    while not topic:
        cands = find_camera_topics(node.topic_list(), args.model)
        if cands:
            topic = cands[0]
            break
        if waited == 0.0 or waited % 5.0 < 0.5:
            write_json({"type": "waiting", "message": f"waiting for the camera of Gazebo model {args.model}"})
        time.sleep(0.5)
        waited += 0.5

    latest: list = [None]
    have = threading.Event()

    def on_image(msg) -> None:
        latest[0] = msg
        have.set()

    if not node.subscribe(Image, topic, on_image):
        write_json({"type": "error", "message": f"could not subscribe to {topic}"})
        return 4
    info: dict = {"type": "status", "topic": topic, "hfov_deg": None}
    try:
        from gz.msgs10.camera_info_pb2 import CameraInfo

        def on_info(msg) -> None:
            h = hfov_from_camera_info(msg)
            if h and info["hfov_deg"] is None:
                info["hfov_deg"] = round(h, 2)
                write_json(info)

        node.subscribe(CameraInfo, topic.rsplit("/", 1)[0] + "/camera_info", on_info)
    except Exception:
        pass
    write_json(info)

    period = 1.0 / max(1.0, args.max_fps)
    next_t = time.monotonic()
    while True:
        if not have.wait(5.0):
            write_json({"type": "waiting", "message": f"no frames on {topic} (is the simulation paused?)"})
            continue
        have.clear()
        msg = latest[0]
        stamp = msg.header.stamp.sec + msg.header.stamp.nsec * 1e-9 if msg.HasField("header") else 0.0
        payload = IMAGE_HEADER.pack(msg.width, msg.height, msg.step, pixel_format(msg), stamp) + msg.data
        try:
            write_packet(KIND_IMAGE, payload)
        except BrokenPipeError:
            return 0
        next_t += period
        delay = next_t - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            next_t = time.monotonic()


if __name__ == "__main__":
    sys.exit(main())
