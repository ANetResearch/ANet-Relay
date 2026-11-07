"""The procedure state machine (05 §4.3): one run per aircraft, advanced on the fleet thread at 50 Hz.

``Run`` holds the steps and the run state (running, waiting, passed, warned, failed, aborted); ``Context`` is
the only way a step reaches the aircraft: it wraps the fleet's existing building blocks (motor tests,
calibration, message rates, parameter writes, plans and skills) with the run's controller as the actor, so
every gate of the device surface applies. Before each step tick the run checks the abort rules: emergency
stop, link loss, crash, control taken, arming or disarming outside the procedure. A person possessing an
aircraft whose procedure an agent started pauses the run instead of aborting it.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any

from .. import authority as auth
from .. import device, preflight
from ..authority import Actor
from ..errors import RelayError, err
from ..fleet_device import MOTOR_TEST_S, REAL_MOTOR_PCT
from ..mav import modes
from ..mav.mavlink import M
from ..mav.params import same_value
from . import layout as layoutmod
from .catalogue import FLIGHT_STEPS, PROCEDURES, STEPS, VERDICTS
from .metrics import finding
from .steps import Step, StepResult, make_step

if TYPE_CHECKING:
    from ..fleet import FleetService, Vehicle

log = logging.getLogger("relay.procedures")

ACTIVE = ("running", "waiting")
FINAL = {"pass": "passed", "warn": "warned", "fail": "failed"}
# Message rates a step may raise, and the rate they go back to (fleet._request_streams).
DEFAULT_RATES = {"ATTITUDE": 20.0, "SERVO_OUTPUT_RAW": 4.0, "VIBRATION": 2.0}
REASONS: dict[str, tuple[str, str]] = {
    "operator": ("aborted by the operator", "操作员中止"),
    "agent": ("aborted by the agent", "智能体中止"),
    "stopped": ("emergency stop", "急停"),
    "link_lost": ("link lost", "链路丢失"),
    "crashed": ("the aircraft crashed", "飞机坠毁"),
    "control_lost": ("control of the aircraft was lost", "失去飞机控制权"),
    "armed": ("armed or disarmed outside the procedure", "流程之外的解锁或上锁"),
    "operator_override": ("the operator took over the sticks", "操作员接管了杆量"),
    "removed": ("the aircraft was removed", "飞机已移除"),
    "error": ("an internal error stopped the step", "内部错误中止了该步骤"),
}


class Context:
    """The building blocks a step may use, bound to one vehicle and the run's actor (fleet thread only)."""

    def __init__(self, fleet: FleetService, v: Vehicle, run: Run) -> None:
        self.fleet = fleet
        self.v = v
        self.run = run

    # -- facts ------------------------------------------------------------------------------------------

    @property
    def actor(self) -> Actor:
        return self.run.actor

    @property
    def shared(self) -> dict[str, Any]:
        return self.run.shared

    @property
    def real(self) -> bool:
        return self.v.kind == "real"

    @property
    def sim(self) -> bool:
        return self.v.kind == "sim"

    def now(self) -> float:
        return self.fleet.clock()

    def wall(self) -> float:
        return self.fleet.wall()

    def layout(self) -> dict[str, Any]:
        return layoutmod.layout(self.v.state["mav_type"])

    def motor_count(self) -> int:
        return device.motor_layout(self.v.state["mav_type"])[0]

    def loss_pct(self) -> float | None:
        received = sum(b[1] for b in self.v.loss_buckets)
        lost = sum(b[2] for b in self.v.loss_buckets)
        return None if received == 0 else round(100.0 * lost / (received + lost), 1)

    def rtt_ms(self) -> float | None:
        v = self.v
        if v.rtt_at is None or self.now() - v.rtt_at > 10.0:
            return None
        return v.rtt_ms

    def preflight_blocking(self) -> list[str]:
        return preflight.summary(self.fleet._preflight_items(self.v))["blocking"]

    def flight_follows(self, step: Step) -> bool:
        """True when a later flight step of this run will run (the aircraft stays airborne for it)."""
        steps = self.run.steps
        later = steps[steps.index(step) + 1 :]
        return any(s.id in FLIGHT_STEPS and s.id not in self.run.skip for s in later)

    def waiting(self, step: Step) -> None:
        self.run.on_waiting(step)

    # -- parameters ------------------------------------------------------------------------------------

    def request_params(self, names: list[str]) -> None:
        if self.v.link_state not in ("ok", "delayed"):
            return
        for name in names:
            if name and name not in self.v.state["params"]:
                self.v.driver.request_param_named(name)

    def param_values(self, names: list[str]) -> dict[str, float]:
        params = self.v.state["params"]
        return {n: float(params[n]["value"]) for n in names if n and n in params}

    def param_readback(self, name: str, value: float, t0: float) -> bool:
        entry = self.v.state["params"].get(name)
        rx = self.v.state["param_rx_at"]
        return (
            bool(entry) and rx is not None and rx >= t0 and same_value(entry["value"], value, entry["type"])
        )

    def set_param(self, name: str, value: float) -> None:
        """PARAM_SET through the fleet (confirm word = the name, readback checked by the step)."""
        self.fleet.set_param(self.v.id, name, value, name, wait=False, actor=self.actor)

    def set_hover(self, value: float) -> None:
        self.fleet.set_calibration(self.v.id, None, value)

    # -- device ----------------------------------------------------------------------------------------

    def calibrate(self, sensor: str, confirm: str | None) -> None:
        self.fleet.calibrate(self.v.id, self.actor, sensor, confirm)

    def spin(self, motor: int, pct: float, seconds: float, confirm: str | None) -> None:
        self.fleet.motors_test(self.v.id, self.actor, motor, pct, seconds, confirm)

    def check_spin(self, confirm: str | None) -> None:
        """The gates of a motor test (controller, link, MOTOR on a real aircraft, disarmed)."""
        v = self.v
        self.fleet._check_control(v, self.actor)
        self.fleet._require_link(v)
        device.require_confirm("motor", v.kind, confirm)
        device.require_disarmed("motor", v.armed)

    def spin_group(self, pct: float, seconds: float, confirm: str | None) -> float:
        """Every motor at ``pct`` for ``seconds``; returns how long the outputs run (05 §4.2 S7).

        PX4 takes one ACTUATOR_TEST per motor and runs them together. ArduPilot's DO_MOTOR_TEST takes a motor
        count and runs the motors one after another, each for ``seconds``.
        """
        v = self.v
        self.check_spin(confirm)
        if v.kind == "real" and pct > REAL_MOTOR_PCT:
            raise err("invalid_request", reason="throttle_pct must be 0 to 30 on a real aircraft")
        if not MOTOR_TEST_S[0] <= seconds <= MOTOR_TEST_S[1]:
            raise err("invalid_request", reason="a motor test lasts 0.2 to 5 s")
        count = self.motor_count()
        v.motor_seq = None
        if v.autopilot == modes.PX4:
            for motor in range(1, count + 1):
                self.fleet._motor_command(v, motor, pct, seconds)
            window = seconds
        else:
            v.driver.command_long(M.MAV_CMD_DO_MOTOR_TEST, (1.0, 0.0, pct, seconds, float(count), 0.0, 0.0))
            window = seconds * count
        v.motor_until = self.now() + window
        self.fleet._emit_device(
            "motor_test",
            v,
            self.actor,
            {"motor": "all", "throttle_pct": pct, "seconds": seconds, "together": v.autopilot == modes.PX4},
            {"motor": "all motors", "throttle_pct": pct, "seconds": seconds},
        )
        return window

    def stop_motors(self) -> None:
        v = self.v
        if v.motor_seq is None and self.now() >= v.motor_until:
            return
        if v.link_state in ("ok", "delayed"):
            self.fleet._stop_motors(v)
            self.fleet._emit_device("motor_test_stopped", v, self.actor, {})
        else:
            v.motor_seq = None

    def set_rates(self, rates: dict[str, float]) -> None:
        for message, hz in rates.items():
            if self.run.rates.get(message) == hz:
                continue
            self.fleet.message_interval(self.v.id, self.actor, message, hz)
            self.run.rates[message] = hz

    def restore_rates(self) -> None:
        if not self.run.rates or self.v.link_state not in ("ok", "delayed"):
            self.run.rates.clear()
            return
        for message in list(self.run.rates):
            hz = DEFAULT_RATES.get(message, 4.0)
            try:
                self.fleet.message_interval(self.v.id, Actor.operator(), message, hz)
            except RelayError:
                pass
        self.run.rates.clear()

    # -- flying ----------------------------------------------------------------------------------------

    def allow_arm(self, allowed: bool) -> None:
        self.run.arm_allowed = allowed
        self.run.prev_armed = self.v.armed

    def run_plan(self, steps: list[dict[str, Any]], confirm: str | None, name: str) -> None:
        self.fleet.run_plan(self.v.id, self.actor, steps, name, confirm)

    def run_skill(self, name: str, params: dict[str, Any]) -> None:
        self.fleet.run_skill(self.v.id, self.actor, name, params)

    def human_override(self) -> bool:
        s = self.v.sticks
        return s is not None and s.source == "human" and self.now() - s.t < 1.0

    def land_if_airborne(self) -> None:
        """The land skill when the aircraft is up and the run still controls it (abort of a flight step)."""
        v = self.v
        if not (v.armed and v.airborne()) or v.state["crashed"] or v.link_state not in ("ok", "delayed"):
            return
        if self.human_override():
            return
        try:
            self.fleet._check_control(v, self.actor)
            prepared, _ = self.fleet._prepare_skill(v, "land", {})
            self.fleet._cancel_plan(v, "bench procedure stopped", "标定流程已停止")
            self.fleet._start_skill(v, "land", prepared, None)
        except RelayError:
            pass

    def fail_flight(self, step: Step, reason: str) -> None:
        """A flight segment failed: later flight steps are skipped, and the aircraft lands unless a person
        has taken the sticks (then the run is aborted and the person keeps flying)."""
        self.shared["flight_failed"] = True
        self.shared["hover_failed"] = True
        if self.human_override():
            self.run.halt = "operator_override"
            return
        self.land_if_airborne()


class Run:
    def __init__(
        self,
        fleet: FleetService,
        v: Vehicle,
        run_id: int,
        procedure: str,
        actor: Actor,
        options: dict[str, Any],
    ) -> None:
        en, zh, step_ids, config = PROCEDURES[procedure]
        self.fleet = fleet
        self.vid = v.id
        self.run_id = run_id
        self.procedure = procedure
        self.title_en, self.title_zh = en, zh
        self.actor = actor
        self.options = options
        self.skip = set(options.get("skip") or ())
        self.steps: list[Step] = [make_step(s, config.get(s)) for s in step_ids]
        self.index = 0
        self.state = "running"
        self.shared: dict[str, Any] = {}
        self.rates: dict[str, float] = {}
        self.started = fleet.wall()
        self.ended: float | None = None
        self.verdict: str | None = None
        self.reason: str | None = None
        self.paused = False
        self.halt: str | None = None
        self.arm_allowed = False
        self.prev_armed = v.armed
        self.stop_mark = v.stop_window_until
        self.kind = v.kind
        self.label = v.label
        self.host = v.host
        self.autopilot = v.autopilot
        self.firmware = v.state["firmware"]
        self.session_id = v.record_session

    # -- helpers ----------------------------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self.state in ACTIVE

    @property
    def current(self) -> Step | None:
        return self.steps[self.index] if self.index < len(self.steps) else None

    def _vehicle(self) -> Vehicle | None:
        return self.fleet.vehicles.get(self.vid)

    def _ctx(self, v: Vehicle) -> Context:
        return Context(self.fleet, v, self)

    def _emit(self, kind: str, data: dict[str, Any]) -> None:
        payload = {
            "run_id": self.run_id,
            "procedure": self.procedure,
            "title_en": self.title_en,
            "title_zh": self.title_zh,
            **data,
        }
        self.fleet.events.emit(
            kind,
            self.vid,
            session=self.actor.session_id,
            data=payload,
            labels={"vehicle": self.label},
        )

    # -- lifecycle -------------------------------------------------------------------------------------

    def begin(self, v: Vehicle) -> None:
        self._emit("procedure_started", {"steps": [s.id for s in self.steps], "by": self.actor.kind})
        self._enter(v)

    def _enter(self, v: Vehicle) -> None:
        """Start the current step and every step after it that finishes at once."""
        ctx = self._ctx(v)
        while self.index < len(self.steps):
            step = self.steps[self.index]
            if step.id in self.skip:
                self._complete(v, step, StepResult("skipped", findings=[_skipped_note()]), advance=False)
                self.index += 1
                continue
            step.state = "running"
            step.started = self.fleet.wall()
            try:
                result = step.start(ctx)
            except RelayError as exc:
                result = StepResult(
                    "fail", findings=[finding("error", "fail", exc.message_en, exc.message_zh)]
                )
            except Exception:
                log.exception("procedure step %s failed to start on %s", step.id, self.vid)
                result = StepResult("fail", findings=[finding("error", "fail", *REASONS["error"])])
            if result is None:
                self._sync_state()
                return
            self._complete(v, step, result, advance=False)
            self.index += 1
        self._finish()

    def _complete(self, v: Vehicle, step: Step, result: StepResult, advance: bool = True) -> None:
        step.result = result
        step.prompt = None
        step.activity = None
        step.state = "skipped" if result.verdict == "skipped" else "done"
        step.ended = self.fleet.wall()
        en, zh = STEPS[step.id][0], STEPS[step.id][1]
        verdict_en, verdict_zh = VERDICTS[result.verdict]
        self._emit(
            "procedure_step",
            {
                "step": step.id,
                "step_en": en,
                "step_zh": zh,
                "verdict": result.verdict,
                "verdict_en": verdict_en,
                "verdict_zh": verdict_zh,
                "index": self.index,
                "total": len(self.steps),
            },
        )
        self.fleet._save_test_run(self)
        if advance:
            self.index += 1
            self._enter(v)

    def _finish(self) -> None:
        report = self.steps[-1].result if self.steps and self.steps[-1].id == "S12" else None
        verdicts = [s.result.verdict for s in self.steps if s.result is not None]
        overall = (
            report.verdict
            if report
            else ("fail" if "fail" in verdicts else "warn" if "warn" in verdicts else "pass")
        )
        self.verdict = overall if overall in FINAL else "pass"
        self.state = FINAL[self.verdict]
        self.ended = self.fleet.wall()
        v = self._vehicle()
        if v is not None:
            self._ctx(v).restore_rates()
        verdict_en, verdict_zh = VERDICTS[self.verdict]
        self._emit(
            "procedure_finished",
            {"verdict": self.verdict, "verdict_en": verdict_en, "verdict_zh": verdict_zh},
        )
        self.fleet._save_test_run(self)

    def _sync_state(self) -> None:
        step = self.current
        self.state = "waiting" if self.paused or (step is not None and step.state == "waiting") else "running"

    def on_waiting(self, step: Step) -> None:
        self.state = "waiting"
        prompt = step.prompt or {}
        en, zh = STEPS[step.id][0], STEPS[step.id][1]
        self._emit(
            "procedure_waiting",
            {
                "step": step.id,
                "step_en": en,
                "step_zh": zh,
                "prompt": prompt.get("id"),
                "prompt_en": prompt.get("text_en"),
                "prompt_zh": prompt.get("text_zh"),
                "operator_only": prompt.get("operator_only", False),
            },
        )

    # -- abort rules (05 §4.3) -------------------------------------------------------------------------

    def _abort_reason(self, v: Vehicle, now: float) -> str | None:
        if v.stop_seq is not None or v.stop_window_until > self.stop_mark:
            return "stopped"
        if v.link_state in ("lost", "offline"):
            return "link_lost"
        if v.state["crashed"]:
            return "crashed"
        if self.halt:
            return self.halt
        return None

    def _control(self, v: Vehicle) -> str | None:
        """None when the run's actor still controls the aircraft, "pause" or "lost" otherwise."""
        try:
            auth.check_control(v.auth, self.actor, self.fleet._claim_alive(v))
        except RelayError as exc:
            if self.actor.kind == "session" and exc.code in ("possessed_by_human", "claim_suspended"):
                return "pause"
            return "lost"
        return None

    def tick(self, now: float) -> None:
        v = self._vehicle()
        if v is None or not self.active:
            return
        reason = self._abort_reason(v, now)
        if reason:
            self.abort(reason)
            return
        control = self._control(v)
        if control == "lost":
            self.abort("control_lost")
            return
        if control == "pause":
            if not self.paused:
                self._pause(v)
            return
        if self.paused:
            if v.armed:
                return  # resume on a disarmed aircraft only
            self._resume(v)
            return
        if v.armed != self.prev_armed:
            if not self.arm_allowed:
                self.abort("armed")
                return
            self.prev_armed = v.armed
        step = self.current
        if step is None:
            return
        ctx = self._ctx(v)
        try:
            result = step.tick(ctx, now)
        except RelayError as exc:
            result = StepResult("fail", findings=[finding("error", "fail", exc.message_en, exc.message_zh)])
        except Exception:
            log.exception("procedure step %s failed on %s", step.id, self.vid)
            self.abort("error")
            return
        if self.halt and self.active:
            if result is not None:
                step.result = result
            self.abort(self.halt)
            return
        if result is not None:
            self._complete(v, step, result)
        elif self.active:
            self._sync_state()

    def _pause(self, v: Vehicle) -> None:
        self.paused = True
        step = self.current
        if step is not None:
            step.restart()
            step.state = "waiting"
            name = self.actor.name or "-"
            step.prompt = {
                "id": "paused",
                "text_en": "An operator has taken control. The procedure resumes when control returns "
                f"to {name} and the aircraft is disarmed.",
                "text_zh": f"操作员已接管控制。控制权交还给 {name} 且飞机上锁后，流程继续。",
                "detail_en": None,
                "detail_zh": None,
                "answers": [],
                "confirm_word": None,
                "operator_only": False,
                "data": {},
            }
            self.on_waiting(step)
        self.state = "waiting"

    def _resume(self, v: Vehicle) -> None:
        self.paused = False
        self.arm_allowed = False
        self.prev_armed = v.armed
        step = self.current
        if step is None:
            return
        step.restart()
        self._enter(v)

    def preempted(self, v: Vehicle) -> bool:
        """A stop, a lost link or a crash processed ahead of this command in the fleet queue wins over it:
        the run aborts here rather than on the next tick, so nothing it was about to do (spin, take off)
        happens."""
        reason = self._abort_reason(v, self.fleet.clock())
        if reason:
            self.abort(reason)
            return True
        return False

    def answer(self, actor: Actor, step_id: str, answer: str, value: Any, confirm: str | None) -> None:
        v = self._vehicle()
        if v is None or not self.active:
            raise err("no_procedure", vehicle=self.vid)
        if self.preempted(v):
            return
        step = self.current
        if step is None or step.id != step_id:
            raise err("procedure_step_mismatch", step=step_id, current=step.id if step else "-")
        prompt = step.prompt
        if step.state != "waiting" or prompt is None or self.paused:
            raise err("procedure_not_waiting", step=step_id)
        if answer not in {a["id"] for a in prompt["answers"]}:
            choices = ", ".join(a["id"] for a in prompt["answers"])
            raise err("invalid_request", reason=f"answer must be one of {choices}")
        if prompt.get("operator_only") and actor.kind != "operator":
            raise err("operator_required")
        if actor.kind == "session":
            auth.check_control(v.auth, actor, self.fleet._claim_alive(v))
        result = step.answer(self._ctx(v), answer, value, confirm)
        if self.halt and self.active:
            self.abort(self.halt)
            return
        if result is not None:
            self._complete(v, step, result)
        else:
            self._sync_state()

    def abort(self, reason: str) -> None:
        if not self.active:
            return
        v = self._vehicle()
        step = self.current
        if v is not None:
            ctx = self._ctx(v)
            if step is not None and reason not in ("stopped", "link_lost", "crashed", "operator_override"):
                try:
                    step.abort(ctx)
                except Exception:  # cleanup must not keep the run alive
                    log.exception("procedure abort cleanup failed on %s", self.vid)
            # always: a motor test started just before a stop is stopped too (no-op without a link or a test)
            ctx.stop_motors()
            ctx.restore_rates()
        if step is not None:
            step.state = "aborted"
            step.prompt = None
            step.activity = None
            step.ended = self.fleet.wall()
        self.state = "aborted"
        self.reason = reason
        self.verdict = None
        self.arm_allowed = False
        self.ended = self.fleet.wall()
        en, zh = REASONS.get(reason, (reason, reason))
        self._emit(
            "procedure_aborted",
            {
                "reason": reason,
                "step": step.id if step else None,
                "detail": en,
                "detail_en": en,
                "detail_zh": zh,
            },
        )
        self.fleet._save_test_run(self)

    # -- views -----------------------------------------------------------------------------------------

    def brief(self) -> dict[str, Any]:
        step = self.current
        return {
            "run_id": self.run_id,
            "procedure": self.procedure,
            "state": self.state,
            "step": step.id if step is not None and self.active else None,
            "index": min(self.index, len(self.steps)),
            "total": len(self.steps),
        }

    def view(self) -> dict[str, Any]:
        step = self.current
        reason = REASONS.get(self.reason or "") if self.reason else None
        return {
            "run_id": self.run_id,
            "procedure": self.procedure,
            "title_en": self.title_en,
            "title_zh": self.title_zh,
            "vehicle": self.vid,
            "label": self.label,
            "kind": self.kind,
            "host": self.host,
            "autopilot": self.autopilot,
            "firmware": self.firmware,
            "simulated": self.kind == "sim",
            "state": self.state,
            "step": step.id if step is not None and self.active else None,
            "index": min(self.index, len(self.steps)),
            "total": len(self.steps),
            "verdict": self.verdict,
            "reason": self.reason,
            "reason_en": reason[0] if reason else None,
            "reason_zh": reason[1] if reason else None,
            "paused": self.paused,
            "by": {"kind": self.actor.kind, "name": self.actor.name},
            "options": {k: (sorted(v) if isinstance(v, set) else v) for k, v in self.options.items()},
            "steps": [s.view() for s in self.steps],
            "started": self.started,
            "ended": self.ended,
        }

    def params_record(self) -> dict[str, Any]:
        changed = []
        for s in self.steps:
            if s.result is None:
                continue
            for item in s.result.suggestions:
                if item.get("state") == "applied":
                    changed.append(
                        {"name": item["name"], "from": item.get("current"), "to": item.get("suggested")}
                    )
        return {"baseline": self.shared.get("baseline", {}), "changed": changed}


def _skipped_note() -> dict[str, Any]:
    return finding("skipped_by_option", "info", "Skipped by the procedure options.", "按流程选项跳过。")


def clean(value: Any) -> Any:
    """JSON-safe copy (NaN and infinities become None)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: clean(x) for k, x in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(x) for x in value]
    return value
