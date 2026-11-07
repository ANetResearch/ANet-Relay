"""Bench steps S1 to S12 (05 §4.2), one class each.

Every step has ``start(ctx)``, ``tick(ctx, now)``, ``answer(ctx, answer, value, confirm)`` and ``abort(ctx)``;
the first three return a ``StepResult`` once the step is finished and None while it runs or waits. A step that
needs the operator sets ``prompt`` (the runner then reports the run as waiting). Steps only use the building
blocks the context exposes (motor tests, calibration, message rates, parameter writes with readback, plans and
skills), so every confirm word, limit and gate of the device surface applies unchanged. Everything runs on the
fleet thread.
"""

from __future__ import annotations

import math
import re
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .. import flighthealth as fh
from ..errors import RelayError, err
from ..mav import modes
from ..mav.mavlink import M
from . import analysis, suggestions
from .catalogue import AXES, METRICS, OPERATOR_ONLY, STEPS
from .metrics import HoverWindow, VibrationWindow, finding, metric

if TYPE_CHECKING:
    from .runner import Context

Verdict = str  # pass | warn | fail | skipped

LINK_WAIT_S = (3.0, 12.0)  # S1: minimum observation, give up on the parameter table after
BATTERY_WAIT_S = 6.0
PARAMS_WAIT_S = 4.0
CALIBRATION_TIMEOUT_S = 30.0  # 05 §4.2 S4
AP_SETTLE_S = 0.6  # ArduPilot: a failure text may follow the accepted ACK
MOTOR_PCT, MOTOR_S = 8.0, 1.5  # S6 (05 §4.2)
MOTOR_SETTLE_S = 0.4
GROUND_PCT, GROUND_S = 10.0, 4.0  # S7
REST_S = 1.5  # S7 baseline before the spin
TAKEOFF_ALT_M = 0.6  # S9
STEP_SETTLE_S = 1.5  # S10: neutral sticks before and between the steps
MODE_RETRY_S, MODE_TRIES = 1.2, 3
LAND_TIMEOUT_S = 60.0
DISARM_AFTER_S = 3.0  # on the ground and still armed after landing: ask for a disarm
LIVE_POINTS = 120
LIVE_PERIOD_S = 0.2
# (axis, stick, seconds) of the S10 excitations (05 §4.2)
EXCITATIONS: tuple[tuple[str, float, float], ...] = (
    ("roll", 0.3, 0.4),
    ("roll", -0.3, 0.4),
    ("pitch", 0.3, 0.4),
    ("pitch", -0.3, 0.4),
    ("yaw", 0.5, 0.6),
    ("yaw", -0.5, 0.6),
)
FAST_RATES = {"ATTITUDE": 50.0, "SERVO_OUTPUT_RAW": 20.0}

# Calibration status texts (05 §4.2 S4): PX4 reports with "[cal]" lines, ArduPilot acknowledges when done.
CAL_TEXT = {
    modes.PX4: (
        re.compile(r"\[cal\] calibration done", re.I),
        re.compile(r"\[cal\] calibration (failed|cancelled)|calibration failed", re.I),
    ),
    modes.ARDUPILOT: (
        re.compile(r"calibration (complete|successful|done)|trim ok|calibrated", re.I),
        re.compile(
            r"(calibration|trim)\s+(failed|timed out|cancelled)|did not converge|failed to calibrate", re.I
        ),
    ),
}
SENSORS: tuple[tuple[str, str, str], ...] = (
    (
        "gyro",
        "Keep the aircraft still, then start the gyroscope calibration.",
        "保持飞机静止，然后开始陀螺校准。",
    ),
    (
        "level",
        "Place the aircraft on a level surface, then start the level calibration.",
        "将飞机放在水平面上，然后开始水平校准。",
    ),
    (
        "baro",
        "Keep the aircraft still and out of drafts, then start the barometer calibration.",
        "保持飞机静止并避开气流，然后开始气压校准。",
    ),
)
SENSOR_NAMES = {"gyro": ("gyroscope", "陀螺"), "level": ("level", "水平"), "baro": ("barometer", "气压计")}


@dataclass
class StepResult:
    verdict: Verdict
    metrics: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    suggestions: list[dict[str, Any]] = field(default_factory=list)


def answer(aid: str, en: str, zh: str, tone: str = "default") -> dict[str, str]:
    return {"id": aid, "label_en": en, "label_zh": zh, "tone": tone}


START = answer("start", "Start", "开始", "primary")
SKIP = answer("skip", "Skip", "跳过")
CONFIRM = answer("confirm", "Confirm", "确认", "primary")


def verdict_of(level: str) -> Verdict:
    return {"bad": "fail", "warn": "warn", "ok": "pass"}.get(level, "warn")


def worst_verdict(verdicts: list[Verdict]) -> Verdict:
    order = ("skipped", "pass", "warn", "fail")
    judged = [v for v in verdicts if v in order]
    return max(judged, key=order.index) if judged else "skipped"


class Step:
    """Common state of a step; subclasses implement the behaviour."""

    def __init__(self, sid: str, config: dict[str, Any] | None = None) -> None:
        self.id = sid
        self.config = dict(config or {})
        self.state = "pending"  # pending | running | waiting | done | skipped | aborted
        self.prompt: dict[str, Any] | None = None
        self.result: StepResult | None = None
        self.started: float | None = None
        self.ended: float | None = None
        self.activity: tuple[str, str] | None = None
        self.live: dict[str, deque[float]] = {}
        self.live_at = -1e9
        self.operator_only = sid in OPERATOR_ONLY

    @property
    def kind(self) -> str:
        return STEPS[self.id][2]

    # -- lifecycle (overridden) -------------------------------------------------------

    def start(self, ctx: Context) -> StepResult | None:
        return None

    def tick(self, ctx: Context, now: float) -> StepResult | None:
        return None

    def answer(self, ctx: Context, answer: str, value: Any, confirm: str | None) -> StepResult | None:
        raise err("invalid_request", reason=f"step {self.id} does not take {answer!r} now")

    def abort(self, ctx: Context) -> None:
        """Leave the aircraft safe; the runner restores message rates afterwards."""

    def restart(self) -> None:
        """Back to the beginning (resume after a pause)."""
        self.__init__(self.id, self.config)  # type: ignore[misc]

    # -- helpers ----------------------------------------------------------------------

    def ask(
        self,
        pid: str,
        en: str,
        zh: str,
        answers: list[dict[str, str]],
        ctx: Context,
        *,
        detail: tuple[str, str] | None = None,
        confirm_word: str | None = None,
        operator_only: bool | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        only = self.operator_only if operator_only is None else operator_only
        self.prompt = {
            "id": pid,
            "text_en": en,
            "text_zh": zh,
            "detail_en": detail[0] if detail else None,
            "detail_zh": detail[1] if detail else None,
            "answers": answers,
            "confirm_word": confirm_word,
            "operator_only": bool(only and ctx.real),
            "data": data or {},
        }
        self.state = "waiting"
        ctx.waiting(self)

    def resume(self, en: str | None = None, zh: str | None = None) -> None:
        self.prompt = None
        self.state = "running"
        self.activity = (en, zh) if en and zh else None

    def doing(self, en: str, zh: str) -> None:
        self.activity = (en, zh)

    def record(self, now: float, **values: float | None) -> None:
        """A point for the live mini charts, at most every LIVE_PERIOD_S."""
        if now - self.live_at < LIVE_PERIOD_S:
            return
        self.live_at = now
        for key, value in values.items():
            series = self.live.setdefault(key, deque(maxlen=LIVE_POINTS))
            series.append(None if value is None or not math.isfinite(value) else round(value, 3))  # type: ignore[arg-type]

    def view(self) -> dict[str, Any]:
        en, zh = STEPS[self.id][0], STEPS[self.id][1]
        r = self.result
        return {
            "id": self.id,
            "title_en": en,
            "title_zh": zh,
            "description_en": STEPS[self.id][3],
            "description_zh": STEPS[self.id][4],
            "kind": self.kind,
            "state": self.state,
            "verdict": r.verdict if r else None,
            "metrics": r.metrics if r else [],
            "findings": r.findings if r else [],
            "suggestions": r.suggestions if r else [],
            "prompt": self.prompt,
            "activity_en": self.activity[0] if self.activity else None,
            "activity_zh": self.activity[1] if self.activity else None,
            "live": {k: list(v) for k, v in self.live.items()} or None,
            "started": self.started,
            "ended": self.ended,
        }


# ================================================================================ S1 link and firmware


# Parameters that firmware versions name differently: one of each pair answers (ArduPilot 4.6 renamed it).
PARAM_ALIASES = {"MAV_GCS_SYSID": "SYSID_MYGCS", "SYSID_MYGCS": "MAV_GCS_SYSID"}


class LinkStep(Step):
    def start(self, ctx: Context) -> StepResult | None:
        self.t0 = ctx.now()
        self.doing("Reading the link, the autopilot and its parameters", "正在读取链路、飞控与参数")
        return None

    def tick(self, ctx: Context, now: float) -> StepResult | None:
        v = ctx.v
        st = v.state
        waited = now - self.t0
        params = st["params"]
        essential = frozenset(
            n
            for n in (v.param_named.unreadable(params) if v.param_named else ())
            if PARAM_ALIASES.get(n) not in params
        )
        named = v.param_named is not None and all(
            n in params or PARAM_ALIASES.get(n) in params for n in v.param_named.names
        )
        ready = st["autopilot"] is not None and named and ctx.rtt_ms() is not None
        if waited < LINK_WAIT_S[0] or (not ready and waited < LINK_WAIT_S[1]):
            return None
        metrics: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        levels: list[str] = []
        age = None if st["last_heartbeat"] is None else now - st["last_heartbeat"]
        live = v.link_state in ("ok", "delayed")
        metrics.append(metric("heartbeat_age_s", age, "s", "ok" if live else "bad"))
        if not live:
            levels.append("bad")
            findings.append(
                finding("link", "fail", "The aircraft is not sending heartbeats.", "飞机未发送心跳。")
            )
        autopilot = st["autopilot"]
        metrics.append(metric("autopilot", autopilot, "", "ok" if autopilot else "bad"))
        if autopilot is None:
            levels.append("bad")
            findings.append(
                finding("autopilot", "fail", "The autopilot type is not identified.", "未识别飞控类型。")
            )
        metrics.append(metric("firmware", st["firmware"], "", "ok" if st["firmware"] else "info"))
        if not st["firmware"]:
            findings.append(
                finding("firmware", "info", "The autopilot did not report its version.", "飞控未报告版本。")
            )
        total = st["param_count"]
        loaded = len(st["params"])
        complete = bool(total) and loaded >= total
        metrics.append(
            metric("params_loaded", f"{loaded} / {total or '-'}", "", "ok" if complete else "info")
        )
        if essential:
            levels.append("warn")
            names = ", ".join(sorted(essential))
            findings.append(
                finding(
                    "params_unreadable",
                    "warn",
                    f"The autopilot did not answer for {names}.",
                    f"飞控未返回 {names}。",
                )
            )
        elif not named:
            levels.append("warn")
            findings.append(
                finding(
                    "params_waiting",
                    "warn",
                    "The preflight parameters are not read yet.",
                    "尚未读到起飞前检查所需参数。",
                )
            )
        loss = ctx.loss_pct()
        rtt = ctx.rtt_ms()
        loss_level = fh._level(loss, *fh.LOSS_PCT)
        rtt_level = fh._level(rtt, *fh.RTT_MS)
        metrics.append(metric("loss_pct", loss, "%", loss_level, warn=fh.LOSS_PCT[0], bad=fh.LOSS_PCT[1]))
        metrics.append(metric("rtt_ms", rtt, "ms", rtt_level, warn=fh.RTT_MS[0], bad=fh.RTT_MS[1]))
        for key, level, value, en, zh in (
            ("loss", loss_level, loss, "Link loss is {v:.1f} %.", "链路丢包 {v:.1f} %。"),
            ("rtt", rtt_level, rtt, "Round trip is {v:.0f} ms.", "往返时延 {v:.0f} ms。"),
        ):
            if level in ("warn", "bad"):
                levels.append(level)
                findings.append(
                    finding(key, "fail" if level == "bad" else "warn", en.format(v=value), zh.format(v=value))
                )
        return StepResult(verdict_of(_worst(levels)), metrics, findings)


def _worst(levels: list[str]) -> str:
    order = ("na", "ok", "warn", "bad")
    return max(levels, key=order.index) if levels else "ok"


# ================================================================================ S2 battery at rest


class BatteryStep(Step):
    def start(self, ctx: Context) -> StepResult | None:
        self.t0 = ctx.now()
        self.doing("Measuring the resting voltage", "正在测量静息电压")
        if ctx.v.armed:
            return StepResult(
                "fail",
                findings=[
                    finding(
                        "armed",
                        "fail",
                        "A resting voltage needs a disarmed aircraft.",
                        "测量静息电压须先上锁。",
                    )
                ],
            )
        return None

    def tick(self, ctx: Context, now: float) -> StepResult | None:
        v = ctx.v
        voltage = v.state["battery"]["voltage"]
        cells = v.cells()
        if (voltage is None or not cells) and now - self.t0 < BATTERY_WAIT_S:
            return None
        if voltage is None or not cells:
            return StepResult(
                "fail",
                [metric("voltage", None, "V", "na")],
                [
                    finding(
                        "no_battery",
                        "fail",
                        "The aircraft reports no battery voltage.",
                        "飞机未报告电池电压。",
                    )
                ],
            )
        per_cell = voltage / cells
        warn_v, crit_v = ctx.fleet.settings.cell_warn_v, ctx.fleet.settings.cell_crit_v
        level = "bad" if per_cell < crit_v else "warn" if per_cell < warn_v else "ok"
        ctx.shared["rest_volts"] = float(voltage)
        metrics = [
            metric("voltage", float(voltage), "V", "info"),
            metric("cells", cells, "", "info"),
            metric("cell_v", per_cell, "V", level, warn=warn_v, bad=crit_v),
        ]
        findings = []
        if level != "ok":
            findings.append(
                finding(
                    "cell_v",
                    "fail" if level == "bad" else "warn",
                    f"Each cell rests at {per_cell:.2f} V, below "
                    f"{crit_v if level == 'bad' else warn_v:.2f} V. "
                    "Charge the battery before flying.",
                    f"每节静息电压 {per_cell:.2f} V，低于 {crit_v if level == 'bad' else warn_v:.2f} V。"
                    "请充电后再飞行。",
                )
            )
        return StepResult(verdict_of(level), metrics, findings)


# ================================================================================ S3 parameter baseline


class ParamsStep(Step):
    def start(self, ctx: Context) -> StepResult | None:
        self.t0 = ctx.now()
        self.names = suggestions.baseline_names(ctx.v.autopilot)
        ctx.request_params(self.names)
        self.doing("Reading the parameter baseline", "正在读取参数基线")
        return None

    def tick(self, ctx: Context, now: float) -> StepResult | None:
        values = ctx.param_values(self.names)
        if len(values) < len(self.names) and now - self.t0 < PARAMS_WAIT_S:
            return None
        ctx.shared["baseline"] = values
        autopilot = ctx.v.autopilot
        frame = " · ".join(
            f"{n} {values[n]:g}" for n in suggestions.FRAME_PARAMS.get(autopilot or "", ()) if n in values
        )
        metrics = [metric("frame", frame or None, "", "info")]
        for name in self.names:
            if name in suggestions.FRAME_PARAMS.get(autopilot or "", ()):
                continue
            metrics.append(metric(name, values.get(name), "", "info", label=(name, name)))
        findings = suggestions.check_ranges(autopilot, values)
        level = "warn" if any(f["level"] == "warn" for f in findings) else "ok"
        if autopilot is None:
            level = "warn"
        return StepResult(verdict_of(level), metrics, findings)


# ================================================================================ S4 sensor calibration


class CalibrationStep(Step):
    def start(self, ctx: Context) -> StepResult | None:
        self.index = 0
        self.outcomes: dict[str, str] = {}
        self.findings: list[dict[str, Any]] = []
        self.running: dict[str, Any] | None = None
        self._ask_next(ctx)
        return None

    def _ask_next(self, ctx: Context) -> None:
        sensor, en, zh = SENSORS[self.index]
        name_en, name_zh = SENSOR_NAMES[sensor]
        self.ask(
            f"calibrate_{sensor}",
            en,
            zh,
            [START, SKIP],
            ctx,
            detail=(
                f"Calibration {self.index + 1} of {len(SENSORS)}: {name_en}.",
                f"第 {self.index + 1}/{len(SENSORS)} 项：{name_zh}。",
            ),
            confirm_word="CALIBRATE" if ctx.real else None,
            data={"sensor": sensor, "index": self.index, "count": len(SENSORS)},
        )

    def answer(self, ctx: Context, answer: str, value: Any, confirm: str | None) -> StepResult | None:
        sensor = SENSORS[self.index][0]
        if answer == "skip":
            self.outcomes[sensor] = "skipped"
            return self._advance(ctx)
        if answer != "start":
            return super().answer(ctx, answer, value, confirm)
        t0, wall0 = ctx.now(), ctx.wall()
        ctx.calibrate(sensor, confirm)  # raises with the device surface's gates (CALIBRATE, disarmed)
        self.running = {"sensor": sensor, "t0": t0, "wall0": wall0, "ack_at": None}
        name_en, name_zh = SENSOR_NAMES[sensor]
        self.resume(f"Calibrating the {name_en}", f"正在校准{name_zh}")
        return None

    def tick(self, ctx: Context, now: float) -> StepResult | None:
        run = self.running
        if run is None:
            return None
        v = ctx.v
        sensor = run["sensor"]
        done_re, fail_re = CAL_TEXT.get(v.autopilot or "", CAL_TEXT[modes.ARDUPILOT])
        texts = [s["text"] for s in v.state["statustext"] if s["t"] >= run["wall0"] - 0.05]
        ack = v.ack_after(M.MAV_CMD_PREFLIGHT_CALIBRATION, run["t0"])
        outcome = None
        if any(fail_re.search(t) for t in texts):
            outcome = "failed"
        elif ack is not None and ack["result"] not in (M.MAV_RESULT_ACCEPTED, M.MAV_RESULT_IN_PROGRESS):
            outcome = "failed"
            texts.append(f"COMMAND_ACK {ack['result_name']}")
        elif any(done_re.search(t) for t in texts):
            outcome = "passed"
        elif v.autopilot != modes.PX4 and ack is not None and ack["result"] == M.MAV_RESULT_ACCEPTED:
            # ArduPilot acknowledges a finished calibration; wait briefly for a failure text
            run["ack_at"] = run["ack_at"] or now
            if now - run["ack_at"] >= AP_SETTLE_S:
                outcome = "passed"
        if outcome is None and now - run["t0"] > CALIBRATION_TIMEOUT_S:
            outcome = "failed"
            texts.append("timed out")
        if outcome is None:
            return None
        self.outcomes[sensor] = outcome
        self.running = None
        if outcome == "failed":
            name_en, name_zh = SENSOR_NAMES[sensor]
            reason = texts[-1] if texts else "no answer"
            self.findings.append(
                finding(
                    f"{sensor}_failed",
                    "fail",
                    f"The {name_en} calibration failed: {reason}.",
                    f"{name_zh}校准失败：{reason}。",
                )
            )
        return self._advance(ctx)

    def _advance(self, ctx: Context) -> StepResult | None:
        self.index += 1
        if self.index < len(SENSORS):
            self._ask_next(ctx)
            return None
        self.prompt = None
        metrics = []
        verdicts = []
        for sensor, _en, _zh in SENSORS:
            outcome = self.outcomes.get(sensor, "skipped")
            level = {"passed": "ok", "failed": "bad"}.get(outcome, "na")
            metrics.append(metric(sensor, outcome, "", level))
            verdicts.append({"passed": "pass", "failed": "fail"}.get(outcome, "skipped"))
        verdict = worst_verdict(verdicts)
        if verdict == "pass" and "skipped" in verdicts:
            verdict = "warn"
            self.findings.append(
                finding("skipped", "warn", "Some calibrations were skipped.", "部分校准项已跳过。")
            )
        return StepResult(verdict, metrics, self.findings)


# ================================================================================ S5 stick directions


class SignsStep(Step):
    def start(self, ctx: Context) -> StepResult | None:
        self.ask(
            "signs",
            "Push each stick axis and watch the stage or the "
            "telemetry. Confirm when roll, pitch and yaw respond "
            "in the direction of the stick.",
            "逐轴推杆并观察舞台或遥测。横滚、俯仰与偏航的响应方向与杆向一致后请确认。",
            [CONFIRM, SKIP],
            ctx,
            detail=(
                "Remove the propellers first, or keep the aircraft disarmed.",
                "请先拆桨，或保持飞机上锁。",
            ),
            data={"signs": ctx.v.calibration.signs.as_dict()},
        )
        return None

    def answer(self, ctx: Context, answer: str, value: Any, confirm: str | None) -> StepResult | None:
        if answer == "skip":
            return StepResult("skipped")
        if answer != "confirm":
            return super().answer(ctx, answer, value, confirm)
        ctx.v.confirmed.signs = True
        signs = ctx.v.calibration.signs.as_dict()
        return StepResult(
            "pass",
            [metric(axis, signs[axis], "", "ok", label=AXES[axis]) for axis in ("roll", "pitch", "yaw")],
        )


# ========================================================================== S6 motor order and direction

MOTOR_ANSWERS = [
    answer("correct", "Correct", "正确", "primary"),
    answer("wrong_position", "Wrong position", "位置错误"),
    answer("wrong_direction", "Wrong direction", "转向错误"),
    answer("did_not_spin", "Did not spin", "未转动"),
    answer("repeat", "Spin again", "再转一次"),
]
PROPS_OFF = (
    "Remove all propellers before this step.",
    "本步骤前请拆下全部桨叶。",
)


class MotorStep(Step):
    def start(self, ctx: Context) -> StepResult | None:
        self.layout = ctx.layout()
        self.queue = [m["n"] for m in self.layout["motors"]]
        self.results: dict[int, str] = {}
        self.word: str | None = None
        self.spin: dict[str, Any] | None = None
        self.ask(
            "props_removed",
            *PROPS_OFF,
            [CONFIRM, SKIP],
            ctx,
            detail=(
                f"Each motor spins alone at {MOTOR_PCT:g} % for {MOTOR_S:g} s, in order.",
                f"各电机依次单独以 {MOTOR_PCT:g} % 转动 {MOTOR_S:g} 秒。",
            ),
            confirm_word="MOTOR" if ctx.real else None,
            data={"layout": self.layout},
        )
        return None

    def answer(self, ctx: Context, answer: str, value: Any, confirm: str | None) -> StepResult | None:
        prompt = (self.prompt or {}).get("id")
        if prompt == "props_removed":
            if answer == "skip":
                return StepResult("skipped")
            if answer != "confirm":
                return super().answer(ctx, answer, value, confirm)
            self.word = confirm  # one confirmation covers every motor of this step (05 §4.2)
            self._spin(ctx, self.queue[0])
            return None
        if prompt != "motor_check" or self.spin is None:
            return super().answer(ctx, answer, value, confirm)
        motor = self.spin["motor"]
        if answer == "repeat":
            self._spin(ctx, motor)
            return None
        if answer not in ("correct", "wrong_position", "wrong_direction", "did_not_spin"):
            return super().answer(ctx, answer, value, confirm)
        return self._record(ctx, motor, answer)

    def _spin(self, ctx: Context, motor: int) -> None:
        ctx.spin(motor, MOTOR_PCT, MOTOR_S, self.word)  # gates: MOTOR, 30 %, 5 s, disarmed, controller
        self.spin = {"motor": motor, "until": ctx.now() + MOTOR_S + MOTOR_SETTLE_S, "peaks": {}}
        self.resume(f"Motor {motor} is spinning", f"电机 {motor} 正在转动")

    def tick(self, ctx: Context, now: float) -> StepResult | None:
        spin = self.spin
        if spin is None or self.state != "running":
            return None
        servo = ctx.v.state["servo16"] or []
        for i, us in enumerate(servo[: self.layout["count"]], 1):
            if us:
                spin["peaks"][i] = max(spin["peaks"].get(i, 0), int(us))
        if now < spin["until"]:
            return None
        motor = spin["motor"]
        observed = self._observed(spin)
        if ctx.sim and ctx.run.options.get("auto_answer"):
            return self._record(ctx, motor, observed, auto=True)
        entry = next(m for m in self.layout["motors"] if m["n"] == motor)
        self.ask(
            "motor_check",
            f"Motor {motor} spun. Was it the highlighted motor, turning in the direction of the arrow?",
            f"电机 {motor} 已转动。转动的是否为高亮位置的电机，转向是否与箭头一致？",
            MOTOR_ANSWERS,
            ctx,
            detail=(
                f"Expected: {'clockwise' if entry['dir'] == 'cw' else 'counter-clockwise'} seen from above.",
                f"应为：俯视{'顺时针' if entry['dir'] == 'cw' else '逆时针'}。",
            ),
            data={
                "layout": self.layout,
                "motor": motor,
                "dir": entry["dir"],
                "index": self.queue.index(motor),
                "count": len(self.queue),
                "observed": observed if ctx.sim else None,
            },
        )
        return None

    def _observed(self, spin: dict[str, Any]) -> str:
        """What the servo outputs show (simulator auto-answer): which output rose during the spin."""
        risen = [i for i, us in spin["peaks"].items() if us > fh.MOTOR_MIN_US]
        if not risen:
            return "did_not_spin"
        return "correct" if risen == [spin["motor"]] else "wrong_position"

    def _record(self, ctx: Context, motor: int, outcome: str, auto: bool = False) -> StepResult | None:
        self.results[motor] = outcome
        self.spin = None
        rest = [m for m in self.queue if m not in self.results]
        if rest:
            self._spin(ctx, rest[0])
            return None
        self.prompt = None
        ok = sum(1 for r in self.results.values() if r == "correct")
        findings = []
        for m, r in sorted(self.results.items()):
            if r == "wrong_position":
                findings.append(
                    finding(
                        f"motor{m}_position",
                        "fail",
                        f"Motor {m} is not at its position in the layout. "
                        "Swap the signal leads or remap the outputs.",
                        f"电机 {m} 的位置与布局图不符。请调换信号线或重新映射输出。",
                    )
                )
            elif r == "wrong_direction":
                findings.append(
                    finding(
                        f"motor{m}_direction",
                        "fail",
                        f"Motor {m} turns the wrong way. Swap any two "
                        "of its phase wires or reverse it in the ESC.",
                        f"电机 {m} 转向错误。请调换任意两根相线，或在电调中反转方向。",
                    )
                )
            elif r == "did_not_spin":
                findings.append(
                    finding(
                        f"motor{m}_stopped",
                        "fail",
                        f"Motor {m} did not spin. Check its ESC power, signal lead and calibration.",
                        f"电机 {m} 未转动。请检查电调供电、信号线与油门校准。",
                    )
                )
        if auto:
            findings.append(
                finding(
                    "auto_answer",
                    "info",
                    "Answered from the simulator's servo outputs.",
                    "由模拟飞控的伺服输出自动回答。",
                )
            )
        metrics = [
            metric("motors_ok", f"{ok} / {len(self.queue)}", "", "ok" if ok == len(self.queue) else "bad")
        ]
        metrics += [
            metric(f"motor{m}", r, "", "ok" if r == "correct" else "bad", label=(f"Motor {m}", f"电机 {m}"))
            for m, r in sorted(self.results.items())
        ]
        return StepResult("pass" if ok == len(self.queue) else "fail", metrics, findings)

    def abort(self, ctx: Context) -> None:
        ctx.stop_motors()


# ================================================================================ S7 ground vibration


class VibrationStep(Step):
    def start(self, ctx: Context) -> StepResult | None:
        self.window = VibrationWindow()
        self.phase = "prompt"
        self.word: str | None = None
        self.ask(
            "props_removed",
            *PROPS_OFF,
            [CONFIRM, SKIP],
            ctx,
            detail=(
                f"All motors spin at {GROUND_PCT:g} % while vibration is recorded at 10 Hz.",
                f"全部电机以 {GROUND_PCT:g} % 转动，同时以 10 Hz 记录振动。",
            ),
            confirm_word="MOTOR" if ctx.real else None,
            operator_only=True,  # a person attests that the propellers are off
            data={"layout": ctx.layout()},
        )
        return None

    def answer(self, ctx: Context, answer: str, value: Any, confirm: str | None) -> StepResult | None:
        if answer == "skip":
            return StepResult("skipped")
        if answer != "confirm":
            return super().answer(ctx, answer, value, confirm)
        ctx.check_spin(confirm)  # the motor-test gates before anything changes
        self.word = confirm
        ctx.set_rates({"VIBRATION": 10.0, "SERVO_OUTPUT_RAW": 10.0})
        self.phase = "rest"
        self.until = ctx.now() + REST_S
        self.resume("Recording vibration at rest", "正在记录静止振动")
        return None

    def tick(self, ctx: Context, now: float) -> StepResult | None:
        if self.phase == "prompt":
            return None
        count = ctx.motor_count()
        self.window.sample(ctx.v, self.phase == "spin", count)
        vib = ctx.v.state["vibration"] or {}
        self.record(now, **{k: _num(vib.get(k)) for k in ("vibration_x", "vibration_y", "vibration_z")})
        if self.phase == "rest" and now >= self.until:
            window = ctx.spin_group(GROUND_PCT, GROUND_S, self.word)
            self.phase = "spin"
            self.until = now + window + 0.3
            self.doing("All motors are spinning", "全部电机正在转动")
            return None
        if self.phase == "spin" and now >= self.until:
            ctx.stop_motors()
            level, metrics, findings = self.window.result(count)
            if ctx.v.autopilot == modes.ARDUPILOT:
                findings.append(
                    finding(
                        "sequential",
                        "info",
                        "ArduPilot runs a motor-count test one motor "
                        "after another; the window covers every motor.",
                        "ArduPilot 的多电机测试按顺序逐个转动；记录窗口覆盖全部电机。",
                    )
                )
            return StepResult(verdict_of(level), metrics, findings)
        return None

    def abort(self, ctx: Context) -> None:
        ctx.stop_motors()


def _num(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


# ================================================================================ S8 propellers fitted


class PropsStep(Step):
    def start(self, ctx: Context) -> StepResult | None:
        self.ask(
            "props_fitted",
            "Fit all propellers. Check each propeller's direction "
            "against the layout and that every nut is tight.",
            "装上全部桨叶。对照布局图检查每片桨的方向，并确认紧固。",
            [CONFIRM, SKIP],
            ctx,
            detail=(
                "On a real aircraft the hover test needs this confirmation.",
                "真机须完成此确认才能进行悬停测试。",
            ),
            data={"layout": ctx.layout()},
        )
        return None

    def answer(self, ctx: Context, answer: str, value: Any, confirm: str | None) -> StepResult | None:
        if answer == "skip":
            return StepResult("skipped")
        if answer != "confirm":
            return super().answer(ctx, answer, value, confirm)
        ctx.v.confirmed.props = True
        return StepResult("pass")


# ================================================================================ flight steps


class FlightStep(Step):
    """Shared flying: the start prompt (ARM on a real aircraft), takeoff, and landing when no flight step
    follows."""

    phase = "prompt"

    def _ask_start(self, ctx: Context) -> None:
        self.phase = "prompt"
        blocking = ctx.preflight_blocking()
        self.ask(
            "arm_start",
            "Clear the area. Arm and start the flight segment.",
            "请清空周围区域。解锁并开始飞行段。",
            [answer("start", "Arm and start", "解锁并开始", "primary"), SKIP],
            ctx,
            detail=(
                f"Takes off to {TAKEOFF_ALT_M:g} m and lands on its own. Emergency stop stays active.",
                f"起飞至 {TAKEOFF_ALT_M:g} m 并自动降落。急停始终有效。",
            ),
            confirm_word="ARM" if ctx.real else None,
            data={"blocking": blocking},
        )

    def _take_off(self, ctx: Context, confirm: str | None, hover_s: float) -> None:
        v = ctx.v
        if v.armed:
            raise err("armed")
        blocking = ctx.preflight_blocking()
        if blocking:
            raise err("preflight_blocked", items=", ".join(blocking), blocking=blocking)
        ctx.set_rates(FAST_RATES)
        ctx.allow_arm(True)
        try:
            ctx.run_plan(
                [{"action": "takeoff", "alt": TAKEOFF_ALT_M}, {"action": "hover", "seconds": hover_s}],
                confirm,
                f"bench-{self.id.lower()}",
            )
        except RelayError:
            ctx.allow_arm(False)
            raise
        self.plan_id = v.plan.id if v.plan else None
        self.phase = "takeoff"
        self.resume("Taking off", "正在起飞")

    def _plan_failed(self, ctx: Context) -> str | None:
        plan = ctx.v.plan
        if plan is None or plan.id != getattr(self, "plan_id", None):
            return "superseded"
        if plan.state in ("failed", "cancelled"):
            return plan.detail or plan.state
        return None

    def _fail(self, ctx: Context, en: str, zh: str) -> StepResult | None:
        """A failed flight segment: land (the step ends once on the ground) unless a person took the
        sticks."""
        result = StepResult("fail", findings=[finding("flight_failed", "fail", en, zh)])
        ctx.fail_flight(self, en)
        v = ctx.v
        if ctx.run.halt or not (v.armed and v.airborne()) or v.skill is None or v.skill.name != "land":
            return result
        self.result_ready = result
        self.phase = "land"
        self.land_t = ctx.now()
        self.ground_since = None
        self.disarm_sent = -1e9
        self.doing("Landing", "正在降落")
        return None

    def _begin_landing(self, ctx: Context) -> None:
        ctx.run_skill("land", {})
        self.phase = "land"
        self.land_t = ctx.now()
        self.ground_since: float | None = None
        self.disarm_sent = -1e9
        self.doing("Landing", "正在降落")

    def _landing(self, ctx: Context, now: float) -> bool | None:
        """True once landed and disarmed; None while landing; False when landing failed."""
        v = ctx.v
        if not v.armed:
            ctx.allow_arm(False)
            return True
        if v.landed == "ground":
            self.ground_since = self.ground_since or now
            if now - self.ground_since > DISARM_AFTER_S and now - self.disarm_sent > 1.5:
                self.disarm_sent = now
                v.driver.arm(False)
        if now - self.land_t > LAND_TIMEOUT_S:
            return False
        return None

    def abort(self, ctx: Context) -> None:
        ctx.land_if_airborne()


class HoverStep(FlightStep):
    def start(self, ctx: Context) -> StepResult | None:
        self.hover_s = float(ctx.run.options.get("hover_s") or self.config.get("hover_s") or 15.0)
        self.only = tuple(self.config.get("only") or ())
        self.window: HoverWindow | None = None
        self.result_ready: StepResult | None = None
        self._ask_start(ctx)
        return None

    def answer(self, ctx: Context, answer: str, value: Any, confirm: str | None) -> StepResult | None:
        if self.phase != "prompt":
            return super().answer(ctx, answer, value, confirm)
        if answer == "skip":
            return StepResult("skipped")
        if answer != "start":
            return super().answer(ctx, answer, value, confirm)
        self._take_off(ctx, confirm, self.hover_s)
        return None

    def tick(self, ctx: Context, now: float) -> StepResult | None:
        v = ctx.v
        if self.phase in ("takeoff", "hover"):
            failed = self._plan_failed(ctx)
            if failed:
                return self._fail(ctx, f"The flight segment stopped: {failed}.", f"飞行段中止：{failed}。")
            plan = v.plan
            assert plan is not None
            if self.phase == "takeoff" and plan.index >= 1:
                self.phase = "hover"
                self.window = HoverWindow(
                    ctx.shared.get("rest_volts") or v.health.idle_volts, self.only or None
                )
            if self.phase == "hover" and self.window is not None:
                sticks = v.skill.out if v.skill is not None and v.skill.active else v.sticks
                self.window.sample(v, now, sticks, v.mode in modes.HOLD_STICK_MODES)
                att = v.state["attitude"] or {}
                tilt = math.degrees(math.hypot(att.get("roll", 0.0), att.get("pitch", 0.0)))
                vib = v.state["vibration"] or {}
                axes = [_num(vib.get(k)) for k in ("vibration_x", "vibration_y", "vibration_z")]
                self.record(
                    now,
                    tilt=tilt,
                    throttle=_num((v.state["hud"] or {}).get("throttle")),
                    vibration=max((a for a in axes if a is not None), default=None),
                )
                held = self.window.duration_s
                self.doing(
                    f"Hovering {held:.0f} / {self.hover_s:.0f} s",
                    f"悬停中 {held:.0f} / {self.hover_s:.0f} 秒",
                )
            if plan.state == "done":
                self.result_ready = self._judge(ctx)
                if ctx.flight_follows(self):
                    return self.result_ready
                self._begin_landing(ctx)
            return None
        if self.phase == "land":
            landed = self._landing(ctx, now)
            if landed is None:
                return None
            result = self.result_ready or StepResult("fail")
            if landed is False:
                result.findings.append(
                    finding(
                        "landing", "fail", "The aircraft did not land and disarm.", "飞机未能降落并上锁。"
                    )
                )
                result.verdict = "fail"
            ctx.restore_rates()
            return result
        return None

    def _judge(self, ctx: Context) -> StepResult:
        assert self.window is not None
        level, metrics, findings = self.window.result()
        hover = self.window.hover_pct()
        if hover is not None:
            ctx.shared["hover_pct"] = hover
        if not self.window.values.get("hover_throttle") and not self.only:
            findings.append(
                finding(
                    "no_hover_data",
                    "warn",
                    "Not enough airborne samples were recorded to judge the hover.",
                    "空中样本不足，无法判定悬停。",
                )
            )
            level = "warn" if level in ("ok", "na") else level
        verdict = verdict_of(level if level != "na" else "warn")
        if verdict == "fail":
            ctx.shared["hover_failed"] = True
        return StepResult(verdict, metrics, findings)


class StepResponseStep(FlightStep):
    def start(self, ctx: Context) -> StepResult | None:
        self.samples: list[tuple[float, float, float, float, float, float, float]] = []
        self.result_ready: StepResult | None = None
        self.box = None
        if ctx.shared.get("hover_failed"):
            result = StepResult(
                "skipped",
                findings=[
                    finding(
                        "hover_failed",
                        "warn",
                        "Skipped: the hover test failed, so no stick steps are flown.",
                        "已跳过：悬停测试未通过，不进行阶跃测试。",
                    )
                ],
            )
            if ctx.v.airborne() and not ctx.flight_follows(self):
                self.result_ready = result
                self._begin_landing(ctx)
                self.resume("Landing", "正在降落")
                return None
            return result
        if ctx.v.airborne():
            self._to_mode(ctx)
            return None
        self._ask_start(ctx)
        return None

    def answer(self, ctx: Context, answer: str, value: Any, confirm: str | None) -> StepResult | None:
        if self.phase != "prompt":
            return super().answer(ctx, answer, value, confirm)
        if answer == "skip":
            return StepResult("skipped")
        if answer != "start":
            return super().answer(ctx, answer, value, confirm)
        self._take_off(ctx, confirm, 3.0)
        return None

    def _to_mode(self, ctx: Context) -> None:
        v = ctx.v
        self.target = modes.possess_mode(v.autopilot, True, "althold") or "ALT_HOLD"
        self.phase = "mode"
        self.mode_tries = 0
        self.mode_sent = -1e9
        self.resume(f"Switching to {self.target}", f"正在切换到 {self.target}")
        local = v.local_enu()
        if local is not None and v.capabilities.position:
            try:
                self.box = ctx.fleet._local_box(v, local)
            except RelayError:
                self.box = None

    def tick(self, ctx: Context, now: float) -> StepResult | None:
        v = ctx.v
        if self.phase == "takeoff":
            failed = self._plan_failed(ctx)
            if failed:
                return self._fail(ctx, f"Takeoff stopped: {failed}.", f"起飞中止：{failed}。")
            if v.plan is not None and v.plan.state == "done":
                self._to_mode(ctx)
            return None
        if self.phase == "mode":
            if v.mode == self.target:
                steps: list[dict[str, Any]] = [
                    {"action": "sticks", "throttle": 0.5, "seconds": STEP_SETTLE_S}
                ]
                for axis, stick, seconds in EXCITATIONS:
                    steps.append({"action": "sticks", axis: stick, "throttle": 0.5, "seconds": seconds})
                    steps.append({"action": "sticks", "throttle": 0.5, "seconds": STEP_SETTLE_S})
                ctx.run_plan(steps, None, "bench-s10")
                self.plan_id = v.plan.id if v.plan else None
                self.phase = "steps"
                self.doing("Flying the stick steps", "正在执行阶跃")
                return None
            if now - self.mode_sent >= MODE_RETRY_S:
                if self.mode_tries >= MODE_TRIES:
                    return self._finish_early(
                        ctx,
                        finding(
                            "mode",
                            "warn",
                            f"The autopilot did not switch to {self.target}; no steps were flown.",
                            f"飞控未切换到 {self.target}，未执行阶跃。",
                        ),
                    )
                self.mode_tries += 1
                self.mode_sent = now
                v.driver.set_mode(self.target)
            return None
        if self.phase == "steps":
            failed = self._plan_failed(ctx)
            if failed:
                return self._fail(ctx, f"The steps stopped: {failed}.", f"阶跃中止：{failed}。")
            local = v.local_enu()
            if self.box is not None and local is not None and not _inside(self.box, local):
                return self._fail(
                    ctx, "The aircraft left the safety volume during the steps.", "阶跃期间飞机离开了安全区。"
                )
            self._sample(v, now)
            if v.plan is not None and v.plan.state == "done":
                self.result_ready = self._judge(ctx)
                if ctx.flight_follows(self):
                    ctx.run_skill("hold", {"seconds": 1.0})  # back to the hold mode for the next flight step
                    return self.result_ready
                self._begin_landing(ctx)
            return None
        if self.phase == "land":
            landed = self._landing(ctx, now)
            if landed is None:
                return None
            result = self.result_ready or StepResult("fail")
            if landed is False:
                result.findings.append(
                    finding(
                        "landing", "fail", "The aircraft did not land and disarm.", "飞机未能降落并上锁。"
                    )
                )
                result.verdict = "fail"
            ctx.restore_rates()
            return result
        return None

    def _finish_early(self, ctx: Context, note: dict[str, Any]) -> StepResult | None:
        result = StepResult("warn", findings=[note])
        if ctx.flight_follows(self):
            return result
        self.result_ready = result
        self._begin_landing(ctx)
        return None

    def _sample(self, v: Any, now: float) -> None:
        entry = v.state["messages"].get("ATTITUDE")
        att = v.state["attitude"]
        skill = v.skill
        out = skill.out if skill is not None and skill.active and skill.name == "sticks" else None
        cmd = (out.roll, out.pitch, out.yaw) if out is not None else (0.0, 0.0, 0.0)
        if att is None or entry is None:
            return
        if self.samples and self.samples[-1][0] == entry["t"] and self.samples[-1][1:4] == cmd:
            return
        roll, pitch, yawspeed = (
            math.degrees(att["roll"]),
            math.degrees(att["pitch"]),
            math.degrees(att["yawspeed"]),
        )
        self.samples.append((entry["t"], *cmd, roll, pitch, yawspeed))
        self.record(now, command=max(cmd, key=abs), roll=roll, pitch=pitch, yaw_rate=yawspeed)

    def _judge(self, ctx: Context) -> StepResult:
        v = ctx.v
        tilt = _max_tilt(v)
        t = [s[0] for s in self.samples]
        series = {
            "roll": [s[4] for s in self.samples],
            "pitch": [s[5] for s in self.samples],
            "yaw": [s[6] for s in self.samples],
        }
        commands = {
            "roll": [s[1] for s in self.samples],
            "pitch": [s[2] for s in self.samples],
            "yaw": [s[3] for s in self.samples],
        }
        params = {**ctx.shared.get("baseline", {}), **ctx.param_values(_rate_names(v.autopilot))}
        metrics: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        proposals: list[dict[str, Any]] = []
        levels: list[str] = []
        for axis in ("roll", "pitch", "yaw"):
            figures = []
            for t_on, t_off, stick in _edges(t, commands[axis]):
                if axis == "yaw":
                    expected = None
                else:
                    expected = stick * tilt * (1.0 if axis == "roll" else -1.0)  # pitch forward: nose down
                fig = analysis.step_response(t, series[axis], t_on, t_off, expected)
                if fig is not None:
                    figures.append(fig)
            merged = _merge(figures)
            level, found, suggested = analysis.judge(axis, merged, v.autopilot, params)
            levels.append(level)
            findings += found
            for s in suggested:
                if not any(p["name"] == s["name"] for p in proposals):
                    proposals.append(s)
            en, zh = AXES[axis]
            for key, unit in (
                ("rise_s", "s"),
                ("overshoot_pct", "%"),
                ("settle_s", "s"),
                ("ss_error_pct", "%"),
                ("residual_pct", "%"),
            ):
                value = None if merged is None else merged.get(key)
                warn, bad = analysis.OVERSHOOT_PCT if key == "overshoot_pct" else (None, None)
                if key == "rise_s":
                    warn = analysis.SLUGGISH_S[axis]
                if key == "residual_pct":
                    warn = analysis.RESIDUAL_PCT
                m_en, m_zh = METRICS[key]
                metrics.append(
                    metric(
                        f"{axis}.{key}",
                        value,
                        unit,
                        level if key == "overshoot_pct" else "info",
                        warn=warn,
                        bad=bad,
                        label=(f"{en} {m_en.lower()}", f"{zh}{m_zh}"),
                    )
                )
        return StepResult(
            verdict_of(_worst(levels) if any(lv != "na" for lv in levels) else "warn"),
            metrics,
            findings,
            proposals,
        )


def _inside(box: Any, p: tuple[float, float, float]) -> bool:
    from .. import skills

    return skills._inside(box, p)


def _max_tilt(v: Any) -> float:
    from .. import skills

    return skills.max_tilt_deg(v.autopilot, v.param)


def _rate_names(autopilot: str | None) -> list[str]:
    names = []
    for p, d in analysis.RATE_PARAMS.get(autopilot or "", {}).values():
        names.append(p)
        if d:
            names.append(d)
    return names


def _edges(t: list[float], cmd: list[float]) -> list[tuple[float, float, float]]:
    """(t_on, t_off, stick) of every stick step in a commanded trace."""
    out = []
    on: tuple[float, float] | None = None
    for ti, c in zip(t, cmd, strict=False):
        if on is None and abs(c) > 0.05:
            on = (ti, c)
        elif on is not None and abs(c) <= 0.05:
            out.append((on[0], ti, on[1]))
            on = None
    return out


def _merge(figures: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Both directions of one axis: the worse of each figure (means for the timings)."""
    if not figures:
        return None

    def mean(key: str) -> float | None:
        values = [f[key] for f in figures if f[key] is not None]
        if len(values) < len(figures):
            return None
        return round(statistics.fmean(values), 3)

    def peak(key: str) -> float | None:
        values = [f[key] for f in figures if f[key] is not None]
        return max(values) if values else None

    ss = [f["ss_error_pct"] for f in figures if f["ss_error_pct"] is not None]
    return {
        "samples": sum(f["samples"] for f in figures),
        "reversed": any(f["reversed"] for f in figures),
        "rise_s": mean("rise_s"),
        "overshoot_pct": peak("overshoot_pct") or 0.0,
        "settle_s": mean("settle_s"),
        "ss_error_pct": round(statistics.fmean(ss), 1) if ss else None,
        "residual_pct": peak("residual_pct"),
        "crossings": max(f["crossings"] for f in figures),
    }


# ================================================================================ S11 hover learning


class HoverLearnStep(Step):
    def start(self, ctx: Context) -> StepResult | None:
        hover = ctx.shared.get("hover_pct")
        if hover is None:
            return StepResult(
                "skipped",
                findings=[
                    finding(
                        "no_hover",
                        "info",
                        "No hover throttle was measured; run the hover test first.",
                        "未测得悬停油门；请先进行悬停测试。",
                    )
                ],
            )
        params = ctx.param_values([suggestions.HOVER_PARAM.get(ctx.v.autopilot or "", "")])
        self.hover = hover
        self.items = suggestions.hover_items(ctx.v.autopilot, hover, params, ctx.v.calibration.hover)
        self.index = -1
        self.pending: dict[str, Any] | None = None
        self.notes: list[dict[str, Any]] = []
        return self._next(ctx)

    def _next(self, ctx: Context) -> StepResult | None:
        self.index += 1
        while self.index < len(self.items) and self.items[self.index]["state"] == "unavailable":
            item = self.items[self.index]
            self.notes.append(
                finding(
                    "param_unavailable",
                    "info",
                    f"{item['name']} is not in the parameter table, so it cannot be written.",
                    f"参数表中没有 {item['name']}，无法写入。",
                )
            )
            self.index += 1
        if self.index >= len(self.items):
            return self._finish()
        item = self.items[self.index]
        current = "-" if item["current"] is None else f"{item['current']:g}"
        where_en = "the autopilot" if item["kind"] == "param" else "Relay"
        where_zh = "飞控" if item["kind"] == "param" else "Relay"
        self.ask(
            f"apply_{item['kind']}",
            f"Set {item['name']} on {where_en} from {current} to {item['suggested']:g}?",
            f"是否将{where_zh}的 {item['name']} 从 {current} 改为 {item['suggested']:g}？",
            [answer("apply", "Apply", "应用", "primary"), answer("keep", "Keep", "保留")],
            ctx,
            detail=(item["reason_en"], item["reason_zh"]),
            confirm_word=item["name"] if item["kind"] == "param" else None,
            data={"item": item},
        )
        return None

    def answer(self, ctx: Context, answer: str, value: Any, confirm: str | None) -> StepResult | None:
        if self.pending is not None or self.index >= len(self.items):
            return super().answer(ctx, answer, value, confirm)
        item = self.items[self.index]
        if answer == "keep":
            item["state"] = "kept"
            return self._next(ctx)
        if answer != "apply":
            return super().answer(ctx, answer, value, confirm)
        if item["kind"] == "relay":
            ctx.set_hover(item["suggested"])
            item["state"] = "applied"
            return self._next(ctx)
        if confirm != item["name"]:
            raise err("confirm_required", expected=item["name"])
        ctx.set_param(item["name"], item["suggested"])
        self.pending = {"name": item["name"], "t0": ctx.now(), "resent": False}
        self.resume(f"Writing {item['name']}", f"正在写入 {item['name']}")
        return None

    def tick(self, ctx: Context, now: float) -> StepResult | None:
        pend = self.pending
        if pend is None:
            return None
        item = self.items[self.index]
        ok = ctx.param_readback(pend["name"], item["suggested"], pend["t0"])
        if ok:
            item["state"] = "applied"
            ctx.fleet._param_event(ctx.v.id, pend["name"], item["suggested"])
            self.pending = None
            return self._next(ctx)
        if not pend["resent"] and now - pend["t0"] > 1.5:
            pend["resent"] = True
            ctx.set_param(pend["name"], item["suggested"])
        if now - pend["t0"] > 3.0:
            item["state"] = "failed"
            readback = ctx.param_values([pend["name"]]).get(pend["name"])
            self.notes.append(
                finding(
                    "readback",
                    "warn",
                    f"{pend['name']} reads back {readback if readback is not None else 'nothing'}, "
                    f"not {item['suggested']:g}.",
                    f"{pend['name']} 回读值为 {readback if readback is not None else '空'}，不是 "
                    f"{item['suggested']:g}。",
                )
            )
            self.pending = None
            return self._next(ctx)
        return None

    def _finish(self) -> StepResult:
        self.prompt = None
        failed = any(i["state"] == "failed" for i in self.items)
        metrics = [metric("hover_suggested", round(self.hover, 1), "%", "info")]
        return StepResult("warn" if failed else "pass", metrics, self.notes, self.items)


# ================================================================================ S12 report


class ReportStep(Step):
    def start(self, ctx: Context) -> StepResult | None:
        verdicts = [s.result.verdict for s in ctx.run.steps if s is not self and s.result is not None]
        failed = sum(1 for v in verdicts if v == "fail")
        warned = sum(1 for v in verdicts if v == "warn")
        overall = "fail" if failed else "warn" if warned else "pass"
        metrics = [
            metric("overall", overall, "", {"pass": "ok", "warn": "warn", "fail": "bad"}[overall]),
            metric("steps_failed", failed, "", "bad" if failed else "ok"),
            metric("steps_warned", warned, "", "warn" if warned else "ok"),
        ]
        findings = []
        if ctx.sim:
            findings.append(
                finding(
                    "simulated",
                    "info",
                    "Simulated aircraft: the figures come from the simulator.",
                    "模拟机：数据来自模拟器。",
                )
            )
        return StepResult(overall, metrics, findings)


STEP_CLASSES: dict[str, type[Step]] = {
    "S1": LinkStep,
    "S2": BatteryStep,
    "S3": ParamsStep,
    "S4": CalibrationStep,
    "S5": SignsStep,
    "S6": MotorStep,
    "S7": VibrationStep,
    "S8": PropsStep,
    "S9": HoverStep,
    "S10": StepResponseStep,
    "S11": HoverLearnStep,
    "S12": ReportStep,
}


def make_step(sid: str, config: dict[str, Any] | None = None) -> Step:
    return STEP_CLASSES[sid](sid, config)
