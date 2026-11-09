"""Dogfight scenario (07 §3, WP-B): one call puts 2-8 simulators into a timed free-for-all or team fight.

``start`` checks the request (simulators only: a real aircraft id is refused with 400), picks free simulators
(disarmed, not possessed, not claimed by a live session, no program running) and adds the rest, then on the
fleet thread places them on a ring near the arena edge facing the centre, fits the airframe preset (a seeded
airframe each) and the hover-assist choice, and opens the arena: on the LAN the terminal joins the current
match or starts one for everybody; off the LAN the arena runs in local practice mode (no socket). A
``dogfight`` agent session claims the aircraft, so the timeline shows who holds them.

Everything after that runs in ``poll`` (a worker thread in the app, called directly by tests): each aircraft
boots, is armed (the arm switch, as a pilot flips it), gets its role's program deployed and takes off on its
own sticks. Programs only ever fly with sticks (06 §5); nothing here commands a position, a heading or a
target. While the scenario runs, an aircraft that leaves the arena (radius + 0.5 m, ceiling + 0.5 m) is
brought down; a shot-down or crashed aircraft comes back at its spawn point after ``RESPAWN_S`` when
``respawn`` is on; a person who possesses an aircraft flies it (the program pauses) and gets it back to the
program when they release it. An emergency stop (one aircraft, or stop-all) is final for the scenario: the
aircraft it stopped never respawns, re-arms or gets its program back, and when it stops every aircraft the
scenario ends (reason ``emergency_stop``, no landing skill over the stop). At the end (time up or ``stop``)
the programs stop, airborne aircraft land as a safety action, the session closes and the arena leaves
practice mode; the match board stays.

Roles: gunners fire (``hunter_medium`` by default), rammers never do (``rammer``; the arena ignores their
trigger). Teams: ``ffa`` (every aircraft on its own) or 2 / 3 teams around the ring; a program that declares a
``team`` parameter gets its team there, and ``obs.contacts[*].team`` carries every contact's.
"""

from __future__ import annotations

import logging
import math
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import flight_level
from .authority import Actor
from .config import SIM_LIMIT
from .errors import RelayError, err
from .judge.scenario import entrant_seed, ring_spawns
from .programs import validator

if TYPE_CHECKING:
    from .fleet import FleetService, Vehicle

log = logging.getLogger("relay.scenarios")

DEFAULT_PROGRAMS = {"gunner": "hunter_medium", "rammer": "rammer"}
ROLES = ("gunner", "rammer", "pilot")  # 08 §5: a pilot flies its own program and never fires
POSITIONS = ("none", "uwb", "gps")
ENTRANT_TEXT = 16
SENSORS = {"baro": True, "position": "none", "contacts": True}
SESSION_NAME = "dogfight"
SPAWN_MARGIN_M = 3.0  # spawns 3 m inside the edge: room to climb and turn before the boundary
BOOT_S = 2.5  # a new simulator's link settles before it is armed
ARM_RETRY_S = 2.0
ARM_TRIES = 3
RESPAWN_S = 5.0
OUT_REASON = "out of the arena"  # the crash reason of an aircraft that leaves the arena (fleet_world)
OUT_MARGIN_M = 0.5
LAND_TIMEOUT_S = 20.0
GROUND_M = 0.15  # below this height and GROUND_V speed a simulator at the end counts as down
GROUND_V = 0.5
FORCE_RETRY_S = 1.0
POLL_S = 0.1


@dataclass
class Craft:
    vid: str
    role: str  # gunner | rammer | pilot
    program: str
    team: str
    spawn: tuple[float, float, float]  # x, y, compass yaw in degrees
    seed: int
    index: int
    position: str = "none"  # 08 §5: the program's position source (none | uwb | gps)
    params: dict[str, Any] = field(default_factory=dict)
    label: str = ""
    state: str = "booting"  # booting | arming | deploying | flying | human | down | stopped | failed
    since: float = 0.0  # fleet clock of the last state change
    tries: int = 0
    error: str | None = None

    def view(self) -> dict[str, Any]:
        return {
            "vid": self.vid,
            "role": self.role,
            "program": self.program,
            "team": self.team,
            "position": self.position,
            "label": self.label,
            "state": self.state,
        }


@dataclass
class Config:
    aircraft: int
    teams: str | int
    roles: dict[str, int]
    programs: dict[str, str]
    arena: dict[str, float]
    airframe: str
    hover_assist: bool
    duration_s: float
    respawn: bool
    seed: int
    lan: bool = False
    entrants: list[dict[str, Any]] | None = None  # 08 §5: one entry per aircraft, in spawn order

    def view(self) -> dict[str, Any]:
        return {
            "aircraft": self.aircraft,
            "teams": self.teams,
            "roles": dict(self.roles),
            "programs": dict(self.programs),
            "arena": dict(self.arena),
            "airframe": self.airframe,
            "hover_assist": self.hover_assist,
            "duration_s": self.duration_s,
            "respawn": self.respawn,
            "seed": self.seed,
            "lan": self.lan,
            "entrants": None if self.entrants is None else [dict(e) for e in self.entrants],
        }


@dataclass
class _Run:
    config: Config
    crafts: list[Craft]
    actor: Actor
    session: Any
    by: str
    started_at: float
    ends_at: float
    match_id: int | None = None
    started_match: bool = False
    phase: str = "booting"
    ending_at: float | None = None
    reason: str | None = None
    error: str | None = None
    released: set[str] = field(default_factory=set)
    forced: dict[str, float] = field(default_factory=dict)


class Dogfights:
    """The fleet's one dogfight at a time (``FleetService.dogfights``)."""

    def __init__(self, fleet: FleetService) -> None:
        self.fleet = fleet
        self.run: _Run | None = None
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._closed = False
        self._estopped: set[str] = set()  # written on the fleet thread without the lock, read by poll

    # ------------------------------------------------------------------ requests (API threads)

    def start(self, body: dict[str, Any], actor: Actor) -> dict[str, Any]:
        with self._lock:
            if self.run is not None and self.run.phase not in ("done",):
                raise err("dogfight_running")
            config, wanted = self._parse(body, actor)
            run = self._setup(config, wanted, actor)
            self.run = run
        self._kick()
        return self.view()

    def stop(self, actor: Actor | None = None, reason: str = "stopped") -> dict[str, Any]:
        with self._lock:
            run = self.run
            if run is None or run.phase in ("ending", "done"):
                raise err("dogfight_not_running")
            self._begin_end(run, reason)
        self._kick()
        return self.view()

    def on_emergency_stop(self, vid: str) -> None:
        """The fleet applied an emergency stop to ``vid`` (fleet thread). Never takes the lock: ``poll`` holds
        it while it waits for the fleet thread."""
        run = self.run
        if run is not None and run.phase != "done" and any(c.vid == vid for c in run.crafts):
            self._estopped.add(vid)
            self._wake.set()

    def view(self) -> dict[str, Any]:
        with self._lock:
            run = self.run
            arena = self.fleet.arena
            now = self.fleet.clock()
            if run is None:
                return {
                    "running": False,
                    "phase": "idle",
                    "started_at": None,
                    "ends_at": None,
                    "config": None,
                    "aircraft": [],
                    "match": None,
                    "by": None,
                    "reason": None,
                    "error": None,
                }
            wall = self.fleet.wall()
            return {
                "running": run.phase not in ("ending", "done"),
                "phase": run.phase,
                "started_at": round(wall - (now - run.started_at), 3),
                "ends_at": round(wall - (now - run.ends_at), 3),
                "config": run.config.view(),
                "aircraft": [c.view() for c in run.crafts],
                "match": arena.board.match_view(now),
                "by": run.by,
                "reason": run.reason,
                "error": run.error,
            }

    def close(self) -> None:
        self._closed = True
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

    # ------------------------------------------------------------------ request parsing

    def _parse(self, body: dict[str, Any], actor: Actor) -> tuple[Config, list[str]]:
        fleet = self.fleet
        entrants = self._entrants(body.get("entrants"))
        n = len(entrants) if entrants is not None else int(body.get("aircraft") or 6)
        wanted = list(body.get("vehicles") or [])
        for vid in wanted:
            v = fleet.vehicles.get(vid)
            if (v is not None and v.kind != "sim") or not str(vid).startswith("sim-"):
                raise err("dogfight_real_aircraft", vehicle=str(vid))
            if v is None:
                raise err("not_found", resource=str(vid))
            if getattr(v.driver.sim, "engine", "relay") == "sitl":  # 11 §2: dogfights fly fast simulators
                raise err("twin_unsupported", operation="dogfight", operation_zh="狗斗")
        if n > SIM_LIMIT:
            raise err("dogfight_no_aircraft", needed=n, free=SIM_LIMIT, limit=SIM_LIMIT)
        roles_raw = body.get("roles")
        if entrants is not None:
            roles = {r: sum(1 for e in entrants if e["role"] == r) for r in ROLES}
        elif roles_raw:
            roles = {"gunner": int(roles_raw.get("gunner", 0)), "rammer": int(roles_raw.get("rammer", 0))}
            if roles["gunner"] + roles["rammer"] != n:
                raise err("invalid_request", reason="roles must add up to aircraft")
        else:
            gunners = max(1, min(n - 1, round(n * 2 / 3)))
            roles = {"gunner": gunners, "rammer": n - gunners}
        programs = dict(DEFAULT_PROGRAMS)
        for role, pid in (body.get("programs") or {}).items():
            if pid:
                programs[role] = str(pid)
        for role in ("gunner", "rammer"):
            if roles.get(role):
                fleet.programs.store.get(programs[role])  # program_not_found for an unknown id
        for e in entrants or ():
            e["program"] = e["program"] or programs[e["role"]]
            fleet.programs.store.get(e["program"])
        box = body.get("arena") or {}
        arena = {
            "radius": float(box.get("radius", 8.0)),
            "floor": 0.0,
            "ceiling": float(box.get("ceiling", 4.0)),
        }
        hover_assist = bool(body.get("hover_assist", False))
        flight_level.check_sim_assist(fleet.settings, actor, hover_assist)  # 06 §5: 403 sticks_only
        seed = body.get("seed")
        if seed is None:
            seed = secrets.randbelow(2**31)
        teams = "custom" if entrants is not None else body.get("teams", "ffa")
        if teams not in ("ffa", 2, 3, "custom"):
            raise err("invalid_request", reason="teams must be ffa, 2 or 3")
        config = Config(
            aircraft=n,
            teams=teams,
            roles=roles,
            programs=programs,
            arena=arena,
            airframe=str(body.get("airframe") or "typical"),
            hover_assist=hover_assist,
            duration_s=float(body.get("duration_s") or 180.0),
            respawn=bool(body.get("respawn", True)),
            seed=int(seed),
            entrants=entrants,
        )
        return config, wanted

    @staticmethod
    def _entrants(raw: Any) -> list[dict[str, Any]] | None:
        """08 §5: the per-aircraft entries of a custom fight, checked and filled in, or None without them."""
        if raw is None:
            return None
        if not isinstance(raw, list) or not 2 <= len(raw) <= SIM_LIMIT:
            raise err("invalid_request", reason=f"entrants must be a list of 2 to {SIM_LIMIT} entries")
        out = []
        for i, e in enumerate(raw):
            if not isinstance(e, dict):
                raise err("invalid_request", reason=f"entrants[{i}] must be an object")
            role = e.get("role", "gunner")
            if role not in ROLES:
                raise err("invalid_request", reason=f"entrants[{i}].role must be gunner, rammer or pilot")
            program = e.get("program")
            if role == "pilot" and not program:
                raise err("invalid_request", reason=f"entrants[{i}].program is required for a pilot")
            position = e.get("position", "none")
            if position not in POSITIONS:
                raise err("invalid_request", reason=f"entrants[{i}].position must be none, uwb or gps")
            params = e.get("params") or {}
            if not isinstance(params, dict):
                raise err("invalid_request", reason=f"entrants[{i}].params must be an object")
            team = str(e.get("team") or "").strip()[:ENTRANT_TEXT]
            label = str(e.get("label") or "").strip()[:ENTRANT_TEXT]
            out.append(
                {
                    "role": role,
                    "program": str(program) if program else "",
                    "team": team,
                    "position": position,
                    "params": dict(params),
                    "label": label,
                }
            )
        return out

    # ------------------------------------------------------------------ set-up

    def _free(self, v: Vehicle) -> bool:
        fleet = self.fleet
        return (
            v.kind == "sim"
            and getattr(v.driver.sim, "engine", "relay") != "sitl"  # firmware twins stay out (11 §2)
            and not v.armed
            and v.auth.possession is None
            and not fleet._claim_alive(v)
            and (v.program is None or not v.program.active)
        )

    def _setup(self, config: Config, wanted: list[str], actor: Actor) -> _Run:
        fleet = self.fleet
        n = config.aircraft

        def pick() -> list[str]:
            chosen = [vid for vid in wanted if self._free(fleet.vehicles[vid])][:n]
            for vid in sorted(fleet.vehicles, key=_sim_order):
                if len(chosen) >= n:
                    break
                if vid not in chosen and self._free(fleet.vehicles[vid]):
                    chosen.append(vid)
            sims = sum(1 for v in fleet.vehicles.values() if v.kind == "sim")
            if n - len(chosen) > SIM_LIMIT - sims:
                raise err(
                    "dogfight_no_aircraft", needed=n, free=len(chosen) + SIM_LIMIT - sims, limit=SIM_LIMIT
                )
            return chosen

        chosen = fleet.call(pick)
        if len(chosen) < n:
            chosen += fleet.add_sims(n - len(chosen), hover_assist=config.hover_assist)
        chosen = sorted(chosen, key=_sim_order)
        opened = fleet.open_session(SESSION_NAME)
        session = fleet.sessions.get(opened["session_id"])
        me = Actor.session(session.session_id, session.name)
        radius = max(2.0, config.arena["radius"] - SPAWN_MARGIN_M)
        spawns = ring_spawns(n, config.seed, radius)
        roles = ["gunner"] * config.roles["gunner"] + ["rammer"] * config.roles["rammer"]
        crafts = []
        for i, vid in enumerate(chosen):
            sp = spawns[i]
            if config.entrants is not None:
                e = config.entrants[i]
                crafts.append(
                    Craft(
                        vid=vid,
                        role=e["role"],
                        program=e["program"],
                        team=e["team"] or vid,
                        spawn=(sp.x, sp.y, sp.yaw_deg),
                        seed=entrant_seed(config.seed, i),
                        index=i,
                        position=e["position"],
                        params=dict(e["params"]),
                        label=e["label"],
                    )
                )
                continue
            role = roles[i]
            team = vid if config.teams == "ffa" else f"team-{i % int(config.teams) + 1}"
            crafts.append(
                Craft(
                    vid=vid,
                    role=role,
                    program=config.programs[role],
                    team=team,
                    spawn=(sp.x, sp.y, sp.yaw_deg),
                    seed=entrant_seed(config.seed, i),
                    index=i,
                )
            )
        now = fleet.clock()

        def place() -> tuple[bool, int | None, bool]:
            arena = fleet.arena
            for c in crafts:
                v = fleet.vehicles[c.vid]
                self._place(v, c)
                v.driver.sim.set_hover_assist(config.hover_assist, fleet.clock(), enter_hold=False)
                v.driver.sim.set_airframe(
                    fleet._resolve_airframe({"preset": config.airframe, "seed": c.seed % 1_000_000})
                )
                fleet.programs.teams[c.vid] = c.team
                arena.respawn_hold.add(c.vid)
                if c.role in ("rammer", "pilot"):
                    arena.hold_fire.add(c.vid)
                else:
                    arena.hold_fire.discard(c.vid)
                c.since = fleet.clock()
            lan = arena.on_lan
            if not arena.enabled:
                arena.begin_practice()
            board = arena.board
            m = board.match
            if lan and m is not None and m.running:
                return lan, m.id, False  # on the LAN: join the match that runs
            m = board.start(config.duration_s, fleet.clock(), broadcast=lan)
            return lan, m.id, True

        for c in crafts:
            fleet.claim(c.vid, me)
        lan, match_id, started = fleet.call(place)
        config.lan = lan
        run = _Run(
            config=config,
            crafts=crafts,
            actor=me,
            session=session,
            by=actor.name or actor.kind if actor.kind == "session" else "operator",
            started_at=now,
            ends_at=now + config.duration_s,
            match_id=match_id,
            started_match=started,
        )
        fleet.events.emit(
            "dogfight_started",
            data={
                "aircraft": n,
                "duration": f"{config.duration_s:g}",
                "where": "LAN match" if lan else "local practice",
                "where_zh": "局域网对局" if lan else "本地练习",
                "roles": dict(config.roles),
                "by": run.by,
            },
        )
        return run

    def _place(self, v: Vehicle, c: Craft) -> None:
        """Back to the ground at the craft's spawn, facing the centre (fleet thread; disarmed or crashed)."""
        fleet = self.fleet
        x, y, yaw = c.spawn
        now = fleet.clock()
        fleet._stop_control(v, "reset", now)
        fleet._abort_course(v, "reset", now)
        v.spawn = (x, y)
        v.frame_offset = (x, y, 0.0)
        v.safety_origin = (x, y, 0.0)
        v.driver.reset()
        v.driver.sim.quad.s.yaw = math.radians(yaw)
        v.state["crashed"] = False
        v.crash_event = False

    # ------------------------------------------------------------------ the worker

    def _kick(self) -> None:
        if self.fleet._thread is None:
            return  # stepped fleet (tests, the evaluator): the caller polls
        self._wake.set()
        if self._thread is None or not self._thread.is_alive():
            self._closed = False
            self._thread = threading.Thread(target=self._loop, name="relay-dogfight", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while not self._closed:
            try:
                if not self.poll():
                    return
            except Exception:  # keep the scenario supervised; one bad poll must not leave aircraft flying
                log.exception("dogfight poll failed")
            self._wake.wait(POLL_S)
            self._wake.clear()

    def poll(self) -> bool:
        """Advance the scenario; False once it is done (nothing left to supervise)."""
        with self._lock:
            run = self.run
            if run is None or run.phase == "done":
                return False
            fleet = self.fleet
            now = fleet.clock()
            fleet.sessions.renew(run.session)
            if run.phase == "ending":
                self._ending(run, now)
                return run.phase != "done"
            if self._emergency(run, now):
                return True
            if now >= run.ends_at:
                self._begin_end(run, "time")
                return True
            for c in run.crafts:
                try:
                    self._craft(run, c, now)
                except RelayError as exc:
                    c.error = exc.message_en
                    c.tries += 1
                    if c.tries >= ARM_TRIES:
                        self._set(c, "failed", now)
                    log.info("dogfight %s: %s", c.vid, exc.message_en)
            states = {c.state for c in run.crafts}
            if states & {"booting", "arming", "deploying"} and run.phase != "running":
                run.phase = (
                    "deploying" if "deploying" in states else "arming" if "arming" in states else "booting"
                )
            else:
                run.phase = "running"
            if all(c.state == "failed" for c in run.crafts):
                run.error = run.crafts[0].error
                self._begin_end(run, "error")
            return True

    def _emergency(self, run: _Run, now: float) -> bool:
        """Aircraft an operator stopped leave the scenario for good; True when that ended the scenario."""
        stopped = [c for c in run.crafts if c.vid in self._estopped]
        self._estopped.difference_update(c.vid for c in stopped)
        fleet = self.fleet
        for c in stopped:
            if c.state in ("stopped", "failed"):
                continue
            c.error = "emergency stop"
            self._set(c, "stopped", now)
            v = fleet.vehicles.get(c.vid)
            if v is None:
                continue
            # a step of this scenario that raced the stop (an arm, a deploy) is undone; the stop itself stands
            try:
                if v.program is not None and v.program.active:
                    fleet.programs.stop(c.vid, run.actor)
                if v.armed and not v.airborne():
                    fleet.arm(c.vid, run.actor, False, wait=False)
            except RelayError as exc:
                log.info("dogfight %s after the emergency stop: %s", c.vid, exc.message_en)
        if stopped and all(c.state in ("stopped", "failed") for c in run.crafts):
            self._begin_end(run, "emergency_stop")
            return True
        return False

    def _set(self, c: Craft, state: str, now: float) -> None:
        c.state = state
        c.since = now

    def _craft(self, run: _Run, c: Craft, now: float) -> None:
        fleet = self.fleet
        v = fleet.vehicles.get(c.vid)
        if v is None:
            self._set(c, "stopped", now)
            return
        if c.state in ("stopped", "failed"):
            return
        sim = v.driver.sim
        s = sim.quad.s
        if v.auth.possession is not None and c.state != "human":
            self._set(c, "human", now)  # a person flies it; the program paused itself
            return
        if c.state == "human":
            if v.auth.possession is None:
                r = v.program
                if r is not None and r.status == "paused":
                    fleet.programs.resume(c.vid, run.actor)
                self._set(c, "flying" if r is not None and r.active else "down", now)
            return
        if c.state == "booting":
            if v.link_state == "ok" and now - c.since >= BOOT_S and not s.crashed:
                fleet.arm(c.vid, run.actor, True, wait=False)
                self._set(c, "arming", now)
            return
        if c.state == "arming":
            if v.armed:
                self._set(c, "deploying", now)
            elif now - c.since >= ARM_RETRY_S:
                c.tries += 1
                if c.tries >= ARM_TRIES:
                    c.error = "the aircraft did not arm"
                    self._set(c, "failed", now)
                else:
                    fleet.arm(c.vid, run.actor, True, wait=False)
                    c.since = now
            return
        if c.state == "deploying":
            self._deploy(run, c)
            c.tries = 0
            self._set(c, "flying", now)
            return
        if c.state == "flying":
            if s.crashed:
                self._set(c, "down", now)
                return
            if not v.armed and (v.program is None or not v.program.active):
                self._set(c, "down", now)  # landed and disarmed by itself, or stopped: back on respawn
                return
            box = run.config.arena
            ox, oy, _oz = v.frame_offset
            outside = math.hypot(s.p[0] + ox, s.p[1] + oy) > box["radius"] + OUT_MARGIN_M or (
                s.p[2] > box["ceiling"] + OUT_MARGIN_M
            )
            if outside:
                fleet.call(lambda: sim.shoot_down(OUT_REASON))
                self._set(c, "down", now)
            return
        if (
            c.state == "down"
            and run.config.respawn
            and now - c.since >= RESPAWN_S
            and v.auth.possession is None
        ):
            fleet.call(lambda: self._respawn(v, c))
            self._set(c, "booting", now - BOOT_S + 0.2)

    def _respawn(self, v: Vehicle, c: Craft) -> None:
        self._place(v, c)
        gun = self.fleet.arena.guns.get(v.id)
        if gun is not None:
            self.fleet.arena._revive(gun)

    def _deploy(self, run: _Run, c: Craft) -> None:
        fleet = self.fleet
        program = fleet.programs.store.get(c.program)
        revision = fleet.programs.store.revision(c.program, None)
        params: dict[str, Any] = dict(c.params)
        if run.config.teams != "ffa" and "team" in validator.validate(revision["source"]).params:
            params["team"] = c.team
        fleet.programs.deploy_source(
            c.vid,
            run.actor,
            program,
            revision["rev"],
            revision["source"],
            params,
            stored=revision.get("params") or {},
            mode="pilot",
            sensors={**SENSORS, "position": c.position},
            arena=dict(run.config.arena),
            seed=c.seed,
        )

    # ------------------------------------------------------------------ the end

    def _begin_end(self, run: _Run, reason: str) -> None:
        fleet = self.fleet
        now = fleet.clock()
        run.phase = "ending"
        run.reason = reason
        run.ending_at = now
        for c in run.crafts:
            v = fleet.vehicles.get(c.vid)
            if v is None:
                continue
            try:
                if v.program is not None and v.program.active:
                    fleet.programs.stop(c.vid, run.actor)
            except RelayError:
                pass
            try:
                if (
                    reason != "emergency_stop"  # the stop's own descent stands; no skill over it
                    and c.error != "emergency stop"
                    and v.airborne()
                    and not v.driver.sim.quad.s.crashed
                    and v.auth.possession is None
                ):
                    fleet.run_skill(c.vid, run.actor, "land", {})  # a safety action at every flight level
            except RelayError as exc:
                log.info("dogfight %s land: %s", c.vid, exc.message_en)
            self._set(c, "stopped", now)
        board = fleet.arena.board
        if (
            reason != "time"
            and run.started_match
            and board.match is not None
            and board.match.id == run.match_id
        ):
            fleet.call(lambda: board.end(fleet.clock(), broadcast=fleet.arena.on_lan))
        self._ending(run, now)

    def _ending(self, run: _Run, now: float) -> None:
        fleet = self.fleet
        assert run.ending_at is not None
        late = now - run.ending_at >= LAND_TIMEOUT_S
        for c in run.crafts:
            if c.vid in run.released:
                continue
            v = fleet.vehicles.get(c.vid)
            if v is None:
                run.released.add(c.vid)
                continue
            s = v.driver.sim.quad.s
            # A physical (unassisted) landing can bounce on the ground for a while without the landing
            # detector firing: a simulator sitting on the ground, slow, counts as down.
            grounded = s.p[2] < GROUND_M and math.hypot(*s.v) < GROUND_V
            settled = not v.airborne() or s.crashed or v.auth.possession is not None or grounded
            if not settled and not late:
                continue
            if v.armed and v.auth.possession is None:
                if not v.airborne() or s.crashed:
                    try:
                        fleet.arm(c.vid, run.actor, False, wait=False)
                    except RelayError:
                        pass
                    run.released.add(c.vid)
                    continue
                # Grounded but not detected, or still up after the landing time: a simulator only, so disarm
                # it with the checks skipped (the motors stop; one still in the air falls).
                if now - run.forced.get(c.vid, -math.inf) >= FORCE_RETRY_S:
                    run.forced[c.vid] = now
                    fleet.call(lambda d=v.driver: d.arm(False, force=True))
                continue
            run.released.add(c.vid)
        if len(run.released) < len(run.crafts):
            return
        arena = fleet.arena

        def release() -> None:
            for c in run.crafts:
                arena.respawn_hold.discard(c.vid)
                arena.hold_fire.discard(c.vid)
                fleet.programs.teams.pop(c.vid, None)
            if arena.practice:
                m = arena.board.match
                if m is not None and m.running:
                    arena.board.end(fleet.clock(), broadcast=False)
                arena.end_practice()  # the board and its final standings stay readable

        fleet.call(release)
        try:
            fleet.close_session(run.session)
        except RelayError:
            pass
        run.phase = "done"
        fleet.events.emit(
            "dogfight_finished",
            data={
                "reason": run.reason,
                "detail_en": {
                    "time": "time is up",
                    "stopped": "stopped",
                    "error": "set-up failed",
                    "emergency_stop": "emergency stop",
                }.get(run.reason or "", run.reason or "-"),
                "detail_zh": {
                    "time": "时间到",
                    "stopped": "已停止",
                    "error": "建立失败",
                    "emergency_stop": "急停",
                }.get(run.reason or "", run.reason or "-"),
            },
        )


def _sim_order(vid: str) -> tuple[int, str]:
    try:
        return (int(vid.rsplit("-", 1)[1]), vid)
    except (IndexError, ValueError):
        return (10**6, vid)


def wait_done(dogfights: Dogfights, timeout: float = 5.0) -> bool:
    """Wait until the worker has finished (tests and shutdown)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        run = dogfights.run
        if run is None or run.phase == "done":
            return True
        time.sleep(0.05)
    return False


__all__ = ["DEFAULT_PROGRAMS", "Dogfights", "wait_done"]
