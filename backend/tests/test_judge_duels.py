"""Duels (06 §3.4): round scoring, placements, Elo, the duel challenges and the ladder."""

from __future__ import annotations

import json
import math

import pytest

from relay.api import schemas_judge as sj
from relay.judge import duels, worker
from relay.judge.challenges import DuelScorer
from relay.judge.duels import ELO_START, RoundScorer, elo_round, expected
from relay.judge.headless import Truth
from relay.judge.scenario import DuelSettings, Slot, duel_scenario, ring_spawns
from relay.programs.store import ProgramStore

pytestmark = pytest.mark.sticks_level

STORE = ProgramStore(None)


def entrant(pid: str) -> dict:
    p = STORE.get(pid)
    return {"program": pid, "rev": 1, "name": p["name"], "source": p["source"], "params": dict(p["params"])}


# ============================================================================ scoring on synthetic rounds


class FakeRun:
    def __init__(self, slots, script):
        self.slots = slots
        self.script = script  # (vid, t) -> dict of Truth overrides
        self.t = 0.0

    def truth(self, slot):
        state = {"down": False, "crashed": False, "out": False, "hp": 100, **self.script(slot.vid, self.t)}
        return Truth(
            (0.0, 0.0, 1.0),
            (0.0, 0.0, 0.0),
            0.0,
            0.0,
            0.0,
            0.5,
            True,
            False,
            state["crashed"],
            state["down"],
            state["out"],
            state["hp"],
        )


def slots(n, roles=None):
    out = []
    for i in range(n):
        s = Slot(f"p{i + 1}", 1, f"P{i + 1}", "", role=(roles or ["entrant"] * n)[i], team=f"slot-{i + 1}")
        s.vid = f"sim-{i + 1}"
        out.append(s)
    return out


def play(scorer, run, duration=60.0, hits=()):
    scorer.begin(run)
    n = 0
    while n * 0.02 <= duration + 1e-9:
        run.t = round(n * 0.02, 6)
        for t, shooter, target in hits:
            if abs(t - run.t) < 1e-6:
                data = {"side": "taken", "shooter": shooter, "target": target}
                scorer.event(run, run.t, {"kind": "arena_hit", "data": data})
        scorer.sample(run, run.t)
        if scorer.finished(run, run.t):
            break
        n += 1
    return scorer.result(run)


def test_last_aircraft_flying_wins_and_places_follow_survival():
    ss = slots(3)

    def script(vid, t):
        if vid == "sim-1" and t >= 10.0:
            return {"down": True, "hp": 0}
        if vid == "sim-3" and t >= 20.0:
            return {"crashed": True, "hp": 40}
        return {"hp": 80 if vid == "sim-2" else 100}

    rnd = play(RoundScorer(), FakeRun(ss, script), hits=[(5.0, "sim-2", "sim-1"), (6.0, "sim-2", "sim-1")])
    assert rnd["winner"] == "p2" and not rnd["draw"]
    assert rnd["duration_s"] == pytest.approx(21.0, abs=0.05)  # one second of grace after the last one out
    by = {a["id"]: a for a in rnd["aircraft"]}
    assert [by[v]["place"] for v in ("sim-2", "sim-3", "sim-1")] == [1, 2, 3]
    assert by["sim-1"]["reason"] == "shot_down" and by["sim-3"]["reason"] == "crashed"
    assert by["sim-2"]["hits"] == 2 and by["sim-1"]["hits_taken"] == 2
    assert by["sim-1"]["survived_s"] == pytest.approx(10.0)


def test_time_up_is_a_draw_placed_by_hit_points():
    ss = slots(2)
    rnd = play(RoundScorer(), FakeRun(ss, lambda vid, t: {"hp": 60 if vid == "sim-1" else 80}), duration=30.0)
    assert rnd["draw"] and rnd["winner"] is None
    places = {a["id"]: a["place"] for a in rnd["aircraft"]}
    assert places == {"sim-2": 1, "sim-1": 2}
    tie = play(RoundScorer(), FakeRun(ss, lambda vid, t: {}), duration=10.0)
    assert {a["place"] for a in tie["aircraft"]} == {1}  # equal keys share the place


def test_duel_challenge_outcomes():
    ss = slots(2, ["player", "opponent"])

    def win(vid, t):
        return {"down": True, "hp": 0} if vid == "sim-2" and t >= 8.0 else {"hp": 60}

    res = play(DuelScorer(), FakeRun(ss, win), hits=[(3.0, "sim-1", "sim-2")])
    assert res["passed"] and res["metrics"]["outcome"] == "win" and res["score"] == pytest.approx(88.0)
    res = play(DuelScorer(), FakeRun(ss, lambda vid, t: {"hp": 40 if vid == "sim-1" else 60}), duration=60.0)
    assert not res["passed"] and res["metrics"]["outcome"] == "time" and res["score"] == pytest.approx(24.0)

    def loss(vid, t):
        return {"down": True, "hp": 0} if vid == "sim-1" and t >= 8.0 else {}

    res = play(DuelScorer(), FakeRun(ss, loss), hits=[(2.0, "sim-1", "sim-2")] * 1)
    assert res["metrics"]["outcome"] == "loss" and res["score"] == pytest.approx(2.0)


def test_elo_is_pairwise_over_the_placements():
    two = elo_round({}, {"a": 1, "b": 2})
    assert two["a"] == pytest.approx(16.0) and two["b"] == pytest.approx(-16.0)
    assert elo_round({}, {"a": 1, "b": 1}) == {"a": 0.0, "b": 0.0}
    strong = elo_round({"a": 1600.0, "b": 1200.0}, {"a": 1, "b": 2})
    assert 0 < strong["a"] < 16.0 and strong["a"] == pytest.approx(32 * (1 - expected(1600, 1200)))
    three = elo_round({}, {"a": 1, "b": 2, "c": 3})
    assert three["a"] == pytest.approx(16.0) and three["b"] == pytest.approx(0.0) and three["c"] == -16.0
    assert sum(three.values()) == pytest.approx(0.0) and ELO_START == 1200.0


def test_spawns_face_the_centre_and_follow_the_seed():
    for n in range(2, 9):
        spawns = ring_spawns(n, 5)
        assert len({(s.x, s.y) for s in spawns}) == n
        for s in spawns:
            bearing = math.degrees(math.atan2(-s.x, -s.y)) % 360.0
            assert abs((bearing - s.yaw_deg + 180.0) % 360.0 - 180.0) < 0.01
    assert ring_spawns(3, 5) == ring_spawns(3, 5) and ring_spawns(3, 5) != ring_spawns(3, 6)
    ss = [Slot(f"p{i}", 1, "p", "") for i in range(4)]
    sc = duel_scenario(DuelSettings(), 9, ss)
    assert sc.guns and sc.arena == {"radius": 8.0, "floor": 0.0, "ceiling": 4.0}
    assert len({json.dumps(s.airframe) for s in ss}) == 4 and len({s.seed for s in ss}) == 4


def test_duel_settings_are_checked():
    assert DuelSettings.parse({"duration_s": 20, "airframe": "ideal"}).view()["duration_s"] == 20.0
    for bad in (
        {"duration_s": 5},
        {"airframe": "broken"},
        {"arena": {"radius": 1, "floor": 0, "ceiling": 4}},
        {"arena": {"radius": 8, "floor": 1, "ceiling": 2.5}},
        {"sensors": {"position": "lidar"}},
        {"turbulence": 2},
        "x",
    ):
        with pytest.raises(ValueError):
            DuelSettings.parse(bad)


# ============================================================================ real rounds


def test_hunter_hard_beats_the_easy_duel():
    out = worker.execute(
        {
            "kind": "challenge",
            "challenge": "duel_easy",
            "runs": [{"seed": 1, "hidden": False}],
            "entrant": entrant("hunter_hard"),
            "opponent": entrant("hunter_easy"),
        }
    )
    (seed,) = out["seeds"]
    assert out["passed"] and seed["metrics"]["outcome"] == "win" and seed["metrics"]["hits"] >= 1
    (trace,) = out["traces"]
    assert trace["arena"] == {"radius": 8.0, "floor": 0.0, "ceiling": 4.0}
    assert [a["role"] for a in trace["aircraft"]] == ["player", "opponent"]
    assert trace["shots"] and all(s["shooter"] in ("sim-1", "sim-2") for s in trace["shots"])
    kinds = [e["kind"] for e in trace["events"]]
    assert "hit" in kinds and "down" in kinds
    downed = trace["aircraft"][1]["frames"][-1]
    assert downed[8] & 4 and downed[7] == 0  # the opponent's last frame: shot down, no hit points


def test_a_duel_of_three_programs():
    entrants = [
        Slot(e["program"], 1, e["name"], e["source"], dict(e["params"]))
        for e in map(entrant, ("hunter_easy", "hunter_medium", "hunter_hard"))
    ]
    out = duels.run_duel(entrants, 2, 3, DuelSettings(duration_s=30.0))
    assert [r["round"] for r in out["rounds"]] == [1, 2] and [r["seed"] for r in out["rounds"]] == [3, 4]
    for rnd in out["rounds"]:
        sj.DuelRound.model_validate({**rnd, "trace_id": None})
        assert sorted(a["program"] for a in rnd["aircraft"]) == [
            "hunter_easy",
            "hunter_hard",
            "hunter_medium",
        ]
        assert min(a["place"] for a in rnd["aircraft"]) == 1
    table = {row["program"]: row for row in out["standings"]}
    assert all(row["rounds"] == 2 for row in table.values())
    assert all((r["winner"] is None) == r["draw"] for r in out["rounds"])
    assert sum(row["wins"] for row in table.values()) == sum(1 for r in out["rounds"] if not r["draw"])
    assert len(out["traces"]) == 2 and all(len(t["aircraft"]) == 3 for t in out["traces"])
    again = duels.run_duel(entrants, 2, 3, DuelSettings(duration_s=30.0))
    assert json.dumps(again["rounds"]) == json.dumps(out["rounds"])  # same seeds, same rounds
