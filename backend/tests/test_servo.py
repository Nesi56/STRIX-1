import time

import pytest

from app.services.hub import EventLog
from app.services.servo_service import ServoError, ServoService, angle_to_pulse_us, clamp_angle, step_toward
from tests.conftest import make_settings, wait_for


def test_pulse_mapping():
    assert angle_to_pulse_us(0, 500, 2400) == 500
    assert angle_to_pulse_us(180, 500, 2400) == 2400
    assert angle_to_pulse_us(90, 500, 2400) == pytest.approx(1450)
    assert angle_to_pulse_us(-50, 500, 2400) == 500  # hardware range clamp
    assert angle_to_pulse_us(500, 500, 2400) == 2400


def test_clamp_and_step():
    assert clamp_angle(10, 30, 150) == 30
    assert clamp_angle(170, 30, 150) == 150
    assert step_toward(90, 100, 3) == 93
    assert step_toward(90, 80, 3) == 87
    assert step_toward(90, 91, 3) == 91


@pytest.fixture
def servo(tmp_path):
    s = make_settings(tmp_path, servo_min_angle=40, servo_max_angle=140, servo_max_speed_dps=100)
    svc = ServoService(s, EventLog())
    svc.start()
    yield svc
    svc.stop()


def test_disarmed_by_default_rejects_commands(servo):
    assert servo.armed is False
    with pytest.raises(ServoError, match="disabled"):
        servo.set_target(100)


def test_commands_are_clamped_to_safe_limits(servo):
    servo.arm()
    st = servo.set_target(175)
    assert st["target_angle"] == 140 and st["clamped"] is True
    st = servo.set_target(0)
    assert st["target_angle"] == 40


def test_motion_is_rate_limited(servo):
    servo.arm()
    assert servo.current == 90  # first arm goes to center
    t0 = time.monotonic()
    servo.set_target(140)
    time.sleep(0.2)
    moved = servo.current - 90
    elapsed = time.monotonic() - t0
    assert 0 < moved <= 100 * elapsed + 3  # never faster than max_speed_dps
    wait_for(lambda: servo.current == 140, timeout=2)


def test_disarm_stops_motion(servo):
    servo.arm()
    servo.set_target(40)
    time.sleep(0.1)
    servo.disarm()
    frozen = servo.current
    time.sleep(0.2)
    assert servo.current == frozen
    assert servo.target == frozen


def test_limit_validation(servo):
    with pytest.raises(ServoError):
        servo.update_limits(120, 60, 50)
    with pytest.raises(ServoError):
        servo.update_limits(-5, 60, 50)
    with pytest.raises(ServoError):
        servo.update_limits(10, 60, 1000)
    st = servo.update_limits(60, 120, 45, 90)
    assert st["limits"]["min_angle"] == 60 and st["limits"]["max_speed_dps"] == 45


def test_limits_persist(tmp_path):
    s = make_settings(tmp_path)
    a = ServoService(s, EventLog())
    a.update_limits(50, 130, 30, 90)
    b = ServoService(s, EventLog())
    assert (b.min_angle, b.max_angle, b.max_speed_dps) == (50, 130, 30)


def test_disabled_servo(tmp_path):
    svc = ServoService(make_settings(tmp_path, servo_backend="disabled"), EventLog())
    svc.start()
    assert svc.state()["status"]["state"] == "disabled"
    with pytest.raises(ServoError):
        svc.arm()


def test_settings_reject_inverted_limits(tmp_path):
    with pytest.raises(Exception):
        make_settings(tmp_path, servo_min_angle=150, servo_max_angle=30)
