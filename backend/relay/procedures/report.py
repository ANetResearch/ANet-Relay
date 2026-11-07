"""Bench report export (05 §4.4): JSON, and Markdown with bilingual tables that paste into a ticket."""

from __future__ import annotations

import json
import time
from typing import Any

from .catalogue import VERDICTS

STATES = {
    "running": ("running", "运行中"),
    "waiting": ("waiting for the operator", "等待操作员"),
    "passed": ("passed", "通过"),
    "warned": ("passed with warnings", "有警告"),
    "failed": ("failed", "失败"),
    "aborted": ("aborted", "已中止"),
}
SUGGESTION_STATES = {
    "proposed": ("proposed", "建议"),
    "applied": ("applied", "已应用"),
    "kept": ("kept", "保留"),
    "failed": ("not confirmed", "未确认"),
    "unavailable": ("unavailable", "不可用"),
}
DIRECTIONS = {"increase": ("increase", "增大"), "decrease": ("decrease", "减小"), "set": ("set", "设为")}


def _utc(t: float | None) -> str:
    if t is None:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(t))


def _cell(value: Any) -> str:
    text = "-" if value is None or value == "" else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _value(m: dict[str, Any]) -> str:
    value = m.get("value")
    if value is None:
        return "-"
    if isinstance(value, float):
        value = f"{value:.3g}" if abs(value) < 1000 else f"{value:.0f}"
    unit = m.get("unit") or ""
    text = f"{value} {unit}".strip()
    if m.get("peak") is not None and m.get("mean") is not None:
        text += f" (peak {m['peak']:.3g} / 峰值)"
    return text


def _limits(m: dict[str, Any]) -> str:
    warn, bad = m.get("warn"), m.get("bad")
    if warn is None and bad is None:
        return "-"
    parts = []
    if warn is not None:
        parts.append(f"warn {warn:g}")
    if bad is not None:
        parts.append(f"fail {bad:g}")
    return " / ".join(parts)


def to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)


def to_markdown(report: dict[str, Any]) -> str:
    verdict = report.get("verdict")
    state_en, state_zh = STATES.get(report.get("state") or "", (report.get("state"), report.get("state")))
    verdict_en, verdict_zh = VERDICTS.get(verdict or "", (state_en, state_zh))
    lines = [
        f"# Bench report / 标定与测试报告 #{report.get('run_id')}",
        "",
        "| Item / 项目 | Value / 值 |",
        "|---|---|",
        f"| Aircraft / 飞机 | {_cell(report.get('label'))} ({_cell(report.get('vehicle'))}) |",
        f"| Address / 地址 | {_cell(report.get('host'))} |",
        f"| Autopilot / 飞控 | {_cell(report.get('autopilot'))} {_cell(report.get('firmware'))} |",
        f"| Procedure / 流程 | {_cell(report.get('title_en'))} / {_cell(report.get('title_zh'))} |",
        f"| Verdict / 判定 | {_cell(verdict_en)} / {_cell(verdict_zh)} |",
        f"| State / 状态 | {_cell(state_en)} / {_cell(state_zh)} |",
        f"| Started / 开始 | {_utc(report.get('started'))} |",
        f"| Ended / 结束 | {_utc(report.get('ended'))} |",
        f"| Simulated / 模拟 | {'yes / 是' if report.get('simulated') else 'no / 否'} |",
    ]
    if report.get("reason_en"):
        lines.append(
            f"| Abort reason / 中止原因 | {_cell(report['reason_en'])} / {_cell(report.get('reason_zh'))} |"
        )
    lines += [
        "",
        "## Steps / 步骤",
        "",
        "| Step / 步骤 | Verdict / 判定 | Metric / 指标 | Value / 值 | Limits / 门限 |",
        "|---|---|---|---|---|",
    ]
    findings: list[str] = []
    proposals: list[dict[str, Any]] = []
    for step in report.get("steps") or []:
        title = f"{step['id']} {step.get('title_en')} / {step.get('title_zh')}"
        v = step.get("verdict")
        v_en, v_zh = VERDICTS.get(v or "", (step.get("state"), step.get("state")))
        metrics = step.get("metrics") or []
        if not metrics:
            lines.append(f"| {_cell(title)} | {_cell(v_en)} / {_cell(v_zh)} | - | - | - |")
        for i, m in enumerate(metrics):
            head = _cell(title) if i == 0 else ""
            verdict_cell = f"{_cell(v_en)} / {_cell(v_zh)}" if i == 0 else ""
            label = f"{m.get('label_en')} / {m.get('label_zh')}"
            lines.append(
                f"| {head} | {verdict_cell} | {_cell(label)} | {_cell(_value(m))} | {_cell(_limits(m))} |"
            )
        for f in step.get("findings") or []:
            findings.append(f"- {step['id']} [{f.get('level')}] {f.get('text_en')} / {f.get('text_zh')}")
        proposals += [{**s, "step": step["id"]} for s in step.get("suggestions") or []]
    lines += ["", "## Findings / 发现", ""]
    lines += findings or ["- None / 无"]
    lines += [
        "",
        "## Suggestions / 建议",
        "",
        "| Step / 步骤 | Parameter / 参数 | Current / 当前值 | "
        "Suggested / 建议值 | Direction / 方向 | Reason / 原因 "
        "| State / 状态 |",
        "|---|---|---|---|---|---|---|",
    ]
    if not proposals:
        lines.append("| - | - | - | - | - | - | - |")
    for s in proposals:
        d_en, d_zh = DIRECTIONS.get(s.get("direction") or "", ("-", "-"))
        if s.get("change_pct") is not None and s.get("direction") != "set":
            d_en = f"{d_en} {abs(s['change_pct'])} %"
            d_zh = f"{d_zh} {abs(s['change_pct'])} %"
        st_en, st_zh = SUGGESTION_STATES.get(s.get("state") or "", ("-", "-"))
        lines.append(
            f"| {s['step']} | {_cell(s.get('name'))} | "
            f"{_cell(s.get('current'))} | {_cell(s.get('suggested'))} "
            f"| {d_en} / {d_zh} | {_cell(s.get('reason_en'))} / "
            f"{_cell(s.get('reason_zh'))} | {st_en} / {st_zh} |"
        )
    lines += ["", "Suggestions are not written automatically. / 建议不会被自动写入。", ""]
    return "\n".join(lines)
