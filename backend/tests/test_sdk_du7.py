"""The Python SDK's 0.6.0 calls (control frames, speed tracking, built-in plans, the gimbal pod, the model)
against a real backend served by uvicorn (12 §2-§5)."""

from __future__ import annotations

import math
import sys
import threading
import time

import pytest
import uvicorn
from conftest import REPO, free_tcp_port

from relay.api.app import create_app
from relay.config import Settings
from relay.fleet import FleetService
from relay.history import History

sys.path.insert(0, str(REPO / "sdk" / "python"))

from anet_relay_client import RelayClient, RelayError

OP = "sdk-op"
pytestmark = pytest.mark.product_defaults


@pytest.fixture(scope="module")
def base():
    svc = FleetService(
        Settings(agent_flight_level="full"), None, operator_token=OP, listen=False, history=History(None)
    )
    svc.update_settings({"safety_volume": {"x": 12.0, "y": 12.0, "z": 4.0}})  # room for the moves below
    port = free_tcp_port()
    server = uvicorn.Server(uvicorn.Config(create_app(svc), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    client = RelayClient(url, operator_token=OP)
    end = time.monotonic() + 10
    while time.monotonic() < end:
        try:
            client.health()
            break
        except RelayError:
            time.sleep(0.05)
    yield url
    server.should_exit = True
    thread.join(timeout=5)


def ready(client, vid):
    end = time.monotonic() + 15
    while True:
        view = client.vehicle(vid)
        if view["link"]["state"] == "ok" and view["capabilities"]["position"]:
            return view
        assert time.monotonic() < end, view["link"]
        time.sleep(0.1)


@pytest.fixture(scope="module")
def du7(base):
    op = RelayClient(base, operator_token=OP)
    (vid,) = op.add_sims(1)  # no frame, no autopilot: the product default
    ready(op, vid)
    agent = RelayClient(base)
    agent.open_session("du7-test")
    agent.claim(vid)
    yield agent, op, vid
    agent.close()


def wait_for(predicate, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("condition not reached")


def along_left(agent, vid, before):
    """How far the aircraft moved to the left of its nose since ``before`` (a nav position), metres.

    ``nav`` is in the control frame: flu is (east, north, up) with the heading counter-clockwise from +x, frd
    (north, east, down) with a compass heading."""
    nav = agent.vehicle(vid)["nav"]
    dx, dy = nav["position"][0] - before[0], nav["position"][1] - before[1]
    yaw = math.radians(nav["yaw_deg"])
    if nav["frame"] == "frd":
        return dx * math.sin(yaw) - dy * math.cos(yaw)
    return dx * -math.sin(yaw) + dy * math.cos(yaw)


def test_default_simulator_is_the_anet_du7_with_a_pod(du7):
    agent, _op, vid = du7
    view = agent.vehicle(vid)
    assert view["model"] == "anet-du7" and view["autopilot"] == "PX4"
    assert view["nav"]["frame"] == "flu" and view["gimbal"]["kind"] == "sim"
    caps = agent.capabilities()
    assert caps["control_frame"] == "flu" and caps["sim_frame_default"] == "anet-du7"
    assert {"anet-du7", "micro"} <= set(caps["models"])


def test_add_sims_frame_and_set_model(base):
    op = RelayClient(base, operator_token=OP)
    (small,) = op.add_sims(1, frame="micro-120")
    ready(op, small)
    view = op.vehicle(small)
    assert view["model"] == "micro" and view["gimbal"] is None and view["sim"]["frame"] == "micro-120"
    with pytest.raises(RelayError) as exc:
        op.gimbal(small)
    assert exc.value.code == "gimbal_missing" and exc.value.status == 404
    with pytest.raises(RelayError) as exc:
        op.set_model(small, "anet-du7")  # a simulator's model follows its frame
    assert exc.value.code == "invalid_request"
    with pytest.raises(RelayError) as exc:
        RelayClient(base).set_model(small, "micro")  # operator only
    assert exc.value.code == "operator_required"
    op.remove_sims([small])


def test_plans_builtin_lists_the_box(base):
    plans = RelayClient(base).plans_builtin()
    box = next(p for p in plans if p["id"] == "du7_box")
    assert [s["action"] for s in box["steps"]] == ["takeoff", "move", "move", "move", "move", "land"]
    with pytest.raises(ValueError):
        RelayClient(base).plan("sim-1")  # neither steps nor builtin
    with pytest.raises(ValueError):
        RelayClient(base).plan("sim-1", [{"action": "land"}], builtin="du7_box")


def test_flight_frames_and_speed(du7):
    agent, _op, vid = du7
    agent.takeoff(vid, alt=1.0, speed=1.0, settle_s=0.5)
    view = agent.vehicle(vid)
    assert view["nav"]["position"][2] == pytest.approx(1.0, abs=0.3)
    start = view["nav"]["position"]
    heading0 = view["nav"]["yaw_deg"]

    # a named direction means the same in every frame; speed and settle_s are accepted
    seq = agent.last_seq()
    done = agent.move(vid, left=0.6, speed=1.0, settle_s=0.5)
    assert done["state"] == "done"
    started = next(e for e in agent.events(after=seq, vehicle=vid)["events"] if e["kind"] == "skill_started")
    # the params of the event are echoed in relay axes (forward, right, up) with frame "relay"
    assert started["data"]["params"]["frame"] == "relay"
    assert started["data"]["params"]["right"] == pytest.approx(-0.6)
    assert along_left(agent, vid, start) == pytest.approx(0.6, abs=0.25)
    assert agent.vehicle(vid)["nav"]["yaw_deg"] == pytest.approx(heading0, abs=3)  # the nose did not turn

    # x / y / z are body offsets in the frame: flu y is left, frd y is right
    start = agent.vehicle(vid)["nav"]["position"]
    agent.move(vid, y=-0.5, frame="flu", speed=1.0, settle_s=0.5)  # flu: right
    assert along_left(agent, vid, start) == pytest.approx(-0.5, abs=0.25)
    start = agent.vehicle(vid)["nav"]["position"]
    agent.move(vid, y=-0.5, frame="frd", speed=1.0, settle_s=0.5)  # frd: left
    assert along_left(agent, vid, start) == pytest.approx(0.5, abs=0.25)
    with pytest.raises(ValueError):
        agent.move(vid, forward=1, x=1)

    # yaw: flu counter-clockwise positive (heading in the flu frame grows), relay clockwise positive
    before = agent.vehicle(vid)["nav"]["yaw_deg"]
    agent.yaw(vid, 30)
    after = agent.vehicle(vid)["nav"]["yaw_deg"]
    assert (after - before + 180) % 360 - 180 == pytest.approx(30, abs=5)
    agent.yaw(vid, 30, frame="relay")  # clockwise: back by 30 degrees in flu terms
    assert (agent.vehicle(vid)["nav"]["yaw_deg"] - before + 180) % 360 - 180 == pytest.approx(0, abs=5)

    # velocity: body reference by default, flu: vy is left of the nose
    start = agent.vehicle(vid)["nav"]["position"]
    agent.velocity(vid, vy=0.4, seconds=1.5)
    assert along_left(agent, vid, start) > 0.3
    agent.velocity(vid, vx=0.0, vy=0.0, seconds=0.2, reference="local", frame="frd")
    with pytest.raises(RelayError) as exc:
        agent.move(vid, forward=1, speed=9)  # outside 0.1-5 m/s
    assert exc.value.code == "invalid_request"
    agent.hold(vid)


def test_goto_frames(du7):
    agent, _op, vid = du7
    x, y, z = agent.vehicle(vid)["nav"]["position"]
    agent.goto(vid, x + 0.5, y, z, speed=1.0, settle_s=0.5)
    assert agent.vehicle(vid)["nav"]["position"][0] - x == pytest.approx(0.5, abs=0.25)
    # frd: x is north, y is east, z is down; the same point is (y, x, -z) in flu
    agent.goto(vid, y, x, -z, frame="frd", speed=1.0, settle_s=0.5)
    nav = agent.vehicle(vid)["nav"]["position"]
    assert nav[0] == pytest.approx(x, abs=0.25) and nav[1] == pytest.approx(y, abs=0.25)


def test_gimbal_calls(du7):
    agent, op, vid = du7
    view = agent.gimbal(vid)
    assert view["kind"] == "sim" and view["lens"] == "visible1" and view["zoom_range"] == [1.0, 30.0]

    done = agent.gimbal_angle(vid, 20.0, -45.0)
    assert done["id"] == vid and done["gimbal"]["mode"] == "angle"
    wait_for(lambda: math.isclose(agent.gimbal(vid)["yaw_deg"], 20.0, abs_tol=1.0))
    assert agent.gimbal(vid)["pitch_deg"] == pytest.approx(-45.0, abs=1.0)

    agent.gimbal_rate(vid, yaw_dps=10.0)
    assert agent.gimbal(vid)["mode"] == "rate"
    agent.gimbal_do(vid, "stop")

    agent.gimbal_zoom(vid, 5.0)
    wait_for(lambda: math.isclose(agent.gimbal(vid)["zoom"], 5.0, abs_tol=0.05))
    narrow = agent.gimbal(vid)["fov_deg"][0]
    assert narrow < 20  # 60 degrees at 1x, 2 atan(tan 30 / 5) at 5x

    agent.gimbal_lens(vid, "ir1", pip="visible2")
    view = agent.gimbal(vid)
    assert view["lens"] == "ir1" and view["pip"] == "visible2"
    with pytest.raises(RelayError) as exc:
        agent.gimbal_lens(vid, "ir2")
        agent.gimbal_zoom(vid, 2.0)  # ir2 is a fixed lens
    assert exc.value.code == "gimbal_unsupported"
    agent.gimbal_lens(vid, "visible1")

    photos = agent.gimbal(vid)["photos"]
    agent.gimbal_photo(vid)
    assert agent.gimbal(vid)["photos"] == photos + 1
    agent.gimbal_record(vid, True)
    assert agent.gimbal(vid)["recording"] is True
    agent.gimbal_record(vid, False)
    captured = agent.gimbal_captures(vid, limit=5)
    assert captured["id"] == vid and captured["total"] >= 3
    kinds = [c["kind"] for c in captured["captures"]]
    assert kinds[-3:] == ["photo", "record_start", "record_stop"]
    photo = captured["captures"][-3]
    assert photo["lens"] == "visible1" and photo["nav"]["frame"] == "flu" and "yaw_deg" in photo["gimbal"]

    agent.gimbal_look_at(vid, 3.0, 0.0, 0.0)  # three metres along +x (flu: east)
    assert agent.gimbal(vid)["mode"] == "angle"
    with pytest.raises(RelayError) as exc:
        agent.gimbal_do(vid, "angle", yaw_deg=500, pitch_deg=0)
    assert exc.value.code == "invalid_request"

    # attaching and detaching are the operator's
    with pytest.raises(RelayError) as exc:
        agent.gimbal_detach(vid)
    assert exc.value.code in ("operator_required", "auth_required")
    assert op.gimbal_detach(vid) == {"id": vid, "detached": True}
    with pytest.raises(RelayError) as exc:
        agent.gimbal(vid)
    assert exc.value.code == "gimbal_missing"
    assert op.gimbal_attach(vid, "sim")["gimbal"]["kind"] == "sim"
    with pytest.raises(RelayError) as exc:
        op.gimbal_attach(vid, "tcp", host="10.0.0.5")  # a simulated aircraft carries a simulated pod
    assert exc.value.code == "gimbal_unsupported"


def test_update_settings_control_frame(du7, base):
    agent, op, vid = du7
    try:
        op.update_settings(control_frame="frd")
        assert op.capabilities()["control_frame"] == "frd"
        assert agent.vehicle(vid)["nav"]["frame"] == "frd"
        # in frd the same unnamed y is to the right
        start = agent.vehicle(vid)["nav"]["position"]
        agent.move(vid, y=0.5, speed=1.0, settle_s=0.5)
        assert along_left(agent, vid, start) == pytest.approx(-0.5, abs=0.25)  # frd: y is right
    finally:
        op.update_settings(control_frame="flu")
    agent.land(vid)


def test_builtin_plan_starts(du7):
    agent, _op, vid = du7
    plan = agent.plan(vid, builtin="du7_box", wait=False)
    assert plan["name"] == "du7_box" and plan["count"] == 6 and plan["state"] == "running"
    agent.cancel_plan(vid)
    wait_for(lambda: (agent.vehicle(vid).get("plan") or {}).get("state") != "running")
    with pytest.raises(RelayError) as exc:
        agent.plan(vid, builtin="nope", wait=False)
    assert exc.value.code == "invalid_request"
