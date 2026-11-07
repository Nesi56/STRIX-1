"""Orientation estimation from a 6-DoF IMU (accelerometer + gyroscope).

Limitations (surfaced to the UI as well):

* The MPU6050 has no magnetometer, so there is **no absolute heading**. Yaw is
  integrated gyro rate relative to the last reset and drifts without bound.
* Roll/pitch are corrected by the gravity vector, which is only valid when
  the airframe is not accelerating. During dynamic manoeuvres the filter
  automatically reduces accelerometer trust and the estimate degrades.
* Nothing here estimates position. Double-integrating acceleration is not a
  usable localization source and is deliberately not implemented.

Conventions
-----------
Inputs are in the *sensor body frame* after axis mapping: X forward, Y left,
Z up — i.e. an MPU6050 lying flat, chip up, reads about +1 g on Z. Internally
they are converted to the aerospace FRD frame (X forward, Y right, Z down) so
that the outputs follow aviation conventions:

* roll  + = right side down
* pitch + = nose up
* yaw   + = nose right (clockwise seen from above), relative to last reset

Angles are in degrees.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

STANDARD_GRAVITY = 9.80665


def wrap_deg(angle: float) -> float:
    """Wrap an angle to (-180, 180]."""
    a = (angle + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a


def accel_to_roll_pitch(ax: float, ay: float, az: float) -> tuple[float, float]:
    """Roll and pitch (degrees) implied by a gravity-dominated accelerometer reading.

    ``ax, ay, az`` are specific force in the sensor frame (X fwd, Y left, Z up).
    """
    # Convert to FRD specific force: (x, -y, -z). At rest level: (0, 0, -g).
    fx, fy, fz = ax, -ay, -az
    roll = math.degrees(math.atan2(-fy, -fz))
    pitch = math.degrees(math.atan2(fx, math.sqrt(fy * fy + fz * fz)))
    return roll, pitch


@dataclass
class OrientationState:
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0  # relative to last reset, NOT a compass heading
    accel_trust: float = 1.0  # 0..1, how much the last update trusted gravity
    accel_norm_g: float = 1.0
    initialized: bool = False
    yaw_reset_time: float | None = None
    yaw_integrated_s: float = 0.0


@dataclass
class ComplementaryFilter:
    """Complementary filter for roll/pitch with relative yaw integration.

    ``alpha`` is the gyro weight per update at the nominal rate. Accelerometer
    corrections are attenuated when the measured specific force magnitude
    departs from 1 g (dynamic acceleration, vibration spikes).
    """

    alpha: float = 0.98
    accel_gate_g: float = 0.15  # full trust within ±0.15 g of 1 g; none beyond 3x this
    max_dt: float = 0.25  # ignore integration gaps longer than this (s)
    state: OrientationState = field(default_factory=OrientationState)

    def reset(self, now: float | None = None) -> None:
        self.state = OrientationState(yaw_reset_time=now)

    def reset_yaw(self, now: float | None = None) -> None:
        self.state.yaw = 0.0
        self.state.yaw_reset_time = now
        self.state.yaw_integrated_s = 0.0

    def accel_weight(self, norm_g: float) -> float:
        err = abs(norm_g - 1.0)
        if err <= self.accel_gate_g:
            return 1.0
        if err >= 3.0 * self.accel_gate_g:
            return 0.0
        return 1.0 - (err - self.accel_gate_g) / (2.0 * self.accel_gate_g)

    def update(
        self,
        accel_ms2: tuple[float, float, float],
        gyro_dps: tuple[float, float, float],
        dt: float,
        now: float | None = None,
    ) -> OrientationState:
        ax, ay, az = accel_ms2
        # Body rates in FRD: p (roll rate), q (pitch rate), r (yaw rate).
        gx, gy, gz = gyro_dps[0], -gyro_dps[1], -gyro_dps[2]
        s = self.state
        norm = math.sqrt(ax * ax + ay * ay + az * az) / STANDARD_GRAVITY
        s.accel_norm_g = norm
        acc_roll, acc_pitch = accel_to_roll_pitch(ax, ay, az)

        if not s.initialized:
            # Seed from gravity so we don't slowly converge from zero.
            s.roll, s.pitch = acc_roll, acc_pitch
            s.yaw = 0.0
            s.initialized = True
            s.yaw_reset_time = now
            s.accel_trust = 1.0
            return s

        if dt <= 0.0 or dt > self.max_dt:
            # Timing gap: re-seed roll/pitch from gravity when trustworthy.
            if self.accel_weight(norm) > 0.5:
                s.roll, s.pitch = acc_roll, acc_pitch
            return s

        # Euler angle rates from body rates (ZYX convention).
        r = math.radians(s.roll)
        p = math.radians(s.pitch)
        cos_p = math.cos(p)
        if abs(cos_p) < 1e-3:  # gimbal-lock guard near ±90° pitch
            cos_p = math.copysign(1e-3, cos_p)
        tan_p = math.sin(p) / cos_p
        roll_rate = gx + math.sin(r) * tan_p * gy + math.cos(r) * tan_p * gz
        pitch_rate = math.cos(r) * gy - math.sin(r) * gz
        yaw_rate = (math.sin(r) * gy + math.cos(r) * gz) / cos_p

        gyro_roll = s.roll + roll_rate * dt
        gyro_pitch = s.pitch + pitch_rate * dt

        w = self.accel_weight(norm)
        # Scale the accelerometer contribution by its trust weight.
        k = (1.0 - self.alpha) * w
        # Blend on the circle for roll (it can wrap through ±180°).
        roll_err = wrap_deg(acc_roll - gyro_roll)
        s.roll = wrap_deg(gyro_roll + k * roll_err)
        s.pitch = max(-90.0, min(90.0, gyro_pitch + k * (acc_pitch - gyro_pitch)))
        s.yaw = wrap_deg(s.yaw + yaw_rate * dt)
        s.yaw_integrated_s += dt
        s.accel_trust = w
        return s


@dataclass
class ImuCalibration:
    """Static bias calibration (gyro bias, accelerometer level offsets)."""

    gyro_bias_dps: tuple[float, float, float] = (0.0, 0.0, 0.0)
    accel_offset_ms2: tuple[float, float, float] = (0.0, 0.0, 0.0)
    gyro_noise_dps: float | None = None  # std dev while stationary
    accel_noise_ms2: float | None = None
    calibrated_at: float | None = None
    samples: int = 0
    level_reference: bool = False  # accel offsets assume the airframe was level

    def apply(
        self, accel: tuple[float, float, float], gyro: tuple[float, float, float]
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        a = tuple(v - o for v, o in zip(accel, self.accel_offset_ms2))
        g = tuple(v - b for v, b in zip(gyro, self.gyro_bias_dps))
        return a, g  # type: ignore[return-value]

    @property
    def is_calibrated(self) -> bool:
        return self.calibrated_at is not None

    def yaw_drift_bound_deg(self, integrated_s: float) -> float | None:
        """Rough 1-sigma bound on accumulated yaw error since reset.

        Uses residual gyro noise as an upper proxy for bias instability. This is
        an indicator, not a guarantee.
        """
        if self.gyro_noise_dps is None:
            return None
        # Residual bias uncertainty of the mean ~ noise/sqrt(N); assume a floor
        # of 0.02 deg/s typical for MPU6050 bias instability over minutes.
        resid = max(0.02, self.gyro_noise_dps / math.sqrt(max(1, self.samples)))
        return resid * integrated_s

    def to_dict(self) -> dict:
        return {
            "gyro_bias_dps": list(self.gyro_bias_dps),
            "accel_offset_ms2": list(self.accel_offset_ms2),
            "gyro_noise_dps": self.gyro_noise_dps,
            "accel_noise_ms2": self.accel_noise_ms2,
            "calibrated_at": self.calibrated_at,
            "samples": self.samples,
            "level_reference": self.level_reference,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ImuCalibration":
        return cls(
            gyro_bias_dps=tuple(float(x) for x in d.get("gyro_bias_dps", (0, 0, 0))),  # type: ignore[arg-type]
            accel_offset_ms2=tuple(float(x) for x in d.get("accel_offset_ms2", (0, 0, 0))),  # type: ignore[arg-type]
            gyro_noise_dps=d.get("gyro_noise_dps"),
            accel_noise_ms2=d.get("accel_noise_ms2"),
            calibrated_at=d.get("calibrated_at"),
            samples=int(d.get("samples", 0)),
            level_reference=bool(d.get("level_reference", False)),
        )


class CalibrationError(RuntimeError):
    pass


def compute_calibration(
    accel_samples: list[tuple[float, float, float]],
    gyro_samples: list[tuple[float, float, float]],
    *,
    level: bool,
    now: float,
    max_gyro_std_dps: float = 1.0,
    max_accel_std_ms2: float = 0.35,
) -> ImuCalibration:
    """Compute a static calibration from samples collected while stationary.

    Raises :class:`CalibrationError` if the samples indicate motion.
    """
    n = len(gyro_samples)
    if n < 50 or len(accel_samples) != n:
        raise CalibrationError(f"not enough samples for calibration ({n})")

    def mean_std(samples: list[tuple[float, float, float]]) -> tuple[list[float], float]:
        means = [sum(s[i] for s in samples) / n for i in range(3)]
        var = max(
            sum((s[i] - means[i]) ** 2 for s in samples) / (n - 1) for i in range(3)
        )
        return means, math.sqrt(var)

    g_mean, g_std = mean_std(gyro_samples)
    a_mean, a_std = mean_std(accel_samples)
    if g_std > max_gyro_std_dps:
        raise CalibrationError(
            f"airframe moved during calibration (gyro std {g_std:.2f} °/s > {max_gyro_std_dps})"
        )
    if a_std > max_accel_std_ms2:
        raise CalibrationError(
            f"vibration/motion during calibration (accel std {a_std:.2f} m/s² > {max_accel_std_ms2})"
        )

    accel_offset = (0.0, 0.0, 0.0)
    if level:
        norm = math.sqrt(sum(v * v for v in a_mean)) / STANDARD_GRAVITY
        if abs(norm - 1.0) > 0.2:
            raise CalibrationError(
                f"accelerometer magnitude {norm:.2f} g is implausible for a stationary sensor"
            )
        # Assume the airframe is level: ideal reading is (0, 0, +g).
        accel_offset = (a_mean[0], a_mean[1], a_mean[2] - STANDARD_GRAVITY)

    return ImuCalibration(
        gyro_bias_dps=(g_mean[0], g_mean[1], g_mean[2]),
        accel_offset_ms2=accel_offset,
        gyro_noise_dps=g_std,
        accel_noise_ms2=a_std,
        calibrated_at=now,
        samples=n,
        level_reference=level,
    )
