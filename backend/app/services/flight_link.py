"""Link to a flight controller over MAVLink: PX4 or ArduPilot, real or simulated (PX4 SITL).

What it does
------------
* Reads telemetry: attitude, raw IMU, GPS, battery, armed state / flight mode and landed state.
* In simulation it stands in for the MPU6050: the vehicle IMU feeds STRIX-1's own orientation filter.
* Points the camera gimbal (MAVLink gimbal protocol v2) when the "servo" is a gimbal.

What it never does
------------------
STRIX-1 does not fly the aircraft. Every message this module sends goes through ``FlightLink._send``,
which allows only the message types in ``ALLOWED_MESSAGES`` and the commands in ``ALLOWED_COMMANDS``
(camera-gimbal pointing, the gimbal-control handshake and telemetry rates). Anything else, such as arming,
take-off, flight modes, setpoints, missions or RC overrides, raises ``ForbiddenMavlink`` before it reaches
the wire. tests/test_flight_link.py checks this, and tests/test_safety_static.py keeps every other module
from talking MAVLink directly.

Honesty: positions are reported only with a 3D GPS fix, battery only when the flight controller reports
it, and everything from a simulator carries ``synthetic: true``.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections import deque
from typing import Any, Callable

from app.services.base import DeviceState, DeviceStatus, StoppableThread
from app.services.hub import EventLog

log = logging.getLogger(__name__)

# PX4 and ArduPilot speak MAVLink 2, and gimbal protocol v2 messages (id > 255) only exist there.
os.environ.setdefault("MAVLINK20", "1")

# ---------------------------------------------------------------- safety allow-list
ALLOWED_COMMANDS: dict[int, str] = {
    511: "SET_MESSAGE_INTERVAL",  # how often the FC sends telemetry we read
    1000: "DO_GIMBAL_MANAGER_PITCHYAW",  # point the camera
    1001: "DO_GIMBAL_MANAGER_CONFIGURE",  # take / release control of the camera gimbal
}
ALLOWED_MESSAGES = frozenset({"HEARTBEAT", "COMMAND_LONG"})

# Camera gimbal: hold roll and pitch level to the horizon (stabilised), yaw follows the drone.
GIMBAL_FLAGS_STABILISED = 4 | 8  # GIMBAL_MANAGER_FLAGS_ROLL_LOCK | GIMBAL_MANAGER_FLAGS_PITCH_LOCK

STREAM_RATES_HZ = {  # message id -> rate requested from the FC
    105: 100.0,  # HIGHRES_IMU
    30: 50.0,  # ATTITUDE
    33: 10.0,  # GLOBAL_POSITION_INT
    24: 5.0,  # GPS_RAW_INT
    74: 5.0,  # VFR_HUD
    147: 1.0,  # BATTERY_STATUS
    1: 1.0,  # SYS_STATUS
    245: 1.0,  # EXTENDED_SYS_STATE
    285: 10.0,  # GIMBAL_DEVICE_ATTITUDE_STATUS
}

LANDED = {0: None, 1: "on ground", 2: "in air", 3: "taking off", 4: "landing"}
AUTOPILOTS = {3: "ArduPilot", 12: "PX4"}
VEHICLE_TYPES = {1: "fixed wing", 2: "quadrotor", 4: "helicopter", 10: "rover", 13: "hexarotor", 14: "octorotor",
                 19: "VTOL", 20: "VTOL", 21: "VTOL", 22: "VTOL"}
FIX = {0: "no GPS", 1: "no fix", 2: "2D fix", 3: "3D fix", 4: "DGPS", 5: "RTK float", 6: "RTK fixed", 7: "static", 8: "PPP"}
UINT16_MAX = 65535
ACK = {0: "accepted", 1: "temporarily rejected", 2: "denied", 3: "unsupported", 4: "failed", 5: "in progress",
       6: "cancelled"}


class ForbiddenMavlink(RuntimeError):
    """Raised when code tries to send anything other than the allowed camera/telemetry messages."""


def check_outgoing(msg_type: str, command: int | None = None) -> None:
    """The single gate every outgoing MAVLink message passes."""
    if msg_type not in ALLOWED_MESSAGES:
        raise ForbiddenMavlink(f"STRIX-1 never sends {msg_type}: it does not control the aircraft")
    if msg_type == "COMMAND_LONG" and command not in ALLOWED_COMMANDS:
        raise ForbiddenMavlink(f"STRIX-1 never sends MAVLink command {command}: only camera-gimbal pointing "
                               "and telemetry-rate requests are allowed")


def validate_url(url: str) -> str:
    """Accept network links and serial ports only (no log files, no websockets)."""
    u = url.strip()
    if u.startswith(("udpin:", "udp:", "tcp:", "udpout:")):
        return u
    dev = u.split(",", 1)[0]
    if dev.startswith("/dev/") or dev.upper().startswith("COM"):
        return u
    raise ValueError(f"unsupported MAVLink link {url!r}: use udpin:HOST:PORT, tcp:HOST:PORT or /dev/ttyX,BAUD")


def default_connector(url: str):
    from pymavlink import mavutil  # type: ignore[import-not-found]

    dev, _, baud = url.partition(",")
    kwargs: dict[str, Any] = {"source_system": 255, "source_component": 191, "dialect": "common"}
    if baud:
        kwargs["baud"] = int(baud)
    return mavutil.mavlink_connection(dev, **kwargs)


def quat_to_euler_deg(q: list[float]) -> tuple[float, float, float]:
    """(roll, pitch, yaw) in degrees from a w,x,y,z quaternion (aerospace convention)."""
    w, x, y, z = q
    roll = math.degrees(math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y)))
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x)))))
    yaw = math.degrees(math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))
    return roll, pitch, yaw


def frd_to_flu_imu(acc: tuple[float, float, float], gyro_rad: tuple[float, float, float]
                   ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """MAVLink IMU (FRD body frame, m/s², rad/s) → STRIX-1 IMU input (X fwd, Y left, Z up; m/s², °/s)."""
    ax, ay, az = acc
    gx, gy, gz = gyro_rad
    d = math.degrees
    return (ax, -ay, -az), (d(gx), -d(gy), -d(gz))


class FlightLink:
    """Owns the MAVLink connection; one reader thread; telemetry snapshots for the rest of STRIX-1."""

    def __init__(self, url: str, events: EventLog, *, synthetic: bool, label: str, gimbal: bool = False,
                 connector: Callable[[str], Any] | None = None) -> None:
        self.url = validate_url(url)
        self.events = events
        self.synthetic = synthetic
        self.label = label
        self.gimbal_enabled = gimbal
        self.status = DeviceStatus("flight")
        self.status.backend = "mavlink"
        self.status.synthetic = synthetic
        self._connector = connector or default_connector
        self._conn: Any = None
        self._thread: StoppableThread | None = None
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._imu_cond = threading.Condition()
        self._imu: tuple[tuple, tuple, float | None] | None = None
        self._imu_seq = 0
        self._imu_times: deque[float] = deque(maxlen=200)
        self._target: int | None = None
        self._last_hb = 0.0  # monotonic, autopilot heartbeat
        self._last_msg = 0.0
        self._msg_times: deque[float] = deque(maxlen=400)
        self._streams_requested = 0.0
        self._next_hb = 0.0
        self._was_connected = False
        self._statustext: dict[str, float] = {}
        self._last_highres = 0.0
        # telemetry
        self.hb: dict[str, Any] = {}
        self.attitude: dict[str, float] | None = None
        self.gps: dict[str, Any] | None = None
        self.global_pos: dict[str, Any] | None = None
        self.vfr: dict[str, float] | None = None
        self.battery_status: dict[str, Any] | None = None
        self.sys_status: dict[str, Any] | None = None
        self.landed: str | None = None
        # gimbal
        self._gimbal_target: float | None = None
        self._gimbal_sent: float | None = None
        self._gimbal_sent_at = 0.0
        self._gimbal_control = "none"  # none | requested | yes | refused
        self._gimbal_ack: str | None = None
        self.gimbal_measured: dict[str, float] | None = None
        self.sent_commands: deque[str] = deque(maxlen=50)  # names of what we sent, for the API/tests

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.status.set(DeviceState.STARTING, f"waiting for {self.label} on {self.url}")
        self._thread = StoppableThread(self._run, "flight-link")
        self._thread.start()

    def stop(self) -> None:
        if self._thread:
            self._thread.stop()
        if self._conn is not None and self._gimbal_control == "yes":
            try:
                self._release_gimbal()
            except Exception:
                pass
        self._close()

    def _close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    @property
    def connected(self) -> bool:
        """Linked once the autopilot has identified itself, while it keeps talking. Heartbeats are 1 Hz in
        *simulated* time, so a slow simulator (no GPU) stretches them: recent telemetry counts too."""
        if self._last_hb <= 0:
            return False
        now = time.monotonic()
        return now - self._last_hb < 6.0 or now - self._last_msg < 2.0

    # ------------------------------------------------------------ sending (the only way out)
    def _send(self, msg: Any) -> None:
        kind = msg.get_type()
        check_outgoing(kind, getattr(msg, "command", None) if kind == "COMMAND_LONG" else None)
        conn = self._conn
        if conn is None:
            return
        with self._send_lock:
            conn.mav.send(msg)
        self.sent_commands.append(ALLOWED_COMMANDS.get(getattr(msg, "command", -1), kind))

    def _command(self, command: int, *params: float) -> None:
        conn = self._conn
        if conn is None or self._target is None:
            return
        p = list(params) + [0.0] * (7 - len(params))
        self._send(conn.mav.command_long_encode(self._target, 1, command, 0, *p))

    def _heartbeat(self) -> None:
        conn = self._conn
        if conn is None or self._target is None:
            return
        # MAV_TYPE_ONBOARD_CONTROLLER, MAV_AUTOPILOT_INVALID, no modes, MAV_STATE_ACTIVE
        self._send(conn.mav.heartbeat_encode(18, 8, 0, 0, 4))

    def _request_streams(self) -> None:
        for msg_id, hz in STREAM_RATES_HZ.items():
            self._command(511, float(msg_id), 1e6 / hz)

    # ------------------------------------------------------------ gimbal
    def set_gimbal_pitch(self, pitch_deg: float) -> None:
        """Point the camera: pitch relative to the horizon (+ up). Sent at most ~10×/s."""
        if not self.gimbal_enabled:
            raise ForbiddenMavlink("gimbal control is off (STRIX_MAVLINK_GIMBAL)")
        self._gimbal_target = max(-90.0, min(45.0, float(pitch_deg)))

    def _service_gimbal(self, now: float) -> None:
        if not self.gimbal_enabled or self._gimbal_target is None or self._target is None:
            return
        if self._gimbal_control == "none" or (self._gimbal_control == "refused" and now - self._gimbal_sent_at > 5.0):
            # Ask the gimbal manager to let this computer point the camera: name our own system/component
            # explicitly (PX4 reads -2 "set itself" as the autopilot taking control).
            self._gimbal_control = "requested"
            self._gimbal_sent_at = now
            me_sys, me_comp = self._conn.mav.srcSystem, self._conn.mav.srcComponent
            self._command(1001, float(me_sys), float(me_comp), -1.0, -1.0, 0.0, 0.0, 0.0)
            return
        if self._gimbal_control == "refused" or (self._gimbal_control == "requested" and now - self._gimbal_sent_at < 0.5):
            return  # refused: retry the handshake later; requested: give the gimbal manager a moment to answer
        changed = self._gimbal_sent is None or abs(self._gimbal_target - self._gimbal_sent) > 0.05
        if (changed and now - self._gimbal_sent_at >= 0.1) or now - self._gimbal_sent_at >= 1.0:
            self._gimbal_sent, self._gimbal_sent_at = self._gimbal_target, now
            # param1 pitch (deg, + up), param2 yaw (deg, + right; 0 = straight ahead), rates NaN = not used
            self._command(1000, self._gimbal_target, 0.0, math.nan, math.nan, float(GIMBAL_FLAGS_STABILISED), 0.0, 0.0)

    def _release_gimbal(self) -> None:
        self._command(1001, -3.0, -3.0, -1.0, -1.0, 0.0, 0.0, 0.0)

    def gimbal_state(self) -> dict[str, Any]:
        return {
            "enabled": self.gimbal_enabled,
            "control": self._gimbal_control,
            "target_pitch_deg": self._gimbal_target,
            "last_ack": self._gimbal_ack,
            "measured": self.gimbal_measured,
        }

    # ------------------------------------------------------------ reading
    def _run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            if self._conn is None:
                try:
                    self._conn = self._connector(self.url)
                except Exception as exc:
                    self.status.set(DeviceState.DISCONNECTED, f"cannot open {self.url}: {exc}")
                    stop.wait(2.0)
                    continue
            try:
                msg = self._conn.recv_match(blocking=True, timeout=0.2)
            except Exception as exc:
                self.status.set(DeviceState.DISCONNECTED, f"link error: {exc}")
                self._close()
                stop.wait(1.0)
                continue
            now = time.monotonic()
            if msg is not None:
                try:
                    self._handle(msg, now)
                except Exception as exc:  # a malformed message must never stop the link
                    self.status.error = f"bad message: {exc}"
            try:
                self._housekeeping(now)
            except ForbiddenMavlink as exc:  # a bug, never expected: blocked, logged, link keeps reading
                log.error("blocked outgoing MAVLink: %s", exc)
                self.status.set(DeviceState.ERROR, str(exc))
            except Exception as exc:
                self.status.error = f"send failed: {exc}"

    def _housekeeping(self, now: float) -> None:
        connected = self.connected
        if connected and not self._was_connected:
            self._was_connected = True
            self._streams_requested = 0.0
            self.status.set(DeviceState.SYNTHETIC if self.synthetic else DeviceState.OK)
            self.events.add("info", "flight", f"{self.label} connected ({self.autopilot_name()}, system {self._target})")
        elif not connected and self._was_connected:
            self._was_connected = False
            self._gimbal_control = "none"
            self.status.set(DeviceState.DISCONNECTED, f"no messages from {self.label}")
            self.events.add("warning", "flight", f"{self.label} lost (no messages)")
        if not connected:
            return
        if self._streams_requested == 0.0 or now - self._streams_requested > 30.0:
            self._streams_requested = now
            self._request_streams()
        if now >= self._next_hb:
            self._next_hb = now + 1.0
            self._heartbeat()
        self._service_gimbal(now)

    def _handle(self, msg: Any, now: float) -> None:
        kind = msg.get_type()
        if kind == "BAD_DATA":
            return
        src_sys, src_comp = msg.get_srcSystem(), msg.get_srcComponent()
        if kind == "HEARTBEAT":
            # The autopilot itself: component 1, a real autopilot type (not a GCS, gimbal or camera).
            if src_comp == 1 and msg.autopilot != 8 and (self._target is None or src_sys == self._target):
                if self._target is None:
                    self._target = src_sys
                    if self._conn is not None:
                        self._conn.mav.srcSystem = src_sys  # we are a component of this vehicle
                        self._conn.mav.srcComponent = 191  # MAV_COMP_ID_ONBOARD_COMPUTER
                self._last_hb = now
                self.hb = {
                    "autopilot": msg.autopilot, "type": msg.type, "armed": bool(msg.base_mode & 128),
                    "mode": self._mode_name(msg), "system_status": msg.system_status,
                }
            return
        if self._target is None or src_sys != self._target:
            return
        self._last_msg = now
        self._msg_times.append(now)
        with self._lock:
            if kind == "ATTITUDE":
                r, p, y = (math.degrees(v) for v in (msg.roll, msg.pitch, msg.yaw))
                self.attitude = {"roll": r, "pitch": p, "yaw": y, "heading": y % 360.0, "t": now}
            elif kind == "HIGHRES_IMU":
                if getattr(msg, "id", 0) in (0, None):
                    self._last_highres = now
                    self._push_imu((msg.xacc, msg.yacc, msg.zacc), (msg.xgyro, msg.ygyro, msg.zgyro), msg.temperature)
            elif kind == "SCALED_IMU" and now - self._last_highres > 1.0:
                g = 9.80665 / 1000.0
                self._push_imu((msg.xacc * g, msg.yacc * g, msg.zacc * g),
                               (msg.xgyro / 1000.0, msg.ygyro / 1000.0, msg.zgyro / 1000.0),
                               getattr(msg, "temperature", 0) / 100.0 or None)
            elif kind == "GPS_RAW_INT":
                h_acc = getattr(msg, "h_acc", 0)
                self.gps = {"fix_type": msg.fix_type, "satellites": msg.satellites_visible if msg.satellites_visible != 255 else None,
                            "hdop": msg.eph / 100.0 if msg.eph != UINT16_MAX else None,
                            "h_acc_m": h_acc / 1000.0 if h_acc else None, "t": now}
            elif kind == "GLOBAL_POSITION_INT":
                self.global_pos = {
                    "lat": msg.lat / 1e7, "lon": msg.lon / 1e7, "alt_msl_m": msg.alt / 1000.0,
                    "alt_rel_m": msg.relative_alt / 1000.0, "vn": msg.vx / 100.0, "ve": msg.vy / 100.0,
                    "vd": msg.vz / 100.0, "hdg": msg.hdg / 100.0 if msg.hdg != UINT16_MAX else None, "t": now,
                }
            elif kind == "VFR_HUD":
                self.vfr = {"groundspeed_ms": msg.groundspeed, "climb_ms": msg.climb, "heading": float(msg.heading), "t": now}
            elif kind == "BATTERY_STATUS" and msg.id == 0:
                # -1 (or PX4's -1 A = -100 cA) means "not measured"
                self.battery_status = {"remaining": msg.battery_remaining,
                                       "current_a": msg.current_battery / 100.0 if msg.current_battery not in (-1, -100) else None,
                                       "t": now}
            elif kind == "SYS_STATUS":
                self.sys_status = {
                    "voltage_v": msg.voltage_battery / 1000.0 if msg.voltage_battery not in (0, UINT16_MAX) else None,
                    "current_a": msg.current_battery / 100.0 if msg.current_battery not in (-1, -100) else None,
                    "remaining": msg.battery_remaining, "t": now,
                }
            elif kind == "EXTENDED_SYS_STATE":
                self.landed = LANDED.get(msg.landed_state)
            elif kind == "GIMBAL_DEVICE_ATTITUDE_STATUS":
                roll, pitch, yaw = quat_to_euler_deg(list(msg.q))
                self.gimbal_measured = {"pitch_deg": pitch, "roll_deg": roll, "yaw_deg": yaw, "t": now}
        if kind == "COMMAND_ACK":
            name = ALLOWED_COMMANDS.get(msg.command)
            if name:
                result = ACK.get(msg.result, str(msg.result))
                if msg.command == 1001:
                    self._gimbal_control = "yes" if msg.result == 0 else "refused"
                if msg.command in (1000, 1001):
                    self._gimbal_ack = f"{name}: {result}"
        elif kind == "STATUSTEXT":
            self._statustext_event(msg, now)

    def _push_imu(self, acc: tuple, gyro_rad: tuple, temp: float | None) -> None:
        accel, gyro = frd_to_flu_imu(acc, gyro_rad)
        with self._imu_cond:
            self._imu = (accel, gyro, temp)
            self._imu_seq += 1
            self._imu_times.append(time.monotonic())
            self._imu_cond.notify_all()

    def wait_imu(self, last_seq: int, timeout: float = 0.25) -> tuple[int, tuple] | None:
        """Next IMU sample after ``last_seq`` (blocks up to ``timeout``), or None."""
        with self._imu_cond:
            if self._imu_seq == last_seq or self._imu is None:
                self._imu_cond.wait(timeout)
            if self._imu is None or self._imu_seq == last_seq:
                return None
            return self._imu_seq, self._imu

    def _statustext_event(self, msg: Any, now: float) -> None:
        text = (msg.text or "").strip()
        if not text or msg.severity > 4:  # EMERGENCY..WARNING only
            return
        if now - self._statustext.get(text, -1e9) < 30.0:
            return
        self._statustext[text] = now
        self.events.add("warning" if msg.severity >= 3 else "error", "flight", f"{self.autopilot_name()}: {text}")

    @staticmethod
    def _mode_name(msg: Any) -> str | None:
        try:
            from pymavlink import mavutil  # type: ignore[import-not-found]

            return mavutil.mode_string_v10(msg)
        except Exception:
            return None

    # ------------------------------------------------------------ views
    def autopilot_name(self) -> str:
        return AUTOPILOTS.get(self.hb.get("autopilot", -1), "autopilot")

    @staticmethod
    def _rate(times: deque[float], window: float = 2.0) -> float | None:
        if len(times) < 2:
            return None
        now = time.monotonic()
        recent = [t for t in times if now - t <= window]
        return round(len(recent) / window, 1) if len(recent) >= 2 else 0.0

    def position(self) -> dict[str, Any]:
        """Position only with a 3D GPS fix reported by the flight controller."""
        now = time.monotonic()
        with self._lock:
            gps, pos = self.gps, self.global_pos
        if not self.connected:
            return {"available": False, "message": f"no link to {self.label}", "source": None}
        if gps is None:
            return {"available": False, "message": "flight controller reports no GPS", "source": None}
        if gps["fix_type"] < 3:
            return {"available": False, "message": f"GPS: {FIX.get(gps['fix_type'], 'no fix')}", "source": None,
                    "satellites": gps.get("satellites")}
        if pos is None or now - pos["t"] > 3.0:
            return {"available": False, "message": "no recent position from the flight controller", "source": None}
        source = f"{self.label} GPS" + (" (simulated)" if self.synthetic else "")
        return {
            "available": True, "source": source, "synthetic": self.synthetic,
            "lat": round(pos["lat"], 7), "lon": round(pos["lon"], 7),
            "alt_msl_m": round(pos["alt_msl_m"], 2), "alt_rel_m": round(pos["alt_rel_m"], 2),
            "fix": FIX.get(gps["fix_type"]), "satellites": gps.get("satellites"), "hdop": gps.get("hdop"),
            "h_acc_m": gps.get("h_acc_m"), "age_s": round(now - pos["t"], 2),
        }

    def battery(self) -> dict[str, Any] | None:
        """Battery as the flight controller reports it (None if it reports nothing)."""
        with self._lock:
            bs, ss = self.battery_status, self.sys_status
        if not self.connected or (bs is None and ss is None):
            return None
        remaining = bs["remaining"] if bs and bs["remaining"] >= 0 else (ss["remaining"] if ss and ss["remaining"] >= 0 else None)
        voltage = ss["voltage_v"] if ss else None
        current = (bs or {}).get("current_a") if bs and bs.get("current_a") is not None else (ss or {}).get("current_a")
        if remaining is None and voltage is None:
            return None
        return {
            "available": remaining is not None, "percent": remaining, "voltage_v": voltage, "current_a": current,
            "source": f"{self.label}" + (" (simulated battery)" if self.synthetic else " (flight controller)"),
            "synthetic": self.synthetic,
            "reason": None if remaining is not None else "flight controller reports voltage only",
        }

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            att, vfr = self.attitude, self.vfr
        hb = self.hb
        connected = self.connected
        return {
            "status": self.status.to_dict(),
            "label": self.label,
            "synthetic": self.synthetic,
            "url": self.url,
            "connected": connected,
            "autopilot": self.autopilot_name() if hb else None,
            "vehicle": VEHICLE_TYPES.get(hb.get("type", -1)) if hb else None,
            "system_id": self._target,
            "armed": hb.get("armed") if connected else None,
            "mode": hb.get("mode") if connected else None,
            "landed": self.landed if connected else None,
            "attitude": ({k: round(v, 2) for k, v in att.items() if k != "t"} if att and now - att["t"] < 2.0 else None),
            "velocity": ({"groundspeed_ms": round(vfr["groundspeed_ms"], 2), "climb_ms": round(vfr["climb_ms"], 2)}
                         if vfr and now - vfr["t"] < 3.0 else None),
            "position": self.position(),
            "battery": self.battery(),
            "gimbal": self.gimbal_state() if self.gimbal_enabled else None,
            "imu_rate_hz": self._rate(self._imu_times),
            "messages_per_s": self._rate(self._msg_times),
            "last_message_age_s": round(now - self._last_msg, 2) if self._last_msg else None,
            "sent": list(self.sent_commands)[-5:],
        }
