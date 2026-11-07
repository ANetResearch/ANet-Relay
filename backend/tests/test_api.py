"""REST and WebSocket API: auth, errors, rate limits, OpenAPI, stream (03 §6.2, §10)."""

import time

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from relay.api.app import create_app
from relay.config import VERSION
from relay.fleet import FleetService
from relay.history import History

OP = {"X-Relay-Operator": "op-token"}


@pytest.fixture
def client():
    svc = FleetService(listen=False, history=History(None), operator_token="op-token")
    app = create_app(svc)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.svc = svc
        yield c


def session(client, name="harness"):
    body = client.post("/api/v1/sessions", json={"name": name}).json()
    return {"X-Relay-Session": body["token"]}, body


def wait_for(fn, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


def error(resp):
    body = resp.json()
    assert set(body["error"]) == {"code", "message_en", "message_zh", "detail"}
    return body["error"]["code"]


def test_health_capabilities_openapi(client):
    assert client.get("/api/v1/health").json()["ok"] is True
    caps = client.get("/api/v1/capabilities").json()
    assert {s["name"] for s in caps["skills"]} == {"takeoff", "land", "hold", "rtl", "goto", "move", "yaw"}
    assert caps["limits"]["stick_timeout_s"] == 0.8
    spec = client.get("/openapi.json").json()
    assert "/api/v1/vehicles/{vid}/possess" in spec["paths"]
    assert "Fleet" in spec["components"]["schemas"]


def test_operator_auth(client):
    assert error(client.post("/api/v1/sims")) == "auth_required"
    assert (
        error(client.post("/api/v1/sims", headers={"X-Relay-Operator": "nope"})) == "invalid_operator_token"
    )
    headers, _ = session(client)
    resp = client.post("/api/v1/sims", headers=headers)
    assert resp.status_code == 403 and error(resp) == "operator_required"
    assert client.post("/api/v1/sims", headers=OP, json={"count": 1}).json() == {"added": ["sim-1"]}


def test_session_auth_and_claim(client):
    client.post("/api/v1/sims", headers=OP)
    resp = client.post("/api/v1/vehicles/sim-1/claim", headers=OP)
    assert resp.status_code == 403 and error(resp) == "session_required"
    resp = client.post("/api/v1/vehicles/sim-1/claim", headers={"X-Relay-Session": "bogus"})
    assert resp.status_code == 401 and error(resp) == "session_expired"
    a, _body_a = session(client, "a")
    b, _ = session(client, "b")
    assert client.post("/api/v1/vehicles/sim-1/claim", headers=a).json()["claim"]["name"] == "a"
    resp = client.post("/api/v1/vehicles/sim-1/claim", headers=b)
    assert resp.status_code == 409 and error(resp) == "claimed_by_other"
    # The operator cannot impersonate an agent's control; the agent cannot impersonate the operator.
    resp = client.post("/api/v1/vehicles/sim-1/possess", headers=a, json={"input": "keyboard"})
    assert resp.status_code == 403
    assert (
        client.post("/api/v1/vehicles/sim-1/possess", headers=OP, json={"input": "keyboard"}).status_code
        == 200
    )
    resp = client.post(
        "/api/v1/vehicles/sim-1/sticks", headers=a, json={"roll": 0, "pitch": 0, "yaw": 0, "throttle": 0}
    )
    assert resp.status_code == 409 and error(resp) == "claim_suspended"
    assert resp.json()["error"]["detail"]["possession"]["input"] == "keyboard"
    sessions = client.get("/api/v1/sessions").json()["sessions"]
    assert "token" not in sessions[0] and sessions[0]["calls"]
    assert client.delete("/api/v1/sessions", headers=a).json()["released"] == ["sim-1"]


def test_errors_are_uniform(client):
    resp = client.get("/api/v1/vehicles/nope")
    assert resp.status_code == 404 and error(resp) == "vehicle_not_found"
    assert resp.json()["error"]["message_zh"]
    resp = client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404 and error(resp) == "not_found"
    resp = client.post("/api/v1/vehicles/sim-1/possess", headers=OP, json={"input": "joystick"})
    assert resp.status_code == 400 and error(resp) == "invalid_request"
    resp = client.post("/api/v1/sessions", json={})
    assert resp.status_code == 400


def test_rate_limit(client):
    headers, _ = session(client)
    sessions = client.app.state.service.sessions
    frozen = sessions.clock()
    sessions.clock = lambda: frozen  # no refill while the burst runs
    codes = [client.post("/api/v1/sessions/heartbeat", headers=headers).status_code for _ in range(25)]
    assert codes.count(200) == 20 and codes[-1] == 429
    resp = client.post("/api/v1/sessions/heartbeat", headers=headers)
    assert error(resp) == "rate_limited" and resp.headers["retry-after"] == "1"


def test_host_guard_and_cors(client):
    resp = client.get("/api/v1/health", headers={"Host": "evil.example:8765"})
    assert resp.status_code == 403 and error(resp) == "untrusted_host"
    ok = client.options(
        "/api/v1/fleet", headers={"Origin": "app://relay", "Access-Control-Request-Method": "GET"}
    )
    assert ok.headers.get("access-control-allow-origin") == "app://relay"
    bad = client.options(
        "/api/v1/fleet", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in bad.headers


def test_arm_needs_control_and_settings(client):
    client.post("/api/v1/sims", headers=OP)
    assert wait_for(lambda: client.get("/api/v1/fleet").json()["vehicles"][0]["link"]["state"] == "ok")
    resp = client.post("/api/v1/vehicles/sim-1/arm", headers=OP, json={"arm": True})
    assert error(resp) == "not_possessed"
    client.post("/api/v1/vehicles/sim-1/possess", headers=OP, json={"input": "keyboard"})
    assert (
        client.post("/api/v1/vehicles/sim-1/mode", headers=OP, json={"name": "ALT_HOLD"}).json()["mode"]
        == "ALT_HOLD"
    )
    assert client.post("/api/v1/vehicles/sim-1/arm", headers=OP, json={"arm": True}).json()["armed"] is True
    assert client.post("/api/v1/vehicles/sim-1/arm", headers=OP, json={"arm": False}).json()["armed"] is False
    resp = client.post("/api/v1/vehicles/sim-1/mode", headers=OP, json={"name": "WARP"})
    assert error(resp) == "unknown_mode"
    resp = client.put("/api/v1/settings", headers=OP, json={"cell_warn_v": 3.2})
    assert error(resp) == "invalid_request"
    settings = client.put("/api/v1/settings", headers=OP, json={"safety_volume": {"x": 4}}).json()
    assert settings["safety_volume"]["x"] == 4.0 and settings["stick_timeout_s"] == 0.8
    assert "data_dir" in settings and "restart_fields" in settings
    # a client may echo the whole GET payload back; read-only fields are ignored
    echoed = client.put("/api/v1/settings", headers=OP, json=settings)
    assert echoed.status_code == 200 and echoed.json()["safety_volume"]["x"] == 4.0


def test_params_write_and_readback(client):
    client.post("/api/v1/sims", headers=OP)
    assert wait_for(lambda: client.get("/api/v1/vehicles/sim-1/params").json()["complete"], 6)
    resp = client.post(
        "/api/v1/vehicles/sim-1/params", headers=OP, json={"name": "WPNAV_SPEED", "value": 150}
    )
    assert error(resp) == "confirm_required"
    resp = client.post(
        "/api/v1/vehicles/sim-1/params",
        headers=OP,
        json={"name": "ANGLE_MAX", "value": 2500.5, "confirm": "ANGLE_MAX"},
    )
    assert error(resp) == "param_type"
    resp = client.post(
        "/api/v1/vehicles/sim-1/params",
        headers=OP,
        json={"name": "WPNAV_SPEED", "value": 150, "confirm": "WPNAV_SPEED"},
    )
    assert resp.json()["value"] == 150
    table = {p["name"]: p for p in client.get("/api/v1/vehicles/sim-1/params").json()["params"]}
    assert table["WPNAV_SPEED"]["value"] == 150 and table["ANGLE_MAX"]["type"] == "int16"


def test_courses_topology_events_history(client):
    courses = client.get("/api/v1/courses").json()
    assert [c["id"] for c in courses["courses"]][:3] == ["cage-loop", "slalom", "climb"]
    resp = client.put("/api/v1/courses/cage-loop", headers=OP, json={"rings": []})
    assert error(resp) == "course_preset_readonly"
    ok = client.put(
        "/api/v1/courses/two", headers=OP, json={"rings": [{"c": [0, 0.5, 1], "yaw_deg": 0, "r": 0.4}] * 2}
    )
    assert ok.status_code == 200
    client.post("/api/v1/sims", headers=OP, json={"count": 2})
    assert wait_for(lambda: len(client.get("/api/v1/topology").json()["relations"]) == 1)
    events = client.get("/api/v1/events", params={"after": 0}).json()
    assert events["last_seq"] >= 2 and events["events"][0]["kind"] == "sim_added"
    assert wait_for(lambda: client.get("/api/v1/history/sessions").json()["sessions"])
    sid = client.get("/api/v1/history/sessions").json()["sessions"][0]["id"]
    time.sleep(1.2)
    detail = client.get(f"/api/v1/history/sessions/{sid}").json()
    assert detail["samples"]["t"]
    export = client.get(f"/api/v1/history/sessions/{sid}/export", params={"fmt": "csv"})
    assert export.headers["content-type"].startswith("text/csv")
    assert error(client.get("/api/v1/history/sessions/9999")) == "record_not_found"


def test_ws_hello_fleet_and_event(client):
    with client.websocket_connect("/api/v1/stream") as ws:
        hello = ws.receive_json()
        assert hello == {"type": "hello", "version": VERSION, "operator": False, "session": None}
        assert ws.receive_json()["type"] == "fleet"
        client.post("/api/v1/sims", headers=OP)
        for _ in range(50):
            msg = ws.receive_json()
            if msg["type"] == "event" and msg["data"]["kind"] == "sim_added":
                break
        else:
            pytest.fail("no sim_added event")


def test_ws_sticks_need_auth_and_disconnect_stops(client):
    client.post("/api/v1/sims", headers=OP)
    client.post("/api/v1/vehicles/sim-1/possess", headers=OP, json={"input": "screen"})
    with client.websocket_connect("/api/v1/stream") as ws:
        ws.receive_json()
        ws.send_json({"type": "sticks", "vehicle": "sim-1", "roll": 0.1, "pitch": 0, "yaw": 0, "throttle": 0})
        for _ in range(30):
            msg = ws.receive_json()
            if msg["type"] == "error":
                assert msg["error"]["code"] == "auth_required"
                break
        else:
            pytest.fail("anonymous sticks were not rejected")
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/api/v1/stream?operator=wrong") as ws:
        ws.receive_json()
    with client.websocket_connect("/api/v1/stream?operator=op-token") as ws:
        assert ws.receive_json()["operator"] is True
        for _ in range(5):
            ws.send_json(
                {"type": "sticks", "vehicle": "sim-1", "roll": 0.1, "pitch": 0, "yaw": 0, "throttle": 0}
            )
            time.sleep(0.02)
        assert wait_for(lambda: client.svc.vehicles["sim-1"].sticks is not None, 2)
    assert wait_for(lambda: client.svc.vehicles["sim-1"].sticks is None, 2)


def test_ws_session_sticks(client):
    client.post("/api/v1/sims", headers=OP)
    headers, body = session(client)
    client.post("/api/v1/vehicles/sim-1/claim", headers=headers)
    with client.websocket_connect(f"/api/v1/stream?session={body['token']}") as ws:
        assert ws.receive_json()["session"] == body["session_id"]
        ws.send_json({"type": "ping"})
        ws.send_json({"type": "sticks", "vehicle": "sim-1", "roll": 0, "pitch": 0.2, "yaw": 0, "throttle": 0})
        assert wait_for(lambda: client.svc.vehicles["sim-1"].sticks is not None, 2)
        assert client.svc.vehicles["sim-1"].sticks.source == "agent"
