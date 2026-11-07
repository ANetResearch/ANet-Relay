"""Procedure catalogue (05 §4.2): the five procedures, their steps and the bilingual labels of the report.

Pure data. A procedure is an ordered list of step ids with per-step configuration; the step classes live in
``steps.py`` and read their configuration from here.
"""

from __future__ import annotations

from typing import Any

# step id -> (English title, Chinese title, kind, one-line description EN, ZH). Kinds follow 05 §4.2:
# auto (no operator), operator (a person confirms), operator_device (a person starts a device operation),
# device (Relay drives the device) and flight (an embedded sequence flies the aircraft).
STEPS: dict[str, tuple[str, str, str, str, str]] = {
    "S1": (
        "Link and firmware",
        "链路与固件",
        "auto",
        "Heartbeat, autopilot type and version, parameter table, link loss and round trip.",
        "心跳、飞控类型与版本、参数表、链路丢包与往返时延。",
    ),
    "S2": (
        "Battery at rest",
        "电池静息",
        "auto",
        "Resting voltage per cell against the warning and critical thresholds.",
        "每节静息电压与告警、严重门限比较。",
    ),
    "S3": (
        "Parameter baseline",
        "参数基线",
        "auto",
        "Frame, tilt limit, rate-loop gains, hover throttle and "
        "failsafe parameters against the recommended ranges.",
        "机架、倾角上限、速率环增益、悬停油门与失控保护参数，与推荐范围比较。",
    ),
    "S4": (
        "Sensor calibration",
        "传感器校准",
        "operator_device",
        "Gyroscope, level and barometer calibration, one after another.",
        "依次进行陀螺、水平与气压校准。",
    ),
    "S5": (
        "Stick directions",
        "杆向",
        "operator",
        "Push each axis and confirm that the aircraft responds in the same direction.",
        "逐轴推杆，确认飞机响应方向与杆向一致。",
    ),
    "S6": (
        "Motor order and direction",
        "电机顺序与转向",
        "operator_device",
        "Each motor spins alone at low throttle; confirm its position and direction against the layout.",
        "逐个以低油门转动电机，对照布局图确认位置与转向。",
    ),
    "S7": (
        "Ground vibration",
        "地面振动",
        "device",
        "All motors spin without propellers while vibration is recorded at 10 Hz.",
        "拆桨状态下转动全部电机，以 10 Hz 记录振动。",
    ),
    "S8": (
        "Propellers fitted",
        "装桨确认",
        "operator",
        "Fit the propellers, check their direction and that they are tight.",
        "装桨，检查桨向与紧固。",
    ),
    "S9": (
        "Hover test",
        "悬停测试",
        "flight",
        "Take off to 0.6 m, hover and measure stability, hover throttle, motors, vibration and battery sag.",
        "起飞至 0.6 m 悬停，测量稳定性、悬停油门、电机输出、振动与压降。",
    ),
    "S10": (
        "Step response",
        "阶跃响应",
        "flight",
        "Short stick steps on roll, pitch and yaw in altitude hold; rise time, overshoot and settling.",
        "定高模式下对横滚、俯仰、偏航给短时阶跃，测上升时间、超调与调节时间。",
    ),
    "S11": (
        "Hover learning",
        "悬停学习",
        "operator_device",
        "Apply the measured hover throttle to the autopilot and to Relay, item by item.",
        "将实测悬停油门逐项写入飞控与 Relay。",
    ),
    "S12": (
        "Report",
        "报告",
        "auto",
        "Overall verdict and the saved report.",
        "汇总判定并保存报告。",
    ),
}
STEP_IDS = tuple(STEPS)
FLIGHT_STEPS = frozenset(k for k, v in STEPS.items() if v[2] == "flight")
# Steps that a person must answer on a real aircraft (05 §4.3): an agent may answer them on a simulator only.
OPERATOR_ONLY = frozenset({"S5", "S6", "S8"})
# Rough durations for the catalogue's estimate, seconds (the hover length is added for S9).
EST_S = {
    "S1": 5,
    "S2": 3,
    "S3": 5,
    "S4": 60,
    "S5": 45,
    "S6": 60,
    "S7": 25,
    "S8": 30,
    "S9": 30,
    "S10": 30,
    "S11": 20,
    "S12": 1,
}

HOVER_S = (5.0, 60.0)  # options.hover_s range
DEFAULT_HOVER_S = 15.0

# id -> (English title, Chinese title, steps, per-step configuration)
PROCEDURES: dict[str, tuple[str, str, tuple[str, ...], dict[str, dict[str, Any]]]] = {
    "full_bench": ("Full bench check", "完整标定与测试", STEP_IDS, {}),
    "preflight_quick": (
        "Quick preflight",
        "快速起飞前检查",
        ("S1", "S2", "S7", "S9", "S12"),
        {"S9": {"hover_s": 10.0}},
    ),
    "motor_check": ("Motor order and direction", "电机顺序与转向", ("S1", "S6", "S12"), {}),
    "vibration_check": (
        "Vibration",
        "振动",
        ("S1", "S7", "S9", "S12"),
        {"S9": {"hover_s": 20.0, "only": ("vibration",)}},
    ),
    "hover_tune": (
        "Hover and stability",
        "悬停与稳定性",
        ("S1", "S8", "S9", "S10", "S11", "S12"),
        {},
    ),
}

VERDICTS: dict[str, tuple[str, str]] = {
    "pass": ("passed", "通过"),
    "warn": ("passed with warnings", "有警告"),
    "fail": ("failed", "失败"),
    "skipped": ("skipped", "跳过"),
}

# metric key -> (English label, Chinese label)
METRICS: dict[str, tuple[str, str]] = {
    "heartbeat_age_s": ("Heartbeat age", "心跳间隔"),
    "autopilot": ("Autopilot", "飞控"),
    "firmware": ("Firmware", "固件"),
    "params_loaded": ("Parameters loaded", "已载入参数"),
    "loss_pct": ("Link loss", "链路丢包"),
    "rtt_ms": ("Round trip", "往返时延"),
    "voltage": ("Pack voltage", "电池电压"),
    "cells": ("Cells", "电芯数"),
    "cell_v": ("Voltage per cell", "每节电压"),
    "frame": ("Frame", "机架"),
    "gyro": ("Gyroscope", "陀螺"),
    "level": ("Level", "水平"),
    "baro": ("Barometer", "气压计"),
    "motors_ok": ("Motors confirmed", "已确认电机"),
    "vibration_x": ("Vibration X", "X 轴振动"),
    "vibration_y": ("Vibration Y", "Y 轴振动"),
    "vibration_z": ("Vibration Z", "Z 轴振动"),
    "vibration_rest": ("Vibration at rest", "静止振动"),
    "clipping": ("Accelerometer clipping", "加速度计削波"),
    "motors_spun": ("Motors spinning", "转动的电机"),
    "oscillation": ("Oscillation", "振荡"),
    "lean": ("Static lean", "静态倾斜"),
    "yaw_drift": ("Yaw drift", "偏航漂移"),
    "alt_hold": ("Altitude hold", "高度保持"),
    "hover_throttle": ("Hover throttle", "悬停油门"),
    "motors": ("Motor output spread", "电机输出差"),
    "vibration": ("Vibration", "振动"),
    "battery_sag": ("Battery sag", "电池压降"),
    "hover_s": ("Hover time", "悬停时长"),
    "rise_s": ("Rise time", "上升时间"),
    "overshoot_pct": ("Overshoot", "超调"),
    "settle_s": ("Settling time", "调节时间"),
    "ss_error_pct": ("Steady-state error", "稳态误差"),
    "residual_pct": ("Residual oscillation", "残余振荡"),
    "hover_suggested": ("Measured hover throttle", "实测悬停油门"),
    "overall": ("Overall", "总体"),
    "steps_failed": ("Steps failed", "失败步骤"),
    "steps_warned": ("Steps with warnings", "有警告步骤"),
}
AXES = {"roll": ("Roll", "横滚"), "pitch": ("Pitch", "俯仰"), "yaw": ("Yaw", "偏航")}


def step_title(step_id: str) -> tuple[str, str]:
    entry = STEPS[step_id]
    return entry[0], entry[1]


def estimate_s(
    steps: tuple[str, ...], config: dict[str, dict[str, Any]], hover_s: float | None = None
) -> int:
    total = 0.0
    for sid in steps:
        total += EST_S[sid]
        if sid == "S9":
            total += (
                hover_s
                if hover_s is not None
                else float(config.get("S9", {}).get("hover_s", DEFAULT_HOVER_S))
            )
    return round(total)


def catalogue_view() -> list[dict[str, Any]]:
    """GET /procedures (05 §4.4)."""
    out = []
    for pid, (en, zh, steps, config) in PROCEDURES.items():
        out.append(
            {
                "id": pid,
                "title_en": en,
                "title_zh": zh,
                "steps": [
                    {"id": s, "title_en": STEPS[s][0], "title_zh": STEPS[s][1], "kind": STEPS[s][2]}
                    for s in steps
                ],
                "est_s": estimate_s(steps, config),
            }
        )
    return out
