"""Deployment of onboard programs on the fleet thread (06 §2.6).

``ProgramService`` belongs to ``FleetService`` (``fleet.programs``). It deploys a stored program revision
to one aircraft, runs every running program once per stick tick (50 Hz, ``tick``) in one batch on the
program host, and writes the outputs through the fleet's own stick path as the ``program`` source: the
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
* Frames: a raising or invalid frame sends safe sticks (attitude centred, throttle held) and counts a fault;
  25 consecutive faults fault the program. A batch that misses its 15 ms deadline sends safe sticks with the
  time of the last good frame (so a hung host still meets the 0.8 s stick timeout); three in a row, or a
  host that exits, restart the host in the background, fault the program that was running and reload the
  others (their ``ctx.mem`` starts empty).
* Real aircraft: only after the operator allows programs on that aircraft (confirm word ``PROGRAM``,
  reset whenever the link drops) and with a passing preflight; ``fire`` is ignored.
* While a program flies an aircraft Relay asks for ATTITUDE 50 Hz, LOCAL_POSITION_NED 20 Hz, VFR_HUD 20 Hz and
  SCALED_IMU 50 Hz, and checks after 3 s that ATTITUDE arrives at 40 Hz or more (``rates_ok``).

Seams for later packages: ``ProgramService(fleet, host_factory=InProcessHost)`` runs programs in process
(headless evaluators); ``teams`` maps aircraft or contact ids to team names for ``obs.contacts``;
``deploy_source(...)`` deploys source that is not in the store (scenarios); ``runs`` holds the ``Run`` of
each aircraft; ``validate`` / ``run_tests`` wrap the validator and the one-shot test host.
"""

from __future__ import annotations

import logging
import secrets
import threading
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .. import authority as auth
from .. import preflight
from ..authority import Actor
from ..config import STICK_TIMEOUT_S
from ..errors import RelayError, err
from ..mav import decode
from ..model import StickInput
from . import obs as obsmod
from . import validator
from .hostclient import HostClient, HostError, StepReply, run_tests_isolated
from .store import ProgramStore, check_params, coerce_param

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
        self.host: Any = None
        self.runs: dict[str, Run] = {}
        self.teams: dict[str, str] = {}
        self._seq = 0
        self._host_lock = threading.Lock()
        self._timeouts = 0
        self._restarts: deque[float] = deque(maxlen=16)
        self._restarting = False

    # ====================================================================== host

    def _ensure_host(self) -> Any:
        """Start the host if needed (any thread; the fleet thread only uses a started host)."""
        with self._host_lock:
            if self.host is not None and self.host.alive:
                return self.host
            host = self.host_factory()
            try:
                host.start()
            except HostError as exc:
                raise err("program_host_unavailable", reason=str(exc)) from exc
            self.host = host
            self._timeouts = 0
            return host

    def close(self) -> None:
        with self._host_lock:
            if self.host is not None:
                self.host.close()
                self.host = None

    def _restart_host(self, culprit: str | None, reason: str) -> None:
        """Kill the host, fault ``culprit`` and reload the other running programs in a new one."""
        now = self.fleet.clock()
        wall = self.fleet.wall()
        old = self.host
        self.host = None
        self._timeouts = 0
        if old is not None:
            old.kill()
        self._restarts.append(now)
        recent = [t for t in self._restarts if now - t <= RESTART_WINDOW_S]
        give_up = len(recent) > RESTART_LIMIT
        for r in list(self.runs.values()):
            if not r.active:
                continue
            if give_up or r.key == culprit:
                self._fault(
                    r, "host", f"{RESTART_REASONS.get(reason, (reason,))[0]}; the program host was restarted"
                )
        en, zh = RESTART_REASONS.get(reason, (reason, reason))
        self.fleet.events.emit(
            "program_host_restarted",
            data={"reason": reason, "reason_en": en, "reason_zh": zh, "culprit": culprit, "gave_up": give_up},
        )
        log.warning("program host restarted (%s), culprit %s", reason, culprit)
        if give_up or not any(r.active for r in self.runs.values()):
            return
        if self.fleet._thread is None:  # stepped by tests: restart inline
            self._start_and_reload(wall)
            return
        if self._restarting:
            return
        self._restarting = True

        def background() -> None:
            try:
                host = self.host_factory()
                host.start()
            except HostError as exc:
                log.error("program host restart failed: %s", exc)
                host = None
            self.fleet.post(lambda: self._adopt(host))

        threading.Thread(target=background, name="program-host-restart", daemon=True).start()

    def _adopt(self, host: Any) -> None:
        self._restarting = False
        if host is None:
            for r in list(self.runs.values()):
                if r.active:
                    self._fault(r, "host", "the program host could not be restarted")
            return
        with self._host_lock:
            if self.host is not None:
                self.host.close()
            self.host = host
        self._reload(self.fleet.wall())

    def _start_and_reload(self, wall: float) -> None:
        try:
            self._ensure_host()
        except RelayError as exc:
            for r in list(self.runs.values()):
                if r.active:
                    self._fault(r, "host", exc.message_en)
            return
        self._reload(wall)

    def _reload(self, wall: float) -> None:
        for r in list(self.runs.values()):
            if not r.active:
                continue
            try:
                reply = self.host.load(r.key, r.source, r.params, r.seed)
            except HostError as exc:
                self._fault(r, "host", f"reload failed: {exc}")
                continue
            if not reply.get("ok"):
                self._fault(r, "host", f"reload failed: {reply.get('error')}")
                continue
            r.add_log(wall, "program reloaded after a host restart; ctx.mem starts empty", "event")

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
        program, revision = self._resolve(program_id, rev)
        return self.deploy_source(
            vid,
            actor,
            program,
            revision["rev"],
            revision["source"],
            {**(revision.get("params") or {}), **(body.get("params") or {})},
            mode=body.get("mode") or "pilot",
            sensors=body.get("sensors"),
            arena=body.get("arena"),
            seed=body.get("seed"),
        )

    def deploy_source(
        self,
        vid: str,
        actor: Actor,
        program: dict[str, Any],
        rev: int,
        source: str,
        params: dict[str, Any],
        *,
        mode: str = "pilot",
        sensors: Any = None,
        arena: Any = None,
        seed: Any = None,
    ) -> dict[str, Any]:
        """Deploy source directly (scenarios and evaluators); ``program`` needs ``id`` and ``name``."""
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
        merged = self._params(params, result.params, result.param_ranges)
        try:
            spec = obsmod.Sensors.parse(sensors)
            arena_box = obsmod.parse_arena(arena)
        except ValueError as exc:
            raise err("invalid_request", reason=str(exc)) from exc
        if seed is None:
            seed = secrets.randbelow(2**31)
        elif isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**63:
            raise err("invalid_request", reason="seed must be a non-negative integer")
        self._ensure_host()

        def run() -> dict[str, Any]:
            v = self.fleet._vehicle(vid)
            self._check_actor(v, actor)
            self.fleet._require_link(v)
            if v.kind == "real":
                self._check_real(v)
            if v.kind == "sim" and v.state["crashed"]:
                raise err("crashed")
            if self.host is None or not self.host.alive:
                raise err("program_host_unavailable", reason="restarting")
            now = self.fleet.clock()
            wall = self.fleet.wall()
            old = self.runs.get(vid)
            if old is not None and old.active:
                self._stop(old, "replaced", emit=True)
            self._seq += 1
            key = f"{vid}#{self._seq}"
            try:
                reply = self.host.load(key, source, merged, int(seed))
            except HostError as exc:
                raise err("program_host_unavailable", reason=str(exc)) from exc
            if not reply.get("ok"):
                raise err(
                    "program_load_failed",
                    reason=str(reply.get("error") or "unknown"),
                    problems=reply.get("problems") or [],
                )
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
            if self.host is None or not self.host.alive:
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
            try:
                ok = self.host is not None and self.host.set_params(r.key, values)
            except HostError as exc:
                raise err("program_host_unavailable", reason=str(exc)) from exc
            if not ok:
                raise err("program_host_unavailable", reason="the program is not loaded")
            r.params.update(values)
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

    def _release_sticks(self, v: Vehicle | None) -> None:
        if v is not None and v.sticks is not None and v.sticks.source == "program":
            v.sticks = None
            v.driver.stop_sticks()

    def _vehicle_of(self, r: Run) -> Vehicle | None:
        return self.fleet.vehicles.get(r.vid)

    def _end(self, r: Run, status: str, reason: str) -> Vehicle | None:
        v = self._vehicle_of(r)
        r.status = status
        r.reason = reason
        self._release_sticks(v)
        if self.host is not None:
            try:
                self.host.unload(r.key)
            except HostError:
                pass
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
        runs = [r for r in self.runs.values() if r.status == "running"]
        if not runs:
            return
        items: list[tuple[str, dict[str, Any], Run, Vehicle]] = []
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
            items.append((r.key, obs, r, v))
        if not items:
            return
        if self.host is None or not self.host.alive:
            if not self._restarting:
                self._restart_host(None, "exited")
            return
        try:
            reply: StepReply = self.host.step(
                [(k, o) for k, o, _, _ in items], BATCH_DEADLINE_S, HARD_STEP_MS
            )
        except HostError:
            reply = StepReply(dead=True, running=items[0][0])
        wall = self.fleet.wall()
        for key, _obs, r, v in items:
            res = reply.results.get(key)
            if res is None:
                self._safe(r, v, r.last_out_t)
            else:
                self._apply(r, v, res, now, wall)
        if reply.dead:
            self._restart_host(reply.running, "exited")
        elif reply.timed_out:
            self._timeouts += 1
            if self._timeouts >= HOST_TIMEOUT_LIMIT:
                self._restart_host(reply.running, "timeouts")
        else:
            self._timeouts = 0

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
            self._release_sticks(v)  # nothing to fly, and a raised throttle would block arming
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
                except RelayError:
                    pass  # arena off, shot down or on the ground: the trigger is simply not live
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
        self._safe(r, v, now)
        if r.consecutive >= FAULT_LIMIT:
            self._fault(r, "faults", error)

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
