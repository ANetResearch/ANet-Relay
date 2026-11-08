"""Scenario parameters of the headless evaluator (06 §3.3).

A ``Scenario`` is everything a run needs besides the programs: the airframe of every aircraft (an airframe
body ``{preset | params, seed}`` as ``PUT /sims/{vid}/airframe`` takes it), the scenario sensors (barometer,
position source, contacts), the arena box, hover assist (always off in challenges and duels, 05 §5), the
turbulence strength, the duration and the spawn points. A ``Slot`` is one aircraft of a run with the program
it flies; its seed drives ``ctx.rng`` and the sensor noise.

Coordinates are the fleet's world ENU frame in metres (x east, y north, z up) with the arena centre at the
origin; a spawn's ``yaw_deg`` is the compass heading the aircraft faces on the ground (0 north, 90 east).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

from ..programs import obs as obsmod
from ..sim.airframe import GENERATED

DUEL_DURATION_S = 60.0
DUEL_DURATION_LIMITS = (10.0, 180.0)
DUEL_ARENA = {"radius": 8.0, "floor": 0.0, "ceiling": 4.0}
DUEL_SENSORS = {"baro": True, "position": "none", "contacts": True}
ARENA_LIMITS = {"radius": (3.0, 30.0), "floor": (0.0, 2.0), "ceiling": (2.0, 10.0)}
SPAWN_RING_M = 4.0


@dataclass(frozen=True)
class Spawn:
    x: float = 0.0
    y: float = 0.0
    yaw_deg: float = 0.0

    def view(self) -> dict[str, float]:
        return {"x": round(self.x, 3), "y": round(self.y, 3), "yaw_deg": round(self.yaw_deg % 360.0, 2)}


@dataclass
class Slot:
    """One aircraft of a run: the program it flies and where and how it starts."""

    program: str
    rev: int
    name: str
    source: str
    params: dict[str, Any] = field(default_factory=dict)
    role: str = "player"  # player | opponent | target | entrant
    team: str | None = None
    airframe: dict[str, Any] | None = None
    spawn: Spawn = field(default_factory=Spawn)
    seed: int = 0
    sensors: dict[str, Any] | None = None  # None: the scenario's sensors
    vid: str = ""  # assigned by the evaluator

    def meta(self) -> dict[str, Any]:
        return {"program": self.program, "rev": self.rev, "name": self.name, "role": self.role}


@dataclass
class Scenario:
    id: str
    seed: int
    duration_s: float
    sensors: dict[str, Any] = field(
        default_factory=lambda: {"baro": True, "position": "none", "contacts": True}
    )
    arena: dict[str, float] | None = None  # the box programs see as obs.arena; leaving it stops the aircraft
    guns: bool = False  # the arena game (05 §7): fire, bullets, hit points
    hover_assist: bool = False
    turbulence: float = 0.0
    course: str | None = None  # a ring course id (03 §8) judged by the fleet's own course runner
    markers: list[dict[str, Any]] = field(default_factory=list)  # waypoints and the like, for replay

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "duration_s": self.duration_s,
            "sensors": dict(self.sensors),
            "arena": dict(self.arena) if self.arena else None,
            "guns": self.guns,
            "hover_assist": self.hover_assist,
            "turbulence": self.turbulence,
            "course": self.course,
            "markers": [dict(m) for m in self.markers],
        }


def entrant_seed(seed: int, index: int) -> int:
    """The program seed of slot ``index`` in a run with scenario seed ``seed`` (stable across versions)."""
    return (int(seed) * 1_000_003 + 7919 * (index + 1)) % (2**31 - 1)


def ring_spawns(count: int, seed: int, radius: float = SPAWN_RING_M) -> list[Spawn]:
    """``count`` spawns evenly on a circle around the centre, facing it, rotated by the seed."""
    rng = random.Random(f"spawns:{seed}:{count}")
    start = rng.uniform(0.0, 2.0 * math.pi)
    out = []
    for i in range(count):
        a = start + 2.0 * math.pi * i / count  # math angle from east, counter-clockwise
        x, y = radius * math.cos(a), radius * math.sin(a)
        heading = math.degrees(math.atan2(-x, -y)) % 360.0  # compass heading towards the centre
        out.append(Spawn(round(x, 3), round(y, 3), round(heading, 2)))
    return out


def _number(raw: dict[str, Any], key: str, lo: float, hi: float) -> float:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"scenario.{key} must be a number")
    if not lo <= float(value) <= hi:
        raise ValueError(f"scenario.{key} must be {lo:g}-{hi:g}")
    return float(value)


@dataclass
class DuelSettings:
    """``POST /duels`` ``scenario`` (all optional): duration, arena, airframe preset, sensors, turbulence."""

    duration_s: float = DUEL_DURATION_S
    arena: dict[str, float] = field(default_factory=lambda: dict(DUEL_ARENA))
    airframe: str = "typical"
    sensors: dict[str, Any] = field(default_factory=lambda: dict(DUEL_SENSORS))
    turbulence: float = 0.0

    @classmethod
    def parse(cls, raw: Any) -> DuelSettings:
        out = cls()
        if raw is None:
            return out
        if not isinstance(raw, dict):
            raise ValueError("scenario must be an object")
        if raw.get("duration_s") is not None:
            out.duration_s = _number(raw, "duration_s", *DUEL_DURATION_LIMITS)
        if raw.get("arena") is not None:
            box = obsmod.parse_arena(raw["arena"])
            assert box is not None
            for key, (lo, hi) in ARENA_LIMITS.items():
                if not lo <= box[key] <= hi:
                    raise ValueError(f"scenario.arena.{key} must be {lo:g}-{hi:g}")
            if box["ceiling"] - box["floor"] < 2.0:
                raise ValueError("scenario.arena needs at least 2 m between floor and ceiling")
            out.arena = box
        if raw.get("airframe") is not None:
            if raw["airframe"] not in GENERATED:
                raise ValueError("scenario.airframe must be ideal, typical or rough")
            out.airframe = raw["airframe"]
        if raw.get("sensors") is not None:
            spec = obsmod.Sensors.parse(raw["sensors"])
            out.sensors = {
                "baro": True if spec.baro is None else spec.baro,
                "position": spec.position or "none",
                "contacts": spec.contacts,
            }
        if raw.get("turbulence") is not None:
            out.turbulence = _number(raw, "turbulence", 0.0, 1.0)
        return out

    def view(self) -> dict[str, Any]:
        return {
            "duration_s": self.duration_s,
            "arena": dict(self.arena),
            "airframe": self.airframe,
            "sensors": dict(self.sensors),
            "turbulence": self.turbulence,
        }


def duel_scenario(
    settings: DuelSettings, seed: int, slots: list[Slot], scenario_id: str = "duel"
) -> Scenario:
    """A free-for-all round: every slot on the spawn ring, each with its own seeded airframe."""
    spawns = ring_spawns(len(slots), seed, min(SPAWN_RING_M, settings.arena["radius"] * 0.5))
    order = list(range(len(slots)))
    random.Random(f"order:{seed}").shuffle(order)
    for i, slot in enumerate(slots):
        slot.spawn = spawns[order[i]]
        slot.seed = entrant_seed(seed, i)
        slot.airframe = {"preset": settings.airframe, "seed": entrant_seed(seed, i) % 1_000_000}
        slot.team = slot.team or f"slot-{i + 1}"
    return Scenario(
        id=scenario_id,
        seed=seed,
        duration_s=settings.duration_s,
        sensors=dict(settings.sensors),
        arena=dict(settings.arena),
        guns=True,
        turbulence=settings.turbulence,
    )


__all__ = [
    "DuelSettings",
    "Scenario",
    "Slot",
    "Spawn",
    "duel_scenario",
    "entrant_seed",
    "ring_spawns",
]
