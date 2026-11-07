"""Step-response analysis (05 §4.2 S10), as pure functions of a sampled signal.

``step_response`` measures one stick step: the response is normalised to its own plateau (the mean of the last
30 % of the step), so the timing figures do not depend on how the autopilot scales sticks to angles or rates.
``judge`` turns the figures into a level, findings and parameter suggestions (05 §4.2: overshoot above 25 %
warns, above 45 % fails; residual oscillation points at P or D being high; a slow rise points at P being low).
Suggestions name a parameter and a direction; nothing is written.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from typing import Any

PLATEAU_SHARE = 0.3  # last share of the step window averaged as the plateau
BASELINE_S = 0.3  # window before the step averaged as the baseline
PEAK_AFTER_S = 0.1  # the peak may come just after release (transport delay)
RELEASE_SETTLE_S = 0.3  # time after release before residual oscillation is measured
SETTLE_BAND = 0.1  # settled when within 10 % of the step amplitude
CROSS_BAND = 0.05  # a residual crossing counts only beyond 5 % of the amplitude
MIN_SAMPLES = 5
REVERSED = -0.3  # normalised plateau below this: the aircraft moved the other way

OVERSHOOT_PCT = (25.0, 45.0)  # (warn, fail)
RESIDUAL_PCT = 15.0  # residual peak-to-peak above this with two crossings: P or D high
SLUGGISH_S = {"roll": 0.3, "pitch": 0.3, "yaw": 0.4}  # 10-90 % rise slower than this: P low
GAIN_ERROR_PCT = 35.0  # steady-state error reported as a note above this
CHANGE_PCT = 10  # suggested change of a gain, percent

# autopilot -> axis -> (P parameter, D parameter or None)
RATE_PARAMS: dict[str, dict[str, tuple[str, str | None]]] = {
    "ArduPilot": {
        "roll": ("ATC_RAT_RLL_P", "ATC_RAT_RLL_D"),
        "pitch": ("ATC_RAT_PIT_P", "ATC_RAT_PIT_D"),
        "yaw": ("ATC_RAT_YAW_P", None),
    },
    "PX4": {
        "roll": ("MC_ROLLRATE_P", "MC_ROLLRATE_D"),
        "pitch": ("MC_PITCHRATE_P", "MC_PITCHRATE_D"),
        "yaw": ("MC_YAWRATE_P", None),
    },
}
AXIS_NAMES = {"roll": ("roll", "横滚"), "pitch": ("pitch", "俯仰"), "yaw": ("yaw", "偏航")}


def _window(t: Sequence[float], y: Sequence[float], lo: float, hi: float) -> list[tuple[float, float]]:
    return [(a, b) for a, b in zip(t, y, strict=False) if lo <= a <= hi and math.isfinite(b)]


def step_response(
    t: Sequence[float],
    y: Sequence[float],
    t_on: float,
    t_off: float,
    expected: float | None = None,
    tail_s: float = 1.2,
) -> dict[str, Any] | None:
    """Figures of one step applied at ``t_on`` and released at ``t_off``; None without enough samples.

    ``expected`` is the commanded change in the units of ``y`` (for the steady-state error), when known.
    """
    before = _window(t, y, t_on - BASELINE_S, t_on - 1e-9)
    during = _window(t, y, t_on, t_off)
    if len(during) < MIN_SAMPLES:
        return None
    baseline = statistics.fmean(v for _, v in before) if before else during[0][1]
    span = t_off - t_on
    tail = [v for a, v in during if a >= t_off - PLATEAU_SHARE * span] or [during[-1][1]]
    plateau = statistics.fmean(tail) - baseline
    reversed_ = False
    if expected is not None and abs(expected) > 1e-9:
        reversed_ = plateau / expected < REVERSED
    amp = plateau if abs(plateau) > 1e-9 else (expected or 1e-9)
    # normalised response: 0 at the baseline, 1 at the plateau
    norm = [(a, (v - baseline) / amp) for a, v in during]
    rise_10 = next((a for a, n in norm if n >= 0.1), None)
    rise_90 = next((a for a, n in norm if n >= 0.9), None)
    rise = None if rise_10 is None or rise_90 is None else max(0.0, rise_90 - rise_10)
    late = _window(t, y, t_on, t_off + PEAK_AFTER_S)
    peak = max((v - baseline) / amp for _, v in late)
    overshoot = max(0.0, (peak - 1.0) * 100.0)
    settle = None
    for i in range(len(norm)):
        if all(abs(n - 1.0) <= SETTLE_BAND for _, n in norm[i:]):
            settle = norm[i][0] - t_on
            break
    after = _window(t, y, t_off + RELEASE_SETTLE_S, t_off + tail_s)
    residual = None
    crossings = 0
    if len(after) >= MIN_SAMPLES:
        dev = [(v - baseline) / abs(amp) for _, v in after]
        residual = (max(dev) - min(dev)) * 100.0
        signs = [1 if d > CROSS_BAND else -1 if d < -CROSS_BAND else 0 for d in dev]
        last = 0
        for s in signs:
            if s and last and s != last:
                crossings += 1
            if s:
                last = s
    ss_error = None
    if expected is not None and abs(expected) > 1e-9:
        ss_error = (1.0 - plateau / expected) * 100.0
    return {
        "samples": len(during),
        "baseline": round(baseline, 4),
        "amplitude": round(plateau, 4),
        "reversed": reversed_,
        "rise_s": None if rise is None else round(rise, 3),
        "overshoot_pct": round(overshoot, 1),
        "settle_s": None if settle is None else round(settle, 3),
        "ss_error_pct": None if ss_error is None else round(ss_error, 1),
        "residual_pct": None if residual is None else round(residual, 1),
        "crossings": crossings,
    }


def _finding(code: str, level: str, en: str, zh: str) -> dict[str, Any]:
    return {"code": code, "level": level, "text_en": en, "text_zh": zh}


def _suggest(name: str, current: float | None, change_pct: int, en: str, zh: str) -> dict[str, Any]:
    return {
        "kind": "param",
        "name": name,
        "current": current,
        "suggested": None if current is None else round(current * (1 + change_pct / 100.0), 5),
        "direction": "increase" if change_pct > 0 else "decrease",
        "change_pct": change_pct,
        "reason_en": en,
        "reason_zh": zh,
        "state": "proposed",
    }


def judge(
    axis: str, figures: dict[str, Any] | None, autopilot: str | None, params: dict[str, float]
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """(level ok | warn | bad | na, findings, suggestions) for one axis."""
    en, zh = AXIS_NAMES[axis]
    if figures is None:
        return (
            "na",
            [_finding(f"{axis}_no_data", "info", f"No {en} response was recorded.", f"未记录到{zh}响应。")],
            [],
        )
    findings: list[dict[str, Any]] = []
    suggestions: list[dict[str, Any]] = []
    p_name, d_name = RATE_PARAMS.get(autopilot or "", {}).get(axis, (None, None))
    if figures["reversed"]:
        findings.append(
            _finding(
                f"{axis}_reversed",
                "fail",
                f"The aircraft moved against the {en} stick. Check "
                "the stick directions and the board orientation.",
                f"飞机的{zh}响应与杆向相反。请检查杆向与飞控安装方向。",
            )
        )
        return "bad", findings, suggestions
    level = "ok"
    overshoot = figures["overshoot_pct"]
    if overshoot > OVERSHOOT_PCT[0]:
        level = "bad" if overshoot > OVERSHOOT_PCT[1] else "warn"
        findings.append(
            _finding(
                f"{axis}_overshoot",
                "fail" if level == "bad" else "warn",
                f"{en.capitalize()} overshoots by {overshoot:.0f} %: the rate P gain is likely high.",
                f"{zh}超调 {overshoot:.0f} %：速率环 P 可能偏高。",
            )
        )
        if p_name:
            suggestions.append(
                _suggest(p_name, params.get(p_name), -CHANGE_PCT, f"{en} overshoot", f"{zh}超调")
            )
    residual = figures["residual_pct"]
    if residual is not None and residual > RESIDUAL_PCT and figures["crossings"] >= 2:
        level = "warn" if level == "ok" else level
        findings.append(
            _finding(
                f"{axis}_residual",
                "warn",
                f"{en.capitalize()} keeps oscillating after the "
                f"step ({residual:.0f} %): P or D is likely high.",
                f"{zh}阶跃后仍有残余振荡（{residual:.0f} %）：P 或 D 可能偏高。",
            )
        )
        if p_name and not any(s["name"] == p_name for s in suggestions):
            suggestions.append(
                _suggest(
                    p_name, params.get(p_name), -CHANGE_PCT, f"{en} residual oscillation", f"{zh}残余振荡"
                )
            )
        if d_name:
            suggestions.append(
                _suggest(
                    d_name, params.get(d_name), -CHANGE_PCT, f"{en} residual oscillation", f"{zh}残余振荡"
                )
            )
    rise = figures["rise_s"]
    if rise is None or rise > SLUGGISH_S[axis]:
        level = "warn" if level == "ok" else level
        shown = "did not reach 90 %" if rise is None else f"rises in {rise:.2f} s"
        shown_zh = "未达到 90 %" if rise is None else f"上升时间 {rise:.2f} 秒"
        findings.append(
            _finding(
                f"{axis}_sluggish",
                "warn",
                f"{en.capitalize()} response is slow ({shown}): the rate P gain is likely low.",
                f"{zh}响应迟缓（{shown_zh}）：速率环 P 可能偏低。",
            )
        )
        if p_name and not any(s["name"] == p_name for s in suggestions):
            suggestions.append(
                _suggest(p_name, params.get(p_name), CHANGE_PCT, f"{en} slow response", f"{zh}响应迟缓")
            )
    ss = figures["ss_error_pct"]
    if ss is not None and abs(ss) > GAIN_ERROR_PCT:
        findings.append(
            _finding(
                f"{axis}_gain",
                "info",
                f"{en.capitalize()} settles {ss:.0f} % away from the commanded angle.",
                f"{zh}稳定值与指令角度相差 {ss:.0f} %。",
            )
        )
    return level, findings, suggestions
