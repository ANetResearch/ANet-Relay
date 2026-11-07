"""Preflight checks (03 §6.3), as a pure function of the vehicle's facts.

Levels: ``block`` items must pass before a real aircraft may arm; ``warn`` and
``info`` items are advisory. Parameter checks report ``waiting`` until the
parameter table has arrived (not passed, but not an error either).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .mav.modes import ARDUPILOT, PX4

TITLES: dict[str, tuple[str, str]] = {
    "link": ("Link is live", "链路在线"),
    "autopilot": ("Autopilot identified", "已识别飞控"),
    "battery": ("Battery above threshold", "电池电压高于门限"),
    "prearm": ("Autopilot pre-arm checks pass", "飞控解锁前自检通过"),
    "gcs_input": ("Autopilot accepts Relay's stick input", "飞控接受 Relay 的杆量输入"),
    "signs": ("Stick directions verified without propellers", "已在无桨状态下确认杆量方向"),
    "props": ("Propellers and safety net checked", "已检查桨叶与防护网"),
    "mode": ("Mode accepts manual input", "当前模式接受手动输入"),
    "failsafe": ("A link-loss failsafe is configured", "已配置失联保护"),
    "position": ("Position estimate available", "有位置估计"),
}
LEVELS = {
    "link": "block",
    "autopilot": "block",
    "battery": "block",
    "prearm": "block",
    "gcs_input": "block",
    "signs": "block",
    "props": "block",
    "mode": "warn",
    "failsafe": "warn",
    "position": "info",
}
ORDER = tuple(TITLES)
WAITING = ("Waiting for parameters", "等待参数")
SIMULATED = ("Simulated", "模拟")
PX4_MAVLINK_INPUT = {1, 2, 3}  # COM_RC_IN_MODE: 0 RC only, 4 stick input disabled


@dataclass
class Facts:
    kind: str = "real"
    link_state: str = "waiting"
    autopilot: str | None = None
    heartbeats: int = 0
    voltage: float | None = None
    cells: int | None = None
    cell_warn_v: float = 3.6
    cell_crit_v: float = 3.45
    prearm: bool | None = None
    params: dict[str, float] = field(default_factory=dict)
    params_complete: bool = False
    params_unreadable: frozenset[str] = frozenset()
    confirmed_signs: bool = False
    confirmed_props: bool = False
    mode: str | None = None
    mode_accepts_sticks: bool = False
    position: bool = False


def _item(check: str, status: str, detail: tuple[str, str] | None = None) -> dict[str, Any]:
    en, zh = TITLES[check]
    return {
        "id": check,
        "level": LEVELS[check],
        "status": status,
        "text_en": en,
        "text_zh": zh,
        "detail_en": detail[0] if detail else None,
        "detail_zh": detail[1] if detail else None,
    }


def _param(f: Facts, *names: str) -> float | None:
    for name in names:
        if name in f.params:
            return f.params[name]
    return None


def _param_status(f: Facts, value_present: bool, *names: str) -> str | None:
    """None when the values are known; "waiting" while they may still arrive; "unreadable" when every name
    was asked for by name repeatedly without an answer and the table is not complete either."""
    if value_present:
        return None
    if f.params_complete:
        return "fail"
    if names and all(n in f.params or n in f.params_unreadable for n in names):
        return "unreadable"
    return "waiting"


def _unreadable(names: tuple[str, ...]) -> tuple[str, str]:
    joined = ", ".join(names)
    return (
        f"Could not read {joined} from the autopilot; check it in the Parameters tab or on the autopilot",
        f"未能从飞控读取 {joined}，请在参数页或飞控上核对",
    )


def evaluate(f: Facts) -> list[dict[str, Any]]:
    sim = f.kind == "sim"
    items = []

    items.append(
        _item(
            "link",
            "pass" if f.link_state == "ok" else "fail",
            (f"Link is {f.link_state}", f"链路{f.link_state}"),
        )
    )

    identified = f.autopilot in (ARDUPILOT, PX4) and f.heartbeats >= 2
    items.append(
        _item(
            "autopilot",
            "pass" if identified else "fail",
            (f.autopilot or "No autopilot heartbeat", f.autopilot or "未收到飞控心跳"),
        )
    )

    if f.voltage is None or not f.cells:
        items.append(_item("battery", "waiting", ("Battery voltage not reported", "未报告电池电压")))
    else:
        per_cell = f.voltage / f.cells
        detail = (
            f"{f.voltage:.2f} V, {per_cell:.2f} V per cell",
            f"{f.voltage:.2f} V，每节 {per_cell:.2f} V",
        )
        if per_cell < f.cell_crit_v:
            items.append(_item("battery", "fail", detail))
        elif per_cell < f.cell_warn_v:
            items.append(_item("battery", "warn", detail))
        else:
            items.append(_item("battery", "pass", detail))

    if f.prearm is None:
        items.append(_item("prearm", "info", ("Not reported by the autopilot", "飞控未报告")))
    else:
        items.append(_item("prearm", "pass" if f.prearm else "fail"))

    if sim:
        items.append(_item("gcs_input", "pass", SIMULATED))
    elif f.autopilot == ARDUPILOT:
        sysid = _param(f, "MAV_GCS_SYSID", "SYSID_MYGCS")
        override = _param(f, "RC_OVERRIDE_TIME")
        pending = _param_status(
            f, sysid is not None and override is not None, "MAV_GCS_SYSID", "SYSID_MYGCS", "RC_OVERRIDE_TIME"
        )
        if pending == "unreadable":
            missing = tuple(
                n for n, x in (("MAV_GCS_SYSID", sysid), ("RC_OVERRIDE_TIME", override)) if x is None
            )
            items.append(_item("gcs_input", "warn", _unreadable(missing)))
        elif pending:
            items.append(
                _item(
                    "gcs_input",
                    pending,
                    WAITING if pending == "waiting" else ("Parameters missing", "缺少参数"),
                )
            )
        elif int(sysid) != 255:  # type: ignore[arg-type]
            items.append(
                _item(
                    "gcs_input",
                    "fail",
                    (
                        f"MAV_GCS_SYSID is {int(sysid)}, expected 255",
                        f"MAV_GCS_SYSID 为 {int(sysid)}，应为 255",
                    ),
                )
            )  # type: ignore[arg-type]
        elif override == 0:
            items.append(
                _item(
                    "gcs_input",
                    "fail",
                    (
                        "RC_OVERRIDE_TIME is 0, overrides are disabled",
                        "RC_OVERRIDE_TIME 为 0，覆盖输入被禁用",
                    ),
                )
            )
        else:
            items.append(_item("gcs_input", "pass"))
    elif f.autopilot == PX4:
        mode = _param(f, "COM_RC_IN_MODE")
        pending = _param_status(f, mode is not None, "COM_RC_IN_MODE")
        if pending == "unreadable":
            items.append(_item("gcs_input", "warn", _unreadable(("COM_RC_IN_MODE",))))
        elif pending:
            items.append(
                _item(
                    "gcs_input",
                    pending,
                    WAITING if pending == "waiting" else ("Parameter missing", "缺少参数"),
                )
            )
        elif int(mode) not in PX4_MAVLINK_INPUT:  # type: ignore[arg-type]
            items.append(
                _item(
                    "gcs_input",
                    "fail",
                    (
                        f"COM_RC_IN_MODE is {int(mode)}, MAVLink input not allowed",
                        f"COM_RC_IN_MODE 为 {int(mode)}，不接受 MAVLink 输入",
                    ),
                )
            )  # type: ignore[arg-type]
        else:
            items.append(_item("gcs_input", "pass"))
    else:
        items.append(_item("gcs_input", "waiting", WAITING))

    for check, confirmed in (("signs", f.confirmed_signs), ("props", f.confirmed_props)):
        if sim:
            items.append(_item(check, "pass", SIMULATED))
        else:
            items.append(
                _item(
                    check,
                    "pass" if confirmed else "fail",
                    None if confirmed else ("Confirm in Relay", "须在 Relay 中确认"),
                )
            )

    items.append(_item("mode", "pass" if f.mode_accepts_sticks else "warn", (f.mode or "-", f.mode or "-")))

    if sim:
        items.append(_item("failsafe", "pass", SIMULATED))
    elif f.autopilot == ARDUPILOT:
        thr = _param(f, "FS_THR_ENABLE")
        gcs = _param(f, "FS_GCS_ENABLE")
        if thr is None and gcs is None:
            items.append(_item("failsafe", "waiting" if not f.params_complete else "warn", WAITING))
        else:
            items.append(_item("failsafe", "pass" if (thr or 0) != 0 or (gcs or 0) != 0 else "warn"))
    elif f.autopilot == PX4:
        action = _param(f, "NAV_RCL_ACT")
        if action is None:
            items.append(_item("failsafe", "waiting" if not f.params_complete else "warn", WAITING))
        else:
            items.append(_item("failsafe", "pass" if action != 0 else "warn"))
    else:
        items.append(_item("failsafe", "waiting", WAITING))

    items.append(_item("position", "pass" if f.position else "info"))
    return items


def summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    blocking = [
        i["id"] for i in items if i["level"] == "block" and i["status"] not in ("pass", "warn", "info")
    ]
    return {"ok": not blocking, "blocking": blocking}
