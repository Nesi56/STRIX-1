import math

import pytest

from app.core.orientation import (
    STANDARD_GRAVITY as G,
    CalibrationError,
    ComplementaryFilter,
    ImuCalibration,
    accel_to_roll_pitch,
    compute_calibration,
    wrap_deg,
)


def gravity_reading(roll_deg: float, pitch_deg: float) -> tuple[float, float, float]:
    """Specific force seen by a sensor (X fwd, Y left, Z up) at a static attitude."""
    phi, theta = math.radians(roll_deg), math.radians(pitch_deg)
    return (G * math.sin(theta), G * math.sin(phi) * math.cos(theta), G * math.cos(phi) * math.cos(theta))


def test_level_reads_zero():
    r, p = accel_to_roll_pitch(0.0, 0.0, G)
    assert abs(r) < 1e-9 and abs(p) < 1e-9


@pytest.mark.parametrize("roll,pitch", [(20, 0), (-35, 0), (0, 15), (0, -40), (25, -10), (-60, 30)])
def test_accel_attitude_round_trip(roll, pitch):
    r, p = accel_to_roll_pitch(*gravity_reading(roll, pitch))
    assert r == pytest.approx(roll, abs=1e-6)
    assert p == pytest.approx(pitch, abs=1e-6)


def test_sign_conventions():
    # Right side down -> left (+Y) axis tilts up -> +ay -> positive roll.
    assert accel_to_roll_pitch(0.0, 3.0, 9.3)[0] > 0
    # Nose up -> +X tilts up -> +ax -> positive pitch.
    assert accel_to_roll_pitch(3.0, 0.0, 9.3)[1] > 0


def test_wrap():
    assert wrap_deg(190) == pytest.approx(-170)
    assert wrap_deg(-190) == pytest.approx(170)
    assert wrap_deg(180) == 180


def test_filter_seeds_from_gravity():
    f = ComplementaryFilter(alpha=0.98)
    st = f.update(gravity_reading(10, -5), (0, 0, 0), 0.0)
    assert st.roll == pytest.approx(10, abs=1e-6)
    assert st.pitch == pytest.approx(-5, abs=1e-6)


def test_filter_converges_to_accel_when_gyro_silent():
    f = ComplementaryFilter(alpha=0.98)
    f.update(gravity_reading(0, 0), (0, 0, 0), 0.0)
    for _ in range(600):
        st = f.update(gravity_reading(15, 8), (0, 0, 0), 0.01)
    assert st.roll == pytest.approx(15, abs=0.2)
    assert st.pitch == pytest.approx(8, abs=0.2)


def test_yaw_integrates_and_sign():
    f = ComplementaryFilter()
    f.update(gravity_reading(0, 0), (0, 0, 0), 0.0)
    # Sensor gz positive = counter-clockwise from above = nose LEFT -> negative relative yaw.
    for _ in range(100):
        st = f.update(gravity_reading(0, 0), (0, 0, 10.0), 0.01)
    assert st.yaw == pytest.approx(-10.0, abs=0.05)
    f.reset_yaw()
    assert f.state.yaw == 0.0


def test_gyro_roll_rate_sign():
    f = ComplementaryFilter(alpha=0.9999)
    f.update(gravity_reading(0, 0), (0, 0, 0), 0.0)
    for _ in range(100):
        st = f.update(gravity_reading(0, 0), (20.0, 0, 0), 0.01)  # roll right at 20 deg/s
    assert st.roll > 15


def test_accel_gating_under_dynamic_acceleration():
    f = ComplementaryFilter(alpha=0.98)
    assert f.accel_weight(1.0) == 1.0
    assert f.accel_weight(1.5) == 0.0
    assert 0.0 < f.accel_weight(1.25) < 1.0


def test_large_dt_gap_does_not_integrate():
    f = ComplementaryFilter()
    f.update(gravity_reading(0, 0), (0, 0, 0), 0.0)
    st = f.update(gravity_reading(0, 0), (0, 0, 100.0), 5.0)
    assert st.yaw == 0.0


def test_calibration_computes_bias_and_level_offset():
    accel = [(0.2, -0.1, G + 0.3)] * 200
    gyro = [(0.5, -0.3, 0.1)] * 200
    cal = compute_calibration(accel, gyro, level=True, now=1.0)
    assert cal.gyro_bias_dps == pytest.approx((0.5, -0.3, 0.1))
    a, g = cal.apply((0.2, -0.1, G + 0.3), (0.5, -0.3, 0.1))
    assert a == pytest.approx((0.0, 0.0, G))
    assert g == pytest.approx((0.0, 0.0, 0.0))
    assert cal.is_calibrated
    assert ImuCalibration.from_dict(cal.to_dict()).gyro_bias_dps == pytest.approx(cal.gyro_bias_dps)


def test_calibration_rejects_motion():
    gyro = [(30.0 * math.sin(i / 5), 0.0, 0.0) for i in range(200)]
    accel = [(0.0, 0.0, G)] * 200
    with pytest.raises(CalibrationError, match="moved"):
        compute_calibration(accel, gyro, level=True, now=1.0)


def test_calibration_rejects_too_few_samples():
    with pytest.raises(CalibrationError):
        compute_calibration([(0, 0, G)] * 10, [(0, 0, 0)] * 10, level=False, now=1.0)


def test_yaw_drift_bound_grows_with_time():
    cal = ImuCalibration(gyro_noise_dps=0.1, samples=400, calibrated_at=1.0)
    assert cal.yaw_drift_bound_deg(100) > cal.yaw_drift_bound_deg(10) > 0
    assert ImuCalibration().yaw_drift_bound_deg(10) is None
