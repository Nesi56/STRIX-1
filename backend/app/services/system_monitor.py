"""Raspberry Pi system health: CPU, memory, thermals, power, network.

Power telemetry notes
---------------------
* ``vcgencmd get_throttled`` reports under-voltage / throttling flags.
* On the Pi 5, ``vcgencmd pmic_read_adc`` exposes the PMIC ADC including the
  5 V input (``EXT5V_V``) and per-rail currents. The rail power sum is an
  estimate for the SoC/PMIC rails only — it excludes USB peripherals, the
  servo and the camera, so it is labelled as such.
* There is **no battery telemetry**: the USB-C PD power bank and the 52Pi PD
  board (as configured here) report nothing to the Pi, so battery percentage
  is shown as unavailable rather than estimated from capacity and time.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import psutil

from app.config.settings import Settings
from app.services.base import StoppableThread
from app.services.hub import EventLog

log = logging.getLogger(__name__)

THROTTLE_BITS = {
    0: "under_voltage_now",
    1: "arm_freq_capped_now",
    2: "throttled_now",
    3: "soft_temp_limit_now",
    16: "under_voltage_occurred",
    17: "arm_freq_capped_occurred",
    18: "throttled_occurred",
    19: "soft_temp_limit_occurred",
}


def parse_throttled(output: str) -> dict[str, Any] | None:
    """Parse ``vcgencmd get_throttled`` output such as ``throttled=0x50005``."""
    m = re.search(r"throttled=(0x[0-9a-fA-F]+|\d+)", output)
    if not m:
        return None
    value = int(m.group(1), 0)
    flags = {name: bool(value & (1 << bit)) for bit, name in THROTTLE_BITS.items()}
    return {"raw": hex(value), **flags}


_PMIC_RE = re.compile(r"^\s*(\S+)\s+(current|volt)\(\d+\)=([0-9.]+)([AV])\s*$", re.MULTILINE)


def parse_pmic_adc(output: str) -> dict[str, Any] | None:
    """Parse Pi 5 ``vcgencmd pmic_read_adc`` output.

    Lines look like ``   VDD_CORE_A current(7)=2.31000000A`` and
    ``   EXT5V_V volt(24)=5.10208000V``.
    """
    currents: dict[str, float] = {}
    volts: dict[str, float] = {}
    for name, kind, val, _unit in _PMIC_RE.findall(output):
        if kind == "current":
            currents[name[:-2] if name.endswith("_A") else name] = float(val)
        else:
            volts[name[:-2] if name.endswith("_V") else name] = float(val)
    if not currents and not volts:
        return None
    rail_power = sum(currents[r] * volts[r] for r in currents if r in volts)
    return {
        "input_voltage_v": volts.get("EXT5V"),
        "rail_power_w": round(rail_power, 3) if rail_power else None,
        "rails": {r: {"v": volts.get(r), "a": currents.get(r)} for r in sorted(set(currents) | set(volts))},
    }


def parse_proc_net_wireless(text: str) -> dict[str, dict[str, float]]:
    """Parse ``/proc/net/wireless`` link quality / signal level per interface."""
    out: dict[str, dict[str, float]] = {}
    for line in text.splitlines()[2:]:
        parts = line.split()
        if len(parts) < 4 or not parts[0].endswith(":"):
            continue
        try:
            out[parts[0][:-1]] = {
                "link_quality": float(parts[2].rstrip(".")),
                "signal_dbm": float(parts[3].rstrip(".")),
            }
        except ValueError:
            continue
    return out


def read_cpu_temp() -> float | None:
    for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
        try:
            kind = (zone / "type").read_text().strip()
            if kind in ("cpu-thermal", "cpu_thermal", "x86_pkg_temp", "soc_thermal") or zone.name == "thermal_zone0":
                return int((zone / "temp").read_text().strip()) / 1000.0
        except (OSError, ValueError):
            continue
    try:
        temps = psutil.sensors_temperatures()  # type: ignore[attr-defined]
        for entries in temps.values():
            if entries:
                return float(entries[0].current)
    except Exception:
        pass
    return None


def read_pi_model() -> str | None:
    try:
        return Path("/proc/device-tree/model").read_text().strip("\x00\n ")
    except OSError:
        return None


def _run(cmd: list[str], timeout: float = 2.0) -> str | None:
    if shutil.which(cmd[0]) is None:
        return None
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def _hwmon_undervoltage() -> bool | None:
    for hw in Path("/sys/class/hwmon").glob("hwmon*"):
        try:
            if (hw / "name").read_text().strip() == "rpi_volt":
                return (hw / "in0_lcrit_alarm").read_text().strip() == "1"
        except OSError:
            continue
    return None


class SystemMonitor:
    def __init__(self, settings: Settings, events: EventLog) -> None:
        self.settings = settings
        self.events = events
        self._lock = threading.Lock()
        self._latest: dict[str, Any] = {}
        self._thread: StoppableThread | None = None
        self._net_prev: tuple[float, int, int] | None = None
        self._proc = psutil.Process(os.getpid())
        self._pi_model = read_pi_model()
        self._extra_pids: dict[str, int] = {}
        self._last_power_poll = 0.0
        self._power: dict[str, Any] = {}
        self._warned: set[str] = set()

    def register_process(self, label: str, pid: int | None) -> None:
        if pid is None:
            self._extra_pids.pop(label, None)
        else:
            self._extra_pids[label] = pid

    def start(self) -> None:
        psutil.cpu_percent(interval=None)  # prime the counters
        self._sample()
        self._thread = StoppableThread(self._loop, "system-monitor")
        self._thread.start()

    def stop(self) -> None:
        if self._thread:
            self._thread.stop()

    def _loop(self, stop: threading.Event) -> None:
        while not stop.wait(2.0):
            try:
                self._sample()
            except Exception:
                log.exception("system monitor sample failed")

    def _poll_power(self) -> dict[str, Any]:
        throttled = parse_throttled(_run(["vcgencmd", "get_throttled"]) or "")
        pmic = parse_pmic_adc(_run(["vcgencmd", "pmic_read_adc"]) or "")
        uv_hwmon = _hwmon_undervoltage()
        undervoltage_now = None
        if throttled is not None:
            undervoltage_now = throttled["under_voltage_now"]
        elif uv_hwmon is not None:
            undervoltage_now = uv_hwmon
        return {
            "throttled": throttled,
            "pmic": pmic,
            "undervoltage_now": undervoltage_now,
            "undervoltage_occurred": throttled["under_voltage_occurred"] if throttled else None,
            "input_voltage_v": pmic["input_voltage_v"] if pmic else None,
            "source": "vcgencmd" if throttled or pmic else ("hwmon" if uv_hwmon is not None else None),
            "battery": {
                "available": False,
                "percent": None,
                "reason": "no battery telemetry source (PD power bank exposes no data to the Pi)",
            },
        }

    def _proc_stats(self, pid: int) -> dict[str, Any] | None:
        try:
            p = psutil.Process(pid)
            with p.oneshot():
                return {"pid": pid, "cpu_percent": p.cpu_percent(None), "rss_mb": p.memory_info().rss / 1e6, "name": p.name()}
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

    def _sample(self) -> None:
        now = time.time()
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        freq = psutil.cpu_freq()
        try:
            load = os.getloadavg()
        except OSError:
            load = (0.0, 0.0, 0.0)
        net = psutil.net_io_counters()
        rx_bps = tx_bps = None
        if self._net_prev is not None:
            dt = now - self._net_prev[0]
            if dt > 0:
                rx_bps = (net.bytes_recv - self._net_prev[1]) / dt
                tx_bps = (net.bytes_sent - self._net_prev[2]) / dt
        self._net_prev = (now, net.bytes_recv, net.bytes_sent)
        wireless: dict[str, Any] = {}
        try:
            wireless = parse_proc_net_wireless(Path("/proc/net/wireless").read_text())
        except OSError:
            pass
        if now - self._last_power_poll > 5.0:
            self._last_power_poll = now
            self._power = self._poll_power()
        try:
            disk = shutil.disk_usage(self.settings.data_dir if self.settings.data_dir.exists() else "/")
            disk_d = {"total_gb": disk.total / 1e9, "free_gb": disk.free / 1e9, "percent": 100 * disk.used / disk.total}
        except OSError:
            disk_d = None
        with self._proc.oneshot():
            me = {"pid": self._proc.pid, "cpu_percent": self._proc.cpu_percent(None), "rss_mb": self._proc.memory_info().rss / 1e6,
                  "threads": self._proc.num_threads()}
        procs = {"backend": me}
        for label, pid in list(self._extra_pids.items()):
            st = self._proc_stats(pid)
            if st:
                procs[label] = st
        sample = {
            "ts": now,
            "hostname": socket.gethostname(),
            "pi_model": self._pi_model,
            "is_raspberry_pi": bool(self._pi_model and "Raspberry Pi" in self._pi_model),
            "cpu_percent": psutil.cpu_percent(interval=None),
            "cpu_per_core": psutil.cpu_percent(interval=None, percpu=True),
            "cpu_count": psutil.cpu_count(),
            "cpu_freq_mhz": freq.current if freq else None,
            "load_avg": list(load),
            "cpu_temp_c": read_cpu_temp(),
            "memory": {
                "total_mb": vm.total / 1e6,
                "available_mb": vm.available / 1e6,
                "used_percent": vm.percent,
                "swap_used_percent": sw.percent,
            },
            "disk": disk_d,
            "network": {"rx_bps": rx_bps, "tx_bps": tx_bps, "wireless": wireless},
            "power": self._power,
            "processes": procs,
            "uptime_s": now - psutil.boot_time(),
        }
        sample["warnings"] = self._warnings(sample)
        with self._lock:
            self._latest = sample

    def _warnings(self, s: dict[str, Any]) -> list[dict[str, str]]:
        w: list[dict[str, str]] = []
        mem = s["memory"]
        if mem["available_mb"] < 300 or mem["used_percent"] > 95:
            w.append({"severity": "critical", "code": "low_memory", "message": f"only {mem['available_mb']:.0f} MB RAM available"})
        elif mem["used_percent"] > 85:
            w.append({"severity": "warning", "code": "memory", "message": f"memory {mem['used_percent']:.0f}% used"})
        t = s["cpu_temp_c"]
        if t is not None and t >= 80:
            w.append({"severity": "critical", "code": "cpu_hot", "message": f"CPU {t:.1f} °C (throttling begins ≈80–85 °C)"})
        elif t is not None and t >= 70:
            w.append({"severity": "warning", "code": "cpu_warm", "message": f"CPU {t:.1f} °C"})
        p = s["power"] or {}
        if p.get("undervoltage_now"):
            w.append({"severity": "critical", "code": "undervoltage", "message": "UNDER-VOLTAGE detected now — check power supply"})
        elif p.get("undervoltage_occurred"):
            w.append({"severity": "warning", "code": "undervoltage_past", "message": "under-voltage occurred since boot"})
        thr = p.get("throttled") or {}
        if thr.get("throttled_now") or thr.get("arm_freq_capped_now"):
            w.append({"severity": "warning", "code": "throttled", "message": "CPU is throttled / frequency capped"})
        v = p.get("input_voltage_v")
        if v is not None and v < 4.85:
            w.append({"severity": "warning", "code": "low_input_voltage", "message": f"5V input at {v:.2f} V"})
        d = s.get("disk")
        if d and d["free_gb"] < 1.0:
            w.append({"severity": "warning", "code": "disk", "message": f"only {d['free_gb']:.1f} GB disk free"})
        # Log new critical warnings once.
        codes = {x["code"] for x in w if x["severity"] == "critical"}
        for c in codes - self._warned:
            msg = next(x["message"] for x in w if x["code"] == c)
            self.events.add("error", "system", msg)
        self._warned = codes
        return w

    def latest(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._latest)

    def low_memory(self) -> bool:
        return any(w["code"] == "low_memory" for w in self.latest().get("warnings", []))
