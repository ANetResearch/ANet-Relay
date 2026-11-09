"""Firmware twins end to end (11 §6, §13): official ArduCopter and PX4 SITL builds on Relay's physics.

Each test creates a twin through the HTTP API on a fleet stepped by a fake clock (the lockstep runs as fast
as the firmware computes), flies the takeoff-hover-land timeline in altitude hold, and checks the climb, the
hold, the landing and the disarm on the physics truth; then it checks the firmware's sockets (loopback
only), kills the process (``twin_exited``) and removes the simulator (process gone). The tests skip when the
runtime packs are not installed (``node scripts/fetch-autopilots.mjs``).
"""

from __future__ import annotations

import os
import signal
import sys
import time

import pytest
from conftest import FakeClock, Stepper
from fastapi.testclient import TestClient
from test_sequence import hover_doc

from relay.api.app import create_app
from relay.fleet import FleetService
from relay.history import History
from relay.twin.packs import PackStore
from relay.twin.process import bound_sockets

OP = {"X-Relay-Operator": "op-token"}
API = "/api/v1"
STORE = PackStore()
ARDUPILOT_RC_PORT = 5501  # 0.0.0.0 in the official binary (11 §2, documented limitation)


def _pack(pack_id: str) -> None:
    pack = STORE.get(pack_id)
    if pack is None or pack.problem() is not None:
        pytest.skip(f"autopilot pack {pack_id} is not installed")


@pytest.fixture
def twin_client():
    clock = FakeClock()
    svc = FleetService(
        listen=False, clock=clock, wall=clock.wall, history=History(None), operator_token="op-token"
    )
    svc.update_settings({"sim_airframe_default": "ideal"})
    app = create_app(svc, manage_service=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.svc = svc
        client.stepper = Stepper(svc, clock)
        yield client
    svc.shutdown()


def _wait(client, predicate, seconds: float) -> bool:
    return client.stepper.until(predicate, seconds)


def _events(svc, kind: str) -> list[dict]:
    return [e for e in svc.events.after(0, 5000) if e["kind"] == kind]


def _fly_hover(client, vid: str, settle_s: float) -> dict:
    """Possess, fly hover_doc in altitude hold, and return the measured figures.

    ``settle_s``: sim time after boot before the takeoff, as an operator waits for the estimator to settle
    (ArduPilot's EKF3 uses GPS about 40 s after boot; PX4 reports a position at once, its height estimate
    settles over the first seconds)."""
    svc = client.svc
    v = svc.vehicles[vid]
    t0 = client.stepper.clock.t
    assert _wait(client, lambda: v.link_state == "ok", 10.0)
    assert _wait(client, lambda: v.capabilities.position, 60.0), "no position estimate"
    client.stepper.run(max(0.0, settle_s - (client.stepper.clock.t - t0)))
    assert (
        client.post(f"{API}/vehicles/{vid}/possess", headers=OP, json={"input": "keyboard"}).status_code
        == 200
    )
    out = client.post(f"{API}/vehicles/{vid}/sequence", headers=OP, json={"sequence": hover_doc(hold_s=8.0)})
    assert out.status_code == 200, out.text
    quad = v.driver.sim.quad
    hold, peak, armed_seen = [], 0.0, False
    end = client.stepper.clock.t + 45.0
    while client.stepper.clock.t < end and v.skill is not None and v.skill.active:
        client.stepper.run(0.05)
        armed_seen = armed_seen or v.armed
        peak = max(peak, quad.s.p[2])
        progress = v.skill.view()["progress_s"]
        if 6.0 <= progress <= 11.0:  # the hover block (3 s to 11 s), after the climb settles
            hold.append(quad.s.p[2])
    assert v.skill is not None and v.skill.state == "done", v.skill.detail if v.skill else None
    _wait(client, lambda: not v.armed, 10.0)
    return {
        "armed_seen": armed_seen,
        "peak": peak,
        "hold": hold,
        "final_z": quad.s.p[2],
        "armed": v.armed,
        "crashed": quad.s.crashed,
    }


def _check_sockets(pid: int, family: str) -> None:
    if not sys.platform.startswith("linux"):
        return
    socks = bound_sockets(pid)
    assert socks, "the firmware holds no socket"
    for proto, address, port, state in socks:
        if address in ("127.0.0.1", "::1") or address.startswith("::ffff:127."):
            continue
        if state == "01" or (proto.startswith("udp") and address in ("0.0.0.0", "::") and port == 0):
            continue  # an established connection, or nothing bound
        # The official ArduPilot binary binds its RC input port and its physics client socket on 0.0.0.0
        # with no option to change it (11 §2, documented); nothing else may listen beyond loopback.
        assert family == "ardupilot" and proto == "udp" and address == "0.0.0.0", (
            proto,
            address,
            port,
            state,
        )
        assert port == ARDUPILOT_RC_PORT or port >= 32768, (proto, address, port)
    assert not [s for s in socks if s[0].startswith("tcp") and s[3] == "0A" and s[1] != "127.0.0.1"]


def _exit_and_remove(client, vid: str) -> None:
    svc = client.svc
    v = svc.vehicles[vid]
    twin = v.driver.sim
    pid = twin.pid
    assert pid is not None
    os.kill(pid, signal.SIGKILL)
    assert _wait(client, lambda: _events(svc, "twin_exited"), 5.0)
    exited = _events(svc, "twin_exited")[-1]
    assert exited["vehicle"] == vid and exited["data"]["code"] is not None
    assert v.link_state == "lost"
    detail = client.get(f"{API}/sims/{vid}/twin").json()
    assert detail["state"] == "exited" and detail["exit"]["code"] == exited["data"]["code"]
    assert client.delete(f"{API}/vehicles/{vid}", headers=OP).status_code == 200
    assert vid not in svc.vehicles


def _assert_gone(pid: int) -> None:
    end = time.monotonic() + 3.0
    while time.monotonic() < end and os.path.exists(f"/proc/{pid}"):
        time.sleep(0.05)
    if sys.platform.startswith("linux"):
        assert not os.path.exists(f"/proc/{pid}")


@pytest.mark.parametrize(
    ("pack_id", "settle_s"),
    [("ardupilot-copter-4.7.2", 0.0), ("px4-1.16.2", 20.0)],
)
def test_twin_flies_takeoff_hover_land_over_the_api(twin_client, pack_id, settle_s):
    _pack(pack_id)
    client = twin_client
    svc = client.svc
    added = client.post(f"{API}/sims", headers=OP, json={"engine": "sitl", "pack": pack_id})
    assert added.status_code == 200, added.text
    (vid,) = added.json()["added"]
    started = _events(svc, "twin_started")
    assert started and started[-1]["data"]["pack"] == pack_id and started[-1]["data"]["restart"] is False
    family = pack_id.split("-")[0]
    snap = svc.vehicles[vid]
    client.stepper.run(1.0)
    view = svc._vehicle_view(snap, client.stepper.clock.t, svc.wall())
    assert view["kind"] == "sim" and view["sim"]["engine"] == "sitl"
    assert view["sim"]["firmware"] == {
        "family": family,
        "version": pack_id.rsplit("-", 1)[1],
        "pack": pack_id,
        "match": None,
        "state": "running",
    }
    twin = client.get(f"{API}/sims/{vid}/twin").json()
    assert (
        twin["state"] == "running"
        and twin["pack"]["id"] == pack_id
        and twin["frame"]["preset"] == "micro-120"
    )
    assert twin["params"]["applied"] == 0 and twin["params"]["forced"] == (
        {"SIM_RATE_HZ": 1000.0} if family == "ardupilot" else {}
    )
    # hover assist does not exist on a twin
    refused = client.post(f"{API}/sims/{vid}/assist", headers=OP, json={"on": False})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "twin_unsupported"

    figures = _fly_hover(client, vid, settle_s)
    assert figures["armed_seen"] and not figures["crashed"]
    hold = figures["hold"]
    assert figures["peak"] > 0.2, figures["peak"]  # it climbed (PX4 ramps its takeoff over MPC_TKO_RAMP_T)
    assert len(hold) > 50
    centre = (max(hold) + min(hold)) / 2.0
    assert max(abs(z - centre) for z in hold) <= 0.15, (min(hold), max(hold), figures["peak"])  # hold ±0.15 m
    assert figures["final_z"] < 0.05 and not figures["armed"]  # landed and disarmed

    # firmware identity as the snapshot reports it
    info = svc._vehicle_view(snap, client.stepper.clock.t, svc.wall())["firmware_info"]
    assert info["family"] == family and info["version"] == pack_id.rsplit("-", 1)[1]
    assert info["git"] == STORE.get(pack_id).git8

    pid = svc.vehicles[vid].driver.sim.pid
    _check_sockets(pid, family)
    _exit_and_remove(client, vid)
    _assert_gone(pid)


def test_twin_remove_kills_the_process(twin_client):
    _pack("ardupilot-copter-4.7.2")
    client = twin_client
    (vid,) = client.post(f"{API}/sims", headers=OP, json={"engine": "sitl"}).json()["added"]
    pid = client.svc.vehicles[vid].driver.sim.pid
    assert (pid is not None and os.path.exists(f"/proc/{pid}")) or not sys.platform.startswith("linux")
    assert client.delete(f"{API}/sims", headers=OP, params={"id": vid}).json()["removed"] == [vid]
    _assert_gone(pid)


def test_older_ardupilot_boots_and_identifies(twin_client):
    _pack("ardupilot-copter-4.3.8")
    client = twin_client
    added = client.post(f"{API}/sims", headers=OP, json={"engine": "sitl", "pack": "ardupilot-copter-4.3.8"})
    assert added.status_code == 200, added.text
    (vid,) = added.json()["added"]
    v = client.svc.vehicles[vid]
    assert _wait(client, lambda: v.link_state == "ok" and v.state["version"] is not None, 10.0)
    firmware = client.get(f"{API}/vehicles/{vid}/firmware").json()
    assert firmware["identity"]["family"] == "ardupilot"
    assert firmware["identity"]["version"] == "4.3.8"
    assert firmware["identity"]["git"] == STORE.get("ardupilot-copter-4.3.8").git8
    assert firmware["match"]["recommended"] == "ardupilot-copter-4.3.8"
    assert firmware["match"]["match"] == "exact"
