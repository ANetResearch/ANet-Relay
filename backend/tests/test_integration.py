"""Real FleetService on free ports against fake vehicles on 127.0.0.2 / 127.0.0.3 (03 §14).

Every assertion about the wire uses the frames the fake flight controller decoded with pymavlink.
"""

import subprocess
import sys
import time

import pytest
from conftest import REPO, free_tcp_port, free_udp_port
from fake_vehicle import FakeVehicle

from relay.authority import Actor
from relay.config import Settings
from relay.errors import RelayError
from relay.fleet import FleetService
from relay.history import History
from relay.mav.mavlink import M

OP = Actor.operator()
AP_ID = "real-127-0-0-2"
PX4_ID = "real-127-0-0-3"


def wait(fn, timeout=5.0, step=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(step)
    return False


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    port = free_udp_port()
    settings = Settings()
    settings.listen_port = port
    svc = FleetService(settings, None, operator_token="op", allow_loopback=True, history=History(None))
    svc.start()
    state = tmp_path_factory.mktemp("fakes")
    ap = FakeVehicle(
        "ardupilot", ("127.0.0.2", 14555), ("127.0.0.1", port), None, state_file=state / "a.json"
    )
    px4 = FakeVehicle("px4", ("127.0.0.3", 14555), ("127.0.0.1", port), None, state_file=state / "p.json")
    ap.start()
    px4.start()
    assert wait(lambda: AP_ID in svc.vehicles and PX4_ID in svc.vehicles, 5)
    assert wait(lambda: svc.vehicles[AP_ID].link_state == "ok" and svc.vehicles[PX4_ID].link_state == "ok", 5)
    yield svc, ap, px4, port
    svc.shutdown()
    ap.stop()
    px4.stop()


def view(svc, vid):
    return next((v for v in svc.fleet()["vehicles"] if v["id"] == vid), {})


def test_discovery_identity_and_isolation(rig):
    svc, _ap, _px4, _ = rig
    a, p = svc.vehicles[AP_ID], svc.vehicles[PX4_ID]
    assert (a.autopilot, p.autopilot) == ("ArduPilot", "PX4")
    assert a.state["sysid"] == 1 and a.state["compid"] == 1
    assert a.host == "127.0.0.2" and p.host == "127.0.0.3" and a.port == 14555
    assert wait(
        lambda: view(svc, AP_ID).get("firmware") == "4.5.7" and view(svc, PX4_ID).get("firmware") == "1.15.4"
    )
    events = [e for e in svc.events.after(0, 5000) if e["kind"] == "vehicle_discovered"]
    assert {e["vehicle"] for e in events} >= {AP_ID, PX4_ID}


def test_data_streams_requested_right_after_first_heartbeat(rig):
    svc, ap, px4, _ = rig
    streams = ap.received("REQUEST_DATA_STREAM")
    rates = {m.req_stream_id: m.req_message_rate for m in streams}
    assert rates[M.MAV_DATA_STREAM_EXTRA1] == 20 and rates[M.MAV_DATA_STREAM_POSITION] == 10
    assert rates[M.MAV_DATA_STREAM_EXTENDED_STATUS] == 2 and rates[M.MAV_DATA_STREAM_RAW_CONTROLLER] == 0
    first_rx = min(t for t, _ in ap.autopilot.received)
    assert min(t for t, m in ap.autopilot.received if m.get_type() == "REQUEST_DATA_STREAM") - first_rx < 0.5
    intervals = {
        int(m.param1): m.param2
        for m in px4.received("COMMAND_LONG")
        if m.command == M.MAV_CMD_SET_MESSAGE_INTERVAL
    }
    assert intervals[M.MAVLINK_MSG_ID_ATTITUDE] == pytest.approx(50000)
    assert intervals[M.MAVLINK_MSG_ID_LOCAL_POSITION_NED] == pytest.approx(100000)
    for fake in (ap, px4):
        requests = [m for m in fake.received("COMMAND_LONG") if m.command == M.MAV_CMD_REQUEST_MESSAGE]
        assert any(int(m.param1) == M.MAVLINK_MSG_ID_AUTOPILOT_VERSION for m in requests)
    assert wait(lambda: view(svc, AP_ID).get("attitude") and view(svc, PX4_ID).get("position"))
    assert wait(lambda: svc.params(AP_ID)["complete"] and svc.params(PX4_ID)["complete"], 6)


def test_stick_encoding_on_the_wire(rig):
    svc, ap, px4, _ = rig
    svc.set_calibration(AP_ID, {"roll": 1, "pitch": 1, "yaw": 1}, None)
    svc.possess(AP_ID, "keyboard")
    svc.possess(PX4_ID, "gamepad", 1)
    t0 = time.monotonic()
    for _ in range(10):
        svc.sticks(AP_ID, OP, {"roll": 0.5, "pitch": 0.25, "yaw": -0.2, "throttle": 0.6})
        svc.sticks(PX4_ID, OP, {"roll": 0.5, "pitch": 0.25, "yaw": -0.2, "throttle": 0.6})
        time.sleep(0.02)
    time.sleep(0.1)
    rc = [m for m in ap.received("RC_CHANNELS_OVERRIDE", since=t0) if m.chan1_raw != 0]
    assert rc and (rc[-1].chan1_raw, rc[-1].chan2_raw, rc[-1].chan3_raw, rc[-1].chan4_raw) == (
        1750,
        1375,
        1600,
        1400,
    )
    assert (rc[-1].chan5_raw, rc[-1].chan8_raw) == (65535, 65535)
    assert rc[-1].get_srcSystem() == 255 and rc[-1].get_srcComponent() == 190
    assert (rc[-1].target_system, rc[-1].target_component) == (1, 1)
    mc = px4.received("MANUAL_CONTROL", since=t0)
    assert mc and (mc[-1].x, mc[-1].y, mc[-1].z, mc[-1].r, mc[-1].target) == (250, 500, 600, -200, 1)
    assert not ap.received("MANUAL_CONTROL") and not px4.received("RC_CHANNELS_OVERRIDE")


def test_release_semantics_per_autopilot(rig):
    svc, ap, px4, _ = rig
    svc.possess(AP_ID, "keyboard")
    svc.possess(PX4_ID, "screen")  # one input source drives one aircraft
    time.sleep(1.1)  # let any earlier stop window close
    for _ in range(5):
        svc.sticks(AP_ID, OP, {"roll": 0.1, "pitch": 0, "yaw": 0, "throttle": 0.2})
        svc.sticks(PX4_ID, OP, {"roll": 0.1, "pitch": 0, "yaw": 0, "throttle": 0.2})
        time.sleep(0.02)
    t0 = time.monotonic()
    svc.release(AP_ID)
    svc.release(PX4_ID)
    time.sleep(0.4)
    after = ap.received("RC_CHANNELS_OVERRIDE", since=t0)
    first = next(i for i, m in enumerate(after) if m.chan3_raw == 0)  # a stick frame may still be in flight
    released = after[first:]
    assert len(released) == 4 and all(m.chan1_raw == m.chan3_raw == m.chan8_raw == 0 for m in released)
    assert px4.received("MANUAL_CONTROL", since=t0 + 0.05) == []  # PX4: stop sending, never z=0
    assert all(m.z != 0 for m in px4.received("MANUAL_CONTROL"))


def test_stale_timeout_stops_px4_stream(rig):
    svc, _ap, px4, _ = rig
    svc.possess(PX4_ID, "screen")
    svc.sticks(PX4_ID, OP, {"roll": 0, "pitch": 0, "yaw": 0, "throttle": 0.5})
    t0 = time.monotonic()
    time.sleep(1.3)
    frames = [t for t, m in px4.autopilot.received if t >= t0 and m.get_type() == "MANUAL_CONTROL"]
    assert frames and max(frames) - t0 < 0.9
    svc.release(PX4_ID)


def test_emergency_stop_px4_sends_zero_throttle_then_disarm(rig):
    svc, _ap, px4, _ = rig
    svc.possess(PX4_ID, "keyboard")
    t0 = time.monotonic()
    svc.stop(PX4_ID, OP)
    time.sleep(0.3)
    mc = px4.received("MANUAL_CONTROL", since=t0)
    assert mc and all(m.z == 0 and m.x == m.y == m.r == 0 for m in mc) and len(mc) <= 4
    disarm = [
        m for m in px4.received("COMMAND_LONG", since=t0) if m.command == M.MAV_CMD_COMPONENT_ARM_DISARM
    ]
    assert disarm and disarm[0].param1 == 0 and disarm[0].param2 == 0
    svc.release(PX4_ID)


def test_mode_switch_on_possess(rig):
    svc, _ap, _px4, _ = rig
    a = svc.vehicles[AP_ID]
    svc.possess(AP_ID, "keyboard")
    svc.set_mode(AP_ID, OP, "GUIDED")
    svc.release(AP_ID)
    assert a.mode == "GUIDED"
    svc.possess(AP_ID, "keyboard")
    assert wait(lambda: a.mode == "ALT_HOLD", 2.5)  # baro present -> ALT_HOLD
    svc.release(AP_ID)
    p = svc.vehicles[PX4_ID]
    svc.possess(PX4_ID, "keyboard")
    svc.set_mode(PX4_ID, OP, "LOITER")  # PX4 AUTO.LOITER ignores sticks
    svc.release(PX4_ID)
    svc.possess(PX4_ID, "keyboard")
    assert wait(lambda: p.mode == "ALTCTL", 2.5)
    svc.release(PX4_ID)


def test_param_write_and_readback_both_autopilots(rig):
    svc, ap, px4, _ = rig
    assert svc.set_param(AP_ID, "RC_OVERRIDE_TIME", 2.5, "RC_OVERRIDE_TIME")["value"] == 2.5
    assert ap.autopilot.params["RC_OVERRIDE_TIME"][0] == 2.5
    assert svc.set_param(PX4_ID, "COM_RC_IN_MODE", 1, "COM_RC_IN_MODE")["value"] == 1
    assert px4.autopilot.params["COM_RC_IN_MODE"][0] == 1.0
    with pytest.raises(RelayError) as exc:
        svc.set_param(PX4_ID, "COM_RC_IN_MODE", 1.5, "COM_RC_IN_MODE")
    assert exc.value.code == "param_type"
    svc.set_param(PX4_ID, "COM_RC_IN_MODE", 3, "COM_RC_IN_MODE")
    events = [e for e in svc.events.after(0, 9000) if e["kind"] == "param_set"]
    assert {e["data"]["name"] for e in events} >= {"RC_OVERRIDE_TIME", "COM_RC_IN_MODE"}


def test_preflight_gates_arming_real_aircraft(rig):
    svc, _ap, _px4, _ = rig
    svc.possess(AP_ID, "keyboard")
    with pytest.raises(RelayError) as exc:
        svc.arm(AP_ID, OP, True, None)
    assert exc.value.code == "confirm_required"
    with pytest.raises(RelayError) as exc:
        svc.arm(AP_ID, OP, True, "ARM")
    assert exc.value.code == "preflight_blocked" and set(exc.value.detail["blocking"]) == {"signs", "props"}
    svc.confirm(AP_ID, "signs")
    svc.confirm(AP_ID, "props")
    svc.set_mode(AP_ID, OP, "ALT_HOLD")
    for _ in range(5):
        svc.sticks(AP_ID, OP, {"roll": 0, "pitch": 0, "yaw": 0, "throttle": 0.5})
        time.sleep(0.02)
    assert svc.arm(AP_ID, OP, True, "ARM")["armed"] is True
    assert svc.arm(AP_ID, OP, False)["armed"] is False
    svc.release(AP_ID)


def test_agent_skill_on_real_autopilot(rig):
    svc, ap, _px4, _ = rig
    opened = svc.open_session("it-agent")
    s = svc.sessions.get(opened["session_id"])
    agent = Actor.session(s.session_id, s.name)
    svc.claim(AP_ID, agent)
    svc.run_skill(AP_ID, agent, "takeoff", {"alt": 1.0, "confirm": "ARM"})
    a = svc.vehicles[AP_ID]
    assert wait(lambda: a.skill.state == "done", 15)
    assert ap.autopilot.mode == "GUIDED" and ap.autopilot.quad.s.p[2] > 0.85
    keepalive = ap.received("RC_CHANNELS_OVERRIDE", since=time.monotonic() - 0.2)
    assert keepalive and keepalive[-1].chan3_raw == 1500  # neutral RC keep-alive while the skill holds
    svc.run_skill(AP_ID, agent, "land", {})
    assert wait(lambda: not a.armed, 15)
    svc.unclaim(AP_ID, agent)


def test_link_loss_and_restore(rig):
    svc, _ap, px4, _ = rig
    p = svc.vehicles[PX4_ID]
    svc.possess(PX4_ID, "keyboard")
    px4.set_drop(True)
    for _ in range(60):
        try:
            svc.sticks(PX4_ID, OP, {"roll": 0, "pitch": 0, "yaw": 0, "throttle": 0.4})
        except RelayError as exc:
            assert exc.code == "link_not_ready"
        time.sleep(0.1)
        if p.link_state == "lost":
            break
    assert p.link_state == "lost" and p.sticks is None
    kinds = [e["kind"] for e in svc.events.after(0, 9000) if e["vehicle"] == PX4_ID]
    assert "link_delayed" in kinds and "link_lost" in kinds
    assert any(a["code"] == "link_lost" for a in svc.alerts.view())
    t_restore = time.monotonic()
    px4.set_drop(False)
    assert wait(lambda: p.link_state == "ok", 3)
    time.sleep(0.5)
    assert px4.received("MANUAL_CONTROL", since=t_restore) == []  # control does not resume by itself
    assert "link_restored" in [e["kind"] for e in svc.events.after(0, 9000) if e["vehicle"] == PX4_ID]
    svc.release(PX4_ID)


def test_capability_gating_without_position(tmp_path):
    port = free_udp_port()
    settings = Settings()
    settings.listen_port = port
    svc = FleetService(settings, None, operator_token="op", allow_loopback=True, history=History(None))
    svc.start()
    fake = FakeVehicle(
        "px4", ("127.0.0.6", 14555), ("127.0.0.1", port), None, position=False, state_file=tmp_path / "n.json"
    )
    fake.start()
    try:
        vid = "real-127-0-0-6"
        assert wait(lambda: vid in svc.vehicles and svc.vehicles[vid].link_state == "ok", 5)
        assert wait(lambda: svc.vehicles[vid].capabilities.althold, 3)
        caps = svc.vehicles[vid].capabilities
        assert caps.manual and caps.althold and not caps.position
        opened = svc.open_session("gate")
        agent = Actor.session(opened["session_id"], "gate")
        svc.claim(vid, agent)
        for name, params in (("goto", {"x": 0, "y": 0, "z": 1}), ("rtl", {})):
            with pytest.raises(RelayError) as exc:
                svc.run_skill(vid, agent, name, params)
            assert exc.value.code == "capability_missing"
        # Takeoff climbs on the throttle stick in ALTCTL; a real aircraft still needs ARM.
        with pytest.raises(RelayError) as exc:
            svc.run_skill(vid, agent, "takeoff", {"alt": 1.0})
        assert exc.value.code != "capability_missing"
        with pytest.raises(RelayError) as exc:
            svc.course_start(vid, agent, "cage-loop")
        assert exc.value.code == "no_position_estimate"
        svc.run_skill(vid, agent, "hold", {})
        assert wait(lambda: svc.vehicles[vid].mode == "ALTCTL", 5)
    finally:
        svc.shutdown()
        fake.stop()


def test_bridge_provisioning_reboot_reappears(tmp_path):
    port = free_udp_port()
    http_port = free_tcp_port("127.0.0.4")
    settings = Settings()
    settings.listen_port = port
    svc = FleetService(
        settings, tmp_path / "data", operator_token="op", allow_loopback=True, history=History(None)
    )
    svc.start()
    fake = FakeVehicle(
        "ardupilot",
        ("127.0.0.4", 14555),
        ("127.0.0.1", port),
        ("127.0.0.4", http_port),
        state_file=tmp_path / "bridge.json",
        sta_bind="127.0.0.5",
        reboot_s=0.5,
    )
    fake.start()
    try:
        result = svc.discover(["127.0.0.4/32"], http_port)
        assert result["total"] == 1
        assert wait(lambda: not svc.scan["running"] and svc.scan["found"] == 1, 5)
        assert svc.vehicles["real-127-0-0-4"].bridge["version"] == "1.2.2"
        record = svc.bridge_read("127.0.0.4", http_port)
        assert record["kind"] == "mavesp8266" and record["settings"]["mode"] == "ap"
        assert "fake-bridge-pass" not in str(record)  # passwords never leave the bridge
        out = svc.bridge_configure(
            "127.0.0.4", http_port, {"mode": "station", "ssid": "anet4d", "password": "fake-bridge-pass"}
        )
        assert out["rebooting"] and out["args"]["pwdsta"] == "***"
        assert wait(
            lambda: "real-127-0-0-5" in svc.vehicles and svc.vehicles["real-127-0-0-5"].link_state == "ok", 8
        )
        assert fake.bridge["mode"] == 1 and fake.bridge["ssidsta"] == "anet4d"
        assert svc.settings.last_network_ssid == "anet4d"
        assert "fake-bridge-pass" not in (tmp_path / "data" / "settings.json").read_text()
        kinds = [e["kind"] for e in svc.events.after(0, 9000)]
        assert "bridge_configured" in kinds
        unknown = svc.bridge_read("127.0.0.5", http_port)
        assert unknown["settings"]["mode"] == "station"
    finally:
        svc.shutdown()
        fake.stop()


def test_fake_vehicle_cli_runs(tmp_path):
    port = free_udp_port()
    settings = Settings()
    settings.listen_port = port
    svc = FleetService(settings, None, operator_token="op", allow_loopback=True, history=History(None))
    svc.start()
    proc = subprocess.Popen(
        [
            sys.executable,
            str(REPO / "tools" / "fakes" / "fake_vehicle.py"),
            "--autopilot",
            "px4",
            "--bind",
            "127.0.0.7:14555",
            "--gcs",
            f"127.0.0.1:{port}",
            "--http",
            "off",
            "--state-file",
            str(tmp_path / "cli.json"),
            "--drop-after",
            "2",
        ],
        stderr=subprocess.PIPE,
    )
    try:
        assert wait(
            lambda: "real-127-0-0-7" in svc.vehicles and svc.vehicles["real-127-0-0-7"].autopilot == "PX4", 6
        )
        assert wait(lambda: svc.vehicles["real-127-0-0-7"].link_state == "delayed", 7)
    finally:
        proc.terminate()
        proc.wait(5)
        svc.shutdown()
