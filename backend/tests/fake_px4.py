"""A tiny stand-in for PX4 SITL: speaks real MAVLink 2 over UDP, records what STRIX-1 sends it."""

from __future__ import annotations

import math
import os
import socket
import threading
import time

os.environ.setdefault("MAVLINK20", "1")

from pymavlink import mavutil  # noqa: E402

mavutil.set_dialect("common")
M = mavutil.mavlink

POSCTL = 3 << 16  # PX4 custom mode: main mode 3 = POSCTL


def free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FakePX4:
    def __init__(self, port: int, sysid: int = 2, fix_type: int = 3, battery: bool = True, gimbal_ack: int = 0) -> None:
        self.conn = mavutil.mavlink_connection(f"udpout:127.0.0.1:{port}", source_system=sysid, source_component=1,
                                               dialect="common")
        self.sysid = sysid
        self.fix_type = fix_type
        self.battery = battery
        self.gimbal_ack = gimbal_ack
        self.received: list = []
        self.armed = True
        self._stop = threading.Event()
        self._threads = [threading.Thread(target=self._tx, daemon=True), threading.Thread(target=self._rx, daemon=True)]

    def start(self) -> "FakePX4":
        for t in self._threads:
            t.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(2)
        self.conn.close()

    def commands(self, command: int) -> list:
        return [m for m in list(self.received) if m.get_type() == "COMMAND_LONG" and m.command == command]

    def _tx(self) -> None:
        mav = self.conn.mav
        t0 = time.monotonic()
        n = 0
        while not self._stop.is_set():
            t = time.monotonic() - t0
            ms = int(t * 1000)
            if n % 50 == 0:
                base = 1 | (128 if self.armed else 0)  # custom mode enabled (+ armed)
                mav.heartbeat_send(M.MAV_TYPE_QUADROTOR, M.MAV_AUTOPILOT_PX4, base, POSCTL, M.MAV_STATE_ACTIVE)
                mav.sys_status_send(0, 0, 0, 500, 15800 if self.battery else 65535, 1210 if self.battery else -1,
                                    77 if self.battery else -1, 0, 0, 0, 0, 0, 0)
                if self.battery:
                    mav.battery_status_send(0, 0, 0, 32767, [3950, 3950, 3950, 3950] + [65535] * 6, 1210, -1, -1, 77)
                mav.extended_sys_state_send(0, M.MAV_LANDED_STATE_IN_AIR)
                mav.gps_raw_int_send(ms * 1000, self.fix_type, 473977418, 85455939, 488000, 80, 120, 300, 9000, 12)
                mav.vfr_hud_send(0.0, 3.2, 92, 45, 488.0, 0.4)
            if n % 10 == 0:
                mav.global_position_int_send(ms, 473977418, 85455939, 488000, 12345, 10, 320, -40, 9200)
                mav.attitude_send(ms, math.radians(2.0), math.radians(-3.0), math.radians(92.0), 0, 0, 0)
            # Level and still in FRD: specific force (0, 0, -g); a slow yaw rate of 0.1 rad/s.
            mav.highres_imu_send(ms * 1000, 0.0, 0.0, -9.81, 0.0, 0.0, 0.1, 0, 0, 0, 1013.0, 0, 0, 31.5, 0xFFFF)
            n += 1
            self._stop.wait(0.01)

    def _rx(self) -> None:
        while not self._stop.is_set():
            try:
                m = self.conn.recv_match(blocking=True, timeout=0.2)
            except OSError:
                return
            if m is None or m.get_type() == "BAD_DATA":
                continue
            self.received.append(m)
            if m.get_type() == "COMMAND_LONG" and m.command in (1000, 1001):
                result = self.gimbal_ack if m.command == 1001 else 0
                self.conn.mav.command_ack_send(m.command, result)
