"""The dogfight scenario (07 §3, WP-B): request checks and permissions, a stepped run from boot to the end
(spawn ring, practice mode, programs with sticks only, possession, respawn, landing), joining a LAN match,
the rammer and the hunters' dodge in the headless evaluator, and the ram_easy challenge."""

import math

import pytest
from conftest import open_agent
from fastapi.testclient import TestClient
from test_leaderboard import PEER, StubPeers, feed, peer_state

from relay.api.app import create_app
from relay.api.schemas_match import DogfightView
from relay.authority import Actor
from relay.errors import RelayError
from relay.fleet import Vehicle
from relay.judge import challenges, duels
from relay.judge.scenario import DuelSettings, Slot
from relay.peers import game, wire
from relay.programs.store import ProgramStore
from relay.scenarios import RESPAWN_S

API = "/api/v1"
OP = {"X-Relay-Operator": "op-token"}
OPERATOR = Actor.operator()


def code(fn) -> str:
    with pytest.raises(RelayError) as exc:
        fn()
    return exc.value.code


def kinds(svc, kind):
    return [e for e in svc.events.after(0, 5000) if e["kind"] == kind]


def run(svc, stepper, seconds, step=0.1):
    """Step the fleet and poll the scenario as its worker thread would."""
    end = svc.clock() + seconds
    while svc.clock() < end - 1e-9:
        stepper.run(step)
        svc.dogfights.poll()


def until(svc, stepper, predicate, timeout):
    end = svc.clock() + timeout
    while svc.clock() < end:
        run(svc, stepper, 0.1)
        if predicate():
            return True
    return False


def crafts(svc):
    return {c.vid: c for c in svc.dogfights.run.crafts}


# ---------------------------------------------------------------------------- requests


def test_requests_are_checked(svc, stepper):
    client = TestClient(create_app(svc), base_url="http://127.0.0.1")
    assert client.get(f"{API}/scenarios/dogfight").json()["phase"] == "idle"
    assert client.post(f"{API}/scenarios/dogfight", json={}).status_code == 401
    fake = Vehicle("real-10-0-0-9", "real", "Quad 09", driver=None)
    svc.vehicles[fake.id] = fake
    try:
        res = client.post(f"{API}/scenarios/dogfight", headers=OP, json={"vehicles": ["real-10-0-0-9"]})
        assert res.status_code == 400 and res.json()["error"]["code"] == "dogfight_real_aircraft"
        assert "Real aircraft never take part" in res.json()["error"]["message_en"]
    finally:
        del svc.vehicles[fake.id]
    for body, status, err in (
        ({"aircraft": 9}, 409, "dogfight_no_aircraft"),
        ({"aircraft": 1}, 400, "invalid_request"),
        ({"aircraft": 13}, 400, "invalid_request"),
        ({"aircraft": 4, "roles": {"gunner": 1, "rammer": 1}}, 400, "invalid_request"),
        ({"aircraft": 2, "programs": {"gunner": "nope"}}, 404, "program_not_found"),
        ({"duration_s": 10}, 400, "invalid_request"),
        ({"teams": 4}, 400, "invalid_request"),
        ({"bogus": 1}, 400, "invalid_request"),
    ):
        res = client.post(f"{API}/scenarios/dogfight", headers=OP, json=body)
        assert (res.status_code, res.json()["error"]["code"]) == (status, err), body
    assert (
        client.delete(f"{API}/scenarios/dogfight", headers=OP).json()["error"]["code"]
        == "dogfight_not_running"
    )
    assert not svc.vehicles  # nothing was added by the refused requests
    token = client.post(f"{API}/sessions", json={"name": "bot"}).json()["token"]
    agent = {"X-Relay-Session": token}
    svc.settings.agent_flight_level = "sticks"
    res = client.post(f"{API}/scenarios/dogfight", headers=agent, json={"aircraft": 2, "hover_assist": True})
    assert res.json()["error"]["code"] == "sticks_only"  # hover assist is not a session's to switch on
    svc.update_settings({"agents_app_control": False})
    res = client.post(f"{API}/scenarios/dogfight", headers=agent, json={"aircraft": 2})
    assert res.json()["error"]["code"] == "app_control_disabled"


def test_busy_simulators_are_left_alone(svc, stepper):
    ids = svc.add_sims(2)
    stepper.run(2.5)
    _session, agent = open_agent(svc, "pilot")
    svc.claim("sim-1", agent)
    out = svc.dogfights.start({"aircraft": 2, "duration_s": 30, "seed": 1}, OPERATOR)
    used = [a["vid"] for a in out["aircraft"]]
    assert "sim-1" not in used and "sim-2" in used and len(used) == 2  # sim-1 is someone's; sim-3 was added
    assert set(svc.vehicles) == {*ids, "sim-3"}
    assert svc.vehicles["sim-1"].auth.claim.name == "pilot"
    assert code(lambda: svc.dogfights.start({"aircraft": 2}, OPERATOR)) == "dogfight_running"
    svc.dogfights.stop(OPERATOR)
    run(svc, stepper, 1.0)
    assert svc.dogfights.view()["phase"] == "done" and svc.dogfights.view()["reason"] == "stopped"


# ---------------------------------------------------------------------------- a run


def test_a_practice_dogfight_from_boot_to_the_end(svc, stepper):
    view = svc.dogfights.start(
        {
            "aircraft": 3,
            "roles": {"gunner": 2, "rammer": 1},
            "duration_s": 40,
            "seed": 5,
            "airframe": "typical",
        },
        OPERATOR,
    )
    DogfightView.model_validate(view)
    assert view["running"] and view["phase"] == "booting" and view["config"]["lan"] is False
    assert [a["role"] for a in view["aircraft"]] == ["gunner", "gunner", "rammer"]
    assert [a["program"] for a in view["aircraft"]] == ["hunter_medium", "hunter_medium", "rammer"]
    started = kinds(svc, "dogfight_started")[-1]
    assert started["text_en"] == "Dogfight started: 3 aircraft for 40 s (local practice)."
    assert started["text_zh"] == "狗斗开始：3 架飞机，40 s（本地练习）。"
    arena = svc.arena
    assert arena.enabled and arena.practice and arena.peers is None and not arena.on_lan
    assert arena.board.match is not None and arena.board.match.running
    assert arena.hold_fire == {"sim-3"} and arena.respawn_hold == {"sim-1", "sim-2", "sim-3"}
    # on a ring near the edge, every nose towards the centre
    for vid in ("sim-1", "sim-2", "sim-3"):
        v = svc.vehicles[vid]
        x, y, _ = v.frame_offset
        assert math.hypot(x, y) == pytest.approx(5.0, abs=1e-3)
        heading = v.driver.sim.quad.s.yaw
        assert math.sin(heading) * -x + math.cos(heading) * -y == pytest.approx(5.0, abs=1e-2)
        assert not v.driver.sim.hover_assist and svc.vehicles[vid].auth.claim.name == "dogfight"
    assert until(svc, stepper, lambda: all(c.state == "flying" for c in crafts(svc).values()), 10)
    assert svc.dogfights.view()["phase"] == "running"
    run(svc, stepper, 3.0)
    for vid in ("sim-1", "sim-2", "sim-3"):
        v = svc.vehicles[vid]
        assert v.program is not None and v.program.status == "running" and v.armed
        assert v.program.mode == "pilot" and v.sticks is not None and v.sticks.source == "program"
    assert svc.programs.teams == {"sim-1": "sim-1", "sim-2": "sim-2", "sim-3": "sim-3"}
    board = arena.board.leaderboard()
    assert {r["controller"]["kind"] for r in board["rows"]} <= {"program"}
    assert any(r["controller"]["name"] == "Rammer r1" for r in board["rows"])
    # the operator takes one over: the program pauses, and gets it back when they let go
    svc.possess("sim-1", "keyboard")
    run(svc, stepper, 0.3)
    assert crafts(svc)["sim-1"].state == "human" and svc.vehicles["sim-1"].program.status == "paused"
    svc.release("sim-1")
    run(svc, stepper, 0.3)
    assert crafts(svc)["sim-1"].state == "flying" and svc.vehicles["sim-1"].program.status == "running"
    # a crash: down, then back at its spawn point after 5 s, armed and flying its program again
    spawn = svc.vehicles["sim-2"].frame_offset
    svc.vehicles["sim-2"].driver.sim.shoot_down("collision")
    run(svc, stepper, 0.3)
    assert crafts(svc)["sim-2"].state == "down"
    run(svc, stepper, RESPAWN_S + 0.2)
    assert crafts(svc)["sim-2"].state in ("booting", "arming", "deploying", "flying")
    assert until(svc, stepper, lambda: crafts(svc)["sim-2"].state == "flying", 8)
    assert svc.vehicles["sim-2"].frame_offset == spawn and not svc.vehicles["sim-2"].driver.sim.quad.s.crashed
    assert arena.board.lines["sim-2"].crashes >= 1
    # time up: programs stop, the aircraft come down, the session closes, the arena leaves practice mode
    assert until(svc, stepper, lambda: svc.dogfights.view()["phase"] in ("ending", "done"), 40)
    assert svc.dogfights.view()["reason"] == "time"
    assert until(svc, stepper, lambda: svc.dogfights.view()["phase"] == "done", 30)
    stepper.run(1.0)  # the last disarm requests reach the simulators
    for vid in ("sim-1", "sim-2", "sim-3"):
        v = svc.vehicles[vid]
        assert v.program is None or not v.program.active
        assert not v.armed and v.auth.claim is None
    assert not arena.enabled and not arena.practice and not arena.respawn_hold and not arena.hold_fire
    assert arena.board.match is not None and not arena.board.match.running  # the final standings stay
    finished = kinds(svc, "dogfight_finished")[-1]
    assert (
        finished["text_en"] == "Dogfight ended: time is up." and finished["text_zh"] == "狗斗结束：时间到。"
    )
    assert kinds(svc, "match_finished")


def test_rammers_never_fire(svc, stepper):
    svc.dogfights.start(
        {"aircraft": 2, "roles": {"gunner": 1, "rammer": 1}, "duration_s": 30, "seed": 2}, OPERATOR
    )
    assert until(svc, stepper, lambda: all(c.state == "flying" for c in crafts(svc).values()), 10)
    run(svc, stepper, 2.0)
    rammer = svc.vehicles["sim-2"]
    assert rammer.driver.sim.quad.s.p[2] > 0.3
    svc.arena.guns["sim-2"].fire_until = svc.clock() + 1.0  # even a trigger held on its behalf
    run(svc, stepper, 0.5)
    assert svc.arena.board.lines["sim-2"].shots == 0
    svc.dogfights.stop(OPERATOR)


def test_an_emergency_stop_of_one_aircraft_is_final_for_the_scenario(svc, stepper):
    svc.dogfights.start({"aircraft": 2, "duration_s": 60, "seed": 3}, OPERATOR)
    assert until(svc, stepper, lambda: all(c.state == "flying" for c in crafts(svc).values()), 10)
    run(svc, stepper, 1.0)
    svc.stop("sim-1", OPERATOR)
    run(svc, stepper, 0.3)
    assert crafts(svc)["sim-1"].state == "stopped" and crafts(svc)["sim-2"].state == "flying"
    run(svc, stepper, RESPAWN_S + 4.0)  # no respawn, no re-arm, no program again
    v = svc.vehicles["sim-1"]
    assert crafts(svc)["sim-1"].state == "stopped"
    assert v.program is None or not v.program.active
    assert (
        not v.armed or v.airborne()
    )  # still coming down from the stop at most, never re-armed on the ground
    assert svc.dogfights.view()["running"]  # the other aircraft fights on
    svc.dogfights.stop(OPERATOR)


def test_stop_all_ends_a_dogfight_and_nothing_flies_again(svc, stepper):
    svc.dogfights.start({"aircraft": 2, "duration_s": 60, "seed": 4}, OPERATOR)
    assert until(svc, stepper, lambda: all(c.state == "flying" for c in crafts(svc).values()), 10)
    run(svc, stepper, 1.0)
    landings = len(kinds(svc, "skill_started"))
    svc.stop_all()
    run(svc, stepper, 0.3)
    view = svc.dogfights.view()
    assert not view["running"] and view["reason"] == "emergency_stop"
    assert len(kinds(svc, "skill_started")) == landings  # no landing skill over the stop
    assert until(svc, stepper, lambda: svc.dogfights.view()["phase"] == "done", 30)
    run(svc, stepper, RESPAWN_S + 4.0)
    stepper.run(1.0)
    for vid in ("sim-1", "sim-2"):
        v = svc.vehicles[vid]
        assert not v.armed and (v.program is None or not v.program.active)
    finished = kinds(svc, "dogfight_finished")[-1]
    assert (
        finished["text_en"] == "Dogfight ended: emergency stop." and finished["text_zh"] == "狗斗结束：急停。"
    )


def test_on_the_lan_a_dogfight_joins_the_running_match(svc, stepper):
    svc.update_settings({"peer_enabled": True})
    stepper.run(game.SEAT_LISTEN_S + 0.1)
    peers = StubPeers(svc.arena.instance, clock=svc.clock)
    svc.arena.peers = peers
    svc.arena.network = True
    feed(svc, peers, peer_state(PEER, stats=wire.Stats()))
    feed(svc, peers, wire.encode_match(2, PEER, 1000, wire.Match(0x77, wire.MATCH_START, 300, 5_000, PEER)))
    stepper.run(0.1)
    assert svc.arena.board.match.id == 0x77
    view = svc.dogfights.start({"aircraft": 2, "duration_s": 30, "seed": 3}, OPERATOR)
    assert view["config"]["lan"] is True and view["match"]["id"] == "00000077"
    assert not svc.arena.practice and not peers.sent["match"]  # joined, not started
    started = kinds(svc, "dogfight_started")[-1]
    assert started["data"]["where"] == "LAN match"
    svc.dogfights.stop(OPERATOR)
    run(svc, stepper, 0.5)
    assert svc.arena.board.match.running  # somebody else's match goes on
    assert svc.arena.enabled  # still on the LAN


def test_an_own_lan_match_starts_with_the_dogfight_and_ends_with_its_stop(svc, stepper):
    svc.update_settings({"peer_enabled": True})
    stepper.run(game.SEAT_LISTEN_S + 0.1)
    peers = StubPeers(svc.arena.instance, clock=svc.clock)
    svc.arena.peers = peers
    svc.arena.network = True
    view = svc.dogfights.start({"aircraft": 2, "duration_s": 60, "seed": 3}, OPERATOR)
    (start,) = peers.sent["match"]
    assert start.action == wire.MATCH_START and start.duration_s == 60 and view["config"]["lan"] is True
    svc.dogfights.stop(OPERATOR)
    assert peers.sent["match"][-1].action == wire.MATCH_END and not svc.arena.board.match.running


# ---------------------------------------------------------------------------- headless evaluator


def _slot(store, pid):
    p = store.get(pid)
    r = store.revision(pid, None)
    return Slot(pid, r["rev"], p["name"], r["source"], {})


def test_smoke_duel_rammer_against_hunter_easy_runs_to_completion():
    store = ProgramStore(None)
    out = duels.run_duel(
        [_slot(store, "rammer"), _slot(store, "hunter_easy")], 1, 1, DuelSettings(duration_s=30.0)
    )
    (rnd,) = out["rounds"]
    assert {a["program"] for a in rnd["aircraft"]} == {"rammer", "hunter_easy"}
    rammer = next(a for a in rnd["aircraft"] if a["program"] == "rammer")
    assert rammer["airborne_s"] > 1.0 and rammer["hits"] == 0  # it flew, and it never fires
    (trace,) = out["traces"]
    states = next(a for a in trace["aircraft"] if a["program"] == "rammer")["states"]
    assert [s for _t, s in states][:2] == ["climb", "pursue"]
    assert not any(s["shooter"] == rammer["id"] for s in trace["shots"])
    # the collision world (07 §2): on this seed the rammer weaves through the gunfire and rams; at 2 m/s
    # or more both aircraft go down, so the round is a draw
    (ram,) = [e for e in trace["events"] if e["kind"] == "collision"]
    assert ram["v_rel"] >= 2.0 and set(ram["crashed"]) == {"sim-1", "sim-2"}
    assert rnd["winner"] is None
    kinds = {e["kind"] for e in trace["events"]}
    assert kinds <= {
        "hit",
        "down",
        "crash",
        "out",
        "program_stopped",
        "program_faulted",
        "collision",
        "liftoff",
    }


def test_ram_easy_runs_and_the_rammer_reaches_the_target():
    challenge = challenges.CHALLENGES["ram_easy"]
    detail = challenge.detail()
    assert detail["info"]["fire"] is False and detail["kind"] == "flight"
    store = ProgramStore(None)
    out = challenges.run_seed(challenge, 1, _slot(store, "rammer"))
    result = out["result"]
    metrics = result["metrics"]
    assert set(metrics) >= {"rammed_s", "v_rel", "closest_m", "liftoff_s", "crashed"}
    assert metrics["closest_m"] < 1.0  # proportional navigation brings it onto the target
    assert not out["trace"]["shots"]  # no gun in this challenge
    # the collision world wrecks the target (and the rammer) at 2 m/s or more
    assert metrics["rammed_s"] is not None and metrics["v_rel"] >= 2.0
    assert result["passed"] and result["score"] >= 60.0
    kinds = [e["kind"] for e in out["trace"]["events"]]
    assert "collision" in kinds and kinds.count("crash") == 2
