"""MPU6050 acquisition, orientation estimation, calibration and health checks.

Acquisition runs in a dedicated thread at ``STRIX_IMU_RATE_HZ`` so I2C latency
never blocks the asyncio event loop. Consumers read the latest sample and a
decimated history ring buffer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import struct
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Protocol

from app.config.settings import Settings, parse_axis_map
from app.core.orientation import (
    STANDARD_GRAVITY,
    CalibrationError,
    ComplementaryFilter,
    ImuCalibration,
    compute_calibration,
)
from app.services.base import DeviceState, DeviceStatus, StoppableThread
from app.services.hub import EventLog

log = logging.getLogger(__name__)

# Register-level constants verified against adafruit_mpu6050 1.3.x.
MPU6050_REG_ACCEL_OUT = 0x3B  # 14 contiguous bytes: accel(6) temp(2) gyro(6)
ACCEL_LSB_PER_G = {2: 16384.0, 4: 8192.0, 8: 4096.0, 16: 2048.0}
GYRO_LSB_PER_DPS = {250: 131.0, 500: 65.5, 1000: 32.8, 2000: 16.4}
ACCEL_RANGE_CODE = {2: 0, 4: 1, 8: 2, 16: 3}
GYRO_RANGE_CODE = {250: 0, 500: 1, 1000: 2, 2000: 3}
DLPF_CODE = {260: 0, 184: 1, 94: 2, 44: 3, 21: 4, 10: 5, 5: 6}

HISTORY_HZ = 25.0


def parse_mpu6050_burst(
    buf: bytes, accel_range_g: int, gyro_range_dps: int
) -> tuple[tuple[float, float, float], tuple[float, float, float], float]:
    """Decode a 14-byte ACCEL_XOUT_H..GYRO_ZOUT_L burst read.

    Returns (accel m/s², gyro °/s, temperature °C).
    """
    if len(buf) != 14:
        raise ValueError(f"expected 14 bytes, got {len(buf)}")
    ax, ay, az, t_raw, gx, gy, gz = struct.unpack(">7h", buf)
    a_scale = STANDARD_GRAVITY / ACCEL_LSB_PER_G[accel_range_g]
    g_scale = 1.0 / GYRO_LSB_PER_DPS[gyro_range_dps]
    temp_c = t_raw / 340.0 + 36.53  # datasheet formula, same as Adafruit driver
    return (
        (ax * a_scale, ay * a_scale, az * a_scale),
        (gx * g_scale, gy * g_scale, gz * g_scale),
        temp_c,
    )


class IMUBackend(Protocol):
    name: str
    synthetic: bool

    def read(self) -> tuple[tuple[float, float, float], tuple[float, float, float], float | None]: ...

    def close(self) -> None: ...


class AdafruitMPU6050Backend:
    """Real MPU6050 on the Pi's I2C bus 1 (GPIO2 SDA / GPIO3 SCL)."""

    name = "adafruit-mpu6050"
    synthetic = False

    def __init__(self, settings: Settings) -> None:
        import adafruit_mpu6050  # type: ignore[import-not-found]
        import board  # type: ignore[import-not-found]

        self._accel_range = settings.imu_accel_range_g
        self._gyro_range = settings.imu_gyro_range_dps
        self._i2c = board.I2C()
        self._mpu = adafruit_mpu6050.MPU6050(self._i2c, address=settings.imu_address)
        self._mpu.accelerometer_range = ACCEL_RANGE_CODE[self._accel_range]
        self._mpu.gyro_range = GYRO_RANGE_CODE[self._gyro_range]
        self._mpu.filter_bandwidth = DLPF_CODE[settings.imu_dlpf_hz]
        # Internal sample rate = 1 kHz (DLPF on) / (1 + divisor). Keep it above
        # our polling rate so every read sees fresh data.
        self._mpu.sample_rate_divisor = max(0, int(1000 / (settings.imu_rate_hz * 2)) - 1)
        self._buf = bytearray(14)
        self._reg = bytes([MPU6050_REG_ACCEL_OUT])

    def read(self):
        # One I2C transaction -> a coherent accel/temp/gyro snapshot.
        with self._mpu.i2c_device as dev:
            dev.write_then_readinto(self._reg, self._buf)
        return parse_mpu6050_burst(bytes(self._buf), self._accel_range, self._gyro_range)

    def close(self) -> None:
        try:
            self._i2c.deinit()
        except Exception:
            pass


class MockIMUBackend:
    """Synthetic but physically consistent IMU signal for demo/testing.

    Generates a slow 'hover with gentle sway' attitude trajectory, converts it
    to the specific force and body rates a real sensor would see, then adds
    bias, white noise and motor-like vibration. Clearly labeled synthetic.
    """

    name = "mock-imu"
    synthetic = True

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._t0 = time.monotonic()
        self._bias = (0.6, -0.4, 0.25)  # °/s, removed by calibration
        self._prev: tuple[float, float, float, float] | None = None
        self._still_until = 0.0

    def hold_still(self, seconds: float) -> None:
        """Simulate the operator setting the airframe level and still (for calibration demos)."""
        self._still_until = time.monotonic() + seconds

    def attitude(self, t: float) -> tuple[float, float, float]:
        roll = 8.0 * math.sin(0.30 * t) + 2.5 * math.sin(1.13 * t + 0.4)
        pitch = 5.0 * math.sin(0.21 * t + 1.0) + 1.5 * math.sin(0.87 * t)
        yaw = 25.0 * math.sin(0.05 * t)
        return roll, pitch, yaw

    def read(self):
        t = time.monotonic() - self._t0
        roll, pitch, yaw = self.attitude(t)
        if time.monotonic() < self._still_until:
            roll = pitch = 0.0
            yaw = self._prev[3] if self._prev else 0.0
        if self._prev is None:
            p = q = r = 0.0
        else:
            pt, pr, pp, py = self._prev
            dt = max(1e-4, t - pt)
            p, q, r = (roll - pr) / dt, (pitch - pp) / dt, (yaw - py) / dt
        self._prev = (t, roll, pitch, yaw)
        phi, theta = math.radians(roll), math.radians(pitch)
        g = STANDARD_GRAVITY
        vib = lambda: self._rng.gauss(0.0, 0.25)  # noqa: E731 - propeller vibration
        accel = (
            g * math.sin(theta) + vib(),
            g * math.sin(phi) * math.cos(theta) + vib(),
            g * math.cos(phi) * math.cos(theta) + vib(),
        )
        n = lambda: self._rng.gauss(0.0, 0.08)  # noqa: E731
        gyro = (p + self._bias[0] + n(), -q + self._bias[1] + n(), -r + self._bias[2] + n())
        temp = 31.0 + 0.6 * math.sin(t / 120.0) + self._rng.gauss(0, 0.03)
        return accel, gyro, temp

    def close(self) -> None:
        pass


class IMUService:
    def __init__(self, settings: Settings, events: EventLog) -> None:
        self.settings = settings
        self.events = events
        self.status = DeviceStatus("imu")
        self._axis_map = parse_axis_map(settings.imu_axis_map)
        self._filter = ComplementaryFilter(alpha=settings.imu_filter_alpha)
        self._cal_path = settings.data_dir / "imu_calibration.json"
        self.calibration = self._load_calibration()
        self._backend: IMUBackend | None = None
        self._thread: StoppableThread | None = None
        self._lock = threading.Lock()
        self._latest: dict[str, Any] | None = None
        hist_len = int(settings.imu_history_s * HISTORY_HZ)
        self._history: deque[tuple] = deque(maxlen=hist_len)
        self._rate_ema: float | None = None
        self._seq = 0
        self._cal_collect: list[tuple[tuple, tuple]] | None = None
        self._cal_state: dict[str, Any] = {"state": "calibrated" if self.calibration.is_calibrated else "uncalibrated"}
        self._saturation_events = 0
        self._last_hist_t = 0.0

    # ----------------------------------------------------------- lifecycle
    def _make_backend(self) -> IMUBackend | None:
        mode = self.settings.backend_for(self.settings.imu_backend)
        if mode == "disabled":
            self.status.backend = "disabled"
            self.status.set(DeviceState.DISABLED, "disabled by configuration")
            return None
        if mode == "mock":
            self.status.backend = MockIMUBackend.name
            self.status.synthetic = True
            return MockIMUBackend()
        try:
            backend = AdafruitMPU6050Backend(self.settings)
            self.status.backend = backend.name
            self.status.synthetic = False
            return backend
        except Exception as exc:  # ImportError, OSError, RuntimeError (WHO_AM_I)
            self.status.backend = "adafruit-mpu6050"
            self.status.set(DeviceState.DISCONNECTED, f"MPU6050 not available: {exc}")
            log.warning("MPU6050 unavailable: %s", exc)
            return None

    def start(self) -> None:
        self._backend = self._make_backend()
        if self.status.state == DeviceState.DISABLED:
            return
        self._thread = StoppableThread(self._run, "imu-acquisition")
        self._thread.start()

    def stop(self) -> None:
        if self._thread:
            self._thread.stop()
        if self._backend:
            self._backend.close()

    # ---------------------------------------------------------- acquisition
    def _map_axes(self, v: tuple[float, float, float]) -> tuple[float, float, float]:
        m = self._axis_map
        return (v[m[0][0]] * m[0][1], v[m[1][0]] * m[1][1], v[m[2][0]] * m[2][1])

    def _run(self, stop: threading.Event) -> None:
        period = 1.0 / self.settings.imu_rate_hz
        next_t = time.monotonic()
        last_mono: float | None = None
        consecutive_errors = 0
        last_retry = 0.0
        accel_lim = self.settings.imu_accel_range_g * STANDARD_GRAVITY * 0.98
        gyro_lim = self.settings.imu_gyro_range_dps * 0.98
        while not stop.is_set():
            if self._backend is None:
                # Periodically retry hardware initialisation (hot-plug / wiring fix).
                if time.monotonic() - last_retry > 5.0:
                    last_retry = time.monotonic()
                    self._backend = self._make_backend()
                    if self._backend is not None:
                        self.events.add("info", "imu", "MPU6050 connected")
                stop.wait(0.5)
                continue
            try:
                accel_raw, gyro_raw, temp = self._backend.read()
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                if consecutive_errors == 5:
                    self.status.set(DeviceState.DEGRADED, f"I2C read errors: {exc}")
                if consecutive_errors >= 25:
                    self.status.set(DeviceState.DISCONNECTED, f"MPU6050 stopped responding: {exc}")
                    self.events.add("error", "imu", f"MPU6050 lost: {exc}")
                    try:
                        self._backend.close()
                    finally:
                        self._backend = None
                        last_retry = time.monotonic()
                stop.wait(period)
                continue

            now = time.time()
            mono = time.monotonic()
            accel_m = self._map_axes(accel_raw)
            gyro_m = self._map_axes(gyro_raw)
            saturated = any(abs(v) >= accel_lim for v in accel_raw) or any(
                abs(v) >= gyro_lim for v in gyro_raw
            )
            if saturated:
                self._saturation_events += 1
            collect = self._cal_collect
            if collect is not None:
                collect.append((accel_m, gyro_m))
            accel, gyro = self.calibration.apply(accel_m, gyro_m)
            dt = 0.0 if last_mono is None else mono - last_mono
            if last_mono is not None and dt > 0:
                inst = 1.0 / dt
                self._rate_ema = inst if self._rate_ema is None else 0.97 * self._rate_ema + 0.03 * inst
            last_mono = mono
            st = self._filter.update(accel, gyro, dt, now)
            self._seq += 1
            sample = {
                "ts": now,
                "seq": self._seq,
                "accel": {"x": accel[0], "y": accel[1], "z": accel[2]},
                "gyro": {"x": gyro[0], "y": gyro[1], "z": gyro[2]},
                "temp_c": temp,
                "orientation": {
                    "roll": st.roll,
                    "pitch": st.pitch,
                    "yaw_relative": st.yaw,
                    "accel_trust": st.accel_trust,
                    "accel_norm_g": st.accel_norm_g,
                },
                "rate_hz": self._rate_ema,
                "saturated": saturated,
            }
            with self._lock:
                self._latest = sample
                if now - self._last_hist_t >= 1.0 / HISTORY_HZ:
                    self._last_hist_t = now
                    self._history.append(
                        (now, *accel, *gyro, st.roll, st.pitch, st.yaw, temp if temp is not None else math.nan)
                    )
            self.status.touch()
            if self.status.state not in (DeviceState.OK, DeviceState.SYNTHETIC):
                self.status.set(DeviceState.SYNTHETIC if self.status.synthetic else DeviceState.OK)

            next_t += period
            delay = next_t - time.monotonic()
            if delay > 0:
                stop.wait(delay)
            else:
                next_t = time.monotonic()  # fell behind; don't try to catch up

    # ------------------------------------------------------------- readers
    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return self._latest

    def history(self, seconds: float | None = None, max_points: int = 2000) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._history)
        if seconds is not None:
            cutoff = time.time() - seconds
            rows = [r for r in rows if r[0] >= cutoff]
        step = max(1, math.ceil(len(rows) / max_points))
        keys = ("ts", "ax", "ay", "az", "gx", "gy", "gz", "roll", "pitch", "yaw", "temp_c")
        return [dict(zip(keys, r)) for r in rows[::step]]

    def window_stats(self, seconds: float = 60.0) -> dict[str, Any]:
        rows = self.history(seconds, max_points=100000)
        if not rows:
            return {"window_s": seconds, "samples": 0}
        out: dict[str, Any] = {"window_s": seconds, "samples": len(rows), "from": rows[0]["ts"], "to": rows[-1]["ts"]}
        for k in ("roll", "pitch", "yaw", "ax", "ay", "az", "gx", "gy", "gz"):
            vals = [r[k] for r in rows]
            mean = sum(vals) / len(vals)
            std = math.sqrt(sum((v - mean) ** 2 for v in vals) / max(1, len(vals) - 1))
            out[k] = {"min": min(vals), "max": max(vals), "mean": mean, "std": std}
        norms = [math.sqrt(r["ax"] ** 2 + r["ay"] ** 2 + r["az"] ** 2) / STANDARD_GRAVITY for r in rows]
        out["accel_norm_g"] = {"min": min(norms), "max": max(norms), "mean": sum(norms) / len(norms)}
        return out

    # --------------------------------------------------------- calibration
    def _load_calibration(self) -> ImuCalibration:
        try:
            return ImuCalibration.from_dict(json.loads(self._cal_path.read_text()))
        except FileNotFoundError:
            return ImuCalibration()
        except Exception as exc:
            log.warning("ignoring unreadable IMU calibration file: %s", exc)
            return ImuCalibration()

    def calibration_status(self) -> dict[str, Any]:
        st = dict(self._cal_state)
        st["calibration"] = self.calibration.to_dict()
        fs = self._filter.state
        st["yaw_integrated_s"] = fs.yaw_integrated_s
        st["yaw_drift_bound_deg"] = self.calibration.yaw_drift_bound_deg(fs.yaw_integrated_s)
        return st

    async def calibrate(self, duration_s: float = 4.0, level: bool = True) -> dict[str, Any]:
        if self._cal_collect is not None:
            raise CalibrationError("calibration already in progress")
        if self.latest() is None:
            raise CalibrationError("IMU has no live data; cannot calibrate")
        self._cal_state = {"state": "calibrating", "started_at": time.time(), "duration_s": duration_s}
        if isinstance(self._backend, MockIMUBackend):
            self._backend.hold_still(duration_s + 0.6)  # synthetic: "operator holds it still"
            await asyncio.sleep(0.3)  # let the synthetic motion settle before collecting
        self._cal_collect = []
        try:
            await asyncio.sleep(duration_s)
            samples = self._cal_collect or []
        finally:
            self._cal_collect = None
        try:
            cal = compute_calibration(
                [s[0] for s in samples], [s[1] for s in samples], level=level, now=time.time()
            )
        except CalibrationError as exc:
            self._cal_state = {"state": "failed", "message": str(exc), "at": time.time()}
            self.events.add("warning", "imu", f"calibration failed: {exc}")
            raise
        self.calibration = cal
        self._filter.reset(time.time())
        self._cal_state = {"state": "calibrated", "message": f"{cal.samples} samples", "at": time.time()}
        try:
            self._cal_path.parent.mkdir(parents=True, exist_ok=True)
            self._cal_path.write_text(json.dumps(cal.to_dict(), indent=2))
        except OSError as exc:
            log.warning("could not persist IMU calibration: %s", exc)
        self.events.add("info", "imu", "IMU calibrated", gyro_bias_dps=list(cal.gyro_bias_dps))
        return self.calibration_status()

    def clear_calibration(self) -> None:
        self.calibration = ImuCalibration()
        self._cal_state = {"state": "uncalibrated"}
        try:
            self._cal_path.unlink(missing_ok=True)
        except OSError:
            pass
        self._filter.reset(time.time())

    def reset_yaw(self) -> None:
        self._filter.reset_yaw(time.time())
        self.events.add("info", "imu", "relative yaw reset to 0°")

    # -------------------------------------------------------------- health
    def health(self) -> list[dict[str, Any]]:
        """Deterministic anomaly checks (also fed to the AI as ground truth)."""
        findings: list[dict[str, Any]] = []
        latest = self.latest()
        state = self.status.state
        if state in (DeviceState.DISABLED,):
            return [{"severity": "info", "code": "disabled", "message": "IMU disabled"}]
        if latest is None or state == DeviceState.DISCONNECTED:
            return [{"severity": "critical", "code": "no_data", "message": self.status.error or "no IMU data"}]
        age = time.time() - latest["ts"]
        if age > 1.0:
            findings.append({"severity": "critical", "code": "stale", "message": f"last sample {age:.1f}s old"})
        rate = latest.get("rate_hz") or 0.0
        if rate < 0.7 * self.settings.imu_rate_hz:
            findings.append(
                {
                    "severity": "warning",
                    "code": "low_rate",
                    "message": f"sample rate {rate:.0f} Hz below target {self.settings.imu_rate_hz:.0f} Hz",
                }
            )
        stats = self.window_stats(10.0)
        if stats.get("samples", 0) >= 20:
            norm = stats["accel_norm_g"]["mean"]
            gyro_std = max(stats[k]["std"] for k in ("gx", "gy", "gz"))
            acc_std = max(stats[k]["std"] for k in ("ax", "ay", "az"))
            if all(stats[k]["std"] == 0.0 for k in ("ax", "ay", "az", "gx", "gy", "gz")):
                findings.append({"severity": "critical", "code": "frozen", "message": "readings are not changing (sensor stuck?)"})
            if gyro_std < 0.5 and abs(norm - 1.0) > 0.12:
                findings.append(
                    {
                        "severity": "warning",
                        "code": "accel_magnitude",
                        "message": f"stationary accel magnitude {norm:.2f} g (expected ≈1.00 g) — check calibration/mounting",
                    }
                )
            if gyro_std < 0.5:
                bias = max(abs(stats[k]["mean"]) for k in ("gx", "gy", "gz"))
                if bias > 1.0:
                    findings.append(
                        {
                            "severity": "warning",
                            "code": "gyro_bias",
                            "message": f"gyro reads {bias:.2f} °/s while still — recalibrate gyro bias",
                        }
                    )
            if acc_std > 3.0:
                findings.append(
                    {"severity": "info", "code": "vibration", "message": f"high vibration (accel σ {acc_std:.1f} m/s²)"}
                )
        if latest.get("saturated"):
            findings.append({"severity": "warning", "code": "saturated", "message": "sensor at range limit — increase range"})
        if not self.calibration.is_calibrated:
            findings.append({"severity": "info", "code": "uncalibrated", "message": "IMU not calibrated"})
        temp = latest.get("temp_c")
        if temp is not None and not (-20 <= temp <= 70):
            findings.append({"severity": "warning", "code": "temperature", "message": f"die temperature {temp:.1f} °C out of range"})
        return findings

    def summary(self) -> dict[str, Any]:
        latest = self.latest()
        fs = self._filter.state
        return {
            "status": self.status.to_dict(),
            "latest": latest,
            "calibration": self.calibration_status(),
            "health": self.health(),
            "config": {
                "rate_hz": self.settings.imu_rate_hz,
                "accel_range_g": self.settings.imu_accel_range_g,
                "gyro_range_dps": self.settings.imu_gyro_range_dps,
                "dlpf_hz": self.settings.imu_dlpf_hz,
                "filter_alpha": self.settings.imu_filter_alpha,
                "axis_map": self.settings.imu_axis_map,
            },
            "limitations": {
                "absolute_heading": False,
                "yaw": "relative to last reset; integrates gyro and drifts",
                "position": "not available from IMU (no validated localization)",
                "yaw_reset_at": fs.yaw_reset_time,
            },
            "saturation_events": self._saturation_events,
        }
