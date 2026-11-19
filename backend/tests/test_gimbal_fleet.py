"""The gimbal pod on the fleet and through the API (12 §5): attach, commands, authority, events, captures."""

from __future__ import annotations

import math

import pytest
from conftest import open_agent
from fastapi.testclient import TestClient

from relay.api.app import create_app
from relay.api.schemas import Fleet
from relay.api.schemas_gimbal import GimbalCaptures, GimbalView
from relay.authority import Actor
from relay.errors import RelayError
from relay.gimbal import SimPod
from relay.gimbal import carrier as geo

OP_HEADER = {"X-Relay-Operator": "op-token"}
OP = Actor.operator()
API = "/api/v1"


def du7(svc, stepper, count: int = 1) -> list[str]:
    """Simulators, the first ``count`` of them ANetDU7 with their pod and the rest micros without one.

    The models are set explicitly (and the pods the fleet may have attached are dropped first), so the tests
    do not depend on which model ``add_sims`` gives a new simulator by default."""
    ids = svc.add_sims(count + 1)
    for i, vid in enumerate(ids):
        v = svc.vehicles[vid]
        svc._gimbal_drop(v)
        v.model = "anet-du7" if i < count else "micro"
        svc._gimbal_autoattach(v)
    stepper.run(0.5)
    return ids


@pytest.fixture
def client(svc):
    with TestClient(create_app(svc, manage_service=False), base_url="http://127.0.0.1") as c:
        yield c


def code(exc: pytest.ExceptionInfo[RelayError]) -> str:
    return exc.value.code


def vehicle_view(svc, vid):
    return next(v for v in svc.fleet()["vehicles"] if v["id"] == vid)


def kinds(svc, vid=None):
    return [e["kind"] for e in svc.events.after(0) if vid is None or e["vehicle"] == vid]


def test_du7_simulator_has_a_gimbal_and_a_micro_has_none(svc, stepper):
    a, b = du7(svc, stepper)
    fleet = svc.fleet()
    Fleet.model_validate(fleet)  # the snapshot still fits the schema, gimbal block included
    assert vehicle_view(svc, a)["gimbal"]["kind"] == "sim"
    assert vehicle_view(svc, b)["gimbal"] is None
    assert svc.vehicles[b].kind == "sim" and not isinstance(svc._gimbals.get(b), SimPod)
    # the snapshot block is the view the endpoint serves
    assert GimbalView.model_validate(svc.gimbal_view(a)).mode == "follow"


def test_autoattach_only_for_the_du7_model_and_only_once(svc, stepper):
    a, b = du7(svc, stepper)
    pod = svc._gimbals[a].pod
    svc._gimbal_autoattach(svc.vehicles[a])
    assert svc._gimbals[a].pod is pod
    svc._gimbal_autoattach(svc.vehicles[b])  # a micro (no ``model``, or "micro") gets nothing by itself
    svc.vehicles[b].model = "micro"
    svc._gimbal_autoattach(svc.vehicles[b])
    assert b not in svc._gimbals


def test_micro_gives_gimbal_missing_for_every_operation(svc, stepper, client):
    _a, b = du7(svc, stepper)
    for call in (
        lambda: svc.gimbal_view(b),
        lambda: svc.gimbal_command(b, "photo", {}, OP),
        lambda: svc.gimbal_detach(b, OP),
        lambda: svc.gimbal_captures(b),
    ):
        with pytest.raises(RelayError) as exc:
            call()
        assert code(exc) == "gimbal_missing" and exc.value.status == 404
        assert "SIM-2" in exc.value.message_en and "SIM-2" in exc.value.message_zh
    for method, path, body in (
        ("get", f"/vehicles/{b}/gimbal", None),
        ("post", f"/vehicles/{b}/gimbal", {"action": "photo"}),
        ("get", f"/vehicles/{b}/gimbal/captures", None),
        ("delete", f"/vehicles/{b}/gimbal", None),
    ):
        r = client.request(method, API + path, json=body, headers=OP_HEADER)
        assert r.status_code == 404 and r.json()["error"]["code"] == "gimbal_missing", path
    assert client.get(f"{API}/vehicles/nope/gimbal").json()["error"]["code"] == "vehicle_not_found"


def test_attach_sim_to_a_micro_then_detach(svc, stepper, client):
    _a, b = du7(svc, stepper)
    r = client.post(f"{API}/vehicles/{b}/gimbal/attach", json={"transport": "sim"}, headers=OP_HEADER)
    assert r.status_code == 200 and r.json()["id"] == b
    GimbalView.model_validate(r.json()["gimbal"])
    stepper.run(0.3)  # the fleet snapshot is rebuilt at 10 Hz
    assert vehicle_view(svc, b)["gimbal"]["kind"] == "sim"
    attached = [e for e in svc.events.after(0) if e["kind"] == "gimbal_attached"]
    assert attached[-1]["vehicle"] == b and "simulated pod" in attached[-1]["text_en"]
    assert "模拟吊舱" in attached[-1]["text_zh"]
    assert (
        client.post(f"{API}/vehicles/{b}/gimbal", json={"action": "photo"}, headers=OP_HEADER).status_code
        == 200
    )
    r = client.delete(f"{API}/vehicles/{b}/gimbal", headers=OP_HEADER)
    assert r.json() == {"id": b, "detached": True}
    stepper.run(0.3)
    assert vehicle_view(svc, b)["gimbal"] is None
    assert kinds(svc, b)[-1] == "gimbal_detached"
    assert client.get(f"{API}/vehicles/{b}/gimbal").status_code == 404
    again = client.delete(f"{API}/vehicles/{b}/gimbal", headers=OP_HEADER)
    assert again.status_code == 404 and again.json()["error"]["code"] == "gimbal_missing"


def test_attach_checks(svc, stepper, client):
    a, _b = du7(svc, stepper)
    bad = [
        ({"transport": "tcp", "host": "10.0.0.5"}, "gimbal_unsupported"),  # a simulator has a simulated pod
        ({"transport": "serial", "device": "/dev/ttyUSB0"}, "gimbal_unsupported"),
        ({"transport": "tcp"}, "invalid_request"),
        ({"transport": "tcp", "host": "a b"}, "invalid_request"),
        ({"transport": "serial", "device": "http://x"}, "serial_unavailable"),
        ({"transport": "usb"}, "invalid_request"),
        ({"transport": "tcp", "host": "10.0.0.5", "port": 70000}, "invalid_request"),
    ]
    for body, expected in bad:
        r = client.post(f"{API}/vehicles/{a}/gimbal/attach", json=body, headers=OP_HEADER)
        assert r.json()["error"]["code"] == expected, body
    assert client.post(f"{API}/vehicles/{a}/gimbal/attach", json={"transport": "sim"}).status_code == 401


def test_a_session_may_not_attach_or_detach(svc, stepper, client):
    a, _b = du7(svc, stepper)
    _session, actor = open_agent(svc)
    svc.claim(a, actor)
    token = {"X-Relay-Session": client.post(f"{API}/sessions", json={"name": "x"}).json()["token"]}
    r = client.post(f"{API}/vehicles/{a}/gimbal/attach", json={"transport": "sim"}, headers=token)
    assert r.status_code == 403 and r.json()["error"]["code"] == "operator_required"
    assert client.delete(f"{API}/vehicles/{a}/gimbal", headers=token).status_code == 403
    with pytest.raises(RelayError) as exc:
        svc.gimbal_attach(a, {"transport": "sim"}, actor)
    assert code(exc) == "operator_required"
    with pytest.raises(RelayError):
        svc.gimbal_detach(a, actor)


def test_command_authority_operator_controller_and_nobody_else(svc, stepper, client):
    a, _b = du7(svc, stepper)
    agent, actor = open_agent(svc, "alpha")
    _other, other_actor = open_agent(svc, "beta")
    with pytest.raises(RelayError) as exc:
        svc.gimbal_command(a, "photo", {}, actor)  # nobody controls the aircraft
    assert code(exc) == "not_claimed"
    svc.gimbal_command(a, "photo", {}, OP)  # the operator always may
    svc.claim(a, actor)
    assert svc.gimbal_command(a, "rate", {"yaw_dps": 5}, actor)["gimbal"]["mode"] == "rate"
    with pytest.raises(RelayError) as exc:
        svc.gimbal_command(a, "photo", {}, other_actor)
    assert code(exc) == "claimed_by_other"
    svc.possess(a, "keyboard")  # a person flies it: the claim is suspended
    with pytest.raises(RelayError) as exc:
        svc.gimbal_command(a, "photo", {}, actor)
    assert code(exc) == "claim_suspended"
    with pytest.raises(RelayError) as exc:
        svc.gimbal_command(a, "photo", {}, other_actor)
    assert code(exc) == "possessed_by_human"
    assert svc.gimbal_command(a, "photo", {}, OP)["gimbal"]["photos"] == 2  # the operator still may
    svc.release(a)
    svc.claim(a, actor)
    token = client.post(f"{API}/sessions", json={"name": "http"}).json()
    r = client.post(
        f"{API}/vehicles/{a}/gimbal", json={"action": "photo"}, headers={"X-Relay-Session": token["token"]}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "claimed_by_other"
    assert client.post(f"{API}/vehicles/{a}/gimbal", json={"action": "photo"}).status_code == 401
    del agent


@pytest.mark.sticks_level
def test_the_gimbal_is_not_a_flight_action_so_every_flight_level_may_use_it(svc, stepper):
    a, _b = du7(svc, stepper)
    assert svc.settings.agent_flight_level == "sticks"
    _session, actor = open_agent(svc)
    svc.claim(a, actor)
    out = svc.gimbal_command(a, "angle", {"yaw_deg": 10, "pitch_deg": -20}, actor)
    assert out["gimbal"]["mode"] == "angle"


def test_commands_move_the_pod_with_the_fleet_tick(svc, stepper, client):
    a, _b = du7(svc, stepper)
    r = client.post(
        f"{API}/vehicles/{a}/gimbal",
        json={"action": "angle", "yaw_deg": 40, "pitch_deg": -60},
        headers=OP_HEADER,
    )
    assert r.status_code == 200 and r.json()["id"] == a and r.json()["gimbal"]["yaw_deg"] == 0.0
    stepper.run(2.5)
    g = client.get(f"{API}/vehicles/{a}/gimbal").json()
    GimbalView.model_validate(g)
    assert g["yaw_deg"] == pytest.approx(40.0, abs=0.3) and g["pitch_deg"] == pytest.approx(-60.0, abs=0.3)
    assert vehicle_view(svc, a)["gimbal"]["yaw_deg"] == pytest.approx(40.0, abs=0.3)
    assert 0.0 <= g["age_s"] < 0.1
    client.post(f"{API}/vehicles/{a}/gimbal", json={"action": "rate", "yaw_dps": 30.0}, headers=OP_HEADER)
    stepper.run(1.0)
    assert client.get(f"{API}/vehicles/{a}/gimbal").json()["yaw_deg"] > 60.0


def test_the_pod_follows_the_simulated_aircraft(svc, stepper):
    ids = svc.add_sims(2)
    b = ids[1]  # spawns 2 m east
    svc.vehicles[b].model = "anet-du7"
    svc._gimbal_autoattach(svc.vehicles[b])
    stepper.run(0.5)
    c = svc._gimbal_carrier(svc.vehicles[b])
    s = svc.vehicles[b].driver.sim.quad.s
    assert c.pos == pytest.approx((s.p[0] + 2.0, s.p[1], s.p[2]), abs=1e-6)
    assert (c.roll, c.pitch, c.yaw) == (s.roll, s.pitch, s.yaw) and c.lat != 0.0 and c.wall == svc.wall()
    # lock holds the line of sight as the aircraft yaws under it
    svc.gimbal_command(b, "angle", {"yaw_deg": 0.0, "pitch_deg": -30.0, "space": "earth"}, OP)
    stepper.run(1.5)
    s.set_attitude(0.0, 0.0, math.radians(60.0))
    stepper.run(1.5)
    assert svc.gimbal_view(b)["yaw_deg"] == pytest.approx(-60.0, abs=0.5)


def test_look_at_solves_the_angles_in_the_requested_frame(svc, stepper, client, monkeypatch):
    a, _b = du7(svc, stepper)
    s = svc.vehicles[a].driver.sim.quad.s
    s.p[:] = [0.0, 0.0, 10.0]  # 10 m up at the origin (the simulator's own frame, spawn offset 0)
    stepper.run(0.2)

    def target(**params):
        svc.gimbal_command(a, "look_at", params, OP)
        _kind, az, el = svc._gimbals[a].pod.sim.tgt
        return az, el

    az, el = target(x=0.0, y=10.0, z=0.0)  # flu: 10 m north on the ground
    assert (az, el) == (pytest.approx(0.0, abs=0.1), pytest.approx(-45.0, abs=0.1))
    az, el = target(x=10.0, y=0.0, z=0.0)  # east
    assert (az, el) == (pytest.approx(90.0, abs=0.1), pytest.approx(-45.0, abs=0.1))
    az, el = target(x=10.0, y=0.0, z=-10.0, frame="frd")  # frd local (NED): 10 m north, 10 m up: level
    assert (az, el) == (pytest.approx(0.0, abs=0.1), pytest.approx(0.0, abs=0.1))
    monkeypatch.setattr(svc.settings, "control_frame", "frd", raising=False)  # the setting is the default
    az, el = target(x=0.0, y=10.0, z=0.0)  # north 0, east 10, on the ground: east, 45 degrees down
    assert (az, el) == (pytest.approx(90.0, abs=0.1), pytest.approx(-45.0, abs=0.1))
    stepper.run(3.0)
    r = client.post(
        f"{API}/vehicles/{a}/gimbal",
        json={"action": "look_at", "x": 0, "y": 10, "z": 0, "frame": "flu"},
        headers=OP_HEADER,
    )
    assert r.status_code == 200 and r.json()["gimbal"]["mode"] == "angle"
    stepper.run(3.0)
    los = svc._gimbals[a].pod.sim.los()
    assert geo.ned_to_angles(geo.enu_to_ned(los)) == pytest.approx((0.0, -45.0), abs=0.5)
    for bad in (
        {},
        {"x": 1, "y": 2},
        {"x": "a", "y": 1, "z": 1},
        {"x": 0, "y": 0, "z": 10},
    ):  # last: at the aircraft
        with pytest.raises(RelayError) as exc:
            svc.gimbal_command(a, "look_at", {**bad, "frame": "flu"}, OP)
        assert code(exc) == "invalid_request"
    with pytest.raises(RelayError):
        svc.gimbal_command(a, "look_at", {"x": 1, "y": 1, "z": 1, "frame": "enu"}, OP)


def test_api_validation(svc, stepper, client):
    a, _b = du7(svc, stepper)
    for body in (
        {"action": "dance"},
        {"action": "angle", "yaw_deg": 400, "pitch_deg": 0},
        {"action": "angle"},
        {"action": "zoom", "level": 99},
        {"action": "lens", "lens": "thermal"},
        {"action": "track", "x": 5},
        {"action": "follow"},
        {},
    ):
        r = client.post(f"{API}/vehicles/{a}/gimbal", json=body, headers=OP_HEADER)
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request", body
    r = client.post(
        f"{API}/vehicles/{a}/gimbal", json={"action": "lens", "lens": "visible2"}, headers=OP_HEADER
    )
    r = client.post(f"{API}/vehicles/{a}/gimbal", json={"action": "zoom", "level": 3}, headers=OP_HEADER)
    assert r.status_code == 409 and r.json()["error"]["code"] == "gimbal_unsupported"
    assert set(r.json()["error"]) == {"code", "message_en", "message_zh", "detail"}


def test_events_for_photo_record_and_lens(svc, stepper):
    a, _b = du7(svc, stepper)
    svc.gimbal_command(a, "photo", {}, OP)
    svc.gimbal_command(a, "photo", {}, OP)
    svc.gimbal_command(a, "record", {"on": True}, OP)
    svc.gimbal_command(a, "record", {"on": True}, OP)  # no change, no event
    svc.gimbal_command(a, "lens", {"lens": "ir1", "pip": "visible1"}, OP)
    svc.gimbal_command(a, "record", {"on": False}, OP)
    events = [e for e in svc.events.after(0) if e["kind"].startswith("gimbal_")]
    assert [e["kind"] for e in events] == [
        "gimbal_capture", "gimbal_capture", "gimbal_record", "gimbal_lens", "gimbal_record",
    ]  # fmt: skip
    photo, _, rec_on, lens, rec_off = events
    assert (
        photo["vehicle"] == a
        and photo["text_en"] == "SIM-1 took photo 1."
        and photo["text_zh"] == "SIM-1 拍照，第 1 张。"
    )
    assert events[1]["data"]["index"] == 2 and events[1]["data"]["kind"] == "photo"
    assert rec_on["data"]["on"] is True and rec_on["text_en"] == "SIM-1 gimbal recording started."
    assert rec_on["text_zh"] == "SIM-1 云台录像已开始。" and rec_off["text_en"].endswith("stopped.")
    assert (
        lens["data"] == {"lens": "ir1", "pip": "visible1"}
        and "ir1" in lens["text_en"]
        and "ir1" in lens["text_zh"]
    )
    from relay.api.schemas import Event

    for e in events:
        Event.model_validate(e)  # every kind is in the event schema


def test_every_gimbal_event_kind_has_text_in_both_languages():
    from relay.events import TEMPLATES, render

    for kind in (
        "gimbal_attached",
        "gimbal_detached",
        "gimbal_link",
        "gimbal_capture",
        "gimbal_record",
        "gimbal_lens",
    ):
        assert kind in TEMPLATES
        en, zh = render(
            kind,
            {
                "vehicle": "SIM-1",
                "detail_en": "x",
                "detail_zh": "y",
                "link": "ok",
                "link_zh": "已恢复",
                "index": 1,
                "lens": "ir1",
            },
        )
        assert "SIM-1" in en and "SIM-1" in zh and "{" not in en + zh and en != zh


def test_captures_list_and_limit(svc, stepper, client, monkeypatch):
    a, _b = du7(svc, stepper)
    stepper.run(0.3)
    monkeypatch.setattr(svc.settings, "control_frame", "frd", raising=False)
    stepper.run(0.05)
    for _ in range(3):
        svc.gimbal_command(a, "photo", {}, OP)
    svc.gimbal_command(a, "record", {"on": True}, OP)
    r = client.get(f"{API}/vehicles/{a}/gimbal/captures?limit=2")
    body = r.json()
    GimbalCaptures.model_validate(body)
    assert body["id"] == a and body["total"] == 4 and [c["seq"] for c in body["captures"]] == [3, 4]
    assert [c["kind"] for c in body["captures"]] == ["photo", "record_start"]
    c = body["captures"][0]
    assert c["nav"]["frame"] == "frd" and set(c["nav"]) >= {"position", "yaw_deg", "lat", "lon", "alt"}
    assert (
        c["t"] == pytest.approx(svc.wall(), abs=0.1)
        and c["lens"] == "visible1"
        and c["gimbal"]["pitch_deg"] == -30.0
    )
    assert client.get(f"{API}/vehicles/{a}/gimbal/captures?limit=0").status_code == 400
    assert len(client.get(f"{API}/vehicles/{a}/gimbal/captures").json()["captures"]) == 4


def test_removing_the_aircraft_drops_its_pod(svc, stepper):
    a, b = du7(svc, stepper)
    svc.gimbal_attach(b, {"transport": "sim"}, OP)
    svc.remove_vehicle(a)
    assert a not in svc._gimbals and b in svc._gimbals
    svc.remove_sims()
    assert svc._gimbals == {}
    svc._gimbal_shutdown()


def test_a_pod_whose_aircraft_vanished_is_swept_by_the_tick(svc, stepper):
    a, _b = du7(svc, stepper)
    del svc.vehicles[a]  # removed by some path that did not call _remove
    stepper.run(0.05)
    assert a not in svc._gimbals


def test_a_failing_pod_does_not_stop_the_tick(svc, stepper, monkeypatch, caplog):
    a, b, _c = du7(svc, stepper, 2)
    monkeypatch.setattr(svc._gimbals[a].pod, "step", lambda *args: 1 / 0)
    stepper.run(0.1)  # the tick survives; the other pod keeps stepping
    svc.gimbal_command(b, "rate", {"yaw_dps": 40}, OP)
    stepper.run(0.5)
    assert svc.gimbal_view(b)["yaw_deg"] > 5.0
    assert any("gimbal step failed" in r.message for r in caplog.records)
