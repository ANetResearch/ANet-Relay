"""Serial links and PX4 hardware in the loop (11 §7, §10) against a scripted fake PX4 board.

The board (``tools/fakes/fake_px4_serial.py``) runs on the test's fake clock behind an in-process port
(``FleetService.serial_opener``), so the whole flow (backup, write, reboot, run, restore, verify) runs in
simulated seconds. One test drives the same board over a pseudo-terminal through pyserial itself. Real
flight controller hardware is not part of these tests (11 §13).
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from types import SimpleNamespace

import pytest
from fake_px4_serial import FakePx4, serve_fd
from fastapi.testclient import TestClient

from relay.api.app import create_app
from relay.api.schemas import Fleet
from relay.api.schemas_hitl import HitlStatus, SerialPorts
from relay.errors import RelayError
from relay.fleet import FleetService
from relay.history import History
from relay.mav import serial_link as sl
from relay.mav.decode import px4_param_decode, px4_param_encode
from relay.mav.mavlink import Encoder, M, Parser
from relay.sim.physics import Quad
from relay.sim.sensors import SensorModel
from relay.twin import hil
from relay.twin import hitl as hitlmod

DEVICE = "/dev/ttyACM0"
OP = {"X-Relay-Operator": "op-token"}
API = "/api/v1"


@pytest.fixture(autouse=True)
def _no_host_ports(monkeypatch):
    """The tests never see this computer's own serial ports."""
    monkeypatch.setattr(sl, "_comports", lambda: [])


@pytest.fixture
def board(clock) -> FakePx4:
    return FakePx4(clock)


def connect(svc, stepper, board, device: str = DEVICE, baud="auto") -> str:
    svc.serial_opener = board.opener
    svc.serial_probe_s = 0.2
    vid = svc.connect_serial(device, baud)["id"]
    v = svc._vehicle(vid)
    assert stepper.until(lambda: v.link_state == "ok" and v.autopilot is not None, 3.0)
    return vid


def start_hitl(svc, stepper, board, vid: str) -> None:
    svc.possess(vid, "keyboard")
    stepper.run(0.2)
    status = svc.hitl_action(vid, "start", "HITL", _operator())
    assert status["state"] == "backing_up"
    v = svc._vehicle(vid)
    assert stepper.until(lambda: v.hitl.state == "running", 20.0), v.hitl.view()


def _operator():
    from relay.authority import Actor

    return Actor.operator("keyboard")


def events(svc, kind: str) -> list[dict]:
    return [e for e in svc.events.after(0, limit=5000) if e["kind"] == kind]


# ---------------------------------------------------------------------------- ports


def _info(device, vid=None, pid=None, description="n/a", manufacturer=None, serial_number=None):
    return SimpleNamespace(
        device=device,
        name=device.rsplit("/", 1)[-1],
        description=description,
        manufacturer=manufacturer,
        product=None,
        vid=vid,
        pid=pid,
        serial_number=serial_number,
    )


def test_classify_known_usb_interfaces():
    assert sl.classify(0x26AC, 0x0011, "") == "autopilot"  # PX4 / 3DR
    assert sl.classify(0x1209, 0x5740, "") == "autopilot"  # ArduPilot ChibiOS
    assert sl.classify(0x1209, 0x5741, "") == "autopilot"
    assert sl.classify(0x2DAE, 0x1016, "") == "autopilot"  # CubePilot
    assert sl.classify(0x3162, 0x004B, "") == "autopilot"  # Holybro
    assert sl.classify(0x0483, 0x5740, "") == "autopilot"  # ST virtual COM
    assert sl.classify(0x0483, 0x3748, "") == "other"  # an ST-Link, not a flight controller
    assert sl.classify(0x10C4, 0xEA60, "CP2102 USB to UART") == "usb-serial"
    assert sl.classify(0x0403, 0x6001, "FT232R") == "usb-serial"
    assert sl.classify(0x1A86, 0x7523, "USB-SERIAL CH340") == "usb-serial"
    assert sl.classify(None, None, "PX4 FMU v5.x") == "autopilot"
    assert sl.classify(None, None, "ttyS0") == "other"


def test_port_listing_shape(monkeypatch):
    infos = [
        _info("/dev/ttyUSB0", 0x10C4, 0xEA60, "CP2102 USB to UART Bridge Controller", "Silicon Labs", "0001"),
        _info("/dev/ttyS4"),
        _info("/dev/ttyACM0", 0x26AC, 0x0032, "PX4 FMU v5.x", "Auterion", "3E0031000B51333130373938"),
    ]
    monkeypatch.setattr(sl, "_comports", lambda: infos)
    app = create_app(
        FleetService(listen=False, history=History(None), operator_token="op-token"), manage_service=False
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        body = client.get(f"{API}/serial/ports").json()  # anonymous read
    SerialPorts.model_validate(body)
    assert body["available"] is True
    devices = [p["device"] for p in body["ports"]]
    assert devices == ["/dev/ttyACM0", "/dev/ttyUSB0"]  # flight controllers first; legacy ttyS hidden
    fc, radio = body["ports"]
    assert fc["autopilot"] is True and fc["kind"] == "autopilot" and fc["vid"] == 0x26AC
    assert fc["serial_number"] == "3E0031000B51333130373938" and fc["vehicle"] is None
    assert radio["autopilot"] is False and radio["kind"] == "usb-serial" and radio["adapter"] == "CP210x"
    for key in ("device", "name", "description", "manufacturer", "vid", "pid", "serial_number", "autopilot"):
        assert key in fc


def test_port_listing_without_pyserial(monkeypatch):
    def missing():
        raise sl.SerialUnavailable("pyserial_missing", "pyserial is not installed")

    monkeypatch.setattr(sl, "_comports", missing)
    out = sl.list_ports()
    assert out == {
        "available": False,
        "reason": "pyserial is not installed",
        "reason_zh": "未安装 pyserial，串口不可用",
        "ports": [],
    }


# ---------------------------------------------------------------------------- serial link


def test_serial_connect_heartbeat_and_snapshot(svc, stepper, board):
    vid = connect(svc, stepper, board)
    assert vid == "usb-ttyACM0"
    v = svc._vehicle(vid)
    assert v.autopilot == "PX4" and v.kind == "real" and v.host is None
    stepper.run(2.0)
    assert v.state["firmware"] and "1.16.2" in v.state["firmware"]
    snap = svc.fleet()
    Fleet.model_validate(snap)
    view = next(x for x in snap["vehicles"] if x["id"] == vid)
    assert view["transport"] == "serial"
    assert view["serial"]["device"] == DEVICE and view["serial"]["baud"] == 115200 and view["serial"]["open"]
    assert view["hitl"] is None and view["bridge"] is None
    discovered = events(svc, "vehicle_discovered")[-1]
    assert discovered["data"]["via"] == "USB" and discovered["data"]["device"] == DEVICE
    # the same device again: the same aircraft, no second port
    assert svc.connect_serial(DEVICE, "auto") == {"id": vid}
    # Relay's GCS heartbeat reaches the board over the port
    assert "HEARTBEAT" in board.received


def test_baud_auto_finds_the_uart_rate_and_errors(svc, stepper, clock):
    uart = FakePx4(clock, baud=921600)
    vid = connect(svc, stepper, uart, "/dev/ttyUSB0", "auto")
    assert svc._vehicle(vid).driver.link.baud == 921600

    other = FakePx4(clock, baud=57600)
    svc.serial_opener = other.opener
    with pytest.raises(RelayError) as no_beat:
        svc.connect_serial("/dev/ttyUSB1", 115200)
    assert no_beat.value.code == "serial_unavailable" and no_beat.value.status == 400
    assert no_beat.value.detail["cause"] == "no_heartbeat"
    assert no_beat.value.detail["reason_zh"] == "未收到飞控心跳"

    def busy(device, baud):
        raise sl.SerialUnavailable("busy", f"{device}: Could not exclusively lock port")

    svc.serial_opener = busy
    with pytest.raises(RelayError) as locked:
        svc.connect_serial("/dev/ttyUSB2", "auto")
    assert locked.value.status == 409 and locked.value.detail["cause"] == "busy"
    with pytest.raises(RelayError) as bad:
        svc.connect_serial("/dev/ttyUSB2", 12345)
    assert bad.value.code == "invalid_request"


def test_serial_device_must_be_a_local_port(svc, monkeypatch):
    """Review fix (v0.5.4): pyserial URLs (network connections, file writes) and plain files are refused
    before anything is opened; local device paths and listed ports pass."""
    opened = []
    svc.serial_opener = lambda device, baud: opened.append(device)
    monkeypatch.setattr(sl, "_comports", lambda: [SimpleNamespace(device="/dev/weird0")])
    view = {"vid": None, "autopilot": False}
    monkeypatch.setattr(sl, "port_view", lambda info: {"device": info.device, **view})
    for device in (
        "socket://10.0.0.5:22",
        "rfc2217://10.0.0.5:2217",
        "spy:///dev/null?file=/tmp/out.txt",
        "loop://",
        "/home/user/.bashrc",
        "/dev/ttyUSB0/../../etc/passwd",
    ):
        with pytest.raises(RelayError) as exc:
            svc.connect_serial(device, 115200)
        assert exc.value.code == "invalid_request", device
    assert opened == []
    for device in ("/dev/ttyACM0", "/dev/cu.usbmodem14101", "/dev/pts/7", "COM12", "\\\\.\\COM3"):
        assert sl.is_port_name(device), device
    assert sl.is_port_name("/dev/weird0")  # listed by the system
    with pytest.raises(sl.SerialUnavailable) as raw:
        sl.open_port("socket://127.0.0.1:9", 115200)
    assert raw.value.cause == "invalid"


def test_unplug_goes_stale_and_replug_reconnects(svc, stepper, board):
    vid = connect(svc, stepper, board)
    v = svc._vehicle(vid)
    link = v.driver.link
    board.up = False  # cable pulled
    assert stepper.until(lambda: v.link_state == "lost", 6.0)
    assert not link.ok and link.error
    assert svc.fleet()["vehicles"][0]["serial"]["open"] is False
    board.up = True  # plugged in again: the same device reappears
    assert stepper.until(lambda: v.link_state == "ok", 4.0)
    assert link.generation == 2 and link.ok
    assert [e["kind"] for e in svc.events.after(0, limit=5000) if e["vehicle"] == vid].count(
        "link_restored"
    ) == 1


def test_remove_closes_the_port(svc, stepper, board):
    vid = connect(svc, stepper, board)
    link = svc._vehicle(vid).driver.link
    svc.remove_vehicle(vid)
    assert link.closed and not link.ok and svc.by_serial == {}
    vid2 = connect(svc, stepper, board)
    assert vid2 == vid


# ---------------------------------------------------------------------------- availability


def test_hitl_unavailable_reasons(svc, stepper, board, clock):
    # a simulator
    svc.add_sims(1, "px4")
    stepper.run(1.0)
    status = svc.hitl_status("sim-1")
    assert status["available"] is False and status["cause"] == "sim"

    # ArduPilot over USB: refused, with the firmware twin recommended
    ardu = FakePx4(clock, autopilot=M.MAV_AUTOPILOT_ARDUPILOTMEGA)
    avid = connect(svc, stepper, ardu, "/dev/ttyACM1")
    status = svc.hitl_status(avid)
    assert status["cause"] == "ardupilot" and "firmware twin" in status["reason"]
    assert "固件孪生" in status["reason_zh"]
    with pytest.raises(RelayError) as refused:
        svc.hitl_action(avid, "start", "HITL", _operator())
    assert refused.value.code == "hitl_unavailable" and refused.value.status == 409
    assert "固件孪生" in refused.value.detail["reason_zh"]

    # PX4 over UDP (a Wi-Fi bridge): refused
    from relay.drivers.mavlink import MavlinkDriver
    from relay.fleet import Vehicle
    from relay.mav.link import UdpLink

    udp = Vehicle("real-10-0-0-9", "real", "Quad 09", MavlinkDriver(UdpLink(0), "10.0.0.9", 14555))
    udp.state["autopilot"] = "PX4"
    svc.vehicles[udp.id] = udp
    assert svc.hitl_status(udp.id)["cause"] == "not_serial"
    del svc.vehicles[udp.id]

    # PX4 over USB: not possessed, then armed, then confirm missing
    vid = connect(svc, stepper, board)
    status = svc.hitl_status(vid)
    HitlStatus.model_validate(status)
    assert status["cause"] == "possession" and status["state"] == "idle" and len(status["steps"]) == 7
    svc.possess(vid, "keyboard")
    stepper.run(0.2)
    board.armed = True
    stepper.run(1.2)
    assert svc.hitl_status(vid)["cause"] == "armed"
    board.armed = False
    stepper.run(1.2)
    status = svc.hitl_status(vid)
    assert status["available"] is True and status["reason"] is None
    with pytest.raises(RelayError) as unconfirmed:
        svc.hitl_action(vid, "start", "hitl", _operator())
    assert unconfirmed.value.code == "confirm_required"
    assert svc.hitl_status(vid)["state"] == "idle"


# ---------------------------------------------------------------------------- the flow


def test_full_flow_start_run_stop_restore_verify(svc, stepper, board, tmp_path):
    svc.hitl_dir = tmp_path
    vid = connect(svc, stepper, board)
    v = svc._vehicle(vid)
    start_hitl(svc, stepper, board, vid)

    status = svc.hitl_status(vid)
    HitlStatus.model_validate(status)
    assert status["changed"] == {
        "SYS_HITL": {"from": 0, "to": 1},
        "SYS_AUTOSTART": {"from": 4001, "to": 1001},
    }
    assert board.flash["SYS_HITL"][0] == 1 and board.flash["SYS_AUTOSTART"][0] == 1001
    assert board.reboots == 1 and board.hil
    steps = {s["key"]: s["state"] for s in status["steps"]}
    assert steps == {
        "confirm": "done",
        "backup": "done",
        "write": "done",
        "reboot": "done",
        "run": "active",
        "restore": "pending",
        "verify": "pending",
    }
    backup = json.loads((tmp_path / status["backup"]["file"]).read_text())
    assert backup["restore_pending"] is True and backup["count"] == len(board.names)
    assert backup["params"]["MPC_THR_HOVER"] == {
        "value": pytest.approx(0.35),
        "type": M.MAV_PARAM_TYPE_REAL32,
    }
    assert backup["params"]["SYS_MC_EST_GROUP"] == {"value": 2.0, "type": M.MAV_PARAM_TYPE_UINT8}
    assert status["backup"]["file"].startswith("usb-ttyACM0-") and status["backup"]["count"] == len(
        board.names
    )

    # running: 250 Hz sensors and 10 Hz GPS reach the board, actuator outputs drive the physics
    sensors0, gps0 = board.hil_sensor, board.hil_gps
    stepper.run(2.0)
    assert 480 <= board.hil_sensor - sensors0 <= 520
    assert 18 <= board.hil_gps - gps0 <= 22
    run = svc.hitl_status(vid)["run"]
    assert run["actuator_frames"] > 50 and 240 <= run["sensor_hz"] <= 260 and 9 <= run["gps_hz"] <= 11
    started = events(svc, "hitl_started")[-1]
    assert started["data"] == {
        "vehicle": vid,
        "family": "px4",
        "version": "1.16.2",
        "backup": status["backup"]["file"],
    }
    assert "PX4 1.16.2" in started["text_en"] and "硬件在环" in started["text_zh"]

    board.armed = True  # the operator armed it: the board's outputs lift the physics
    stepper.run(2.0)
    snap = svc.fleet()
    Fleet.model_validate(snap)
    view = next(x for x in snap["vehicles"] if x["id"] == vid)
    assert view["hitl"]["state"] == "running" and view["hitl"]["engine"] == "hitl"
    assert view["hitl"]["label_zh"] == "硬件在环 PX4 1.16.2"
    assert view["hitl"]["label_en"] == "Hardware in the loop PX4 1.16.2"
    pose = view["hitl"]["pose"]
    assert pose["z"] > 0.3 and view["position"]["z"] == pose["z"]  # the stage shows the physics
    assert view["attitude"]["roll"] == pose["roll"]

    # stop: disarm (HITL mode), write back, reboot, reread, compare
    status = svc.hitl_action(vid, "stop", None, _operator())
    assert status["state"] == "restoring"
    assert stepper.until(lambda: v.hitl.state in ("restored", "failed"), 30.0), v.hitl.view()
    status = svc.hitl_status(vid)
    assert status["state"] == "restored", status
    assert status["mismatches"] == [] and status["error"] is None and status["pending_restore"] is False
    assert {s["key"]: s["state"] for s in status["steps"]}["verify"] == "done"
    assert board.flash["SYS_HITL"][0] == 0 and board.flash["SYS_AUTOSTART"][0] == 4001
    assert not board.hil and board.reboots == 2 and not board.armed
    restored = events(svc, "hitl_restored")[-1]["data"]
    assert restored["restored"] == ["SYS_AUTOSTART", "SYS_HITL"] and restored["mismatches"] == []
    kept = json.loads((tmp_path / status["backup"]["file"]).read_text())
    assert kept["restore_pending"] is False and kept["restored_at"] is not None
    assert svc.hitl_status(vid)["available"] is True  # ready for another run
    stepper.run(0.2)
    assert svc.fleet()["vehicles"][0]["hitl"]["state"] == "restored"
    assert events(svc, "hitl_failed") == []


def test_restore_mismatch_is_reported_and_the_backup_kept(svc, stepper, board, tmp_path):
    svc.hitl_dir = tmp_path
    vid = connect(svc, stepper, board)
    v = svc._vehicle(vid)
    start_hitl(svc, stepper, board, vid)
    # the next boot (the restore's) brings back a different hover throttle and a new flight time
    board.on_boot = {"MPC_THR_HOVER": 0.41, "LND_FLIGHT_T_LO": 1300.0}
    svc.hitl_action(vid, "stop", None, _operator())
    assert stepper.until(lambda: v.hitl.state in ("restored", "failed"), 30.0)
    status = svc.hitl_status(vid)
    assert status["state"] == "restored"
    assert status["mismatches"] == [
        {"name": "MPC_THR_HOVER", "expected": pytest.approx(0.35), "actual": pytest.approx(0.41)}
    ]
    assert status["volatile"] == ["LND_FLIGHT_T_LO"]
    assert status["error"]["code"] == "hitl_restore_mismatch"
    assert "备份文件已保留" in status["error"]["message_zh"]
    assert {s["key"]: s["state"] for s in status["steps"]}["verify"] == "failed"
    data = events(svc, "hitl_restored")[-1]["data"]
    assert data["mismatches"][0]["name"] == "MPC_THR_HOVER"
    kept = json.loads((tmp_path / status["backup"]["file"]).read_text())
    assert kept["mismatches"][0]["name"] == "MPC_THR_HOVER" and kept["restore_pending"] is False


def test_failure_mid_write_enters_restore(svc, stepper, board, tmp_path):
    svc.hitl_dir = tmp_path
    vid = connect(svc, stepper, board)
    v = svc._vehicle(vid)
    svc.possess(vid, "keyboard")
    stepper.run(0.2)
    board.ignore_set = {"SYS_AUTOSTART"}  # the board acknowledges with the old value
    svc.hitl_action(vid, "start", "HITL", _operator())
    assert stepper.until(lambda: v.hitl.state in ("restored", "failed"), 40.0), v.hitl.view()
    status = svc.hitl_status(vid)
    steps = {s["key"]: s["state"] for s in status["steps"]}
    assert steps["write"] == "failed" and steps["restore"] == "done" and steps["verify"] == "done"
    assert steps["run"] == "skipped" and steps["reboot"] == "skipped"
    assert status["state"] == "restored" and status["error"]["code"] == "hitl_failed"
    assert status["error"]["step"] == "write"
    failed = events(svc, "hitl_failed")[-1]["data"]
    assert failed["step"] == "write" and failed["reason_zh"] == "飞控未确认参数写入"
    assert board.flash["SYS_HITL"][0] == 0 and board.flash["SYS_AUTOSTART"][0] == 4001
    assert status["mismatches"] == []
    assert events(svc, "hitl_started") == []


def test_pending_restore_runs_on_the_next_connection(clock, board, tmp_path):
    from conftest import Stepper

    def service():
        s = FleetService(
            data_dir=tmp_path,
            listen=False,
            clock=clock,
            wall=clock.wall,
            history=History(None),
            operator_token="op-token",
        )
        return s, Stepper(s, clock)

    svc, stepper = service()
    vid = connect(svc, stepper, board)
    start_hitl(svc, stepper, board, vid)
    assert (tmp_path / "hitl-backups").is_dir()
    svc.shutdown()  # Relay quits mid-session: the board stays in HITL mode
    assert board.hil and board.flash["SYS_HITL"][0] == 1

    svc2, stepper2 = service()
    try:
        vid2 = connect(svc2, stepper2, board)
        v2 = svc2._vehicle(vid2)
        assert stepper2.until(lambda: v2.hitl is not None, 2.0)
        status = svc2.hitl_status(vid2)
        assert status["pending_restore"] is True and status["state"] in ("restoring", "verifying")
        assert status["available"] is False
        steps = {s["key"]: s["state"] for s in status["steps"]}
        assert steps["backup"] == "skipped" and steps["run"] == "skipped"
        assert stepper2.until(lambda: v2.hitl.state in ("restored", "failed"), 40.0), v2.hitl.view()
        status = svc2.hitl_status(vid2)
        assert status["state"] == "restored" and status["mismatches"] == []
        assert status["pending_restore"] is False and status["available"] is False  # not possessed
        assert board.flash["SYS_HITL"][0] == 0 and board.flash["SYS_AUTOSTART"][0] == 4001 and not board.hil
        files = list((tmp_path / "hitl-backups").glob("*.json"))
        assert len(files) == 1 and json.loads(files[0].read_text())["restore_pending"] is False
        assert hitlmod.pending_backups(tmp_path / "hitl-backups", {"vehicle": vid2}) == []
    finally:
        svc2.shutdown()


def test_link_loss_while_running_restores_after_replug(svc, stepper, board, tmp_path):
    svc.hitl_dir = tmp_path
    vid = connect(svc, stepper, board)
    v = svc._vehicle(vid)
    start_hitl(svc, stepper, board, vid)
    board.up = False  # unplugged mid-run
    assert stepper.until(lambda: v.hitl.state == "restoring", 8.0)
    assert events(svc, "hitl_failed")[-1]["data"]["step"] == "run"
    stepper.run(2.0)
    board.up = True  # plugged in: the board boots, still in HITL mode
    board._boot(clock_now(svc))
    assert stepper.until(lambda: v.hitl.state in ("restored", "failed"), 40.0), v.hitl.view()
    assert v.hitl.state == "restored" and board.flash["SYS_HITL"][0] == 0


def clock_now(svc) -> float:
    return svc.clock()


# ---------------------------------------------------------------------------- API


def test_hitl_api_paths(svc, stepper, board):
    svc.serial_opener = board.opener
    svc.serial_probe_s = 0.2
    with TestClient(create_app(svc, manage_service=False), base_url="http://127.0.0.1") as client:
        assert client.post(f"{API}/vehicles", json={"serial": {"device": DEVICE}}).status_code == 401
        both = {"host": "10.0.0.9", "serial": {"device": DEVICE}}
        assert client.post(f"{API}/vehicles", json=both, headers=OP).status_code == 400
        assert client.post(f"{API}/vehicles", json={}, headers=OP).status_code == 400
        resp = client.post(f"{API}/vehicles", json={"serial": {"device": DEVICE, "baud": "auto"}}, headers=OP)
        assert resp.status_code == 200, resp.text
        vid = resp.json()["id"]
        stepper.run(1.5)
        status = client.get(f"{API}/vehicles/{vid}/hitl")
        assert status.status_code == 200
        HitlStatus.model_validate(status.json())
        assert status.json()["cause"] == "possession"
        start = {"action": "start", "confirm": "HITL"}
        assert client.post(f"{API}/vehicles/{vid}/hitl", json=start).status_code == 401
        refused = client.post(f"{API}/vehicles/{vid}/hitl", json=start, headers=OP)
        assert refused.status_code == 409
        body = refused.json()["error"]
        assert body["code"] == "hitl_unavailable" and body["detail"]["reason_zh"].startswith("须由操作员")
        assert (
            client.post(f"{API}/vehicles/{vid}/hitl", json={"action": "pause"}, headers=OP).status_code == 400
        )
        stop = client.post(f"{API}/vehicles/{vid}/hitl", json={"action": "stop"}, headers=OP)
        assert stop.status_code == 200 and stop.json()["state"] == "idle"
        assert client.get(f"{API}/vehicles/nope/hitl").status_code == 404
        svc.possess(vid, "keyboard")
        stepper.run(0.2)
        unconfirmed = client.post(f"{API}/vehicles/{vid}/hitl", json={"action": "start"}, headers=OP)
        assert unconfirmed.status_code == 400 and unconfirmed.json()["error"]["code"] == "confirm_required"
        busy_port = client.post(
            f"{API}/vehicles", json={"serial": {"device": "/dev/ttyACM9", "baud": 7}}, headers=OP
        )
        assert busy_port.status_code == 400


# ---------------------------------------------------------------------------- encoders and comparison


def test_hil_encoder_round_trips():
    quad = Quad(effects=True)
    quad.s.p[2] = 12.0
    sensors = SensorModel((47.4, 8.5, 488.0), seed=3, noise=0.5)
    enc = hil.HilEncoder(sensors)
    parser = Parser()
    msgs = parser.parse(enc.frame(quad, 1_000_000, 0.004))
    assert [m.get_type() for m in msgs] == ["HIL_SENSOR", "HIL_GPS"]
    sensor, gps = msgs
    assert sensor.fields_updated == hil.FIELDS_UPDATED and sensor.time_usec == 1_000_000
    assert sensor.zacc == pytest.approx(-9.81, abs=0.5)
    assert sensor.pressure_alt == pytest.approx(500.0, abs=3.0)
    assert gps.lat == pytest.approx(47.4e7, abs=200) and gps.alt == pytest.approx(500_000, abs=5000)
    assert gps.fix_type == 3 and gps.satellites_visible > 0
    # no second GPS frame until 100 ms later
    assert [m.get_type() for m in parser.parse(enc.frame(quad, 1_004_000, 0.004))] == ["HIL_SENSOR"]

    out = Encoder(1, 1).encode(
        M.MAVLink_hil_actuator_controls_message(5, [0.2, 1.4, -0.3, math.nan] + [0.0] * 12, 128, 0)
    )
    controls, armed = hil.decode_actuators(Parser().parse(out)[0])
    assert controls == [pytest.approx(0.2), 1.0, 0.0, 0.0] and armed is True


def test_param_bytes_and_comparison():
    for value, ptype in (
        (4001, M.MAV_PARAM_TYPE_INT32),
        (2, M.MAV_PARAM_TYPE_UINT8),
        (-5, M.MAV_PARAM_TYPE_INT32),
    ):
        assert px4_param_decode(px4_param_encode(value, ptype), ptype) == value
    r32 = M.MAV_PARAM_TYPE_REAL32
    i32 = M.MAV_PARAM_TYPE_INT32
    same = hitlmod.same_param
    assert same({"value": 0.35, "type": r32}, {"value": 0.3499999940395355, "type": r32})  # float32 bytes
    assert not same({"value": 0.35, "type": r32}, {"value": 0.3500001, "type": r32})
    assert not same({"value": 1.0, "type": i32}, {"value": 1.0, "type": r32})  # a type change differs
    assert same({"value": 4001.0, "type": i32}, {"value": 4001.0, "type": i32})
    mismatches, volatile = hitlmod.compare(
        {
            "A": {"value": 1.0, "type": i32},
            "B": {"value": 0.5, "type": r32},
            "LND_FLIGHT_T_LO": {"value": 1, "type": i32},
        },
        {
            "A": {"value": 2.0, "type": i32},
            "C": {"value": 3.0, "type": i32},
            "LND_FLIGHT_T_LO": {"value": 9, "type": i32},
        },
    )
    assert mismatches == [
        {"name": "A", "expected": 1, "actual": 2},
        {"name": "B", "expected": 0.5, "actual": None},
        {"name": "C", "expected": None, "actual": 3},
    ]
    assert volatile == ["LND_FLIGHT_T_LO"]


def test_backup_matching_by_usb_serial_number(tmp_path):
    def write(name, **over):
        data = {
            "kind": "relay-hitl-backup",
            "vehicle": "usb-ttyACM0",
            "board": {"serial_number": "ABC", "vid": 0x26AC},
            "captured_at": 10.0,
            "restore_pending": True,
            "params": {},
            **over,
        }
        (tmp_path / name).write_text(json.dumps(data))

    write("a.json")
    write("b.json", captured_at=20.0)
    write("c.json", restore_pending=False)
    write("d.json", board={"serial_number": "XYZ", "vid": 0x26AC})
    write("e.json", vehicle="usb-ttyACM3", board={})
    found = hitlmod.pending_backups(
        tmp_path, {"vehicle": "usb-ttyACM1", "serial_number": "ABC", "vid": 0x26AC}
    )
    assert [p.name for p in found] == ["b.json", "a.json"]  # another port, the same board; newest first
    assert [p.name for p in hitlmod.pending_backups(tmp_path, {"vehicle": "usb-ttyACM3"})] == ["e.json"]


# ---------------------------------------------------------------------------- pyserial over a pseudo-terminal


def test_pyserial_pty_connect_and_unplug():
    pytest.importorskip("serial")
    board = FakePx4(time.monotonic)
    master, slave = os.openpty()
    path = os.ttyname(slave)
    stop = threading.Event()
    thread = threading.Thread(target=serve_fd, args=(board, master, stop.is_set), daemon=True)
    thread.start()
    svc = FleetService(listen=False, history=History(None), operator_token="op-token")
    try:
        svc.serial_probe_s = 1.0
        vid = svc.connect_serial(path, "auto")["id"]
        v = svc._vehicle(vid)
        end = time.monotonic() + 5.0
        while time.monotonic() < end and not (v.link_state == "ok" and v.autopilot == "PX4"):
            svc.step()
            time.sleep(0.005)
        assert v.link_state == "ok" and v.autopilot == "PX4"
        assert v.driver.link.baud == 115200 and v.driver.link.ok
        stop.set()
        thread.join(2.0)
        os.close(master)  # the other end goes away: reads fail, the link closes and keeps trying
        end = time.monotonic() + 3.0
        while time.monotonic() < end and v.driver.link.ok:
            svc.step()
            time.sleep(0.005)
        assert not v.driver.link.ok and v.driver.link.error
    finally:
        stop.set()
        svc.shutdown()
        os.close(slave)
