"""The Python SDK's hover-assist calls against a real backend served by uvicorn (05 §5.2)."""

from __future__ import annotations

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


def test_sdk_add_without_assist_and_switch(base):
    op = RelayClient(base, operator_token=OP)
    (vid,) = op.add_sims(1, hover_assist=False)
    end = time.monotonic() + 10
    while op.vehicle(vid)["link"]["state"] != "ok":
        assert time.monotonic() < end
        time.sleep(0.1)
    assert op.vehicle(vid)["sim"]["hover_assist"] is False
    assert op.sim_assist(vid, True) == {"id": vid, "hover_assist": True}
    assert op.vehicle(vid)["sim"]["hover_assist"] is True

    agent = RelayClient(base)
    agent.open_session("assist-test")
    try:
        assert agent.sim_assist(vid, False)["hover_assist"] is False  # nobody controls it
    finally:
        agent.close()


def test_sdk_arena_view_and_fire(base):
    # LAN arena (05 §7.4-§7.5): the read-only view and the agents' trigger, which needs the arena
    op = RelayClient(base, operator_token=OP)
    view = op.arena()
    assert view["enabled"] is False and view["local"]["max_ammo"] == 24
    (vid,) = op.add_sims(1)
    agent = RelayClient(base)
    agent.open_session("arena-test")
    try:
        end = time.monotonic() + 10
        while op.vehicle(vid)["link"]["state"] != "ok":
            assert time.monotonic() < end
            time.sleep(0.1)
        agent.claim(vid)
        with pytest.raises(RelayError) as exc:
            agent.fire(vid, 1.0)
        assert exc.value.code == "arena_off"
    finally:
        agent.close()
