"""The Python SDK and the MCP server against a real backend served by uvicorn on a free port."""

from __future__ import annotations

import io
import json
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
sys.path.insert(0, str(REPO / "mcp"))

from anet_relay_client import RelayClient, RelayError
from anet_relay_mcp import TOOLS, Server

OP = "sdk-op"


@pytest.fixture(scope="module")
def base():
    svc = FleetService(
        Settings(agent_flight_level="full"), None, operator_token=OP, listen=False, history=History(None)
    )
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
    client.add_sims(2)
    end = time.monotonic() + 10
    while not all(v["link"]["state"] == "ok" and v["capabilities"]["position"] for v in client.vehicles()):
        assert time.monotonic() < end
        time.sleep(0.1)
    yield url
    server.should_exit = True
    thread.join(timeout=5)


def test_sdk_flight_and_errors(base):
    relay = RelayClient(base)
    relay.open_session("sdk-test")
    try:
        with pytest.raises(RelayError) as exc:
            relay.arm("sim-1")
        assert exc.value.code == "not_claimed" and exc.value.status == 409 and exc.value.message_zh
        assert relay.claim("sim-1")["claim"]["name"] == "sdk-test"
        assert relay.takeoff("sim-1", alt=1.0)["state"] == "done"
        done = relay.goto("sim-1", 0.4, 0.3, 1.0)
        assert done["name"] == "goto" and done["state"] == "done"
        with pytest.raises(RelayError) as exc:
            relay.goto("sim-1", 5.0, 0.0, 1.0)
        assert exc.value.code == "outside_safety_volume"
        assert relay.land("sim-1")["state"] == "done"
        event = relay.wait_event("skill_finished", vehicle="sim-1", after=0, timeout=2)
        assert event["data"]["skill_id"] > 0
        newest = relay.events(limit=2, tail=True)
        assert len(newest["events"]) == 2 and newest["events"][-1]["seq"] == newest["last_seq"]
        relay.unclaim("sim-1")
    finally:
        relay.close()
    assert not any(s["name"] == "sdk-test" for s in RelayClient(base).request("GET", "/sessions")["sessions"])


def test_sdk_runs_a_bench_procedure(base):
    """WP-B: an agent runs motor_check on a simulator through the SDK and reads the saved report."""
    relay = RelayClient(base)
    relay.open_session("sdk-bench")
    try:
        assert {p["id"] for p in relay.procedure_list()} >= {"full_bench", "motor_check"}
        relay.claim("sim-2")
        started = relay.procedure_start("sim-2", "motor_check", auto_answer=True)
        assert started["procedure"] == "motor_check" and started["by"]["kind"] == "session"
        view = relay.wait_procedure("sim-2", timeout=30)
        assert view["state"] == "waiting" and view["step"] == "S6"
        prompt = next(s for s in view["steps"] if s["id"] == "S6")["prompt"]
        assert prompt["id"] == "props_removed" and prompt["operator_only"] is False
        relay.procedure_answer("sim-2", "S6", "confirm")
        view = relay.wait_procedure("sim-2", timeout=30, waiting=False)
        assert view["state"] == "passed", view
        assert relay.procedure_status("sim-2")["run_id"] == view["run_id"]
        runs = relay.test_runs("sim-2")
        assert runs[0]["run_id"] == view["run_id"] and runs[0]["counts"]["pass"] == 3
        assert relay.test_run(view["run_id"])["steps"][1]["id"] == "S6"
        assert "| S6 " in relay.test_run_export(view["run_id"], "md")
        with pytest.raises(RelayError) as exc:
            relay.procedure_abort("sim-2")
        assert exc.value.code == "no_procedure"
        relay.unclaim("sim-2")
    finally:
        relay.close()


def test_sdk_stream_and_handback(base):
    relay = RelayClient(base)
    operator = RelayClient(base, operator_token=OP)
    relay.open_session("stream-test")
    try:
        stream = relay.stream(timeout=5)
        hello = next(stream)
        assert hello["type"] == "hello" and hello["session"] == relay.session_id
        assert any(m["type"] == "fleet" for _, m in zip(range(5), stream, strict=False))
        relay.claim("sim-2")
        seq = relay.last_seq()
        operator.possess("sim-2", "screen")
        with pytest.raises(RelayError) as exc:
            relay.hold("sim-2")
        assert exc.value.code in ("possessed_by_human", "claim_suspended")
        operator.release("sim-2")
        handback = relay.wait_event("relay_handback", vehicle="sim-2", after=seq, timeout=3)
        assert handback["data"].get("session_id", relay.session_id) == relay.session_id
        stream.close()
    finally:
        relay.close()


def drive(server: Server, messages: list[dict]) -> list[dict]:
    """Feed messages one by one, waiting for each tool call (the session stays open across calls)."""
    out = io.StringIO()
    server.out = out
    server.ensure_session()
    for message in messages:
        worker = server.handle_line(json.dumps(message))
        if worker is not None:
            worker.join(timeout=90)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_mcp_protocol(base):
    calls = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "relay_fleet", "arguments": {}},
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "relay_goto", "arguments": {"vehicle": "sim-2", "x": 2, "y": 0, "z": 1}},
        },
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "relay_sticks", "arguments": {}},
        },
        {"jsonrpc": "2.0", "id": 6, "method": "ping"},
        {"jsonrpc": "2.0", "id": 7, "method": "nope"},
    ]
    server = Server(base, "mcp-test")
    try:
        replies = {r["id"]: r for r in drive(server, calls) if "id" in r}
    finally:
        server.relay.close()
    init = replies[1]["result"]
    assert init["protocolVersion"] == "2025-06-18" and init["capabilities"]["tools"] == {"listChanged": False}
    names = [t["name"] for t in replies[2]["result"]["tools"]]
    assert (
        len(names) == 20
        and {"relay_course_start", "relay_plan", "relay_move", "relay_yaw", "relay_sticks_for", "relay_refly"}
        <= set(names)
        and "relay_sticks" not in names
    )
    fleet = json.loads(replies[3]["result"]["content"][0]["text"])
    assert replies[3]["result"]["isError"] is False and {v["id"] for v in fleet["vehicles"]} >= {
        "sim-1",
        "sim-2",
    }
    error = json.loads(replies[4]["result"]["content"][0]["text"])
    assert replies[4]["result"]["isError"] is True and error["error"] == "not_claimed"
    assert replies[5]["error"]["code"] == -32602 and replies[6]["result"] == {}
    assert replies[7]["error"]["code"] == -32601


def test_mcp_flies_and_unknown_version(base):
    server = Server(base, "mcp-fly")
    calls = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}]
    for i, (name, args) in enumerate(
        [
            ("relay_claim", {"vehicle": "sim-2"}),
            ("relay_takeoff", {"vehicle": "sim-2", "alt": 1.0}),
            ("relay_course_start", {"vehicle": "sim-2"}),
            ("relay_land", {"vehicle": "sim-2"}),
            ("relay_events", {"limit": 3}),
            ("relay_release_claim", {"vehicle": "sim-2"}),
        ],
        start=2,
    ):
        calls.append(
            {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}
        )
    try:
        replies = {r["id"]: r for r in drive(server, calls)}
    finally:
        server.relay.close()
    assert replies[1]["result"]["protocolVersion"] == "2025-11-25"
    for rid in range(2, 8):
        assert replies[rid]["result"]["isError"] is False, replies[rid]
    course = json.loads(replies[4]["result"]["content"][0]["text"])["course"]
    assert course["course_id"] == "cage-loop" and len(course["rings"]) == course["total"]
    events = json.loads(replies[6]["result"]["content"][0]["text"])
    assert len(events["events"]) == 3 and events["events"][-1]["seq"] == events["last_seq"]


def test_mcp_tool_schemas_are_closed():
    for tool in TOOLS:
        schema = tool["inputSchema"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert set(schema["required"]) <= set(schema["properties"])
