"""Flight-controller link: telemetry parsing against a fake PX4, and the rule that STRIX-1 never flies."""

from __future__ import annotations

import math

import pytest

from app.services.flight_link import (
    ALLOWED_COMMANDS,
    FlightLink,
    ForbiddenMavlink,
    check_outgoing,
    frd_to_flu_imu,
    quat_to_euler_deg,
    validate_url,
)
from app.services.hub import EventLog
from tests.conftest import wait_for
from tests.fake_px4 import M, FakePX4, free_udp_port

# Things STRIX-1 must never send: arming, take-off, landing, waypoints, modes, setpoints, RC, missions.
FORBIDDEN_COMMANDS = [
    M.MAV_CMD_COMPONENT_ARM_DISARM, M.MAV_CMD_NAV_TAKEOFF, M.MAV_CMD_NAV_LAND, M.MAV_CMD_NAV_WAYPOINT,
    M.MAV_CMD_DO_SET_MODE, M.MAV_CMD_NAV_RETURN_TO_LAUNCH, M.MAV_CMD_DO_REPOSITION, M.MAV_CMD_MISSION_START,
    M.MAV_CMD_DO_CHANGE_SPEED, M.MAV_CMD_CONDITION_YAW, M.MAV_CMD_DO_SET_SERVO, M.MAV_CMD_DO_MOTOR_TEST,
    M.MAV_CMD_DO_FLIGHTTERMINATION, M.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN, M.MAV_CMD_DO_SET_ACTUATOR,
]
FORBIDDEN_MESSAGES = ["SET_POSITION_TARGET_LOCAL_NED", "SET_POSITION_TARGET_GLOBAL_INT", "SET_ATTITUDE_TARGET",
                      "MANUAL_CONTROL", "RC_CHANNELS_OVERRIDE", "SET_MODE", "MISSION_ITEM_INT", "COMMAND_INT",
                      "PARAM_SET", "SET_ACTUATOR_CONTROL_TARGET"]


def test_allow_list_is_camera_and_telemetry_only():
    assert ALLOWED_COMMANDS == {511: "SET_MESSAGE_INTERVAL", 1000: "DO_GIMBAL_MANAGER_PITCHYAW",
                                1001: "DO_GIMBAL_MANAGER_CONFIGURE"}
    assert M.MAV_CMD_DO_GIMBAL_MANAGER_PITCHYAW == 1000 and M.MAV_CMD_DO_GIMBAL_MANAGER_CONFIGURE == 1001
    for cmd in FORBIDDEN_COMMANDS:
        with pytest.raises(ForbiddenMavlink):
            check_outgoing("COMMAND_LONG", cmd)
    for name in FORBIDDEN_MESSAGES:
        with pytest.raises(ForbiddenMavlink):
            check_outgoing(name)
    check_outgoing("HEARTBEAT")
    for cmd in ALLOWED_COMMANDS:
        check_outgoing("COMMAND_LONG", cmd)


class _Recorder:
    """A connection whose encoder is real MAVLink but whose wire is a list."""

    def __init__(self) -> None:
        from pymavlink import mavutil

        self.written: list[bytes] = []
        self.mav = mavutil.mavlink.MAVLink(self, srcSystem=1, srcComponent=191)

    def write(self, buf: bytes) -> None:
        self.written.append(buf)

    def recv_match(self, **_):
        return None

    def close(self) -> None:
        pass


def test_send_gate_blocks_real_encoded_flight_messages():
    link = FlightLink("udpin:127.0.0.1:9", EventLog(), synthetic=True, label="test", connector=lambda url: _Recorder())
    link._conn = rec = _Recorder()
    link._target = 1
    mav = rec.mav
    blocked = [
        mav.command_long_encode(1, 1, M.MAV_CMD_COMPONENT_ARM_DISARM, 0, 1, 0, 0, 0, 0, 0, 0),
        mav.command_long_encode(1, 1, M.MAV_CMD_NAV_TAKEOFF, 0, 0, 0, 0, 0, 0, 0, 10),
        mav.set_position_target_local_ned_encode(0, 1, 1, 1, 0, 1.0, 2.0, -3.0, 0, 0, 0, 0, 0, 0, 0, 0),
        mav.manual_control_encode(1, 0, 0, 500, 0, 0),
        mav.rc_channels_override_encode(1, 1, *([1500] * 8)),
        mav.set_mode_encode(1, 1, 0),
    ]
    for msg in blocked:
        with pytest.raises(ForbiddenMavlink):
            link._send(msg)
    assert rec.written == [], "nothing forbidden may reach the wire"
    link._send(mav.command_long_encode(1, 1, 1000, 0, -20, 0, math.nan, math.nan, 12, 0, 0))
    assert len(rec.written) == 1


def test_frames_and_math():
    acc, gyro = frd_to_flu_imu((0.0, 0.0, -9.81), (0.0, 0.1, -0.2))
    assert acc == (0.0, -0.0, 9.81)  # level: +1 g on Z up, like an MPU6050 lying flat
    assert gyro[1] == pytest.approx(-math.degrees(0.1)) and gyro[2] == pytest.approx(math.degrees(0.2))
    r, p, y = quat_to_euler_deg([math.cos(math.radians(15)), 0.0, math.sin(math.radians(15)), 0.0])
    assert p == pytest.approx(30.0, abs=1e-6) and abs(r) < 1e-6 and abs(y) < 1e-6
    assert validate_url("udpin:127.0.0.1:14541") and validate_url("/dev/serial0,921600")
    for bad in ("flight.tlog", "ws://x", "csv:log.csv", "log.bin"):
        with pytest.raises(ValueError):
            validate_url(bad)


@pytest.fixture
def px4_link():
    port = free_udp_port()
    link = FlightLink(f"udpin:127.0.0.1:{port}", EventLog(), synthetic=True, label="PX4 SITL #1", gimbal=True)
    link.start()
    fake = FakePX4(port).start()
    yield link, fake
    fake.stop()
    link.stop()


def test_reads_px4_telemetry(px4_link):
    link, fake = px4_link
    assert wait_for(lambda: link.connected and link.position().get("available") and link.battery(), timeout=8)
    snap = link.snapshot()
    assert snap["autopilot"] == "PX4" and snap["vehicle"] == "quadrotor" and snap["system_id"] == 2
    assert snap["armed"] is True and snap["mode"] == "POSCTL" and snap["landed"] == "in air"
    assert snap["attitude"]["heading"] == pytest.approx(92.0, abs=0.01)
    pos = snap["position"]
    assert pos["lat"] == pytest.approx(47.3977418) and pos["lon"] == pytest.approx(8.5455939)
    assert pos["alt_rel_m"] == pytest.approx(12.345, abs=0.01) and pos["fix"] == "3D fix" and pos["satellites"] == 12
    assert pos["synthetic"] is True and "simulated" in pos["source"]
    bat = snap["battery"]
    assert bat["percent"] == 77 and bat["voltage_v"] == pytest.approx(15.8) and bat["synthetic"] is True
    assert snap["velocity"]["groundspeed_ms"] == pytest.approx(3.2, abs=1e-3)
    # IMU arrives converted to STRIX-1's frame (Z up): level = +9.81 on Z.
    got = link.wait_imu(0, timeout=1.0)
    assert got is not None
    accel, gyro, temp = got[1]
    assert accel[2] == pytest.approx(9.81) and gyro[2] == pytest.approx(-math.degrees(0.1)) and temp == pytest.approx(31.5)
    # It asked for telemetry rates and announces itself as an onboard computer of the same vehicle.
    assert wait_for(lambda: fake.commands(511) and any(m.get_type() == "HEARTBEAT" for m in fake.received), timeout=4)
    hb = next(m for m in fake.received if m.get_type() == "HEARTBEAT")
    assert hb.get_srcSystem() == 2 and hb.get_srcComponent() == 191 and hb.type == M.MAV_TYPE_ONBOARD_CONTROLLER


def test_points_gimbal_and_nothing_else(px4_link):
    link, fake = px4_link
    assert wait_for(lambda: link.connected, timeout=8)
    link.set_gimbal_pitch(-30.0)
    assert wait_for(lambda: fake.commands(1000), timeout=5)
    cfg = fake.commands(1001)[0]
    assert (cfg.param1, cfg.param2) == (2.0, 191.0), "asks for gimbal control for this computer (vehicle 2, onboard computer)"
    cmd = fake.commands(1000)[-1]
    assert cmd.param1 == pytest.approx(-30.0) and cmd.param2 == 0.0 and int(cmd.param5) == 12
    assert link.gimbal_state()["control"] == "yes"
    link.set_gimbal_pitch(-90.0 - 50)  # clamped to the gimbal's range
    assert wait_for(lambda: fake.commands(1000)[-1].param1 == pytest.approx(-90.0), timeout=3)
    sent = {m.command for m in fake.received if m.get_type() == "COMMAND_LONG"}
    assert sent <= set(ALLOWED_COMMANDS), f"sent something other than camera/telemetry commands: {sent}"
    assert {m.get_type() for m in fake.received} <= {"HEARTBEAT", "COMMAND_LONG"}


def test_no_position_without_3d_fix():
    port = free_udp_port()
    link = FlightLink(f"udpin:127.0.0.1:{port}", EventLog(), synthetic=False, label="Flight controller")
    link.start()
    fake = FakePX4(port, fix_type=2, battery=False).start()
    try:
        assert wait_for(lambda: link.connected and link.gps is not None, timeout=8)
        pos = link.position()
        assert pos["available"] is False and pos["message"] == "GPS: 2D fix" and "lat" not in pos
        assert link.battery() is None, "no battery reported → no battery shown (never estimated)"
        with pytest.raises(ForbiddenMavlink):
            link.set_gimbal_pitch(10.0)  # gimbal control is off on this link
    finally:
        fake.stop()
        link.stop()
    assert not any(m.get_type() == "COMMAND_LONG" and m.command in (1000, 1001) for m in fake.received)
