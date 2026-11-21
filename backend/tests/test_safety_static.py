"""Source-level guard: STRIX-1 never flies the aircraft.

Only ``app/services/flight_link.py`` may talk MAVLink, it may build only HEARTBEAT and COMMAND_LONG
messages, and every send goes through its single allow-listed ``_send``. Flight-control APIs of pymavlink
must not appear anywhere in the backend.
"""

from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
LINK = APP / "services" / "flight_link.py"

FLIGHT_CONTROL_APIS = [
    "command_long_send", "command_int_send", "command_int_encode", "arducopter_arm", "arducopter_disarm",
    "set_mode_send", "set_mode_encode", "set_mode_apm", "set_mode_px4", "set_mode_auto", "set_mode_rtl",
    "set_mode_loiter", "set_mode_manual", "set_position_target", "set_attitude_target", "manual_control_",
    "rc_channels_override", "mission_item", "mission_count", "mission_set_current", "param_set", "set_servo",
    "motors_armed", "set_actuator_control_target", "set_gps_global_origin", "landing_target",
]


def sources() -> list[Path]:
    return [p for p in APP.rglob("*.py") if "__pycache__" not in p.parts]


def test_only_the_flight_link_talks_mavlink():
    offenders = [p.relative_to(APP) for p in sources()
                 if p != LINK and re.search(r"\bpymavlink\b|\bmavutil\b", p.read_text())]
    assert offenders == [], f"MAVLink used outside flight_link.py: {offenders}"


def test_no_flight_control_api_anywhere():
    hits = []
    for p in sources():
        text = p.read_text()
        for api in FLIGHT_CONTROL_APIS:
            if api in text:
                hits.append(f"{p.relative_to(APP)}: {api}")
    assert hits == [], hits


def test_flight_link_builds_only_allowed_messages_and_sends_in_one_place():
    text = LINK.read_text()
    built = set(re.findall(r"mav\.(\w+)_encode\(", text))
    assert built == {"command_long", "heartbeat"}, built
    assert len(re.findall(r"\.mav\.send\(", text)) == 1, "every send must go through FlightLink._send"
    body = text[text.index("    def _send(") : text.index("    def _command(")]
    assert "check_outgoing(" in body.split(".mav.send(")[0], "the allow-list check must run before the send"
