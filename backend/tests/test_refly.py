"""Re-fly: options from a record and the three ways to fly it again (04 §4)."""

import math

import pytest
from conftest import open_agent

from relay.errors import RelayError
from relay.history import History

PLAN = [
    {"action": "takeoff", "alt": 1.0},
    {"action": "hover", "seconds": 1},
    {"action": "move", "forward": 1.0},
    {"action": "hover", "seconds": 1},
    {"action": "land"},
]
GOTO_PLAN = [
    {"action": "takeoff", "alt": 1.0},
    {"action": "goto", "x": 0.0, "y": 0.0, "z": 1.0},  # replaced below with the real origin
    {"action": "land"},
]


def boot(svc, stepper, position=True, flavour="ardupilot", count=1):
    ids = svc.add_sims(count, flavour, position)
    stepper.run(2.5)
    return ids


def truth(v):
    return list(v.driver.sim.quad.s.p)


def fly(svc, stepper, vid, steps, session, agent, budget=120.0):
    """Fly a plan and return (record session id, peak height above the start)."""
    svc.claim(vid, agent)
    v = svc.vehicles[vid]
    z0 = truth(v)[2]
    peak = 0.0
    svc.run_plan(vid, agent, steps, name="record")
    end = stepper.clock.t + budget
    while v.plan.running and stepper.clock.t < end:
        stepper.run(0.1, [session])
        peak = max(peak, truth(v)[2] - z0)
    assert v.plan.state == "done", v.plan.detail
    assert stepper.until(lambda: not v.armed, 8, [session])
    stepper.run(1.0, [session])
    return v.record_session, peak


def run_until_idle(stepper, v, session, budget=120.0):
    assert stepper.until(lambda: v.refly is None, budget, [session]), "re-fly did not finish"
    assert stepper.until(lambda: not v.armed, 8, [session])


def displacement(v, start):
    now = truth(v)
    return now[0] - start[0], now[1] - start[1], now[2] - start[2]


def goto_plan(origin):
    x, y = origin[0], origin[1]
    return [
        {"action": "takeoff", "alt": 1.0},
        {"action": "goto", "x": x + 1.0, "y": y, "z": origin[2] + 1.0},
        {"action": "goto", "x": x + 1.0, "y": y + 0.8, "z": origin[2] + 1.0},
        {"action": "land"},
    ]


def test_stick_columns_are_recorded(svc, stepper):
    (vid,) = boot(svc, stepper, position=False)
    session, agent = open_agent(svc)
    sid, _ = fly(svc, stepper, vid, PLAN, session, agent)
    cols = svc.history.session_detail(sid)["samples"]
    sourced = [s for s in cols["s_source"] if s]
    assert sourced and set(sourced) == {"skill"}
    assert any(abs(r) > 0.05 for r in cols["s_pitch"] if r is not None)  # the forward move tilted
    assert max(t for t in cols["s_throttle"] if t is not None) > 0.6  # the climb


def test_old_database_gets_the_new_columns(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE samples (session_id INTEGER NOT NULL, t REAL NOT NULL, armed INTEGER, mode TEXT, "
        "roll REAL, pitch REAL, yaw REAL, x REAL, y REAL, z REAL, alt REAL, climb REAL, voltage REAL, "
        "current REAL, remaining REAL, throttle REAL, controller TEXT);"
    )
    conn.commit()
    conn.close()
    for _ in range(2):  # idempotent
        history = History(path)
        history.close()
    columns = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(samples)")}
    assert {"s_roll", "s_pitch", "s_yaw", "s_throttle", "s_source"} <= columns


def test_plan_and_path_refly_on_another_sim(svc, stepper):
    a, b, c = boot(svc, stepper, position=True, count=3)
    session, agent = open_agent(svc)
    va = svc.vehicles[a]
    start_a = truth(va)
    sid, _ = fly(svc, stepper, a, goto_plan(va.world_enu()), session, agent)
    flown = displacement(va, start_a)
    assert abs(flown[0] - 1.0) < 0.4 and abs(flown[1] - 0.8) < 0.4

    options = svc.refly_options(sid)
    assert options["recommended"] == "plan"
    assert options["plan"]["steps"][0]["action"] == "takeoff"
    assert len(options["path"]["waypoints"]) >= 5 and options["path"]["duration_s"] > 3
    assert options["sticks"] is None  # position flight: the sticks were a keep-alive in GUIDED

    for vid, mode in ((b, "plan"), (c, "path")):
        v = svc.vehicles[vid]
        svc.claim(vid, agent)
        start = truth(v)
        result = svc.refly(vid, agent, sid, mode)
        assert result["plan"]["state"] == "running"
        run_until_idle(stepper, v, session)
        moved = displacement(v, start)
        assert abs(moved[0] - flown[0]) < 0.5 and abs(moved[1] - flown[1]) < 0.5, (mode, moved, flown)
        assert moved[2] < 0.3  # landed again
    kinds = [e["kind"] for e in svc.events.after(0, 5000)]
    assert kinds.count("refly_started") == 2 and kinds.count("refly_finished") == 2


def test_auto_picks_the_recommendation(svc, stepper):
    a, b = boot(svc, stepper, position=True, count=2)
    session, agent = open_agent(svc)
    sid, _ = fly(svc, stepper, a, goto_plan(svc.vehicles[a].world_enu()), session, agent)
    svc.claim(b, agent)
    svc.refly(b, agent, sid, "auto")
    v = svc.vehicles[b]
    assert v.refly is not None and v.refly["mode"] == "plan"
    run_until_idle(stepper, v, session)


@pytest.mark.parametrize("flavour", ["ardupilot", "px4"])
def test_sticks_refly_matches_the_recorded_flight(svc, stepper, flavour):
    a, b = boot(svc, stepper, position=False, flavour=flavour, count=2)
    session, agent = open_agent(svc)
    va, vb = svc.vehicles[a], svc.vehicles[b]
    start_a = truth(va)
    sid, peak_a = fly(svc, stepper, a, PLAN, session, agent)
    forward = math.hypot(*displacement(va, start_a)[:2])
    assert forward > 0.5

    options = svc.refly_options(sid)
    assert options["path"] is None
    assert options["sticks"]["mode"] in ("ALT_HOLD", "ALTCTL") and options["sticks"]["duration_s"] > 5
    assert options["recommended"] == "plan"  # the plan is richer than the sticks

    svc.claim(b, agent)
    start_b = truth(vb)
    result = svc.refly(b, agent, sid, "sticks")
    assert result["skill"]["name"] == "replay"
    peak_b = 0.0
    end = stepper.clock.t + 120
    while vb.refly is not None and stepper.clock.t < end:
        stepper.run(0.1, [session])
        peak_b = max(peak_b, truth(vb)[2] - start_b[2])
    assert vb.refly is None
    assert stepper.until(lambda: not vb.armed, 10, [session])
    forward_b = math.hypot(*displacement(vb, start_b)[:2])
    assert abs(forward_b - forward) < 0.6, (forward_b, forward)
    assert peak_b > 0.5 and abs(peak_b - peak_a) < 0.6, (peak_b, peak_a)
    assert any(e["kind"] == "refly_finished" for e in svc.events.after(0, 5000))


def test_sticks_replay_scales_time(svc, stepper):
    a, b = boot(svc, stepper, position=False, count=2)
    session, agent = open_agent(svc)
    sid, _ = fly(svc, stepper, a, PLAN, session, agent)
    svc.claim(b, agent)
    t0 = stepper.clock.t
    svc.refly(b, agent, sid, "sticks", speed=2.0)
    vb = svc.vehicles[b]
    run_until_idle(stepper, vb, session)
    fast = stepper.clock.t - t0
    duration = svc.refly_options(sid)["sticks"]["duration_s"]
    assert fast < duration * 0.8 + 12  # roughly half the original time plus the mode and arm phases


def test_errors(svc, stepper):
    (a,) = boot(svc, stepper, position=False)
    session, agent = open_agent(svc)
    svc.claim(a, agent)
    with pytest.raises(RelayError) as exc:
        svc.refly(a, agent, 999, "auto")
    assert exc.value.code == "record_not_found"
    with pytest.raises(RelayError) as exc:
        svc.refly_options(999)
    assert exc.value.code == "record_not_found"
    # a record of an idle aircraft holds nothing to fly
    stepper.run(2.0, [session])
    with pytest.raises(RelayError) as exc:
        svc.refly(a, agent, svc.vehicles[a].record_session, "auto")
    assert exc.value.code == "refly_unavailable"
    with pytest.raises(RelayError) as exc:
        svc.refly(a, agent, svc.vehicles[a].record_session, "sticks")
    assert exc.value.code == "refly_unavailable"


def test_refly_over_the_api():
    from fastapi.testclient import TestClient

    from relay.api.app import create_app
    from relay.fleet import FleetService

    svc = FleetService(listen=False, history=History(None), operator_token="op-token")
    with TestClient(create_app(svc), base_url="http://127.0.0.1") as client:
        op = {"X-Relay-Operator": "op-token"}
        assert client.get("/api/v1/history/sessions/42/refly").status_code == 404
        client.post("/api/v1/sims", headers=op)
        token = client.post("/api/v1/sessions", json={"name": "t"}).json()["token"]
        resp = client.post(
            "/api/v1/vehicles/sim-1/refly", json={"session_id": 42}, headers={"X-Relay-Session": token}
        )
        assert resp.status_code in (404, 409)
        assert (
            client.post(
                "/api/v1/vehicles/sim-1/refly", json={"session_id": 1, "speed": 9}, headers=op
            ).status_code
            == 400
        )
