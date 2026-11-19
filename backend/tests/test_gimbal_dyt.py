"""DYT codec (12 §5.3): the vendor document's byte examples, round trips and the stream parser."""

from __future__ import annotations

import random

import pytest

from relay.gimbal import dyt
from relay.gimbal.dyt import Code


def hexb(text: str) -> bytes:
    return bytes.fromhex(text)


# every command example line of the vendor document (2.2 表1 后的指令示例)
VECTORS = [
    ("rotate right 10 deg/s", dyt.rate(10, 0), "EB 90 24 64 00 00 00 00 00 00 00 00 00 00 00 03"),
    ("rotate down 10 deg/s", dyt.rate(0, -10), "EB 90 24 00 00 9C FF 00 00 00 00 00 00 00 00 3A"),
    ("stop rotation", dyt.stop(), "EB 90 24 00 00 00 00 00 00 00 00 00 00 00 00 9F"),
    ("angle 10 / -10", dyt.frame_angle(10, -10), "EB 90 26 E8 03 18 FC 00 00 00 00 00 00 00 00 A0"),
    ("zoom in", dyt.command(Code.ZOOM_RATE, rate=50), "EB 90 25 00 00 00 00 00 32 00 00 00 00 00 00 D2"),
    ("zoom out", dyt.zoom_rate(-50), "EB 90 25 00 00 00 00 00 CE 00 00 00 00 00 00 6E"),
    ("zoom stop", dyt.zoom_rate(0), "EB 90 25 00 00 00 00 00 00 00 00 00 00 00 00 A0"),
    ("zoom to 5x", dyt.zoom_to(5), "EB 90 5A 32 00 00 00 00 32 00 00 00 00 00 00 39"),
    ("stop tracking", dyt.command(Code.TRACK_STOP), "EB 90 0E 00 00 00 00 00 00 00 00 00 00 00 00 89"),
]


@pytest.mark.parametrize(("name", "frame", "expected"), VECTORS, ids=[v[0] for v in VECTORS])
def test_vendor_examples_match_byte_for_byte(name, frame, expected):
    assert frame == hexb(expected), name
    assert len(frame) == dyt.CMD_LEN


def test_track_point_example_and_its_typo():
    # The document prints "track (100, -200)" as ``... 64 00 37 FF ...`` but -200 is 0xFF38 (0xFF37 = -201);
    # its checksum 55 belongs to the printed bytes, which are the ones decoded here.
    printed = hexb("EB 90 0D 64 00 37 FF 01 32 00 00 00 00 00 00 55")
    assert dyt.command(Code.TRACK_POINT, 100, -201, 1, 0x32) == printed
    (decoded,) = dyt.CommandParser().feed(printed)
    assert (decoded.code, decoded.x, decoded.y, decoded.p3, decoded.rate) == (0x0D, 100, -201, 1, 0x32)
    ours = dyt.track_point(100, -200)
    assert ours[3:7] == bytes([0x64, 0x00, 0x38, 0xFF]) and ours[-1] == dyt.checksum(ours[:-1])


def test_network_wrapper_example():
    data = hexb("eb 90 2b 00 00 00 00 00 00 00 00 00 00 00 00 a6")
    assert dyt.command(Code.CENTER) == data
    assert dyt.wrap(data) == hexb("eb 90 10") + data + hexb("4c")
    assert dyt.WrapParser().feed(dyt.wrap(data)) == [data]


def test_wrap_limits():
    with pytest.raises(ValueError):
        dyt.wrap(b"")
    with pytest.raises(ValueError):
        dyt.wrap(bytes(256))


def test_every_control_code_encoder():
    cases = {
        dyt.lens("ir1"): (Code.IR1, 0),
        dyt.lens("visible1", "ir2"): (Code.VISIBLE1, 4),
        dyt.lens("visible2", "visible2"): (Code.VISIBLE2, 0),  # small == big: no picture in picture
        dyt.earth_angle(-90, 45): (Code.EARTH_ANGLE, 0),
        dyt.scan(30, -10, 10): (Code.SCAN, 50),
    }
    for frame, (code, p3) in cases.items():
        (c,) = dyt.CommandParser().feed(frame)
        assert (c.code, c.p3) == (code, p3)
    (c,) = dyt.CommandParser().feed(dyt.earth_angle(-90, 45))
    assert (c.x, c.y) == (-9000, 4500)
    (c,) = dyt.CommandParser().feed(dyt.command(Code.FOCUS_POSITION, 49152))
    assert c.x_u16 == 49152  # focus positions exceed S16 and travel as U16


def test_geo_command_layout():
    f = dyt.geo_command(dyt.GEO_POINT, 47.397742, 8.545594, 488.0)
    assert f[:3] == bytes([0xEB, 0x90, 0x3A]) and f[3] == 2 and len(f) == 16
    assert int.from_bytes(f[4:8], "little", signed=True) == 473977420
    assert int.from_bytes(f[8:12], "little", signed=True) == 85455940
    assert int.from_bytes(f[12:14], "little", signed=True) == 2440  # 488 m / 0.2
    assert f[-1] == dyt.checksum(f[:-1])
    assert dyt.geo_command(dyt.GEO_EXIT)[3] == 0 and dyt.geo_command(dyt.GEO_CENTER)[3] == 1


def test_carrier_attitude_frame_round_trip():
    c = dyt.CarrierAttitude(
        roll_deg=-3.5, pitch_deg=12.25, yaw_deg=-170.0, lat=47.397742, lon=8.545594, alt_m=488.2,
        rel_alt_m=12.4, utc=1_800_000_000.37, airspeed=3.0, groundspeed=2.5,
    )  # fmt: skip
    f = dyt.carrier_frame(c)
    assert len(f) == 32 and f[:2] == bytes([0xEB, 0x91]) and f[-1] == dyt.checksum(f[:-1])
    (back,) = dyt.CommandParser().feed(f)
    assert back.roll_deg == pytest.approx(-3.5) and back.pitch_deg == pytest.approx(12.25)
    assert back.yaw_deg == pytest.approx(-170.0) and back.lat == pytest.approx(47.397742, abs=1e-7)
    assert back.lon == pytest.approx(8.545594, abs=1e-7) and back.alt_m == pytest.approx(488.2)
    assert back.rel_alt_m == pytest.approx(12.4) and back.utc == pytest.approx(1_800_000_000.37, abs=0.011)
    assert back.airspeed == 3.0 and back.groundspeed == 2.5
    # a heading above 180 degrees travels as the equivalent negative one (north zero, east positive)
    (west,) = dyt.CommandParser().feed(dyt.carrier_frame(dyt.CarrierAttitude(yaw_deg=270.0)))
    assert west.yaw_deg == pytest.approx(-90.0)


def full_telemetry() -> dyt.Telemetry:
    return dyt.Telemetry(
        track_source="ir1", track_algorithm="vehicle", auto_prompt=True, locked=True, enhance=True,
        recording=True, roll_control=True, motors=True, follow=True, lock=False, laser_on=True,
        zoom=409.5, big="ir2", small="visible2", miss_x_deg=-1.25, miss_y_deg=3.5, roll_deg=-2.5,
        pitch_deg=-30.25, yaw_deg=125.5, gate_w_px=96, gate_h_px=64, roll_dps=0.5, pitch_dps=-12.5,
        yaw_dps=99.99, laser_range_m=812.3, selftest_done=True, gyro_fail=True, servo_error=False,
        image_error=True,
    )  # fmt: skip


def test_telemetry_round_trip_all_fields():
    t = full_telemetry()
    frame = dyt.encode_telemetry(t)
    assert len(frame) == dyt.TELEMETRY_LEN and frame[:2] == bytes([0xEE, 0x16])
    assert frame[-1] == dyt.checksum(frame[:-1])
    (back,) = dyt.Parser().feed(frame)
    assert back == t  # exact: every value above sits on its field's resolution


def test_telemetry_twelve_bit_zoom_and_invalid_laser():
    for zoom in (1.0, 25.5, 30.0, 255.9, 256.0, 409.5):
        (t,) = dyt.Parser().feed(dyt.encode_telemetry(dyt.Telemetry(zoom=zoom)))
        assert t.zoom == pytest.approx(zoom)
    frame = dyt.encode_telemetry(dyt.Telemetry(zoom=30.0))
    assert frame[4] == 0x2C and frame[5] & 0x0F == 0x01  # 300 = 0x12C: low byte 2C, high nibble 1
    (t,) = dyt.Parser().feed(dyt.encode_telemetry(dyt.Telemetry(laser_range_m=None)))
    assert t.laser_range_m is None
    (t,) = dyt.Parser().feed(dyt.encode_telemetry(dyt.Telemetry(laser_range_m=0.1)))
    assert t.laser_range_m == pytest.approx(0.1)


def test_target_and_status_frames():
    target = dyt.TargetPosition(47.397742, 8.545594, 450.2, 12.4, (26, 3, 5, 10, 11, 12, 34))
    status = dyt.encode_status(Code.GEO, bytes([2, 0]))
    out = dyt.Parser().feed(dyt.encode_target(target) + status + dyt.encode_status(Code.PHOTO))
    assert isinstance(out[0], dyt.TargetPosition) and out[0].lat == pytest.approx(47.397742, abs=1e-7)
    assert out[0].alt_m == pytest.approx(450.2) and out[0].utc == (26, 3, 5, 10, 11, 12, 34)
    assert isinstance(out[1], dyt.Status) and out[1].code == Code.GEO and out[1].geo_ok is True
    assert dyt.Parser().feed(dyt.encode_status(Code.GEO, bytes([2, 1])))[0].geo_ok is False
    assert out[2].code == Code.PHOTO and out[2].params == b"" and out[2].geo_ok is None


def test_parser_handles_any_chunking():
    t1, t2 = full_telemetry(), dyt.Telemetry(yaw_deg=10.0, zoom=2.0)
    stream = dyt.encode_telemetry(t1) + dyt.encode_status(Code.GEO, bytes([1, 0])) + dyt.encode_telemetry(t2)
    for size in (1, 2, 3, 7, 31, 32, 33, 1000):
        p = dyt.Parser()
        out = []
        for i in range(0, len(stream), size):
            out += p.feed(stream[i : i + size])
        assert [type(o) for o in out] == [dyt.Telemetry, dyt.Status, dyt.Telemetry], size
        assert out[0] == t1 and out[2] == t2 and p.bad == 0 and p.frames == 3


def test_parser_resyncs_after_garbage_and_counts_bad_checksums():
    good = dyt.encode_telemetry(dyt.Telemetry(yaw_deg=5.0))
    corrupt = bytearray(dyt.encode_telemetry(dyt.Telemetry(yaw_deg=6.0)))
    corrupt[12] ^= 0x40
    p = dyt.Parser()
    out = p.feed(b"\x00\x01\xee\xee\x99" + good + bytes(corrupt) + b"\xee" + good)
    assert [round(t.yaw_deg) for t in out] == [5, 5]
    assert p.bad == 1 and p.frames == 2 and p.skipped > 0
    # a lone sync byte at the end of a chunk waits for its partner
    p = dyt.Parser()
    assert p.feed(good[:1]) == [] and len(p.feed(good[1:])) == 1


def test_parser_finds_a_frame_inside_a_corrupt_one():
    inner = dyt.encode_telemetry(dyt.Telemetry(yaw_deg=7.0))
    broken = bytes([0xEE, 0x16, 0x01, 0x02]) + inner  # a frame start whose body swallows a real frame
    p = dyt.Parser()
    out = p.feed(broken + dyt.encode_telemetry(dyt.Telemetry(yaw_deg=8.0)))
    assert [round(t.yaw_deg) for t in out] == [7, 8] and p.bad >= 1


def test_parser_survives_random_noise():
    rng = random.Random(12)
    good = dyt.encode_telemetry(dyt.Telemetry(yaw_deg=1.0))
    p = dyt.Parser()
    got = 0
    for _ in range(200):
        got += len(p.feed(bytes(rng.randrange(256) for _ in range(rng.randrange(1, 40))) + good))
    assert got >= 150  # noise may eat a frame that starts inside it, never more than a few
    assert len(p._buf) < dyt.MAX_PENDING


def test_command_and_wrap_parsers_resync():
    cmds = dyt.CommandParser()
    f = dyt.rate(1, 2)
    out = cmds.feed(b"\x11\xeb\x00" + f[:5])
    out += cmds.feed(f[5:] + bytes(f[:15]) + b"\x00" + dyt.stop())  # one good, one bad checksum, one good
    assert [(c.code, c.x) for c in out] == [(0x24, 10), (0x24, 0)] and cmds.bad == 1
    wrap = dyt.WrapParser()
    block = dyt.wrap(dyt.stop())
    bad = bytearray(block)
    bad[5] ^= 1
    # the data of a wrapped block starts with EB 90 itself, so a corrupt block can look like a longer wrapper:
    # the parser recovers once enough bytes have arrived to disprove it
    out = wrap.feed(b"junk" + bytes(bad) + block + block)
    assert out == [dyt.stop(), dyt.stop()] and wrap.bad >= 1
