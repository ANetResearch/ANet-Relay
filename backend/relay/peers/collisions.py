"""Collisions with peers' aircraft (07 §2 "局域网", WP-B), on the fleet thread as part of the arena.

Every terminal judges only its own aircraft, as with bullets (05 §7.3). Each tick the remote aircraft of
version 2 peers become moving spheres in this terminal's local frame: from the previous tick's position to the
latest STATE position carried forward by its age (at most ``LEAD_MAX_S``, the 0.15 s lead of the view). A
0.4 peer's aircraft never collide: it would not hear about it.

Detection: when WP-A's collision world is on the fleet (``fleet.world`` with ``external`` and
``on_external_contact``), the arena fills ``world.external`` every tick and the world sweeps them at 250 Hz
with the local aircraft, applies the contact to the local aircraft and calls ``on_contact``. Without it this
module sweeps once per fleet tick itself (``relay.sim.collide`` functions, or the stand-in
``collide_fallback`` on a tree without WP-A's module) and applies the same rule through ``Quad.collide`` (or
``Quad.impulse`` and ``SimAutopilot.shoot_down`` on the pre-v0.5 flight model).

The rule (07 §2): equal masses, restitution ``RESTITUTION``; a closing speed of ``RAM_CRASH_V`` or more
crashes the aircraft (``crash_reason = "collision"``), below it the aircraft bounces and its attitude is
disturbed by the off-centre push.

COLLIDE: a contact applied here is sent to the peer that flies the other aircraft, with this aircraft's
velocity and whether it crashed. The receiver re-checks it against its own state before it applies the same
rule to its aircraft: the sender flies the named aircraft (its last STATE), the packet is younger than
``MAX_SKEW_S`` (sender clock difference plus half the round trip), the two aircraft were within
``2 * CRAFT_RADIUS + VERIFY_MARGIN_M`` of each other at the contact time on this terminal's own view, and the
sender keeps under ``COLLIDE_RATE`` per aircraft. A contact that both sides found themselves is applied
once per side (``PAIR_COOLDOWN_S``). It answers with a REPLY (never answered again) so that the sender learns
whether this aircraft crashed. A COLLIDE with CRASHED credits a ram to this terminal's aircraft once per
contact, as HIT credits hits.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import arena as frame
from . import wire

try:  # WP-A (07 §2): the shared collision functions
    from ..sim import collide
except ImportError:  # a tree without WP-A's module: the stand-in with the same seam
    from . import collide_fallback as collide  # type: ignore[no-redef]

try:  # WP-A: the fleet thread's collision world and its external body record
    from ..sim.world import ExternalBody  # type: ignore[import-not-found]
except ImportError:

    @dataclass
    class ExternalBody:  # type: ignore[no-redef]
        """A peer's aircraft as a moving sphere in the local frame across one fleet tick."""

        key: str
        p0: tuple[float, float, float]
        p1: tuple[float, float, float]
        v: tuple[float, float, float]
        label: str


if TYPE_CHECKING:
    from .game import Arena
    from .service import Peer

LEAD_MAX_S = 0.15
MAX_SKEW_S = 0.3  # the same bound as MAX_SHOT_AGE_S
VERIFY_MARGIN_M = 0.3
COLLIDE_RATE = 4.0  # COLLIDE packets per peer aircraft and second (burst of the same size)
PAIR_COOLDOWN_S = 0.5
STATE_REACH_S = 0.5  # how far a peer's last STATE is carried forward for the check
CONTACTS_SEEN = 512
KICK_GAIN = 0.3
KICK_MAX = 8.0  # rad/s
CRASH_REASON = "collision"

Vec3 = tuple[float, float, float]


def _sub(a: Any, b: Any) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _unit(d: Vec3) -> Vec3 | None:
    n = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])
    if n < 1e-9:
        return None
    return (d[0] / n, d[1] / n, d[2] / n)


def rate_kick(offset: Vec3, dv: Vec3, heading: float) -> Vec3:
    """Body-rate disturbance (roll, pitch, yaw rate in rad/s) of a push ``dv`` at ``offset`` from centre."""
    w = (
        offset[1] * dv[2] - offset[2] * dv[1],
        offset[2] * dv[0] - offset[0] * dv[2],
        offset[0] * dv[1] - offset[1] * dv[0],
    )
    scale = KICK_GAIN / (collide.CRAFT_RADIUS * collide.CRAFT_RADIUS)
    fx, fy = math.sin(heading), math.cos(heading)  # nose (ENU)
    rx, ry = math.cos(heading), -math.sin(heading)  # right wing
    out = (
        (w[0] * fx + w[1] * fy) * scale,
        (w[0] * rx + w[1] * ry) * scale,
        -w[2] * scale,
    )
    return tuple(max(-KICK_MAX, min(KICK_MAX, c)) for c in out)  # type: ignore[return-value]


def apply_contact(sim: Any, dv: Vec3, kick: Vec3, crash: bool) -> None:
    """Hand a contact to the flight model: ``Quad.collide`` (WP-A), else impulse and shoot-down (v0.4)."""
    quad = sim.quad
    fn = getattr(quad, "collide", None)
    if fn is not None:
        fn(dv, kick, CRASH_REASON if crash else None)
        return
    quad.impulse(dv, (kick[0] * 0.05, kick[1] * 0.05))
    if crash:
        sim.shoot_down(CRASH_REASON)


@dataclass
class _Pending:
    vid: str
    key: str
    at: float


class LanCollisions:
    def __init__(self, arena: Arena) -> None:
        self.arena = arena
        self.external: dict[str, ExternalBody] = {}
        self._last: dict[str, Vec3] = {}  # key -> previous tick's local position
        self._prev_local: dict[str, Vec3] = {}  # local vid -> previous tick's position (own sweep)
        self._pairs: dict[tuple[str, str], float] = {}  # (local vid, remote key) -> applied at
        self._seen: OrderedDict[tuple[int, int], bool] = OrderedDict()  # (peer, contact) -> credited
        self._rate: dict[tuple[int, int], tuple[float, float]] = {}
        self._contact_seq = 0
        self._hooked: Any = None
        self.counters = {"sent": 0, "applied": 0, "late": 0, "far": 0, "unknown": 0, "rate_limited": 0}

    # ------------------------------------------------------------------ helpers

    @property
    def fleet(self) -> Any:
        return self.arena.fleet

    def _world(self) -> Any:
        world = getattr(self.fleet, "world", None)
        if world is None or not hasattr(world, "external"):
            return None
        return world

    def clear(self) -> None:
        self.external = {}
        self._last.clear()
        self._pairs.clear()
        self._rate.clear()
        world = self._world()
        if world is not None:
            world.external = {}

    def forget_peer(self, instance: int) -> None:
        prefix = f"{instance:016x}/"
        for key in [k for k in self._last if k.startswith(prefix)]:
            del self._last[key]
        for key in [k for k in self._rate if k[0] == instance]:
            del self._rate[key]

    # ------------------------------------------------------------------ the tick

    def tick(self, sims: dict[str, Any], now: float) -> None:
        self.external = self._bodies(now)
        for pair in [p for p, t in self._pairs.items() if now - t > PAIR_COOLDOWN_S]:
            del self._pairs[pair]
        world = self._world()
        if world is not None:
            world.external = self.external
            if self._hooked is not world:
                world.on_external_contact = self.on_contact
                self._hooked = world
            return
        self._sweep(sims, now)

    def _bodies(self, now: float) -> dict[str, ExternalBody]:
        arena, peers = self.arena, self.arena.peers
        out: dict[str, ExternalBody] = {}
        if arena.seat is None or peers is None:
            self._last.clear()
            return out
        for peer in peers.peers.values():
            if not peer.joined or peer.seat is None or peer.version < wire.VERSION:
                continue
            lead = min(LEAD_MAX_S, max(0.0, now - peer.state_at))
            for a in peer.aircraft.values():
                key = f"{peer.key}/sim-{a.index}"
                p = frame.peer_to_local(a.p, peer.seat, arena.seat)
                v = frame.peer_vec_to_local(a.v, peer.seat, arena.seat)
                p1 = (p[0] + v[0] * lead, p[1] + v[1] * lead, max(0.0, p[2] + v[2] * lead))
                p0 = self._last.get(key, p1)
                if math.dist(p0, p1) > 5.0:
                    p0 = p1  # a respawn or a seat change, not a motion
                out[key] = ExternalBody(key, p0, p1, v, f"{peer.callsign} / SIM-{a.index}")
        self._last = {k: b.p1 for k, b in out.items()}
        return out

    def _sweep(self, sims: dict[str, Any], now: float) -> None:
        """Without WP-A's world: one swept test per fleet tick between local aircraft and remote spheres."""
        positions: dict[str, Vec3] = {}
        for vid, v in sims.items():
            s = v.driver.sim.quad.s
            ox, oy, oz = v.frame_offset
            positions[vid] = (s.p[0] + ox, s.p[1] + oy, s.p[2] + oz)
        if self.external:
            reach = 2.0 * collide.CRAFT_RADIUS
            for vid, a1 in positions.items():
                v = sims[vid]
                s = v.driver.sim.quad.s
                if s.crashed and s.on_ground:
                    continue
                a0 = self._prev_local.get(vid, a1)
                for key, body in self.external.items():
                    if (vid, key) in self._pairs:
                        continue
                    if math.dist(a1, body.p1) > reach + math.dist(a0, a1) + math.dist(body.p0, body.p1) + 0.5:
                        continue
                    t = collide.sweep_spheres(a0, a1, body.p0, body.p1, reach)
                    if t is None:
                        continue
                    ca = tuple(a0[i] + (a1[i] - a0[i]) * t for i in range(3))
                    cb = tuple(body.p0[i] + (body.p1[i] - body.p0[i]) * t for i in range(3))
                    n = _unit(_sub(ca, cb))
                    if n is None:
                        continue
                    dv, _dvb, closing = collide.response(tuple(s.v), body.v, n, 1.0, 1.0)
                    if closing <= 0.0:
                        continue
                    crash = closing >= collide.RAM_CRASH_V
                    point = tuple(cb[i] + n[i] * collide.CRAFT_RADIUS for i in range(3))
                    offset = _sub(point, ca)
                    apply_contact(v.driver.sim, dv, rate_kick(offset, dv, s.yaw), crash)
                    self.on_contact(key, vid, point, closing, crash)
        self._prev_local = positions

    # ------------------------------------------------------------------ contacts

    def on_contact(self, key: str, local_vid: str, point: Any, v_rel: float, crashed: bool) -> None:
        """A contact between a local aircraft and a peer's, already applied to the local one (WP-A's hook
        ``World.on_external_contact`` or this module's own sweep): tell the peer and the event log."""
        now = self.fleet.clock()
        pair = (local_vid, key)
        if pair in self._pairs:
            return  # the same contact, reported again while the spheres still touch
        self._pairs[pair] = now
        peer, index = self._peer_of(key)
        v = self.fleet.vehicles.get(local_vid)
        if peer is None or index is None or v is None:
            return
        self.counters["applied"] += 1
        self._send(peer, index, v, point, v_rel, crashed, reply=False, now=now)
        self._emit(local_vid, v, key, point, v_rel, crashed)

    def _peer_of(self, key: str) -> tuple[Peer | None, int | None]:
        peers = self.arena.peers
        if peers is None or "/sim-" not in key:
            return None, None
        head, _, tail = key.partition("/sim-")
        try:
            instance, index = int(head, 16), int(tail)
        except ValueError:
            return None, None
        return peers.peers.get(instance), index

    def _send(
        self,
        peer: Peer,
        target: int,
        v: Any,
        point: Any,
        v_rel: float,
        crashed: bool,
        *,
        reply: bool,
        now: float,
        at: float | None = None,
    ) -> None:
        peers, seat = self.arena.peers, self.arena.seat
        index = _sim_index(v.id)
        if peers is None or not peers.ok or seat is None or index is None:
            return
        s = v.driver.sim.quad.s
        self._contact_seq = (self._contact_seq + 1) & 0xFFFFFFFF
        flags = (wire.COLLIDE_CRASHED if crashed else 0) | (wire.COLLIDE_REPLY if reply else 0)
        peers.send_collide(
            wire.Collide(
                to_instance=peer.instance,
                contact=self._contact_seq,
                target=target,
                own=index,
                flags=flags,
                t_ms=peers.mono_ms(now if at is None else at),  # a reply names the contact's time
                point=frame.to_arena(seat, point),
                v=frame.vec_to_arena(seat, tuple(s.v)),
                closing=max(0.0, float(v_rel)),
            )
        )
        self.counters["sent"] += 1

    def _emit(self, vid: str, v: Any, key: str, point: Any, v_rel: float, crashed: bool) -> None:
        body = self.external.get(key)
        label = body.label if body is not None else key
        self.fleet.events.emit(
            "collision",
            vid,
            data={
                "a": vid,
                "b": key,
                "b_label": label,
                "point": [round(float(c), 3) for c in point],
                "v_rel": round(float(v_rel), 2),
                "crashed": [vid] if crashed else [],
                "kind": "craft",
                "remote": True,
                "detail_en": f"collided with {label} at {v_rel:.1f} m/s",
                "detail_zh": f"与 {label} 相撞，相对速度 {v_rel:.1f} m/s",
            },
            labels={"vehicle": v.label},
        )

    # ------------------------------------------------------------------ COLLIDE from a peer

    def on_packet(self, peer: Peer, c: wire.Collide, age: float, now: float) -> None:
        arena = self.arena
        if arena.seat is None or peer.seat is None or peer.version < wire.VERSION:
            return
        sender = peer.aircraft.get(c.own)
        vid = f"sim-{c.target}"
        v = self.fleet.vehicles.get(vid)
        if sender is None or v is None or v.kind != "sim":
            self.counters["unknown"] += 1
            return
        if not 0.0 <= age <= MAX_SKEW_S:
            self.counters["late"] += 1
            return
        bucket = (peer.instance, c.own)
        tokens, at = self._rate.get(bucket, (COLLIDE_RATE, now))
        tokens = min(COLLIDE_RATE, tokens + max(0.0, now - at) * COLLIDE_RATE)
        if tokens < 1.0:
            self._rate[bucket] = (tokens, now)
            self.counters["rate_limited"] += 1
            return
        self._rate[bucket] = (tokens - 1.0, now)
        contact = (peer.instance, c.contact)
        if contact in self._seen:
            return
        # this terminal's own view at the contact time: our aircraft moved back by the age, theirs forward
        s = v.driver.sim.quad.s
        ox, oy, oz = v.frame_offset
        mine = (s.p[0] + ox - s.v[0] * age, s.p[1] + oy - s.v[1] * age, s.p[2] + oz - s.v[2] * age)
        tc = now - age
        carry = max(-STATE_REACH_S, min(STATE_REACH_S, tc - peer.state_at))
        p = frame.peer_to_local(sender.p, peer.seat, arena.seat)
        vel = frame.peer_vec_to_local(sender.v, peer.seat, arena.seat)
        theirs = (p[0] + vel[0] * carry, p[1] + vel[1] * carry, p[2] + vel[2] * carry)
        if math.dist(mine, theirs) >= 2.0 * collide.CRAFT_RADIUS + VERIFY_MARGIN_M:
            self.counters["far"] += 1
            return
        self._seen[contact] = True
        while len(self._seen) > CONTACTS_SEEN:
            self._seen.popitem(last=False)
        if c.crashed:
            arena.board.ram(vid)  # their aircraft crashed from the contact with ours
        key = f"{peer.key}/sim-{c.own}"
        if (vid, key) in self._pairs:
            return  # found and applied here too: the peer has our COLLIDE already
        self._pairs[(vid, key)] = now
        if s.crashed and s.on_ground:
            return
        v_theirs = frame.vec_from_arena(arena.seat, c.v)
        n = _unit(_sub(mine, theirs)) or _unit(_sub(tuple(s.v), v_theirs)) or (0.0, 0.0, 1.0)
        dv, _dvb, closing = collide.response(tuple(s.v), v_theirs, n, 1.0, 1.0)
        crash = closing >= collide.RAM_CRASH_V
        point = frame.from_arena(arena.seat, c.point)
        here = (s.p[0] + ox, s.p[1] + oy, s.p[2] + oz)
        apply_contact(v.driver.sim, dv, rate_kick(_sub(point, here), dv, s.yaw), crash)
        self.counters["applied"] += 1
        if not c.reply:
            self._send(peer, c.own, v, point, closing, crash, reply=True, now=now, at=tc)
        self._emit(vid, v, key, point, closing, crash)


def _sim_index(vid: str) -> int | None:
    if not vid.startswith("sim-"):
        return None
    try:
        n = int(vid[4:])
    except ValueError:
        return None
    return n if 1 <= n <= wire.MAX_AIRCRAFT else None


__all__ = ["ExternalBody", "LanCollisions", "apply_contact", "rate_kick"]
