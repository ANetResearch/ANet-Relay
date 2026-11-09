"""Which of a real aircraft's parameters a firmware twin takes, and the parameter files it starts with
(11 §5).

A twin runs the aircraft's firmware on Relay's simulated sensors, motors and links, so the parameters that
describe the aircraft's hardware stay out: serial ports and their protocols, sensor ids and calibrations,
board orientation and trim, buses, notification devices, ESC protocol and output wiring, battery monitor
wiring, the system id, statistics, and sensors the simulation does not provide. Everything else (control
gains, limits, failsafes, hover thrust, mixer geometry, modes and channel mapping) goes in. ``EXCLUDED`` holds
one list per firmware of ``(pattern, reason)``; a pattern is an ``fnmatch`` pattern on the parameter name.

``split`` applies a list: ``(applied, excluded)`` with ``excluded`` as ``[{name, reason_en, reason_zh}]``.
``ardupilot_parm`` writes an ArduPilot ``--defaults`` file (``NAME VALUE`` per line); ``px4_params`` writes
the file the PX4 pack's ``px4-rc.mavlink`` sources (``. <baseline>``, then ``param set NAME VALUE`` lines).
"""

from __future__ import annotations

import fnmatch
import math
import re
from collections.abc import Iterable
from typing import Any

NAME_RE = re.compile(r"^[A-Z0-9_]{1,16}$")

# (pattern, reason_en, reason_zh)
Rule = tuple[str, str, str]

_SERIAL = ("serial ports and their protocols are the twin's own links", "串口及其协议由孪生自己的链路决定")
_IDS = ("sensor and device ids belong to the board's hardware", "传感器与设备编号属于真机硬件")
_CAL = ("sensor calibration of the real board", "真机飞控板的传感器校准")
_ORIENT = ("board and sensor mounting of the real aircraft", "真机飞控板与传感器的安装方向")
_GPS = ("the twin's GPS is simulated", "孪生的 GPS 为模拟")
_BOARD = ("board hardware settings", "飞控板硬件设置")
_BUS = ("CAN and DroneCAN buses are not simulated", "未模拟 CAN 与 DroneCAN 总线")
_NOTIFY = ("LEDs, buzzers and displays are not simulated", "未模拟指示灯、蜂鸣器与显示")
_SYSID = ("the twin keeps its own system id", "孪生使用自己的系统编号")
_STATS = ("statistics of the real aircraft", "真机的统计数据")
_ESC = ("ESC protocol and output wiring of the real aircraft", "真机的电调协议与输出接线")
_BATT = ("battery monitor wiring of the real board", "真机的电池监测接线")
_SENSOR = ("the simulation does not provide this sensor", "仿真未提供此传感器")
_LOOP = ("the twin runs the main loop rate its lockstep is sized for", "孪生按锁步设计的主循环频率运行")
_SIM = ("simulator settings are Relay's", "仿真参数由 Relay 设定")
_INTERNAL = ("internal bookkeeping of the firmware", "固件内部记录")
_LINK = ("network links of the twin stay off", "孪生不开启其他网络链路")
_HIL = ("hardware-in-the-loop settings are Relay's", "硬件在环设置由 Relay 设定")
_AIRFRAME = ("the twin selects its own simulation airframe", "孪生使用自己的仿真机架")


def _rules(patterns: Iterable[str], reason: tuple[str, str]) -> list[Rule]:
    return [(p, reason[0], reason[1]) for p in patterns]


EXCLUDED: dict[str, list[Rule]] = {
    "ardupilot": [
        *_rules(("SERIAL*",), _SERIAL),
        *_rules(("*_DEV_ID*", "*DEVID*", "INS_*_ID", "COMPASS_PRIO*_ID"), _IDS),
        *_rules(
            (
                "INS_*OFFS*",
                "INS_*SCAL*",
                "INS_ACC*_CALTEMP",
                "INS_GYR*_CALTEMP",
                "INS_TCAL*",
                "TCAL*",
                "COMPASS_OFS*",
                "COMPASS_DIA*",
                "COMPASS_ODI*",
                "COMPASS_MOT*",
                "COMPASS_SCALE*",
                "BARO*_GND_PRESS",
            ),
            _CAL,
        ),
        *_rules(("AHRS_ORIENTATION", "AHRS_TRIM_*", "COMPASS_ORIENT*", "COMPASS_EXTERN*"), _ORIENT),
        *_rules(("GPS_TYPE*", "GPS*_TYPE", "GPS_AUTO_CONFIG", "GPS_AUTO_SWITCH"), _GPS),
        *_rules(("BRD_*",), _BOARD),
        *_rules(("CAN_*",), _BUS),
        *_rules(("NTF_*",), _NOTIFY),
        *_rules(("SYSID_THISMAV",), _SYSID),
        *_rules(("STAT_*",), _STATS),
        *_rules(
            (
                "MOT_PWM_*",
                "SERVO*_FUNCTION",
                "SERVO*_MIN",
                "SERVO*_MAX",
                "SERVO*_TRIM",
                "SERVO*_REVERSED",
                "SERVO_BLH_*",
                "SERVO_DSHOT_*",
                "SERVO_FTW_*",
                "SERVO_ROB_*",
                "SERVO_SBUS_*",
                "SERVO_VOLZ_*",
                "SERVO_GPIO_MASK",
                "SERVO_RATE",
                "SERVO_32_ENABLE",
            ),
            _ESC,
        ),
        *_rules(
            ("BATT*_VOLT_PIN", "BATT*_CURR_PIN", "BATT*_VOLT_MULT", "BATT*_AMP_PERVLT", "BATT*_AMP_OFFSET"),
            _BATT,
        ),
        *_rules(("RNGFND*", "PRX*", "FLOW_*", "ARSPD*", "OA_*", "BCN_*", "VISO_*"), _SENSOR),
        *_rules(("SCHED_LOOP_RATE",), _LOOP),
        *_rules(("SIM_*",), _SIM),
        *_rules(("FORMAT_VERSION", "SYSID_SW_MREV", "SYSID_SW_TYPE", "SYSID_ENFORCE"), _INTERNAL),
    ],
    "px4": [
        *_rules(("CAL_*", "TC_*", "SENS_*"), _CAL),
        *_rules(("SYS_AUTOSTART", "SYS_AUTOCONFIG"), _AIRFRAME),
        *_rules(("SYS_HITL", "HIL_ACT_*"), _HIL),
        *_rules(("SYS_USE_IO", "SYS_DM_BACKEND", "SYS_FAC_CAL_MODE"), _BOARD),
        *_rules(("MAV_*",), _SYSID),
        *_rules(("SER_*", "*_CONFIG", "RC_INPUT_PROTO"), _SERIAL),
        *_rules(("UAVCAN_*", "CANNODE_*"), _BUS),
        *_rules(("PWM_*", "DSHOT_*", "MOT_SLEW_MAX"), _ESC),
        *_rules(("EKF2_*_ID", "*_DEV_ID*"), _IDS),
        *_rules(("BAT*_V_DIV", "BAT*_A_PER_V", "BAT*_V_CHANNEL", "BAT*_I_CHANNEL", "BAT*_SOURCE"), _BATT),
        *_rules(("UXRCE_DDS_*",), _LINK),
        *_rules(("SIM_*",), _SIM),
        *_rules(("LED_*", "TUNE_*"), _NOTIFY),
    ],
}

# Parameters Relay itself sets for every twin after the profile (the lockstep needs them; 11 §6.1).
FORCED: dict[str, dict[str, float]] = {
    "ardupilot": {"SIM_RATE_HZ": 1000.0},
    "px4": {},
}


def exclusion(family: str, name: str) -> tuple[str, str] | None:
    """(reason_en, reason_zh) when ``name`` stays out of a twin of ``family``, else None."""
    for pattern, en, zh in EXCLUDED.get(family, []):
        if fnmatch.fnmatchcase(name, pattern):
            return en, zh
    return None


def split(family: str, params: dict[str, float]) -> tuple[dict[str, float], list[dict[str, str]]]:
    """``(applied, excluded)``: the parameters a twin takes and the ones it leaves out, with the reason."""
    applied: dict[str, float] = {}
    excluded: list[dict[str, str]] = []
    for name in sorted(params):
        value = params[name]
        if not NAME_RE.match(name) or not _finite(value):
            excluded.append({"name": name, "reason_en": "not a valid parameter", "reason_zh": "不是有效参数"})
            continue
        reason = exclusion(family, name)
        if reason is not None:
            excluded.append({"name": name, "reason_en": reason[0], "reason_zh": reason[1]})
        else:
            applied[name] = float(value)
    return applied, excluded


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def format_value(value: float, integer: bool = False) -> str:
    """A parameter value as a parameter file writes it: integers without a fraction, floats round-trip."""
    if integer or float(value).is_integer():
        return str(round(value))
    return repr(float(value))


def ardupilot_parm(params: dict[str, float], header: str = "") -> str:
    """An ArduPilot ``--defaults`` file."""
    lines = [f"# {line}" for line in header.splitlines() if line.strip()]
    lines += [f"{name} {format_value(value)}" for name, value in sorted(params.items())]
    return "\n".join(lines) + "\n"


def px4_params(
    params: dict[str, float],
    types: dict[str, str] | None = None,
    *,
    baseline: list[str] | None = None,
    header: str = "",
) -> str:
    """The PX4 file ``RELAY_PX4_PARAMS`` names: source each baseline file, then ``param set`` the profile.

    ``types`` maps a name to its MAVLink type name (``INT32`` / ``REAL32`` ...); an integer type is written
    without a fraction. Paths are quoted for the shell (a pack path may contain spaces)."""
    types = types or {}
    lines = ["#!/bin/sh", *[f"# {line}" for line in header.splitlines() if line.strip()]]
    for path in baseline or []:
        lines.append(f". {_shell_quote(path)}")
    for name, value in sorted(params.items()):
        integer = types.get(name, "").upper().startswith(("INT", "UINT"))
        lines.append(f"param set {name} {format_value(value, integer)}")
    return "\n".join(lines) + "\n"


def _shell_quote(text: str) -> str:
    return "'" + str(text).replace("'", "'\"'\"'") + "'"


def parse_parm(text: str) -> dict[str, float]:
    """Read an ArduPilot ``NAME VALUE`` file (``#`` comments, comma or whitespace separated)."""
    out: dict[str, float] = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = re.split(r"[\s,]+", line)
        if len(parts) >= 2 and NAME_RE.match(parts[0]):
            try:
                out[parts[0]] = float(parts[1])
            except ValueError:
                continue
    return out


def parse_px4(text: str) -> dict[str, float]:
    """Read ``param set`` / ``param set-default`` lines."""
    out: dict[str, float] = {}
    for raw in text.splitlines():
        parts = raw.split("#", 1)[0].split()
        if len(parts) >= 4 and parts[0] == "param" and parts[1] in ("set", "set-default"):
            try:
                out[parts[2]] = float(parts[3])
            except ValueError:
                continue
    return out


__all__ = [
    "EXCLUDED",
    "FORCED",
    "ardupilot_parm",
    "exclusion",
    "format_value",
    "parse_parm",
    "parse_px4",
    "px4_params",
    "split",
]
