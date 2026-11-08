"""Challenges in the headless evaluator (06 §3.2-§3.4): the set, the scorers, determinism and speed."""

from __future__ import annotations

import json
import logging
import time

import pytest

from relay.api import schemas_judge as sj
from relay.judge import TRACE_FIELDS, challenges, worker
from relay.judge.challenges import CHALLENGES, HoverScorer, LiftoffScorer, SquareScorer
from relay.judge.headless import Truth
from relay.judge.scenario import Slot, Spawn
from relay.programs.store import ProgramStore

pytestmark = pytest.mark.sticks_level

STORE = ProgramStore(None)
CONST = "def step(obs, ctx):\n    return Sticks(throttle=0.5)\n"
# lifts off only after 5.5 s, then holds height like hover_trim
LATE = (
    "def init(ctx):\n    ctx.mem['trim'] = 0.6\n\n"
    "def step(obs, ctx):\n"
    "    if obs.t < 5.5:\n        return Sticks(throttle=0.0)\n"
    "    error = clamp(1.0 - obs.alt, -0.5, 0.5)\n"
    "    if obs.airborne:\n"
    "        ctx.mem['trim'] = clamp(ctx.mem['trim'] + 0.12 * error * obs.dt, 0.25, 0.8)\n"
    "    return Sticks(throttle=clamp(ctx.mem['trim'] + 0.25 * error - 0.18 * obs.vz, 0.0, 1.0))\n"
)
# full throttle: off the ground at once, never holds the band
CLIMB_AWAY = "def step(obs, ctx):\n    return Sticks(throttle=0.85 if obs.alt < 2.5 else 0.5)\n"
log = logging.getLogger("relay.judge.test")


def entrant(pid: str) -> dict:
    p = STORE.get(pid)
    return {"program": pid, "rev": 1, "name": p["name"], "source": p["source"], "params": dict(p["params"])}


def custom(source: str, name: str = "custom") -> dict:
    return {"program": name, "rev": 1, "name": name, "source": source, "params": {}}


def spec(cid: str, who: dict, seeds=None, hidden=None, opponent=None) -> dict:
    c = CHALLENGES[cid]
    runs = [{"seed": s, "hidden": False} for s in (seeds if seeds is not None else c.visible)]
    runs += [{"seed": s, "hidden": True} for s in (hidden or ())]
    return {"kind": "challenge", "challenge": cid, "runs": runs, "entrant": who, "opponent": opponent}


def strip(result: dict) -> dict:
    out = json.loads(json.dumps(result))
    out.pop("speed", None)
    return out


# ============================================================================ the set


def test_the_challenge_set_follows_the_contract_table():
    assert list(CHALLENGES) == [
        "liftoff",
        "hover",
        "heading",
        "square",
        "rings",
        "pursuit",
        "duel_easy",
        "duel_medium",
        "duel_hard",
    ]
    for c in CHALLENGES.values():
        assert c.visible and len(c.hidden) >= 3 and not set(c.visible) & set(c.hidden)
        assert c.name_en and c.name_zh and c.goal_en and c.goal_zh and c.scoring_en and c.scoring_zh
        assert c.name_en != c.name_zh and c.scene_en != c.scene_zh
        detail = c.detail()
        assert detail["hover_assist"] is False and detail["hidden_seeds"] == len(c.hidden)
        text = json.dumps(detail)
        assert all(str(s) not in text for s in c.hidden), c.id  # hidden seeds are never shown
    assert [CHALLENGES[f"duel_{x}"].opponent for x in ("easy", "medium", "hard")] == [
        "hunter_easy",
        "hunter_medium",
        "hunter_hard",
    ]


def test_hidden_seeds_fly_other_airframes():
    hover = CHALLENGES["hover"]
    frames = set()
    for seed in (*hover.visible, *hover.hidden):
        _scenario, (player,) = hover.build(seed, Slot("p", 1, "p", CONST), None)
        frames.add(json.dumps(player.airframe["params"], sort_keys=True))
        assert player.airframe["params"]["cg_offset_cm"][0] >= 0.4  # the centre of gravity sits forward
    assert len(frames) == len(hover.visible) + len(hover.hidden)
    liftoff = CHALLENGES["liftoff"]
    for seed in (*liftoff.visible, *liftoff.hidden):
        _scenario, (player,) = liftoff.build(seed, Slot("p", 1, "p", CONST), None)
        from relay.sim.airframe import resolve

        af = resolve(player.airframe, None, "ideal", lambda: 0)
        assert 0.6 <= af.hover_throttle() <= 0.64  # "the payload brings the hover throttle to about 0.62"


# ============================================================================ hover and liftoff


def test_hover_trim_passes_hover_on_visible_and_hidden_seeds():
    c = CHALLENGES["hover"]
    started = time.perf_counter()
    out = worker.execute(spec("hover", entrant("hover_trim"), hidden=c.hidden))
    wall = time.perf_counter() - started
    assert out["passed"], [(s["seed"], s["metrics"]) for s in out["seeds"]]
    assert len(out["seeds"]) == len(c.visible) + len(c.hidden)
    assert all(s["passed"] and s["metrics"]["drift_m"] <= 0.8 for s in out["seeds"])
    assert [s["seed"] for s in out["seeds"] if s["hidden"]] == [None] * len(c.hidden)
    assert len(out["traces"]) == len(c.visible)  # hidden seeds leave no trace
    assert out["score"] >= 75
    log.warning("benchmark hover x5: %s (%.1f s wall)", out["speed"], wall)


def test_a_constant_throttle_fails_hover_and_liftoff():
    hover = worker.execute(spec("hover", custom(CONST)))
    assert not hover["passed"] and all(not s["passed"] for s in hover["seeds"])
    assert hover["metrics"]["in_band"] < 0.5
    liftoff = worker.execute(spec("liftoff", custom(CONST), seeds=[1]))
    (seed,) = liftoff["seeds"]
    assert not liftoff["passed"] and seed["score"] == 0.0 and seed["metrics"]["liftoff_s"] is None


def test_liftoff_semantics():
    good = worker.execute(spec("liftoff", entrant("liftoff"), hidden=CHALLENGES["liftoff"].hidden))
    assert good["passed"] and all(s["metrics"]["liftoff_s"] <= 5.0 for s in good["seeds"])
    assert all(s["metrics"]["hold_s"] >= 3.0 for s in good["seeds"]) and good["score"] >= 70
    late = worker.execute(spec("liftoff", custom(LATE), seeds=[1]))["seeds"][0]
    assert not late["passed"] and late["metrics"]["liftoff_s"] > 5.0
    assert late["metrics"]["hold_s"] >= 3.0  # it holds, but too late: hold credit only, no liftoff credit
    assert late["score"] == pytest.approx(40.0)
    away = worker.execute(spec("liftoff", custom(CLIMB_AWAY), seeds=[1]))["seeds"][0]
    assert not away["passed"] and away["metrics"]["liftoff_s"] <= 5.0 and away["metrics"]["hold_s"] < 3.0
    assert 20.0 <= away["score"] < 70.0


# ============================================================================ determinism and the trace


def test_same_inputs_give_identical_results_and_traces():
    first = worker.execute(spec("hover", entrant("hover_trim"), seeds=[2]))
    second = worker.execute(spec("hover", entrant("hover_trim"), seeds=[2]))
    assert strip(first) == strip(second)
    other = worker.execute(spec("hover", entrant("hover_trim"), seeds=[1]))
    assert strip(other)["seeds"][0]["metrics"] != strip(first)["seeds"][0]["metrics"]


def test_trace_shape_is_the_replay_contract():
    out = worker.execute(spec("square", entrant("hover_trim"), seeds=[1]))
    (trace,) = out["traces"]
    trace = {**trace, "id": "tr-0000000000000000", "created": 0.0, "job": "job-x"}
    sj.Trace.model_validate(trace)
    assert trace["fields"] == list(TRACE_FIELDS) and trace["hz"] == 10.0
    (a,) = trace["aircraft"]
    assert len(a["frames"]) == len(trace["t"]) and all(len(row) == len(TRACE_FIELDS) for row in a["frames"])
    assert trace["t"][0] == 0.0 and trace["t"][1] == pytest.approx(0.1)
    assert a["role"] == "player" and a["states"][0][1] in ("level", "climb")
    assert [m["kind"] for m in trace["markers"]] == ["corner"] * 4
    assert any(e["kind"] == "liftoff" for e in trace["events"])
    assert trace["result"]["passed"] is False and trace["result"]["metrics"]["corners"] == 0


def test_the_ring_course_is_judged_by_the_course_runner():
    out = worker.execute(spec("rings", entrant("hover_trim"), seeds=[1]))
    (trace,) = out["traces"]
    assert trace["course"]["id"] == "cage-loop" and len(trace["course"]["rings"]) == 4
    assert out["seeds"][0]["metrics"]["rings"] == 0 and not out["passed"]


# ============================================================================ scorers on synthetic flights


class FakeRun:
    def __init__(self, path):
        self.path = path  # t -> Truth
        self.player = Slot("p", 1, "p", CONST, spawn=Spawn(0.0, 0.0, 0.0), vid="sim-1")
        self.slots = [self.player]
        self.marks = []
        self.t = 0.0

    def slot(self, role):
        return self.player

    def truth(self, slot):
        return self.path(self.t)

    def mark(self, kind, vehicle=None, **data):
        self.marks.append((self.t, kind))


def truth(x=0.0, y=0.0, z=0.0, ground=False, yaw=0.0):
    return Truth((x, y, z), (0.0, 0.0, 0.0), 0.0, 0.0, yaw, 0.5, True, ground, False, False, False, None)


def fly(scorer, run, duration):
    scorer.begin(run)
    n = 0
    while n * 0.02 <= duration + 1e-9:
        run.t = round(n * 0.02, 6)
        scorer.sample(run, run.t)
        if scorer.finished(run, run.t):
            break
        n += 1
    return scorer.result(run)


def test_liftoff_needs_a_continuous_hold():
    def bumpy(t):  # airborne at 1 s, in the band, but out of it for a moment at 3 s
        if t < 1.0:
            return truth(ground=True)
        return truth(z=1.5 if 3.0 <= t < 3.1 else 1.0)

    res = fly(LiftoffScorer(), FakeRun(bumpy), 12.0)
    assert res["passed"] and res["metrics"]["liftoff_s"] == pytest.approx(1.0)
    assert res["score"] == pytest.approx(100.0)

    def short(t):  # never 3 s without a break
        return truth(ground=t < 1.0, z=0.0 if t < 1.0 else (1.0 if (t % 2.5) < 2.0 else 1.5))

    res = fly(LiftoffScorer(), FakeRun(short), 12.0)
    assert not res["passed"] and res["metrics"]["hold_s"] == pytest.approx(2.0, abs=0.03)
    assert res["score"] == pytest.approx(20.0 + 40.0 * 2.0 / 3.0, abs=0.5)


def test_hover_drift_and_height_band():
    def drifting(t):
        return truth(x=0.05 * t, z=1.0)

    res = fly(HoverScorer(), FakeRun(drifting), 26.0)
    assert res["metrics"]["drift_m"] == pytest.approx(1.0, abs=0.01) and not res["passed"]
    assert res["metrics"]["in_band"] == 1.0
    assert res["score"] == pytest.approx(50.0 + 50.0 * (1.0 - 1.0 / 1.6), abs=0.2)
    res = fly(HoverScorer(), FakeRun(lambda t: truth(x=0.02 * t, z=1.0)), 26.0)
    assert res["passed"] and res["metrics"]["drift_m"] == pytest.approx(0.4, abs=0.01)


def test_square_corners_in_order():
    corners = challenges.CORNERS
    plan = [(0.0, 0.0, 0.0), *corners]

    def path(t):  # the next corner every 5 s, starting from the take-off at 1 s
        if t < 1.0:
            return truth(ground=True)
        i = min(len(plan) - 1, int((t - 1.0) / 5.0) + 1)
        return truth(*plan[i])

    res = fly(SquareScorer(corners=list(corners)), FakeRun(path), 40.0)
    assert res["passed"] and res["metrics"]["corners"] == 4
    assert res["metrics"]["time_s"] == pytest.approx(15.0, abs=0.05)
    assert res["score"] == pytest.approx(60.0 + 40.0 * 25.0 / 30.0, abs=0.2)
    backwards = [(0.0, 0.0, 0.0), *reversed(corners)]
    res = fly(
        SquareScorer(corners=list(corners)), FakeRun(lambda t: truth(*backwards[min(4, int(t / 5))])), 40.0
    )
    assert not res["passed"] and res["metrics"]["corners"] == 1  # only the first corner counts out of order


def test_challenge_results_validate_against_the_schema():
    out = worker.execute(spec("heading", entrant("hover_trim"), seeds=[1]))
    seeds = [{**s, "trace_id": None} for s in out["seeds"]]
    sj.ChallengeResult.model_validate(
        {
            "challenge": "heading",
            "score": out["score"],
            "passed": out["passed"],
            "metrics": out["metrics"],
            "seeds": seeds,
            "trace_id": None,
            "best": None,
            "speed": out["speed"],
        }
    )
    # hover_trim holds no heading: unequal motors turn it away
    assert not out["passed"] and out["metrics"]["max_error_deg"] > 10.0
