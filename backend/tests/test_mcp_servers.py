"""The three MCP servers against a stub Relay HTTP server that records every request (04 §5, §7)."""

from __future__ import annotations

import io
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "mcp"))
sys.path.insert(0, str(REPO / "sdk" / "python"))

import anet_relay_app_mcp as app_mcp  # noqa: E402
import anet_relay_device_mcp as device_mcp  # noqa: E402
import anet_relay_mcp as flight_mcp  # noqa: E402
import relay_mcp_match as match_mcp  # noqa: E402
import relay_mcp_programs as programs_mcp  # noqa: E402

PNG = "iVBORw0KGgo="


class Stub:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.fail: dict[str, tuple[int, dict]] = {}
        self.expire_once = False
        self.opened = 0
        self.responses: dict[str, object] = {}


@pytest.fixture()
def stub():
    state = Stub()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _serve(self):
            parts = urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if length else None
            path = parts.path.removeprefix("/api/v1")
            if path == "/sessions" and self.command == "POST":
                state.opened += 1
                return self._json(
                    200,
                    {
                        "session_id": f"s{state.opened}",
                        "token": f"tok{state.opened}",
                        "name": body["name"],
                        "lease_s": 60,
                    },
                )
            if path == "/sessions/heartbeat" or (path == "/sessions" and self.command == "DELETE"):
                return self._json(200, {})
            state.requests.append(
                {
                    "method": self.command,
                    "path": path,
                    "query": parse_qs(parts.query),
                    "body": body,
                    "session": self.headers.get("X-Relay-Session"),
                    "operator": self.headers.get("X-Relay-Operator"),
                }
            )
            if state.expire_once:
                state.expire_once = False
                return self._json(401, {"error": {"code": "session_expired", "message_en": "expired"}})
            if path in state.fail:
                status, error = state.fail[path]
                return self._json(status, {"error": error})
            if path in state.responses:
                return self._json(200, state.responses[path])
            if path.endswith("/export"):
                data = b"t,x\n0,1\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/csv")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return None
            return self._json(200, {"ok": True})

        def _json(self, status, payload):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _serve

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    state.base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield state
    httpd.shutdown()
    httpd.server_close()


def make(module, stub, token=None):
    server = module.Server(stub.base, "t", operator_token=token)
    server.out = io.StringIO()
    return server


def rpc(server, method, params=None, rid=1):
    server.out.seek(0)
    server.out.truncate()
    msg = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
    worker = server.handle_line(json.dumps(msg))
    if worker:
        worker.join(timeout=20)
    return json.loads(server.out.getvalue().splitlines()[-1])


def call(server, name, args=None):
    reply = rpc(server, "tools/call", {"name": name, "arguments": args or {}})
    return reply.get("result", reply)


def text(result):
    return json.loads(result["content"][0]["text"])


def last(stub):
    return stub.requests[-1]


SERVERS = [
    (flight_mcp, "anet-relay"),
    (device_mcp, "anet-relay-device"),
    (app_mcp, "anet-relay-app"),
]


@pytest.mark.parametrize(("module", "server_name"), SERVERS)
def test_initialize_and_list(stub, module, server_name):
    server = make(module, stub)
    init = rpc(server, "initialize", {"protocolVersion": "2025-06-18"})["result"]
    assert init["serverInfo"]["name"] == server_name and init["protocolVersion"] == "2025-06-18"
    assert (
        rpc(server, "initialize", {"protocolVersion": "1999"}, 2)["result"]["protocolVersion"] == "2025-11-25"
    )
    tools = rpc(server, "tools/list")["result"]["tools"]
    assert tools and len({t["name"] for t in tools}) == len(tools)
    for tool in tools:
        schema = tool["inputSchema"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert set(schema["required"]) <= set(schema["properties"])
        assert len(tool["description"]) > 20
    assert rpc(server, "nope", rid=3)["error"]["code"] == -32601
    assert rpc(server, "tools/call", {"name": "missing"}, 4)["error"]["code"] == -32602


def test_prefixes_and_counts(stub):
    names = {
        flight_mcp: [t["name"] for t in flight_mcp.TOOLS],
        device_mcp: [t.name for t in device_mcp.build_tools()],
        app_mcp: [t.name for t in app_mcp.build_tools()],
    }
    assert len(names[flight_mcp]) == 34 and {"relay_sticks_for", "relay_refly"} <= set(names[flight_mcp])
    assert len(flight_mcp.STICKS_TOOL_NAMES) == 26 and TWIN_TOOLS <= flight_mcp.STICKS_TOOL_NAMES
    assert all(n.startswith("relay_") for n in names[flight_mcp])
    assert all(n.startswith("device_") for n in names[device_mcp])
    # WP-4b: the program tools keep the names of the 06 §6 table
    # WP-B: and the dogfight tools of 07 §3
    extra = set(programs_mcp.PROGRAM_TOOL_NAMES) | set(match_mcp.MATCH_TOOL_NAMES)
    extra |= {"relay_hitl", "relay_serial_ports"}  # 11 §12: named like the Copilot tools
    assert len(names[app_mcp]) == 67 and extra - set(names[app_mcp]) == set()
    assert not {"relay_hitl", "relay_serial_ports"} & set(names[flight_mcp])  # HITL: app server only
    assert all(n.startswith("app_") or n in extra for n in names[app_mcp])
    ui = {
        "app_set_language",
        "app_set_workspace",
        "app_select_vehicle",
        "app_set_view",
        "app_open_inspector",
        "app_close_inspector",
        "app_open_settings",
        "app_open_connect",
        "app_close_dialogs",
        "app_set_pref",
        "app_notify",
        "app_bind_gamepad",
        "app_unbind_gamepad",
        "app_possess",
        "app_release",
        "app_open_record",
        "app_replay",
        "app_toggle_copilot",
        "app_window",
        "app_screenshot",
    }
    assert ui <= set(names[app_mcp])


DEVICE_CASES = [
    ("device_overview", {"vehicle": "sim-1"}, "GET", "/vehicles/sim-1/device", None),
    (
        "device_message",
        {"vehicle": "sim-1", "name": "ATTITUDE"},
        "GET",
        "/vehicles/sim-1/messages/ATTITUDE",
        None,
    ),
    ("device_param", {"vehicle": "sim-1", "name": "A/B"}, "GET", "/vehicles/sim-1/params/A%2FB", None),
    (
        "device_params_read",
        {"vehicle": "sim-1", "names": ["X"]},
        "POST",
        "/vehicles/sim-1/params/read",
        {"names": ["X"]},
    ),
    ("device_params_refresh", {"vehicle": "sim-1"}, "POST", "/vehicles/sim-1/params/refresh", {}),
    ("device_commands", {}, "GET", "/commands", None),
    (
        "device_command",
        {"vehicle": "sim-1", "command": "MAV_CMD_DO_SET_SERVO", "params": [5, 1500], "timeout_s": 2},
        "POST",
        "/vehicles/sim-1/command",
        {"command": "MAV_CMD_DO_SET_SERVO", "params": [5, 1500], "timeout_s": 2},
    ),
    (
        "device_motor_test",
        {"vehicle": "sim-1", "motor": "all", "throttle_pct": 10, "seconds": 1, "confirm": "MOTOR"},
        "POST",
        "/vehicles/sim-1/motors/test",
        {"motor": "all", "throttle_pct": 10, "seconds": 1, "confirm": "MOTOR"},
    ),
    ("device_motors_stop", {"vehicle": "sim-1"}, "POST", "/vehicles/sim-1/motors/stop", {}),
    (
        "device_actuator",
        {"vehicle": "sim-1", "index": 7, "pwm": 1600},
        "POST",
        "/vehicles/sim-1/actuators/set",
        {"index": 7, "pwm": 1600},
    ),
    (
        "device_rc",
        {"vehicle": "sim-1", "channels": {"5": 1500}},
        "POST",
        "/vehicles/sim-1/rc",
        {"channels": {"5": 1500}},
    ),
    (
        "device_message_interval",
        {"vehicle": "sim-1", "message": "ATTITUDE", "hz": 10},
        "POST",
        "/vehicles/sim-1/message-interval",
        {"message": "ATTITUDE", "hz": 10},
    ),
    (
        "device_request_message",
        {"vehicle": "sim-1", "message": "AUTOPILOT_VERSION"},
        "POST",
        "/vehicles/sim-1/request-message",
        {"message": "AUTOPILOT_VERSION"},
    ),
    (
        "device_reboot",
        {"vehicle": "sim-1", "confirm": "REBOOT"},
        "POST",
        "/vehicles/sim-1/reboot",
        {"confirm": "REBOOT"},
    ),
    (
        "device_calibrate",
        {"vehicle": "sim-1", "sensor": "gyro", "confirm": "CALIBRATE"},
        "POST",
        "/vehicles/sim-1/calibrate",
        {"sensor": "gyro", "confirm": "CALIBRATE"},
    ),
    ("device_set_home", {"vehicle": "sim-1", "here": True}, "POST", "/vehicles/sim-1/home", {"here": True}),
    (
        "device_terminate",
        {"vehicle": "sim-1", "confirm": "TERMINATE"},
        "POST",
        "/vehicles/sim-1/terminate",
        {"confirm": "TERMINATE"},
    ),
    (
        "device_arm",
        {"vehicle": "sim-1", "force": True, "confirm": "FORCE"},
        "POST",
        "/vehicles/sim-1/arm",
        {"arm": True, "confirm": "FORCE", "force": True},
    ),
    ("device_disarm", {"vehicle": "sim-1"}, "POST", "/vehicles/sim-1/arm", {"arm": False}),
    (
        "device_set_mode",
        {"vehicle": "sim-1", "name": "LOITER"},
        "POST",
        "/vehicles/sim-1/mode",
        {"name": "LOITER"},
    ),
    ("device_claim", {"vehicle": "sim-1"}, "POST", "/vehicles/sim-1/claim", {}),
    # bench procedures (WP-B, 05 §4)
    ("device_procedure_list", {}, "GET", "/procedures", None),
    (
        "device_procedure_start",
        {"vehicle": "sim-1", "procedure": "motor_check", "options": {"auto_answer": True}},
        "POST",
        "/vehicles/sim-1/procedure",
        {"procedure": "motor_check", "options": {"auto_answer": True}},
    ),
    ("device_procedure_status", {"vehicle": "sim-1"}, "GET", "/vehicles/sim-1/procedure", None),
    (
        "device_procedure_answer",
        {"vehicle": "sim-1", "step": "S6", "answer": "confirm", "confirm": "MOTOR"},
        "POST",
        "/vehicles/sim-1/procedure/answer",
        {"step": "S6", "answer": "confirm", "confirm": "MOTOR"},
    ),
    ("device_procedure_abort", {"vehicle": "sim-1"}, "POST", "/vehicles/sim-1/procedure/abort", {}),
    ("device_test_runs", {"vehicle": "sim-1"}, "GET", "/test-runs", None),
    ("device_test_run", {"run_id": 3}, "GET", "/test-runs/3", None),
    ("device_test_run", {"run_id": 3, "fmt": "md"}, "GET", "/test-runs/3/export", None),
]


@pytest.mark.parametrize(
    ("tool", "args", "method", "path", "body"), DEVICE_CASES, ids=[c[0] for c in DEVICE_CASES]
)
def test_device_tool_requests(stub, tool, args, method, path, body):
    server = make(device_mcp, stub)
    result = call(server, tool, args)
    assert result["isError"] is False, result
    req = last(stub)
    assert (req["method"], req["path"]) == (method, path)
    assert req["body"] == body and req["session"] == "tok1" and req["operator"] is None


def test_device_procedure_status_drops_the_live_series(stub):
    server = make(device_mcp, stub)
    stub.responses["/vehicles/sim-1/procedure"] = {
        "run_id": 1,
        "state": "running",
        "steps": [{"id": "S9", "live": {"alt": [0.1, 0.2]}, "metrics": []}],
    }
    out = text(call(server, "device_procedure_status", {"vehicle": "sim-1"}))
    assert out["steps"] == [{"id": "S9", "metrics": []}]
    assert "operator_only" in device_mcp.INSTRUCTIONS


def test_device_skills_and_derived(stub):
    server = make(device_mcp, stub)
    stub.responses["/vehicles/sim-1/skills/attitude"] = {
        "skill": {"id": 4, "name": "attitude", "state": "running"}
    }
    result = call(server, "device_attitude", {"vehicle": "sim-1", "roll_deg": 5, "seconds": 2, "wait": False})
    assert text(result)["skill"]["id"] == 4
    assert last(stub)["path"] == "/vehicles/sim-1/skills/attitude"
    # advertised defaults are sent; thrust stays out so the backend uses the calibrated hover thrust
    assert last(stub)["body"] == {"roll_deg": 5, "pitch_deg": 0, "yaw_rate_dps": 0, "seconds": 2}
    call(server, "device_sticks", {"vehicle": "sim-1", "throttle": 0.6, "seconds": 1, "wait": False})
    assert last(stub)["path"].endswith("/skills/sticks")
    call(server, "device_sticks", {"vehicle": "sim-1", "roll": 0.2, "seconds": 1, "wait": False})
    assert last(stub)["body"] == {"roll": 0.2, "pitch": 0, "yaw": 0, "throttle": 0.5, "seconds": 1}
    call(server, "device_velocity", {"vehicle": "sim-1", "vx": 1, "seconds": 1, "wait": False})
    assert last(stub)["path"].endswith("/skills/velocity")
    stub.responses["/vehicles/sim-1"] = {
        "id": "sim-1",
        "autopilot": "ardupilot",
        "mode": "LOITER",
        "modes": ["LOITER"],
    }
    assert text(call(server, "device_modes", {"vehicle": "sim-1"}))["modes"] == ["LOITER"]
    stub.responses["/vehicles/sim-1/params"] = {
        "id": "sim-1",
        "received": 3,
        "total": 3,
        "complete": True,
        "params": [{"name": "MOT_A", "value": 1}, {"name": "MOT_B", "value": 2}, {"name": "X", "value": 3}],
    }
    listed = text(call(server, "device_params_list", {"vehicle": "sim-1", "prefix": "mot_"}))
    assert listed["values"] == {"MOT_A": 1, "MOT_B": 2} and last(stub)["query"] == {}
    stub.responses["/vehicles/sim-1/messages"] = {
        "id": "sim-1",
        "messages": {"ATTITUDE": {"fields": {"roll": 1}, "hz": 10, "age_s": 0.1, "count": 5}},
    }
    msgs = text(call(server, "device_messages", {"vehicle": "sim-1"}))
    assert msgs["messages"]["ATTITUDE"] == {"hz": 10, "age_s": 0.1, "count": 5}


def test_device_param_set_runs_as_the_session(stub):
    server = make(device_mcp, stub)
    args = {"vehicle": "sim-1", "name": "A", "value": 1, "confirm": "A"}
    assert call(server, "device_param_set", args)["isError"] is False
    req = last(stub)
    assert req["path"] == "/vehicles/sim-1/params" and req["session"] == "tok1" and req["operator"] is None


def test_errors_map_and_session_reopens(stub):
    server = make(device_mcp, stub)
    stub.fail["/vehicles/sim-1/motors/test"] = (
        400,
        {
            "code": "confirm_required",
            "message_en": "Confirm.",
            "message_zh": "需要确认。",
            "detail": {"confirm": "MOTOR"},
        },
    )
    result = call(server, "device_motor_test", {"vehicle": "sim-1", "motor": 1, "throttle_pct": 5})
    body = text(result)
    assert result["isError"] is True and body["error"] == "confirm_required"
    assert body["detail"] == {"confirm": "MOTOR"} and body["message_zh"] == "需要确认。"
    bad = call(server, "device_overview", {})
    assert bad["isError"] is True and "invalid arguments" in bad["content"][0]["text"]
    stub.expire_once = True
    assert call(server, "device_overview", {"vehicle": "sim-1"})["isError"] is False
    assert stub.opened == 2 and last(stub)["session"] == "tok2"


def test_concurrent_expiry_reopens_the_session_once(stub):
    server = make(device_mcp, stub)
    server.ensure_session()
    expired = server.relay.session_id
    opened = stub.opened
    server.ensure_session(stale=expired)  # the first call that found it expired reopens
    assert stub.opened == opened + 1
    server.ensure_session(stale=expired)  # a second one, late, reuses the new session
    assert stub.opened == opened + 1 and server.relay.session_id != expired


APP_CASES = [
    ("app_set_language", {"lang": "zh"}, "set_language", {"lang": "zh"}),
    ("app_set_workspace", {"workspace": "agents"}, "set_workspace", {"workspace": "agents"}),
    ("app_select_vehicle", {"vehicle": None}, "select_vehicle", {"vehicle": None}),
    ("app_set_view", {"view": "chase"}, "set_view", {"view": "chase"}),
    ("app_open_inspector", {"tab": "device"}, "open_inspector", {"tab": "device"}),
    ("app_close_inspector", {}, "close_inspector", {}),
    ("app_open_settings", {"section": "copilot"}, "open_settings", {"section": "copilot"}),
    ("app_open_connect", {"tab": "scan"}, "open_connect", {"tab": "scan"}),
    ("app_close_dialogs", {}, "close_dialogs", {}),
    (
        "app_set_pref",
        {"key": "units", "value": "imperial"},
        "set_pref",
        {"key": "units", "value": "imperial"},
    ),
    ("app_notify", {"text": "hi", "level": "warn"}, "notify", {"text": "hi", "level": "warn"}),
    ("app_bind_gamepad", {"index": 1, "vehicle": "sim-1"}, "bind_gamepad", {"index": 1, "vehicle": "sim-1"}),
    ("app_unbind_gamepad", {"index": 1}, "unbind_gamepad", {"index": 1}),
    (
        "app_possess",
        {"vehicle": "sim-1", "input": "gamepad", "pad": 1},
        "possess",
        {"vehicle": "sim-1", "input": "gamepad", "pad": 1},
    ),
    ("app_release", {"vehicle": "sim-1"}, "release", {"vehicle": "sim-1"}),
    ("app_open_record", {"session_id": 3}, "open_record", {"session_id": 3}),
    ("app_replay", {"action": "seek", "t": 4.5}, "replay", {"action": "seek", "t": 4.5}),
    ("app_toggle_copilot", {"open": True}, "toggle_copilot", {"open": True}),
    ("app_window", {"action": "focus"}, "window", {"action": "focus"}),
]


@pytest.mark.parametrize(("tool", "args", "action", "sent"), APP_CASES, ids=[c[0] for c in APP_CASES])
def test_app_ui_tools(stub, tool, args, action, sent):
    server = make(app_mcp, stub)
    stub.responses["/app/ui"] = {"ok": True, "data": {}}
    assert call(server, tool, args)["isError"] is False
    req = last(stub)
    assert (req["method"], req["path"]) == ("POST", "/app/ui")
    assert req["body"] == {"action": action, "args": sent} and req["session"] == "tok1"


APP_REST = [
    ("app_status", {}, "GET", "/app", None, None),
    ("app_ui_state", {}, "GET", "/app/ui", None, None),
    ("app_gamepads", {}, "GET", "/app/gamepads", None, None),
    ("app_actions", {}, "GET", "/app/actions", None, None),
    (
        "app_scan_network",
        {"networks": ["10.0.0.0/24"]},
        "POST",
        "/discover",
        {"networks": ["10.0.0.0/24"]},
        None,
    ),
    (
        "app_connect_address",
        {"host": "10.0.0.5", "port": 14550},
        "POST",
        "/vehicles",
        {"host": "10.0.0.5", "port": 14550},
        None,
    ),
    ("app_remove_vehicle", {"vehicle": "real-1"}, "DELETE", "/vehicles/real-1", None, None),
    (
        "app_sims_add",
        {"count": 2, "autopilot": "px4"},
        "POST",
        "/sims",
        {"count": 2, "autopilot": "px4"},
        None,
    ),
    ("app_sims_remove", {"id": ["sim-2", "sim-3"]}, "DELETE", "/sims", None, None),
    (
        "app_sims_add",
        {"engine": "sitl", "pack": "px4-1.16.2", "frame": {"preset": "small-250"}},
        "POST",
        "/sims",
        {"engine": "sitl", "pack": "px4-1.16.2", "frame": {"preset": "small-250"}},
        None,
    ),
    (
        "app_connect_address",
        {"serial": {"device": "/dev/ttyACM0", "baud": "auto"}},
        "POST",
        "/vehicles",
        {"serial": {"device": "/dev/ttyACM0", "baud": "auto"}},
        None,
    ),
    ("relay_serial_ports", {}, "GET", "/serial/ports", None, None),
    ("relay_hitl", {"vehicle": "real-1"}, "GET", "/vehicles/real-1/hitl", None, None),
    (
        "relay_hitl",
        {"vehicle": "real-1", "action": "start", "confirm": "HITL"},
        "POST",
        "/vehicles/real-1/hitl",
        {"action": "start", "confirm": "HITL"},
        None,
    ),
    (
        "relay_hitl",
        {"vehicle": "real-1", "action": "stop"},
        "POST",
        "/vehicles/real-1/hitl",
        {"action": "stop"},
        None,
    ),
    ("app_sim_reset", {"vehicle": "sim-1"}, "POST", "/sims/sim-1/reset", {}, None),
    ("app_sim_assist", {"vehicle": "sim-1", "on": False}, "POST", "/sims/sim-1/assist", {"on": False}, None),
    ("app_settings_get", {}, "GET", "/settings", None, None),
    (
        "app_settings_set",
        {"settings": {"agents_app_control": False}},
        "PUT",
        "/settings",
        {"agents_app_control": False},
        None,
    ),
    ("app_alerts_list", {}, "GET", "/alerts", None, None),
    ("app_alert_ack", {"alert_id": "a1"}, "POST", "/alerts/a1/ack", {}, None),
    ("app_stop_all", {}, "POST", "/stop-all", {}, None),
    ("app_network", {}, "GET", "/network", None, None),
    ("app_records_list", {"limit": 5}, "GET", "/history/sessions", None, None),
    ("app_record_detail", {"session_id": 3}, "GET", "/history/sessions/3", None, None),
    ("app_refly_options", {"session_id": 3}, "GET", "/history/sessions/3/refly", None, None),
    ("app_courses_list", {}, "GET", "/courses", None, None),
]


@pytest.mark.parametrize(
    ("tool", "args", "method", "path", "body", "auth"), APP_REST, ids=[c[0] for c in APP_REST]
)
def test_app_rest_tools(stub, tool, args, method, path, body, auth):
    server = make(app_mcp, stub, token="op")
    assert call(server, tool, args)["isError"] is False
    req = last(stub)
    assert (req["method"], req["path"], req["body"]) == (method, path, body)
    assert (req["operator"] == "op") == (auth == "op")
    if tool == "app_sims_remove":
        assert req["query"] == {"id": ["sim-2", "sim-3"]}
    if tool == "app_records_list":
        assert req["query"] == {"limit": ["5"]}


def test_app_hitl_and_connect_check_their_arguments(stub):
    """11 §12: HITL start needs the confirm word; a connection is by host or by serial, never both."""
    server = make(app_mcp, stub)
    before = len(stub.requests)
    result = call(server, "relay_hitl", {"vehicle": "real-1", "action": "start"})
    assert result["isError"] is True and "HITL" in result["content"][0]["text"]
    result = call(server, "app_connect_address", {"host": "10.0.0.5", "serial": {"device": "COM5"}})
    assert result["isError"] is True and "host or serial" in result["content"][0]["text"]
    assert len(stub.requests) == before
    tool = next(t for t in rpc(server, "tools/list")["result"]["tools"] if t["name"] == "relay_hitl")
    assert tool["annotations"]["destructiveHint"] is True
    assert tool["inputSchema"]["properties"]["confirm"]["enum"] == ["HITL"]
    sims = next(t for t in rpc(server, "tools/list")["result"]["tools"] if t["name"] == "app_sims_add")
    assert sims["inputSchema"]["properties"]["engine"]["enum"] == ["relay", "sitl"]
    reset = next(t for t in rpc(server, "tools/list")["result"]["tools"] if t["name"] == "app_sim_reset")
    assert "crashed" in reset["description"] and "restarts its firmware" in reset["description"]
    stub.fail["/vehicles/real-1/hitl"] = (
        409,
        {"code": "hitl_unavailable", "message_en": "No HITL.", "detail": {"reason": "ArduPilot"}},
    )
    result = call(server, "relay_hitl", {"vehicle": "real-1"})
    assert result["isError"] is True and text(result)["error"] == "hitl_unavailable"
    assert text(result)["detail"] == {"reason": "ArduPilot"}


# ---------------------------------------------------------------------------- 11 §12: firmware twins

TWIN_TOOLS = {
    "relay_autopilots_list",
    "relay_firmware_identify",
    "relay_twin_profiles_list",
    "relay_twin_profile_get",
    "relay_twin_profile_capture",
    "relay_twin_start",
    "relay_twin_status",
    "relay_sim_frame",
}

TWIN_CASES = [
    ("relay_autopilots_list", {}, "GET", "/autopilots", None),
    ("relay_firmware_identify", {"vehicle": "real-1"}, "GET", "/vehicles/real-1/firmware", None),
    ("relay_twin_profile_get", {"profile": "tp-1"}, "GET", "/twins/profiles/tp-1", None),
    (
        "relay_twin_profile_capture",
        {"vehicle": "real-1", "name": "Small", "frame": {"preset": "small-250", "cells": 3}},
        "POST",
        "/twins/profiles",
        {"from_vehicle": "real-1", "name": "Small", "frame": {"preset": "small-250", "cells": 3}},
    ),
    (
        "relay_twin_profile_capture",
        {"vehicle": "real-1", "profile": "tp-1"},
        "POST",
        "/twins/profiles",
        {"from_vehicle": "real-1", "profile": "tp-1"},
    ),
    ("relay_twin_status", {"vehicle": "sim-2"}, "GET", "/sims/sim-2/twin", None),
    (
        "relay_sim_frame",
        {"vehicle": "sim-2", "preset": "large-450", "mass_kg": 1.2},
        "PUT",
        "/sims/sim-2/frame",
        {"preset": "large-450", "mass_kg": 1.2},
    ),
]


@pytest.mark.parametrize(
    ("tool", "args", "method", "path", "body"),
    TWIN_CASES,
    ids=[f"{c[0]}-{i}" for i, c in enumerate(TWIN_CASES)],
)
def test_twin_tool_requests(stub, tool, args, method, path, body):
    server = make(flight_mcp, stub)
    assert call(server, tool, args)["isError"] is False
    req = last(stub)
    assert (req["method"], req["path"], req["body"]) == (method, path, body)
    assert req["session"] == "tok1"


def test_twin_start_and_list(stub):
    server = make(flight_mcp, stub)
    stub.responses["/sims"] = {"added": ["sim-2", "sim-3"]}
    result = call(
        server,
        "relay_twin_start",
        {"profile": "tp-1", "count": 2, "home": {"lat": 47.6, "lon": -122.3, "alt_m": 50}},
    )
    assert text(result) == {"added": ["sim-2", "sim-3"]}
    assert last(stub)["body"] == {
        "count": 2,
        "engine": "sitl",
        "profile": "tp-1",
        "home": {"lat": 47.6, "lon": -122.3, "alt_m": 50},
    }
    call(server, "relay_twin_start", {"pack": "ardupilot-copter-4.6.3", "frame": {"preset": "small-250"}})
    assert last(stub)["body"] == {
        "count": 1,
        "engine": "sitl",
        "pack": "ardupilot-copter-4.6.3",
        "frame": {"preset": "small-250"},
    }
    stub.responses["/twins/profiles"] = {"profiles": [{"id": "tp-1"}]}
    assert text(call(server, "relay_twin_profiles_list")) == {"profiles": [{"id": "tp-1"}]}
    stub.fail["/sims"] = (
        503,
        {"code": "twin_start_failed", "message_en": "No heartbeat.", "detail": {"reason": "timeout"}},
    )
    failed = call(server, "relay_twin_start", {"pack": "px4-1.16.2"})
    assert failed["isError"] is True and text(failed)["error"] == "twin_start_failed"


def test_app_screenshot_returns_image(stub):
    server = make(app_mcp, stub)
    stub.responses["/app/ui"] = {"ok": True, "data": {"png_base64": PNG, "width": 800, "height": 600}}
    result = call(server, "app_screenshot", {"max_width": 800})
    assert result["isError"] is False
    assert result["content"][0] == {"type": "image", "data": PNG, "mimeType": "image/png"}
    assert json.loads(result["content"][1]["text"]) == {"width": 800, "height": 600}
    assert last(stub)["body"] == {"action": "screenshot", "args": {"max_width": 800}}


def test_app_errors_and_export(stub):
    server = make(app_mcp, stub)
    stub.fail["/app/ui"] = (503, {"code": "ui_unavailable", "message_en": "No window."})
    result = call(server, "app_set_language", {"lang": "en"})
    assert result["isError"] is True and text(result)["error"] == "ui_unavailable"
    exported = text(call(server, "app_record_export", {"session_id": 3, "fmt": "csv"}))
    assert exported["content"] == "t,x\n0,1\n" and exported["truncated"] is False
    assert last(stub)["query"] == {"fmt": ["csv"]}


def test_flight_new_tools(stub):
    server = make(flight_mcp, stub)
    stub.responses["/vehicles/sim-1/skills/sticks"] = {
        "skill": {"id": 2, "name": "sticks", "state": "running"}
    }
    call(server, "relay_sticks_for", {"vehicle": "sim-1", "pitch": 0.3, "seconds": 2, "wait": False})
    assert last(stub)["body"] == {"roll": 0.0, "pitch": 0.3, "yaw": 0.0, "throttle": 0.5, "seconds": 2.0}
    stub.responses["/vehicles/sim-1/refly"] = {"plan": {"id": 9}}
    result = call(server, "relay_refly", {"vehicle": "sim-1", "session_id": 4, "mode": "path", "wait": False})
    assert result["isError"] is False and text(result)["plan"]["id"] == 9
    assert last(stub)["body"] == {"session_id": 4, "mode": "path"}


def test_flight_tools_follow_the_agent_flight_level(stub):
    """06 §5 (WP-1): at ``sticks`` the flight server lists only the tools Relay still accepts."""
    server = make(flight_mcp, stub)
    every = [t["name"] for t in flight_mcp.TOOLS]
    assert [t["name"] for t in rpc(server, "tools/list")["result"]["tools"]] == every  # no setting: full
    assert last(stub)["path"] == "/settings" and last(stub)["method"] == "GET"
    stub.responses["/settings"] = {"agent_flight_level": "sticks"}
    names = [t["name"] for t in rpc(server, "tools/list")["result"]["tools"]]
    assert set(names) == flight_mcp.STICKS_TOOL_NAMES and names == [n for n in every if n in names]
    assert not {"relay_takeoff", "relay_goto", "relay_plan", "relay_refly", "relay_hold"} & set(names)
    assert "sticks" in rpc(server, "initialize", {"protocolVersion": "2025-06-18"})["result"]["instructions"]
    stub.responses["/settings"] = {"agent_flight_level": "full"}  # read again on every listing
    assert len(rpc(server, "tools/list")["result"]["tools"]) == len(every)
    stub.fail["/settings"] = (500, {"code": "internal", "message_en": "down"})
    assert len(rpc(server, "tools/list")["result"]["tools"]) == len(every)  # the last level read
    fresh = make(flight_mcp, stub)
    assert {t["name"] for t in rpc(fresh, "tools/list")["result"]["tools"]} == flight_mcp.STICKS_TOOL_NAMES


# ---------------------------------------------------------------------------- WP-4b: program tools (06 §6)

CONTRACT_PROGRAM_TOOLS = {
    "program_list",
    "program_get",
    "program_create",
    "program_update",
    "program_validate",
    "program_test",
    "program_deploy",
    "program_stop",
    "program_resume",
    "program_params",
    "program_status",
    "program_log",
    "challenge_list",
    "challenge_submit",
    "job_status",
    "duel_run",
    "ladder",
    "airframe_set",
}

PROGRAM_REST = [
    ("program_list", {}, "GET", "/programs", None),
    ("program_validate", {"program": "p1", "source": "x"}, "POST", "/programs/p1/validate", {"source": "x"}),
    ("program_test", {"program": "p1", "rev": 2}, "POST", "/programs/p1/test", {"rev": 2}),
    (
        "program_create",
        {"name": "Mine", "copy_of": "hover_trim"},
        "POST",
        "/programs",
        {"name": "Mine", "copy_of": "hover_trim"},
    ),
    (
        "program_update",
        {"program": "p1", "source": "s", "note": "rewrite"},
        "PUT",
        "/programs/p1",
        {"source": "s", "note": "rewrite"},
    ),
    ("program_log", {"vehicle": "sim-1", "after": 3}, "GET", "/vehicles/sim-1/program/log", None),
    ("program_stop", {"vehicle": "sim-1"}, "POST", "/vehicles/sim-1/program/stop", {}),
    ("program_resume", {"vehicle": "sim-1"}, "POST", "/vehicles/sim-1/program/resume", {}),
    (
        "program_params",
        {"vehicle": "sim-1", "params": {"kp": 0.3}},
        "PATCH",
        "/vehicles/sim-1/program/params",
        {"kp": 0.3},
    ),
    (
        "airframe_set",
        {"vehicle": "sim-1", "preset": "rough", "seed": 4},
        "PUT",
        "/sims/sim-1/airframe",
        {"preset": "rough", "seed": 4},
    ),
    ("challenge_list", {}, "GET", "/challenges", None),
    ("job_status", {"job": "j1"}, "GET", "/jobs/j1", None),
    ("ladder", {}, "GET", "/ladder", None),
    (
        "duel_run",
        {"programs": ["hunter_easy", "hunter_hard"], "rounds": 3, "wait": False},
        "POST",
        "/duels",
        {"programs": ["hunter_easy", "hunter_hard"], "rounds": 3},
    ),
]


def test_app_server_lists_the_program_tools(stub):
    names = {t.name for t in app_mcp.build_tools()}
    assert CONTRACT_PROGRAM_TOOLS | {"program_guide"} <= names
    assert "program_guide" in app_mcp.INSTRUCTIONS
    server = make(app_mcp, stub)
    listed = {t["name"]: t for t in rpc(server, "tools/list")["result"]["tools"]}
    assert listed["program_get"]["annotations"]["readOnlyHint"] is True
    assert listed["program_deploy"]["annotations"]["destructiveHint"] is True


@pytest.mark.parametrize(
    ("tool", "args", "method", "path", "body"), PROGRAM_REST, ids=[c[0] for c in PROGRAM_REST]
)
def test_program_tool_requests(stub, tool, args, method, path, body):
    server = make(app_mcp, stub)
    stub.responses["/programs"] = {
        "programs": [],
        "program": {"id": "p1", "rev": 1},
        "validation": {"ok": True},
    }
    stub.responses["/programs/p1"] = {
        "program": {"id": "p1", "rev": 2, "source": "a"},
        "validation": {"ok": True},
    }
    result = call(server, tool, args)
    assert result["isError"] is False, result
    req = last(stub)
    assert (req["method"], req["path"], req["body"]) == (method, path, body)
    assert req["session"] == "tok1"
    if tool == "program_log":
        assert req["query"] == {"after": ["3"], "limit": ["100"]}
    if tool in ("program_stop", "program_resume", "program_params"):
        assert any(r["path"] == "/vehicles/sim-1/claim" for r in stub.requests), "claimed first"


def test_program_get_numbers_the_source_and_reads_old_revisions(stub):
    server = make(app_mcp, stub)
    stub.responses["/programs/p1"] = {
        "program": {
            "id": "p1",
            "name": "Hover",
            "rev": 2,
            "source": "def step(obs, ctx):\n    pass",
            "tests": [],
        }
    }
    stub.responses["/programs/p1/revisions"] = {
        "revisions": [
            {"rev": 1, "source": "old", "params": {"a": 1}, "author": "mcp-app", "note": "first", "t": 1},
            {"rev": 2, "source": "x", "params": {}, "author": "operator", "note": "edit", "t": 2},
        ]
    }
    got = text(call(server, "program_get", {"program": "p1"}))
    assert got["source_numbered"] == "1| def step(obs, ctx):\n2|     pass" and got["lines"] == 2
    assert [r["rev"] for r in got["revisions"]] == [2, 1] and "source" not in got["revisions"][0]
    old = text(call(server, "program_get", {"program": "p1", "rev": 1, "numbered": False}))
    assert (old["source"], old["rev"], old["params"]) == ("old", 1, {"a": 1})
    missing = call(server, "program_get", {"program": "p1", "rev": 7})
    assert missing["isError"] is True and text(missing)["error"] == "revision_not_found"
    assert programs_mcp.numbered("\n".join(f"l{i}" for i in range(10))).split("\n")[9] == "10| l9"


def test_program_update_with_a_patch_returns_the_new_numbered_source(stub):
    server = make(app_mcp, stub)
    stub.responses["/programs/p1"] = {
        "program": {"id": "p1", "rev": 3, "source": "a\nb"},
        "validation": {"ok": True},
    }
    patch = [{"start": 2, "end": 2, "text": "c"}]
    out = text(call(server, "program_update", {"program": "p1", "patch": patch, "note": "trim"}))
    assert last(stub)["body"] == {"patch": patch, "note": "trim"}
    assert out["source_numbered"] == "1| a\n2| b" and out["program"]["rev"] == 3


def test_program_deploy_claims_sets_a_manual_mode_and_arms(stub):
    server = make(app_mcp, stub)
    stub.responses["/vehicles/sim-1"] = {
        "id": "sim-1",
        "autopilot": "ardupilot",
        "mode": "ALT_HOLD",
        "armed": False,
    }
    airframe = {"preset": "typical", "seed": 5}
    args = {"vehicle": "sim-1", "program": "hover_trim", "arm": True, "seed": 7, "airframe": airframe}
    assert call(server, "program_deploy", args)["isError"] is False
    sent = [(r["method"], r["path"], r["body"]) for r in stub.requests if r["method"] != "GET"]
    assert sent == [
        ("POST", "/vehicles/sim-1/claim", {}),
        ("POST", "/vehicles/sim-1/mode", {"name": "STABILIZE"}),
        ("POST", "/vehicles/sim-1/arm", {"arm": True, "confirm": None}),
        (
            "POST",
            "/vehicles/sim-1/program",
            {"program": "hover_trim", "mode": "pilot", "seed": 7, "airframe": airframe},
        ),
    ]
    stub.requests.clear()
    stub.responses["/vehicles/sim-1"] = {"id": "sim-1", "possession": {"input": "keyboard"}}
    blocked = call(server, "program_deploy", {"vehicle": "sim-1", "program": "hover_trim"})
    assert blocked["isError"] is True and text(blocked)["error"] == "possessed_by_human"
    assert not [r for r in stub.requests if r["method"] == "POST"], "nothing sent while a person flies it"
    stub.responses["/vehicles/sim-1"] = {"id": "sim-1", "claim": {"session_id": "other", "name": "mcp"}}
    taken = call(server, "program_deploy", {"vehicle": "sim-1", "program": "hover_trim"})
    assert text(taken)["error"] == "claimed_by_other"


def test_program_status_and_guide(stub):
    server = make(app_mcp, stub)
    stub.responses["/vehicles/sim-1"] = {
        "id": "sim-1",
        "program": {"id": "hover_trim", "status": "running", "fps": 50},
        "controller": "program",
        "sim": {"hover_assist": False, "airframe": {"preset": "typical", "seed": 2}},
    }
    status = text(call(server, "program_status", {"vehicle": "sim-1"}))
    assert status["program"]["status"] == "running" and status["controller"] == "program"
    assert status["sim"] == {"hover_assist": False, "airframe": {"preset": "typical", "seed": 2}}
    guide = text(call(server, "program_guide"))["guide"]
    assert guide == (REPO / "docs" / "programs-guide.md").read_text("utf-8").strip()
    assert "Sticks(" in guide and "PID(" in guide


def test_challenge_submit_waits_for_the_job(stub):
    server = make(app_mcp, stub)
    stub.responses["/challenges/hover/submit"] = {"job": {"id": "j1", "state": "queued"}}
    stub.responses["/jobs/j1"] = {"id": "j1", "state": "done", "result": {"score": 91, "passed": True}}
    out = text(call(server, "challenge_submit", {"challenge": "hover", "program": "hover_trim"}))
    assert out["state"] == "done" and out["result"]["score"] == 91
    submit = next(r for r in stub.requests if r["path"] == "/challenges/hover/submit")
    assert submit["body"] == {"program": "hover_trim"}


def test_judge_tools_without_the_judge_api(stub):
    server = make(app_mcp, stub)
    for path in ("/challenges", "/challenges/hover/submit", "/jobs/j1", "/duels", "/ladder"):
        stub.fail[path] = (404, {"code": "not_found", "message_en": "Nothing is available at this path."})
    for tool, args in [
        ("challenge_list", {}),
        ("challenge_submit", {"challenge": "hover", "program": "p"}),
        ("job_status", {"job": "j1"}),
        ("duel_run", {"programs": ["a", "b"]}),
        ("ladder", {}),
    ]:
        result = call(server, tool, args)
        assert result["isError"] is True and text(result)["error"] == "judge_unavailable", tool
        assert "judge API" in text(result)["message"]
    del stub.fail["/challenges"]  # the judge exists: an unknown job keeps its own error
    assert text(call(server, "job_status", {"job": "j1"}))["error"] == "not_found"


def test_flight_server_points_to_the_program_tools_at_sticks(stub):
    stub.responses["/settings"] = {"agent_flight_level": "sticks"}
    server = make(flight_mcp, stub)
    instructions = rpc(server, "initialize", {"protocolVersion": "2025-06-18"})["result"]["instructions"]
    assert "program_deploy" in instructions and "anet-relay-app" in instructions


# ---------------------------------------------------------------------------- WP-B dogfight tools (07 §3-§4)

MATCH_REST = [
    (
        "dogfight_start",
        {"aircraft": 6, "teams": "ffa", "roles": {"gunner": 4, "rammer": 2}, "duration_s": 120},
        "POST",
        "/scenarios/dogfight",
        {"aircraft": 6, "teams": "ffa", "roles": {"gunner": 4, "rammer": 2}, "duration_s": 120},
    ),
    ("dogfight_start", {}, "POST", "/scenarios/dogfight", {"aircraft": 6, "teams": "ffa"}),
    ("dogfight_stop", {}, "DELETE", "/scenarios/dogfight", None),
    ("dogfight_status", {}, "GET", "/scenarios/dogfight", None),
    ("leaderboard", {"by": "rams"}, "GET", "/arena/leaderboard", None),
]


def test_app_server_lists_the_dogfight_tools(stub):
    names = {"dogfight_start", "dogfight_stop", "dogfight_status", "leaderboard"}
    assert set(match_mcp.MATCH_TOOL_NAMES) == names
    assert "dogfight_start" in app_mcp.INSTRUCTIONS and "sticks only" in app_mcp.INSTRUCTIONS
    server = make(app_mcp, stub)
    listed = {t["name"]: t for t in rpc(server, "tools/list")["result"]["tools"]}
    assert set(match_mcp.MATCH_TOOL_NAMES) <= set(listed)
    assert listed["leaderboard"]["annotations"]["readOnlyHint"] is True
    assert listed["dogfight_status"]["annotations"]["readOnlyHint"] is True
    props = set(listed["dogfight_start"]["inputSchema"]["properties"])
    assert {"aircraft", "teams", "roles", "programs", "arena", "airframe", "duration_s", "respawn"} <= props


@pytest.mark.parametrize(
    ("tool", "args", "method", "path", "body"),
    MATCH_REST,
    ids=[f"{c[0]}-{i}" for i, c in enumerate(MATCH_REST)],
)
def test_dogfight_tool_requests(stub, tool, args, method, path, body):
    server = make(app_mcp, stub)
    stub.responses["/scenarios/dogfight"] = {"running": True, "phase": "booting", "aircraft": []}
    stub.responses["/arena/leaderboard"] = {"by": "rams", "match": None, "rows": []}
    result = call(server, tool, args)
    assert result["isError"] is False, result
    req = last(stub)
    assert (req["method"], req["path"], req["body"]) == (method, path, body)
    assert req["session"] == "tok1"
    if tool == "leaderboard":
        assert req["query"] == {"by": ["rams"]}
