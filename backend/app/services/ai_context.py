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

SKILLS = ["look", "tilt", "center", "scan", "find", "track", "goto", "wait"]


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
        "memory": _memory(c),
        "detector": c.detector.engine_kind,
        "prompts": list(c.detector.prompts),
        "system": {"cpu": sysm.get("cpu_percent"), "cpu_temp": sysm.get("cpu_temp_c"),
                   "warnings": [w["message"] for w in sysm.get("warnings", [])]},
    }


def _memory(c: Any) -> dict[str, Any]:
    mem = getattr(c, "memory", None)
    if mem is None:
        return {"lines": [], "objects": [], "camera": None}
    snap = mem.snapshot()
    return {"lines": mem.summary_lines(), "objects": snap["objects"], "camera": snap["camera"]}


def remembered_targets(o: dict[str, Any]) -> list[str]:
    """Labels in memory that are not in view now: hidden first, then non-containers, most recently seen first."""
    objs = [m for m in o.get("memory", {}).get("objects", []) if m["memory"] != "seen"]
    objs.sort(key=lambda m: (m["memory"] != "hidden", m["container"], -m["last_seen"]))
    out: list[str] = []
    for m in objs:
        if m["label"] not in out:
            out.append(m["label"])
    return out


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


def say_detection(d: dict[str, Any]) -> str:
    """Plain-language phrase for the operator, e.g. "sports ball about 0.6 m away, 22° to the left"."""
    b, e = d["bearing_deg"], d["elevation_deg"]
    side = "straight ahead" if abs(b) < 5 else f"{abs(b):.0f}° to the {'right' if b > 0 else 'left'}"
    vert = "" if abs(e) < 8 else (", above the center" if e > 0 else ", below the center")
    if d.get("distance_m") is None:
        dist = ""
    elif d.get("distance_kind") == "upper_bound":
        dist = f" at most {d['distance_m']:.1f} m away"
    else:
        dist = f" about {d['distance_m']:.1f} m away"
    return f"{d['label']}{dist}, {side}{vert}"


def count_phrase(labels: list[str]) -> str:
    """["cup", "cup", "sports ball"] → "2 cups and a sports ball"."""
    from collections import Counter

    parts = []
    for lab, n in Counter(labels).items():
        if n == 1:
            parts.append(f"{'an' if lab[0] in 'aeiou' else 'a'} {lab}")
        else:
            parts.append(f"{n} {lab}{'' if lab.endswith('s') else 's'}")
    if not parts:
        return "nothing"
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


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
    mem = o.get("memory") or {}
    if mem.get("lines"):
        L.append("MEMORY [object permanence; az/el = direction from the drone, az + right, el + up; SEEN = in view now]:")
        L.extend(f"  - {line}" for line in mem["lines"])
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
            mem = [m for m in (o.get("memory") or {}).get("objects", []) if m["label"] == target]
            cam = (o.get("memory") or {}).get("camera")
            if mem and cam:
                m = max(mem, key=lambda m: m["last_seen"])
                daz = (m["az"] - cam["az"] + 180) % 360 - 180
                where = f"hidden under {m['contained_by_name']}" if m["memory"] == "hidden" else f"out of sight {m['out_of_sight_s']:.0f} s"
                turn = f"turn the drone {'right' if daz > 0 else 'left'} about {abs(daz):.0f}°" if abs(daz) > 5 else "the drone is already facing it"
                out.append(f"I can't see the {target} right now, but I remember it ({where}): {turn}, or ask me to go to it.")
            else:
                out.append(f"I can't see a {target} — turn the drone slowly or ask me to look around.")
    elif dets:
        prompted = [d for d in dets if d["label"] in o["prompts"]] or dets
        with_range = [d for d in prompted if d.get("distance_m") is not None]
        pick = min(with_range, key=lambda d: d["distance_m"]) if with_range else prompted[0]
    if pick:
        lab = pick["label"]
        b = pick["bearing_deg"]
        if abs(b) > 5:
            out.append(f"Turn the drone {'right' if b > 0 else 'left'} about {abs(b):.0f}° to center the {lab}.")
        else:
            out.append(f"The drone is facing the {lab}.")
        dist = pick.get("distance_m")
        if dist is not None and pick.get("distance_kind") == "upper_bound":
            out.append(f"The {lab} is partly out of the picture — tilt the camera to see all of it.")
        elif dist is not None:
            if dist > standoff_m + 0.3:
                out.append(f"Move about {dist - standoff_m:.1f} m closer to be {standoff_m:.1f} m away (it's about {dist:.1f} m away now).")
            else:
                out.append(f"You're close enough (about {dist:.1f} m) — hold position.")
        if abs(pick["elevation_deg"]) > 8 and not o["servo"]["armed"]:
            out.append(f"Ask me to tilt the camera {'up' if pick['elevation_deg'] > 0 else 'down'} about {abs(pick['elevation_deg']):.0f}°.")
    i = o["imu"]
    if i and (abs(i["roll"]) > 20 or abs(i["pitch"]) > 20):
        out.append(f"Level the drone first (it's tilted {max(abs(i['roll']), abs(i['pitch'])):.0f}°).")
    e = o["env"]
    if e and (e["temperature_c"] > 45 or e["temperature_c"] < 0):
        out.append(f"It's {e['temperature_c']:.0f} °C — too hot or cold for the electronics.")
    for w in o["system"]["warnings"]:
        if "VOLTAGE" in w.upper():
            out.append("The power supply is too weak — land and check the battery.")
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
- {"skill":"goto","target":"sports ball"}  aim the camera at an object from MEMORY, even when it is out of view
                                          or HIDDEN under a cup (target may also be an id like "#4")
- {"skill":"wait","seconds":2}
Rules: at most 5 steps; use an empty plan [] for questions that need no camera motion;
targets must be detector classes such as person, car, dog, chair, bottle, cup, sports ball.
Use goto for objects listed in MEMORY that are not SEEN now; it also gives the pilot yaw advice.

Examples:
TASK: look up a bit -> {"thought":"tilt up 15 degrees","target":"","plan":[{"skill":"tilt","delta":15}]}
TASK: move the servo please -> {"thought":"no direction given, tilt up 20 degrees","target":"","plan":[{"skill":"tilt","delta":20}]}
TASK: look at the floor -> {"thought":"point the camera down","target":"","plan":[{"skill":"look","tilt":-45}]}
TASK: find the person and keep them in view -> {"thought":"locate then track","target":"person","plan":[{"skill":"find","target":"person"},{"skill":"track","target":"person","seconds":8}]}
TASK: where is the ball? -> {"thought":"the ball is in memory, aim at it","target":"sports ball","plan":[{"skill":"goto","target":"sports ball"}]}
TASK: send it to the remembered target -> {"thought":"aim at the remembered object","target":"sports ball","plan":[{"skill":"goto","target":"sports ball"}]}
TASK: what do you remember? -> {"thought":"answer from memory","target":"","plan":[]}
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

REPORT_PROMPT = """You are Qwen, the co-pilot AI on a small camera drone. The pilot flies the drone; you can only tilt the
camera (already done, see ACTION LOG) and give advice. Answer the TASK in 1-4 short, friendly, plain sentences, like
talking to the pilot. No tags, no lists, no headings, no code.
Use ONLY the data given (and the camera IMAGE if attached): what you see in the image, YOLO objects with distance and
direction, MEMORY (objects out of sight or hidden under a cup, and for how long), sensor values when asked, what the
camera did, and one tip from the PILOT SUGGESTIONS if useful. Say "about" for distances (they are estimates).
Never invent positions, GPS, altitude or battery levels. If the data is SYNTHETIC, mention once that it's demo data."""

WATCH_PROMPT = """You are the co-pilot AI of a small camera drone. Something just changed (EVENT): YOLO saw new objects, or
an object went under / came out from a cup. Tell the pilot in ONE short, friendly sentence what happened (with about how
far and which way, or which cup and for how long). If a camera IMAGE is attached you may add what you see in it.
Only use the data and the image. No tags, no code."""

_VISUAL_RE = None


def is_visual(task: str) -> bool:
    """Questions that need the camera image itself (not just YOLO's class list)."""
    import re

    global _VISUAL_RE
    if _VISUAL_RE is None:
        _VISUAL_RE = re.compile(
            r"\b(identify|recogni[sz]e|describe|what (?:is|are) (?:this|that|it|these|those|in front|there)|what's (?:this|that|there)|"
            r"what do you see|what can you see|what does the camera (?:see|show)|what (?:is|am i) holding|look at (?:this|that)|"
            r"read (?:this|that|the)|what colou?r|how many|count|see anything|in view|in the (?:image|picture|frame|video))\b",
            re.IGNORECASE,
        )
    return bool(_VISUAL_RE.search(task))


def deterministic_watch(dets: list[dict[str, Any]], sugg: list[str], synthetic: bool) -> str:
    """Plain one-line announcement of new YOLO detections (used when Qwen is offline)."""
    near = [d for d in dets if d.get("distance_m") is not None]
    lead = min(near, key=lambda d: d["distance_m"]) if near else (dets[0] if dets else None)
    text = f"I see {count_phrase([d['label'] for d in dets])}"
    if lead and len(dets) > 1:
        text += f" — the closest is the {say_detection(lead)}"
    elif lead:
        text = f"I see a {say_detection(lead)}"
    return text + "." + (" (demo data)" if synthetic else "")


ENV_WORDS = r"temp|humid|warm|cold|hot|weather|environment|dht"
IMU_WORDS = r"imu|level|roll|pitch|yaw|attitude|orientation|tilted|mpu"
SENSOR_WORDS = ENV_WORDS + "|" + IMU_WORDS


def _asks(task: str, words: str) -> bool:
    import re

    return bool(re.search(words, task, re.IGNORECASE))


def deterministic_report(o: dict[str, Any], task: str, actions: list[str], sugg: list[str], note: str = "",
                         target: str | None = None) -> str:
    """Short plain-language answer built from the data when Qwen is offline."""
    out: list[str] = []
    if note:
        out.append(note)
    out.extend(a[0].upper() + a[1:] + ("" if a.endswith(".") else ".") for a in actions if a)
    did_goto = any("I remembered" in a for a in actions)
    dets = o["detections"]
    if target:
        hits = [d for d in dets if d["label"] == target]
        if hits and not did_goto:
            out.append(f"The {say_detection(hits[0])}.")
        elif not hits and not did_goto:
            mem = [m for m in (o.get("memory") or {}).get("objects", []) if m["label"] == target]
            if mem:
                m = mem[0]
                where = f"hidden under {m['contained_by_name']}" if m["memory"] == "hidden" else f"out of sight for {m['out_of_sight_s']:.0f} s"
                out.append(f"I can't see the {target} now, but I remember it ({where}).")
            else:
                out.append(f"I don't see a {target}.")
    elif (not actions and not _asks(task, SENSOR_WORDS)) or is_visual(task) or _asks(task, r"\b(see|there|around|objects?|anything)\b"):
        if dets:
            near = [d for d in dets if d.get("distance_m") is not None]
            lead = min(near, key=lambda d: d["distance_m"]) if near else dets[0]
            out.append(f"I see {count_phrase([d['label'] for d in dets])}; the closest is the {say_detection(lead)}.")
        else:
            out.append("I don't see anything right now.")
        hidden = [m for m in (o.get("memory") or {}).get("objects", []) if m["memory"] == "hidden"]
        for m in hidden[:2]:
            out.append(f"I also remember a {m['label']} hidden under {m['contained_by_name']}.")
    i, e = o["imu"], o["env"]
    if e and _asks(task, ENV_WORDS):
        out.append(f"It's {e['temperature_c']:.1f} °C with {e['humidity_pct']:.0f} % humidity.")
    if i and _asks(task, IMU_WORDS):
        out.append(f"The drone is at roll {i['roll']:+.0f}°, pitch {i['pitch']:+.0f}°.")
    if is_visual(task):
        out.append("To describe the picture itself I need the Qwen vision model (scripts/setup_ai.sh).")
    tip = next((t for t in sugg if not (did_goto and "remember" in t)), None)
    if tip and (target or not actions):
        out.append(f"Tip: {tip}")
    if o["synthetic"]:
        out.append("(demo data)")
    return " ".join(out)
