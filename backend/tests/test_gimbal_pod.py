"""The real pod path (12 §5.3): the link and ``DytPod`` against the fake pod on loopback TCP, the serial
transport through a stand-in port, and the fleet's attach / command / detach flow end to end."""

from __future__ import annotations

import math
import time

import pytest
from conftest import free_tcp_port
from fake_dyt_pod import FakeDytServer, FakePod

from relay.authority import Actor
from relay.errors import RelayError
from relay.fleet import Vehicle
from relay.gimbal import Carrier, DytPod, dyt
from relay.gimbal import link as linkmod
from relay.gimbal.link import DytLink

OP = Actor.operator()
Code = dyt.Code


@pytest.fixture(autouse=True)
def fast_timeouts(monkeypatch):
    monkeypatch.setattr(linkmod, "LOST_S", 0.3)
    monkeypatch.setattr(linkmod, "CONNECT_GRACE_S", 0.4)
    monkeypatch.setattr(linkmod, "BACKOFF_FIRST_S", 0.05)


@pytest.fixture
def pod_server():
    with FakeDytServer(hz=120.0) as server:
        yield server


def wait(cond, timeout: float = 3.0, tick=None) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if tick is not None:
            tick()
        if cond():
            return True
        time.sleep(0.003)
    return False


def pose(**kw) -> Carrier:
    base = {
        "pos": (1.0, 2.0, 3.0), "roll": 0.1, "pitch": -0.05, "yaw": math.radians(100.0), "lat": 47.4,
        "lon": 8.5, "alt": 520.0, "rel_alt": 3.0, "speed": 1.0, "wall": 1.8e9,
    }  # fmt: skip
    return Carrier(**{**base, **kw})


# ---------------------------------------------------------------------------- the link


def test_link_connects_receives_telemetry_and_sends_wrapped_frames(pod_server):
    link = DytLink("tcp", host="127.0.0.1", port=pod_server.port)
    assert link.state == "connecting" and link.address == f"127.0.0.1:{pod_server.port}"
    link.start()
    try:
        got: list = []
        assert wait(lambda: link.state == "ok" and bool(got.extend(link.poll()) or got))
        assert isinstance(got[0], dyt.Telemetry) and link.connected and link.connects == 1
        assert link.age_s is not None and link.age_s < 0.2
        link.send(dyt.frame_angle(25.0, -10.0))
        assert wait(lambda: any(c.code == Code.FRAME_ANGLE for c in pod_server.received))
        cmd = next(c for c in pod_server.received if c.code == Code.FRAME_ANGLE)
        assert (cmd.x, cmd.y) == (2500, -1000)
        assert wait(lambda: pod_server.pod.t.yaw_deg > 20.0)  # the fake pod turned
    finally:
        link.close()
    assert link._thread is None


def test_link_state_goes_lost_without_telemetry_and_recovers(pod_server):
    link = DytLink("tcp", host="127.0.0.1", port=pod_server.port)
    link.start()
    try:
        assert wait(lambda: link.state == "ok")
        pod_server.pause()
        assert wait(lambda: link.state == "lost")
        pod_server.resume()
        assert wait(lambda: link.state == "ok")
    finally:
        link.close()


def test_link_reconnects_after_the_pod_drops_the_connection(pod_server):
    link = DytLink("tcp", host="127.0.0.1", port=pod_server.port)
    link.start()
    try:
        assert wait(lambda: link.state == "ok")
        pod_server.drop()
        assert wait(lambda: link.connects >= 2 and link.state == "ok", timeout=5.0)
    finally:
        link.close()


def test_link_to_nothing_stays_quiet_then_is_lost_and_closes_fast():
    port = free_tcp_port()  # nothing listens there
    link = DytLink("tcp", host="127.0.0.1", port=port)
    link.start()
    try:
        assert link.state == "connecting"
        assert wait(lambda: link.state == "lost" and link.error is not None)
        assert not link.connected and link.poll() == []
    finally:
        started = time.monotonic()
        link.close()
    assert time.monotonic() - started < 1.0


def test_link_counts_bad_frames_and_resyncs(pod_server):
    link = DytLink("tcp", host="127.0.0.1", port=pod_server.port)
    link.start()
    try:
        assert wait(lambda: link.state == "ok")
        bad = bytearray(dyt.encode_telemetry(dyt.Telemetry()))
        bad[9] ^= 0xFF
        pod_server.garbage(b"\x00\x13" + bytes(bad) + b"\xee")
        assert wait(lambda: link.parser.bad >= 1)
        link.poll()
        assert wait(lambda: any(isinstance(f, dyt.Telemetry) for f in link.poll()))
    finally:
        link.close()


def test_serial_transport_speaks_the_plain_protocol():
    class Port:  # a stand-in for the pyserial port: the pod's side of a UART
        def __init__(self) -> None:
            self.pod = FakePod()
            self.parser = dyt.CommandParser()
            self.opened = 0
            self.closed = False
            self.last = time.monotonic()

        def write(self, data: bytes) -> None:
            for item in self.parser.feed(data):
                if isinstance(item, dyt.Command):
                    self.pod.apply(item)

        def read(self, n: int) -> bytes:
            now = time.monotonic()
            if now - self.last < 0.01:
                return b""
            self.pod.step(now - self.last)
            self.last = now
            return self.pod.telemetry()

        def close(self) -> None:
            self.closed = True

    port = Port()

    def opener(device: str, baud: int):
        assert (device, baud) == ("/dev/ttyUSB9", 115200)
        port.opened += 1
        return port

    link = DytLink("serial", device="/dev/ttyUSB9", serial_opener=opener)
    link.start()
    try:
        assert wait(lambda: link.state == "ok") and link.address == "/dev/ttyUSB9"
        link.send(dyt.rate(20.0, 0.0))
        assert wait(lambda: port.pod.rate == (20.0, 0.0))  # decoded without any wrapper
        assert port.parser.bad == 0
    finally:
        link.close()
    assert port.closed and port.opened == 1


def test_link_rejects_an_unknown_transport():
    with pytest.raises(ValueError):
        DytLink("udp")


# ---------------------------------------------------------------------------- the pod


def drive(pod: DytPod, carrier: Carrier, clock: list[float]):
    def tick() -> None:
        clock[0] += 0.01
        pod.step(clock[0], carrier)

    return tick


def test_pod_view_follows_the_telemetry_and_commands_change_the_pod(pod_server):
    pod = DytPod(DytLink("tcp", host="127.0.0.1", port=pod_server.port))
    clock = [100.0]
    tick = drive(pod, pose(), clock)
    try:
        assert pod.view(clock[0])["link"] == "connecting" and pod.view(clock[0])["kind"] == "dyt"
        assert wait(lambda: pod.view(clock[0])["link"] == "ok", tick=tick)
        v = pod.view(clock[0])
        assert (
            v["address"] == f"127.0.0.1:{pod_server.port}" and v["mode"] == "lock" and v["lens"] == "visible1"
        )
        assert v["fov_deg"] == [60.0, 35.98] and v["zoom_range"] == [1.0, 30.0] and 0.0 <= v["age_s"] < 0.5
        assert v["debug"]["frames"] > 0 and v["debug"]["bad_frames"] == 0
        pod.command("angle", {"yaw_deg": 30.0, "pitch_deg": -20.0}, clock[0])
        assert pod.view(clock[0])["mode"] == "angle"
        assert wait(lambda: abs(pod.view(clock[0])["yaw_deg"] - 30.0) < 0.3, timeout=4.0, tick=tick)
        assert pod.view(clock[0])["pitch_deg"] == pytest.approx(-20.0, abs=0.3)
        pod.command("zoom", {"level": 4.0}, clock[0])
        assert wait(lambda: pod.view(clock[0])["zoom"] == 4.0, tick=tick)
        h = 2 * math.degrees(math.atan(math.tan(math.radians(30)) / 4))
        assert pod.view(clock[0])["fov_deg"][0] == pytest.approx(h, abs=0.01)
        pod.command("lens", {"lens": "ir1", "pip": "visible2"}, clock[0])
        assert wait(lambda: pod.view(clock[0])["lens"] == "ir1", tick=tick)
        v = pod.view(clock[0])
        assert v["pip"] == "visible2" and v["fov_deg"][0] == pytest.approx(
            2 * math.degrees(math.atan(math.tan(math.radians(20)) / 1)), abs=0.01
        )
        assert ("lens", {"lens": "ir1", "pip": "visible2"}) in pod.take_events()
        pod.command("follow", {"on": True}, clock[0])
        assert wait(lambda: pod.view(clock[0])["mode"] == "follow", tick=tick)
        pod.command("laser", {"on": True}, clock[0])
        assert wait(lambda: pod.view(clock[0])["laser"] == {"on": True, "range_m": 123.4}, tick=tick)
        pod.command("motors", {"on": False}, clock[0])
        assert wait(lambda: pod.view(clock[0])["mode"] == "off", tick=tick)
        with pytest.raises(RelayError) as exc:
            pod.command("rate", {"yaw_dps": 5.0}, clock[0])
        assert exc.value.code == "gimbal_busy"
        pod.command("motors", {"on": True}, clock[0])
        assert wait(lambda: pod.view(clock[0])["motors"], tick=tick)  # the telemetry says so
        pod.command("track", {"x": 64, "y": -64, "algorithm": "vehicle"}, clock[0])
        assert wait(lambda: pod.view(clock[0])["tracking"]["state"] == "locked", tick=tick)
        t = pod.view(clock[0])["tracking"]
        assert (
            t["algorithm"] == "vehicle"
            and t["box_px"] == [96, 96]
            and t["x_deg"] == pytest.approx(0.05, abs=0.01)
        )
        pod.command("track_stop", {}, clock[0])
        assert wait(lambda: pod.view(clock[0])["tracking"]["state"] == "off", tick=tick)
        pod.command("palette", {"palette": "pseudo"}, clock[0])
        pod.command("focus", {"mode": "manual", "position": 30000}, clock[0])
        assert pod.view(clock[0])["palette"] == "pseudo" and pod.view(clock[0])["focus"]["position"] == 30000
        expected = {
            Code.FRAME_ANGLE, Code.ZOOM_TO, Code.IR1, Code.FOLLOW_ON, Code.LASER_ON, Code.MOTORS_OFF,
            Code.ALGO_VEHICLE, Code.TRACK_POINT, Code.TRACK_STOP, Code.PALETTE_PSEUDO, Code.FOCUS_MODE,
            Code.FOCUS_POSITION,
        }  # fmt: skip
        assert wait(lambda: expected <= {c.code for c in pod_server.received})
        ir = next(c for c in pod_server.received if c.code == Code.IR1)
        assert ir.p3 == 2  # the small picture: visible2
    finally:
        pod.close()


def test_pod_sends_the_carrier_attitude_at_ten_hertz(pod_server):
    pod = DytPod(DytLink("tcp", host="127.0.0.1", port=pod_server.port))
    clock = [50.0]
    tick = drive(pod, pose(), clock)
    try:
        assert wait(lambda: pod.view(clock[0])["link"] == "ok", tick=tick)
        clock[0] = 50.0
        pod._att_t = -1e9
        for _ in range(100):  # one simulated second at 100 Hz
            tick()
        assert wait(lambda: len(pod_server.attitudes) >= 9, tick=lambda: None)
        n = len(pod_server.attitudes)
        assert 9 <= n <= 12
        a = pod_server.attitudes[-1]
        assert a.roll_deg == pytest.approx(math.degrees(0.1), abs=0.01) and a.pitch_deg == pytest.approx(
            math.degrees(-0.05), abs=0.01
        )
        assert a.yaw_deg == pytest.approx(100.0, abs=0.01) and a.lat == pytest.approx(47.4, abs=1e-6)
        assert (
            a.alt_m == pytest.approx(520.0, abs=0.2)
            and a.rel_alt_m == pytest.approx(3.0, abs=0.2)
            and a.groundspeed == 1.0
        )
    finally:
        pod.close()


def test_pod_reports_link_loss_and_refuses_commands_while_offline(pod_server):
    pod = DytPod(DytLink("tcp", host="127.0.0.1", port=pod_server.port))
    clock = [0.0]
    tick = drive(pod, pose(), clock)
    try:
        assert wait(lambda: pod.view(clock[0])["link"] == "ok", tick=tick)
        pod_server.pause()
        assert wait(lambda: pod.view(clock[0])["link"] == "lost", tick=tick)
        with pytest.raises(RelayError) as exc:
            pod.command("photo", {}, clock[0])
        assert (
            exc.value.code == "gimbal_offline"
            and exc.value.status == 409
            and str(pod_server.port) in exc.value.message_en
        )
        pod_server.resume()
        assert wait(lambda: pod.view(clock[0])["link"] == "ok", tick=tick)
        events = [d["link"] for k, d in pod.take_events() if k == "link"]
        assert events == ["ok", "lost", "ok"]
    finally:
        pod.close()


def test_pod_captures_photos_and_the_recording_the_pod_reports(pod_server):
    pod = DytPod(DytLink("tcp", host="127.0.0.1", port=pod_server.port))
    clock = [0.0]
    tick = drive(pod, pose(frame="flu"), clock)
    try:
        assert wait(lambda: pod.view(clock[0])["link"] == "ok", tick=tick)
        pod.take_events()
        pod.command("photo", {}, clock[0])
        pod.command("record", {"on": True}, clock[0])
        assert pod.view(clock[0])["photos"] == 1
        assert wait(lambda: pod.view(clock[0])["recording"], tick=tick)  # the telemetry confirms it
        pod.command("record", {"on": False}, clock[0])
        assert wait(lambda: not pod.view(clock[0])["recording"], tick=tick)
        assert [c["kind"] for c in pod.captures] == ["photo", "record_start", "record_stop"]
        photo = pod.captures[0]
        assert (
            photo["nav"]["position"] == [1.0, 2.0, 3.0]
            and photo["nav"]["lat"] == 47.4
            and photo["lens"] == "visible1"
        )
        assert [k for k, _ in pod.take_events()] == ["capture", "record", "record"]
        assert wait(lambda: pod_server.pod.photos == 1)
    finally:
        pod.close()


def test_pod_decodes_the_target_position_and_ignores_status_frames(pod_server):
    pod = DytPod(DytLink("tcp", host="127.0.0.1", port=pod_server.port))
    clock = [0.0]
    tick = drive(pod, pose(), clock)
    try:
        assert wait(lambda: pod.view(clock[0])["link"] == "ok", tick=tick)
        assert pod.view(clock[0])["target"] is None
        pod_server.garbage(
            dyt.encode_target(dyt.TargetPosition(47.5, 8.6, 480.0)) + dyt.encode_status(Code.PHOTO)
        )
        assert wait(lambda: pod.view(clock[0])["target"] is not None, tick=tick)
        assert pod.view(clock[0])["target"] == {"lat": 47.5, "lon": 8.6, "alt": 480.0}
        pod.command("geo_track", {"lat": 47.5, "lon": 8.6, "alt": 480.0}, clock[0])
        assert wait(
            lambda: pod.last_status is not None and pod.last_status.geo_ok, tick=tick
        )  # the fake answers 0x3a
        assert pod.view(clock[0])["mode"] == "track"
    finally:
        pod.close()


def test_command_encodings_reach_the_pod_as_documented(pod_server):
    pod = DytPod(DytLink("tcp", host="127.0.0.1", port=pod_server.port))
    clock = [0.0]
    tick = drive(pod, pose(), clock)
    try:
        assert wait(lambda: pod.view(clock[0])["link"] == "ok", tick=tick)
        sent = [
            ("rate", {"yaw_dps": 10.0, "pitch_dps": 0.0}, dyt.rate(10.0, 0.0)),
            ("stop", {}, dyt.stop()),
            ("angle", {"yaw_deg": 10.0, "pitch_deg": -10.0, "space": "earth"}, dyt.earth_angle(10.0, -10.0)),
            ("center", {}, dyt.command(Code.CENTER)),
            ("zoom_rate", {"rate": -50}, dyt.zoom_rate(-50)),
            ("digital_zoom", {"on": True}, dyt.command(Code.DIGITAL_ZOOM_ON)),
            ("scan", {"yaw_deg": 30.0, "pitch_deg": -10.0, "speed_dps": 10.0}, dyt.scan(30.0, -10.0, 10.0)),
            ("scan_stop", {}, dyt.command(Code.SCAN_STOP)),
            ("geo_track", {"mode": "center"}, dyt.geo_command(dyt.GEO_CENTER)),
            ("geo_track", {"mode": "exit"}, dyt.geo_command(dyt.GEO_EXIT)),
            ("lock", {"on": False}, dyt.command(Code.LOCK_OFF)),
            ("enhance", {"on": True}, dyt.command(Code.ENHANCE_ON)),
            ("osd", {"on": False}, dyt.command(Code.OSD_OFF)),
            ("low_light", {"on": True}, dyt.command(Code.LOW_LIGHT_ON)),
        ]
        for action, params, frame in sent:
            before = len(pod_server.received)
            pod.command(action, params, clock[0])
            assert wait(lambda before=before: len(pod_server.received) > before), action
            assert pod_server.received[before].raw == frame, action
    finally:
        pod.close()


# ---------------------------------------------------------------------------- the fleet, end to end


@pytest.fixture
def real(svc):
    """A real-aircraft record (no radio link) whose telemetry the test writes."""
    v = Vehicle("real-1", "real", "Quad 01", object(), host="10.0.0.9", port=14550)
    v.state["attitude"] = {"roll": 0.02, "pitch": 0.03, "yaw": math.radians(250.0)}
    v.state["global"] = {"lat": 22.5, "lon": 114.0, "alt": 60.0, "valid": True}
    v.state["local"] = {"x": 4.0, "y": 5.0, "z": -6.0, "vx": 3.0, "vy": 4.0, "vz": 0.0, "t": 0.0}
    svc.vehicles[v.id] = v
    yield v
    svc._gimbal_drop(v)
    svc.vehicles.pop(v.id, None)  # the shutdown has no driver to stop


def pump(svc, clock, cond, timeout: float = 3.0) -> bool:
    def tick() -> None:
        clock.t += 0.01
        svc._gimbal_step(clock.t)

    return wait(cond, timeout, tick)


def event_kinds(svc):
    return [e["kind"] for e in svc.events.after(0) if e["kind"].startswith("gimbal_")]


def test_fleet_attach_command_lose_and_detach_a_real_pod(svc, clock, real, pod_server):
    out = svc.gimbal_attach("real-1", {"transport": "tcp", "host": "127.0.0.1", "port": pod_server.port}, OP)
    assert out["gimbal"]["kind"] == "dyt" and out["gimbal"]["link"] == "connecting"
    link = svc._gimbals["real-1"].pod.link
    thread = link._thread
    assert pump(svc, clock, lambda: svc.gimbal_view("real-1")["link"] == "ok")
    snapshot = svc._gimbal_view(real, clock.t)  # the block of vehicle.gimbal
    assert snapshot["address"] == f"127.0.0.1:{pod_server.port}"

    # the aircraft's attitude and position go out to the pod
    assert pump(svc, clock, lambda: len(pod_server.attitudes) >= 2)
    a = pod_server.attitudes[-1]
    assert a.yaw_deg == pytest.approx(-110.0, abs=0.01) and a.lat == pytest.approx(22.5, abs=1e-6)
    assert (
        a.alt_m == pytest.approx(60.0, abs=0.2)
        and a.rel_alt_m == pytest.approx(6.0, abs=0.2)
        and a.groundspeed == 5.0
    )

    svc.gimbal_command("real-1", "angle", {"yaw_deg": 45.0, "pitch_deg": -30.0}, OP)
    assert pump(svc, clock, lambda: abs(svc.gimbal_view("real-1")["yaw_deg"] - 45.0) < 0.3, timeout=4.0)
    assert svc.gimbal_view("real-1")["pitch_deg"] == pytest.approx(-30.0, abs=0.3)
    svc.gimbal_command("real-1", "photo", {}, OP)
    svc.gimbal_command("real-1", "record", {"on": True}, OP)
    assert pump(svc, clock, lambda: svc.gimbal_view("real-1")["recording"])
    caps = svc.gimbal_captures("real-1")
    assert caps["total"] == 2 and [c["kind"] for c in caps["captures"]] == ["photo", "record_start"]
    assert caps["captures"][0]["nav"]["position"] == [5.0, 4.0, 6.0]  # NED (4, 5, -6) in ENU

    # the pod falls silent: the link is lost, commands are refused; it speaks again: ok
    pod_server.pause()
    assert pump(svc, clock, lambda: svc.gimbal_view("real-1")["link"] == "lost")
    with pytest.raises(RelayError) as exc:
        svc.gimbal_command("real-1", "photo", {}, OP)
    assert exc.value.code == "gimbal_offline"
    pod_server.resume()
    assert pump(svc, clock, lambda: svc.gimbal_view("real-1")["link"] == "ok")

    kinds = event_kinds(svc)
    assert kinds[0] == "gimbal_attached" and kinds.count("gimbal_link") == 3  # ok, lost, ok
    assert {"gimbal_capture", "gimbal_record"} <= set(kinds)
    links = [e for e in svc.events.after(0) if e["kind"] == "gimbal_link"]
    assert [e["data"]["link"] for e in links] == ["ok", "lost", "ok"]
    assert (
        links[1]["text_en"] == "Quad 01 gimbal pod link lost."
        and links[1]["text_zh"] == "Quad 01 云台吊舱链路已丢失。"
    )
    attached = next(e for e in svc.events.after(0) if e["kind"] == "gimbal_attached")
    assert f"127.0.0.1:{pod_server.port}" in attached["text_en"] and "DYT" in attached["text_zh"]

    svc.gimbal_detach("real-1", OP)
    thread.join(2.0)
    assert not thread.is_alive() and svc.vehicles["real-1"].id not in svc._gimbals
    assert event_kinds(svc)[-1] == "gimbal_detached"
    with pytest.raises(RelayError) as exc:
        svc.gimbal_view("real-1")
    assert exc.value.code == "gimbal_missing"


def test_fleet_attach_to_a_pod_that_is_not_there_and_replace_it(svc, clock, real, pod_server):
    dead = free_tcp_port()
    svc.gimbal_attach("real-1", {"transport": "tcp", "host": "127.0.0.1", "port": dead}, OP)
    first = svc._gimbals["real-1"].pod.link._thread
    assert pump(svc, clock, lambda: svc.gimbal_view("real-1")["link"] == "lost")
    with pytest.raises(RelayError) as exc:
        svc.gimbal_command("real-1", "photo", {}, OP)
    assert exc.value.code == "gimbal_offline"
    assert "gimbal_link" not in event_kinds(svc)  # never connected: lost without a link event
    svc.gimbal_attach(
        "real-1", {"transport": "tcp", "host": "127.0.0.1", "port": pod_server.port}, OP
    )  # replaces
    first.join(2.0)
    assert not first.is_alive()
    assert pump(svc, clock, lambda: svc.gimbal_view("real-1")["link"] == "ok")
    assert event_kinds(svc).count("gimbal_attached") == 2


def test_fleet_serial_attach_uses_the_serial_opener(svc, clock, real):
    class Port:
        def __init__(self) -> None:
            self.pod, self.parser, self.last = FakePod(), dyt.CommandParser(), time.monotonic()

        def write(self, data: bytes) -> None:
            for item in self.parser.feed(data):
                if isinstance(item, dyt.Command):
                    self.pod.apply(item)

        def read(self, n: int) -> bytes:
            now = time.monotonic()
            if now - self.last < 0.01:
                return b""
            self.pod.step(now - self.last)
            self.last = now
            return self.pod.telemetry()

        def close(self) -> None:
            pass

    port = Port()
    svc.serial_opener = lambda device, baud: port
    out = svc.gimbal_attach("real-1", {"transport": "serial", "device": "/dev/ttyUSB3", "baud": 115200}, OP)
    assert out["gimbal"]["address"] == "/dev/ttyUSB3"
    assert pump(svc, clock, lambda: svc.gimbal_view("real-1")["link"] == "ok")
    svc.gimbal_command("real-1", "rate", {"yaw_dps": 12.0, "pitch_dps": -3.0}, OP)
    assert pump(svc, clock, lambda: port.pod.rate == (12.0, -3.0))
    assert port.parser.bad == 0  # plain frames only: the rate command and the carrier attitude both decode
    svc.gimbal_detach("real-1", OP)


def test_shutdown_closes_every_link(svc, clock, real, pod_server):
    svc.gimbal_attach("real-1", {"transport": "tcp", "host": "127.0.0.1", "port": pod_server.port}, OP)
    thread = svc._gimbals["real-1"].pod.link._thread
    assert pump(svc, clock, lambda: svc.gimbal_view("real-1")["link"] == "ok")
    svc._gimbal_shutdown()
    thread.join(2.0)
    assert not thread.is_alive() and svc._gimbals == {}
