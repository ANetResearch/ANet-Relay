"""Agent flight level (06 §5): at ``sticks`` agent sessions fly like a person with a radio."""

from __future__ import annotations

import pytest
from conftest import open_agent
from fastapi.testclient import TestClient

from relay import flight_level
from relay.api.app import create_app
from relay.authority import Actor
from relay.config import Settings
from relay.errors import RelayError
from relay.fleet import FleetService
from relay.history import History

pytestmark = pytest.mark.sticks_level

OP = Actor.operator()
HEADERS = {"X-Relay-Operator": "op-token"}


def boot(svc, stepper, position=True):
    (vid,) = svc.add_sims(1, "ardupilot", position)
    stepper.run(2.5)
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    return vid, session, agent


def refused(fn, *args, **kwargs):
    with pytest.raises(RelayError) as exc:
        fn(*args, **kwargs)
    assert exc.value.code == "sticks_only" and exc.value.status == 403, exc.value.code
    assert exc.value.detail["level"] == "sticks"
    return exc.value


def test_default_is_sticks_and_settings_show_it(svc):
    assert Settings().agent_flight_level == "sticks"
    assert svc.get_settings()["flight_level"] == {
        "level": "sticks",
        "session_skills": ["land", "sticks"],
        "safety_skills": ["land"],
    }
    with pytest.raises(RelayError) as exc:
        svc.update_settings({"agent_flight_level": "auto"})
    assert exc.value.code == "invalid_request"
    svc.update_settings({"agent_flight_level": "full"})
    assert svc.get_settings()["flight_level"]["session_skills"] is None


@pytest.mark.parametrize(
    ("skill", "params"),
    [
        ("takeoff", {"alt": 1.0}),
        ("hold", {}),
        ("rtl", {}),
        ("goto", {"x": 0, "y": 0, "z": 1}),
        ("move", {"forward": 1}),
        ("yaw", {"deg": 30}),
        ("velocity", {"vx": 0.2, "seconds": 1}),
        ("attitude", {"pitch": 5, "seconds": 1}),
    ],
)
def test_high_level_skills_are_refused(svc, stepper, skill, params):
    vid, _session, agent = boot(svc, stepper)
    error = refused(svc.run_skill, vid, agent, skill, params)
    assert error.detail["action"] == skill and "sticks" in error.message_en and "摇杆" in error.message_zh


def test_sticks_arm_manual_modes_land_and_stop_are_allowed(svc, stepper):
    vid, session, agent = boot(svc, stepper)
    v = svc.vehicles[vid]
    svc.set_mode(vid, agent, "STABILIZE")
    svc.set_mode(vid, agent, "ACRO")
    svc.set_mode(vid, agent, "STABILIZE")
    for mode in ("ALT_HOLD", "LOITER", "GUIDED", "AUTO", "RTL", "LAND"):
        refused(svc.set_mode, vid, agent, mode)
    svc.arm(vid, agent, True)
    stepper.run(0.3, renew=[session])
    assert v.armed
    svc.sticks(vid, agent, {"roll": 0, "pitch": 0, "yaw": 0, "throttle": 0.3})
    assert v.sticks.source == "agent"
    svc.run_skill(vid, agent, "sticks", {"throttle": 0.6, "seconds": 1.0})
    stepper.run(1.2, renew=[session])
    out = svc.run_skill(vid, agent, "land", {})
    assert out["skill"]["name"] == "land"
    (started,) = [
        e for e in svc.events.after(0) if e["kind"] == "skill_started" and e["data"]["skill"] == "land"
    ]
    assert started["data"]["safety"] is True
    svc.stop(vid, agent)
    stepper.run(0.5, renew=[session])


def test_plans_with_high_level_steps_are_refused(svc, stepper):
    vid, session, agent = boot(svc, stepper)
    error = refused(
        svc.run_plan, vid, agent, [{"action": "sticks", "throttle": 0.5, "seconds": 1}, {"action": "takeoff"}]
    )
    assert error.detail["action"] == "plan step takeoff"
    svc.arm(vid, agent, True)
    stepper.run(0.3, renew=[session])
    svc.run_plan(vid, agent, [{"action": "sticks", "throttle": 0.55, "seconds": 0.5}, {"action": "land"}])
    stepper.run(0.2, renew=[session])
    svc.cancel_plan(vid, agent)  # no internal hold (it is a position mode) for a limited session
    assert svc.vehicles[vid].mode == "STABILIZE"


def test_raw_commands_and_rc_aux_channels_are_refused(svc, stepper):
    vid, _session, agent = boot(svc, stepper)
    for name in (
        "MAV_CMD_NAV_TAKEOFF",
        "MAV_CMD_DO_SET_MODE",
        "MAV_CMD_NAV_RETURN_TO_LAUNCH",
        "MAV_CMD_DO_REPOSITION",
    ):
        refused(svc.raw_command, vid, agent, name, [])
    refused(svc.rc_override, vid, agent, {5: 1500}, None)
    out = svc.rc_override(vid, agent, {1: 1500, 2: 1500, 3: 1000, 4: 1500}, None)
    assert out is not None
    svc.rc_override(vid, agent, None, {"roll": 0.0, "throttle": 0.0})


def test_refly_only_recorded_sticks_in_manual_modes(svc, stepper):
    svc.update_settings({"agent_flight_level": "full"})
    vid, session, agent = boot(svc, stepper, position=False)  # recorded sticks need a no-position flight
    svc.run_plan(
        vid, agent, [{"action": "takeoff", "alt": 1.0}, {"action": "hover", "seconds": 1}, {"action": "land"}]
    )
    v = svc.vehicles[vid]
    assert stepper.until(lambda: not v.plan.running, 60, [session]) and v.plan.state == "done"
    assert stepper.until(lambda: not v.armed, 10, [session])
    stepper.run(1.0, [session])
    sid = v.record_session
    svc.update_settings({"agent_flight_level": "sticks"})
    options = svc.refly_options(sid)
    assert options["plan"] is not None and options["sticks"] is not None
    for mode in ("plan", "path"):
        if options.get(mode) is not None:
            refused(svc.refly, vid, agent, sid, mode)
    error = refused(svc.refly, vid, agent, sid, "sticks")  # flown in ALT_HOLD / LAND
    assert error.detail["action"].startswith("re-fly in mode")


def test_operator_and_full_level_are_unaffected(svc, stepper):
    vid, session, agent = boot(svc, stepper)
    assert not flight_level.limited(svc.settings, OP)
    assert flight_level.limited(svc.settings, agent)
    svc.update_settings({"agent_flight_level": "full"})
    svc.claim(vid, agent)
    svc.set_mode(vid, agent, "ALT_HOLD")
    svc.arm(vid, agent, True)
    stepper.run(0.3, renew=[session])
    assert svc.run_skill(vid, agent, "takeoff", {"alt": 0.5})["skill"]["name"] == "takeoff"


@pytest.fixture
def client():
    svc = FleetService(listen=False, history=History(None), operator_token="op-token")
    with TestClient(create_app(svc), base_url="http://127.0.0.1") as c:
        c.svc = svc
        yield c


def code(resp):
    return resp.json()["error"]["code"]


def test_rest_and_websocket_enforce_the_level(client):
    import time

    client.post("/api/v1/sims", headers=HEADERS, json={"count": 1})
    body = client.post("/api/v1/sessions", json={"name": "level"}).json()
    agent = {"X-Relay-Session": body["token"]}
    client.post("/api/v1/vehicles/sim-1/claim", headers=agent)
    resp = client.post("/api/v1/vehicles/sim-1/skills/takeoff", headers=agent, json={"alt": 1})
    assert resp.status_code == 403 and code(resp) == "sticks_only"
    resp = client.post("/api/v1/vehicles/sim-1/mode", headers=agent, json={"name": "GUIDED"})
    assert resp.status_code == 403 and code(resp) == "sticks_only"
    resp = client.post(
        "/api/v1/vehicles/sim-1/sticks", headers=agent, json={"roll": 0, "pitch": 0, "yaw": 0, "throttle": 0}
    )
    assert resp.status_code == 200, resp.json()
    with client.websocket_connect(f"/api/v1/stream?session={body['token']}") as ws:
        ws.receive_json()
        ws.send_json({"type": "sticks", "vehicle": "sim-1", "roll": 0, "pitch": 0.1, "yaw": 0, "throttle": 0})
        end = time.monotonic() + 2
        while time.monotonic() < end and (
            client.svc.vehicles["sim-1"].sticks is None or client.svc.vehicles["sim-1"].sticks.pitch != 0.1
        ):
            time.sleep(0.05)
        assert client.svc.vehicles["sim-1"].sticks.source == "agent"
    # the level is the operator's: an agent cannot raise it, even with app control
    client.svc.update_settings({"agents_app_control": True})
    resp = client.put("/api/v1/settings", headers=agent, json={"agent_flight_level": "full"})
    assert resp.status_code == 403 and code(resp) == "operator_required"
    resp = client.put("/api/v1/settings", headers=HEADERS, json={"agent_flight_level": "full"})
    assert resp.status_code == 200 and resp.json()["flight_level"]["level"] == "full"
    assert client.get("/api/v1/settings").json()["agent_flight_level"] == "full"
    resp = client.post("/api/v1/vehicles/sim-1/mode", headers=agent, json={"name": "GUIDED"})
    assert resp.status_code == 200, resp.json()
