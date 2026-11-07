"""Device-level facts: the raw command catalogue, confirm words and frame helpers (04 §1, §2.1).

Pure data and small functions; the fleet service applies them. Nothing here talks to a vehicle.
"""

from __future__ import annotations

from typing import Any

from .errors import err
from .mav.mavlink import M

# Danger level -> confirm word (04 §1). Real aircraft must carry the word; simulated aircraft need not,
# except for TERMINATE, which both require.
CONFIRM_WORDS: dict[str, str] = {
    "motor": "MOTOR",
    "arm": "ARM",
    "force_arm": "FORCE",
    "reboot": "REBOOT",
    "calibrate": "CALIBRATE",
    "terminate": "TERMINATE",
}
ALWAYS_CONFIRM = frozenset({"terminate"})
# Levels that need a disarmed aircraft, with the phrase used in the must_be_disarmed error.
DISARMED_ACTIONS: dict[str, tuple[str, str]] = {
    "motor": ("testing motors", "测试电机"),
    "reboot": ("rebooting", "重启"),
    "calibrate": ("calibrating", "校准"),
}
FORCE_ARM_MAGIC = 21196.0  # MAV_CMD_COMPONENT_ARM_DISARM param2: skip the autopilot's pre-arm checks

ALL = ["PX4", "ArduPilot"]
PX4_ONLY = ["PX4"]
AP_ONLY = ["ArduPilot"]


def _p(index: int, name: str, unit: str = "", low: float | None = None, high: float | None = None) -> dict:
    out: dict[str, Any] = {"index": index, "name": name, "unit": unit}
    if low is not None:
        out["min"] = low
    if high is not None:
        out["max"] = high
    return out


def _c(
    name: str,
    en: str,
    zh: str,
    params: list[dict],
    danger: str | None = None,
    needs_disarmed: bool = False,
    autopilots: list[str] | None = None,
) -> dict[str, Any]:
    number = int(getattr(M, f"MAV_CMD_{name}"))
    return {
        "id": number,
        "name": f"MAV_CMD_{name}",
        "label_en": en,
        "label_zh": zh,
        "params": params,
        "danger": danger,
        "needs_disarmed": needs_disarmed,
        "autopilots": list(autopilots or ALL),
    }


_LAT = _p(5, "latitude", "deg")
_LON = _p(6, "longitude", "deg")

CATALOGUE: list[dict[str, Any]] = [
    _c(
        "COMPONENT_ARM_DISARM",
        "Arm or disarm",
        "解锁或上锁",
        [_p(1, "arm", "", 0, 1), _p(2, "force (21196 skips checks)")],
        danger="arm",
    ),
    _c(
        "DO_SET_MODE",
        "Set flight mode",
        "设置飞行模式",
        [_p(1, "base mode"), _p(2, "custom mode"), _p(3, "custom sub mode")],
    ),
    _c(
        "NAV_TAKEOFF",
        "Take off",
        "起飞",
        [_p(1, "pitch", "deg"), _p(4, "yaw", "deg"), _p(7, "altitude", "m")],
    ),
    _c(
        "NAV_LAND", "Land", "降落", [_p(4, "yaw", "deg"), _p(5, "latitude", "deg"), _p(6, "longitude", "deg")]
    ),
    _c("NAV_RETURN_TO_LAUNCH", "Return to launch", "返航", []),
    _c(
        "NAV_LOITER_UNLIM",
        "Loiter indefinitely",
        "无限盘旋",
        [_p(3, "radius", "m"), _p(4, "yaw", "deg"), _LAT, _LON, _p(7, "altitude", "m")],
    ),
    _c(
        "DO_CHANGE_SPEED",
        "Change speed",
        "改变速度",
        [_p(1, "speed type"), _p(2, "speed", "m/s"), _p(3, "throttle", "%")],
    ),
    _c(
        "CONDITION_YAW",
        "Turn to heading",
        "转向航向",
        [
            _p(1, "angle", "deg", 0, 360),
            _p(2, "rate", "deg/s"),
            _p(3, "direction"),
            _p(4, "relative", "", 0, 1),
        ],
    ),
    _c(
        "DO_REPOSITION",
        "Reposition",
        "重新定位",
        [_p(1, "speed", "m/s"), _p(2, "bits"), _p(4, "yaw", "rad"), _LAT, _LON, _p(7, "altitude", "m")],
    ),
    _c(
        "DO_SET_HOME",
        "Set home position",
        "设置返航点",
        [_p(1, "use current", "", 0, 1), _p(4, "yaw", "deg"), _LAT, _LON, _p(7, "altitude", "m")],
    ),
    _c(
        "DO_SET_SERVO",
        "Set servo output",
        "设置舵机输出",
        [_p(1, "output", "", 1, 16), _p(2, "pwm", "us", 800, 2200)],
    ),
    _c(
        "DO_REPEAT_SERVO",
        "Repeat servo output",
        "重复舵机输出",
        [_p(1, "output", "", 1, 16), _p(2, "pwm", "us", 800, 2200), _p(3, "cycles"), _p(4, "delay", "s")],
    ),
    _c("DO_SET_RELAY", "Set relay", "设置继电器", [_p(1, "relay", "", 0), _p(2, "state", "", 0, 1)]),
    _c(
        "DO_MOTOR_TEST",
        "Spin one motor",
        "单电机转动测试",
        [
            _p(1, "motor", "", 1, 8),
            _p(2, "throttle type"),
            _p(3, "throttle", "%", 0, 100),
            _p(4, "timeout", "s"),
            _p(5, "motor count"),
        ],
        danger="motor",
        needs_disarmed=True,
        autopilots=AP_ONLY,
    ),
    _c(
        "ACTUATOR_TEST",
        "Test an actuator",
        "执行器测试",
        [_p(1, "value", "", -1, 1), _p(2, "timeout", "s"), _p(5, "output function")],
        danger="motor",
        needs_disarmed=True,
        autopilots=PX4_ONLY,
    ),
    _c(
        "DO_FLIGHTTERMINATION",
        "Flight termination",
        "飞行终止",
        [_p(1, "terminate", "", 0, 1)],
        danger="terminate",
    ),
    _c(
        "PREFLIGHT_CALIBRATION",
        "Calibrate sensors",
        "传感器校准",
        [_p(1, "gyro"), _p(2, "magnetometer"), _p(3, "ground pressure"), _p(5, "accelerometer")],
        danger="calibrate",
        needs_disarmed=True,
    ),
    _c(
        "PREFLIGHT_REBOOT_SHUTDOWN",
        "Reboot or shut down",
        "重启或关机",
        [_p(1, "autopilot", "", 0, 3), _p(2, "onboard computer", "", 0, 3)],
        danger="reboot",
        needs_disarmed=True,
    ),
    _c(
        "SET_MESSAGE_INTERVAL",
        "Set message interval",
        "设置消息间隔",
        [_p(1, "message id"), _p(2, "interval", "us")],
    ),
    _c("REQUEST_MESSAGE", "Request one message", "请求单条消息", [_p(1, "message id")]),
    _c(
        "DO_SET_ROI_LOCATION",
        "Point at a location",
        "指向某位置",
        [_LAT, _LON, _p(7, "altitude", "m")],
    ),
    _c("DO_SET_ROI_NONE", "Clear point of interest", "清除关注点", []),
    _c("MISSION_START", "Start mission", "开始任务", [_p(1, "first item"), _p(2, "last item")]),
    _c("DO_PAUSE_CONTINUE", "Pause or continue", "暂停或继续", [_p(1, "continue", "", 0, 1)]),
    _c("DO_GRIPPER", "Gripper", "夹爪", [_p(1, "instance"), _p(2, "action", "", 0, 1)]),
    _c(
        "DO_DIGICAM_CONTROL",
        "Camera control",
        "相机控制",
        [_p(1, "session"), _p(2, "zoom position"), _p(5, "shoot command")],
    ),
    _c(
        "IMAGE_START_CAPTURE",
        "Start image capture",
        "开始拍照",
        [_p(2, "interval", "s"), _p(3, "total images"), _p(4, "sequence")],
    ),
    _c(
        "VIDEO_START_CAPTURE",
        "Start video",
        "开始录像",
        [_p(1, "stream id"), _p(2, "status frequency", "Hz")],
    ),
    _c("VIDEO_STOP_CAPTURE", "Stop video", "停止录像", [_p(1, "stream id")]),
]
BY_ID = {c["id"]: c for c in CATALOGUE}
BY_NAME = {c["name"]: c for c in CATALOGUE}

CALIBRATIONS: dict[str, tuple[float, ...]] = {
    "gyro": (1, 0, 0, 0, 0, 0, 0),
    "baro": (0, 0, 1, 0, 0, 0, 0),
    "level": (0, 0, 0, 0, 2, 0, 0),
    "accel_simple": (0, 0, 0, 0, 4, 0, 0),
}
GEOGRAPHIC = (0, 3, 5, 6, 10, 11)  # MAV_FRAME values whose x / y are latitude / longitude

_FRAME_NAMES = {
    int(getattr(M, name)): name.removeprefix("MAV_TYPE_")
    for name in dir(M)
    if name.startswith("MAV_TYPE_") and isinstance(getattr(M, name), int)
}
_MOTORS = {
    int(M.MAV_TYPE_QUADROTOR): (4, "quad"),
    int(M.MAV_TYPE_HEXAROTOR): (6, "hex"),
    int(M.MAV_TYPE_OCTOROTOR): (8, "octo"),
    int(M.MAV_TYPE_TRICOPTER): (3, "tri"),
    int(M.MAV_TYPE_COAXIAL): (2, "coaxial"),
}
MESSAGE_IDS: dict[str, int] = {cls.msgname: int(i) for i, cls in M.mavlink_map.items()}


def find_command(spec: Any) -> dict[str, Any]:
    """Catalogue entry for a name (``MAV_CMD_X`` or ``X``) or a number; 400 command_not_allowed otherwise."""
    entry = None
    if isinstance(spec, bool):
        entry = None
    elif isinstance(spec, int):
        entry = BY_ID.get(spec)
    elif isinstance(spec, str):
        text = spec.strip().upper()
        if text.isascii() and text.isdigit():
            entry = BY_ID.get(int(text))
        else:
            entry = BY_NAME.get(text if text.startswith("MAV_CMD_") else f"MAV_CMD_{text}")
    if entry is None:
        raise err("command_not_allowed", command=str(spec))
    return entry


def frame_view(mav_type: int | None) -> dict[str, Any]:
    return {
        "mav_type": mav_type,
        "name": None if mav_type is None else _FRAME_NAMES.get(int(mav_type), "UNKNOWN"),
    }


def motor_layout(mav_type: int | None) -> tuple[int, str]:
    """(motor count, layout name) for a MAV_TYPE; four motors when the frame is unknown."""
    return _MOTORS.get(int(mav_type), (4, "quad")) if mav_type is not None else (4, "quad")


def message_id(name: str) -> int:
    entry = MESSAGE_IDS.get(str(name or "").strip().upper())
    if entry is None:
        raise err("invalid_request", reason=f"{name!r} is not a MAVLink message name")
    return entry


def danger_level(entry: dict[str, Any], params: list[float], motor_count: int) -> str | None:
    """Effective danger of a raw command: the catalogue level, raised by what the parameters ask for."""
    level = entry["danger"]
    if entry["id"] == M.MAV_CMD_COMPONENT_ARM_DISARM and len(params) > 1 and params[1] == FORCE_ARM_MAGIC:
        return "force_arm"
    if (
        entry["id"] in (M.MAV_CMD_DO_SET_SERVO, M.MAV_CMD_DO_REPEAT_SERVO)
        and params
        and 1 <= params[0] <= motor_count
    ):
        return "motor"
    return level


def require_confirm(level: str | None, kind: str, confirm: str | None) -> None:
    """400 confirm_required unless the word matches (real aircraft; TERMINATE also on simulated ones)."""
    if level is None:
        return
    word = CONFIRM_WORDS.get(level)
    if word is None:
        return
    if kind != "real" and level not in ALWAYS_CONFIRM:
        return
    if confirm != word:
        raise err("confirm_required", expected=word)


def require_disarmed(level: str | None, armed: bool) -> None:
    if level in DISARMED_ACTIONS and armed:
        en, zh = DISARMED_ACTIONS[level]
        error = err("must_be_disarmed", action=en)
        error.message_zh = f"{zh}前须先给飞机上锁。"
        raise error


def is_motor_output(index: int, motor_count: int) -> bool:
    return 1 <= index <= motor_count
