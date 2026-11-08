"""Object memory: what is SEEN, what is REMEMBERED, and what is HIDDEN.

Every YOLO detection is turned into a *world direction* with the camera geometry:

    azimuth   = drone relative yaw (IMU) + detection bearing in the image
    elevation = camera tilt (servo) + drone pitch (IMU) + detection elevation in the image
    range     = monocular size-prior estimate (or unknown)

(image angles are first de-rotated by the IMU roll). An object keeps the same coordinates
when the camera tilts or the drone yaws, so the memory survives looking away. The frame is
drone-centred (x right, y up, z forward at yaw 0) and assumes the drone holds its position:
there is no validated position source, and relative yaw drifts slowly (no magnetometer).

Track states
------------
* ``visible``      matched in the latest frame                               → SEEN
* ``out_of_view``  the camera points elsewhere                               → REMEMBERED
* ``occluded``     the camera looks at where it was, but it isn't detected  → REMEMBERED
* ``hidden``       it vanished where a container (cup, bowl …) covers it    → HIDDEN
                   While hidden it moves with its container, so shuffled cups carry the
                   remembered object along (object permanence + containment).

Re-identification: a new detection near a remembered track of the same class revives it. If
the only remembered candidate is farther away, the track is *relocated* (memory corrected)
and an event records how wrong the memory was.
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_CONTAINERS = ("cup", "bowl", "vase", "wine glass")


def wrap180(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def ang_dist(az1: float, el1: float, az2: float, el2: float) -> float:
    """Approximate angular distance in degrees between two directions."""
    daz = wrap180(az1 - az2) * math.cos(math.radians((el1 + el2) / 2))
    return math.hypot(daz, el1 - el2)


def deroll(bearing: float, elevation: float, roll_deg: float) -> tuple[float, float]:
    """Undo camera roll (+ = right side down) on image angles. Rolling right makes the world appear rotated
    counter-clockwise, so a level point to the right shows up higher in the image."""
    r = math.radians(roll_deg)
    c, s = math.cos(r), math.sin(r)
    return bearing * c + elevation * s, -bearing * s + elevation * c


def to_world(d: dict[str, Any], cam_az: float, cam_el: float, roll: float, half_h: float, half_v: float) -> dict[str, Any]:
    """Convert a detector result (image bearing/elevation, normalized bbox) into a memory-frame detection."""
    b, e = deroll(d["bearing_deg"], d["elevation_deg"], roll)
    x, y, w, h = d["bbox"]
    m = 0.004  # a box touching the frame edge is cut off: its centre/size along that axis are unreliable
    return {
        "label": d["label"], "confidence": d["confidence"],
        "az": wrap180(cam_az + b), "el": cam_el + e,
        "w_deg": w * 2 * half_h, "h_deg": h * 2 * half_v,
        "range_m": d.get("distance_m"), "range_kind": d.get("distance_kind", "none"),
        "cut_h": x <= m or x + w >= 1 - m, "cut_v": y <= m or y + h >= 1 - m,
    }


@dataclass
class Track:
    id: int
    label: str
    az: float
    el: float
    w_deg: float
    h_deg: float
    range_m: float | None
    range_kind: str
    first_seen: float
    last_seen: float
    confidence: float
    hits: int = 1
    missed: int = 0
    state: str = "visible"
    contained_by: int | None = None
    hidden_since: float | None = None
    offset: tuple[float, float] = (0.0, 0.0)  # (Δaz, Δrange) from the container while hidden
    anchor_az: float = 0.0  # container azimuth at the last "moved" event
    drop_m: float | None = None  # height of the object below the camera when hidden (keeps it on the surface)
    cont_drop_m: float | None = None  # same for its container (to tell "lifted" from "slid farther away")
    cont_el0: float = 0.0  # container elevation when hidden (fallback when ranges are unknown)
    relocated_by_deg: float | None = None
    vaz: float = 0.0  # angular velocity estimate (°/s) for motion prediction between detector frames
    vel: float = 0.0

    def predict(self, now: float) -> tuple[float, float]:
        """Constant-velocity prediction of the direction at ``now`` (only for tracks seen < 1 s ago)."""
        dt = now - self.last_seen
        if self.state != "visible" or dt > 1.0:
            return self.az, self.el
        return wrap180(self.az + self.vaz * dt), self.el + self.vel * dt

    @property
    def confirmed(self) -> bool:
        return self.hits >= 2

    def xyz(self) -> tuple[float, float, float]:
        r = self.range_m if self.range_m is not None else 2.0
        a, e = math.radians(self.az), math.radians(self.el)
        return r * math.cos(e) * math.sin(a), r * math.sin(e), r * math.cos(e) * math.cos(a)


@dataclass
class ObjectMemory:
    """Pure tracking logic (no I/O), unit-tested directly."""

    containers: tuple[str, ...] = DEFAULT_CONTAINERS
    ttl_s: float = 600.0
    miss_frames: int = 3
    gate_deg: float = 9.0
    reid_deg: float = 60.0
    tracks: dict[int, Track] = field(default_factory=dict)
    events: deque = field(default_factory=lambda: deque(maxlen=80))
    seq: int = 0
    _next_id: int = 1

    def name(self, t: Track | None) -> str:
        return "?" if t is None else f"{t.label} #{t.id}"

    def _event(self, now: float, kind: str, text: str, track: Track | None = None) -> None:
        self.events.append({"ts": now, "kind": kind, "text": text, "track": track.id if track else None})

    @staticmethod
    def _in_fov(t: Track, cam_az: float, cam_el: float, half_h: float, half_v: float) -> bool:
        return abs(wrap180(t.az - cam_az)) < half_h * 0.92 and abs(t.el - cam_el) < half_v * 0.92

    def update(self, dets: list[dict[str, Any]], cam_az: float, cam_el: float, half_h: float, half_v: float,
               now: float) -> None:
        """Integrate one detector frame. Each detection: label, confidence, az, el, w_deg, h_deg, range_m, range_kind."""
        self.seq += 1
        matched: set[int] = set()
        used: set[int] = set()
        # 1) gated nearest-neighbour association on motion-predicted directions, per class, closest pairs first
        pred = {t.id: t.predict(now) for t in self.tracks.values()}
        gate = {t.id: self.gate_deg + 0.5 * math.hypot(t.vaz, t.vel) * min(1.0, now - t.last_seen)
                for t in self.tracks.values()}
        pairs = sorted(
            (ang_dist(d["az"], d["el"], *pred[t.id]), di, t.id)
            for di, d in enumerate(dets) for t in self.tracks.values() if t.label == d["label"]
        )
        for dist, di, tid in pairs:
            if dist > gate[tid] or di in used or tid in matched:
                continue
            self._apply(self.tracks[tid], dets[di], now)
            used.add(di)
            matched.add(tid)
        # 2) unmatched detections: re-identify a remembered track, or start a new one
        for di, d in enumerate(dets):
            if di in used:
                continue
            cands = sorted((t for t in self.tracks.values()
                            if t.label == d["label"] and t.id not in matched and t.state != "visible" and t.confirmed),
                           key=lambda t: ang_dist(d["az"], d["el"], t.az, t.el))
            if cands and ang_dist(d["az"], d["el"], cands[0].az, cands[0].el) <= self.reid_deg:
                t = cands[0]
                off = ang_dist(d["az"], d["el"], t.az, t.el)
                if off > 2 * self.gate_deg:
                    t.relocated_by_deg = round(off, 1)
                    where = (f" (I thought it was under {self.name(self.tracks.get(t.contained_by or -1))})"
                             if t.contained_by else "")
                    self._event(now, "relocated",
                                f"{self.name(t)} turned up {off:.0f}° from where I remembered it{where} — memory updated", t)
                self._apply(t, d, now, snap=True)
                matched.add(t.id)
                continue
            t = Track(self._next_id, d["label"], d["az"], d["el"], d.get("w_deg", 2.0), d.get("h_deg", 2.0),
                      d.get("range_m"), d.get("range_kind", "none"), now, now, d.get("confidence", 0.0))
            self.tracks[t.id] = t
            self._next_id += 1
            matched.add(t.id)
        # 3) unmatched tracks: out of view / occluded / hidden
        for t in list(self.tracks.values()):
            if t.id in matched:
                continue
            if not t.confirmed:
                if now - t.last_seen > 1.0:
                    del self.tracks[t.id]
                continue
            if t.state == "hidden":
                continue  # stays inside its container until it is seen again
            if not self._in_fov(t, cam_az, cam_el, half_h, half_v):
                if t.state == "visible":
                    t.state = "out_of_view"
                t.missed = 0
                continue
            t.missed += 1
            if t.missed < self.miss_frames:
                continue
            cont = self._covering_container(t)
            if cont is not None:
                t.state, t.contained_by, t.hidden_since = "hidden", cont.id, now
                t.offset = (wrap180(t.az - cont.az), (t.range_m or 0.0) - (cont.range_m or 0.0))
                t.anchor_az, t.cont_el0, t.missed = cont.az, cont.el, 0
                t.drop_m = -t.range_m * math.sin(math.radians(t.el)) if t.range_m else None
                t.cont_drop_m = -cont.range_m * math.sin(math.radians(cont.el)) if cont.range_m else None
                self._event(now, "hidden", f"{self.name(t)} went under {self.name(cont)}", t)
            elif t.state != "occluded":
                t.state = "occluded"
                self._event(now, "occluded", f"lost sight of {self.name(t)}", t)
        # 4) hidden objects travel with their container (azimuth and range; a lifted cup doesn't lift them)
        for t in self.tracks.values():
            if t.state != "hidden":
                continue
            c = self.tracks.get(t.contained_by or -1)
            if c is None:
                t.state, t.contained_by = "occluded", None
                continue
            if c.id in matched:
                t.az = wrap180(c.az + t.offset[0])
                if c.range_m is not None and t.range_m is not None:
                    t.range_m = max(0.05, c.range_m + t.offset[1])
                    if t.drop_m is not None:  # stays on the surface: elevation follows the new range, not a lift
                        t.el = -math.degrees(math.asin(max(-1.0, min(1.0, t.drop_m / t.range_m))))
                if abs(wrap180(c.az - t.anchor_az)) > 8.0:
                    t.anchor_az = c.az
                    self._event(now, "carried", f"{self.name(c)} moved — {self.name(t)} is still inside", t)
                # how far the container rose above where it would sit at its current range
                if t.cont_drop_m is not None and c.range_m:
                    ground_el = -math.degrees(math.asin(max(-1.0, min(1.0, t.cont_drop_m / c.range_m))))
                    lift = c.el - ground_el
                else:
                    lift = c.el - t.cont_el0
                # the container was lifted, we are looking, and the object still isn't there → memory was wrong
                if lift > max(2.5, c.h_deg * 0.8) and self._in_fov(t, cam_az, cam_el, half_h, half_v):
                    t.missed += 1
                    if t.missed >= self.miss_frames + 2:
                        self._event(now, "not_there", f"{self.name(c)} was lifted but {self.name(t)} wasn't under it", t)
                        t.state, t.contained_by, t.hidden_since = "occluded", None, None
        # 5) forget old memories
        for t in list(self.tracks.values()):
            if now - t.last_seen > self.ttl_s:
                self._event(now, "forgot", f"forgot {self.name(t)} (not seen for {self.ttl_s / 60:.0f} min)", t)
                del self.tracks[t.id]
                for o in self.tracks.values():
                    if o.contained_by == t.id:
                        o.contained_by, o.state = None, "occluded"

    def _apply(self, t: Track, d: dict[str, Any], now: float, snap: bool = False) -> None:
        was, gone_s = t.state, now - t.last_seen
        a = 1.0 if snap else 0.6  # smoothing
        if was == "visible" and not snap and 0.0 < gone_s <= 1.0:
            k = 0.5
            t.vaz += k * (wrap180(d["az"] - t.az) / gone_s - t.vaz)
            t.vel += k * ((d["el"] - t.el) / gone_s - t.vel)
            a = 0.8 if math.hypot(t.vaz, t.vel) > 3.0 else a  # follow moving objects more tightly
        else:
            t.vaz = t.vel = 0.0
        # cut-off boxes: keep the previous estimate along the cut axis (and the range, which uses the height)
        ah = 0.15 * a if d.get("cut_h") else a
        av = 0.15 * a if d.get("cut_v") else a
        t.az = wrap180(t.az + ah * wrap180(d["az"] - t.az))
        t.el += av * (d["el"] - t.el)
        if not d.get("cut_h"):
            t.w_deg += a * (d.get("w_deg", t.w_deg) - t.w_deg)
        if not d.get("cut_v"):
            t.h_deg += a * (d.get("h_deg", t.h_deg) - t.h_deg)
        r = d.get("range_m")
        if r is not None and (not d.get("cut_v") or t.range_m is None):
            t.range_m = r if (t.range_m is None or snap) else t.range_m + a * (r - t.range_m)
            t.range_kind = d.get("range_kind", t.range_kind)
        t.confidence = d.get("confidence", t.confidence)
        t.last_seen = now
        t.hits += 1
        t.missed = 0
        if was == "hidden":
            right = not snap
            self._event(now, "revealed", f"{self.name(t)} found under "
                        f"{self.name(self.tracks.get(t.contained_by or -1))} after {gone_s:.0f} s"
                        f"{' — memory was right' if right else ''}", t)
        elif was in ("occluded", "out_of_view") and gone_s > 2.0 and t.confirmed:
            self._event(now, "reseen", f"{self.name(t)} is back in view after {gone_s:.0f} s", t)
        t.state, t.contained_by, t.hidden_since = "visible", None, None

    def _covering_container(self, t: Track) -> Track | None:
        """A visible container whose angular extent covers where ``t`` was last seen."""
        if t.label in self.containers:
            return None
        best, best_d = None, 1e9
        for c in self.tracks.values():
            if c.label not in self.containers or c.state != "visible" or c.id == t.id:
                continue
            if abs(wrap180(t.az - c.az)) <= c.w_deg * 0.65 + 1.0 and abs(t.el - c.el) <= c.h_deg * 0.65 + 1.5:
                d = ang_dist(c.az, c.el, t.az, t.el)
                if d < best_d:
                    best, best_d = c, d
        return best

    def clear(self) -> None:
        self.tracks.clear()
        self.events.clear()

    def find(self, query: str) -> Track | None:
        """A confirmed track by ``#id`` or class name (the most recently seen one of that class)."""
        q = query.strip().lower()
        if q.startswith("#") and q[1:].isdigit():
            t = self.tracks.get(int(q[1:]))
            return t if t and t.confirmed else None
        cands = [t for t in self.tracks.values() if t.label == q and t.confirmed]
        return max(cands, key=lambda t: t.last_seen) if cands else None

    def snapshot(self, now: float, cam_az: float, cam_el: float, half_h: float, half_v: float) -> list[dict[str, Any]]:
        out = []
        th, tv = math.tan(math.radians(half_h)), math.tan(math.radians(half_v))
        for t in sorted(self.tracks.values(), key=lambda t: t.id):
            if not t.confirmed:
                continue
            x, y, z = t.xyz()
            daz, del_ = wrap180(t.az - cam_az), t.el - cam_el
            proj = None
            if t.state != "visible" and abs(daz) < half_h * 0.98 and abs(del_) < half_v * 0.98:
                cx = 0.5 + math.tan(math.radians(daz)) / (2 * th)
                cy = 0.5 - math.tan(math.radians(del_)) / (2 * tv)
                bw, bh = t.w_deg / (2 * half_h), t.h_deg / (2 * half_v)
                proj = [round(cx - bw / 2, 4), round(cy - bh / 2, 4), round(bw, 4), round(bh, 4)]
            c = self.tracks.get(t.contained_by or -1)
            out.append({
                "id": t.id, "label": t.label, "name": self.name(t), "state": t.state,
                "memory": "seen" if t.state == "visible" else "hidden" if t.state == "hidden" else "remembered",
                "container": t.label in self.containers,
                "contained_by": t.contained_by, "contained_by_name": self.name(c) if c else None,
                "contains": [o.id for o in self.tracks.values() if o.contained_by == t.id and o.state == "hidden"],
                "az": round(t.az, 1), "el": round(t.el, 1), "w_deg": round(t.w_deg, 2), "h_deg": round(t.h_deg, 2),
                "range_m": round(t.range_m, 2) if t.range_m is not None else None, "range_kind": t.range_kind,
                "xyz": [round(x, 3), round(y, 3), round(z, 3)],
                "first_seen": t.first_seen, "last_seen": t.last_seen,
                "out_of_sight_s": 0.0 if t.state == "visible" else round(now - t.last_seen, 1),
                "hidden_for_s": round(now - t.hidden_since, 1) if t.hidden_since else None,
                "confidence": round(t.confidence, 2), "projected_bbox": proj,
                "relocated_by_deg": t.relocated_by_deg,
            })
        return out


class MemoryService:
    """Feeds every new detector frame into :class:`ObjectMemory`, using the IMU and servo for the camera pose."""

    def __init__(self, c: Any) -> None:
        self.c = c
        s = c.settings
        containers = tuple(x.strip() for x in s.memory_containers.split(",") if x.strip())
        self.memory = ObjectMemory(containers=containers, ttl_s=s.memory_ttl_s, miss_frames=s.memory_miss_frames)
        self._lock = threading.Lock()
        self._task: asyncio.Task | None = None
        self._last_det_seq = -1
        self._cam = (0.0, 0.0, 51.0, 35.0)
        self.updated_at: float | None = None

    def camera_pose(self, frame_w: int | None = None, frame_h: int | None = None,
                    pose: dict[str, float] | None = None) -> tuple[float, float, float, float, float]:
        """(azimuth, elevation, roll, half HFOV, half VFOV) of the camera axis in the memory frame.
        ``pose`` is the IMU/servo pose sampled when the frame was grabbed (else: now)."""
        p = pose or self.c.camera_pose_now()
        yaw, pitch, roll, tilt = p["yaw"], p["pitch"], p["roll"], p["tilt"]
        half_h = self.c.settings.camera_hfov_deg / 2
        if not frame_w or not frame_h:
            cam = self.c.camera.camera_status()
            frame_w, frame_h = cam.get("width") or 16, cam.get("height") or 9
        half_v = math.degrees(math.atan(math.tan(math.radians(half_h)) * frame_h / frame_w))
        return wrap180(yaw), tilt + pitch, roll, half_h, half_v

    def tracked_labels(self) -> set[str] | None:
        prompts = set(self.c.detector.prompts)
        return (prompts | set(self.memory.containers)) if prompts else None

    def step(self) -> bool:
        res = self.c.detector.latest(prompted_only=False)
        if not res or res["seq"] == self._last_det_seq:
            return False
        self._last_det_seq = res["seq"]
        cam_az, cam_el, roll, half_h, half_v = self.camera_pose(res.get("frame_w"), res.get("frame_h"), res.get("pose"))
        keep = self.tracked_labels()
        dets = [to_world(d, cam_az, cam_el, roll, half_h, half_v) for d in res["detections"]
                if keep is None or d["label"] in keep]
        with self._lock:
            self.memory.update(dets, cam_az, cam_el, half_h, half_v, time.time())
            self._cam = (cam_az, cam_el, half_h, half_v)
        self.updated_at = time.time()
        return True

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="object-memory")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _run(self) -> None:
        while True:
            try:
                self.step()
            except Exception:
                log.exception("object memory update failed")
            await asyncio.sleep(0.05)

    def clear(self) -> None:
        with self._lock:
            self.memory.clear()
        self.c.events.add("info", "memory", "object memory cleared")

    @property
    def seq(self) -> int:
        return self.memory.seq

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            cam_az, cam_el, half_h, half_v = self._cam
            objs = self.memory.snapshot(now, cam_az, cam_el, half_h, half_v)
            events = list(self.memory.events)[-15:]
            seq = self.memory.seq
        counts = {k: sum(1 for o in objs if o["memory"] == k) for k in ("seen", "remembered", "hidden")}
        return {
            "seq": seq, "ts": now, "objects": objs, "events": events, "counts": counts,
            "camera": {"az": round(cam_az, 1), "el": round(cam_el, 1), "half_h": round(half_h, 1), "half_v": round(half_v, 1)},
            "frame": "drone-centred: x right, y up, z forward at yaw 0. Assumes the drone holds position; yaw is relative.",
            "scene": self.c.camera.scene(),
        }

    def summary_lines(self, limit: int = 8) -> list[str]:
        """Short text lines for the agent's observation."""
        lines = []
        for o in self.snapshot()["objects"][:limit]:
            rng = f"~{o['range_m']:.1f} m" if o["range_m"] is not None else "range unknown"
            where = f"az {o['az']:+.0f}°, el {o['el']:+.0f}°, {rng}"
            if o["memory"] == "seen":
                lines.append(f"{o['name']}: in view now ({where})")
            elif o["memory"] == "hidden":
                lines.append(f"{o['name']}: hidden under {o['contained_by_name']} for {o['hidden_for_s'] or 0:.0f} s ({where})")
            else:
                why = "the camera is pointed elsewhere" if o["state"] == "out_of_view" else "not detected where expected"
                lines.append(f"{o['name']}: remembered, out of sight for {o['out_of_sight_s']:.0f} s, {why} ({where})")
        return lines

    def resolve(self, query: str) -> dict[str, Any] | None:
        with self._lock:
            t = self.memory.find(query)
            if t is None:
                return None
            c = self.memory.tracks.get(t.contained_by or -1) if t.contained_by else None
            return {"id": t.id, "name": self.memory.name(t), "label": t.label, "state": t.state, "az": t.az, "el": t.el,
                    "range_m": t.range_m, "out_of_sight_s": 0.0 if t.state == "visible" else time.time() - t.last_seen,
                    "contained_by": self.memory.name(c) if c else None}

    def aim_at(self, query: str) -> dict[str, Any]:
        """Point the camera (servo tilt) at a remembered target and return pilot guidance.

        Only the camera servo moves; yaw and distance are advice for the pilot."""
        info = self.resolve(query)
        if info is None:
            raise KeyError(f"nothing called '{query}' in memory")
        cam_az, _, _, _, _ = self.camera_pose()
        pitch = float(((self.c.imu.latest() or {}).get("orientation") or {}).get("pitch", 0.0))
        tilt = info["el"] - pitch
        st = self.c.servo.tilt_to(tilt)  # raises ServoError if control is disabled / unavailable
        reached = (st["target_angle"] - self.c.servo.center_angle) * self.c.servo.up_sign
        yaw_delta = wrap180(info["az"] - cam_az)
        guidance = []
        if abs(yaw_delta) > 3:
            guidance.append(f"Turn the drone {'right' if yaw_delta > 0 else 'left'} about {abs(yaw_delta):.0f}° to face it.")
        else:
            guidance.append("The drone is already facing it.")
        if info["range_m"] is not None:
            guidance.append(f"It's about {info['range_m']:.1f} m away.")
        if st.get("clamped"):
            guidance.append(f"The camera can't tilt that far (stopped at {reached:+.0f}°, it's at {tilt:+.0f}°).")
        return {"target": info["name"], "id": info["id"], "state": info["state"], "contained_by": info["contained_by"],
                "out_of_sight_s": round(info["out_of_sight_s"], 1), "tilt_deg": round(reached, 1),
                "requested_tilt_deg": round(tilt, 1), "clamped": bool(st.get("clamped")),
                "yaw_delta_deg": round(yaw_delta, 1), "guidance": guidance}
