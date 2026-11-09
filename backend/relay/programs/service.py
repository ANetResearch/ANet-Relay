"""Deployment of onboard programs on the fleet thread (06 §2.6).

``ProgramService`` belongs to ``FleetService`` (``fleet.programs``). It deploys a stored program revision
to one aircraft in a program host of its own (``Run.host``), runs every running program once per stick tick
(50 Hz, ``tick``: every host gets its frame, then the replies are collected against one 15 ms deadline), and
writes the outputs through the fleet's own stick path as the ``program`` source: the
vehicle's controller becomes ``program`` (``Vehicle.controller``), the timeline shows ``PROGRAM · <name>``
and the 0.8 s stick timeout, stop windows and calibration signs apply unchanged.

Rules:

* Who: the operator, or the session that controls the aircraft (``authority.check_control``).
* Modes: ``pilot`` (the program alone holds the sticks) and ``copilot`` (the program reads the person's
  sticks as ``obs.sticks_in`` and its output replaces them; without fresh human sticks it sends nothing).
* A person possessing the aircraft pauses a pilot-mode program at once; release does not resume it.
  Emergency stop, link loss, a crash, removal, reset, a new skill / plan / re-fly, sticks from a session and
  the end of the deploying session's claim stop it. Stopped and faulted programs release the sticks
  (``stop_sticks``), so the autopilot's own failsafe takes over.
* Isolation: one host process per deployment, so a program cannot read another's data, take its time, use
  its memory or crash its host. The host starts and loads on the caller's thread before the fleet thread
  swaps the deployment in (a revision that fails to load leaves the flying one as it was); stopping, faulting
  and pausing release the sticks first and kill the host without a round trip; live parameters travel with
  the next frame. The fleet thread never waits for a host beyond the frame deadline.
* Frames: a raising or invalid frame sends safe sticks (attitude centred, throttle held) and counts a fault;
  25 consecutive faults fault the program. A frame that misses its 15 ms deadline sends safe sticks with the
  time of the last good frame (so a hung host still meets the 0.8 s stick timeout); three in a row, a host
  that exits during a frame or one the memory watchdog killed fault the program. A host that exits between
  frames is restarted in the background and the program reloaded (``ctx.mem`` starts empty), at most
  ``RESTART_LIMIT`` times in ``RESTART_WINDOW_S``.
* Real aircraft: only after the operator allows programs on that aircraft (confirm word ``PROGRAM``,
  reset whenever the link drops) and with a passing preflight; ``fire`` is ignored.
* While a program flies an aircraft Relay asks for ATTITUDE 50 Hz, LOCAL_POSITION_NED 20 Hz, VFR_HUD 20 Hz and
  SCALED_IMU 50 Hz, and checks after 3 s that ATTITUDE arrives at 40 Hz or more (``rates_ok``).

Seams for later packages: ``ProgramService(fleet, host_factory=InProcessHost)`` runs programs in process
(headless evaluators; one engine per deployment); ``hosts`` maps aircraft to their host; ``teams`` maps
aircraft or contact ids to team names for ``obs.contacts``; ``deploy_source(...)`` deploys source that is not
in the store (scenarios); ``runs`` holds the ``Run`` of each aircraft; ``validate`` / ``run_tests`` wrap the
validator and the one-shot test host.
"""

from __future__ import annotations

import itertools
import logging
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .. import authority as auth
from .. import preflight
from ..authority import Actor
from ..config import STICK_TIMEOUT_S
from ..errors import RelayError, err
from ..mav import decode, modes
from ..model import StickInput
from . import obs as obsmod
from . import validator
from .hostclient import HostClient, HostError, StepReply, run_tests_isolated
from .store import ProgramStore, check_params, coerce_param, fitting_params

if TYPE_CHECKING:
    from ..fleet import FleetService, Vehicle

log = logging.getLogger("relay.programs")

FAULT_LIMIT = 25
BATCH_DEADLINE_S = 0.015
HARD_STEP_MS = 8.0
HOST_TIMEOUT_LIMIT = 3
RESTART_WINDOW_S = 30.0
RESTART_LIMIT = 5
LOG_RING = 500
FPS_WINDOW_S = 1.0
RATE_CHECK_S = 3.0
RATE_MIN_HZ = 40.0
ARM_PRIME_S = (
    0.6  # zero-throttle sticks reach the autopilot before an arming deployment changes mode and arms
)
ERROR_LOG_PERIOD_S = 1.0
CONFIRM_WORD = "PROGRAM"
MODES = ("pilot", "copilot")

# reason code -> (English, Chinese), for program_stopped / program_paused / program_faulted
REASONS: dict[str, tuple[str, str]] = {
    "stopped": ("stopped by the controller", "被控制者停止"),
    "possessed": ("an operator took over", "操作员已接管"),
    "emergency_stop": ("emergency stop", "急停"),
    "link_lost": ("link lost", "链路丢失"),
    "crashed": ("crashed", "坠毁"),
    "removed": ("aircraft removed", "飞机已移除"),
    "reset": ("simulator reset", "模拟机已复位"),
    "shutdown": ("Relay is shutting down", "Relay 正在关闭"),
    "superseded": ("superseded by another command", "被其他指令取代"),
    "claim_ended": ("the deploying session released the aircraft", "部署程序的会话已放弃认领"),
    "replaced": ("replaced by a new deployment", "被新的部署取代"),
    "not_allowed": ("programs are no longer allowed on this aircraft", "这架飞机已不再允许运行程序"),
    "faults": ("too many failed frames", "连续失败的帧过多"),
    "host": ("the program host stopped responding", "程序宿主无响应"),
    "memory": ("program state exceeds the memory limit", "程序状态超过内存上限"),
}
RESTART_REASONS = {
    "exited": ("the program host exited", "程序宿主已退出"),
    "timeouts": ("3 batches missed the 15 ms deadline", "连续 3 批超过 15 ms 时限"),
    "memory": ("the program host exceeded its memory limit", "程序宿主超过内存上限"),
}
_STOP_CONTROL_REASONS = {
    "link lost": "link_lost",
    "removed": "removed",
    "reset": "reset",
    "shutdown": "shutdown",
    "crashed": "crashed",
}


class Run:
    """One deployment on one aircraft (fleet thread)."""

    def __init__(
        self,
        key: str,
        vid: str,
        program: dict[str, Any],
        rev: int,
        source: str,
        params: dict[str, Any],
        defaults: dict[str, Any],
        ranges: dict[str, list[float]],
        mode: str,
        owner: Actor,
        seed: int,
        builder: obsmod.ObsBuilder,
        started: float,
        started_wall: float,
    ) -> None:
        self.key = key
        self.vid = vid
        self.program_id = program["id"]
        self.name = program["name"]
        self.rev = rev
        self.source = source
        self.params = params
        self.defaults = defaults
        self.ranges = ranges
        self.mode = mode
        self.owner = owner
        self.seed = seed
        self.builder = builder
        self.started = started
        self.started_wall = started_wall
        self.status = "running"
        self.reason: str | None = None
        self.state = ""
        self.sm: dict[str, Any] | None = None
        self.faults = 0
        self.consecutive = 0
        self.overruns = 0
        self.frames = 0
        self.frame_times: deque[float] = deque(maxlen=128)
        self.last_error: str | None = None
        self.last_error_logged = (None, -1e9)
        self.last_throttle = 0.0
        self.last_out_t: float | None = None
        self.human_in: StickInput | None = None
        self.rates_check_at: float | None = started + RATE_CHECK_S
        self.rates_ok: bool | None = None
        self.rates_retried = False
        self.logs: deque[dict[str, Any]] = deque(maxlen=LOG_RING)
        self.log_seq = 0
        self.host: Any = None  # this deployment's own program host
        self.timeouts = 0
        self.restarts: deque[float] = deque(maxlen=16)
        self.restarting = False
        self.pending_params: dict[str, Any] = {}  # live changes sent with the next frame
        self.firing = False  # the program holds the trigger (Arena.fire)
        self.armed_here = False  # this deployment armed the aircraft (deploy arm): it disarms on the ground
        self.disarm_refused = False  # ctx.disarm() in the air was refused (logged once)

    @property
    def active(self) -> bool:
        return self.status in ("running", "paused")

    @property
    def flying(self) -> bool:
        """The program holds the sticks (pilot mode, running); the vehicle's controller is ``program``."""
        return self.status == "running" and self.mode == "pilot"

    def add_log(self, wall: float, text: str, level: str = "log") -> None:
        self.log_seq += 1
        self.logs.append({"seq": self.log_seq, "t": round(wall, 3), "level": level, "text": text})

    def fps(self, now: float) -> float:
        return round(sum(1 for t in self.frame_times if now - t <= FPS_WINDOW_S) / FPS_WINDOW_S, 1)

    def view(self, now: float) -> dict[str, Any]:
        return {
            "id": self.program_id,
            "name": self.name,
            "rev": self.rev,
            "state": self.state,
            "status": self.status,
            "reason": self.reason,
            "mode": self.mode,
            "fps": self.fps(now),
            "frames": self.frames,
            "faults": self.faults,
            "overruns": self.overruns,
            "last_error": self.last_error,
            "params": dict(self.params),
            "seed": self.seed,
            "rates_ok": self.rates_ok,
            "sm": self.sm,
            "log_seq": self.log_seq,
            "owner": {"kind": self.owner.kind, "name": self.owner.name or "operator"},
            "started_at": self.started_wall,
        }


class ProgramService:
    def __init__(
        self,
        fleet: FleetService,
        host_factory: Callable[[], Any] = HostClient,
        test_runner: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.fleet = fleet
        self.store = ProgramStore(fleet.data_dir)
        self.host_factory = host_factory
        self.test_runner = test_runner or run_tests_isolated
        self.runs: dict[str, Run] = {}
        self.teams: dict[str, str] = {}
        self._keys = itertools.count(1)

    # ====================================================================== hosts (one per deployment)

    @property
    def hosts(self) -> dict[str, Any]:
        """The host of each aircraft's active deployment (diagnostics and tests)."""
        return {vid: r.host for vid, r in self.runs.items() if r.host is not None}

    def _new_host(self) -> Any:
        """Start a host for one deployment (any thread except the fleet thread: it may take a while)."""
        host = self.host_factory()
        try:
            host.start()
        except HostError as exc:
            raise err("program_host_unavailable", reason=str(exc)) from exc
        return host

    @staticmethod
    def _discard(host: Any) -> None:
        """Kill a host without waiting for it (``HostClient.discard``)."""
        if host is None:
            return
        try:
            (getattr(host, "discard", None) or host.kill)()
        except Exception:  # pragma: no cover - a host that is already gone
            log.exception("discarding a program host")

    def close(self) -> None:
        for r in self.runs.values():
            host, r.host = r.host, None
            self._discard(host)

    def _host_lost(self, r: Run, culprit: bool, reason: str) -> None:
        """``r``'s host exited, missed 3 batches or grew past its memory limit. The program itself is to blame
        (it was running, or the host's memory is its memory): fault it. Otherwise restart the host off the
        fleet thread and reload the program (``ctx.mem`` starts empty), at most ``RESTART_LIMIT`` times in
        ``RESTART_WINDOW_S``. Other aircraft's programs run in their own hosts and are not affected."""
        now = self.fleet.clock()
        host, r.host = r.host, None
        r.timeouts = 0
        self._discard(host)
        r.restarts.append(now)
        recent = [t for t in r.restarts if now - t <= RESTART_WINDOW_S]
        give_up = not culprit and len(recent) > RESTART_LIMIT
        en, zh = RESTART_REASONS.get(reason, (reason, reason))
        self.fleet.events.emit(
            "program_host_restarted",
            r.vid,
            data={
                "reason": reason,
                "reason_en": en,
                "reason_zh": zh,
                "culprit": r.key if culprit else None,
                "gave_up": give_up,
            },
            labels={"vehicle": self._label(r)},
        )
        log.warning("program host of %s lost (%s), culprit %s", r.vid, reason, culprit)
        if culprit or give_up:
            why = "memory" if reason == "memory" else "host"
            self._fault(r, why, f"{en}; the program host was restarted")
            return
        self._restart(r)

    def _restart(self, r: Run) -> None:
        r.restarting = True

        def work() -> None:
            host = None
            error = None
            try:
                host = self._new_host()
                reply = host.load(r.key, r.source, dict(r.params), r.seed)
                if not reply.get("ok"):
                    error = f"reload failed: {reply.get('error')}"
            except RelayError as exc:
                error = f"the program host could not be restarted: {exc.message_en}"
            except HostError as exc:
                error = f"reload failed: {exc}"
            self.fleet.post(lambda: self._adopt(r, host, error))

        if self.fleet._thread is None:  # stepped by tests and evaluators: restart inline
            work()
            return
        threading.Thread(target=work, name="program-host-restart", daemon=True).start()

    def _adopt(self, r: Run, host: Any, error: str | None) -> None:
        r.restarting = False
        if self.runs.get(r.vid) is not r or not r.active:
            self._discard(host)
            return
        if error is not None:
            self._discard(host)
            self._fault(r, "host", error)
            return
        r.host = host
        r.add_log(self.fleet.wall(), "program reloaded after a host restart; ctx.mem starts empty", "event")

    # ====================================================================== deploy and control

    def _check_actor(self, v: Vehicle, actor: Actor) -> None:
        if actor.kind == "operator":
            return
        auth.check_control(v.auth, actor, self.fleet._claim_alive(v))

    def _resolve(self, program_id: str, rev: int | None) -> tuple[dict[str, Any], dict[str, Any]]:
        program = self.store.get(program_id)
        revision = self.store.revision(program_id, rev)
        return program, revision

    def deploy(self, vid: str, actor: Actor, body: dict[str, Any]) -> dict[str, Any]:
        """``POST /vehicles/{id}/program`` (06 §2.6). Runs the store lookups on the caller's thread."""
        program_id = body.get("program")
        if not isinstance(program_id, str) or not program_id:
            raise err("invalid_request", reason="program is required")
        rev = body.get("rev")
        if rev is not None and (isinstance(rev, bool) or not isinstance(rev, int)):
            raise err("invalid_request", reason="rev must be an integer")
        arm = body.get("arm")
        if arm is not None and not isinstance(arm, bool):
            raise err("invalid_request", reason="arm must be a boolean")
        confirm = body.get("confirm")
        if arm:
            # checked before anything changes: a real aircraft needs the word ARM and a passing preflight
            self.fleet.call(lambda: self._arm_ready(vid, confirm))
        program, revision = self._resolve(program_id, rev)
        params = dict(body.get("params") or {})
        team = body.get("team")
        if team is not None:
            # 08 §5: the aircraft's team in every contact list, and the program's own team parameter
            if not isinstance(team, str) or not 0 < len(team.strip()) <= 16:
                raise err("invalid_request", reason="team must be a string of 1 to 16 characters")
            team = team.strip()
            if "team" in validator.validate(revision["source"]).params:
                params.setdefault("team", team)
        deployed = self.deploy_source(
            vid,
            actor,
            program,
            revision["rev"],
            revision["source"],
            params,
            stored=revision.get("params") or {},
            mode=body.get("mode") or "pilot",
            sensors=body.get("sensors"),
            arena=body.get("arena"),
            seed=body.get("seed"),
            airframe=body.get("airframe"),  # WP-2a seam (06 §3.3)
        )
        if team is not None:
            self.teams[vid] = team
        if arm:
            deployed["arm"] = self._arm_for_run(vid, actor, confirm)
        return deployed

    def _arm_ready(self, vid: str, confirm: Any) -> None:
        v = self.fleet._vehicle(vid)
        if not v.armed:
            self.fleet._arm_checks(v, confirm if isinstance(confirm, str) else None)

    def _arm_for_run(self, vid: str, actor: Actor, confirm: Any) -> dict[str, Any]:
        """Arm the aircraft for the program just deployed (the program's sticks start once it is armed).

        The deployment is in place either way; a refusal comes back as ``{ok: false, code, reason}`` and the
        program waits on the ground, as it does when the aircraft is armed later by hand.
        """
        armed, mode, autopilot = self.fleet.call(
            lambda: (lambda v: (v.armed, v.mode, v.autopilot))(self.fleet._vehicle(vid))
        )
        if armed:
            return {"ok": True, "armed": True, "already": True}
        # the autopilot must see the zero-throttle sticks before the mode change and the arm command
        self.fleet.call(lambda: self._idle(self.fleet._vehicle(vid), self.fleet.clock()))
        if self.fleet.threaded:
            time.sleep(ARM_PRIME_S)
        try:
            if modes.mode_class(autopilot, mode) != "manual":
                # programs fly the throttle itself, as the MCP deploy does: STABILIZE (PX4 STABILIZED)
                manual = "STABILIZED" if autopilot == modes.PX4 else "STABILIZE"
                self.fleet.set_mode(vid, actor, manual, authorised=True)
            # the deployment checked the actor; an operator does not have to possess the aircraft for it
            word = confirm if isinstance(confirm, str) else None
            result = self.fleet.arm(vid, actor, True, word, authorised=True)
        except RelayError as exc:
            return {"ok": False, "armed": False, "code": exc.code, "reason": exc.message_en}

        def mark() -> None:
            r = self.runs.get(vid)
            if r is not None and r.active:
                r.armed_here = True
                r.add_log(self.fleet.wall(), "armed for this deployment", "event")

        self.fleet.call(mark)
        return {"ok": True, "armed": result.get("armed")}

    def deploy_source(
        self,
        vid: str,
        actor: Actor,
        program: dict[str, Any],
        rev: int,
        source: str,
        params: dict[str, Any],
        *,
        stored: dict[str, Any] | None = None,
        mode: str = "pilot",
        sensors: Any = None,
        arena: Any = None,
        seed: Any = None,
        airframe: Any = None,
    ) -> dict[str, Any]:
        """Deploy source directly (scenarios and evaluators); ``program`` needs ``id`` and ``name``.

        ``params`` are checked strictly; ``stored`` (a revision's saved overrides) only where they still fit
        the source's PARAMS and PARAM_RANGES. The new deployment gets its own host, started and loaded on the
        caller's thread; only when that succeeded does the fleet thread replace the aircraft's current
        deployment (a revision that fails to load leaves the program that is flying untouched).

        ``airframe`` (simulators only, 06 §3.3 / §4): ``{preset | params, seed?}`` fitted to the simulator
        before the program starts, as ``PUT /sims/{vid}/airframe`` would. The program never sees the values:
        defects reach ``obs`` only through the simulated sensors (ATTITUDE, SCALED_IMU, throttle, motion).
        """
        if mode not in MODES:
            raise err("invalid_request", reason="mode must be pilot or copilot")
        result = validator.validate(source)
        if not result.ok:
            first = result.errors[0]
            raise err(
                "program_invalid",
                reason=f"line {first.line}: {first.message_en}",
                problems=[p.view() for p in result.errors],
            )
        merged = self._params(
            {**fitting_params(stored or {}, result.params, result.param_ranges), **(params or {})},
            result.params,
            result.param_ranges,
        )
        try:
            spec = obsmod.Sensors.parse(sensors)
            arena_box = obsmod.parse_arena(arena)
        except ValueError as exc:
            raise err("invalid_request", reason=str(exc)) from exc
        if seed is None:
            seed = secrets.randbelow(2**31)
        elif isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**63:
            raise err("invalid_request", reason="seed must be a non-negative integer")
        if airframe is not None and not isinstance(airframe, dict):
            raise err("invalid_request", reason="airframe must be an object")

        def checks() -> tuple[Vehicle, Any]:
            v = self.fleet._vehicle(vid)
            self._check_actor(v, actor)
            self.fleet._require_link(v)
            if v.kind == "real":
                self._check_real(v)
            if v.kind == "sim" and v.state["crashed"]:
                raise err("crashed")
            fit = None
            if airframe is not None:  # WP-2a: resolve (and reject) before anything changes
                if v.kind != "sim":
                    raise err("sim_only")
                fit = self.fleet._resolve_airframe(airframe, v.driver.sim.airframe)
            return v, fit

        self.fleet.call(lambda: checks() and None)
        # load off the fleet thread: compiling and a module body take their time, the sticks do not wait
        host = self._new_host()
        key = f"{vid}#{next(self._keys)}"
        try:
            reply = host.load(key, source, merged, int(seed))
        except HostError as exc:
            self._discard(host)
            raise err("program_host_unavailable", reason=str(exc)) from exc
        if not reply.get("ok"):
            self._discard(host)
            raise err(
                "program_load_failed",
                reason=str(reply.get("error") or "unknown"),
                problems=reply.get("problems") or [],
            )

        def install() -> dict[str, Any]:
            v, fit = checks()
            now = self.fleet.clock()
            wall = self.fleet.wall()
            old = self.runs.get(vid)
            inherited = bool(old is not None and old.active and old.armed_here and v.armed)
            if old is not None and old.active:
                self._stop(old, "replaced", emit=True)
            if fit is not None:
                self.fleet._apply_airframe(v, fit, actor)
            # the program is now the stick source: stop skills, plans and earlier sticks
            self.fleet._stop_skill(v, "superseded by a program", "被机载程序取代")
            self.fleet._cancel_plan(v, "superseded by a program", "被机载程序取代")
            v.rc_frame = None
            if v.sticks is not None and v.sticks.source != "human":
                v.sticks = None
            builder = obsmod.ObsBuilder(int(seed), spec, arena_box, t0=now)
            r = Run(
                key,
                vid,
                program,
                rev,
                source,
                merged,
                dict(result.params),
                dict(result.param_ranges),
                mode,
                actor,
                int(seed),
                builder,
                now,
                wall,
            )
            r.host = host
            r.armed_here = inherited  # a new revision on an aircraft the old one armed takes the duty over
            r.state = str(reply.get("state") or "")
            for line in reply.get("logs") or []:
                r.add_log(wall, str(line))
            if mode == "pilot" and v.auth.possession is not None:
                r.status = "paused"
                r.reason = "possessed"
            self.runs[vid] = r
            v.program = r
            self._request_rates(v, obsmod.PROGRAM_RATES)
            r.add_log(wall, f"deployed {r.name} revision {rev} in {mode} mode", "event")
            self.fleet.events.emit(
                "program_started",
                v.id,
                session=actor.session_id,
                data={
                    "program": r.program_id,
                    "name": r.name,
                    "rev": rev,
                    "mode": mode,
                    "by": actor.kind,
                    "session_name": actor.name,
                    "status": r.status,
                },
                labels={"vehicle": v.label, "name": r.name, "rev": str(rev)},
            )
            return {"id": vid, "program": r.view(now)}

        def run() -> dict[str, Any]:
            try:
                return install()
            except BaseException:
                self._discard(host)  # refused after loading: the new host goes, the old run is as it was
                raise

        return self.fleet.call(run, timeout=3.0)

    def _params(
        self, given: dict[str, Any], defaults: dict[str, Any], ranges: dict[str, list[float]]
    ) -> dict[str, Any]:
        check_params(given)
        out = dict(defaults)
        for name, value in given.items():
            if name not in defaults:
                raise err("program_param_invalid", name=name, reason="not in PARAMS")
            out[name] = coerce_param(name, value, defaults[name], ranges.get(name))
        return out

    def _check_real(self, v: Vehicle) -> None:
        if not v.programs_allowed:
            raise err("programs_not_allowed")
        summary = preflight.summary(self.fleet._preflight_items(v))
        if not summary["ok"]:
            raise err("preflight_blocked", items=", ".join(summary["blocking"]), blocking=summary["blocking"])

    def _run_for(self, v: Vehicle) -> Run:
        r = self.runs.get(v.id)
        if r is None:
            raise err("no_program")
        return r

    def stop(self, vid: str, actor: Actor) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            v = self.fleet._vehicle(vid)
            self._check_actor(v, actor)
            r = self._run_for(v)
            if r.active:
                self._stop(r, "stopped", emit=True, by=actor.kind)
            return {"id": vid, "program": r.view(self.fleet.clock())}

        return self.fleet.call(run)

    def resume(self, vid: str, actor: Actor) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            v = self.fleet._vehicle(vid)
            self._check_actor(v, actor)
            r = self._run_for(v)
            if r.status == "running":
                return {"id": vid, "program": r.view(self.fleet.clock())}
            if r.status != "paused":
                raise err("program_not_paused", status=r.status)
            if r.mode == "pilot" and v.auth.possession is not None:
                raise err("possessed_by_human", possession=v.auth.possession.view())
            self.fleet._require_link(v)
            if v.kind == "real":
                self._check_real(v)
            if r.host is None or not r.host.alive:
                raise err("program_host_unavailable", reason="restarting")
            self.fleet._stop_skill(v, "superseded by a program", "被机载程序取代")
            self.fleet._cancel_plan(v, "superseded by a program", "被机载程序取代")
            r.status = "running"
            r.reason = None
            r.consecutive = 0
            r.builder.last = None
            r.add_log(self.fleet.wall(), "resumed", "event")
            self.fleet.events.emit(
                "program_resumed",
                v.id,
                session=actor.session_id,
                data={"program": r.program_id, "name": r.name, "rev": r.rev, "by": actor.kind},
                labels={"vehicle": v.label, "name": r.name},
            )
            return {"id": vid, "program": r.view(self.fleet.clock())}

        return self.fleet.call(run)

    def set_params(self, vid: str, actor: Actor, patch: Any) -> dict[str, Any]:
        """``PATCH /vehicles/{id}/program/params``: types and ranges follow the PARAMS defaults."""
        if not isinstance(patch, dict) or not patch:
            raise err("invalid_request", reason="give at least one parameter")

        def run() -> dict[str, Any]:
            v = self.fleet._vehicle(vid)
            self._check_actor(v, actor)
            r = self._run_for(v)
            if not r.active:
                raise err("no_program")
            values = {}
            for name, value in patch.items():
                if name not in r.defaults:
                    raise err("program_param_invalid", name=str(name), reason="not in PARAMS")
                values[name] = coerce_param(name, value, r.defaults[name], r.ranges.get(name))
            # sent with the program's next frame: the fleet thread never waits for a host here
            r.params.update(values)
            r.pending_params.update(values)
            text = ", ".join(f"{k} = {x}" for k, x in values.items())
            r.add_log(self.fleet.wall(), f"params {text} ({actor.name or actor.kind})", "event")
            return {"id": vid, "params": dict(r.params)}

        return self.fleet.call(run)

    def log(self, vid: str, after: int = 0, limit: int = LOG_RING) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            v = self.fleet._vehicle(vid)
            r = self._run_for(v)
            lines = [line for line in r.logs if line["seq"] > after][: max(1, min(limit, LOG_RING))]
            return {"id": vid, "program": r.program_id, "lines": lines, "last_seq": r.log_seq}

        return self.fleet.call(run)

    def allow(self, vid: str, on: bool, confirm: str | None) -> dict[str, Any]:
        """Operator switch ``Allow programs on this aircraft`` (real aircraft; reset when the link drops)."""

        def run() -> dict[str, Any]:
            v = self.fleet._vehicle(vid)
            if v.kind != "real":
                return {"id": vid, "programs_allowed": True}
            if on and confirm != CONFIRM_WORD:
                raise err("confirm_required", expected=CONFIRM_WORD)
            changed = v.programs_allowed != bool(on)
            v.programs_allowed = bool(on)
            if not on:
                r = self.runs.get(vid)
                if r is not None and r.active:
                    self._stop(r, "not_allowed", emit=True)
            if changed:
                self.fleet.events.emit(
                    "program_gate",
                    v.id,
                    data={"allowed": v.programs_allowed},
                    labels={
                        "vehicle": v.label,
                        "detail_en": "allowed" if on else "not allowed",
                        "detail_zh": "已允许" if on else "已禁止",
                    },
                )
            return {"id": vid, "programs_allowed": v.programs_allowed}

        return self.fleet.call(run)

    # ====================================================================== status changes

    def _idle(self, v: Vehicle, t: float) -> None:
        """Disarmed: centred sticks at zero throttle. A real autopilot arms a pilot mode only with stick
        input present (PX4 "No manual control input", ArduPilot "Throttle below failsafe"), and only with the
        throttle down; releasing the sticks here left nothing for it to see. A person's live sticks win."""
        h = v.sticks
        if h is not None and h.source == "human" and self.fleet.clock() - h.t <= STICK_TIMEOUT_S:
            return
        v.sticks = StickInput(0.0, 0.0, 0.0, 0.0, t=t, source="program")
        v.rc_frame = None

    def _release_sticks(self, v: Vehicle | None) -> None:
        if v is not None and v.sticks is not None and v.sticks.source == "program":
            v.sticks = None
            v.driver.stop_sticks()

    def _release_trigger(self, r: Run, v: Vehicle | None, now: float | None = None) -> None:
        """Let go of the trigger the program pressed (``Arena.fire`` would keep shooting until its 0.3 s hold
        expires). Only the program's own press: a person's fire input on the same aircraft stays."""
        if not r.firing:
            return
        r.firing = False
        if v is None or v.kind != "sim":
            return
        gun = self.fleet.arena.guns.get(v.id)
        now = self.fleet.clock() if now is None else now
        if gun is not None and gun.fire_until > now:
            self.fleet.arena.fire(v, False, None, now)

    def _vehicle_of(self, r: Run) -> Vehicle | None:
        return self.fleet.vehicles.get(r.vid)

    def _label(self, r: Run) -> str:
        v = self._vehicle_of(r)
        return v.label if v is not None else r.vid

    def _end(self, r: Run, status: str, reason: str) -> Vehicle | None:
        v = self._vehicle_of(r)
        r.status = status
        r.reason = reason
        self._release_sticks(v)
        self._release_trigger(r, v)
        if r.armed_here and reason != "replaced" and v is not None and v.armed and v.landed == "ground":
            v.driver.arm(False)  # armed for this deployment and still on the ground: leave it as it was
            r.add_log(self.fleet.wall(), "disarmed on the ground", "event")
        host, r.host = r.host, None
        self._discard(host)  # the sticks are released first; the host goes without a round trip
        if v is not None:
            self._request_rates(v, obsmod.DEFAULT_RATES)
        return v

    def _stop(self, r: Run, reason: str, emit: bool = True, by: str | None = None) -> None:
        v = self._end(r, "stopped", reason)
        en, zh = REASONS.get(reason, (reason, reason))
        r.add_log(self.fleet.wall(), f"stopped: {en}", "event")
        if emit:
            self.fleet.events.emit(
                "program_stopped",
                r.vid,
                data={
                    "program": r.program_id,
                    "name": r.name,
                    "reason": reason,
                    "by": by,
                    "detail_en": en,
                    "detail_zh": zh,
                },
                labels={"vehicle": v.label if v else r.vid, "name": r.name},
            )

    def _fault(self, r: Run, reason: str, error: str) -> None:
        v = self._end(r, "faulted", reason)
        r.last_error = error
        en, zh = REASONS.get(reason, (reason, reason))
        r.add_log(self.fleet.wall(), f"faulted: {error}", "error")
        self.fleet.events.emit(
            "program_faulted",
            r.vid,
            data={
                "program": r.program_id,
                "name": r.name,
                "reason": reason,
                "error": error,
                "detail_en": en,
                "detail_zh": zh,
            },
            labels={"vehicle": v.label if v else r.vid, "name": r.name},
        )

    def _pause(self, r: Run, reason: str) -> None:
        r.status = "paused"
        r.reason = reason
        v = self._vehicle_of(r)
        self._release_sticks(v)
        self._release_trigger(r, v)
        en, zh = REASONS.get(reason, (reason, reason))
        r.add_log(self.fleet.wall(), f"paused: {en}", "event")
        self.fleet.events.emit(
            "program_paused",
            r.vid,
            data={
                "program": r.program_id,
                "name": r.name,
                "reason": reason,
                "detail_en": en,
                "detail_zh": zh,
            },
            labels={"vehicle": v.label if v else r.vid, "name": r.name},
        )

    def _request_rates(self, v: Vehicle, rates: tuple[tuple[int, float], ...]) -> None:
        if v.link_state not in ("ok", "delayed"):
            return
        for msg_id, hz in rates:
            v.driver.set_message_interval(msg_id, hz)

    # ====================================================================== fleet hooks

    def on_authority(self, v: Vehicle, outcome: auth.Outcome) -> None:
        """After ``_apply_effects``: possession pauses, emergency stop and the owner's claim end stop."""
        r = self.runs.get(v.id)
        if r is None or not r.active:
            return
        if auth.EMERGENCY_STOP in outcome.effects:
            self._stop(r, "emergency_stop")
            return
        for kind, data in outcome.events:
            if (
                kind == "claim_released"
                and r.owner.kind == "session"
                and data.get("session_id") == r.owner.session_id
            ):
                self._stop(r, "claim_ended")
                return
            if kind == "possession_started" and r.mode == "pilot" and r.status == "running":
                self._pause(r, "possessed")
                return

    def on_vehicle_stop(self, v: Vehicle, reason: str) -> None:
        """``_stop_control`` (link lost, removal, reset, shutdown) and crashes stop the program."""
        r = self.runs.get(v.id)
        if r is not None and r.active:
            self._stop(r, _STOP_CONTROL_REASONS.get(reason, "superseded"))
        if reason == "removed":
            self.runs.pop(v.id, None)

    def on_superseded(self, v: Vehicle) -> None:
        """A skill, plan or re-fly started on the aircraft."""
        r = self.runs.get(v.id)
        if r is not None and r.active:
            self._stop(r, "superseded")

    def on_session_sticks(self, v: Vehicle, actor: Actor) -> None:
        r = self.runs.get(v.id)
        if r is not None and r.active and r.mode == "pilot" and actor.kind == "session":
            self._stop(r, "superseded", by="session")

    def on_link(self, v: Vehicle, state: str) -> None:
        if state in ("lost", "offline"):
            v.programs_allowed = False  # "each connection": the operator allows programs again

    # ====================================================================== the 50 Hz tick

    def tick(self, now: float) -> None:
        for r in list(self.runs.values()):
            host = r.host
            if r.active and not r.restarting and (host is None or not host.alive):
                memory = host is not None and host.killed_for == "memory"
                # exited with a frame outstanding (or killed for its memory): the program did it
                culprit = memory or (host is not None and host.busy)
                self._host_lost(r, culprit, "memory" if memory else "exited")
        runs = [r for r in self.runs.values() if r.status == "running" and r.host is not None]
        if not runs:
            return
        items: list[tuple[Run, Vehicle, dict[str, Any]]] = []
        for r in runs:
            v = self._vehicle_of(r)
            if v is None or v.link_state not in ("ok", "delayed"):
                continue
            if r.mode == "pilot" and v.auth.possession is not None:
                self._pause(r, "possessed")
                continue
            sticks_in = None
            if r.mode == "copilot":
                if v.sticks is not None and v.sticks.source == "human":
                    r.human_in = v.sticks
                h = r.human_in
                if h is None or v.auth.possession is None or now - h.t > STICK_TIMEOUT_S:
                    continue
                sticks_in = {
                    "roll": h.roll,
                    "pitch": h.pitch,
                    "yaw": h.yaw,
                    "throttle": h.throttle,
                    "fire": False,
                }
            self._check_rates(r, v, now)
            obs = r.builder.build(self.fleet, v, now, sticks_in, self.teams)
            items.append((r, v, obs))
        if not items:
            return
        # every aircraft's host gets its frame first, then the replies are collected against one deadline
        sent: list[tuple[Run, Vehicle, Any, Any]] = []
        for r, v, obs in items:
            params, r.pending_params = r.pending_params, {}
            host = r.host
            try:
                pending = host.begin_step([(r.key, obs, params)], BATCH_DEADLINE_S, HARD_STEP_MS)
            except HostError:
                pending = None
            sent.append((r, v, host, pending))
        wall = self.fleet.wall()
        for r, v, host, pending in sent:
            try:
                reply: StepReply = (
                    host.finish_step(pending) if pending is not None else StepReply(dead=True, running=r.key)
                )
            except HostError:
                reply = StepReply(dead=True, running=r.key)
            res = reply.results.get(r.key)
            if res is None:
                self._release_trigger(r, v, now)
                self._safe(r, v, r.last_out_t)
            else:
                self._apply(r, v, res, now, wall)
            if not r.active or r.host is not host:
                continue  # faulted by this frame (memory, faults), or stopped meanwhile
            if host.killed_for == "memory":  # the watchdog killed it; its exit may not have shown yet
                self._host_lost(r, True, "memory")
            elif reply.dead:
                self._host_lost(r, reply.running == r.key, "exited")
            elif reply.timed_out:
                r.timeouts += 1
                if r.timeouts >= HOST_TIMEOUT_LIMIT:
                    self._host_lost(r, True, "timeouts")
            else:
                r.timeouts = 0

    def _check_rates(self, r: Run, v: Vehicle, now: float) -> None:
        if r.rates_check_at is None or now < r.rates_check_at:
            return
        entry = v.state["messages"].get("ATTITUDE")
        hz = decode.message_rate(entry["times"], now) if entry else 0.0
        if hz >= RATE_MIN_HZ:
            r.rates_ok = True
            r.rates_check_at = None
        elif not r.rates_retried:
            r.rates_retried = True
            r.rates_check_at = now + RATE_CHECK_S
            self._request_rates(v, obsmod.PROGRAM_RATES)
        else:
            r.rates_ok = False
            r.rates_check_at = None
            r.add_log(
                self.fleet.wall(), f"ATTITUDE arrives at {hz:g} Hz; the link keeps the original rate", "event"
            )

    def _write(self, r: Run, v: Vehicle, values: tuple[float, float, float, float], t: float) -> None:
        if not v.armed:
            self._idle(v, t)  # nothing to fly yet: hold the throttle down so the autopilot will arm
            return
        v.sticks = StickInput(values[0], values[1], values[2], values[3], t=t, source="program")
        v.rc_frame = None

    def _safe(self, r: Run, v: Vehicle, t: float | None) -> None:
        """Attitude centred, throttle held; ``t`` is the time the sticks count from (stick timeout)."""
        if t is None:
            return
        self._write(r, v, (0.0, 0.0, 0.0, r.last_throttle), t)

    def _apply(self, r: Run, v: Vehicle, res: dict[str, Any], now: float, wall: float) -> None:
        for line in res.get("logs") or []:
            r.add_log(wall, str(line))
        if res.get("disarm"):
            self._program_disarm(r, v, wall)
        if res.get("dropped"):
            r.add_log(wall, f"{res['dropped']} log lines dropped (at most 4 per frame)", "event")
        r.state = str(res.get("state") or "")
        r.sm = res.get("sm")
        if res.get("over"):
            r.overruns += 1
        if res.get("ok") and isinstance(res.get("out"), list) and len(res["out"]) == 5:
            roll, pitch, yaw, throttle, fire = res["out"]
            r.consecutive = 0
            r.frames += 1
            r.frame_times.append(now)
            r.last_throttle = float(throttle)
            t = r.human_in.t if r.mode == "copilot" and r.human_in is not None else now
            r.last_out_t = t
            self._write(r, v, (float(roll), float(pitch), float(yaw), float(throttle)), t)
            if fire and v.kind == "sim" and v.armed:
                try:
                    self.fleet.arena.fire(v, True, None, now)
                    r.firing = True
                except RelayError:
                    pass  # arena off, shot down or on the ground: the trigger is simply not live
            elif not fire:
                self._release_trigger(r, v, now)  # fire=False lets go now, not at the hold expiry
            return
        error = str(res.get("error") or "frame failed")
        r.faults += 1
        r.consecutive += 1
        r.last_error = error
        last_text, last_at = r.last_error_logged
        if error != last_text or now - last_at >= ERROR_LOG_PERIOD_S:
            r.last_error_logged = (error, now)
            r.add_log(wall, error, "error")
        if res.get("fatal"):
            reason = "memory" if "memory" in error else "faults"
            self._fault(r, reason, error)
            return
        self._release_trigger(r, v, now)
        self._safe(r, v, now)
        if r.consecutive >= FAULT_LIMIT:
            self._fault(r, "faults", error)

    def _program_disarm(self, r: Run, v: Vehicle, wall: float) -> None:
        """``ctx.disarm()``: honoured on the ground only (the landed state says so), never in the air."""
        if not v.armed:
            return
        if v.landed != "ground":
            if not r.disarm_refused:
                r.disarm_refused = True
                r.add_log(wall, f"disarm refused: the aircraft is not on the ground ({v.landed})", "event")
            return
        r.disarm_refused = False
        self._release_sticks(v)
        v.driver.arm(False)
        r.add_log(wall, "disarmed on the ground", "event")

    # ====================================================================== views

    def view(self, v: Vehicle) -> dict[str, Any] | None:
        r = self.runs.get(v.id)
        return None if r is None else r.view(self.fleet.clock())

    # ====================================================================== store-level operations

    def validate(self, source: Any) -> dict[str, Any]:
        return validator.validate(source).view()

    def run_tests(self, program_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """``POST /programs/{id}/test`` (06 §3.1) in a one-shot host process."""
        from .store import check_tests

        program = self.store.get(program_id)
        rev = body.get("rev")
        if body.get("source") is not None:
            source = body["source"]
            params = dict(program.get("params") or {})
        else:
            revision = self.store.revision(
                program_id, rev if isinstance(rev, int) and not isinstance(rev, bool) else None
            )
            source, params = revision["source"], dict(revision.get("params") or {})
        tests = (
            check_tests(body["tests"]) if body.get("tests") is not None else list(program.get("tests") or [])
        )
        result = validator.validate(source)
        if not result.ok:
            first = result.errors[0]
            raise err(
                "program_invalid",
                reason=f"line {first.line}: {first.message_en}",
                problems=[p.view() for p in result.errors],
            )
        merged = {**result.params, **{k: x for k, x in params.items() if k in result.params}}
        seed = body.get("seed") if isinstance(body.get("seed"), int) else 0
        try:
            out = self.test_runner(source, merged, tests, seed)
        except HostError as exc:
            raise err("program_host_unavailable", reason=str(exc)) from exc
        out.pop("id", None)
        out.pop("ok", None)
        out["program"] = program_id
        return out


__all__ = ["FAULT_LIMIT", "REASONS", "ProgramService", "Run"]
