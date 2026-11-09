"""The Python SDK's firmware twin, serial and hardware-in-the-loop calls (11 §12).

Request shapes are checked against a stub HTTP server that records every request (it also answers the HITL and
serial routes); the twin reads that need no runtime pack also run against a real backend served by uvicorn.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
import uvicorn
from conftest import REPO, free_tcp_port

from relay.api.app import create_app
from relay.config import Settings
from relay.fleet import FleetService
from relay.history import History

sys.path.insert(0, str(REPO / "sdk" / "python"))

from anet_relay_client import RelayClient, RelayError

OP = "sdk-twins-op"


# ---------------------------------------------------------------------------- stub Relay


class Stub:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.responses: dict[str, object] = {}
        self.fail: dict[str, tuple[int, dict]] = {}


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
            if path in state.fail:
                status, error = state.fail[path]
                return self._json(status, {"error": error})
            return self._json(200, state.responses.get(path, {"ok": True}))

        def _json(self, status, payload):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = _serve

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    state.base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield state
    httpd.shutdown()
    httpd.server_close()


def _last(stub: Stub) -> tuple[str, str, object]:
    req = stub.requests[-1]
    return req["method"], req["path"], req["body"]


def test_twin_calls_send_the_contract_shapes(stub):
    relay = RelayClient(stub.base, operator_token="op")
    stub.responses["/autopilots"] = {"platform": "linux-x64", "packs": [{"id": "px4-1.16.2"}]}
    assert relay.autopilots()["packs"][0]["id"] == "px4-1.16.2"
    assert _last(stub) == ("GET", "/autopilots", None)

    relay.firmware("real-10-0-0-5")
    assert _last(stub) == ("GET", "/vehicles/real-10-0-0-5/firmware", None)

    stub.responses["/twins/profiles"] = {"profiles": [{"id": "tp-1"}]}
    assert relay.twin_profiles() == [{"id": "tp-1"}]
    relay.twin_profile("tp-1")
    assert _last(stub) == ("GET", "/twins/profiles/tp-1", None)

    relay.capture_twin_profile("real-1")
    assert _last(stub) == ("POST", "/twins/profiles", {"from_vehicle": "real-1"})
    relay.capture_twin_profile("real-1", name="Small", frame="small-250", profile="tp-1")
    assert _last(stub) == (
        "POST",
        "/twins/profiles",
        {"from_vehicle": "real-1", "name": "Small", "frame": {"preset": "small-250"}, "profile": "tp-1"},
    )
    relay.update_twin_profile("tp-1", name="Big", home=(47.6, -122.3))
    assert _last(stub) == (
        "PUT",
        "/twins/profiles/tp-1",
        {"name": "Big", "home": {"lat": 47.6, "lon": -122.3, "alt_m": 0.0}},
    )
    relay.update_twin_profile("tp-1", clear_home=True)
    assert _last(stub) == ("PUT", "/twins/profiles/tp-1", {"home": None})
    relay.delete_twin_profile("tp-1")
    assert _last(stub) == ("DELETE", "/twins/profiles/tp-1", None)

    stub.responses["/sims"] = {"added": ["sim-4"]}
    assert relay.start_twin(profile="tp-1") == ["sim-4"]
    assert _last(stub) == ("POST", "/sims", {"count": 1, "engine": "sitl", "profile": "tp-1"})
    relay.start_twin(
        pack="ardupilot-copter-4.6.3",
        frame={"preset": "small-250", "mass_kg": 0.42},
        home={"lat": 1.0, "lon": 2.0, "alt_m": 3.0},
        count=2,
    )
    assert _last(stub) == (
        "POST",
        "/sims",
        {
            "count": 2,
            "engine": "sitl",
            "pack": "ardupilot-copter-4.6.3",
            "frame": {"preset": "small-250", "mass_kg": 0.42},
            "home": {"lat": 1.0, "lon": 2.0, "alt_m": 3.0},
        },
    )

    relay.twin("sim-4")
    assert _last(stub) == ("GET", "/sims/sim-4/twin", None)
    relay.set_frame("sim-4", preset="large-450", cells=4)
    assert _last(stub) == ("PUT", "/sims/sim-4/frame", {"preset": "large-450", "cells": 4})

    # packs: importing and deleting install an executable, so they go with the operator token
    relay.import_autopilot("/tmp/px4-1.17.0-linux-x64.tar.gz")
    assert _last(stub) == ("POST", "/autopilots/import", {"path": "/tmp/px4-1.17.0-linux-x64.tar.gz"})
    assert stub.requests[-1]["operator"] == "op"
    relay.delete_autopilot("px4-1.17.0")
    assert _last(stub) == ("DELETE", "/autopilots/px4-1.17.0", None)


def test_app_operations_use_the_session_when_one_is_open(stub):
    relay = RelayClient(stub.base, operator_token="op")
    relay._token = "tok"  # as after open_session
    stub.responses["/sims"] = {"added": ["sim-2"]}
    relay.start_twin(pack="px4-1.16.2")
    assert stub.requests[-1]["session"] == "tok" and stub.requests[-1]["operator"] is None
    relay.capture_twin_profile("real-1")
    assert stub.requests[-1]["session"] == "tok"


def test_serial_and_hitl_calls(stub):
    relay = RelayClient(stub.base, operator_token="op")
    stub.responses["/serial/ports"] = {
        "ports": [{"device": "/dev/ttyACM0", "vid": 0x26AC, "pid": 0x0011, "autopilot": True}]
    }
    assert relay.serial_ports()[0]["autopilot"] is True
    assert _last(stub) == ("GET", "/serial/ports", None)

    relay.connect(serial="/dev/ttyACM0")
    assert _last(stub) == ("POST", "/vehicles", {"serial": {"device": "/dev/ttyACM0"}})
    relay.connect(serial="COM5", baud=921600)
    assert _last(stub) == ("POST", "/vehicles", {"serial": {"device": "COM5", "baud": 921600}})
    relay.connect(serial={"device": "/dev/ttyUSB0", "baud": "auto"})
    assert _last(stub) == ("POST", "/vehicles", {"serial": {"device": "/dev/ttyUSB0", "baud": "auto"}})
    relay.connect("10.0.0.5", 14550)
    assert _last(stub) == ("POST", "/vehicles", {"host": "10.0.0.5", "port": 14550})
    relay.connect_vehicle("10.0.0.6")
    assert _last(stub) == ("POST", "/vehicles", {"host": "10.0.0.6"})
    sent = len(stub.requests)
    with pytest.raises(ValueError):
        relay.connect()
    with pytest.raises(ValueError):
        relay.connect("10.0.0.5", serial="/dev/ttyACM0")
    assert len(stub.requests) == sent

    stub.responses["/vehicles/real-1/hitl"] = {"id": "real-1", "available": False, "state": "idle"}
    assert relay.hitl("real-1")["state"] == "idle"
    assert _last(stub) == ("GET", "/vehicles/real-1/hitl", None)
    relay.hitl("real-1", "start", confirm="HITL")
    assert _last(stub) == ("POST", "/vehicles/real-1/hitl", {"action": "start", "confirm": "HITL"})
    relay.hitl("real-1", "stop")
    assert _last(stub) == ("POST", "/vehicles/real-1/hitl", {"action": "stop"})
    with pytest.raises(ValueError):
        relay.hitl("real-1", "reboot")

    stub.fail["/vehicles/real-2/hitl"] = (
        409,
        {
            "code": "hitl_unavailable",
            "message_en": "Hardware in the loop is not available.",
            "message_zh": "硬件在环不可用。",
            "detail": {"reason": "ArduPilot removed HIL", "reason_zh": "ArduPilot 已移除 HIL"},
        },
    )
    with pytest.raises(RelayError) as exc:
        relay.hitl("real-2", "start", confirm="HITL")
    assert exc.value.code == "hitl_unavailable" and exc.value.status == 409
    assert exc.value.detail["reason_zh"] == "ArduPilot 已移除 HIL"


# ---------------------------------------------------------------------------- real backend


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
    yield url
    server.should_exit = True
    thread.join(timeout=5)


def test_sdk_twin_reads_and_frame_on_a_fast_simulator(base):
    op = RelayClient(base, operator_token=OP)
    packs = op.autopilots()
    assert {"platform", "packs"} <= set(packs)
    assert isinstance(op.twin_profiles(), list)
    with pytest.raises(RelayError) as exc:
        op.twin_profile("tp-00000000")
    assert exc.value.code == "profile_not_found"
    (vid,) = op.add_sims(1)
    end = time.monotonic() + 10
    while op.vehicle(vid)["link"]["state"] != "ok":
        assert time.monotonic() < end
        time.sleep(0.1)
    with pytest.raises(RelayError) as exc:
        op.capture_twin_profile(vid)
    assert exc.value.code == "real_only"
    framed = op.set_frame(vid, preset="small-250")
    assert framed["engine"] == "relay" and framed["frame"]["preset"] == "small-250"
    assert op.firmware(vid)["id"] == vid
