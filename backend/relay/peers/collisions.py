"""Collisions with peers' aircraft (07 §2 "局域网", WP-B), on the fleet thread as part of the arena.

Every terminal judges only its own aircraft, as with bullets (05 §7.3). Each tick the remote aircraft of
version 2 peers become moving spheres in this terminal's local frame: from the previous tick's position to the
latest STATE position carried forward by its age (at most ``LEAD_MAX_S``, the 0.15 s lead of the view). A
0.4 peer's aircraft never collide: it would not hear about it.

Detection is WP-A's collision world (``fleet.world``): the arena replaces ``world.external`` every tick and
the world sweeps the remote spheres at 250 Hz with the local aircraft, applies the contact to the local
aircraft (``World.apply_contact``), reports it as a ``collision`` event and calls ``on_contact``, which sends
the COLLIDE. All volumes and rules are ``relay.sim.collide``.

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
from typing import TYPE_CHECKING, Any

from ..sim import collide
from ..sim.physics import MASS
from ..sim.world import Body, ExternalBody
from . import arena as frame
from . import wire

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

Vec3 = tuple[float, float, float]


def _sub(a: Any, b: Any) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _unit(d: Vec3) -> Vec3 | None:
    n = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])
    if n < 1e-9:
        return None
    return (d[0] / n, d[1] / n, d[2] / n)


class LanCollisions:
    def __init__(self, arena: Arena) -> None:
        self.arena = arena
        self.external: dict[str, ExternalBody] = {}
        self._last: dict[str, Vec3] = {}  # key -> previous tick's local position
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
        """Hand this tick's remote spheres to the world, which sweeps them during the next fleet step."""
        self.external = self._bodies(now)
        for pair in [p for p, t in self._pairs.items() if now - t > PAIR_COOLDOWN_S]:
            del self._pairs[pair]
        world = self._world()
        if world is None:
            return
        world.external = self.external
        if self._hooked is not world:
            world.on_external_contact = self.on_contact
            self._hooked = world

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

    # ------------------------------------------------------------------ contacts

    def on_contact(self, key: str, local_vid: str, point: Any, v_rel: float, crashed: bool) -> None:
        """A contact between a local aircraft and a peer's, already applied to the local one and reported as a
        ``collision`` event by the collision world (``World.on_external_contact``): tell the peer."""
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

    def _emit(self, vid: str, v: Any, key: str, label: str, point: Any, v_rel: float, crashed: bool) -> None:
        """A verified COLLIDE applied here: the same ``collision`` event as a contact the world found."""
        self.fleet._emit_collision(
            {
                "a": vid,
                "a_label": v.label,
                "b": key,
                "b_label": label,
                "point": [round(float(c), 3) for c in point],
                "v_rel": round(float(v_rel), 3),
                "crashed": [vid] if crashed else [],
                "kind": "craft",
                "remote": True,
            }
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
        world = self._world()
        if world is None:
            return
        v_theirs = frame.vec_from_arena(arena.seat, c.v)
        n = _unit(_sub(mine, theirs)) or _unit(_sub(tuple(s.v), v_theirs)) or (0.0, 0.0, 1.0)
        mass = world._mass(v.driver.sim)
        dv, _dvb, closing = collide.response(tuple(s.v), v_theirs, n, mass, MASS)  # as the world does
        crash = collide.crashes(closing)
        crashed = crash and not s.crashed
        point = frame.from_arena(arena.seat, c.point)
        body = self.external.get(key)
        label = body.label if body is not None else f"{peer.callsign} / SIM-{c.own}"
        here = Body(vid, v.label, v.driver.sim, tuple(v.frame_offset))
        world.apply_contact(here, dv, n, point, closing, key, label, crash, now)
        self.counters["applied"] += 1
        if not c.reply:
            self._send(peer, c.own, v, point, closing, crashed, reply=True, now=now, at=tc)
        self._emit(vid, v, key, label, point, closing, crashed)


def _sim_index(vid: str) -> int | None:
    if not vid.startswith("sim-"):
        return None
    try:
        n = int(vid[4:])
    except ValueError:
        return None
    return n if 1 <= n <= wire.MAX_AIRCRAFT else None


__all__ = ["ExternalBody", "LanCollisions"]
