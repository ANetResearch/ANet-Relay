"""The challenge set (06 §3.2): fixed scenarios with scoring rules, judged on the ground truth.

Every challenge has visible seeds (listed by the API, with replay traces) and hidden seeds (only counted;
their airframes differ, so a program tuned to one seed does not pass by luck). A submission runs every seed;
it passes when every seed passes and scores the mean of the seed scores (0-100, rounded to 0.1).

Times are seconds since the program started (time 0 is the deployment, with the aircraft armed on the
ground at its spawn). Heights are above the take-off point. ``CHALLENGES`` keeps the API order.

=============  ==============================================================================================
``liftoff``    Payload brings the hover throttle to about 0.62; barometer, no position. Pass: off the ground
               within 5 s and then 3 s without a break at 1.0 +- 0.2 m (within 12 s). Score: passed
               70 + 30 * (5 - liftoff_s) / 4 (clamped); otherwise 20 for a liftoff within 5 s plus 40 * the
               longest hold / 3 s.
``hover``      Centre of gravity forward of the middle and a level calibration error; barometer, no position.
               Window 6-26 s. Pass: horizontal drift (from the position at 6 s) at most 0.8 m and height
               1.0 +- 0.25 m throughout. Score: 50 * share of the window in the height band +
               50 * max(0, 1 - drift / 1.6).
``heading``    Unequal motor efficiencies turn the airframe. Window 5-20 s. Pass: heading within 10 degrees of
               the spawn heading and at least 0.4 m up throughout. Score: 70 * share of the window within the
               limits + 30 * max(0, 1 - max_error / 30).
``square``     UWB-like position (10 Hz, sigma 5 cm). Pass: the four corners (``info.corners``, radius 0.4 m)
               in order within 40 s. Score: passed 60 + 40 * (40 - time) / 30 (clamped, time from the
               take-off to the last corner); otherwise 12 per corner.
``rings``      The ``cage-loop`` ring course (03 §8) with the UWB-like position, judged by the course runner
               of ``relay.course`` on the ground truth. Pass: every ring within 60 s. Score: passed
               60 + 40 * (60 - time) / 45 (clamped, the course time from the take-off); otherwise
               50 * rings / total.
``pursuit``    A scripted target circles the arena centre (radius 3 m, 0.5 m/s, 1.2 m up); contacts and the
               UWB-like position. Score: 100 * share of the 60 s within 2 m of the target (3-D, truth).
               Pass: score at least 50.
``duel_*``     One against one with the built-in ``hunter_easy`` / ``hunter_medium`` / ``hunter_hard`` in the
               arena (radius 8 m, ceiling 4 m), 60 s. Pass: a win (the opponent shot down, crashed or out of
               the arena while the player still flies). Score: win 70 + 30 * hp / 100; time up
               60 * hp / (hp + opponent hp); loss 10 * min(hits, 5) / 5.
``ram_easy``   WP-B (07 §3): a target hovers 1.2 m up on its spawn (the other side of the spawn ring);
               barometer and contacts, no position, no gun (the arena is off, so nothing can fire). Pass: the
               target crashes from a collision with the player within 60 s (a ``collision`` event that lists
               it as crashed). Score: passed 60 + 40 * (60 - time) / 50 (clamped); otherwise
               40 * max(0, 1 - closest approach / 4 m).
=============  ==============================================================================================
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..course import PRESETS as COURSES
from ..course import scaled
from ..sim.airframe import generate
from . import target as targetmod
from .duels import RoundScorer
from .headless import Headless, Truth
from .scenario import DUEL_ARENA, Scenario, Slot, Spawn, entrant_seed, ring_spawns

SAMPLE_DT = 0.02


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def _base(name: str, seed: int) -> dict[str, Any]:
    """A typical airframe for ``seed`` as explicit values, to overlay a challenge's defects on."""
    return generate("typical", seed % 1_000_000).params()


def _rng(name: str, seed: int) -> random.Random:
    return random.Random(f"challenge:{name}:{seed}")


def _af(name: str, seed: int, **values: Any) -> dict[str, Any]:
    params = _base(name, seed)
    params.update(values)
    return {"preset": "custom", "seed": seed % 1_000_000, "params": params}


# ============================================================================ scorers


@dataclass
class _Base:
    """Shared bookkeeping: the player's truth each sample, crash and liftoff."""

    crashed_at: float | None = None
    liftoff_at: float | None = None
    max_alt: float = 0.0

    def begin(self, run: Headless) -> None:
        self.player = run.slot("player")

    def sample(self, run: Headless, t: float) -> None:
        tr = run.truth(self.player)
        if tr.crashed and self.crashed_at is None:
            self.crashed_at = t
        if self.liftoff_at is None and tr.airborne and tr.p[2] > 0.05:
            self.liftoff_at = t
            run.mark("liftoff", self.player.vid)
        self.max_alt = max(self.max_alt, tr.p[2])
        self.on_sample(run, t, tr)

    def on_sample(self, run: Headless, t: float, tr: Truth) -> None: ...

    def event(self, run: Headless, t: float, event: dict[str, Any]) -> None: ...

    def finished(self, run: Headless, t: float) -> bool:
        return False

    def common(self) -> dict[str, Any]:
        return {
            "liftoff_s": None if self.liftoff_at is None else round(self.liftoff_at, 2),
            "crashed": self.crashed_at is not None,
            "max_alt_m": round(self.max_alt, 3),
        }


@dataclass
class LiftoffScorer(_Base):
    band: float = 0.2
    target: float = 1.0
    hold_s: float = 3.0
    deadline_s: float = 5.0
    run_from: float | None = None
    hold_best: float = 0.0

    def on_sample(self, run: Headless, t: float, tr: Truth) -> None:
        if self.liftoff_at is None or tr.crashed:
            self.run_from = None
            return
        if abs(tr.p[2] - self.target) <= self.band and tr.airborne:
            if self.run_from is None:
                self.run_from = t
            self.hold_best = max(self.hold_best, t - self.run_from + SAMPLE_DT)
        else:
            self.run_from = None

    def passed(self) -> bool:
        return (
            self.liftoff_at is not None
            and self.liftoff_at <= self.deadline_s
            and self.hold_best >= self.hold_s - 1e-6
            and self.crashed_at is None
        )

    def finished(self, run: Headless, t: float) -> bool:
        return self.passed() or self.crashed_at is not None

    def result(self, run: Headless) -> dict[str, Any]:
        passed = self.passed()
        if passed:
            assert self.liftoff_at is not None
            score = 70.0 + 30.0 * _clamp((self.deadline_s - self.liftoff_at) / 4.0)
        else:
            early = self.liftoff_at is not None and self.liftoff_at <= self.deadline_s
            score = (20.0 if early else 0.0) + 40.0 * min(self.hold_best, self.hold_s) / self.hold_s
        return {
            "score": round(score, 1),
            "passed": passed,
            "metrics": {**self.common(), "hold_s": round(min(self.hold_best, 99.0), 2)},
        }


@dataclass
class HoverScorer(_Base):
    window: tuple[float, float] = (6.0, 26.0)
    target: float = 1.0
    band: float = 0.25
    drift_limit: float = 0.8
    ref: tuple[float, float] | None = None
    drift: float = 0.0
    inside: int = 0
    total: int = 0
    alt_min: float | None = None
    alt_max: float | None = None

    def on_sample(self, run: Headless, t: float, tr: Truth) -> None:
        lo, hi = self.window
        if t < lo - 1e-9 or t > hi + 1e-9:
            return
        if self.ref is None:
            self.ref = (tr.p[0], tr.p[1])
        self.drift = max(self.drift, math.hypot(tr.p[0] - self.ref[0], tr.p[1] - self.ref[1]))
        self.total += 1
        ok = abs(tr.p[2] - self.target) <= self.band and not tr.crashed
        self.inside += 1 if ok else 0
        self.alt_min = tr.p[2] if self.alt_min is None else min(self.alt_min, tr.p[2])
        self.alt_max = tr.p[2] if self.alt_max is None else max(self.alt_max, tr.p[2])

    def finished(self, run: Headless, t: float) -> bool:
        return self.crashed_at is not None

    def result(self, run: Headless) -> dict[str, Any]:
        expected = round((self.window[1] - self.window[0]) / SAMPLE_DT) + 1
        share = self.inside / max(self.total, expected)
        passed = share >= 1.0 - 1e-9 and self.drift <= self.drift_limit and self.crashed_at is None
        drift = self.drift if self.ref is not None else None
        score = 50.0 * share + (50.0 * max(0.0, 1.0 - self.drift / 1.6) if self.ref is not None else 0.0)
        return {
            "score": round(score, 1),
            "passed": passed,
            "metrics": {
                **self.common(),
                "drift_m": None if drift is None else round(drift, 3),
                "in_band": round(share, 3),
                "alt_min_m": None if self.alt_min is None else round(self.alt_min, 3),
                "alt_max_m": None if self.alt_max is None else round(self.alt_max, 3),
            },
        }


@dataclass
class HeadingScorer(_Base):
    window: tuple[float, float] = (5.0, 20.0)
    limit_deg: float = 10.0
    min_alt: float = 0.4
    ref: float | None = None
    ok: int = 0
    total: int = 0
    max_err: float = 0.0

    def on_sample(self, run: Headless, t: float, tr: Truth) -> None:
        if self.ref is None:
            self.ref = math.radians(self.player.spawn.yaw_deg)
        lo, hi = self.window
        if t < lo - 1e-9 or t > hi + 1e-9:
            return
        err = abs((math.degrees(tr.yaw - self.ref) + 180.0) % 360.0 - 180.0)
        self.max_err = max(self.max_err, err)
        self.total += 1
        if err <= self.limit_deg and tr.p[2] >= self.min_alt and not tr.crashed:
            self.ok += 1

    def finished(self, run: Headless, t: float) -> bool:
        return self.crashed_at is not None

    def result(self, run: Headless) -> dict[str, Any]:
        expected = round((self.window[1] - self.window[0]) / SAMPLE_DT) + 1
        share = self.ok / max(self.total, expected)
        passed = share >= 1.0 - 1e-9 and self.crashed_at is None
        score = 70.0 * share + 30.0 * max(0.0, 1.0 - self.max_err / 30.0)
        return {
            "score": round(score, 1),
            "passed": passed,
            "metrics": {
                **self.common(),
                "max_error_deg": round(self.max_err, 2),
                "in_limits": round(share, 3),
            },
        }


@dataclass
class SquareScorer(_Base):
    corners: list[tuple[float, float, float]] = field(default_factory=list)
    radius: float = 0.4
    limit_s: float = 40.0
    reached: list[float] = field(default_factory=list)

    def on_sample(self, run: Headless, t: float, tr: Truth) -> None:
        if len(self.reached) >= len(self.corners) or tr.crashed:
            return
        c = self.corners[len(self.reached)]
        if math.dist(tr.p, c) <= self.radius:
            self.reached.append(t)
            run.mark("corner", self.player.vid, corner=len(self.reached), total=len(self.corners))

    def finished(self, run: Headless, t: float) -> bool:
        return len(self.reached) >= len(self.corners) or self.crashed_at is not None

    def result(self, run: Headless) -> dict[str, Any]:
        n = len(self.reached)
        passed = n == len(self.corners) and self.reached[-1] <= self.limit_s
        elapsed = self.reached[-1] - (self.liftoff_at or 0.0) if passed else None
        if elapsed is not None:
            score = 60.0 + 40.0 * _clamp((self.limit_s - elapsed) / 30.0)
        else:
            score = 12.0 * n
        return {
            "score": round(score, 1),
            "passed": passed,
            "metrics": {
                **self.common(),
                "corners": n,
                "splits_s": [round(t, 2) for t in self.reached],
                "time_s": None if elapsed is None else round(elapsed, 2),
            },
        }


@dataclass
class RingsScorer(_Base):
    limit_s: float = 60.0
    rings: int = 0
    total: int = 0
    time_s: float | None = None
    aborted: str | None = None

    def begin(self, run: Headless) -> None:
        super().begin(run)
        self.course = run.course_run
        self.total = len(self.course.rings) if self.course is not None else 0

    def on_sample(self, run: Headless, t: float, tr: Truth) -> None:
        if self.course is None or not self.course.active:
            return
        if tr.crashed:
            self.course.abort("crashed", t)
            self.aborted = "crashed"
            return
        for kind, data in self.course.update(tr.p, t, t, tr.airborne):
            if kind == "ring_passed":
                self.rings = int(data["ring"])
                run.mark("ring", self.player.vid, ring=self.rings, total=self.total, elapsed=data["elapsed"])
            elif kind == "course_finished":
                self.time_s = float(data["total_s"])
                run.mark("course_finished", self.player.vid, total_s=self.time_s)

    def finished(self, run: Headless, t: float) -> bool:
        return self.time_s is not None or self.aborted is not None or self.crashed_at is not None

    def result(self, run: Headless) -> dict[str, Any]:
        passed = self.time_s is not None and self.time_s <= self.limit_s
        if passed:
            assert self.time_s is not None
            score = 60.0 + 40.0 * _clamp((self.limit_s - self.time_s) / 45.0)
        else:
            score = 50.0 * self.rings / max(1, self.total)
        return {
            "score": round(score, 1),
            "passed": passed,
            "metrics": {
                **self.common(),
                "rings": self.rings,
                "total": self.total,
                "time_s": None if self.time_s is None else round(self.time_s, 3),
                "aborted": self.aborted,
            },
        }


@dataclass
class PursuitScorer(_Base):
    radius: float = 2.0
    pass_share: float = 0.5
    near: int = 0
    total: int = 0
    closest: float | None = None

    def begin(self, run: Headless) -> None:
        super().begin(run)
        self.target = run.slot("target")

    def on_sample(self, run: Headless, t: float, tr: Truth) -> None:
        other = run.truth(self.target)
        d = math.dist(tr.p, other.p)
        self.closest = d if self.closest is None else min(self.closest, d)
        self.total += 1
        if d <= self.radius and not tr.crashed:
            self.near += 1

    def finished(self, run: Headless, t: float) -> bool:
        return self.crashed_at is not None

    def result(self, run: Headless) -> dict[str, Any]:
        expected = round(run.scenario.duration_s / SAMPLE_DT)
        share = self.near / max(self.total, expected)
        return {
            "score": round(100.0 * share, 1),
            "passed": share >= self.pass_share,
            "metrics": {
                **self.common(),
                "share_within": round(share, 3),
                "closest_m": None if self.closest is None else round(self.closest, 3),
            },
        }


@dataclass
class RamScorer(_Base):
    """WP-B (07 §3): the target must crash from a collision with the player."""

    limit_s: float = 60.0
    rammed_at: float | None = None
    v_rel: float | None = None
    closest: float | None = None
    target_crashed: bool = False

    def begin(self, run: Headless) -> None:
        super().begin(run)
        self.target = run.slot("target")

    def on_sample(self, run: Headless, t: float, tr: Truth) -> None:
        other = run.truth(self.target)
        d = math.dist(tr.p, other.p)
        self.closest = d if self.closest is None else min(self.closest, d)
        if other.crashed:
            self.target_crashed = True

    def event(self, run: Headless, t: float, event: dict[str, Any]) -> None:
        if event.get("kind") != "collision" or self.rammed_at is not None:
            return
        data = event.get("data") or {}
        ids = {data.get("a"), data.get("b")}
        if self.target.vid in (data.get("crashed") or ()) and self.player.vid in ids:
            self.rammed_at = t
            self.v_rel = data.get("v_rel")
            run.mark("rammed", self.target.vid, by=self.player.vid)

    def finished(self, run: Headless, t: float) -> bool:
        return self.rammed_at is not None or self.crashed_at is not None or self.target_crashed

    def result(self, run: Headless) -> dict[str, Any]:
        passed = self.rammed_at is not None and self.rammed_at <= self.limit_s
        if passed:
            assert self.rammed_at is not None
            score = 60.0 + 40.0 * _clamp((self.limit_s - self.rammed_at) / 50.0)
        else:
            score = 40.0 * max(0.0, 1.0 - (self.closest if self.closest is not None else 99.0) / 4.0)
        return {
            "score": round(score, 1),
            "passed": passed,
            "metrics": {
                **self.common(),
                "rammed_s": None if self.rammed_at is None else round(self.rammed_at, 2),
                "v_rel": self.v_rel,
                "closest_m": None if self.closest is None else round(self.closest, 3),
            },
        }


class DuelScorer(RoundScorer):
    """A one-against-one round judged from the player's side."""

    def result(self, run: Headless) -> dict[str, Any]:
        rnd = super().result(run)
        player = next(a for a in rnd["aircraft"] if a["role"] == "player")
        other = next(a for a in rnd["aircraft"] if a["role"] == "opponent")
        hp, ohp = player["hp"] or 0, other["hp"] or 0
        if player["alive"] and not other["alive"]:
            outcome, score = "win", 70.0 + 30.0 * hp / 100.0
        elif not player["alive"]:
            outcome = "draw" if not other["alive"] else "loss"
            score = (
                60.0 * hp / (hp + ohp)
                if outcome == "draw" and hp + ohp > 0
                else 10.0 * min(player["hits"], 5) / 5
            )
        else:
            outcome = "time"
            score = 60.0 * hp / (hp + ohp) if hp + ohp > 0 else 0.0
        return {
            "score": round(score, 1),
            "passed": outcome == "win",
            "metrics": {
                "outcome": outcome,
                "hp": hp,
                "opponent_hp": ohp,
                "hits": player["hits"],
                "hits_taken": other["hits"],
                "survived_s": player["survived_s"],
                "opponent_survived_s": other["survived_s"],
                "reason": player["reason"],
                "opponent_reason": other["reason"],
            },
            "round": rnd,
        }


# ============================================================================ the catalogue


@dataclass(frozen=True)
class Challenge:
    id: str
    name_en: str
    name_zh: str
    scene_en: str
    scene_zh: str
    goal_en: str
    goal_zh: str
    scoring_en: str
    scoring_zh: str
    duration_s: float
    visible: tuple[int, ...]
    hidden: tuple[int, ...]
    build: Callable[[int, Slot, Slot | None], tuple[Scenario, list[Slot]]]
    scorer: Callable[[], Any]
    sensors: dict[str, Any]
    info: dict[str, Any] = field(default_factory=dict)
    opponent: str | None = None
    kind: str = "flight"

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name_en": self.name_en,
            "name_zh": self.name_zh,
            "goal_en": self.goal_en,
            "goal_zh": self.goal_zh,
            "scene_en": self.scene_en,
            "scene_zh": self.scene_zh,
            "duration_s": self.duration_s,
            "visible_seeds": list(self.visible),
            "hidden_seeds": len(self.hidden),
            "opponent": self.opponent,
        }

    def detail(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "scoring_en": self.scoring_en,
            "scoring_zh": self.scoring_zh,
            "sensors": dict(self.sensors),
            "hover_assist": False,
            "info": dict(self.info),
        }


BARO_ONLY = {"baro": True, "position": "none", "contacts": False}
UWB = {"baro": True, "position": "uwb", "contacts": False}
UWB_CONTACTS = {"baro": True, "position": "uwb", "contacts": True}
ARENA_SENSORS = {"baro": True, "position": "none", "contacts": True}
CORNERS = [(1.0, 1.0, 1.0), (1.0, -1.0, 1.0), (-1.0, -1.0, 1.0), (-1.0, 1.0, 1.0)]
FLIGHT_ARENA = {"radius": 6.0, "floor": 0.0, "ceiling": 4.0}


def _player(slot: Slot, seed: int, airframe: dict[str, Any], spawn: Spawn | None = None) -> Slot:
    slot.role = "player"
    slot.seed = entrant_seed(seed, 0)
    slot.airframe = airframe
    slot.spawn = spawn or Spawn(0.0, 0.0, 0.0)
    slot.team = "player"
    return slot


def _liftoff(seed: int, player: Slot, _o: Slot | None) -> tuple[Scenario, list[Slot]]:
    rng = _rng("liftoff", seed)
    af = _af("liftoff", seed, payload_kg=round(rng.uniform(0.032, 0.040), 4))
    sc = Scenario("liftoff", seed, 12.0, sensors=dict(BARO_ONLY), arena=dict(FLIGHT_ARENA))
    return sc, [_player(player, seed, af)]


def _hover(seed: int, player: Slot, _o: Slot | None) -> tuple[Scenario, list[Slot]]:
    rng = _rng("hover", seed)
    cg = (round(rng.uniform(0.4, 0.8), 3), round(rng.uniform(-0.15, 0.15), 3))
    trim = (round(rng.uniform(-0.8, 0.8), 2), round(rng.uniform(-0.8, 0.8), 2))
    af = _af("hover", seed, cg_offset_cm=list(cg), level_trim_deg=list(trim), imu_noise=0.5, prop_damage=0.0)
    sc = Scenario("hover", seed, 26.0, sensors=dict(BARO_ONLY), arena=dict(FLIGHT_ARENA))
    return sc, [_player(player, seed, af, Spawn(0.0, 0.0, round(rng.uniform(0.0, 360.0), 1)))]


def _heading(seed: int, player: Slot, _o: Slot | None) -> tuple[Scenario, list[Slot]]:
    rng = _rng("heading", seed)
    a = rng.uniform(0.03, 0.06) * rng.choice((-1.0, 1.0))
    gains = [round(a + rng.uniform(-0.005, 0.005), 3) for _ in range(2)]
    gains += [round(-a + rng.uniform(-0.005, 0.005), 3) for _ in range(2)]
    af = _af("heading", seed, motor_gain=gains, prop_damage=0.0)
    sc = Scenario("heading", seed, 20.0, sensors=dict(BARO_ONLY), arena=dict(FLIGHT_ARENA))
    return sc, [_player(player, seed, af, Spawn(0.0, 0.0, round(rng.uniform(0.0, 360.0), 1)))]


def _square(seed: int, player: Slot, _o: Slot | None) -> tuple[Scenario, list[Slot]]:
    af = _af("square", seed, prop_damage=0.0)
    markers = [{"kind": "corner", "p": list(c), "r": 0.4, "index": i + 1} for i, c in enumerate(CORNERS)]
    sc = Scenario("square", seed, 40.0, sensors=dict(UWB), arena=dict(FLIGHT_ARENA), markers=markers)
    return sc, [_player(player, seed, af)]


def _rings(seed: int, player: Slot, _o: Slot | None) -> tuple[Scenario, list[Slot]]:
    af = _af("rings", seed, prop_damage=0.0)
    sc = Scenario("rings", seed, 60.0, sensors=dict(UWB), arena=dict(FLIGHT_ARENA), course="cage-loop")
    return sc, [_player(player, seed, af)]


def _pursuit(seed: int, player: Slot, _o: Slot | None) -> tuple[Scenario, list[Slot]]:
    af = _af("pursuit", seed, prop_damage=0.0)
    tgt = Slot(
        program=targetmod.PROGRAM_ID,
        rev=1,
        name=targetmod.NAME,
        source=targetmod.SOURCE,
        role="target",
        team="target",
        airframe={"preset": "ideal", "seed": 0},
        spawn=Spawn(3.0, 0.0, 0.0),
        seed=entrant_seed(seed, 1),
        sensors=dict(UWB),
    )
    sc = Scenario("pursuit", seed, 60.0, sensors=dict(UWB_CONTACTS), arena=dict(FLIGHT_ARENA))
    return sc, [_player(player, seed, af), tgt]


def _duel(level: str) -> Callable[[int, Slot, Slot | None], tuple[Scenario, list[Slot]]]:
    def build(seed: int, player: Slot, opponent: Slot | None) -> tuple[Scenario, list[Slot]]:
        assert opponent is not None
        a, b = ring_spawns(2, seed)
        _player(player, seed, {"preset": "typical", "seed": entrant_seed(seed, 0) % 1_000_000}, a)
        opponent.role = "opponent"
        opponent.team = "opponent"
        opponent.seed = entrant_seed(seed, 1)
        opponent.airframe = {"preset": "typical", "seed": entrant_seed(seed, 1) % 1_000_000}
        opponent.spawn = b
        sc = Scenario(
            f"duel_{level}", seed, 60.0, sensors=dict(ARENA_SENSORS), arena=dict(DUEL_ARENA), guns=True
        )
        return sc, [player, opponent]

    return build


def _ram(seed: int, player: Slot, _o: Slot | None) -> tuple[Scenario, list[Slot]]:
    """WP-B (07 §3): the player against a hovering target, no guns."""
    a, b = ring_spawns(2, seed)
    _player(player, seed, {"preset": "typical", "seed": entrant_seed(seed, 0) % 1_000_000}, a)
    tgt = Slot(
        program=targetmod.HOVER_PROGRAM_ID,
        rev=1,
        name=targetmod.HOVER_NAME,
        source=targetmod.HOVER_SOURCE,
        role="target",
        team="target",
        airframe={"preset": "ideal", "seed": 0},
        spawn=b,
        seed=entrant_seed(seed, 1),
        sensors=dict(UWB),
    )
    sc = Scenario("ram_easy", seed, 60.0, sensors=dict(ARENA_SENSORS), arena=dict(DUEL_ARENA), guns=False)
    return sc, [player, tgt]


def _cage_rings() -> list[dict[str, Any]]:
    return scaled(COURSES["cage-loop"], (3.0, 3.0, 2.0))["rings"]


_DUEL_SCORING_EN = (
    "Win: 70 + 30 x remaining hit points / 100. Time up: 60 x your hit points / both hit points. "
    "Loss: 10 x min(hits, 5) / 5."
)
_DUEL_SCORING_ZH = (
    "胜利：70 + 30 × 剩余生命值 / 100；到时：60 × 己方生命值 / 双方生命值之和；"
    "失败：10 × min(命中数, 5) / 5。"
)

CHALLENGES: dict[str, Challenge] = {
    c.id: c
    for c in (
        Challenge(
            id="liftoff",
            name_en="Liftoff",
            name_zh="起飞",
            scene_en="A payload raises the hover throttle to about 0.62. Barometer, no position source.",
            scene_zh="载重使悬停油门约为 0.62。有气压计，无定位源。",
            goal_en="Leave the ground within 5 s, then hold 1.0 +- 0.2 m for 3 s without a break.",
            goal_zh="5 s 内离地，并在 1.0 ± 0.2 m 连续保持 3 s。",
            scoring_en=(
                "Passed: 70 + 30 x (5 - liftoff time) / 4. Otherwise 20 for a liftoff within 5 s plus "
                "40 x longest hold / 3 s."
            ),
            scoring_zh=(
                "通过：70 + 30 × (5 − 离地时间) / 4；未通过：5 s 内离地得 20，另加 40 × 最长保持时间 / 3 s。"
            ),
            duration_s=12.0,
            visible=(1, 2),
            hidden=(7703, 19211, 40087),
            build=_liftoff,
            scorer=LiftoffScorer,
            sensors=BARO_ONLY,
            info={"target_alt_m": 1.0, "band_m": 0.2, "hold_s": 3.0, "deadline_s": 5.0},
        ),
        Challenge(
            id="hover",
            name_en="Hover",
            name_zh="悬停",
            scene_en=(
                "The centre of gravity sits forward of the middle and the level calibration is off. "
                "Barometer, no position source."
            ),
            scene_zh="重心前偏，水平校准有误差。有气压计，无定位源。",
            goal_en="From 6 s to 26 s: horizontal drift at most 0.8 m and height 1.0 +- 0.25 m throughout.",
            goal_zh="6 s 至 26 s 内：水平漂移不超过 0.8 m，高度始终在 1.0 ± 0.25 m。",
            scoring_en="50 x share of the window in the height band + 50 x max(0, 1 - drift / 1.6 m).",
            scoring_zh="50 × 高度达标时间比例 + 50 × max(0, 1 − 漂移 / 1.6 m)。",
            duration_s=26.0,
            visible=(1, 2),
            hidden=(5501, 23117, 61403),
            build=_hover,
            scorer=HoverScorer,
            sensors=BARO_ONLY,
            info={"target_alt_m": 1.0, "band_m": 0.25, "drift_m": 0.8, "window_s": [6.0, 26.0]},
        ),
        Challenge(
            id="heading",
            name_en="Heading",
            name_zh="定向",
            scene_en="Unequal motor efficiencies make the airframe turn. Barometer, no position source.",
            scene_zh="电机效率不一致导致偏航漂移。有气压计，无定位源。",
            goal_en="From 5 s to 20 s: heading within 10 degrees of the spawn heading, at least 0.4 m up.",
            goal_zh="5 s 至 20 s 内：航向与出生航向相差不超过 10°，高度不低于 0.4 m。",
            scoring_en=(
                "70 x share of the window within the limits + 30 x max(0, 1 - largest error / 30 degrees)."
            ),
            scoring_zh="70 × 达标时间比例 + 30 × max(0, 1 − 最大误差 / 30°)。",
            duration_s=20.0,
            visible=(1, 2),
            hidden=(3307, 17389, 52021),
            build=_heading,
            scorer=HeadingScorer,
            sensors=BARO_ONLY,
            info={"limit_deg": 10.0, "min_alt_m": 0.4, "window_s": [5.0, 20.0]},
        ),
        Challenge(
            id="square",
            name_en="Square",
            name_zh="方形航线",
            scene_en=(
                "A UWB-like position source (10 Hz, 5 cm noise). Corners in world east / north / up metres."
            ),
            scene_zh="有 UWB 式定位（10 Hz，噪声 5 cm）。角点为世界坐标东 / 北 / 上（米）。",
            goal_en="Pass the four corners in order (within 0.4 m) within 40 s.",
            goal_zh="40 s 内按顺序经过四个角点（半径 0.4 m）。",
            scoring_en="Passed: 60 + 40 x (40 - time) / 30. Otherwise 12 per corner.",
            scoring_zh="通过：60 + 40 × (40 − 用时) / 30；未通过：每个角点 12 分。",
            duration_s=40.0,
            visible=(1, 2),
            hidden=(2203, 13859, 47353),
            build=_square,
            scorer=lambda: SquareScorer(corners=list(CORNERS)),
            sensors=UWB,
            info={"corners": [list(c) for c in CORNERS], "radius_m": 0.4},
        ),
        Challenge(
            id="rings",
            name_en="Rings",
            name_zh="穿环",
            scene_en="The Cage loop ring course with a UWB-like position source (10 Hz, 5 cm noise).",
            scene_zh="围网环线穿环航线，有 UWB 式定位（10 Hz，噪声 5 cm）。",
            goal_en="Fly through every ring in order, along its direction, within 60 s.",
            goal_zh="60 s 内按顺序、沿规定方向穿过全部圆环。",
            scoring_en="Passed: 60 + 40 x (60 - time) / 45. Otherwise 50 x rings / total.",
            scoring_zh="通过：60 + 40 × (60 − 用时) / 45；未通过：50 × 已过环数 / 总数。",
            duration_s=60.0,
            visible=(1, 2),
            hidden=(1109, 9923, 33071),
            build=_rings,
            scorer=RingsScorer,
            sensors=UWB,
            info={"course": "cage-loop", "rings": _cage_rings()},
        ),
        Challenge(
            id="pursuit",
            name_en="Pursuit",
            name_zh="追踪",
            scene_en=(
                "A scripted target circles the arena centre (radius 3 m, 0.5 m/s, 1.2 m up). Contacts and a "
                "UWB-like position source."
            ),
            scene_zh="脚本目标机绕场地中心飞行（半径 3 m，0.5 m/s，高度 1.2 m）。有接触传感器与 UWB 式定位。",
            goal_en="Stay within 2 m of the target for at least half of the 60 s.",
            goal_zh="60 s 内至少一半时间与目标保持在 2 m 以内。",
            scoring_en="100 x share of the 60 s within 2 m of the target.",
            scoring_zh="100 × 与目标距离在 2 m 以内的时间比例。",
            duration_s=60.0,
            visible=(1, 2),
            hidden=(4409, 21737, 58889),
            build=_pursuit,
            scorer=PursuitScorer,
            sensors=UWB_CONTACTS,
            info={"radius_m": 2.0, "target": {"radius_m": 3.0, "speed_ms": 0.5, "alt_m": 1.2}},
        ),
        *(
            Challenge(
                id=f"duel_{level}",
                name_en=f"Duel ({label_en})",
                name_zh=f"决斗（{label_zh}）",
                scene_en=(
                    f"One against one with the built-in hunter_{level} in the arena "
                    "(radius 8 m, ceiling 4 m). Barometer and contacts, no position source."
                ),
                scene_zh=(
                    f"在对抗场中与内置 hunter_{level} 一对一（半径 8 m，天花板 4 m）。"
                    "有气压计与接触传感器，无定位源。"
                ),
                goal_en="Win: bring the opponent down and keep flying.",
                goal_zh="胜利：击落对手且自身存活。",
                scoring_en=_DUEL_SCORING_EN,
                scoring_zh=_DUEL_SCORING_ZH,
                duration_s=60.0,
                visible=(1, 2),
                hidden=hidden,
                build=_duel(level),
                scorer=DuelScorer,
                sensors=ARENA_SENSORS,
                info={"arena": dict(DUEL_ARENA)},
                opponent=f"hunter_{level}",
                kind="duel",
            )
            for level, label_en, label_zh, hidden in (
                ("easy", "easy", "简单", (6101, 14321, 39113)),
                ("medium", "medium", "中等", (8231, 27449, 50311)),
                ("hard", "hard", "困难", (9467, 31627, 64171)),
            )
        ),
        # WP-B (07 §3)
        Challenge(
            id="ram_easy",
            name_en="Ram (easy)",
            name_zh="撞击（简单）",
            scene_en=(
                "A target hovers 1.2 m up on the far side of the arena (radius 8 m, ceiling 4 m). Barometer "
                "and contacts, no position source. No gun: nothing can fire."
            ),
            scene_zh=(
                "靶机在场地另一侧 1.2 m 高处悬停（半径 8 m，天花板 4 m）。"
                "有气压计与接触传感器，无定位源；无武器，不能开火。"
            ),
            goal_en="Bring the target down by flying into it within 60 s.",
            goal_zh="60 s 内撞毁靶机。",
            scoring_en=(
                "Passed: 60 + 40 x (60 - time) / 50. Otherwise 40 x max(0, 1 - closest approach / 4 m)."
            ),
            scoring_zh="通过：60 + 40 × (60 − 用时) / 50；未通过：40 × max(0, 1 − 最近距离 / 4 m)。",
            duration_s=60.0,
            visible=(1, 2),
            hidden=(7307, 18329, 44101),
            build=_ram,
            scorer=RamScorer,
            sensors=ARENA_SENSORS,
            info={"arena": dict(DUEL_ARENA), "target": {"alt_m": 1.2}, "fire": False},
        ),
    )
}


def get(challenge_id: str) -> Challenge | None:
    return CHALLENGES.get(challenge_id)


def run_seed(
    challenge: Challenge,
    seed: int,
    player: Slot,
    opponent: Slot | None = None,
    *,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """One seed of a challenge: ``{result: {score, passed, metrics}, trace, sim_s, wall_s}``."""
    scenario, slots = challenge.build(seed, player, opponent)
    run = Headless(scenario, slots, cancelled=cancelled, progress=progress)
    try:
        return run.run(challenge.scorer())
    except BaseException:
        run.close()
        raise


__all__ = ["CHALLENGES", "Challenge", "get", "run_seed"]
