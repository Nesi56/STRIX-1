"""DHT11 temperature / humidity acquisition.

Backends (in ``auto`` order):

1. ``kernel`` — Linux ``dht11`` IIO driver enabled with
   ``dtoverlay=dht11,gpiopin=4`` in ``/boot/firmware/config.txt``. The kernel
   performs the timing-critical bit decoding with interrupts, which is the
   most reliable option on the Raspberry Pi 5 (RP1 GPIO).
2. ``adafruit`` — ``adafruit_dht`` via Blinka. On the Pi 5 this falls back to
   user-space bit-banging which is timing sensitive and may fail often.

The DHT11 is a slow sensor (≤1 Hz, ±2 °C, ±5 %RH); we poll every
``STRIX_DHT_INTERVAL_S`` seconds (default 3 s, minimum 2 s).
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Protocol

from app.config.settings import Settings
from app.services.base import DeviceState, DeviceStatus, StoppableThread
from app.services.hub import EventLog

log = logging.getLogger(__name__)

IIO_ROOT = Path("/sys/bus/iio/devices")
FAILURES_UNTIL_DISCONNECTED = 5


class DHTReadError(RuntimeError):
    pass


def parse_iio_milli(raw: str) -> float:
    """IIO dht11 reports milli-degrees C / milli-percent RH as integers."""
    raw = raw.strip()
    if not raw or not raw.lstrip("-").isdigit():
        raise DHTReadError(f"unexpected IIO value {raw!r}")
    return int(raw) / 1000.0


def validate_dht11(temp_c: float, humidity: float) -> None:
    # DHT11 datasheet operating range: 0–50 °C, 20–90 %RH (readings may extend a bit).
    if not (-10.0 <= temp_c <= 60.0):
        raise DHTReadError(f"implausible DHT11 temperature {temp_c} °C")
    if not (0.0 <= humidity <= 100.0):
        raise DHTReadError(f"implausible DHT11 humidity {humidity} %")


def find_dht_iio_device(root: Path = IIO_ROOT) -> Path | None:
    if not root.exists():
        return None
    for dev in sorted(root.glob("iio:device*")):
        try:
            name = (dev / "name").read_text().strip()
        except OSError:
            continue
        if name.startswith("dht11") and (dev / "in_temp_input").exists():
            return dev
    return None


class DHTBackend(Protocol):
    name: str
    synthetic: bool

    def read(self) -> tuple[float, float]: ...

    def close(self) -> None: ...


class KernelIIODHT:
    name = "kernel-iio-dht11"
    synthetic = False

    def __init__(self, device: str = "") -> None:
        path = Path(device) if device else find_dht_iio_device()
        if path is None or not (path / "in_temp_input").exists():
            raise DHTReadError(
                "no dht11 IIO device found — add 'dtoverlay=dht11,gpiopin=4' to /boot/firmware/config.txt and reboot"
            )
        self.path = path

    def read(self) -> tuple[float, float]:
        try:
            # The driver triggers a fresh measurement on read (cached ~2 s).
            temp = parse_iio_milli((self.path / "in_temp_input").read_text())
            hum = parse_iio_milli((self.path / "in_humidityrelative_input").read_text())
        except OSError as exc:  # EIO / ETIMEDOUT on a failed handshake or checksum
            raise DHTReadError(f"IIO read failed: {exc.strerror or exc}") from exc
        validate_dht11(temp, hum)
        return temp, hum

    def close(self) -> None:
        pass


class AdafruitDHT:
    name = "adafruit-dht11"
    synthetic = False

    def __init__(self, gpio: int) -> None:
        import adafruit_dht  # type: ignore[import-not-found]
        import board  # type: ignore[import-not-found]

        pin = getattr(board, f"D{gpio}")
        # pulseio is unreliable on Pi 5; bit-bang via digitalio instead.
        self._dev = adafruit_dht.DHT11(pin, use_pulseio=False)

    def read(self) -> tuple[float, float]:
        try:
            self._dev.measure()
            temp, hum = self._dev.temperature, self._dev.humidity
        except RuntimeError as exc:  # checksum / missing pulses are routine on DHTs
            raise DHTReadError(str(exc)) from exc
        if temp is None or hum is None:
            raise DHTReadError("sensor returned no data")
        validate_dht11(float(temp), float(hum))
        return float(temp), float(hum)

    def close(self) -> None:
        try:
            self._dev.exit()
        except Exception:
            pass


class MockDHT:
    """Synthetic indoor-ish environment with slow drift and integer-like DHT11 steps."""

    name = "mock-dht11"
    synthetic = True

    def __init__(self, seed: int | None = None, failure_rate: float = 0.05) -> None:
        self._rng = random.Random(seed)
        self._t0 = time.monotonic()
        self.failure_rate = failure_rate

    def read(self) -> tuple[float, float]:
        if self._rng.random() < self.failure_rate:
            raise DHTReadError("simulated checksum failure (synthetic)")
        t = time.monotonic() - self._t0
        temp = 22.0 + 1.5 * math.sin(t / 300.0) + self._rng.gauss(0, 0.2)
        hum = 46.0 - 4.0 * math.sin(t / 420.0) + self._rng.gauss(0, 0.6)
        return round(temp, 1), round(max(0.0, min(100.0, hum)), 0)

    def close(self) -> None:
        pass


class DHTService:
    def __init__(self, settings: Settings, events: EventLog) -> None:
        self.settings = settings
        self.events = events
        self.status = DeviceStatus("dht11")
        self._backend: DHTBackend | None = None
        self._thread: StoppableThread | None = None
        self._lock = threading.Lock()
        self._latest: dict[str, Any] | None = None
        self._history: deque[tuple[float, float, float]] = deque(maxlen=4000)
        self._failures = 0
        self._total_reads = 0
        self._total_failures = 0
        self._session = {"temp_min": None, "temp_max": None, "hum_min": None, "hum_max": None, "since": time.time()}

    def _make_backend(self) -> DHTBackend | None:
        mode = self.settings.backend_for(self.settings.dht_backend)
        if mode == "disabled":
            self.status.backend = "disabled"
            self.status.set(DeviceState.DISABLED, "disabled by configuration")
            return None
        if mode == "mock":
            self.status.backend, self.status.synthetic = MockDHT.name, True
            return MockDHT()
        errors = []
        candidates = ["kernel", "adafruit"] if mode == "auto" else [mode]
        for c in candidates:
            try:
                backend: DHTBackend = (
                    KernelIIODHT(self.settings.dht_iio_device) if c == "kernel" else AdafruitDHT(self.settings.dht_gpio)
                )
                self.status.backend, self.status.synthetic = backend.name, False
                return backend
            except Exception as exc:
                errors.append(f"{c}: {exc}")
        self.status.backend = "/".join(candidates)
        self.status.set(DeviceState.DISCONNECTED, "; ".join(errors))
        log.warning("DHT11 unavailable: %s", "; ".join(errors))
        return None

    def start(self) -> None:
        self._backend = self._make_backend()
        if self.status.state == DeviceState.DISABLED:
            return
        self._thread = StoppableThread(self._run, "dht-acquisition")
        self._thread.start()

    def stop(self) -> None:
        if self._thread:
            self._thread.stop()
        if self._backend:
            self._backend.close()

    def _run(self, stop: threading.Event) -> None:
        last_retry = 0.0
        while not stop.is_set():
            if self._backend is None:
                if time.monotonic() - last_retry > 15.0:
                    last_retry = time.monotonic()
                    self._backend = self._make_backend()
                stop.wait(1.0)
                continue
            self._total_reads += 1
            try:
                temp, hum = self._backend.read()
            except Exception as exc:
                self._failures += 1
                self._total_failures += 1
                if self._failures >= FAILURES_UNTIL_DISCONNECTED:
                    if self.status.state != DeviceState.DISCONNECTED:
                        self.events.add("error", "dht11", f"DHT11 not responding: {exc}")
                    self.status.set(DeviceState.DISCONNECTED, f"{self._failures} consecutive read failures: {exc}")
                elif not self.status.synthetic:
                    self.status.set(DeviceState.DEGRADED, f"read failed ({self._failures}x): {exc}")
                else:
                    self.status.set(DeviceState.SYNTHETIC, f"read failed ({self._failures}x): {exc}")
                # Back off a little after a failure (DHT11 needs ≥1 s between reads).
                stop.wait(max(2.0, self.settings.dht_interval_s / 2))
                continue
            self._failures = 0
            now = time.time()
            sample = {
                "ts": now,
                "temperature_c": temp,
                "temperature_f": temp * 9.0 / 5.0 + 32.0,
                "humidity_pct": hum,
            }
            with self._lock:
                self._latest = sample
                self._history.append((now, temp, hum))
                s = self._session
                s["temp_min"] = temp if s["temp_min"] is None else min(s["temp_min"], temp)
                s["temp_max"] = temp if s["temp_max"] is None else max(s["temp_max"], temp)
                s["hum_min"] = hum if s["hum_min"] is None else min(s["hum_min"], hum)
                s["hum_max"] = hum if s["hum_max"] is None else max(s["hum_max"], hum)
            self.status.touch()
            self.status.set(DeviceState.SYNTHETIC if self.status.synthetic else DeviceState.OK)
            stop.wait(self.settings.dht_interval_s)

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return self._latest

    def history(self, seconds: float | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._history)
        if seconds is not None:
            cutoff = time.time() - seconds
            rows = [r for r in rows if r[0] >= cutoff]
        return [{"ts": r[0], "temperature_c": r[1], "humidity_pct": r[2]} for r in rows]

    def reset_session_extremes(self) -> None:
        with self._lock:
            self._session = {"temp_min": None, "temp_max": None, "hum_min": None, "hum_max": None, "since": time.time()}

    def trend(self, seconds: float = 300.0) -> dict[str, Any]:
        rows = self.history(seconds)
        if len(rows) < 2:
            return {"window_s": seconds, "samples": len(rows)}
        return {
            "window_s": seconds,
            "samples": len(rows),
            "from": rows[0]["ts"],
            "to": rows[-1]["ts"],
            "temperature_change_c": rows[-1]["temperature_c"] - rows[0]["temperature_c"],
            "humidity_change_pct": rows[-1]["humidity_pct"] - rows[0]["humidity_pct"],
            "temperature_min": min(r["temperature_c"] for r in rows),
            "temperature_max": max(r["temperature_c"] for r in rows),
        }

    def summary(self) -> dict[str, Any]:
        with self._lock:
            session = dict(self._session)
        latest = self.latest()
        age = None if latest is None else time.time() - latest["ts"]
        return {
            "status": self.status.to_dict(),
            "latest": latest,
            "age_s": age,
            "stale": age is not None and age > max(15.0, self.settings.dht_interval_s * 4),
            "session": session,
            "reads": self._total_reads,
            "failures": self._total_failures,
            "interval_s": self.settings.dht_interval_s,
            "accuracy": {"temperature_c": 2.0, "humidity_pct": 5.0},
        }
