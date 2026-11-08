"""The Python SDK's dogfight, leaderboard and match calls against a real backend served by uvicorn
(07 §3-§4, WP-B)."""

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

OP = "sdk-match-op"


@pytest.fixture(scope="module")
def base():
    svc = FleetService(Settings(), None, operator_token=OP, listen=False, history=History(None))
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


def _until(fn, timeout=15.0):
    end = time.monotonic() + timeout
    while True:
        value = fn()
        if value:
            return value
        assert time.monotonic() < end, "timed out"
        time.sleep(0.1)


def test_sdk_runs_a_dogfight_and_reads_the_leaderboard(base):
    op = RelayClient(base, operator_token=OP)
    assert op.dogfight_status()["running"] is False
    with pytest.raises(RelayError) as exc:
        op.match_start(60)
    assert exc.value.code == "arena_off"
    with pytest.raises(RelayError) as exc:
        op.dogfight_start(2, vehicles=["px4-1"])
    assert (exc.value.code, exc.value.status) == ("dogfight_real_aircraft", 400)
    with pytest.raises(RelayError) as exc:
        op.dogfight_start(2, roles={"gunner": 2, "rammer": 1})
    assert exc.value.status == 400

    view = op.dogfight_start(2, roles={"gunner": 1, "rammer": 1}, duration_s=30, seed=4)
    assert view["running"] is True and len(view["aircraft"]) == 2
    assert sorted(a["role"] for a in view["aircraft"]) == ["gunner", "rammer"]
    assert {a["program"] for a in view["aircraft"]} == {"hunter_medium", "rammer"}
    assert view["match"] is not None and view["match"]["running"] is True
    with pytest.raises(RelayError) as exc:
        op.dogfight_start(2)
    assert exc.value.code == "dogfight_running"
    with pytest.raises(RelayError) as exc:
        op.match_start(60)
    assert exc.value.code == "match_running"

    _until(lambda: op.dogfight_status()["phase"] == "running")
    board = op.leaderboard("survival")
    assert board["by"] == "survival" and board["match"]["running"] is True
    assert {r["aircraft"].lower() for r in board["rows"]} >= {a["vid"] for a in view["aircraft"]}
    _until(  # the controller column refreshes at 5 Hz
        lambda: {r["controller"]["kind"] for r in op.leaderboard()["rows"] if r["local"]} == {"program"}
    )
    with pytest.raises(RelayError):
        op.leaderboard("fastest")

    stopped = op.dogfight_stop()
    assert stopped["phase"] in ("ending", "done") and stopped["reason"] == "stopped"
    done = _until(lambda: (s := op.dogfight_status())["phase"] == "done" and s, 30)
    assert done["running"] is False
    with pytest.raises(RelayError) as exc:
        op.dogfight_stop()
    assert exc.value.code == "dogfight_not_running"
    final = op.leaderboard()
    assert final["match"] is not None and final["match"]["running"] is False and final["rows"]
