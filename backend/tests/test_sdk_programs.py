"""The Python SDK's program, judge and airframe calls against a real backend served by uvicorn (06 §6, WP-4b).

The agent session stays at the default agent flight level ``sticks``: a manual mode, arming and a program is
the whole flight path.
"""

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

OP = "sdk-programs-op"


@pytest.fixture(scope="module")
def base():
    settings = Settings()
    assert settings.agent_flight_level == "sticks"
    svc = FleetService(settings, None, operator_token=OP, listen=False, history=History(None))
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


def _until(fn, timeout=10.0):
    end = time.monotonic() + timeout
    while True:
        value = fn()
        if value:
            return value
        assert time.monotonic() < end, "timed out"
        time.sleep(0.1)


def test_sdk_writes_tests_and_versions_a_program(base):
    agent = RelayClient(base)
    agent.open_session("sdk-programs")
    try:
        ids = {p["id"] for p in agent.program_list()}
        assert {"hover_trim", "liftoff", "patrol", "hunter_easy"} <= ids
        builtin = agent.program_get("hover_trim")
        assert builtin["builtin"] is True and "def step" in builtin["source"]

        created = agent.program_create("SDK hover", copy_of="hover_trim", note="from the SDK")
        pid = created["program"]["id"]
        assert created["validation"]["ok"] is True and created["program"]["author"] == "sdk-programs"
        source = agent.program_get(pid)["source"]
        first = source.split("\n")[0]
        patched = agent.program_update(
            pid, patch=[{"start": 1, "end": 1, "text": "# tuned " + first}], note="c"
        )
        assert patched["program"]["rev"] == 2 and patched["validation"]["ok"] is True
        assert agent.program_get(pid)["source"].startswith("# tuned ")
        revisions = agent.program_revisions(pid)
        assert [r["rev"] for r in revisions] == [2, 1] and revisions[0]["note"] == "c"

        bad = agent.program_validate(pid, source="import os\n")
        assert bad["ok"] is False and bad["errors"][0]["line"] == 1
        results = agent.program_test(pid)
        assert results["total"] >= 1 and results["passed"] == results["total"]
        custom = agent.program_test(
            pid, tests=[{"name": "low", "obs": {"alt": 0.4}, "expect": {"throttle": [0.55, 1.0]}}]
        )
        assert custom["passed"] == 1
        with pytest.raises(RelayError) as exc:
            agent.program_update("hover_trim", source="x = 1\n", note="no")
        assert exc.value.code == "program_readonly"
        agent.program_delete(pid)
    finally:
        agent.close()


def test_sdk_flies_a_program_at_the_sticks_level(base):
    op = RelayClient(base, operator_token=OP)
    (vid,) = op.add_sims(1, hover_assist=False)
    _until(lambda: op.vehicle(vid)["link"]["state"] == "ok")
    agent = RelayClient(base)
    agent.open_session("sdk-pilot")
    try:
        fitted = agent.airframe_set(vid, preset="rough", seed=2)
        assert (fitted["preset"], fitted["seed"]) == ("rough", 2) and fitted["hover_throttle"] > 0.5
        assert agent.airframe_get(vid)["preset"] == "rough"
        agent.claim(vid)
        with pytest.raises(RelayError) as exc:
            agent.takeoff(vid, 1.0)
        assert exc.value.code == "sticks_only"
        agent.mode(vid, "STABILIZE")
        agent.arm(vid)
        _until(lambda: agent.vehicle(vid)["armed"])
        run = agent.program_deploy(
            vid, "hover_trim", params={"target_alt": 0.8}, seed=3, airframe={"preset": "typical", "seed": 4}
        )
        assert run["program"]["status"] == "running"
        assert agent.airframe_get(vid)["preset"] == "typical"
        status = _until(lambda: (s := agent.program_status(vid)) and s["fps"] > 20 and s)
        assert status["id"] == "hover_trim" and status["mode"] == "pilot"
        tuned = agent.program_params(vid, target_alt=1.0)
        assert tuned is not None
        log = agent.program_log(vid)
        assert log["program"] == "hover_trim" and any("deployed" in line["text"] for line in log["lines"])
        stopped = agent.program_stop(vid)
        assert stopped is not None
        assert _until(lambda: agent.program_status(vid)["status"] == "stopped")
        with pytest.raises(RelayError) as exc:
            agent.program_resume(vid)
        assert exc.value.code in ("program_not_paused", "no_program")
    finally:
        agent.close()
        op.remove_sims([vid])


def test_sdk_judges_a_program_and_runs_a_duel(base):
    """WP-5: the SDK judge calls against the merged judge routes, the evaluator in its worker process."""
    agent = RelayClient(base)
    agent.open_session("sdk-judge")
    try:
        listed = agent.challenge_list()["challenges"]
        assert [c["id"] for c in listed][:2] == ["liftoff", "hover"] and len(listed) == 9
        job = agent.challenge_submit("liftoff", "liftoff", timeout=120)
        assert job["state"] == "done", job
        result = job["result"]
        assert result["challenge"] == "liftoff" and result["passed"] and len(result["seeds"]) == 5
        assert job["programs"] == [{"program": "liftoff", "rev": 1, "name": "Lift-off"}]
        trace = agent.trace(result["trace_id"])
        assert trace["kind"] == "challenge" and trace["aircraft"][0]["program"] == "liftoff"
        assert len(trace["t"]) == len(trace["aircraft"][0]["frames"]) > 0
        assert agent.job_status(job["id"])["state"] == "done"
        duel = agent.duel_run(
            ["hunter_easy", "hunter_hard"], rounds=1, seed=3, scenario={"duration_s": 15}, timeout=120
        )
        assert duel["state"] == "done", duel
        assert len(duel["result"]["rounds"]) == 1
        ladder = agent.ladder()["ladder"]
        assert {r["program"] for r in ladder} == {"hunter_easy", "hunter_hard"}
        with pytest.raises(RelayError) as exc:
            agent.job_status("job-missing")
        assert exc.value.code == "job_not_found"
        with pytest.raises(RelayError) as exc:
            agent.challenge_submit("nope", "liftoff")
        assert exc.value.code == "challenge_not_found"
    finally:
        agent.close()
