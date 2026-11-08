"""Duels (06 §3.4): 2-8 programs, free-for-all rounds in the arena, and the Elo ladder.

Every round puts one aircraft per program on the spawn ring of the arena (positions shuffled and airframes
seeded by the round seed ``seed + round - 1``), arms them and runs the programs with the arena's own fire,
bullet and hit rules. An aircraft is out of the round when it is shot down, crashes or leaves the arena box.
The round ends when at most one aircraft is left (after a one-second grace, so a falling winner is seen) or
when the time is up.

Placement in a round: aircraft still flying first, then by survival time, then by hit points left, then by
hits scored; equal keys share a place. The round's winner is the only aircraft left; otherwise the round is a
draw for the win / draw / loss record. The ladder (``<data_dir>/programs/ladder.json``) moves by pairwise Elo
over the placements of each round: every pair is a game, worth ``K / (n - 1)`` with ``K = 32``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .headless import Headless
from .scenario import DuelSettings, Slot, duel_scenario

ELO_K = 32.0
ELO_START = 1200.0
GRACE_S = 1.0
MIN_PROGRAMS = 2
MAX_PROGRAMS = 8
MAX_ROUNDS = 20


@dataclass
class _Entry:
    slot: Slot
    alive: bool = True
    eliminated_at: float | None = None
    reason: str | None = None
    hits: int = 0
    hits_taken: int = 0
    hp: int | None = None
    airborne_s: float = 0.0


@dataclass
class RoundScorer:
    """Judges one free-for-all round from the ground truth and the arena's hit events."""

    entries: dict[str, _Entry] = field(default_factory=dict)
    last_out: float | None = None
    t_end: float = 0.0

    def begin(self, run: Headless) -> None:
        self.entries = {s.vid: _Entry(s) for s in run.slots}

    def sample(self, run: Headless, t: float) -> None:
        self.t_end = t
        for e in self.entries.values():
            tr = run.truth(e.slot)
            e.hp = tr.hp
            if tr.airborne:
                e.airborne_s += 0.02
            if e.alive and tr.eliminated:
                e.alive = False
                e.eliminated_at = t
                e.reason = "shot_down" if tr.down else "out" if tr.out else "crashed"
                self.last_out = t

    def event(self, run: Headless, t: float, event: dict[str, Any]) -> None:
        if event["kind"] != "arena_hit":
            return
        data = event.get("data") or {}
        if data.get("side") != "taken":
            return
        shooter, target = data.get("shooter"), data.get("target")
        if shooter in self.entries and shooter != target:
            self.entries[shooter].hits += 1
        if target in self.entries:
            self.entries[target].hits_taken += 1

    def finished(self, run: Headless, t: float) -> bool:
        alive = sum(1 for e in self.entries.values() if e.alive)
        if len(self.entries) < 2 or alive > 1 or self.last_out is None:
            return False
        return t - self.last_out >= GRACE_S

    @staticmethod
    def _key(e: _Entry, t_end: float) -> tuple[int, float, int, int]:
        survived = t_end if e.alive else (e.eliminated_at or 0.0)
        return (1 if e.alive else 0, round(survived, 2), e.hp or 0, e.hits)

    def result(self, run: Headless) -> dict[str, Any]:
        t_end = self.t_end
        ordered = sorted(self.entries.values(), key=lambda e: self._key(e, t_end), reverse=True)
        places: dict[str, int] = {}
        place = 0
        previous = None
        for i, e in enumerate(ordered):
            key = self._key(e, t_end)
            if key != previous:
                place = i + 1
                previous = key
            places[e.slot.vid] = place
        alive = [e for e in self.entries.values() if e.alive]
        winner = alive[0].slot if len(alive) == 1 and len(self.entries) > 1 else None
        aircraft = []
        for e in self.entries.values():
            s = e.slot
            aircraft.append(
                {
                    "id": s.vid,
                    "program": s.program,
                    "rev": s.rev,
                    "name": s.name,
                    "role": s.role,
                    "team": s.team,
                    "alive": e.alive,
                    "hp": e.hp,
                    "hits": e.hits,
                    "hits_taken": e.hits_taken,
                    "survived_s": round(t_end if e.alive else (e.eliminated_at or 0.0), 2),
                    "airborne_s": round(e.airborne_s, 2),
                    "reason": e.reason,
                    "place": places[s.vid],
                }
            )
        return {
            "winner": None if winner is None else winner.program,
            "winner_id": None if winner is None else winner.vid,
            "draw": winner is None,
            "duration_s": round(t_end, 2),
            "aircraft": aircraft,
        }


def round_score(rnd: dict[str, Any]) -> dict[str, Any]:
    """A round as a scorer result (score / passed are not used for duels)."""
    return {"score": None, "passed": None, "metrics": {}, "round": rnd}


class _RoundAsResult(RoundScorer):
    def result(self, run: Headless) -> dict[str, Any]:
        return round_score(super().result(run))


def run_duel(
    entrants: list[Slot],
    rounds: int,
    seed: int,
    settings: DuelSettings,
    *,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Every round in turn: ``{rounds: [...], standings: [...], traces: [...], sim_s, wall_s}``."""
    out_rounds = []
    traces = []
    sim_s = wall_s = 0.0
    for r in range(rounds):
        round_seed = seed + r
        slots = [
            Slot(
                program=e.program,
                rev=e.rev,
                name=e.name,
                source=e.source,
                params=dict(e.params),
                role="entrant",
                team=f"slot-{i + 1}",
            )
            for i, e in enumerate(entrants)
        ]
        scenario = duel_scenario(settings, round_seed, slots)

        def step_progress(f: float, r: int = r) -> None:
            if progress is not None:
                progress((r + f) / rounds)

        run = Headless(scenario, slots, cancelled=cancelled, progress=step_progress)
        try:
            out = run.run(_RoundAsResult())
        except BaseException:
            run.close()
            raise
        rnd = out["result"]["round"]
        rnd.update(round=r + 1, seed=round_seed, trace=len(traces))
        out["trace"]["round"] = r + 1
        out["trace"]["result"] = rnd
        traces.append(out["trace"])
        out_rounds.append(rnd)
        sim_s += out["sim_s"]
        wall_s += out["wall_s"]
    return {
        "rounds": out_rounds,
        "standings": standings(out_rounds, entrants),
        "traces": traces,
        "sim_s": round(sim_s, 3),
        "wall_s": round(wall_s, 3),
    }


def standings(rounds: list[dict[str, Any]], entrants: list[Slot]) -> list[dict[str, Any]]:
    table: dict[str, dict[str, Any]] = {
        e.program: {
            "program": e.program,
            "rev": e.rev,
            "name": e.name,
            "rounds": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "hits": 0,
            "hits_taken": 0,
            "survived_s": 0.0,
            "mean_place": 0.0,
        }
        for e in entrants
    }
    for rnd in rounds:
        for a in rnd["aircraft"]:
            row = table[a["program"]]
            row["rounds"] += 1
            row["hits"] += a["hits"]
            row["hits_taken"] += a["hits_taken"]
            row["survived_s"] += a["survived_s"]
            row["mean_place"] += a["place"]
            if rnd["winner"] is None:
                row["draws"] += 1
            elif rnd["winner"] == a["program"]:
                row["wins"] += 1
            else:
                row["losses"] += 1
    out = []
    for row in table.values():
        n = max(1, row["rounds"])
        row["survived_s"] = round(row["survived_s"] / n, 2)
        row["mean_place"] = round(row["mean_place"] / n, 2)
        out.append(row)
    out.sort(key=lambda r: (-r["wins"], r["mean_place"], -r["hits"], r["program"]))
    return out


def expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + math.pow(10.0, (rb - ra) / 400.0))


def elo_round(ratings: dict[str, float], places: dict[str, int], k: float = ELO_K) -> dict[str, float]:
    """Rating changes for one round (pairwise over the placements); ``ratings`` is not changed."""
    ids = sorted(places)
    n = len(ids)
    delta = {pid: 0.0 for pid in ids}
    if n < 2:
        return delta
    weight = k / (n - 1)
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            ra, rb = ratings.get(a, ELO_START), ratings.get(b, ELO_START)
            if places[a] < places[b]:
                score = 1.0
            elif places[a] > places[b]:
                score = 0.0
            else:
                score = 0.5
            change = weight * (score - expected(ra, rb))
            delta[a] += change
            delta[b] -= change
    return delta


__all__ = [
    "ELO_K",
    "ELO_START",
    "MAX_PROGRAMS",
    "MAX_ROUNDS",
    "MIN_PROGRAMS",
    "RoundScorer",
    "elo_round",
    "expected",
    "run_duel",
    "standings",
]
