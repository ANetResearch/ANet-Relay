"""Parameter baseline and recommended ranges (05 §4.2 S3), and the hover-learning items (S11).

The ranges are deliberately wide: they flag values that are unusual for a small multirotor, they are not
tuning targets. Nothing here writes a parameter; S3 only warns and S11 proposes values the operator applies
one by one.
"""

from __future__ import annotations

from typing import Any

# autopilot -> [(parameter, low, high, English note, Chinese note)];
# a high of None means a "not 0" check (see check_ranges)
RANGES: dict[str, list[tuple[str, float | None, float | None, str, str]]] = {
    "ArduPilot": [
        ("ANGLE_MAX", 1500.0, 4500.0, "tilt limit, centidegrees", "倾角上限，0.01 度"),
        ("ATC_RAT_RLL_P", 0.05, 0.3, "roll rate P", "横滚速率 P"),
        ("ATC_RAT_RLL_I", 0.05, 0.3, "roll rate I", "横滚速率 I"),
        ("ATC_RAT_RLL_D", 0.0005, 0.012, "roll rate D", "横滚速率 D"),
        ("ATC_RAT_PIT_P", 0.05, 0.3, "pitch rate P", "俯仰速率 P"),
        ("ATC_RAT_PIT_I", 0.05, 0.3, "pitch rate I", "俯仰速率 I"),
        ("ATC_RAT_PIT_D", 0.0005, 0.012, "pitch rate D", "俯仰速率 D"),
        ("ATC_RAT_YAW_P", 0.1, 0.6, "yaw rate P", "偏航速率 P"),
        ("MOT_THST_HOVER", 0.15, 0.65, "hover throttle", "悬停油门"),
        ("FS_THR_ENABLE", 1.0, None, "radio failsafe enabled", "遥控失控保护已启用"),
        ("RC_OVERRIDE_TIME", 0.5, 10.0, "override hold time, s", "覆盖保持时间，秒"),
    ],
    "PX4": [
        ("MPC_MAN_TILT_MAX", 15.0, 45.0, "tilt limit, degrees", "倾角上限，度"),
        ("MC_ROLLRATE_P", 0.05, 0.35, "roll rate P", "横滚速率 P"),
        ("MC_ROLLRATE_I", 0.05, 0.4, "roll rate I", "横滚速率 I"),
        ("MC_ROLLRATE_D", 0.0005, 0.012, "roll rate D", "横滚速率 D"),
        ("MC_PITCHRATE_P", 0.05, 0.35, "pitch rate P", "俯仰速率 P"),
        ("MC_PITCHRATE_I", 0.05, 0.4, "pitch rate I", "俯仰速率 I"),
        ("MC_PITCHRATE_D", 0.0005, 0.012, "pitch rate D", "俯仰速率 D"),
        ("MC_YAWRATE_P", 0.1, 0.6, "yaw rate P", "偏航速率 P"),
        ("MPC_THR_HOVER", 0.15, 0.65, "hover throttle", "悬停油门"),
        ("NAV_RCL_ACT", 1.0, None, "RC loss action set", "已设置遥控丢失动作"),
        ("COM_RC_LOSS_T", 0.2, 2.0, "RC loss timeout, s", "遥控丢失超时，秒"),
    ],
}
# Recorded as the frame identity, not judged.
FRAME_PARAMS = {"ArduPilot": ("FRAME_CLASS", "FRAME_TYPE"), "PX4": ("SYS_AUTOSTART",)}
# Failsafe parameters recorded with the baseline.
FAILSAFE_PARAMS = {
    "ArduPilot": ("FS_THR_ENABLE", "FS_GCS_ENABLE", "RC_OVERRIDE_TIME"),
    "PX4": ("NAV_RCL_ACT", "NAV_DLL_ACT", "COM_RC_LOSS_T"),
}
HOVER_PARAM = {"ArduPilot": "MOT_THST_HOVER", "PX4": "MPC_THR_HOVER"}
HOVER_PARAM_RANGE = {"ArduPilot": (0.125, 0.6875), "PX4": (0.1, 0.8)}
RELAY_HOVER = (0.2, 0.7)  # calibration.hover range (set_calibration)


def baseline_names(autopilot: str | None) -> list[str]:
    """Every parameter S3 reads and records, in a stable order."""
    names: list[str] = []
    for name in (
        *FRAME_PARAMS.get(autopilot or "", ()),
        *(r[0] for r in RANGES.get(autopilot or "", [])),
        *FAILSAFE_PARAMS.get(autopilot or "", ()),
    ):
        if name not in names:
            names.append(name)
    return names


def check_ranges(autopilot: str | None, values: dict[str, float]) -> list[dict[str, Any]]:
    """Findings for parameters outside the recommended ranges (warn) or missing (info)."""
    out: list[dict[str, Any]] = []
    for name, low, high, en, zh in RANGES.get(autopilot or "", []):
        value = values.get(name)
        if value is None:
            out.append(
                {
                    "code": f"param_missing_{name}",
                    "level": "info",
                    "text_en": f"{name} ({en}) was not reported by the autopilot.",
                    "text_zh": f"飞控未报告 {name}（{zh}）。",
                }
            )
            continue
        if high is None:
            bad = low is not None and value < low
            if bad:
                out.append(
                    {
                        "code": f"param_off_{name}",
                        "level": "warn",
                        "text_en": f"{name} is {value:g}: the {en} check expects a non-zero value.",
                        "text_zh": f"{name} 为 {value:g}：{zh}检查要求非零值。",
                    }
                )
            continue
        if low is not None and not low <= value <= high:
            out.append(
                {
                    "code": f"param_range_{name}",
                    "level": "warn",
                    "text_en": f"{name} ({en}) is {value:g}, outside the usual {low:g} to {high:g}.",
                    "text_zh": f"{name}（{zh}）为 {value:g}，超出常见范围 {low:g} 至 {high:g}。",
                }
            )
    return out


def hover_items(
    autopilot: str | None, hover_pct: float, params: dict[str, float], relay_hover: float
) -> list[dict[str, Any]]:
    """The two S11 proposals: the autopilot's hover parameter and Relay's calibration.hover."""
    share = hover_pct / 100.0
    items: list[dict[str, Any]] = []
    name = HOVER_PARAM.get(autopilot or "")
    if name is not None:
        low, high = HOVER_PARAM_RANGE[autopilot or ""]
        current = params.get(name)
        suggested = round(min(high, max(low, share)), 3)
        items.append(
            {
                "kind": "param",
                "name": name,
                "current": current,
                "suggested": suggested,
                "direction": "set",
                "change_pct": None if not current else round((suggested - current) / current * 100.0),
                "reason_en": f"Measured hover throttle {hover_pct:.0f} %",
                "reason_zh": f"实测悬停油门 {hover_pct:.0f} %",
                "state": "proposed" if current is not None else "unavailable",
            }
        )
    low, high = RELAY_HOVER
    suggested = round(min(high, max(low, share)), 3)
    items.append(
        {
            "kind": "relay",
            "name": "calibration.hover",
            "current": round(relay_hover, 3),
            "suggested": suggested,
            "direction": "set",
            "change_pct": None if not relay_hover else round((suggested - relay_hover) / relay_hover * 100.0),
            "reason_en": f"Measured hover throttle {hover_pct:.0f} %",
            "reason_zh": f"实测悬停油门 {hover_pct:.0f} %",
            "state": "proposed",
        }
    )
    return items
