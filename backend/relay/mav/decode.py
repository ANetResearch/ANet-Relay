"""MAVLink message objects -> per-vehicle state dict (single entry: ``apply_message``).

Real and simulated vehicles go through the same function. The first heartbeat
whose autopilot is not MAV_AUTOPILOT_INVALID (and whose type is a vehicle)
locks the vehicle's sysid / compid / autopilot; afterwards only that identity
refreshes the heartbeat timer and only that system's telemetry updates state.
"""

from __future__ import annotations

import math
import re
import struct
from collections import deque
from typing import Any

from . import modes
from .mavlink import M

NON_AUTOPILOT_TYPES = {
    M.MAV_TYPE_GCS,
    M.MAV_TYPE_ONBOARD_CONTROLLER,
    M.MAV_TYPE_GIMBAL,
    M.MAV_TYPE_ADSB,
    M.MAV_TYPE_CAMERA,
    M.MAV_TYPE_ANTENNA_TRACKER,
}

SENSOR_BITS = (
    ("gyro", M.MAV_SYS_STATUS_SENSOR_3D_GYRO),
    ("accel", M.MAV_SYS_STATUS_SENSOR_3D_ACCEL),
    ("mag", M.MAV_SYS_STATUS_SENSOR_3D_MAG),
    ("baro", M.MAV_SYS_STATUS_SENSOR_ABSOLUTE_PRESSURE),
    ("gps", M.MAV_SYS_STATUS_SENSOR_GPS),
    ("optical_flow", M.MAV_SYS_STATUS_SENSOR_OPTICAL_FLOW),
    ("rangefinder", M.MAV_SYS_STATUS_SENSOR_LASER_POSITION),
    ("ahrs", M.MAV_SYS_STATUS_AHRS),
    ("motor_outputs", M.MAV_SYS_STATUS_SENSOR_MOTOR_OUTPUTS),
    ("rc_receiver", M.MAV_SYS_STATUS_SENSOR_RC_RECEIVER),
    ("battery", M.MAV_SYS_STATUS_SENSOR_BATTERY),
    ("prearm", M.MAV_SYS_STATUS_PREARM_CHECK),
    ("logging", M.MAV_SYS_STATUS_LOGGING),
)

LANDED_NAMES = {0: "unknown", 1: "ground", 2: "air", 3: "takeoff", 4: "landing"}
FW_TYPE_SUFFIX = {0: "-dev", 64: "-alpha", 128: "-beta", 192: "-rc", 255: ""}
FIRMWARE_TEXT = re.compile(r"^(ArduCopter|Copter|PX4)\s+V?(\d+\.\d+(?:\.\d+)?\S*)", re.I)
CRASH_TEXT = re.compile(r"\bcrash", re.I)
STATUSTEXT_KEEP = 50
TIMES_KEEP = 128
PX4_INT_TYPES = {
    M.MAV_PARAM_TYPE_UINT8,
    M.MAV_PARAM_TYPE_INT8,
    M.MAV_PARAM_TYPE_UINT16,
    M.MAV_PARAM_TYPE_INT16,
    M.MAV_PARAM_TYPE_UINT32,
    M.MAV_PARAM_TYPE_INT32,
}


def jsonify(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonify(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace").rstrip("\x00")
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if value is None or isinstance(value, (bool, int, str)):
        return value
    return str(value)


def text_field(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", "replace")
    return "" if value is None else str(value).split("\x00", 1)[0].strip()


def is_autopilot_heartbeat(msg: Any) -> bool:
    return (
        msg.get_type() == "HEARTBEAT"
        and msg.autopilot != M.MAV_AUTOPILOT_INVALID
        and msg.type not in NON_AUTOPILOT_TYPES
    )


def is_gcs_traffic(msg: Any) -> bool:
    """True for traffic another ground station or Relay itself would send."""
    if msg.get_type() == "HEARTBEAT":
        return msg.type == M.MAV_TYPE_GCS
    return msg.get_srcSystem() >= 245


def firmware_from_version(flight_sw_version: int) -> str | None:
    if not flight_sw_version:
        return None
    major = (flight_sw_version >> 24) & 0xFF
    minor = (flight_sw_version >> 16) & 0xFF
    patch = (flight_sw_version >> 8) & 0xFF
    kind = flight_sw_version & 0xFF
    return f"{major}.{minor}.{patch}{FW_TYPE_SUFFIX.get(kind, '')}"


def px4_param_decode(value: float, ptype: int) -> float:
    """PX4 encodes integer parameters bytewise inside the float field."""
    if ptype in PX4_INT_TYPES:
        raw = struct.pack("<f", value)
        if ptype in (M.MAV_PARAM_TYPE_UINT32, M.MAV_PARAM_TYPE_UINT16, M.MAV_PARAM_TYPE_UINT8):
            return float(struct.unpack("<I", raw)[0])
        return float(struct.unpack("<i", raw)[0])
    return value


def px4_param_encode(value: float, ptype: int) -> float:
    if ptype in PX4_INT_TYPES:
        if ptype in (M.MAV_PARAM_TYPE_UINT32, M.MAV_PARAM_TYPE_UINT16, M.MAV_PARAM_TYPE_UINT8):
            raw = struct.pack("<I", int(value) & 0xFFFFFFFF)
        else:
            raw = struct.pack("<i", int(value))
        return struct.unpack("<f", raw)[0]
    return float(value)


def fresh_state() -> dict[str, Any]:
    return {
        "sysid": None,
        "compid": None,
        "autopilot_id": None,
        "autopilot": None,
        "mav_type": None,
        "base_mode": 0,
        "custom_mode": 0,
        "mode": None,
        "armed": False,
        "system_status": None,
        "last_heartbeat": None,  # monotonic
        "heartbeats": 0,
        "battery": {
            "voltage": None,
            "current": None,
            "remaining_pct": None,
            "consumed_mah": None,
            "cell_voltages": None,
            "cells": None,
        },
        "attitude": None,
        "global": None,
        "local": None,
        "local_times": deque(maxlen=64),
        "gps": None,
        "hud": None,
        "rc": None,
        "servo": None,
        "servo16": None,
        "sensors": [],
        "sensor_bits": (0, 0, 0),
        "prearm": None,
        "landed": None,
        "statustext": [],
        "home": None,
        "ekf_flags": None,
        "estimator_flags": None,
        "vibration": None,
        "radio": None,
        "firmware": None,
        "version": None,
        "params": {},
        "param_count": 0,
        "param_rx_at": None,
        "last_ack": None,
        "crashed": False,
        "timesync": None,
        "messages": {},
        "seq": {},
    }


def _record(state: dict[str, Any], mtype: str, msg: Any, now: float) -> dict[str, Any]:
    fields = jsonify(msg.to_dict())
    fields.pop("mavpackettype", None)
    entry = state["messages"].get(mtype)
    if entry is None:
        entry = {"fields": fields, "t": now, "count": 0, "times": deque(maxlen=TIMES_KEEP), "src": None}
        state["messages"][mtype] = entry
    entry["fields"] = fields
    entry["t"] = now
    entry["count"] += 1
    entry["times"].append(now)
    entry["src"] = [msg.get_srcSystem(), msg.get_srcComponent()]
    return fields


def message_rate(times: deque, now: float, window: float = 2.0) -> float:
    count = 0
    for t in reversed(times):
        if now - t > window:
            break
        count += 1
    return round(count / window, 1)


def apply_message(state: dict[str, Any], msg: Any, now: float, wall: float) -> list[tuple[str, Any]]:
    """Update ``state`` from one message and return notes for the caller to turn into events.

    Notes: ("identity", None) on lock, ("heartbeat", None), ("statustext", entry), ("command_ack", ack),
    ("param", name), ("crash", text).
    """
    mtype = msg.get_type()
    if mtype == "BAD_DATA":
        return []
    fields = _record(state, mtype, msg, now)
    notes: list[tuple[str, Any]] = []
    if mtype == "HEARTBEAT":
        _heartbeat(state, msg, fields, notes, now)
        return notes
    if state["sysid"] is None or msg.get_srcSystem() != state["sysid"]:
        return notes
    handler = _HANDLERS.get(mtype)
    if handler is not None:
        handler(state, msg, fields, notes, now, wall)
    return notes


def _heartbeat(state, msg, fields, notes, now):
    if not is_autopilot_heartbeat(msg):
        return
    src = (msg.get_srcSystem(), msg.get_srcComponent(), int(fields["autopilot"]))
    if state["sysid"] is None:
        state["sysid"], state["compid"], state["autopilot_id"] = src
        state["autopilot"] = modes.autopilot_name(src[2])
        notes.append(("identity", None))
    elif src != (state["sysid"], state["compid"], state["autopilot_id"]):
        return
    base = int(fields.get("base_mode") or 0)
    custom = int(fields.get("custom_mode") or 0)
    state.update(
        mav_type=int(fields["type"]),
        base_mode=base,
        custom_mode=custom,
        mode=modes.mode_name(state["autopilot"], base, custom),
        armed=bool(base & M.MAV_MODE_FLAG_SAFETY_ARMED),
        system_status=int(fields.get("system_status") or 0),
        last_heartbeat=now,
    )
    state["heartbeats"] += 1
    notes.append(("heartbeat", None))


def _sensors(present: int, enabled: int, health: int) -> list[dict[str, Any]]:
    return [
        {"name": name, "present": True, "enabled": bool(enabled & bit), "healthy": bool(health & bit)}
        for name, bit in SENSOR_BITS
        if present & bit
    ]


def _sys_status(state, msg, fields, notes, now, wall):
    battery = state["battery"]
    voltage = fields.get("voltage_battery")
    current = fields.get("current_battery")
    remaining = fields.get("battery_remaining")
    if voltage not in (None, 0, 65535):
        battery["voltage"] = round(voltage / 1000.0, 3)
    if current not in (None, -1):
        battery["current"] = round(current / 100.0, 2)
    if remaining not in (None, -1):
        battery["remaining_pct"] = int(remaining)
    present = int(fields.get("onboard_control_sensors_present") or 0)
    enabled = int(fields.get("onboard_control_sensors_enabled") or 0)
    health = int(fields.get("onboard_control_sensors_health") or 0)
    state["sensor_bits"] = (present, enabled, health)
    state["sensors"] = _sensors(present, enabled, health)
    bit = M.MAV_SYS_STATUS_PREARM_CHECK
    state["prearm"] = bool(health & bit) if present & bit else None


def _battery_status(state, msg, fields, notes, now, wall):
    battery = state["battery"]
    cells = [v for v in (fields.get("voltages") or []) if v not in (None, 65535)]
    cells = [v for v in cells if v > 0]
    if cells:
        total = sum(cells) / 1000.0
        battery["voltage"] = round(total, 3)
        if len(cells) > 1:
            battery["cell_voltages"] = [round(v / 1000.0, 3) for v in cells]
            battery["cells"] = len(cells)
    current = fields.get("current_battery")
    if current not in (None, -1):
        battery["current"] = round(current / 100.0, 2)
    remaining = fields.get("battery_remaining")
    if remaining not in (None, -1):
        battery["remaining_pct"] = int(remaining)
    consumed = fields.get("current_consumed")
    if consumed not in (None, -1):
        battery["consumed_mah"] = int(consumed)


def _attitude(state, msg, fields, notes, now, wall):
    state["attitude"] = {
        k: float(fields.get(k) or 0.0)
        for k in ("roll", "pitch", "yaw", "rollspeed", "pitchspeed", "yawspeed")
    }


def _global_position(state, msg, fields, notes, now, wall):
    lat = fields.get("lat") or 0
    lon = fields.get("lon") or 0
    state["global"] = {
        "valid": not (lat == 0 and lon == 0),
        "lat": lat / 1e7,
        "lon": lon / 1e7,
        "alt": (fields.get("alt") or 0) / 1000.0,
        "relative_alt": (fields.get("relative_alt") or 0) / 1000.0,
        "t": now,
    }


def _gps_raw(state, msg, fields, notes, now, wall):
    eph = fields.get("eph")
    sats = fields.get("satellites_visible")
    state["gps"] = {
        "fix": int(fields.get("fix_type") or 0),
        "sats": None if sats in (None, 255) else int(sats),
        "hdop": None if eph in (None, 0, 65535) else round(eph / 100.0, 2),
    }


def _vfr_hud(state, msg, fields, notes, now, wall):
    state["hud"] = {
        k: fields.get(k) for k in ("airspeed", "groundspeed", "heading", "throttle", "alt", "climb")
    }


def _local_position(state, msg, fields, notes, now, wall):
    state["local"] = {k: float(fields.get(k) or 0.0) for k in ("x", "y", "z", "vx", "vy", "vz")}
    state["local"]["t"] = now
    state["local_times"].append(now)
    notes.append(("position", None))


def _rc_channels(state, msg, fields, notes, now, wall):
    count = max(1, min(18, int(fields.get("chancount") or 8)))
    state["rc"] = [int(fields.get(f"chan{i}_raw") or 0) for i in range(1, count + 1)]


def _servo_output(state, msg, fields, notes, now, wall):
    state["servo"] = [int(fields.get(f"servo{i}_raw") or 0) for i in range(1, 9)]
    state["servo16"] = [int(fields.get(f"servo{i}_raw") or 0) for i in range(1, 17)]


def _radio_status(state, msg, fields, notes, now, wall):
    state["radio"] = {k: fields.get(k) for k in ("rssi", "remrssi", "noise", "remnoise", "rxerrors", "fixed")}


def _statustext(state, msg, fields, notes, now, wall):
    text = text_field(fields.get("text"))
    if not text:
        return
    entry = {"t": wall, "severity": int(fields.get("severity") or 0), "text": text}
    state["statustext"].append(entry)
    del state["statustext"][:-STATUSTEXT_KEEP]
    match = FIRMWARE_TEXT.match(text)
    if match and not state["firmware"]:
        state["firmware"] = match.group(2)
    notes.append(("statustext", entry))
    if CRASH_TEXT.search(text) and not state["crashed"]:
        state["crashed"] = True
        notes.append(("crash", text))


def _command_ack(state, msg, fields, notes, now, wall):
    command = int(fields.get("command") or 0)
    result = int(fields.get("result") or 0)
    cmd_entry = M.enums["MAV_CMD"].get(command)
    res_entry = M.enums["MAV_RESULT"].get(result)
    ack = {
        "command": command,
        "command_name": cmd_entry.name.replace("MAV_CMD_", "") if cmd_entry else str(command),
        "result": result,
        "result_name": res_entry.name.replace("MAV_RESULT_", "") if res_entry else str(result),
        "progress": int(fields.get("progress") or 0),
        "t": now,
        "wall": wall,
    }
    state["last_ack"] = ack
    state.setdefault("acks", {})[command] = ack
    notes.append(("command_ack", ack))


def _param_value(state, msg, fields, notes, now, wall):
    # Only the autopilot's own table: a Wi-Fi bridge (mavesp8266, component 240) shares the system id and
    # answers parameter requests with its own short list, which would corrupt the count and the indices.
    if state["compid"] is not None and msg.get_srcComponent() != state["compid"]:
        return
    name = text_field(fields.get("param_id"))
    if not name:
        return
    ptype = int(fields.get("param_type") or M.MAV_PARAM_TYPE_REAL32)
    value = float(msg.param_value)
    if state["autopilot"] == modes.PX4:
        value = px4_param_decode(value, ptype)
    if ptype != M.MAV_PARAM_TYPE_REAL32 and ptype != M.MAV_PARAM_TYPE_REAL64:
        value = float(round(value))
    state["params"][name] = {"value": value, "type": ptype, "index": int(fields.get("param_index") or 0)}
    count = int(fields.get("param_count") or 0)
    if count and count != 65535:
        state["param_count"] = count
    state["param_rx_at"] = now
    notes.append(("param", name))


def _extended_sys_state(state, msg, fields, notes, now, wall):
    state["landed"] = LANDED_NAMES.get(int(fields.get("landed_state") or 0), "unknown")


def _home_position(state, msg, fields, notes, now, wall):
    state["home"] = {
        "lat": (fields.get("latitude") or 0) / 1e7,
        "lon": (fields.get("longitude") or 0) / 1e7,
        "alt": (fields.get("altitude") or 0) / 1000.0,
    }


def _autopilot_version(state, msg, fields, notes, now, wall):
    state["version"] = {
        k: fields.get(k)
        for k in ("flight_sw_version", "board_version", "vendor_id", "product_id", "capabilities")
    }
    # 11 §4: the git hash bytes (decoded per firmware by twin.identity) and the OS version
    custom = getattr(msg, "flight_custom_version", None) or []
    state["version"]["flight_custom_version"] = bytes(bytearray(custom)[:8]).hex()
    state["version"]["os_sw_version"] = fields.get("os_sw_version")
    firmware = firmware_from_version(int(fields.get("flight_sw_version") or 0))
    if firmware:
        state["firmware"] = firmware


def _ekf_status(state, msg, fields, notes, now, wall):
    state["ekf_flags"] = int(fields.get("flags") or 0)


def _estimator_status(state, msg, fields, notes, now, wall):
    state["estimator_flags"] = int(fields.get("flags") or 0)


def _vibration(state, msg, fields, notes, now, wall):
    keys = ("vibration_x", "vibration_y", "vibration_z", "clipping_0", "clipping_1", "clipping_2")
    state["vibration"] = {k: fields.get(k) for k in keys}


def _timesync(state, msg, fields, notes, now, wall):
    if int(fields.get("tc1") or 0) != 0:
        state["timesync"] = {"ts1": int(fields.get("ts1") or 0), "t": now}
        notes.append(("timesync", int(fields.get("ts1") or 0)))


_HANDLERS = {
    "SYS_STATUS": _sys_status,
    "BATTERY_STATUS": _battery_status,
    "ATTITUDE": _attitude,
    "GLOBAL_POSITION_INT": _global_position,
    "GPS_RAW_INT": _gps_raw,
    "VFR_HUD": _vfr_hud,
    "LOCAL_POSITION_NED": _local_position,
    "RC_CHANNELS": _rc_channels,
    "SERVO_OUTPUT_RAW": _servo_output,
    "RADIO_STATUS": _radio_status,
    "STATUSTEXT": _statustext,
    "COMMAND_ACK": _command_ack,
    "PARAM_VALUE": _param_value,
    "EXTENDED_SYS_STATE": _extended_sys_state,
    "HOME_POSITION": _home_position,
    "AUTOPILOT_VERSION": _autopilot_version,
    "EKF_STATUS_REPORT": _ekf_status,
    "ESTIMATOR_STATUS": _estimator_status,
    "VIBRATION": _vibration,
    "TIMESYNC": _timesync,
}
