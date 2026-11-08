"""Arena rules on the fleet thread (05 §7.1-§7.4): fire input, magazines, bullets, hit tests, hit points,
shoot-down and respawn, scores, the 20 Hz STATE broadcast and the ``arena`` stream view.

Only local simulators take part. A shot leaves the nose of an armed, airborne simulator that a person or an
agent controls; real aircraft never fire, never appear in STATE and cannot be hit. Every terminal tests
bullets (its own and its peers') against its own simulators only, at the integrator's 1/240 s steps with a
swept segment-against-sphere test (the target's centre interpolated across the fleet tick), and reports hits
on its aircraft with HIT packets. Effects of a hit go through ``Quad.impulse`` and
``SimAutopilot.shoot_down``; nothing here sends a stick, a mode, an arm command or a parameter.

Bullets are kept in arena coordinates, so a seat change never moves one in flight. Remote aircraft and bullets
are converted into the local frame only for the view.

WP-B (07 §2-§4): ``board`` keeps the per-aircraft statistics, the match and the merged leaderboard
(``peers.board``); ``collisions`` the contacts with version 2 peers' aircraft (``peers.collisions``). A
dogfight scenario off the LAN runs the arena in practice mode (``begin_practice``: no socket, ``network``
off).

LAN input never sets the fleet thread's pace: a remote shot is accepted only when it is younger than
``MAX_SHOT_AGE_S`` and its aircraft keeps under ``REMOTE_FIRE_RATE``; each bullet catches up at most
``CATCHUP_STEPS`` per tick, the whole flight work of a tick stops at ``FLY_BUDGET_S`` of wall time (the rest
continues on the next tick) and a bullet more than ``MAX_LAG_S`` behind is dropped. A HIT is accepted once
per bullet, only from the terminal that owns the target, with the damage clamped and the shoot-down checked
against the target's hit points.
"""

from __future__ import annotations

import logging
import math
import secrets
import socket
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import err
from . import arena as frame
from . import ballistics as bal
from . import wire
from .board import Board  # WP-B
from .collisions import LanCollisions  # WP-B
from .service import DEFAULT_PORT, STATE_HZ, PeerService, default_interfaces

if TYPE_CHECKING:
    from ..fleet import FleetService, Vehicle

log = logging.getLogger("relay.arena")

FIRE_RATE = 8.0
MAGAZINE = 24
RELOAD_S = 1.5
HOLD_EXPIRY_S = 0.3  # WebSocket fire input lapses without a refresh (05 §7.4)
REST_FIRE_MAX_S = 3.0
MAX_HP = 100
RESPAWN_S = 5.0
SEAT_LISTEN_S = 1.5
VIEW_HZ = 20.0
STATE_SHOT_COPIES = 3
REMOTE_BULLETS_PER_PEER = 64
MAX_BULLETS = 512
SHOT_DOWN = "Shot down"
# remote input bounds (fleet-thread budget; see the module notes)
MAX_SHOT_AGE_S = 0.3
REMOTE_FIRE_RATE = 12.0  # per remote aircraft; a terminal fires 8 rounds per second at most
CATCHUP_STEPS = 48
MAX_LAG_S = 0.5
FLY_BUDGET_S = 0.002
ID_FILE = "peer-id"


def load_instance(data_dir: Path | None) -> int:
    """The install's arena id: 64 random bits kept in <data_dir>/peer-id (05 §7.3)."""
    path = data_dir / ID_FILE if data_dir is not None else None
    if path is not None:
        try:
            value = int(path.read_text("utf-8").strip(), 16)
            if 0 < value < 2**64:
                return value
        except (OSError, ValueError):
            pass
    value = 0
    while value == 0:
        value = secrets.randbits(64)
    if path is not None:
        try:
            data_dir.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
            path.write_text(f"{value:016x}\n", "utf-8")
        except OSError as exc:
            log.warning("peer-id not saved: %s", exc)
    return value


def default_callsign() -> str:
    """The host name, cut at the first dot, cleaned and at most 16 characters."""
    try:
        name = socket.gethostname().split(".")[0]
    except OSError:
        name = ""
    return wire.clean_callsign(name) or "Relay"


def sim_index(vid: str) -> int | None:
    if not vid.startswith("sim-"):
        return None
    try:
        n = int(vid[4:])
    except ValueError:
        return None
    return n if 1 <= n <= wire.MAX_AIRCRAFT else None


@dataclass
class Bullet:
    owner: int  # instance id of the terminal that fired it
    shooter: int  # aircraft index on that terminal
    bullet: int
    p0: tuple[float, float, float]  # arena coordinates at t0
    v0: tuple[float, float, float]
    t0: float  # fleet clock at which (p0, v0) holds
    p: tuple[float, float, float] = (0.0, 0.0, 0.0)
    v: tuple[float, float, float] = (0.0, 0.0, 0.0)
    steps: int = 0

    def __post_init__(self) -> None:
        self.p, self.v = self.p0, self.v0


@dataclass
class Gun:
    """Per local simulator: hit points, magazine and fire input."""

    hp: int = MAX_HP
    ammo: int = MAGAZINE
    reload_until: float | None = None
    fire_until: float = 0.0
    next_shot: float = 0.0
    down_at: float | None = None
    centre: tuple[float, float, float] | None = None  # arena position at the previous tick

    @property
    def down(self) -> bool:
        return self.down_at is not None


@dataclass
class Score:
    hits: int = 0
    downs: int = 0
    deaths: int = 0

    def view(self) -> dict[str, int]:
        return {"hits": self.hits, "downs": self.downs, "deaths": self.deaths}


@dataclass
class _Pending:
    shot: wire.Shot
    copies: int = STATE_SHOT_COPIES


@dataclass(eq=False, repr=False)
class Arena:
    fleet: FleetService
    network: bool = True
    interfaces: Any = default_interfaces
    instance: int = 0
    enabled: bool = False
    seat: int | None = None
    seat_at: float | None = None  # when the seat is chosen after listening
    peers: PeerService | None = None
    guns: dict[str, Gun] = field(default_factory=dict)
    bullets: list[Bullet] = field(default_factory=list)
    score: Score = field(default_factory=Score)
    snapshot: dict[str, Any] = field(default_factory=dict)
    _own_shots: deque[tuple[int, int]] = field(default_factory=lambda: deque(maxlen=256))
    _pending: deque[_Pending] = field(default_factory=lambda: deque(maxlen=wire.MAX_STATE_SHOTS))
    _bullet_seq: int = 0
    _next_state: float = 0.0
    _next_view: float = 0.0
    _last_tick: float | None = None
    _port: int | None = None
    _shot_rate: dict[tuple[int, int], tuple[float, float]] = field(default_factory=dict)  # (tokens, at)
    _target_hp: dict[tuple[int, int], int] = field(default_factory=dict)  # remote aircraft hit points
    late_bullets: int = 0
    _fly_turn: int = 0
    # WP-2b headless evaluator: wall-time budget of the bullet work per tick (unbounded there, so a run
    # does not depend on how fast the machine is); None keeps FLY_BUDGET_S, as the live fleet does
    fly_budget_s: float | None = None
    # WP-B (07 §3-§4): leaderboard and match, LAN collisions, local practice mode of a dogfight scenario
    board: Board = field(init=False)
    collisions: LanCollisions = field(init=False)
    practice: bool = False
    _network: bool = True
    respawn_hold: set[str] = field(default_factory=set)  # the scenario respawns these itself
    hold_fire: set[str] = field(default_factory=set)  # rammers: the trigger is ignored

    def __post_init__(self) -> None:
        if not self.instance:
            self.instance = load_instance(self.fleet.data_dir)
        self.board = Board(self)
        self.collisions = LanCollisions(self)
        self._network = self.network
        self.fleet.events.add_listener(self.board.on_event)
        self._build_view(self.fleet.clock())

    # ------------------------------------------------------------------ settings

    @property
    def callsign(self) -> str:
        return wire.clean_callsign(self.fleet.settings.peer_callsign) or default_callsign()

    def validate(self, settings: Any) -> None:
        """Port rules the settings model cannot check alone: the arena port is not the API port."""
        port = settings.peer_port
        if self.fleet.http_port is not None and port == self.fleet.http_port:
            raise err("invalid_request", reason="setting peer_port is not valid")

    def apply_settings(self) -> None:
        """Join or leave the arena and follow port changes (fleet thread)."""
        s = self.fleet.settings
        now = self.fleet.clock()
        if not s.peer_enabled:
            if self.enabled and not self.practice:
                self._leave()
            return
        if self.practice:  # WP-B: the operator joined the LAN during a practice dogfight
            self.end_practice()
        if not self.enabled:
            self._join(now)
        elif self.network and self._port != s.peer_port:
            self._open(s.peer_port)
        self._build_view(now)

    def _join(self, now: float) -> None:
        self.enabled = True
        self.seat = None
        self.seat_at = now + SEAT_LISTEN_S
        self.score = Score()
        self.bullets.clear()
        self._pending.clear()
        self._next_state = now
        if self.network:
            self._open(self.fleet.settings.peer_port)

    def _open(self, port: int) -> None:
        if self.peers is not None:
            self.peers.close()
        self.peers = PeerService(self.instance, port, clock=self.fleet.clock, interfaces=self.interfaces)
        self._port = port
        if not self.peers.open():
            log.warning("arena socket unavailable: %s", self.peers.error)

    def _leave(self) -> None:
        if self.peers is not None:
            self.peers.close()
        self.board.leave(self.fleet.clock())  # WP-B
        self.collisions.clear()
        self.enabled = False
        self.seat = None
        self.seat_at = None
        self.bullets.clear()
        self._pending.clear()
        self._shot_rate.clear()
        self._target_hp.clear()
        self._port = None
        for gun in self.guns.values():
            gun.fire_until = 0.0
        self._build_view(self.fleet.clock())

    def close(self) -> None:
        if self.peers is not None:
            self.peers.close()
            self.peers = None

    # ------------------------------------------------------------------ WP-B: practice mode (07 §3)

    @property
    def on_lan(self) -> bool:
        return self.enabled and self.network and not self.practice

    def begin_practice(self) -> None:
        """Run the arena on this terminal only (no socket) for a dogfight while it is not on the LAN."""
        if self.enabled:
            return
        self.practice = True
        self.network = False
        self._join(self.fleet.clock())
        self._build_view(self.fleet.clock())

    def end_practice(self) -> None:
        if not self.practice:
            return
        self.practice = False
        self.network = self._network
        if self.enabled:
            self._leave()
        if self.fleet.settings.peer_enabled:
            self.apply_settings()

    # ------------------------------------------------------------------ queries

    def is_down(self, vid: str) -> bool:
        gun = self.guns.get(vid)
        return gun is not None and gun.down

    def network_view(self) -> dict[str, Any]:
        base: dict[str, Any] = {"enabled": self.enabled, "seat": self.seat}
        if self.peers is not None and self.enabled:
            base.update(self.peers.view())
        return base

    # ------------------------------------------------------------------ fire input

    def fire(self, v: Vehicle, on: bool, seconds: float | None, now: float) -> dict[str, Any]:
        """Hold-type fire input for one local simulator (the caller checked kind and control)."""
        gun = self.guns.setdefault(v.id, Gun())
        if not on or (seconds is not None and seconds <= 0):
            gun.fire_until = 0.0
            return self._fire_view(v.id, gun, False)
        if not self.enabled:
            raise err("arena_off")
        if gun.down:
            raise err("arena_down")
        if not v.armed:
            raise err("not_armed")
        if not v.airborne():
            raise err("not_airborne")
        hold = HOLD_EXPIRY_S if seconds is None else min(REST_FIRE_MAX_S, float(seconds))
        gun.fire_until = max(gun.fire_until, now + hold)
        return self._fire_view(v.id, gun, True)

    def _fire_view(self, vid: str, gun: Gun, firing: bool) -> dict[str, Any]:
        return {
            "id": vid,
            "firing": firing,
            "ammo": gun.ammo,
            "max": MAGAZINE,
            "reloading": gun.reload_until is not None,
            "hp": gun.hp,
        }

    # ------------------------------------------------------------------ the tick

    def tick(self, now: float) -> None:
        last = self._last_tick if self._last_tick is not None else now
        self._last_tick = now
        sims = {vid: v for vid, v in self.fleet.vehicles.items() if v.kind == "sim" and sim_index(vid)}
        for vid in [k for k in self.guns if k not in sims]:
            del self.guns[vid]
        for vid in sims:
            gun = self.guns.setdefault(vid, Gun())
            if gun.down and not sims[vid].driver.sim.quad.s.crashed:
                self._revive(gun)  # reset by the operator, also while the arena is off
        if not self.enabled:
            return
        if self.seat is None and self.seat_at is not None and now >= self.seat_at:
            self._choose_seat()
        result = self.peers.tick(now) if self.peers is not None else None
        if result is not None:
            self._on_peers(result, now)
        centres = {vid: self._centre(v) for vid, v in sims.items()}
        for vid, v in sims.items():
            self._gun_tick(v, self.guns[vid], centres[vid], now)
        if self.seat is not None:
            self._fly(sims, centres, last, now)
        for vid, gun in self.guns.items():
            gun.centre = centres.get(vid)
        # WP-B (07 §2, §4): contacts with peers' aircraft, then statistics and the match clock
        self.collisions.tick(sims, now)
        self.board.tick(sims, now)
        if self.peers is not None and self.peers.ok and now >= self._next_state:
            self._next_state = max(self._next_state + 1.0 / STATE_HZ, now)
            self._send_state(sims)
        if now >= self._next_view:
            self._next_view = now + 1.0 / VIEW_HZ
            self._build_view(now)

    def _centre(self, v: Vehicle) -> tuple[float, float, float] | None:
        if self.seat is None:
            return None
        s = v.driver.sim.quad.s
        ox, oy, oz = v.frame_offset
        return frame.to_arena(self.seat, (s.p[0] + ox, s.p[1] + oy, s.p[2] + oz))

    def _choose_seat(self) -> None:
        taken = set()
        if self.peers is not None:
            taken = {p.seat for p in self.peers.peers.values() if p.seat is not None}
        self.seat = frame.free_seat(taken)
        if self.seat is None:  # twelve terminals already seated: share seat 0 rather than stay out
            self.seat = 0

    def _on_peers(self, result: Any, now: float) -> None:
        for peer in result.joined:
            self.fleet.events.emit(
                "peer_joined", data={"peer": peer.key, "callsign": peer.callsign, "seat": peer.seat}
            )
        for peer, reason in result.left:
            self.board.forget_peer(peer.instance)  # WP-B
            self.collisions.forget_peer(peer.instance)
            self.bullets = [b for b in self.bullets if b.owner != peer.instance]
            for table in (self._shot_rate, self._target_hp):
                for key in [k for k in table if k[0] == peer.instance]:
                    del table[key]
            self.fleet.events.emit(
                "peer_left", data={"peer": peer.key, "callsign": peer.callsign, "reason": reason}
            )
        for peer in result.states:
            for a in peer.aircraft.values():
                self._target_hp[(peer.instance, a.index)] = a.hp
            # two terminals on one seat: the smaller instance keeps it, the other moves on (05 §7.2)
            if (
                self.seat is not None
                and peer.seat == self.seat
                and not frame.keeps_seat(self.instance, peer.instance)
                and self.peers is not None
            ):
                taken = {p.seat for p in self.peers.peers.values() if p.seat is not None}
                self.seat = frame.free_seat(taken | {self.seat}, after=self.seat) or 0
        for peer, shot, age in result.shots:
            self._remote_shot(peer, shot, age, now)
        for peer, hit in result.hits:
            self._remote_hit(peer, hit)
        for peer, collide, age in result.collides:  # WP-B (07 §2)
            self.collisions.on_packet(peer, collide, age, now)
        for peer, match, age in result.matches:  # WP-B (07 §4)
            self.board.on_match(peer, match, age, now)

    # ------------------------------------------------------------------ guns

    def _can_fire(self, v: Vehicle, gun: Gun) -> bool:
        return (
            self.seat is not None
            and not gun.down
            and v.armed
            and v.airborne()
            and v.controller(self.fleet.sessions) in ("human", "agent", "program")  # program: WP-1
            and not v.driver.sim.quad.s.crashed
            and (v.id not in self.hold_fire or v.auth.possession is not None)  # WP-B: rammers do not fire
        )

    def _gun_tick(self, v: Vehicle, gun: Gun, centre: Any, now: float) -> None:
        sim = v.driver.sim
        if gun.down:
            if not sim.quad.s.crashed:  # reset by the operator (Circle held, or the reset action)
                self._revive(gun)
            elif (
                self.fleet.settings.arena_auto_respawn
                and v.id not in self.respawn_hold
                and now - (gun.down_at or now) >= RESPAWN_S
            ):
                self._respawn(v, gun, now)
            return
        if gun.reload_until is not None and now >= gun.reload_until:
            gun.reload_until = None
            gun.ammo = MAGAZINE
        firing = now < gun.fire_until and centre is not None and self._can_fire(v, gun)
        if not firing or gun.reload_until is not None or now < gun.next_shot:
            return
        # keep the 8 rounds per second cadence across ticks; after a pause the next round waits a full period
        period = 1.0 / FIRE_RATE
        gun.next_shot = gun.next_shot + period if now - gun.next_shot < period else now + period
        self._spawn(v, centre, now)
        gun.ammo -= 1
        if gun.ammo <= 0:
            gun.ammo = 0
            gun.reload_until = now + RELOAD_S

    def _spawn(self, v: Vehicle, centre: tuple[float, float, float], now: float) -> None:
        assert self.seat is not None
        s = v.driver.sim.quad.s
        heading = frame.heading_to_arena(self.seat, s.yaw)
        vel = frame.vec_to_arena(self.seat, s.v)
        muzzle = bal.muzzle(heading, s.pitch, vel)
        self._bullet_seq = (self._bullet_seq + 1) & 0xFFFFFFFF
        index = sim_index(v.id) or 0
        bullet = Bullet(self.instance, index, self._bullet_seq, centre, muzzle, now)
        self._add_bullet(bullet)
        self.board.shot(v.id)  # WP-B
        self._own_shots.append((self._bullet_seq, index))
        if self.peers is not None and self.peers.ok:
            shot = wire.Shot(index, self._bullet_seq, self.peers.mono_ms(now), centre, muzzle)
            self.peers.send_shot(shot)
            self._pending.append(_Pending(shot))

    def _add_bullet(self, b: Bullet) -> None:
        if len(self.bullets) >= MAX_BULLETS:
            self.bullets.pop(0)
        self.bullets.append(b)

    def _remote_shot(self, peer: Any, shot: wire.Shot, age: float, now: float) -> None:
        if not 0.0 <= age <= MAX_SHOT_AGE_S:
            if self.peers is not None:
                self.peers.counters["late_shot"] += 1
            return
        key = (peer.instance, shot.shooter)
        tokens, at = self._shot_rate.get(key, (REMOTE_FIRE_RATE, now))
        tokens = min(REMOTE_FIRE_RATE, tokens + max(0.0, now - at) * REMOTE_FIRE_RATE)
        if tokens < 1.0:
            self._shot_rate[key] = (tokens, now)
            return
        self._shot_rate[key] = (tokens - 1.0, now)
        if sum(1 for b in self.bullets if b.owner == peer.instance) >= REMOTE_BULLETS_PER_PEER:
            return
        self._add_bullet(Bullet(peer.instance, shot.shooter, shot.bullet, shot.p, shot.v, now - age))

    # ------------------------------------------------------------------ bullets and hits

    def _fly(self, sims: dict[str, Vehicle], centres: dict[str, Any], last: float, now: float) -> None:
        span = max(1e-9, now - last)
        dead: set[int] = set()
        deadline = time.perf_counter() + (FLY_BUDGET_S if self.fly_budget_s is None else self.fly_budget_s)
        # this terminal's rounds first (they decide our own score); remote ones take turns at what is left
        own = [b for b in self.bullets if b.owner == self.instance]
        remote = [b for b in self.bullets if b.owner != self.instance]
        if remote:
            k = self._fly_turn % len(remote)
            remote = remote[k:] + remote[:k]
            self._fly_turn += 1
        for b in own + remote:
            if now - (b.t0 + b.steps * bal.DT) > MAX_LAG_S:
                self.late_bullets += 1  # too far behind to mean anything any more
                dead.add(id(b))
                continue
            if time.perf_counter() > deadline:
                continue  # the rest of the flight work waits for the next tick
            budget = CATCHUP_STEPS
            while budget > 0 and b.t0 + (b.steps + 1) * bal.DT <= now:
                budget -= 1
                ts = b.t0 + b.steps * bal.DT
                p0 = b.p
                b.p, b.v = bal.step(b.p, b.v)
                b.steps += 1
                hit = self._sweep(b, p0, ts, sims, centres, last, span)
                if hit is not None:
                    vid, frac = hit
                    point = tuple(p0[i] + (b.p[i] - p0[i]) * frac for i in range(3))
                    self._hit(sims[vid], b, point, centres[vid], now)  # type: ignore[arg-type]
                    dead.add(id(b))
                    break
                if b.steps >= bal.TTL_STEPS or b.p[2] <= 0.0:
                    dead.add(id(b))
                    break
        if dead:
            self.bullets = [b for b in self.bullets if id(b) not in dead]

    def _sweep(
        self,
        b: Bullet,
        p0: tuple[float, float, float],
        ts: float,
        sims: dict[str, Vehicle],
        centres: dict[str, Any],
        last: float,
        span: float,
    ) -> tuple[str, float] | None:
        best: tuple[str, float] | None = None
        reach = math.dist(p0, b.p) + bal.HIT_RADIUS + 0.5
        for vid, c1 in centres.items():
            if c1 is None:
                continue
            gun = self.guns.get(vid)
            if gun is None or gun.down:
                continue
            if b.owner == self.instance and b.shooter == sim_index(vid) and ts - b.t0 < bal.SELF_IMMUNE_S:
                continue
            prev = gun.centre if gun.centre is not None else c1
            if math.dist(p0, c1) > reach + math.dist(prev, c1):
                continue
            f0 = min(1.0, max(0.0, (ts - last) / span))
            f1 = min(1.0, max(0.0, (ts + bal.DT - last) / span))
            ca = tuple(prev[i] + (c1[i] - prev[i]) * f0 for i in range(3))
            cb = tuple(prev[i] + (c1[i] - prev[i]) * f1 for i in range(3))
            t = bal.sweep(p0, b.p, ca, cb, bal.HIT_RADIUS)
            if t is not None and (best is None or t < best[1]):
                best = (vid, t)
        return best

    def _hit(self, v: Vehicle, b: Bullet, point: tuple[float, float, float], centre: Any, now: float) -> None:
        assert self.seat is not None
        gun = self.guns[v.id]
        sim = v.driver.sim
        s = sim.quad.s
        centre = centre or point
        v_target = frame.vec_to_arena(self.seat, s.v)
        dv = bal.hit_impulse(b.v, v_target)
        offset = tuple(point[i] - centre[i] for i in range(3))
        kick = bal.hit_kick(offset, frame.heading_to_arena(self.seat, s.yaw), b.bullet)
        sim.quad.impulse(frame.vec_from_arena(self.seat, dv), kick)
        gun.hp = max(0, gun.hp - bal.DAMAGE)
        down = gun.hp == 0
        if down:
            gun.down_at = now
            gun.fire_until = 0.0
            sim.shoot_down(SHOT_DOWN)
            self.score.deaths += 1
            self.board.death(v.id)  # WP-B
        own = b.owner == self.instance
        if own:
            self.score.hits += 1
            if down:
                self.score.downs += 1
            if f"sim-{b.shooter}" != v.id:
                self.board.hit(f"sim-{b.shooter}", bal.DAMAGE, down)  # WP-B
        elif self.peers is not None and self.peers.ok:
            self.peers.send_hit(wire.Hit(b.owner, b.bullet, sim_index(v.id) or 0, bal.DAMAGE, down, point))
        shooter = self._shooter_label(b)
        local_point = list(_round3(frame.from_arena(self.seat, point)))
        travel = frame.vec_from_arena(self.seat, b.v)
        data = {
            "side": "taken",
            "target": v.id,
            "shooter": shooter[0],
            "shooter_callsign": shooter[1],
            "own_shot": own,
            "damage": bal.DAMAGE,
            "hp": gun.hp,
            "down": down,
            "point": local_point,
            "dir": [round(travel[0], 3), round(travel[1], 3), round(travel[2], 3)],
        }
        self.fleet.events.emit(
            "arena_hit",
            v.id,
            data={
                **data,
                "detail_en": f"was hit by {shooter[2]}, {gun.hp} HP left",
                "detail_zh": f"被 {shooter[3]} 命中，剩余生命值 {gun.hp}",
            },
            labels={"vehicle": v.label},
        )
        if down:
            self.fleet.events.emit(
                "arena_down",
                v.id,
                data={
                    "side": "taken",
                    "target": v.id,
                    "shooter": shooter[0],
                    "shooter_callsign": shooter[1],
                    "own_shot": own,
                    "point": local_point,
                    "detail_en": f"was shot down by {shooter[2]}",
                    "detail_zh": f"被 {shooter[3]} 击落",
                },
                labels={"vehicle": v.label},
            )

    def _shooter_label(self, b: Bullet) -> tuple[str, str, str, str]:
        """(key, callsign, English name, Chinese name) of the aircraft that fired ``b``."""
        if b.owner == self.instance:
            return (f"sim-{b.shooter}", self.callsign, f"SIM-{b.shooter}", f"SIM-{b.shooter}")
        peer = self.peers.peers.get(b.owner) if self.peers is not None else None
        call = peer.callsign if peer is not None else "Relay"
        key = f"{b.owner:016x}/sim-{b.shooter}"
        return (key, call, f"{call} / SIM-{b.shooter}", f"{call} / SIM-{b.shooter}")

    def _remote_hit(self, peer: Any, hit: wire.Hit) -> None:
        """A peer reports that one of our bullets hit its aircraft (it owns the target, so it decides).

        Accepted once per bullet and only for an aircraft the sending terminal flies; the damage is clamped
        and a shoot-down needs the target's hit points (its last STATE, less the hits since) to reach zero."""
        target = (peer.instance, hit.target)
        if hit.target not in peer.aircraft:
            return
        hp = self._target_hp.get(target, MAX_HP)
        if hp <= 0:
            return  # already down: no further hits
        damage = max(0, min(bal.DAMAGE, hit.damage))
        left = max(0, hp - damage)
        self._target_hp[target] = left  # every terminal's hits on it, so the shoot-down check follows them
        if hit.shooter_instance != self.instance:
            return
        entry = next((e for e in self._own_shots if e[0] == hit.bullet), None)
        if entry is None:
            return  # not a bullet this terminal fired recently, or one already reported
        self._own_shots.remove(entry)
        shooter = entry[1]
        down = bool(hit.down) and left == 0
        self.bullets = [b for b in self.bullets if not (b.owner == self.instance and b.bullet == hit.bullet)]
        self.score.hits += 1
        if down:
            self.score.downs += 1
        vid = f"sim-{shooter}"
        self.board.hit(vid, damage, down)  # WP-B
        v = self.fleet.vehicles.get(vid)
        label = v.label if v is not None else f"SIM-{shooter}"
        target = f"{peer.callsign} / SIM-{hit.target}"
        data = {
            "side": "hit",
            "target": f"{peer.key}/sim-{hit.target}",
            "target_callsign": peer.callsign,
            "shooter": vid,
            "damage": damage,
            "down": down,
            "point": self._local(hit.point),
        }
        self.fleet.events.emit(
            "arena_hit",
            vid if v is not None else None,
            data={**data, "detail_en": f"hit {target}", "detail_zh": f"命中 {target}"},
            labels={"vehicle": label},
        )
        if down:
            self.fleet.events.emit(
                "arena_down",
                vid if v is not None else None,
                data={**data, "detail_en": f"shot down {target}", "detail_zh": f"击落 {target}"},
                labels={"vehicle": label},
            )

    def _local(self, p: tuple[float, float, float]) -> list[float] | None:
        if self.seat is None:
            return None
        return list(_round3(frame.from_arena(self.seat, p)))

    # ------------------------------------------------------------------ down and respawn

    def _revive(self, gun: Gun) -> None:
        gun.hp = MAX_HP
        gun.ammo = MAGAZINE
        gun.reload_until = None
        gun.down_at = None
        gun.fire_until = 0.0

    def _respawn(self, v: Vehicle, gun: Gun, now: float) -> None:
        """Back to the spawn point: the reset_sim path without its armed check (the aircraft is down)."""
        self.fleet._abort_course(v, "reset", now)
        v.driver.reset()
        v.state["crashed"] = False
        v.crash_event = False
        self._revive(gun)

    # ------------------------------------------------------------------ STATE and the view

    def _send_state(self, sims: dict[str, Vehicle]) -> None:
        assert self.peers is not None
        aircraft = []
        for vid, v in sorted(sims.items(), key=lambda item: sim_index(item[0]) or 0)[: wire.MAX_AIRCRAFT]:
            sim = v.driver.sim
            s = sim.quad.s
            gun = self.guns.get(vid) or Gun()
            ox, oy, oz = v.frame_offset
            flags = (
                (wire.ARMED if sim.armed else 0)
                | (wire.DOWN if gun.down else 0)
                | (wire.ASSIST if getattr(sim, "hover_assist", True) else 0)
                | (wire.RELOADING if gun.reload_until is not None else 0)
            )
            aircraft.append(
                wire.Aircraft(
                    index=sim_index(vid) or 0,
                    flags=flags,
                    hp=gun.hp,
                    p=(s.p[0] + ox, s.p[1] + oy, s.p[2] + oz),
                    v=(s.v[0], s.v[1], s.v[2]),
                    att=(s.roll, s.pitch, s.yaw),
                    throttle=s.motor,
                    ammo=gun.ammo,
                )
            )
        shots = []
        for item in self._pending:
            if item.copies > 0:
                shots.append(item.shot)
                item.copies -= 1
        state = wire.State(
            callsign=self.callsign,
            seat=self.seat,
            rtt_ms=self.peers.median_rtt(),
            hits=self.score.hits,
            downs=self.score.downs,
            deaths=self.score.deaths,
            aircraft=tuple(aircraft),
            shots=tuple(shots),
            # WP-B (07 §4): the leaderboard block once per second (a version 2 STATE)
            stats=self.board.stats_block(self.fleet.clock())
            if self.board.next_state_carries_stats()
            else None,
        )
        self.peers.send_state(state)

    def _build_view(self, now: float) -> None:
        wall = self.fleet.wall()
        peers_view = []
        shots_view = []
        if self.enabled and self.peers is not None:
            for peer in sorted(
                self.peers.peers.values(), key=lambda p: (p.seat is None, p.seat or 0, p.instance)
            ):
                if not peer.joined:
                    continue
                vehicles = []
                if peer.seat is not None:
                    mine = self.seat if self.seat is not None else 0
                    for a in sorted(peer.aircraft.values(), key=lambda x: x.index):
                        p = frame.peer_to_local(a.p, peer.seat, mine)
                        vel = frame.peer_vec_to_local(a.v, peer.seat, mine)
                        vehicles.append(
                            {
                                "key": f"{peer.key}/sim-{a.index}",
                                "label": f"SIM-{a.index}",
                                "x": round(p[0], 3),
                                "y": round(p[1], 3),
                                "z": round(p[2], 3),
                                "vx": round(vel[0], 3),
                                "vy": round(vel[1], 3),
                                "vz": round(vel[2], 3),
                                "roll": round(a.att[0], 4),
                                "pitch": round(a.att[1], 4),
                                "yaw": round(frame.peer_heading_to_local(a.att[2], peer.seat, mine), 4),
                                "throttle": round(a.throttle, 3),
                                "armed": a.armed,
                                "down": a.down,
                                "hp": a.hp,
                            }
                        )
                peers_view.append(
                    {
                        "id": peer.key,
                        "callsign": peer.callsign,
                        "seat": peer.seat,
                        "rtt_ms": peer.rtt_ms,
                        "stale": peer.stale(now),
                        "age_s": round(max(0.0, now - peer.last_seen), 2),
                        "score": {"hits": peer.hits, "downs": peer.downs, "deaths": peer.deaths},
                        "vehicles": vehicles,
                    }
                )
        if self.enabled and self.seat is not None:
            for b in self.bullets[-MAX_BULLETS:]:
                p = frame.from_arena(self.seat, b.p0)
                vel = frame.vec_from_arena(self.seat, b.v0)
                shots_view.append(
                    {
                        "id": f"{b.owner:016x}:{b.bullet}",
                        "owner": f"{b.owner:016x}",
                        "mine": b.owner == self.instance,
                        "t0": round(wall - (now - b.t0), 4),
                        "p": [round(x, 4) for x in p],
                        "v": [round(x, 4) for x in vel],
                    }
                )
        guns = {vid: g for vid, g in sorted(self.guns.items())}
        self.snapshot = {
            "t": wall,
            "enabled": self.enabled,
            "instance": f"{self.instance:016x}",
            "seat": self.seat,
            "callsign": self.callsign,
            "error": self.peers.error if self.peers is not None and self.enabled else None,
            "peers": peers_view,
            "shots": shots_view,
            "local": {
                "ammo": {vid: g.ammo for vid, g in guns.items()},
                "hp": {vid: g.hp for vid, g in guns.items()},
                "reloading": {vid: g.reload_until is not None for vid, g in guns.items()},
                # reload progress 0..1 of the reloading guns (HUD voxel steps, 05 §7.5)
                "reload": {
                    vid: round(min(1.0, max(0.0, 1.0 - (g.reload_until - now) / RELOAD_S)), 2)
                    for vid, g in guns.items()
                    if g.reload_until is not None
                },
                "down": {vid: g.down for vid, g in guns.items()},
                "max_ammo": MAGAZINE,
                "max_hp": MAX_HP,
                "score": self.score.view(),
            },
            # WP-B (07 §4): the match and the leaderboard revision (refetch the board when it changes)
            "match": self.board.match_view(now),
            "board_rev": self.board.rev,
        }


def _round3(p: tuple[float, float, float]) -> tuple[float, float, float]:
    return (round(p[0], 3), round(p[1], 3), round(p[2], 3))


__all__ = ["DEFAULT_PORT", "Arena", "load_instance"]
