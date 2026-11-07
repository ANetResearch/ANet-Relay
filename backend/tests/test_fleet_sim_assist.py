"""Hover assist through the fleet service and the API (05 §5.2)."""

import pytest
from conftest import open_agent
from fastapi.testclient import TestClient

from relay.api.app import create_app
from relay.authority import Actor
from relay.errors import RelayError
from relay.fleet import FleetService
from relay.history import History

OP = Actor.operator()


def boot(svc, stepper, **kw):
    ids = svc.add_sims(1, **kw)
    stepper.run(2.5)
    return ids[0], svc.vehicles[ids[0]]


def test_new_sims_follow_the_setting(svc, stepper):
    _vid, v = boot(svc, stepper)
    assert v.driver.sim.hover_assist is True
    assert svc._vehicle_view(v, svc.clock(), svc.wall())["sim"] == {"hover_assist": True, "turbulence": 0.3}
    svc.update_settings({"sim_hover_assist": False})
    (second,) = svc.add_sims(1)
    assert svc.vehicles[second].driver.sim.hover_assist is False
    (third,) = svc.add_sims(1, hover_assist=True)
    assert svc.vehicles[third].driver.sim.hover_assist is True
    added = [e for e in svc.events.after(0) if e["kind"] == "sim_added"]
    assert [e["data"]["hover_assist"] for e in added] == [True, False, True]


def test_turbulence_setting_is_validated_and_applied_live(svc, stepper):
    _vid, v = boot(svc, stepper)
    with pytest.raises(RelayError) as exc:
        svc.update_settings({"sim_turbulence": 1.5})
    assert exc.value.code == "invalid_request"
    svc.update_settings({"sim_turbulence": 0.8})
    assert v.driver.sim.turbulence == pytest.approx(0.8)
    assert svc.get_settings()["sim_turbulence"] == pytest.approx(0.8)


def test_assist_off_loses_althold_and_position_capabilities(svc, stepper):
    vid, v = boot(svc, stepper, hover_assist=False)
    caps = v.capabilities
    assert caps.manual and not caps.althold and not caps.position
    assert not v.has_baro()
    svc.possess(vid, "keyboard")
    stepper.run(1.0)
    assert v.mode == "STABILIZE"  # possess_mode auto picks the manual mode without a barometer
    svc.release(vid)


def test_switch_emits_event_and_changes_capabilities(svc, stepper):
    vid, v = boot(svc, stepper)
    assert v.capabilities.althold and v.capabilities.position
    assert svc.set_sim_assist(vid, False, OP) == {"id": vid, "hover_assist": False}
    stepper.run(0.3)
    assert not v.capabilities.althold and not v.capabilities.position
    events = [e for e in svc.events.after(0) if e["kind"] == "sim_assist_changed"]
    assert events[-1]["text_en"] == "SIM-1 hover assist off."
    assert events[-1]["text_zh"] == "SIM-1 悬停辅助已关闭。"
    assert events[-1]["data"] == {"hover_assist": False, "by": "operator"}
    svc.set_sim_assist(vid, False, OP)  # unchanged: no second event
    assert len([e for e in svc.events.after(0) if e["kind"] == "sim_assist_changed"]) == 1
    svc.set_sim_assist(vid, True, OP)
    stepper.run(0.3)
    assert v.capabilities.althold and v.capabilities.position


def test_switch_in_flight_moves_to_stabilize(svc, stepper):
    vid, v = boot(svc, stepper)
    svc.possess(vid, "keyboard")
    stepper.run(1.0)
    assert v.mode == "ALT_HOLD"
    svc.arm(vid, OP, True)
    for _ in range(120):
        svc.sticks(vid, OP, {"roll": 0, "pitch": 0, "yaw": 0, "throttle": 0.85})
        stepper.run(0.02)
    assert v.airborne()
    svc.set_sim_assist(vid, False, OP)  # allowed armed
    for _ in range(50):
        svc.sticks(vid, OP, {"roll": 0, "pitch": 0, "yaw": 0, "throttle": 0.5})
        stepper.run(0.02)
    assert v.mode == "STABILIZE" and v.armed
    svc.set_mode(vid, OP, "ALT_HOLD")
    for _ in range(25):
        svc.sticks(vid, OP, {"roll": 0, "pitch": 0, "yaw": 0, "throttle": 0.5})
        stepper.run(0.02)
    assert v.mode == "STABILIZE"
    assert any(s["text"] == "Mode requires hover assist" for s in v.state["statustext"])
    svc.release(vid)


def test_sim_only(svc, stepper):
    vid, v = boot(svc, stepper)
    v.kind = "real"
    with pytest.raises(RelayError) as exc:
        svc.set_sim_assist(vid, False, OP)
    assert exc.value.code == "sim_only"


def test_agents_need_control_or_an_uncontrolled_sim(svc, stepper):
    vid, _v = boot(svc, stepper)
    _session, agent = open_agent(svc)
    with pytest.raises(RelayError) as exc:
        svc.set_sim_assist(vid, False, agent)
    assert exc.value.code == "not_claimed"
    assert svc.set_sim_assist(vid, False, agent, app_control=True)["hover_assist"] is False
    svc.possess(vid, "keyboard")
    with pytest.raises(RelayError) as exc:
        svc.set_sim_assist(vid, True, agent, app_control=True)
    assert exc.value.code == "possessed_by_human"
    svc.release(vid)
    svc.claim(vid, agent)
    assert svc.set_sim_assist(vid, True, agent)["hover_assist"] is True
    _, other = open_agent(svc, "other")
    with pytest.raises(RelayError) as exc:
        svc.set_sim_assist(vid, False, other, app_control=True)
    assert exc.value.code == "claimed_by_other"
    svc.update_settings({"agents_app_control": False})
    svc.unclaim(vid, agent)
    with pytest.raises(RelayError) as exc:
        svc.set_sim_assist(vid, False, other, app_control=True)
    assert exc.value.code == "app_control_disabled"


@pytest.fixture
def client():
    svc = FleetService(listen=False, history=History(None), operator_token="op-token")
    app = create_app(svc)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.svc = svc
        yield c


def test_api_add_and_switch(client):
    op = {"X-Relay-Operator": "op-token"}
    added = client.post("/api/v1/sims", json={"count": 1, "hover_assist": False}, headers=op).json()["added"]
    vid = added[0]
    assert client.svc.vehicles[vid].driver.sim.hover_assist is False
    resp = client.post(f"/api/v1/sims/{vid}/assist", json={"on": True}, headers=op)
    assert resp.status_code == 200 and resp.json() == {"id": vid, "hover_assist": True}
    resp = client.post(f"/api/v1/sims/{vid}/assist", json={}, headers=op)
    assert resp.status_code == 400
    resp = client.post("/api/v1/sims/sim-9/assist", json={"on": True}, headers=op)
    assert resp.status_code == 404
    assert client.post(f"/api/v1/sims/{vid}/assist", json={"on": True}).status_code == 401


def test_fake_vehicle_no_assist_flag():
    from fake_vehicle import FakeVehicle

    assert FakeVehicle(http=None, bind=("127.0.0.9", 14555)).autopilot.hover_assist is True
    fake = FakeVehicle(http=None, bind=("127.0.0.9", 14555), hover_assist=False, turbulence=0.5)
    assert fake.autopilot.hover_assist is False and fake.autopilot.turbulence == pytest.approx(0.5)
