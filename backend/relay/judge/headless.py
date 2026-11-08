"""The headless evaluator core (06 §3.4): one scenario run on an isolated, stepped ``FleetService``.

The fleet is the live one with nothing attached: ``listen=False`` (no MAVLink socket, no LAN arena socket),
``History(None)`` (nothing on disk), an injected clock that the evaluator advances in 5 ms ticks as fast as
the machine allows, and ``InProcessHost`` (the evaluator is itself an isolated process, see ``worker``).
Everything else is the code that flies in real time: the simulated autopilot with its airframe defects, the
stick path with its 0.8 s timeout, the arena's guns, bullets and hits, the ring-course judge and the program
host's engine with its per-step interrupt (``JudgeHost`` widens that wall-time limit to ``STEP_LIMIT_MS`` so
a loaded machine cannot change a result). Nothing here reads the wall clock into the simulation, so
the same scenario, seeds and program revisions give the same result.

A run: the simulators are added with their airframes and spawns, a ``judge`` session claims them, the fleet
boots for ``BOOT_S``, every aircraft is armed, then every slot's program is deployed (time 0 of the run) and
the fleet steps until the duration ends or the scorer says it is decided. The scorer sees the ground truth
(the simulator's state, not the program's observation) at 50 Hz and every fleet event. Aircraft that leave
the arena box are stopped (emergency stop) and count as out.

The recorder writes the replay trace (10 Hz, every aircraft, shots, hits and other events) in the shape
documented in ``relay.judge`` (``TRACE_VERSION``).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .. import course as coursemod
from ..authority import Actor
from ..config import Settings
from ..errors import RelayError, err
from ..history import History
from ..peers import arena as frame
from ..programs.hostclient import InProcessHost
from .scenario import Scenario, Slot

TICK_S = 0.005
BOOT_S = 2.5
ARM_WAIT_S = 2.0
SAMPLE_EVERY = 4  # ticks: 50 Hz scorer samples
TRACE_EVERY = 20  # ticks: 10 Hz trace frames
RENEW_EVERY = 100  # ticks: the judge session's lease
PROGRESS_EVERY = 200  # ticks: one simulated second
OUT_MARGIN_M = 0.5
STEP_LIMIT_MS = 50.0
TRACE_VERSION = 1
TRACE_FIELDS = ("x", "y", "z", "roll", "pitch", "yaw", "throttle", "hp", "flags")
FLAG_ARMED, FLAG_AIRBORNE, FLAG_DOWN, FLAG_CRASHED, FLAG_OUT = 1, 2, 4, 8, 16
TRACE_EVENTS = {
    "arena_hit": "hit",
    "arena_down": "down",
    "program_faulted": "program_faulted",
    "program_stopped": "program_stopped",
}


class JudgeHost(InProcessHost):
    """The program engine with a wall-time step limit wide enough that a busy machine (a step pre-empted by
    the scheduler) never changes a run; a program that loops is still interrupted, after ``STEP_LIMIT_MS``."""

    def step(self, items: list[tuple[str, dict[str, Any]]], deadline_s: float, hard_ms: float) -> Any:
        return super().step(items, deadline_s, max(hard_ms, STEP_LIMIT_MS))


class Cancelled(Exception):
    """The job was cancelled while the run was in progress."""


class _Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def wall(self) -> float:
        return 1.8e9 + self.t


@dataclass
class Truth:
    """Ground truth of one aircraft (world ENU metres, radians, compass yaw)."""

    p: tuple[float, float, float]
    v: tuple[float, float, float]
    roll: float
    pitch: float
    yaw: float
    motor: float
    armed: bool
    on_ground: bool
    crashed: bool
    down: bool
    out: bool
    hp: int | None

    @property
    def airborne(self) -> bool:
        return not self.on_ground and not self.crashed

    @property
    def eliminated(self) -> bool:
        return self.down or self.crashed or self.out


class Scorer(Protocol):
    def begin(self, run: Headless) -> None: ...

    def sample(self, run: Headless, t: float) -> None: ...

    def event(self, run: Headless, t: float, event: dict[str, Any]) -> None: ...

    def finished(self, run: Headless, t: float) -> bool: ...

    def result(self, run: Headless) -> dict[str, Any]: ...


def _r(x: float, digits: int = 3) -> float:
    return round(x, digits) + 0.0


class Headless:
    """One run of ``scenario`` with ``slots``; ``run(scorer)`` returns ``{result, trace, sim_s, wall_s}``."""

    def __init__(
        self,
        scenario: Scenario,
        slots: list[Slot],
        *,
        tick_s: float = TICK_S,
        cancelled: Callable[[], bool] | None = None,
        progress: Callable[[float], None] | None = None,
    ) -> None:
        from ..fleet import FleetService  # the fleet imports the program service, which imports nothing here

        self.scenario = scenario
        self.slots = slots
        self.tick_s = tick_s
        self.cancelled = cancelled or (lambda: False)
        self.progress = progress or (lambda _f: None)
        self.clock = _Clock()
        settings = Settings()
        settings.sim_hover_assist = bool(scenario.hover_assist)
        settings.sim_turbulence = float(scenario.turbulence)
        settings.sim_airframe_default = "ideal"
        settings.peer_enabled = bool(scenario.guns)
        settings.arena_auto_respawn = False
        settings.agent_flight_level = "sticks"
        self.svc = FleetService(
            settings,
            None,
            listen=False,
            clock=self.clock,
            wall=self.clock.wall,
            history=History(None),
            operator_token="",
        )
        self.svc.settings.agent_flight_level = "sticks"
        self.svc.airframe_seed_source = lambda: int(scenario.seed) % 1_000_000
        self.svc.programs.host_factory = JudgeHost
        self.svc._next["snapshot"] = math.inf  # nobody reads the snapshot of a headless fleet
        self.svc.arena.instance = 1
        self.svc.arena.fly_budget_s = math.inf
        self._events: list[dict[str, Any]] = []
        self.svc.events.add_listener(self._events.append)
        self.t0: float | None = None
        self.out: set[str] = set()
        self.by_vid: dict[str, Slot] = {}
        self.actor: Actor | None = None
        self.session: Any = None
        # trace
        self.times: list[float] = []
        self.frames: dict[str, list[list[Any]]] = {}
        self.states: dict[str, list[list[Any]]] = {}
        self.shots: list[dict[str, Any]] = []
        self.trace_events: list[dict[str, Any]] = []
        self._seen_bullets: set[int] = set()
        self._crash_logged: set[str] = set()
        self.course: dict[str, Any] | None = None
        self.course_run: coursemod.CourseRun | None = None

    # ------------------------------------------------------------------ set-up

    def _tick(self, n: int = 1) -> None:
        for _ in range(n):
            self.clock.t += self.tick_s
            self.svc.step(self.clock.t)

    def _advance(self, seconds: float) -> None:
        ticks = round(seconds / self.tick_s)
        for i in range(ticks):
            if i % RENEW_EVERY == 0 and self.session is not None:
                self.svc.sessions.renew(self.session)
            self._tick()

    def setup(self) -> None:
        svc = self.svc
        for slot in self.slots:
            (vid,) = svc.add_sims(1, hover_assist=self.scenario.hover_assist, airframe=slot.airframe)
            v = svc.vehicles[vid]
            v.spawn = (slot.spawn.x, slot.spawn.y)
            v.frame_offset = (slot.spawn.x, slot.spawn.y, 0.0)
            v.safety_origin = (slot.spawn.x, slot.spawn.y, 0.0)
            v.driver.sim.quad.s.yaw = math.radians(slot.spawn.yaw_deg)
            slot.vid = vid
            self.by_vid[vid] = slot
            self.frames[vid] = []
            self.states[vid] = []
        opened = svc.open_session("judge")
        self.session = svc.sessions.get(opened["session_id"])
        self.actor = Actor.session(self.session.session_id, self.session.name)
        for slot in self.slots:
            svc.claim(slot.vid, self.actor)
        self._advance(BOOT_S)
        for slot in self.slots:
            svc.arm(slot.vid, self.actor, True)
        waited = 0.0
        while not all(svc.vehicles[s.vid].armed for s in self.slots):
            if waited >= ARM_WAIT_S:
                raise err("judge_setup_failed", reason="an aircraft did not arm")
            self._advance(0.05)
            waited += 0.05
        self.svc.sessions.renew(self.session)
        for index, slot in enumerate(self.slots):
            sensors = slot.sensors if slot.sensors is not None else self.scenario.sensors
            svc.programs.teams[slot.vid] = slot.team or slot.vid
            try:
                svc.programs.deploy_source(
                    slot.vid,
                    self.actor,
                    {"id": slot.program, "name": slot.name},
                    slot.rev,
                    slot.source,
                    dict(slot.params),
                    mode="pilot",
                    sensors=sensors,
                    arena=self.scenario.arena,
                    seed=slot.seed,
                )
            except RelayError as exc:
                exc.detail.setdefault("slot", index)
                exc.detail.setdefault("program", slot.program)
                raise
        if self.scenario.course is not None:  # the fleet's ring judge (03 §8), fed with the ground truth
            player = next((s for s in self.slots if s.role == "player"), self.slots[0])
            origin = (player.spawn.x, player.spawn.y, 0.0, 0.0)
            self.course_run = coursemod.CourseRun.begin(svc.course(self.scenario.course), origin, None)
            self.course = {"id": self.scenario.course, "rings": list(self.course_run.rings)}
        self.t0 = self.clock.t
        self._events.clear()

    # ------------------------------------------------------------------ queries

    @property
    def t(self) -> float:
        return 0.0 if self.t0 is None else round(self.clock.t - self.t0, 6)

    def slot(self, role: str) -> Slot:
        return next(s for s in self.slots if s.role == role)

    def truth(self, slot: Slot) -> Truth:
        v = self.svc.vehicles[slot.vid]
        sim = v.driver.sim
        s = sim.quad.s
        ox, oy, oz = v.frame_offset
        arena = self.svc.arena
        gun = arena.guns.get(slot.vid) if arena.enabled else None
        return Truth(
            p=(s.p[0] + ox, s.p[1] + oy, s.p[2] + oz),
            v=(s.v[0], s.v[1], s.v[2]),
            roll=s.roll,
            pitch=s.pitch,
            yaw=s.yaw,
            motor=s.motor,
            armed=bool(sim.armed),
            on_ground=bool(s.on_ground),
            crashed=bool(s.crashed),
            down=gun is not None and gun.down,
            out=slot.vid in self.out,
            hp=None if gun is None else gun.hp,
        )

    def program_state(self, slot: Slot) -> str:
        r = self.svc.programs.runs.get(slot.vid)
        return r.state if r is not None else ""

    # ------------------------------------------------------------------ the run

    def run(self, scorer: Scorer) -> dict[str, Any]:
        started = time.perf_counter()
        if self.t0 is None:
            self.setup()
        scorer.begin(self)
        self._record()
        ticks = round(self.scenario.duration_s / self.tick_s)
        n = 0
        while n < ticks:
            if n % RENEW_EVERY == 0:
                self.svc.sessions.renew(self.session)
            self._tick()
            n += 1
            if self._events:
                self._drain(scorer)
            if n % SAMPLE_EVERY == 0:
                self._arena_bounds()
                scorer.sample(self, self.t)
            if n % TRACE_EVERY == 0:
                self._record()
            if n % PROGRESS_EVERY == 0:
                if self.cancelled():
                    raise Cancelled()
                self.progress(n / ticks)
                if scorer.finished(self, self.t):
                    break
        if n % TRACE_EVERY:
            self._record()
        result = scorer.result(self)
        wall_s = time.perf_counter() - started
        trace = self.trace(result)
        self.close()
        return {"result": result, "trace": trace, "sim_s": round(self.t, 3), "wall_s": round(wall_s, 3)}

    def close(self) -> None:
        self.svc.shutdown()

    def _drain(self, scorer: Scorer) -> None:
        events, self._events[:] = list(self._events), []
        t = self.t
        for event in events:
            kind = TRACE_EVENTS.get(event["kind"])
            if kind is not None:
                self.trace_events.append(self._trace_event(t, kind, event))
            scorer.event(self, t, event)

    def _trace_event(self, t: float, kind: str, event: dict[str, Any]) -> dict[str, Any]:
        data = event.get("data") or {}
        out: dict[str, Any] = {"t": _r(t), "kind": kind, "vehicle": event.get("vehicle")}
        if kind in ("hit", "down"):
            out.update(
                shooter=data.get("shooter"),
                damage=data.get("damage"),
                hp=data.get("hp"),
                point=data.get("point"),
            )
        elif kind in ("program_faulted", "program_stopped"):
            out.update(reason=data.get("reason"))
        return out

    def _arena_bounds(self) -> None:
        box = self.scenario.arena
        if box is None:
            return
        for slot in self.slots:
            if slot.vid in self.out:
                continue
            tr = self.truth(slot)
            if tr.crashed or tr.down:
                continue
            outside = math.hypot(tr.p[0], tr.p[1]) > box["radius"] + OUT_MARGIN_M or tr.p[2] > box[
                "ceiling"
            ] + (OUT_MARGIN_M)
            if outside:
                self.out.add(slot.vid)
                self.trace_events.append({"t": _r(self.t), "kind": "out", "vehicle": slot.vid})
                try:
                    self.svc.stop(slot.vid, Actor.operator())
                except RelayError:
                    pass

    def mark(self, kind: str, vehicle: str | None = None, **data: Any) -> None:
        """A scorer's own replay event (a corner reached, a liftoff)."""
        self.trace_events.append({"t": _r(self.t), "kind": kind, "vehicle": vehicle, **data})

    # ------------------------------------------------------------------ trace

    def _record(self) -> None:
        self.times.append(_r(self.t, 2))
        for slot in self.slots:
            tr = self.truth(slot)
            flags = (
                (FLAG_ARMED if tr.armed else 0)
                | (FLAG_AIRBORNE if tr.airborne else 0)
                | (FLAG_DOWN if tr.down else 0)
                | (FLAG_CRASHED if tr.crashed else 0)
                | (FLAG_OUT if tr.out else 0)
            )
            if tr.crashed and slot.vid not in self._crash_logged:
                self._crash_logged.add(slot.vid)
                self.trace_events.append({"t": _r(self.t), "kind": "crash", "vehicle": slot.vid})
            self.frames[slot.vid].append(
                [
                    _r(tr.p[0]),
                    _r(tr.p[1]),
                    _r(tr.p[2]),
                    _r(tr.roll),
                    _r(tr.pitch),
                    _r(tr.yaw % (2.0 * math.pi)),
                    _r(tr.motor),
                    tr.hp,
                    flags,
                ]
            )
            state = self.program_state(slot)
            history = self.states[slot.vid]
            if not history or history[-1][1] != state:
                history.append([_r(self.t, 2), state])
        self._record_shots()

    def _record_shots(self) -> None:
        arena = self.svc.arena
        if not arena.enabled or arena.seat is None or self.t0 is None:
            return
        for b in arena.bullets:
            if b.owner != arena.instance or b.bullet in self._seen_bullets:
                continue
            self._seen_bullets.add(b.bullet)
            p = frame.from_arena(arena.seat, b.p0)
            v = frame.vec_from_arena(arena.seat, b.v0)
            self.shots.append(
                {
                    "id": b.bullet,
                    "shooter": f"sim-{b.shooter}",
                    "t0": _r(b.t0 - self.t0, 4),
                    "p": [_r(x, 4) for x in p],
                    "v": [_r(x, 4) for x in v],
                }
            )

    def trace(self, result: dict[str, Any]) -> dict[str, Any]:
        sc = self.scenario
        return {
            "version": TRACE_VERSION,
            "scenario": sc.id,
            "seed": sc.seed,
            "hz": round(1.0 / (self.tick_s * TRACE_EVERY), 3),
            "duration_s": _r(self.t, 2),
            "fields": list(TRACE_FIELDS),
            "arena": dict(sc.arena) if sc.arena else None,
            "course": self.course,
            "markers": [dict(m) for m in sc.markers],
            "t": self.times,
            "aircraft": [
                {
                    "id": slot.vid,
                    "label": self.svc.vehicles[slot.vid].label,
                    "program": slot.program,
                    "rev": slot.rev,
                    "name": slot.name,
                    "role": slot.role,
                    "team": slot.team or slot.vid,
                    "spawn": slot.spawn.view(),
                    "frames": self.frames[slot.vid],
                    "states": self.states[slot.vid],
                }
                for slot in self.slots
            ],
            "shots": sorted(self.shots, key=lambda s: (s["t0"], s["id"])),
            "events": self.trace_events,
            "result": result,
        }


__all__ = ["TRACE_FIELDS", "TRACE_VERSION", "Cancelled", "Headless", "Scorer", "Truth"]
