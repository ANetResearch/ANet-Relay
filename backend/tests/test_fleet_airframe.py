"""Airframe defects through the fleet service and the API (06 §4, WP-2a)."""

import time

import pytest
from conftest import open_agent
from fastapi.testclient import TestClient

from relay.api import schemas
from relay.api.app import create_app
from relay.authority import Actor
from relay.errors import RelayError
from relay.fleet import FleetService
from relay.history import History
from relay.sim.airframe import generate

OP = Actor.operator()


def seeded(svc, *seeds):
    it = iter(seeds)
    svc.airframe_seed_source = lambda: next(it)


def test_new_sims_take_the_default_preset_with_a_fresh_seed(svc, stepper):
    seeded(svc, 101, 102, 103)
    a, b = svc.add_sims(2)
    assert svc.vehicles[a].driver.sim.airframe == generate("typical", 101)
    assert svc.vehicles[b].driver.sim.airframe == generate("typical", 102)
    svc.update_settings({"sim_airframe_default": "rough"})
    (c,) = svc.add_sims(1)
    assert svc.vehicles[c].driver.sim.airframe == generate("rough", 103)
    added = [e for e in svc.events.after(0) if e["kind"] == "sim_added"]
    assert added[-1]["data"]["airframe"] == {"preset": "rough", "seed": 103}
    with pytest.raises(RelayError) as exc:
        svc.update_settings({"sim_airframe_default": "custom"})
    assert exc.value.code == "invalid_request"
    assert svc.get_settings()["sim_airframe_default"] == "rough"


def test_add_sims_with_an_explicit_airframe(svc, stepper):
    ids = svc.add_sims(2, airframe={"preset": "rough", "seed": 7})
    assert [svc.vehicles[i].driver.sim.airframe for i in ids] == [generate("rough", 7)] * 2
    (vid,) = svc.add_sims(1, airframe={"params": {"payload_kg": 0.05}})
    af = svc.vehicles[vid].driver.sim.airframe
    assert af.preset == "custom" and af.payload_kg == 0.05 and not any(af.cg_offset_cm)
    with pytest.raises(RelayError) as exc:
        svc.add_sims(1, airframe={"params": {"payload_kg": 5}})
    assert exc.value.code == "invalid_request" and "payload_kg" in exc.value.message_en
    assert len(svc.vehicles) == 3  # nothing added on a bad body


def test_snapshot_shows_preset_and_seed_only(svc, stepper):
    (vid,) = svc.add_sims(1, airframe={"preset": "rough", "seed": 5})
    stepper.run(1.0)
    view = svc._vehicle_view(svc.vehicles[vid], svc.clock(), svc.wall())
    assert view["sim"]["airframe"] == {"preset": "rough", "seed": 5}
    schemas.Vehicle.model_validate(view)


def test_set_and_get_airframe(svc, stepper):
    (vid,) = svc.add_sims(1, airframe={"preset": "ideal"})
    stepper.run(1.0)
    out = svc.set_sim_airframe(vid, {"preset": "rough", "seed": 9}, OP)
    assert out["preset"] == "rough" and out["seed"] == 9
    assert out["params"] == generate("rough", 9).params()
    assert out["active"] is False  # hover assist is on: the defects wait
    assert out["limits"]["payload_kg"] == [0.0, 0.2]
    assert 0.5 < out["hover_throttle"] < 0.6
    schemas.SimAirframe.model_validate(out)
    assert svc.get_sim_airframe(vid, OP) == out
    events = [e for e in svc.events.after(0) if e["kind"] == "sim_airframe_changed"]
    assert events[-1]["text_en"] == "SIM-1 airframe set to rough (seed 9)."
    assert events[-1]["text_zh"] == "SIM-1 机体已设为粗糙（种子 9）。"
    assert events[-1]["data"] == {"preset": "rough", "seed": 9, "by": "operator"}
    # a seed alone re-rolls the current preset; params make it custom on top of the current values
    assert svc.set_sim_airframe(vid, {"seed": 10}, OP)["params"] == generate("rough", 10).params()
    tuned = svc.set_sim_airframe(vid, {"params": {"imu_noise": 0.5}}, OP)
    assert tuned["preset"] == "custom" and tuned["seed"] == 10 and tuned["params"]["imu_noise"] == 0.5
    assert tuned["params"]["cg_offset_cm"] == generate("rough", 10).params()["cg_offset_cm"]
    assert svc.set_sim_airframe(vid, {"seed": 3}, OP)["params"] == tuned["params"]  # custom keeps its values
    heavy = svc.set_sim_airframe(vid, {"preset": "ideal", "params": {"payload_kg": 0.2}}, OP)
    assert heavy["hover_throttle"] is None
    svc.set_sim_assist(vid, False, OP)
    assert svc.get_sim_airframe(vid, OP)["active"] is True


def test_airframe_permissions(svc, stepper):
    (vid,) = svc.add_sims(1)
    stepper.run(1.0)
    _session, agent = open_agent(svc)
    with pytest.raises(RelayError) as exc:
        svc.get_sim_airframe(vid, agent)
    assert exc.value.code == "not_claimed"
    assert svc.get_sim_airframe(vid, agent, app_control=True)["id"] == vid  # uncontrolled, app control on
    svc.claim(vid, agent)
    assert svc.set_sim_airframe(vid, {"preset": "rough"}, agent)["preset"] == "rough"
    _, other = open_agent(svc, "other")
    with pytest.raises(RelayError) as exc:
        svc.set_sim_airframe(vid, {"preset": "ideal"}, other, app_control=True)
    assert exc.value.code == "claimed_by_other"
    v = svc.vehicles[vid]
    v.kind = "real"
    with pytest.raises(RelayError) as exc:
        svc.set_sim_airframe(vid, {"preset": "ideal"}, OP)
    assert exc.value.code == "sim_only"


def test_payload_hover_throttle_reaches_flight_health(svc, stepper):
    (vid,) = svc.add_sims(1, position=False, hover_assist=False, airframe={"params": {"payload_kg": 0.04}})
    stepper.run(2.5)
    v = svc.vehicles[vid]
    q = v.driver.sim.quad
    svc.possess(vid, "keyboard")
    stepper.run(0.5)
    assert v.mode == "STABILIZE"
    svc.arm(vid, OP, True)
    for _ in range(600):  # a height loop on the throttle stick only, as a person would fly it
        thr = q.hover_throttle(1.0) + 0.25 * (1.0 - q.s.p[2]) - 0.15 * q.s.v[2]
        svc.sticks(vid, OP, {"roll": 0, "pitch": 0, "yaw": 0, "throttle": min(1.0, max(0.0, thr))})
        stepper.run(0.02)
    assert v.airborne()
    svc._build_snapshot(stepper.clock.t)
    item = {i["key"]: i for i in svc.fleet()["vehicles"][0]["health"]["items"]}["hover_throttle"]
    expected = q.hover_throttle(1.0) * 100
    assert expected > 60  # 0.16 kg take-off weight on motors sized for 0.12 kg
    assert item["value"] == pytest.approx(expected, abs=3.0)
    assert item["level"] == "ok"
    svc.release(vid)


@pytest.fixture
def client():
    svc = FleetService(listen=False, history=History(None), operator_token="op-token")
    app = create_app(svc)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.svc = svc
        yield c


def test_api_airframe_routes(client):
    op = {"X-Relay-Operator": "op-token"}
    body = {"count": 1, "airframe": {"preset": "rough", "seed": 21}}
    (vid,) = client.post("/api/v1/sims", json=body, headers=op).json()["added"]
    assert client.svc.vehicles[vid].driver.sim.airframe == generate("rough", 21)
    got = client.get(f"/api/v1/sims/{vid}/airframe", headers=op)
    assert got.status_code == 200 and got.json()["params"] == generate("rough", 21).params()
    put = client.put(f"/api/v1/sims/{vid}/airframe", json={"params": {"cg_offset_cm": [1, -1]}}, headers=op)
    assert put.status_code == 200 and put.json()["preset"] == "custom"
    assert put.json()["params"]["cg_offset_cm"] == [1.0, -1.0]
    assert client.put(f"/api/v1/sims/{vid}/airframe", json={}, headers=op).status_code == 400
    gain = {"params": {"motor_gain": [0.5, 0, 0, 0]}}
    bad = client.put(f"/api/v1/sims/{vid}/airframe", json=gain, headers=op)
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_request"
    assert client.put(f"/api/v1/sims/{vid}/airframe", json={"preset": "nope"}, headers=op).status_code == 400
    assert client.put(f"/api/v1/sims/{vid}/airframe", json={"seed": -1}, headers=op).status_code == 400
    assert client.get("/api/v1/sims/sim-9/airframe", headers=op).status_code == 404
    assert client.get(f"/api/v1/sims/{vid}/airframe").status_code == 401  # never anonymous
    # the snapshot is rebuilt on the fleet loop's tick: wait for the one after the change
    deadline = time.monotonic() + 3.0
    while True:
        airframe = client.get("/api/v1/fleet").json()["vehicles"][0]["sim"]["airframe"]
        if airframe == {"preset": "custom", "seed": 21} or time.monotonic() > deadline:
            break
        time.sleep(0.05)
    assert airframe == {"preset": "custom", "seed": 21}
