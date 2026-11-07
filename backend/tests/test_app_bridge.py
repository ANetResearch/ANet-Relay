"""Application-level channel: ui_state, ui_command round trip and permissions (04 §3)."""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from relay import app_bridge
from relay.api.app import create_app
from relay.fleet import FleetService
from relay.history import History

OP = {"X-Relay-Operator": "op-token"}
WS = "/api/v1/stream?operator=op-token"
STATE = {
    "lang": "en",
    "workspace": "fly",
    "selected": None,
    "gamepads": [{"index": 0, "id": "pad", "name": "Pad"}],
}


@pytest.fixture
def client():
    svc = FleetService(listen=False, history=History(None), operator_token="op-token")
    with TestClient(create_app(svc), base_url="http://127.0.0.1") as c:
        c.svc = svc
        yield c


def session(client, name="harness"):
    return {"X-Relay-Session": client.post("/api/v1/sessions", json={"name": name}).json()["token"]}


def wait_for(fn, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(0.02)
    return False


def next_command(ws):
    for _ in range(200):
        msg = ws.receive_json()
        if msg["type"] == "ui_command":
            return msg
    raise AssertionError("no ui_command")


def post_async(client, body, headers=OP):
    out = {}

    def run():
        out["resp"] = client.post("/api/v1/app/ui", json=body, headers=headers)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, out


def connect_window(client, ws):
    ws.send_json({"type": "ui_state", "data": STATE})
    assert wait_for(lambda: client.get("/api/v1/app/ui").json()["connected"])


def test_unavailable_without_window(client):
    ui = client.get("/api/v1/app/ui").json()
    assert ui["state"] is None and ui["connected"] is False
    resp = client.post("/api/v1/app/ui", json={"action": "get_state"}, headers=OP)
    assert resp.status_code == 503 and resp.json()["error"]["code"] == "ui_unavailable"
    info = client.get("/api/v1/app").json()
    assert info["ui"]["connected"] is False and info["agents_app_control"] is True
    assert info["counts"]["vehicles"] == 0


def test_round_trip_and_state(client):
    with client.websocket_connect(WS) as ws:
        connect_window(client, ws)
        state = client.get("/api/v1/app/ui").json()
        assert state["state"]["lang"] == "en" and state["age_s"] is not None
        assert client.get("/api/v1/app/gamepads").json()["gamepads"][0]["index"] == 0
        assert client.get("/api/v1/app").json()["counts"]["gamepads"] == 1
        thread, out = post_async(client, {"action": "set_language", "args": {"lang": "zh"}})
        cmd = next_command(ws)
        assert (cmd["action"], cmd["args"], cmd["by"]) == ("set_language", {"lang": "zh"}, "operator")
        assert cmd["operator"] is True
        ws.send_json({"type": "ui_result", "id": cmd["id"], "ok": True, "data": {"lang": "zh"}})
        thread.join(5)
        assert out["resp"].status_code == 200
        assert out["resp"].json() == {"ok": True, "data": {"lang": "zh"}}
        # a session may call itself "operator"; only the flag tells the window who asked
        impostor = session(client, "operator")
        thread, out = post_async(client, {"action": "get_state", "args": {}}, headers=impostor)
        cmd = next_command(ws)
        assert cmd["by"] == "operator" and cmd["operator"] is False
        ws.send_json({"type": "ui_result", "id": cmd["id"], "ok": True, "data": {}})
        thread.join(5)
        assert out["resp"].status_code == 200
    events = client.get("/api/v1/events?after=0").json()["events"]
    ran = [e for e in events if e["kind"] == "app_command"]
    assert ran and ran[0]["data"] == {"action": "set_language", "args": {"lang": "zh"}, "by": "operator"}
    # the window is gone with its socket
    assert wait_for(lambda: not client.get("/api/v1/app/ui").json()["connected"])


def test_action_failure_and_timeout(client, monkeypatch):
    with client.websocket_connect(WS) as ws:
        connect_window(client, ws)
        thread, out = post_async(client, {"action": "select_vehicle", "args": {"vehicle": "nope"}})
        cmd = next_command(ws)
        ws.send_json({"type": "ui_result", "id": cmd["id"], "ok": False, "error": "vehicle_not_found"})
        thread.join(5)
        resp = out["resp"]
        assert resp.status_code == 409
        error = resp.json()["error"]
        assert error["code"] == "ui_action_failed" and error["detail"]["error"] == "vehicle_not_found"
        monkeypatch.setattr(app_bridge, "UI_WAIT_S", 0.3)
        resp = client.post("/api/v1/app/ui", json={"action": "get_state"}, headers=OP)
        assert resp.status_code == 504 and resp.json()["error"]["code"] == "ui_timeout"
        # a late answer for the abandoned command is ignored
        ws.send_json({"type": "ui_result", "id": "unknown", "ok": True})


def test_session_permission_follows_the_setting(client):
    headers = session(client, "scout")
    with client.websocket_connect(WS) as ws:
        connect_window(client, ws)
        thread, out = post_async(client, {"action": "set_view", "args": {"view": "top"}}, headers)
        cmd = next_command(ws)
        assert cmd["by"] == "scout"
        ws.send_json({"type": "ui_result", "id": cmd["id"], "ok": True})
        thread.join(5)
        assert out["resp"].status_code == 200
        assert (
            client.put("/api/v1/settings", json={"agents_app_control": False}, headers=OP).status_code == 200
        )
        resp = client.post("/api/v1/app/ui", json={"action": "set_view", "args": {}}, headers=headers)
        assert resp.status_code == 403 and resp.json()["error"]["code"] == "app_control_disabled"
        # the operator is not affected by the setting
        thread, out = post_async(client, {"action": "get_state"})
        cmd = next_command(ws)
        ws.send_json({"type": "ui_result", "id": cmd["id"], "ok": True, "data": {}})
        thread.join(5)
        assert out["resp"].status_code == 200
    assert client.post("/api/v1/app/ui", json={"action": "get_state"}).status_code == 401
    assert client.put("/api/v1/settings", json={"agents_app_control": "no"}, headers=OP).status_code == 400


def test_only_operator_sockets_carry_ui_messages(client):
    headers = session(client)
    token = headers["X-Relay-Session"]
    with client.websocket_connect(f"/api/v1/stream?session={token}") as ws:
        ws.send_json({"type": "ui_state", "data": STATE})
        for _ in range(50):
            msg = ws.receive_json()
            if msg["type"] == "error":
                assert msg["error"]["code"] == "operator_required"
                break
        else:
            raise AssertionError("no error")
    assert client.get("/api/v1/app/ui").json()["state"] is None


def test_newest_window_receives_commands(client):
    with client.websocket_connect(WS) as first, client.websocket_connect(WS) as second:
        first.send_json({"type": "ui_state", "data": STATE})
        assert wait_for(lambda: client.get("/api/v1/app/ui").json()["connected"])
        second.send_json({"type": "ui_state", "data": {**STATE, "lang": "zh"}})
        assert wait_for(lambda: client.get("/api/v1/app/ui").json()["state"]["lang"] == "zh")
        thread, out = post_async(client, {"action": "get_state"})
        cmd = next_command(second)
        second.send_json({"type": "ui_result", "id": cmd["id"], "ok": True, "data": {"ok": 1}})
        thread.join(5)
        assert out["resp"].json()["data"] == {"ok": 1}


def test_actions_catalogue(client):
    actions = {a["name"]: a for a in client.get("/api/v1/app/actions").json()["actions"]}
    assert {"get_state", "set_language", "screenshot", "possess", "window", "toggle_copilot"} <= set(actions)
    lang = actions["set_language"]
    assert lang["description_en"] and lang["description_zh"]
    assert lang["args"][0]["name"] == "lang" and lang["args"][0]["required"] is True
    assert lang["args"][0]["description_zh"]


def test_app_operations_follow_the_app_control_setting(client):
    agent = session(client, "app-agent")
    resp = client.post("/api/v1/sims", json={"count": 1}, headers=agent)
    assert resp.status_code == 200, resp.text
    vid = resp.json()["added"][0]
    assert client.post(f"/api/v1/vehicles/{vid}/label", json={"label": "A"}, headers=agent).status_code == 200
    assert client.put("/api/v1/settings", json={"retention_days": 30}, headers=agent).status_code == 200
    # echoing the current grant is harmless; changing it belongs to the operator
    assert client.put("/api/v1/settings", json={"agents_app_control": True}, headers=agent).status_code == 200
    resp = client.put("/api/v1/settings", json={"agents_app_control": False}, headers=agent)
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "operator_required"
    # possession and the operator's attestations never pass to an agent
    resp = client.post(f"/api/v1/vehicles/{vid}/possess", headers=agent)
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "operator_required"

    assert client.put("/api/v1/settings", json={"agents_app_control": False}, headers=OP).status_code == 200
    for method, path, body in (
        ("post", "/api/v1/sims", {"count": 1}),
        ("post", "/api/v1/stop-all", None),
        ("post", "/api/v1/network/retry", None),
        ("put", "/api/v1/settings", {"retention_days": 60}),
    ):
        resp = client.request(method.upper(), path, json=body, headers=agent)
        assert resp.status_code == 403 and resp.json()["error"]["code"] == "app_control_disabled", path
    resp = client.post("/api/v1/sims", json={"count": 1})
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "auth_required"


def test_set_param_needs_control_for_sessions(client):
    vid = client.post("/api/v1/sims", json={"count": 1}, headers=OP).json()["added"][0]
    agent = session(client, "param-agent")
    resp = client.post(
        f"/api/v1/vehicles/{vid}/params",
        json={"name": "ANGLE_MAX", "value": 3000, "confirm": "ANGLE_MAX"},
        headers=agent,
    )
    assert resp.status_code in (403, 409) and resp.json()["error"]["code"] != "operator_required", resp.text
