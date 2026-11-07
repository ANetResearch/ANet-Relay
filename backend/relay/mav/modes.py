"""Flight mode tables for ArduPilot Copter and PX4 (03 §5.4).

Mode classes: ``manual`` (self-level, pilot throttle), ``althold`` (altitude
hold), ``position`` (position hold with pilot sticks), ``auto`` (the autopilot
flies; sticks are ignored).
"""

from __future__ import annotations

from pymavlink import mavutil

from .mavlink import M

ARDUPILOT = "ArduPilot"
PX4 = "PX4"

ARDUPILOT_MODES: dict[str, int] = {
    "STABILIZE": 0,
    "ACRO": 1,
    "ALT_HOLD": 2,
    "AUTO": 3,
    "GUIDED": 4,
    "LOITER": 5,
    "RTL": 6,
    "CIRCLE": 7,
    "LAND": 9,
    "DRIFT": 11,
    "SPORT": 13,
    "GUIDED_NOGPS": 20,
    "POSHOLD": 16,
    "BRAKE": 17,
    "SMART_RTL": 21,
    "FLOWHOLD": 22,
}
ARDUPILOT_BY_NUMBER = {number: name for name, number in ARDUPILOT_MODES.items()}

PX4_MODES = (
    "MANUAL",
    "STABILIZED",
    "ACRO",
    "ALTCTL",
    "POSCTL",
    "LOITER",
    "MISSION",
    "RTL",
    "LAND",
    "TAKEOFF",
    "OFFBOARD",
)

_CLASSES: dict[str, dict[str, str]] = {
    ARDUPILOT: {
        "STABILIZE": "manual",
        "ACRO": "manual",
        "DRIFT": "manual",
        "ALT_HOLD": "althold",
        "SPORT": "althold",
        "LOITER": "position",
        "POSHOLD": "position",
        "FLOWHOLD": "position",
        "GUIDED": "auto",
        "GUIDED_NOGPS": "auto",
        "AUTO": "auto",
        "RTL": "auto",
        "SMART_RTL": "auto",
        "LAND": "auto",
        "BRAKE": "auto",
        "CIRCLE": "auto",
    },
    PX4: {
        "MANUAL": "manual",
        "STABILIZED": "manual",
        "ACRO": "manual",
        "ALTCTL": "althold",
        "POSCTL": "position",
        "LOITER": "auto",
        "MISSION": "auto",
        "RTL": "auto",
        "LAND": "auto",
        "TAKEOFF": "auto",
        "OFFBOARD": "auto",
    },
}

STICK_MODES: dict[str, frozenset[str]] = {
    ARDUPILOT: frozenset(
        {"STABILIZE", "ALT_HOLD", "LOITER", "POSHOLD", "ACRO", "SPORT", "DRIFT", "FLOWHOLD"}
    ),
    PX4: frozenset({"MANUAL", "STABILIZED", "ALTCTL", "POSCTL", "ACRO"}),
}

# Stick modes that hold altitude: sticks are rates around a held altitude (03 §5.7).
HOLD_STICK_MODES = frozenset({"ALT_HOLD", "LOITER", "POSHOLD", "FLOWHOLD", "ALTCTL", "POSCTL"})


def autopilot_name(autopilot: int | None) -> str | None:
    if autopilot == M.MAV_AUTOPILOT_ARDUPILOTMEGA:
        return ARDUPILOT
    if autopilot == M.MAV_AUTOPILOT_PX4:
        return PX4
    return None


def available_modes(autopilot: str | None) -> list[str]:
    if autopilot == ARDUPILOT:
        return list(ARDUPILOT_MODES)
    if autopilot == PX4:
        return list(PX4_MODES)
    return []


def mode_name(autopilot: str | None, base_mode: int, custom_mode: int) -> str | None:
    if autopilot == PX4:
        return mavutil.interpret_px4_mode(base_mode, custom_mode)
    if autopilot == ARDUPILOT:
        return ARDUPILOT_BY_NUMBER.get(int(custom_mode), f"MODE_{int(custom_mode)}")
    return None


def mode_class(autopilot: str | None, mode: str | None) -> str | None:
    if not autopilot or not mode:
        return None
    return _CLASSES.get(autopilot, {}).get(mode)


def accepts_sticks(autopilot: str | None, mode: str | None) -> bool:
    return bool(autopilot and mode and mode in STICK_MODES.get(autopilot, frozenset()))


def possess_mode(autopilot: str | None, has_baro: bool, setting: str = "auto") -> str | None:
    """Mode Relay switches to when an operator possesses a vehicle in a non-stick mode."""
    althold = setting == "althold" or (setting == "auto" and has_baro)
    if autopilot == ARDUPILOT:
        return "ALT_HOLD" if althold else "STABILIZE"
    if autopilot == PX4:
        return "ALTCTL" if althold else "STABILIZED"
    return None


def set_mode_params(autopilot: str | None, name: str) -> tuple[float, float, float]:
    """MAV_CMD_DO_SET_MODE params 1-3. Raises KeyError for unknown modes."""
    if autopilot == PX4:
        base, main, sub = mavutil.px4_map[name]
        return float(base), float(main), float(sub)
    if autopilot == ARDUPILOT:
        return float(M.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED), float(ARDUPILOT_MODES[name]), 0.0
    raise KeyError(name)


def px4_custom_mode(name: str) -> tuple[int, int]:
    """(base_mode flags, custom_mode) for a PX4 mode name, as PX4 reports them in HEARTBEAT."""
    base, main, sub = mavutil.px4_map[name]
    return base, (main << 16) | (sub << 24)


def has_althold_mode(autopilot: str | None) -> bool:
    return any(mode_class(autopilot, m) in ("althold", "position") for m in available_modes(autopilot))
