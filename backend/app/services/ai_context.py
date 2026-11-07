"""Grounded context for the AI assistant.

The model only ever sees a compact, timestamped digest built here from real
service state. Each item is marked MEASURED / DERIVED / DETECTOR / UNAVAILABLE
so answers can keep observations, interpretations and missing data apart.
"""

from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Any

SYSTEM_PROMPT = """You are the analysis assistant of STRIX-1, an observation and diagnostics ground station \
for a small research drone (Raspberry Pi 5 companion computer, MPU6050 IMU, DHT11, Camera Module 3 Wide, \
camera tilt servo). Answer ONLY from the TELEMETRY CONTEXT block supplied with each question.

Rules:
1. Tag claims: [MEASURED] for values present in the context (quote the value and its timestamp/age), \
[INTERPRETATION] for your own inference, [UNAVAILABLE] when the data is not in the context.
2. Never invent numbers, locations, GPS coordinates, altitude, ground speed, distances, compass heading or \
battery level. The MPU6050 has no magnetometer: yaw is relative to the last reset and drifts.
3. Image content is known only through the listed detector outputs (method + confidence) or an attached \
image if one is explicitly provided to you. Do not claim to see anything else.
4. If the context says the data is SYNTHETIC, state that the values are demo data, not real measurements.
5. You cannot control the aircraft, motors, navigation, flight controller or servo. Refuse requests to do so \
and do not produce flight commands, setpoints, code or shell commands.
6. Be concise and technical. Prefer short bullet lists. Mention uncertainty and sensor limitations."""


def _fmt_ts(ts: float | None, now: float) -> str:
    if ts is None:
        return "n/a"
    age = now - ts
    return f"{datetime.fromtimestamp(ts).strftime('%H:%M:%S')} ({age:.0f}s ago)" if age < 3600 else datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def _f(v: Any, nd: int = 2, unit: str = "") -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    return f"{v:.{nd}f}{unit}"


def build_context(c: Any, *, imu_window_s: float = 60.0, include_observations: int = 3) -> dict[str, Any]:
    """Collect structured context from the service container ``c``."""
    now = time.time()
    imu = c.imu.summary()
    env = c.dht.summary()
    servo = c.servo.state()
    cam = c.camera.camera_status()
    sysm = c.system.latest()
    synthetic_sources = [
        name for name, st in (("imu", imu["status"]), ("dht11", env["status"]), ("camera", cam["status"]), ("servo", servo["status"]))
        if st.get("synthetic")
    ]
    return {
        "generated_at": now,
        "synthetic_sources": synthetic_sources,
        "imu": imu,
        "imu_window": c.imu.window_stats(imu_window_s),
        "environment": env,
        "environment_trend": c.dht.trend(300.0),
        "servo": servo,
        "camera": cam,
        "system": sysm,
        "observations": c.mapping.latest_observations(include_observations),
        "position": c.mapping.position(),
        "recording": c.recording.status(),
    }


def render_context(ctx: dict[str, Any]) -> str:
    now = ctx["generated_at"]
    L: list[str] = []
    L.append(f"=== TELEMETRY CONTEXT generated {datetime.fromtimestamp(now).isoformat(timespec='seconds')} ===")
    if ctx["synthetic_sources"]:
        L.append(f"DATA MODE: SYNTHETIC DEMO DATA for {', '.join(ctx['synthetic_sources'])} (not real measurements)")
    else:
        L.append("DATA MODE: live hardware")

    imu = ctx["imu"]
    st = imu["status"]
    L.append(f"\n[IMU MPU6050] status={st['state']} backend={st['backend']}" + (f" error={st['error']}" if st.get("error") else ""))
    latest = imu.get("latest")
    if latest:
        o = latest["orientation"]
        a, g = latest["accel"], latest["gyro"]
        L.append(f"MEASURED at {_fmt_ts(latest['ts'], now)}: accel m/s² x={_f(a['x'])} y={_f(a['y'])} z={_f(a['z'])}; "
                 f"gyro °/s x={_f(g['x'])} y={_f(g['y'])} z={_f(g['z'])}; die temp {_f(latest.get('temp_c'), 1, ' °C')}; "
                 f"rate {_f(latest.get('rate_hz'), 0, ' Hz')}")
        L.append(f"DERIVED (complementary filter): roll={_f(o['roll'], 1, '°')} pitch={_f(o['pitch'], 1, '°')} "
                 f"relative_yaw={_f(o['yaw_relative'], 1, '°')} (yaw drifts, not a heading); accel trust={_f(o['accel_trust'], 2)}")
    else:
        L.append("UNAVAILABLE: no IMU samples")
    w = ctx["imu_window"]
    if w.get("samples"):
        L.append(f"DERIVED last {w['window_s']:.0f}s ({w['samples']} samples): "
                 f"roll {_f(w['roll']['min'], 1)}..{_f(w['roll']['max'], 1)}° (σ {_f(w['roll']['std'], 2)}), "
                 f"pitch {_f(w['pitch']['min'], 1)}..{_f(w['pitch']['max'], 1)}° (σ {_f(w['pitch']['std'], 2)}), "
                 f"|a| mean {_f(w['accel_norm_g']['mean'], 3)} g (min {_f(w['accel_norm_g']['min'], 2)}, max {_f(w['accel_norm_g']['max'], 2)}), "
                 f"gyro σ x/y/z {_f(w['gx']['std'], 2)}/{_f(w['gy']['std'], 2)}/{_f(w['gz']['std'], 2)} °/s")
    cal = imu["calibration"]
    L.append(f"Calibration: {cal.get('state')}" + (
        f", yaw drift bound ≈±{_f(cal.get('yaw_drift_bound_deg'), 1)}° since reset" if cal.get("yaw_drift_bound_deg") is not None else ""))
    for f in imu["health"]:
        L.append(f"HEALTH CHECK {f['severity'].upper()}: {f['message']}")

    env = ctx["environment"]
    st = env["status"]
    L.append(f"\n[DHT11] status={st['state']} backend={st['backend']}" + (f" error={st['error']}" if st.get("error") else ""))
    el = env.get("latest")
    if el:
        L.append(f"MEASURED at {_fmt_ts(el['ts'], now)}: temperature {_f(el['temperature_c'], 1, ' °C')}, "
                 f"humidity {_f(el['humidity_pct'], 0, ' %RH')} (DHT11 accuracy ±2 °C, ±5 %RH)"
                 + (" — STALE" if env.get("stale") else ""))
        s = env["session"]
        L.append(f"DERIVED session extremes: temp {_f(s['temp_min'], 1)}..{_f(s['temp_max'], 1)} °C, humidity {_f(s['hum_min'], 0)}..{_f(s['hum_max'], 0)} %")
    else:
        L.append("UNAVAILABLE: no DHT11 reading")
    tr = ctx["environment_trend"]
    if tr.get("samples", 0) >= 2:
        L.append(f"DERIVED change over last {tr['window_s']:.0f}s: temperature {tr['temperature_change_c']:+.1f} °C, "
                 f"humidity {tr['humidity_change_pct']:+.0f} % ({tr['samples']} readings)")

    sv = ctx["servo"]
    L.append(f"\n[CAMERA SERVO] status={sv['status']['state']} armed={sv['armed']} commanded={_f(sv['commanded_angle'], 1, '°')} "
             f"limits {sv['limits']['min_angle']:.0f}–{sv['limits']['max_angle']:.0f}° (no position feedback)")

    cam = ctx["camera"]
    L.append(f"\n[CAMERA] status={cam['status']['state']} backend={cam['status']['backend']} "
             f"{cam.get('width')}x{cam.get('height')} measured {cam.get('measured_fps')} fps; recording={cam['recording']['active']}")
    obs = ctx["observations"]
    if obs:
        for o in obs[-3:]:
            syn = " SYNTHETIC FRAME" if (o.get("frame") or {}).get("synthetic") else ""
            L.append(f"DETECTOR (OpenCV, {o['frame'].get('source', 'live')}{syn}) at {_fmt_ts(o['ts'], now)}: {o['summary']}")
    else:
        L.append("UNAVAILABLE: no image analysis has been run yet")

    sysm = ctx["system"] or {}
    if sysm:
        mem = sysm.get("memory", {})
        L.append(f"\n[SYSTEM] MEASURED at {_fmt_ts(sysm.get('ts'), now)}: CPU {_f(sysm.get('cpu_percent'), 0, '%')}, "
                 f"CPU temp {_f(sysm.get('cpu_temp_c'), 1, ' °C')}, RAM {_f(mem.get('used_percent'), 0, '%')} used")
        p = sysm.get("power") or {}
        if p.get("input_voltage_v") is not None:
            L.append(f"MEASURED 5V input {p['input_voltage_v']:.2f} V")
        if p.get("undervoltage_now") is not None:
            L.append(f"MEASURED under-voltage now: {p['undervoltage_now']}")
        for wn in sysm.get("warnings", []):
            L.append(f"WARNING: {wn['message']}")
    L.append("\nUNAVAILABLE (no sensor): GPS position, altitude, ground speed, absolute compass heading, battery percentage, distances in the scene.")
    L.append(f"POSITION: {ctx['position']['message']}")
    L.append("=== END CONTEXT ===")
    return "\n".join(L)


def deterministic_digest(ctx: dict[str, Any], question: str) -> str:
    """Rule-based answer used when no language model is available."""
    now = ctx["generated_at"]
    lines = ["**Telemetry digest** (deterministic, generated without a language model)."]
    if ctx["synthetic_sources"]:
        lines.append(f"- ⚠ SYNTHETIC DEMO DATA: {', '.join(ctx['synthetic_sources'])}")
    latest = ctx["imu"].get("latest")
    if latest:
        o = latest["orientation"]
        lines.append(f"- [MEASURED→DERIVED] roll {o['roll']:.1f}°, pitch {o['pitch']:.1f}°, relative yaw {o['yaw_relative']:.1f}° at {_fmt_ts(latest['ts'], now)}")
    else:
        lines.append("- [UNAVAILABLE] IMU data")
    w = ctx["imu_window"]
    if w.get("samples"):
        lines.append(f"- [DERIVED] last {w['window_s']:.0f}s: roll {w['roll']['min']:.1f}…{w['roll']['max']:.1f}°, "
                     f"pitch {w['pitch']['min']:.1f}…{w['pitch']['max']:.1f}°, mean |a| {w['accel_norm_g']['mean']:.3f} g")
    el = ctx["environment"].get("latest")
    if el:
        lines.append(f"- [MEASURED] {el['temperature_c']:.1f} °C, {el['humidity_pct']:.0f} %RH at {_fmt_ts(el['ts'], now)}")
    tr = ctx["environment_trend"]
    if tr.get("samples", 0) >= 2:
        lines.append(f"- [DERIVED] temperature change over {tr['window_s']:.0f}s: {tr['temperature_change_c']:+.1f} °C")
    for f in ctx["imu"]["health"]:
        lines.append(f"- [HEALTH] {f['severity']}: {f['message']}")
    if ctx["observations"]:
        o = ctx["observations"][-1]
        lines.append(f"- [DETECTOR] {o['summary']} ({_fmt_ts(o['ts'], now)})")
    lines.append(f"- {ctx['position']['message']}")
    return "\n".join(lines)
