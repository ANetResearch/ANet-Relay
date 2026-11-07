"""Window statistics for the bench steps (05 §4.2 S7, S9), built on flight health (03 §5.9).

``HoverWindow`` feeds a private ``FlightHealth`` for the whole hover and takes its view once a second, so
every figure uses the same statistics and thresholds as the live health block; the report keeps the mean and
the peak of each figure over the window. ``VibrationWindow`` records the raw VIBRATION message (any arming
state) for the ground test, judged at half the flight thresholds.
"""

from __future__ import annotations

import statistics
from typing import Any

from .. import flighthealth as fh
from .catalogue import METRICS

HOVER_KEYS = (
    "oscillation",
    "lean",
    "yaw_drift",
    "alt_hold",
    "hover_throttle",
    "motors",
    "vibration",
    "battery_sag",
)
THRESHOLDS: dict[str, tuple[float, float]] = {
    "oscillation": fh.RATE_RMS_DPS,
    "lean": fh.LEAN_DEG,
    "yaw_drift": fh.YAW_DRIFT_DPS,
    "alt_hold": fh.CLIMB_STD_MS,
    "motors": fh.MOTOR_SPREAD_US,
    "vibration": fh.VIBRATION_MS2,
    "battery_sag": fh.SAG_PER_CELL_V,
}
UNITS = {
    "oscillation": "deg/s",
    "lean": "deg",
    "yaw_drift": "deg/s",
    "alt_hold": "m/s",
    "hover_throttle": "%",
    "motors": "us",
    "vibration": "m/s2",
    "battery_sag": "V/cell",
}
GROUND_SHARE = 0.5  # ground vibration thresholds are half the flight ones (05 §4.2 S7)
VIEW_PERIOD_S = 1.0
ORDER = ("na", "ok", "warn", "bad")


def worst(levels: list[str]) -> str:
    return max(levels, key=ORDER.index) if levels else "na"


def level_for(key: str, value: float | None) -> str:
    """The flight-health level of one figure (the thresholds of flighthealth.py)."""
    if value is None:
        return "na"
    if key == "hover_throttle":
        if value >= fh.HOVER_BAD_PCT:
            return "bad"
        return "warn" if value >= fh.HOVER_HIGH_PCT or value < fh.HOVER_LOW_PCT else "ok"
    warn, bad = THRESHOLDS[key]
    return fh._level(value, warn, bad)


def metric(
    key: str,
    value: Any,
    unit: str = "",
    level: str = "info",
    *,
    mean: float | None = None,
    peak: float | None = None,
    warn: float | None = None,
    bad: float | None = None,
    label: tuple[str, str] | None = None,
) -> dict[str, Any]:
    en, zh = label or METRICS.get(key, (key, key))
    out: dict[str, Any] = {
        "key": key,
        "label_en": en,
        "label_zh": zh,
        "value": round(value, 3) if isinstance(value, float) else value,
        "unit": unit,
        "level": level,
    }
    if mean is not None:
        out["mean"] = round(mean, 3)
    if peak is not None:
        out["peak"] = round(peak, 3)
    if warn is not None:
        out["warn"] = warn
    if bad is not None:
        out["bad"] = bad
    return out


def finding(code: str, level: str, en: str, zh: str) -> dict[str, Any]:
    return {"code": code, "level": level, "text_en": en, "text_zh": zh}


def _hover_finding(key: str, value: float, level: str, detail: dict[str, Any]) -> dict[str, Any]:
    tag = "fail" if level == "bad" else "warn"
    if key == "oscillation":
        return finding(
            key,
            tag,
            f"Roll and pitch rates oscillate at {value:.1f} deg/s "
            "RMS. Check propeller balance, frame stiffness "
            "and the rate gains.",
            f"横滚与俯仰角速度振荡 {value:.1f} deg/s RMS。请检查桨叶动平衡、机架刚度与速率环增益。",
        )
    if key == "lean":
        return finding(
            key,
            tag,
            f"The aircraft leans {value:.1f} deg with the sticks "
            "centred. Recalibrate level and check the centre "
            "of gravity.",
            f"杆量回中时飞机倾斜 {value:.1f} 度。请重新进行水平校准并检查重心。",
        )
    if key == "yaw_drift":
        return finding(
            key,
            tag,
            f"Yaw drifts {value:.1f} deg/s with the yaw stick "
            "centred. Check the motor directions and the compass.",
            f"偏航杆回中时偏航漂移 {value:.1f} deg/s。请检查电机转向与罗盘。",
        )
    if key == "alt_hold":
        return finding(
            key,
            tag,
            f"Vertical speed varies by {value:.2f} m/s in altitude "
            "hold. Shield the barometer from propeller wash.",
            f"定高时垂直速度波动 {value:.2f} m/s。请为气压计遮挡桨洗气流。",
        )
    if key == "hover_throttle":
        if value < fh.HOVER_LOW_PCT:
            return finding(
                key,
                tag,
                f"Hover needs only {value:.0f} % throttle. Lower "
                "the hover throttle parameter so altitude hold "
                "keeps its range.",
                f"悬停仅需 {value:.0f} % 油门。请相应降低悬停油门参数，保证定高的调节范围。",
            )
        return finding(
            key,
            tag,
            f"Hover needs {value:.0f} % throttle: the aircraft "
            "is heavy for its motors or the battery is low.",
            f"悬停需要 {value:.0f} % 油门：飞机相对电机偏重，或电池电量不足。",
        )
    if key == "motors":
        high = detail.get("high")
        return finding(
            key,
            tag,
            f"Motor outputs differ by {value:.0f} us; motor {high} "
            "works hardest. Check the centre of gravity, that "
            "motor and its propeller.",
            f"电机输出相差 {value:.0f} us，电机 {high} 负荷最大。请检查重心、该电机及其桨叶。",
        )
    if key == "vibration":
        return finding(
            key,
            tag,
            f"Vibration peaks at {value:.1f} m/s2. Balance the "
            "propellers and soft-mount the flight controller.",
            f"振动峰值 {value:.1f} m/s2。请做桨叶动平衡并为飞控加装减震。",
        )
    return finding(
        key,
        tag,
        f"The battery sags {value:.2f} V per cell under load. "
        "The pack is worn or too small for this aircraft.",
        f"负载下每节电压下降 {value:.2f} V。电池老化或容量不足。",
    )


class HoverWindow:
    """Mean and peak of the flight-health figures over a hover (05 §4.2 S9)."""

    def __init__(self, idle_volts: float | None, only: tuple[str, ...] | None = None) -> None:
        self.health = fh.FlightHealth()
        self.health.idle_volts = idle_volts
        self.keys = tuple(k for k in HOVER_KEYS if not only or k in only)
        self.values: dict[str, list[float]] = {k: [] for k in self.keys}
        self.detail: dict[str, dict[str, Any]] = {}
        self.vib: list[float] = []
        self.vib_at: float | None = None
        self.next_view = 0.0
        self.started: float | None = None
        self.ended: float | None = None

    def sample(self, v: Any, now: float, sticks: Any, hold: bool) -> None:
        if self.started is None:
            self.started = now
        self.ended = now
        self.health.sample(v, now, sticks, hold)
        entry = v.state["messages"].get("VIBRATION")
        vib = v.state["vibration"]
        if entry is not None and vib and entry["t"] != self.vib_at:
            self.vib_at = entry["t"]
            axes = [
                float(vib[k]) for k in ("vibration_x", "vibration_y", "vibration_z") if vib.get(k) is not None
            ]
            if axes:
                self.vib.append(max(axes))
        if now < self.next_view:
            return
        self.next_view = now + VIEW_PERIOD_S
        view = self.health.view(v, now, {}, False)
        for item in view["items"]:
            key = item["key"]
            if key not in self.values or item["level"] == "na" or item["value"] is None:
                continue
            if key == "vibration":
                continue  # from the raw samples below
            self.values[key].append(float(item["value"]))
            if item.get("detail"):
                self.detail[key] = item["detail"]

    @property
    def duration_s(self) -> float:
        return 0.0 if self.started is None or self.ended is None else self.ended - self.started

    def hover_pct(self) -> float | None:
        values = self.values.get("hover_throttle") or []
        return statistics.fmean(values) if values else None

    def result(self) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
        """(level, metrics, findings)."""
        metrics = [metric("hover_s", round(self.duration_s, 1), "s")]
        findings: list[dict[str, Any]] = []
        levels: list[str] = []
        for key in self.keys:
            if key == "vibration":
                values = self.vib
            else:
                values = self.values[key]
            if not values:
                metrics.append(metric(key, None, UNITS[key], "na"))
                continue
            mean = statistics.fmean(values)
            peak = max(values)
            if key == "battery_sag":
                mean = peak = values[-1]  # cumulative: lowest voltage seen
            level = level_for(key, mean)
            if level == "ok" and level_for(key, peak) == "bad" and key != "hover_throttle":
                level = "warn"
            detail = self.detail.get(key, {})
            if key == "motors" and detail.get("saturated") is not None:
                level = worst([level, fh._level(float(detail["saturated"]), *fh.MOTOR_SAT_FRAC)])
            if key == "vibration":
                clipped = None
                if self.health.clip_now is not None and self.health.clip_base is not None:
                    clipped = self.health.clip_now - self.health.clip_base
                if clipped:
                    level = "bad"
                    findings.append(
                        finding(
                            "clipping",
                            "fail",
                            f"The accelerometer clipped {clipped} times in the hover.",
                            f"悬停期间加速度计削波 {clipped} 次。",
                        )
                    )
            warn, bad = THRESHOLDS.get(key, (fh.HOVER_HIGH_PCT, fh.HOVER_BAD_PCT))
            metrics.append(metric(key, mean, UNITS[key], level, mean=mean, peak=peak, warn=warn, bad=bad))
            levels.append(level)
            if level in ("warn", "bad"):
                findings.append(_hover_finding(key, peak if key == "vibration" else mean, level, detail))
        return worst(levels), metrics, findings


class VibrationWindow:
    """Raw VIBRATION samples of a ground spin, with the clipping counters (05 §4.2 S7)."""

    def __init__(self) -> None:
        self.rest: list[float] = []
        self.spin: dict[str, list[float]] = {"vibration_x": [], "vibration_y": [], "vibration_z": []}
        self.clip_base: int | None = None
        self.clip_now: int | None = None
        self.seen_at: float | None = None
        self.motors_seen: set[int] = set()

    def sample(self, v: Any, spinning: bool, motor_count: int) -> None:
        entry = v.state["messages"].get("VIBRATION")
        vib = v.state["vibration"]
        if spinning:
            servo = v.state["servo16"] or []
            for i, us in enumerate(servo[:motor_count], 1):
                if us and us > fh.MOTOR_MIN_US:
                    self.motors_seen.add(i)
        if entry is None or not vib or entry["t"] == self.seen_at:
            return
        self.seen_at = entry["t"]
        clip = sum(int(vib.get(k) or 0) for k in ("clipping_0", "clipping_1", "clipping_2"))
        if self.clip_base is None:
            self.clip_base = clip
        self.clip_now = clip
        if not spinning:
            axes = [float(vib[k]) for k in self.spin if vib.get(k) is not None]
            if axes:
                self.rest.append(max(axes))
            return
        for k in self.spin:
            if vib.get(k) is not None:
                self.spin[k].append(float(vib[k]))

    def result(self, motor_count: int) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
        warn, bad = (fh.VIBRATION_MS2[0] * GROUND_SHARE, fh.VIBRATION_MS2[1] * GROUND_SHARE)
        metrics: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        levels: list[str] = []
        if self.rest:
            metrics.append(metric("vibration_rest", statistics.fmean(self.rest), "m/s2", "info"))
        for key, values in self.spin.items():
            if not values:
                metrics.append(metric(key, None, "m/s2", "na", warn=warn, bad=bad))
                continue
            mean, peak = statistics.fmean(values), max(values)
            level = fh._level(peak, warn, bad)
            levels.append(level)
            metrics.append(metric(key, mean, "m/s2", level, mean=mean, peak=peak, warn=warn, bad=bad))
            if level != "ok":
                axis = key[-1].upper()
                findings.append(
                    finding(
                        key,
                        "fail" if level == "bad" else "warn",
                        f"Ground vibration on {axis} peaks at {peak:.1f} "
                        "m/s2 without propellers. Check the motor "
                        "bearings, the motor mounts and the flight controller mount.",
                        f"拆桨状态下 {axis} 轴振动峰值 {peak:.1f} m/s2。请检查电机轴承、电机座与飞控安装。",
                    )
                )
        clipped = None if self.clip_now is None or self.clip_base is None else self.clip_now - self.clip_base
        metrics.append(metric("clipping", clipped, "", "bad" if clipped else "ok" if clipped == 0 else "na"))
        if clipped:
            levels.append("bad")
            findings.append(
                finding(
                    "clipping",
                    "fail",
                    f"The accelerometer clipped {clipped} times on the ground.",
                    f"地面测试中加速度计削波 {clipped} 次。",
                )
            )
        metrics.append(metric("motors_spun", len(self.motors_seen), "", "info"))
        if not any(self.spin.values()):
            findings.append(
                finding(
                    "no_vibration",
                    "warn",
                    "The aircraft sent no VIBRATION messages during the spin.",
                    "转动期间飞机未发送 VIBRATION 消息。",
                )
            )
            levels.append("warn")
        elif self.motors_seen and len(self.motors_seen) < motor_count:
            findings.append(
                finding(
                    "motors_missing",
                    "warn",
                    f"Only {len(self.motors_seen)} of {motor_count} motor outputs rose during the spin.",
                    f"转动期间仅 {len(self.motors_seen)}/{motor_count} 路电机输出升高。",
                )
            )
            levels.append("warn")
        return worst(levels), metrics, findings
