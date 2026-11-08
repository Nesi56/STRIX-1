"""Grounding for the Qwen agent: observations, suggestions and prompts.

The model only sees compact, timestamped data built here from real service state:
IMU attitude, DHT11 environment, camera-servo state and the detector's objects with
*estimated* range and bearing. Movement suggestions are computed deterministically
(geometry, not model guesses) and are advice for the human pilot — STRIX-1 never
commands the aircraft. The only actuator the agent may use is the camera tilt servo.
"""

from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Any

SKILLS = ["look", "tilt", "center", "scan", "find", "track", "wait"]


def _f(v: Any, nd: int = 1, unit: str = "") -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    return f"{v:.{nd}f}{unit}"


def _age(ts: float | None, now: float) -> str:
    return "n/a" if ts is None else f"{max(0.0, now - ts):.0f}s ago"


def observe(c: Any) -> dict[str, Any]:
    """Collect a compact observation from the service container ``c``."""
    now = time.time()
    imu = c.imu.latest()
    env = c.dht.latest()
    det = c.detector.latest(prompted_only=False)
    sv = c.servo
    sysm = c.system.latest() or {}
    dets = []
    if det and now - det["ts"] < 3.0:
        dets = sorted(det["detections"], key=lambda d: -d["confidence"])[:8]
    synthetic = [n for n, s in (("imu", c.imu.status), ("dht11", c.dht.status), ("camera", c.camera.status),
                                ("detector", c.detector.status)) if s.synthetic]
    return {
        "ts": now,
        "synthetic": synthetic,
        "imu": None if imu is None else {
            "ts": imu["ts"], "roll": imu["orientation"]["roll"], "pitch": imu["orientation"]["pitch"],
            "yaw_rel": imu["orientation"]["yaw_relative"], "accel_g": imu["orientation"]["accel_norm_g"],
            "gyro": imu["gyro"], "temp_c": imu.get("temp_c"),
        },
        "imu_status": c.imu.status.state.value,
        "imu_health": [h for h in c.imu.health() if h["severity"] != "info"],
        "env": env,
        "env_status": c.dht.status.state.value,
        "servo": {
            "available": sv.state()["available"], "armed": sv.armed, "angle": sv.current, "tilt_deg": sv.tilt_deg(),
            "min_tilt": (sv.min_angle - sv.center_angle) * sv.up_sign, "max_tilt": (sv.max_angle - sv.center_angle) * sv.up_sign,
        },
        "detections": dets,
        "detector": c.detector.engine_kind,
        "prompts": list(c.detector.prompts),
        "system": {"cpu": sysm.get("cpu_percent"), "cpu_temp": sysm.get("cpu_temp_c"),
                   "warnings": [w["message"] for w in sysm.get("warnings", [])]},
    }


def describe_detection(d: dict[str, Any]) -> str:
    side = "center" if abs(d["bearing_deg"]) < 5 else (f"{abs(d['bearing_deg']):.0f}° right" if d["bearing_deg"] > 0 else f"{abs(d['bearing_deg']):.0f}° left")
    vert = "" if abs(d["elevation_deg"]) < 5 else (f", {abs(d['elevation_deg']):.0f}° above center" if d["elevation_deg"] > 0 else f", {abs(d['elevation_deg']):.0f}° below center")
    if d.get("distance_m") is None:
        rng = "range unknown"
    elif d.get("distance_kind") == "upper_bound":
        rng = f"≤{d['distance_m']:.1f} m (partly out of frame)"
    else:
        rng = f"~{d['distance_m']:.1f} m (est.)"
    return f"{d['label']} {d['confidence']:.2f}, {rng}, {side}{vert}"


def observation_text(o: dict[str, Any]) -> str:
    now = o["ts"]
    L = [f"OBSERVATION {datetime.fromtimestamp(now).strftime('%H:%M:%S')}"]
    if o["synthetic"]:
        L.append(f"DATA: SYNTHETIC DEMO DATA ({', '.join(o['synthetic'])}) — not real measurements")
    i = o["imu"]
    if i:
        L.append(f"IMU [MEASURED {_age(i['ts'], now)}]: roll {i['roll']:+.1f}°, pitch {i['pitch']:+.1f}°, relative yaw "
                 f"{i['yaw_rel']:+.1f}° (drifts, no compass), |a| {i['accel_g']:.2f} g, gyro "
                 f"{i['gyro']['x']:+.1f}/{i['gyro']['y']:+.1f}/{i['gyro']['z']:+.1f} °/s")
    else:
        L.append(f"IMU: UNAVAILABLE ({o['imu_status']})")
    for h in o["imu_health"]:
        L.append(f"IMU HEALTH {h['severity'].upper()}: {h['message']}")
    e = o["env"]
    if e:
        L.append(f"DHT11 [MEASURED {_age(e['ts'], now)}]: {e['temperature_c']:.1f} °C, {e['humidity_pct']:.0f} %RH (±2 °C, ±5 %)")
    else:
        L.append(f"DHT11: UNAVAILABLE ({o['env_status']})")
    s = o["servo"]
    if not s["available"]:
        L.append("CAMERA SERVO: unavailable")
    else:
        L.append(f"CAMERA SERVO: control {'ENABLED' if s['armed'] else 'DISABLED by operator'}, tilt {_f(s['tilt_deg'], 0, '°')} "
                 f"(+ up), range {s['min_tilt']:+.0f}°..{s['max_tilt']:+.0f}°")
    if o["detections"]:
        L.append(f"OBJECTS [{o['detector']} detector]:")
        for d in o["detections"]:
            L.append(f"  - {describe_detection(d)}")
    else:
        L.append(f"OBJECTS [{o['detector']} detector]: none detected")
    for w in o["system"]["warnings"]:
        L.append(f"SYSTEM WARNING: {w}")
    L.append("UNAVAILABLE: GPS/position, altitude, ground speed, compass heading, battery level.")
    return "\n".join(L)


def suggestions(o: dict[str, Any], target: str | None, standoff_m: float) -> list[str]:
    """Deterministic advice for the human pilot (never executed by STRIX-1)."""
    out: list[str] = []
    dets = o["detections"]
    pick = None
    if target:
        cands = [d for d in dets if d["label"] == target]
        pick = cands[0] if cands else None
        if pick is None:
            out.append(f"No {target} detected — rotate the drone slowly or run a camera scan; check lighting.")
    elif dets:
        prompted = [d for d in dets if d["label"] in o["prompts"]] or dets
        with_range = [d for d in prompted if d.get("distance_m") is not None]
        pick = min(with_range, key=lambda d: d["distance_m"]) if with_range else prompted[0]
    if pick:
        lab = pick["label"]
        b = pick["bearing_deg"]
        if abs(b) > 5:
            out.append(f"Yaw {'right' if b > 0 else 'left'} ~{abs(b):.0f}° to bring the {lab} to the center of the frame.")
        else:
            out.append(f"Heading is aligned with the {lab} (within 5°).")
        dist = pick.get("distance_m")
        if dist is not None and pick.get("distance_kind") == "upper_bound":
            out.append(f"The {lab} is partly out of frame (range ≤ {dist:.1f} m) — tilt the camera to frame it before approaching.")
        elif dist is not None:
            if dist > standoff_m + 0.3:
                out.append(f"Advance ~{dist - standoff_m:.1f} m to reach a {standoff_m:.1f} m standoff (estimated range {dist:.1f} m, ±30 %).")
            else:
                out.append(f"Within the {standoff_m:.1f} m standoff (~{dist:.1f} m) — hold position.")
        if abs(pick["elevation_deg"]) > 8 and not o["servo"]["armed"]:
            out.append(f"Tilt the camera {'up' if pick['elevation_deg'] > 0 else 'down'} ~{abs(pick['elevation_deg']):.0f}° "
                       "(enable servo control to let Qwen do it).")
    i = o["imu"]
    if i and (abs(i["roll"]) > 20 or abs(i["pitch"]) > 20):
        out.append(f"Level the airframe first (roll {i['roll']:+.0f}°, pitch {i['pitch']:+.0f}°).")
    e = o["env"]
    if e and (e["temperature_c"] > 45 or e["temperature_c"] < 0):
        out.append(f"Ambient {e['temperature_c']:.0f} °C is outside the comfortable range for the electronics.")
    for w in o["system"]["warnings"]:
        if "VOLTAGE" in w.upper():
            out.append("Power problem detected — land and check the supply before continuing.")
            break
    return out


PLANNER_PROMPT = """You are the PLANNER of STRIX-1, an AI camera system on a small drone.
You control ONLY the camera tilt servo (up/down). You never fly or move the drone.
Turn the operator's TASK into a short plan of camera skills. Reply with JSON only:
{"thought": "<one short sentence>", "target": "<object class or empty>", "plan": [<steps>]}

Skills (tilt is in degrees relative to level; + is up, - is down):
- {"skill":"look","tilt":N}            point the camera at absolute tilt N
- {"skill":"tilt","delta":N}           tilt relative to now
- {"skill":"center"}                   level the camera
- {"skill":"scan"}                     sweep the tilt range and record what is seen
- {"skill":"find","target":"person"}   sweep until that object class is detected
- {"skill":"track","target":"person","seconds":8}  keep that object vertically centered
- {"skill":"wait","seconds":2}
Rules: at most 5 steps; use an empty plan [] for questions that need no camera motion;
targets must be detector classes such as person, car, dog, chair, bottle.

Examples:
TASK: look up a bit -> {"thought":"tilt up 15 degrees","target":"","plan":[{"skill":"tilt","delta":15}]}
TASK: find the person and keep them in view -> {"thought":"locate then track","target":"person","plan":[{"skill":"find","target":"person"},{"skill":"track","target":"person","seconds":8}]}
TASK: how warm is it? -> {"thought":"question only","target":"","plan":[]}"""

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string", "maxLength": 200},
        "target": {"type": "string", "maxLength": 30},
        "plan": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "skill": {"type": "string", "enum": SKILLS},
                    "tilt": {"type": "number"},
                    "delta": {"type": "number"},
                    "target": {"type": "string", "maxLength": 30},
                    "seconds": {"type": "number"},
                },
                "required": ["skill"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["thought", "target", "plan"],
    "additionalProperties": False,
}

REPORT_PROMPT = """You are STRIX-1's onboard assistant on a camera drone. The operator flies the drone; you only
move the camera tilt servo (already done, see ACTION LOG) and give advice.
Answer the TASK using ONLY the data provided, in at most 6 short lines:
- [MEASURED] sensor values you rely on (IMU, DHT11) with their numbers.
- [DETECTED] objects with estimated distance and direction (estimates are ±30 %).
- What the camera did, from the ACTION LOG.
- SUGGEST: lines with how to move or complete the task — reuse the PILOT SUGGESTIONS.
Mark your own guesses [INTERPRETATION] and missing data [UNAVAILABLE]. If data is SYNTHETIC say so.
Never invent positions, GPS, altitude or battery levels. No code, no shell commands."""


def deterministic_report(o: dict[str, Any], task: str, actions: list[str], sugg: list[str]) -> str:
    lines = ["**Telemetry report** (rule-based — Qwen offline)."]
    if o["synthetic"]:
        lines.append(f"- ⚠ SYNTHETIC DEMO DATA ({', '.join(o['synthetic'])})")
    i = o["imu"]
    if i:
        lines.append(f"- [MEASURED] roll {i['roll']:+.1f}°, pitch {i['pitch']:+.1f}°, relative yaw {i['yaw_rel']:+.1f}°")
    e = o["env"]
    if e:
        lines.append(f"- [MEASURED] {e['temperature_c']:.1f} °C, {e['humidity_pct']:.0f} %RH")
    for d in o["detections"][:4]:
        lines.append(f"- [DETECTED] {describe_detection(d)}")
    if not o["detections"]:
        lines.append("- [DETECTED] no objects in view")
    for a in actions:
        lines.append(f"- Camera: {a}")
    for s in sugg:
        lines.append(f"- SUGGEST: {s}")
    return "\n".join(lines)
