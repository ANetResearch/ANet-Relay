"""The collision world of the local simulators (07 §2): lockstep physics with swept contacts.

The fleet thread calls ``World.step(bodies, rings, now)`` once per tick, before the simulator drivers
emit their telemetry. It advances every simulated aircraft to ``now`` together, one 250 Hz physics step
at a time, and after each step sweeps every aircraft's segment (previous centre to new centre) against

* the other local simulators (moving spheres, both respond),
* the solid rings of the active ring courses (tori),
* ``external``: the remote aircraft of the LAN arena, which the arena fills every tick in this terminal's
  local frame (``ExternalBody``, the segment it covers during the tick); only the local aircraft responds
  and ``on_external_contact`` is called so the arena can tell the other terminal (``COLLIDE``), with the
  local aircraft's velocity from before the contact (the peer applies the same rule to its own aircraft).

The ground is judged by ``physics.Quad`` itself at the same rate. Contact rules and volumes are those of
``sim.collide``. Real aircraft are never bodies here. Coordinates are the fleet's world ENU frame (the
simulator position plus the vehicle's frame offset).

``step`` returns the contact reports for the fleet to publish (``collision`` events) and keeps the last
contact of every aircraft for its program (``obs.collision``, 06 §2.2). Nothing here reads a clock or a
random source: the same inputs give the same contacts.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import collide
from .physics import DT, IXX, IYY, IZZ, MASS

Vec3 = tuple[float, float, float]

PAIR_DISTANCE = 2.0 * collide.CRAFT_RADIUS
EVENT_GAP_S = 0.25  # one report per pair while a bounce settles; a crash is always reported
REPORT_V = 0.2  # m/s: slower contacts (one aircraft resting on another) are resolved but not reported
CONTACT_MEMORY_S = 1.0  # how long obs.collision shows a contact
_INERTIA = (IXX, IYY, IZZ)


@dataclass
class ExternalBody:
    """A remote aircraft for one fleet tick: centre ``p0`` at the tick's start, ``p1`` at its end (local
    frame), velocity ``v`` (ENU m/s), and the label to show (``callsign / SIM-n``)."""

    key: str
    p0: Vec3
    p1: Vec3
    v: Vec3
    label: str = ""


@dataclass
class Body:
    """A local simulator in the world: ``sim`` is its ``SimAutopilot``, ``offset`` its frame offset."""

    id: str
    label: str
    sim: Any
    offset: Vec3 = (0.0, 0.0, 0.0)


@dataclass
class Contact:
    """The last contact of an aircraft: when, with what, closing speed and the normal (ENU, toward it)."""

    t: float
    other: str
    v_rel: float
    normal: Vec3
    point: Vec3


@dataclass
class World:
    external: dict[str, ExternalBody] = field(default_factory=dict)
    # (remote key, local id, point, closing speed, crashed, local velocity before the contact)
    on_external_contact: Callable[[str, str, Vec3, float, bool, Vec3], None] | None = None
    fixed_rings: list[tuple[str, dict[str, Any]]] = field(default_factory=list)  # e.g. the evaluator's course
    enabled: bool = True
    contacts: dict[str, Contact] = field(default_factory=dict)
    _reported: dict[tuple[str, str], float] = field(default_factory=dict)
    _last: float | None = None

    # ------------------------------------------------------------------ queries

    def last_contact(self, vid: str, now: float) -> Contact | None:
        c = self.contacts.get(vid)
        if c is None or now - c.t > CONTACT_MEMORY_S:
            return None
        return c

    def forget(self, vid: str) -> None:
        self.contacts.pop(vid, None)
        for key in [k for k in self._reported if vid in k]:
            del self._reported[key]

    # ------------------------------------------------------------------ the step

    def step(
        self, bodies: Sequence[Body], rings: Sequence[tuple[str, dict[str, Any]]], now: float
    ) -> list[dict[str, Any]]:
        """Advance ``bodies`` to ``now`` in lockstep and resolve contacts; returns the contact reports."""
        last = self._last if self._last is not None else now
        self._last = now
        counts = [b.sim.pending_steps(now) for b in bodies]
        steps = max(counts, default=0)
        reports: list[dict[str, Any]] = []
        if steps == 0:
            return reports
        solid = [(key, ring, *collide.ring_frame(ring)) for key, ring in (*rings, *self.fixed_rings)]
        externals = list(self.external.values()) if self.enabled else []
        span = max(1e-9, now - last)
        for k in range(steps):
            moved: list[tuple[Body, Vec3, Vec3]] = []
            for b, count in zip(bodies, counts, strict=True):
                if count <= k:
                    continue
                s = b.sim.quad.s
                ox, oy, oz = b.offset
                p0 = (s.p[0] + ox, s.p[1] + oy, s.p[2] + oz)
                b.sim.physics_step()
                moved.append((b, p0, (s.p[0] + ox, s.p[1] + oy, s.p[2] + oz)))
            if not self.enabled or not moved:
                continue
            t_step = moved[0][0].sim.t
            if len(moved) > 1:
                self._pairs(moved, t_step, reports)
            if solid:
                self._rings(moved, solid, t_step, reports)
            if externals:
                # the part of the remote aircraft's tick segment this physics step covers
                f0 = max(0.0, min(1.0, 1.0 - (steps - k) * DT / span))
                f1 = max(0.0, min(1.0, 1.0 - (steps - k - 1) * DT / span))
                self._externals(moved, externals, f0, f1, t_step, reports)
        return reports

    # ------------------------------------------------------------------ contacts

    @staticmethod
    def _mass(sim: Any) -> float:
        af = sim.quad.active_airframe()
        return af.mass if af is not None else MASS

    def apply_contact(
        self,
        body: Body,
        dv: Vec3,
        n: Vec3,
        point: Vec3,
        closing: float,
        other: str,
        other_label: str,
        crash: bool,
        t: float,
    ) -> None:
        """Apply one contact to a local aircraft: velocity change ``dv`` (ENU), the rim rate kick, a crash at
        or above the threshold, and the record ``obs.collision`` shows. The LAN arena uses it for a remote
        terminal's ``COLLIDE`` once it has checked it against its own state (07 §2)."""
        quad = body.sim.quad
        s = quad.s
        kick = collide.rate_kick(dv, n, s.q, self._mass(body.sim), _INERTIA)
        ox, oy, oz = body.offset
        local = (point[0] - ox, point[1] - oy, point[2] - oz)
        reason = f"collision with {other_label or other}" if crash else None
        if crash and not s.crashed:
            body.sim.assist_ramp = None
        quad.collide(dv, kick, reason, point=local)
        if closing >= REPORT_V or crash:
            self.contacts[body.id] = Contact(t, other, closing, n, point)

    def _report(
        self,
        reports: list[dict[str, Any]],
        a: Body,
        b: str,
        b_label: str,
        point: Vec3,
        closing: float,
        crashed: list[str],
        kind: str,
        t: float,
        remote: bool = False,
    ) -> None:
        if closing < REPORT_V and not crashed:
            return
        key = (a.id, b) if a.id < b else (b, a.id)
        last = self._reported.get(key)
        if not crashed and last is not None and t - last < EVENT_GAP_S:
            return
        self._reported[key] = t
        reports.append(
            {
                "a": a.id,
                "a_label": a.label,
                "b": b,
                "b_label": b_label,
                "point": [round(c, 3) for c in point],
                "v_rel": round(closing, 3),
                "crashed": crashed,
                "kind": kind,
                **({"remote": True} if remote else {}),
            }
        )

    def _pairs(self, moved: list[tuple[Body, Vec3, Vec3]], t: float, reports: list[dict[str, Any]]) -> None:
        for i in range(len(moved)):
            a, a0, a1 = moved[i]
            sa = a.sim.quad.s
            for j in range(i + 1, len(moved)):
                b, b0, b1 = moved[j]
                sb = b.sim.quad.s
                if (sa.on_ground and sb.on_ground) or (sa.crashed and sb.crashed):
                    continue  # resting on the ground, or two wrecks
                hit = collide.sweep_spheres(a0, a1, b0, b1, PAIR_DISTANCE)
                if hit is None:
                    continue
                ca = tuple(a0[c] + (a1[c] - a0[c]) * hit for c in range(3))
                cb = tuple(b0[c] + (b1[c] - b0[c]) * hit for c in range(3))
                n = _unit((ca[0] - cb[0], ca[1] - cb[1], ca[2] - cb[2]))
                if n is None:
                    n = _unit(tuple(sb.v[c] - sa.v[c] for c in range(3)))  # type: ignore[arg-type]
                if n is None:
                    continue
                va, vb = tuple(sa.v), tuple(sb.v)
                ma, mb = self._mass(a.sim), self._mass(b.sim)
                dva, dvb, closing = collide.response(va, vb, n, ma, mb)  # type: ignore[arg-type]
                if closing <= 0.0:
                    continue
                crash = collide.crashes(closing)
                point = tuple(cb[c] + n[c] * collide.CRAFT_RADIUS for c in range(3))
                crashed = [x.id for x, s in ((a, sa), (b, sb)) if crash and not s.crashed]
                self.apply_contact(a, dva, n, point, closing, b.id, b.label, crash, t)  # type: ignore[arg-type]
                neg = (-n[0], -n[1], -n[2])
                self.apply_contact(b, dvb, neg, point, closing, a.id, a.label, crash, t)  # type: ignore[arg-type]
                self._report(reports, a, b.id, b.label, point, closing, crashed, "craft", t)  # type: ignore[arg-type]

    def _rings(
        self,
        moved: list[tuple[Body, Vec3, Vec3]],
        solid: list[tuple[Any, ...]],
        t: float,
        reports: list[dict[str, Any]],
    ) -> None:
        for body, p0, p1 in moved:
            s = body.sim.quad.s
            if s.on_ground:
                continue
            for key, _ring, centre, normal, radius in solid:
                hit = collide.sweep_torus(
                    p0, p1, centre, normal, radius, collide.RING_TUBE, collide.CRAFT_RADIUS
                )
                if hit is None:
                    continue
                _f, point, n = hit
                va = (s.v[0], s.v[1], s.v[2])
                dv, _dvb, closing = collide.response(va, (0.0, 0.0, 0.0), n, self._mass(body.sim), math.inf)
                if closing <= 0.0:
                    continue
                crash = collide.crashes(closing)
                crashed = [body.id] if crash and not s.crashed else []
                label = f"ring {key.split(':', 1)[-1]}"
                self.apply_contact(body, dv, n, point, closing, key, label, crash, t)
                self._report(reports, body, key, label, point, closing, crashed, "ring", t)

    def _externals(
        self,
        moved: list[tuple[Body, Vec3, Vec3]],
        externals: list[ExternalBody],
        f0: float,
        f1: float,
        t: float,
        reports: list[dict[str, Any]],
    ) -> None:
        for e in externals:
            e0 = tuple(e.p0[c] + (e.p1[c] - e.p0[c]) * f0 for c in range(3))
            e1 = tuple(e.p0[c] + (e.p1[c] - e.p0[c]) * f1 for c in range(3))
            for body, a0, a1 in moved:
                s = body.sim.quad.s
                hit = collide.sweep_spheres(a0, a1, e0, e1, PAIR_DISTANCE)  # type: ignore[arg-type]
                if hit is None:
                    continue
                ca = tuple(a0[c] + (a1[c] - a0[c]) * hit for c in range(3))
                cb = tuple(e0[c] + (e1[c] - e0[c]) * hit for c in range(3))
                n = _unit((ca[0] - cb[0], ca[1] - cb[1], ca[2] - cb[2]))
                if n is None:
                    n = _unit(tuple(e.v[c] - s.v[c] for c in range(3)))  # type: ignore[arg-type]
                if n is None:
                    continue
                va = (s.v[0], s.v[1], s.v[2])
                dv, _dvb, closing = collide.response(va, e.v, n, self._mass(body.sim), MASS)
                if closing <= 0.0:
                    continue
                crash = collide.crashes(closing)
                point = tuple(cb[c] + n[c] * collide.CRAFT_RADIUS for c in range(3))
                crashed = [body.id] if crash and not s.crashed else []
                self.apply_contact(body, dv, n, point, closing, e.key, e.label, crash, t)  # type: ignore[arg-type]
                self._report(reports, body, e.key, e.label, point, closing, crashed, "craft", t, remote=True)  # type: ignore[arg-type]
                if self.on_external_contact is not None:  # crashed: this contact wrecked ours (ram credit)
                    self.on_external_contact(e.key, body.id, point, closing, bool(crashed), va)  # type: ignore[arg-type]


def _unit(v: Vec3) -> Vec3 | None:
    m = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if m < 1e-9:
        return None
    return (v[0] / m, v[1] / m, v[2] / m)


__all__ = ["Body", "Contact", "ExternalBody", "World"]
