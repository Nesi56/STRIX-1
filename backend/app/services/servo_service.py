"""Camera tilt servo (TowerPro SG90) control.

Scope and safety
----------------
* Only the one-axis **camera** servo is controlled. This module has no
  knowledge of, and no interface to, propulsion or flight control.
* Commands are clamped to operator-configurable safe limits that are always
  inside the 0–180° mechanical range.
* Motion is slew-rate limited (``STRIX_SERVO_MAX_SPEED_DPS``) by an internal
  50 Hz trajectory loop, so slider jumps become smooth moves.
* Control starts **disarmed** unless ``STRIX_SERVO_START_ARMED=true``; the
  operator can disarm at any time, which stops motion and releases the pulse.
* The SG90 has no position feedback: the reported angle is the *commanded*
  output, not a measurement. Because the true start position is unknown, the
  very first move after arming cannot be rate limited by software.

Power: the servo must be powered from a regulated external 5 V supply that
shares ground with the Pi. Never power it from a GPIO pin.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Protocol

from app.config.settings import Settings
from app.services.base import DeviceState, DeviceStatus, StoppableThread
from app.services.hub import EventLog

log = logging.getLogger(__name__)

HW_MIN_ANGLE = 0.0
HW_MAX_ANGLE = 180.0
LOOP_HZ = 50.0
PWM_PERIOD_NS = 20_000_000  # 50 Hz servo frame


class ServoError(RuntimeError):
    pass


def angle_to_pulse_us(angle: float, min_pulse_us: int, max_pulse_us: int) -> float:
    angle = max(HW_MIN_ANGLE, min(HW_MAX_ANGLE, angle))
    return min_pulse_us + (max_pulse_us - min_pulse_us) * (angle / HW_MAX_ANGLE)


def clamp_angle(angle: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, angle))


def step_toward(current: float, target: float, max_step: float) -> float:
    delta = target - current
    if abs(delta) <= max_step:
        return target
    return current + max_step * (1 if delta > 0 else -1)


class ServoBackend(Protocol):
    name: str
    synthetic: bool

    def write_pulse_us(self, pulse_us: float) -> None: ...

    def detach(self) -> None: ...

    def close(self) -> None: ...


class GpiozeroServoBackend:
    """gpiozero ``Servo`` (lgpio pin factory on Pi 5: software-timed PWM, some jitter)."""

    name = "gpiozero"
    synthetic = False

    def __init__(self, settings: Settings) -> None:
        from gpiozero import Servo  # type: ignore[import-not-found]

        self._min = settings.servo_min_pulse_us / 1e6
        self._max = settings.servo_max_pulse_us / 1e6
        self._servo = Servo(
            settings.servo_gpio,
            initial_value=None,  # no pulses until the operator commands a position
            min_pulse_width=self._min,
            max_pulse_width=self._max,
            frame_width=PWM_PERIOD_NS / 1e9,
        )

    def write_pulse_us(self, pulse_us: float) -> None:
        width = pulse_us / 1e6
        value = (width - self._min) / (self._max - self._min) * 2.0 - 1.0
        self._servo.value = max(-1.0, min(1.0, value))

    def detach(self) -> None:
        self._servo.detach()

    def close(self) -> None:
        try:
            self._servo.detach()
            self._servo.close()
        except Exception:
            pass


class SysfsPWMServoBackend:
    """Kernel hardware PWM via ``/sys/class/pwm`` (jitter-free pulses).

    Requires a PWM overlay that routes GPIO18 to a PWM channel. On the Pi 5,
    GPIO18 is RP1 PWM0 channel 2; the pwmchip number depends on the kernel
    (``pwmchip0`` on 6.12+, ``pwmchip2`` on some older 6.6 builds). Verify with
    ``ls /sys/class/pwm`` and ``pinctrl get 18`` — see docs/HARDWARE.md.
    """

    name = "hardware-pwm"
    synthetic = False

    def __init__(self, settings: Settings) -> None:
        self.chip = Path(f"/sys/class/pwm/pwmchip{settings.servo_pwm_chip}")
        self.channel = settings.servo_pwm_channel
        if not self.chip.is_dir():
            raise ServoError(f"{self.chip} not found — enable a PWM overlay for GPIO{settings.servo_gpio}")
        self.pwm = self.chip / f"pwm{self.channel}"
        if not self.pwm.exists():
            self._write(self.chip / "export", str(self.channel))
            for _ in range(50):  # udev may take a moment to apply permissions
                if (self.pwm / "period").exists():
                    break
                time.sleep(0.02)
        self._write(self.pwm / "enable", "0", ignore_errors=True)
        self._write(self.pwm / "duty_cycle", "0", ignore_errors=True)
        self._write(self.pwm / "period", str(PWM_PERIOD_NS))
        self._enabled = False

    @staticmethod
    def _write(path: Path, value: str, ignore_errors: bool = False) -> None:
        try:
            path.write_text(value)
        except OSError as exc:
            if not ignore_errors:
                raise ServoError(f"cannot write {path}: {exc}") from exc

    def write_pulse_us(self, pulse_us: float) -> None:
        self._write(self.pwm / "duty_cycle", str(int(pulse_us * 1000)))
        if not self._enabled:
            self._write(self.pwm / "enable", "1")
            self._enabled = True

    def detach(self) -> None:
        self._write(self.pwm / "enable", "0", ignore_errors=True)
        self._enabled = False

    def close(self) -> None:
        self.detach()
        self._write(self.pwm / "duty_cycle", "0", ignore_errors=True)
        self._write(self.chip / "unexport", str(self.channel), ignore_errors=True)


class MockServoBackend:
    name = "mock-servo"
    synthetic = True

    def __init__(self) -> None:
        self.pulse_us: float | None = None
        self.writes = 0

    def write_pulse_us(self, pulse_us: float) -> None:
        self.pulse_us = pulse_us
        self.writes += 1

    def detach(self) -> None:
        self.pulse_us = None

    def close(self) -> None:
        self.pulse_us = None


class ServoService:
    def __init__(self, settings: Settings, events: EventLog) -> None:
        self.settings = settings
        self.events = events
        self.status = DeviceStatus("servo")
        self._cfg_path = settings.data_dir / "servo_config.json"
        self.min_angle = settings.servo_min_angle
        self.max_angle = settings.servo_max_angle
        self.center_angle = settings.servo_center_angle
        self.max_speed_dps = settings.servo_max_speed_dps
        self._load_overrides()
        self.armed = False
        self.target: float | None = None
        self.current: float | None = None  # commanded output angle (no feedback)
        self._attached = False
        self._last_move = 0.0
        self._lock = threading.Lock()
        self._backend: ServoBackend | None = None
        self._thread: StoppableThread | None = None

    # ------------------------------------------------------------ config
    def _load_overrides(self) -> None:
        try:
            d = json.loads(self._cfg_path.read_text())
        except FileNotFoundError:
            return
        except Exception as exc:
            log.warning("ignoring unreadable servo config: %s", exc)
            return
        try:
            self._apply_limits(d.get("min_angle", self.min_angle), d.get("max_angle", self.max_angle),
                               d.get("max_speed_dps", self.max_speed_dps), d.get("center_angle", self.center_angle))
        except ServoError as exc:
            log.warning("ignoring invalid saved servo limits: %s", exc)

    def _apply_limits(self, lo: float, hi: float, speed: float, center: float | None = None) -> None:
        lo, hi, speed = float(lo), float(hi), float(speed)
        center = float(self.center_angle if center is None else center)
        if not (HW_MIN_ANGLE <= lo < hi <= HW_MAX_ANGLE):
            raise ServoError("limits must satisfy 0 ≤ min < max ≤ 180")
        if not (5.0 <= speed <= 360.0):
            raise ServoError("max speed must be within 5–360 °/s")
        center = clamp_angle(center, lo, hi)
        self.min_angle, self.max_angle, self.max_speed_dps, self.center_angle = lo, hi, speed, center

    def update_limits(self, min_angle: float, max_angle: float, max_speed_dps: float,
                      center_angle: float | None = None) -> dict[str, Any]:
        with self._lock:
            self._apply_limits(min_angle, max_angle, max_speed_dps, center_angle)
            if self.target is not None:
                self.target = clamp_angle(self.target, self.min_angle, self.max_angle)
        try:
            self._cfg_path.parent.mkdir(parents=True, exist_ok=True)
            self._cfg_path.write_text(json.dumps({
                "min_angle": self.min_angle, "max_angle": self.max_angle,
                "max_speed_dps": self.max_speed_dps, "center_angle": self.center_angle,
            }, indent=2))
        except OSError as exc:
            log.warning("could not persist servo limits: %s", exc)
        self.events.add("info", "servo", f"limits set to {self.min_angle:.0f}–{self.max_angle:.0f}° @ {self.max_speed_dps:.0f}°/s")
        return self.state()

    # --------------------------------------------------------- lifecycle
    def _make_backend(self) -> ServoBackend | None:
        mode = self.settings.backend_for(self.settings.servo_backend)
        if mode == "disabled":
            self.status.backend = "disabled"
            self.status.set(DeviceState.DISABLED, "disabled by configuration")
            return None
        if mode == "mock":
            self.status.backend, self.status.synthetic = MockServoBackend.name, True
            return MockServoBackend()
        errors = []
        # "auto" never probes sysfs PWM: on the Pi 5 a pwmchip may belong to the
        # cooling-fan controller. Hardware PWM must be selected explicitly after
        # verifying chip/channel (docs/HARDWARE.md).
        for c in (["gpiozero"] if mode == "auto" else [mode]):
            try:
                b: ServoBackend = SysfsPWMServoBackend(self.settings) if c == "hardware_pwm" else GpiozeroServoBackend(self.settings)
                self.status.backend, self.status.synthetic = b.name, False
                return b
            except Exception as exc:
                errors.append(f"{c}: {exc}")
        self.status.backend = mode
        self.status.set(DeviceState.DISCONNECTED, "; ".join(errors))
        log.warning("servo unavailable: %s", "; ".join(errors))
        return None

    def start(self) -> None:
        self._backend = self._make_backend()
        if self._backend is None:
            return
        self.status.set(DeviceState.SYNTHETIC if self.status.synthetic else DeviceState.OK)
        self._thread = StoppableThread(self._run, "servo-trajectory")
        self._thread.start()
        if self.settings.servo_start_armed:
            self.arm()

    def stop(self) -> None:
        if self._thread:
            self._thread.stop()
        if self._backend:
            try:
                self._backend.close()
            except Exception:
                pass

    # ----------------------------------------------------------- control
    def arm(self) -> dict[str, Any]:
        if self._backend is None:
            raise ServoError(self.status.error or "servo backend unavailable")
        with self._lock:
            if not self.armed:
                self.armed = True
                if self.current is None:
                    # Unknown physical position: the first move goes straight to center.
                    self.current = self.center_angle
                    self.target = self.center_angle
                    self._write(self.current)
        self.events.add("info", "servo", "camera servo control ENABLED")
        return self.state()

    def disarm(self) -> dict[str, Any]:
        with self._lock:
            self.armed = False
            self.target = self.current  # stop any motion in progress
            if self._backend is not None and self._attached:
                try:
                    self._backend.detach()
                except Exception:
                    pass
                self._attached = False
        self.events.add("info", "servo", "camera servo control DISABLED")
        return self.state()

    def set_target(self, angle: float) -> dict[str, Any]:
        if self._backend is None:
            raise ServoError(self.status.error or "servo backend unavailable")
        if not self.armed:
            raise ServoError("servo control is disabled — enable it first")
        with self._lock:
            commanded = clamp_angle(float(angle), self.min_angle, self.max_angle)
            self.target = commanded
        st = self.state()
        st["requested_angle"] = float(angle)
        st["clamped"] = commanded != float(angle)
        return st

    def center(self) -> dict[str, Any]:
        return self.set_target(self.center_angle)

    @property
    def up_sign(self) -> float:
        """+1 if a larger servo angle tilts the camera up."""
        return 1.0 if self.settings.servo_up_is_positive else -1.0

    def tilt_deg(self) -> float | None:
        """Commanded camera tilt relative to center (+ = up)."""
        if self.current is None:
            return None
        return (self.current - self.center_angle) * self.up_sign

    def nudge(self, up_deg: float) -> dict[str, Any]:
        """Tilt the camera by ``up_deg`` (+ = up) relative to the current target."""
        base = self.target if self.target is not None else (self.current if self.current is not None else self.center_angle)
        return self.set_target(base + up_deg * self.up_sign)

    def tilt_to(self, tilt_up_deg: float) -> dict[str, Any]:
        """Absolute camera tilt relative to center (+ = up)."""
        return self.set_target(self.center_angle + tilt_up_deg * self.up_sign)

    def set_speed(self, max_speed_dps: float) -> dict[str, Any]:
        return self.update_limits(self.min_angle, self.max_angle, max_speed_dps, self.center_angle)

    def at_target(self, tol: float = 0.5) -> bool:
        return self.target is None or self.current is None or abs(self.target - self.current) <= tol

    def _write(self, angle: float) -> None:
        assert self._backend is not None
        pulse = angle_to_pulse_us(angle, self.settings.servo_min_pulse_us, self.settings.servo_max_pulse_us)
        self._backend.write_pulse_us(pulse)
        self._attached = True
        self._last_move = time.monotonic()
        self.status.touch()

    def _run(self, stop: threading.Event) -> None:
        period = 1.0 / LOOP_HZ
        while not stop.is_set():
            try:
                with self._lock:
                    if self.armed and self.target is not None and self.current is not None:
                        if self.target != self.current:
                            self.current = step_toward(self.current, self.target, self.max_speed_dps * period)
                            self._write(self.current)
                        elif (
                            self.settings.servo_detach_when_idle
                            and self._attached
                            and time.monotonic() - self._last_move > 1.0
                        ):
                            # Stop pulses when idle: prevents SG90 hunting/jitter and heat.
                            self._backend.detach()  # type: ignore[union-attr]
                            self._attached = False
            except Exception as exc:
                self.status.set(DeviceState.ERROR, f"servo write failed: {exc}")
                log.exception("servo loop error")
                stop.wait(1.0)
            stop.wait(period)

    def state(self) -> dict[str, Any]:
        moving = self.target is not None and self.current is not None and abs(self.target - self.current) > 1e-6
        return {
            "status": self.status.to_dict(),
            "available": self._backend is not None,
            "armed": self.armed,
            "target_angle": self.target,
            "commanded_angle": self.current,
            "moving": moving,
            "attached": self._attached,
            "limits": {
                "min_angle": self.min_angle,
                "max_angle": self.max_angle,
                "center_angle": self.center_angle,
                "max_speed_dps": self.max_speed_dps,
                "hardware_min": HW_MIN_ANGLE,
                "hardware_max": HW_MAX_ANGLE,
            },
            "pulse_range_us": [self.settings.servo_min_pulse_us, self.settings.servo_max_pulse_us],
            "gpio": self.settings.servo_gpio,
            "position_feedback": False,
            "tilt_deg": self.tilt_deg(),
            "up_is_positive": self.settings.servo_up_is_positive,
        }
