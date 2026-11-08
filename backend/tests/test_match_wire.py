"""Wire protocol version 2 (07 §4, WP-B): COLLIDE, MATCH and the STATE statistics block, interop with
version 1 (Relay 0.4) terminals, bounds and fuzzing, and the peer
table's version and statistics bookkeeping."""

import random
import struct

import pytest

from relay.peers import wire
from relay.peers.service import PeerService


def aircraft(index=1, p=(1.0, 2.0, 0.5)):
    return wire.Aircraft(index, wire.ARMED, 80, p, (0.5, -0.25, 0.0), (0.1, -0.05, 1.2), 0.5, 20)


def stats(n=2, match=0xC0FFEE, running=True):
    records = tuple(
        wire.Stat(i + 1, "program", f"Rammer r{i + 1}", 3, 7, 20, 1, 2, 1, 140, 12_500, 40_000)
        for i in range(n)
    )
    return wire.Stats(match, running, records)


def state(n=2, shots=1, with_stats=True):
    shot = wire.Shot(1, 7, 1234, (0.0, -10.0, 1.0), (0.0, 35.0, 0.0))
    return wire.State(
        "Alpha",
        3,
        12,
        4,
        1,
        2,
        tuple(aircraft(i + 1) for i in range(n)),
        (shot,) * shots,
        stats(n) if with_stats else None,
    )


def collide(**over):
    base = dict(
        to_instance=0xB2,
        contact=9,
        target=2,
        own=1,
        flags=wire.COLLIDE_CRASHED,
        t_ms=480,
        point=(1.0, -9.0, 1.2),
        v=(0.0, 3.0, 0.0),
        closing=2.6,
    )
    base.update(over)
    return wire.Collide(**base)


def match(**over):
    base = dict(match=0x0A0B0C0D, action=wire.MATCH_START, duration_s=180, elapsed_ms=2500, origin=0xA1)
    base.update(over)
    return wire.Match(**base)


def v2_packets() -> list[bytes]:
    return [
        wire.encode_state(1, 0xA1, 500, state()),
        wire.encode_collide(2, 0xA1, 501, collide()),
        wire.encode_match(3, 0xA1, 502, match()),
    ]


def _expect_error(data: bytes) -> None:
    with pytest.raises(wire.WireError):
        wire.decode(data)


def v1_decode(data: bytes) -> wire.Packet:
    """What a Relay 0.4 terminal accepts: version 1 only, the six version 1 types, exact lengths."""
    if len(data) < wire.HEADER_SIZE or data[4] != wire.LEGACY_VERSION:
        raise wire.WireError("magic")
    packet = wire.decode(data)
    if packet.type not in (wire.STATE, wire.SHOT, wire.HIT, wire.BYE, wire.PING, wire.PONG):
        raise wire.WireError("type")
    return packet


# ---------------------------------------------------------------------------- codec


def test_version_2_types_round_trip():
    st, co, ma = (wire.decode(p) for p in v2_packets())
    assert st.header.version == co.header.version == ma.header.version == wire.VERSION == 2
    body = st.body
    assert body.callsign == "Alpha" and len(body.aircraft) == 2 and body.shots
    assert body.stats == stats(2)
    r = body.stats.records[0]
    assert (r.index, r.kind, r.name, r.downs, r.hits, r.shots, r.rams) == (
        1,
        "program",
        "Rammer r1",
        3,
        7,
        20,
        1,
    )
    assert (r.deaths, r.crashes, r.damage, r.alive_ms, r.best_alive_ms) == (2, 1, 140, 12_500, 40_000)
    c = co.body
    assert c.to_instance == 0xB2 and (c.target, c.own) == (2, 1) and c.crashed and not c.reply
    assert c.point == pytest.approx((1.0, -9.0, 1.2)) and c.closing == pytest.approx(2.6)
    assert ma.body == match()
    longest = wire.encode_state(1, 1, 0, state(8, 4))
    assert len(longest) == 24 + 27 + 8 * 32 + 4 * 33 + 6 + 8 * 56 <= wire.MAX_PACKET


def test_legacy_packets_keep_version_1_byte_for_byte():
    plain = wire.encode_state(1, 0xA1, 500, state(with_stats=False))
    assert plain[4] == 1 and wire.decode(plain).body.stats is None
    for data in (
        wire.encode_shot(2, 0xA1, 501, wire.Shot(2, 9, 500, (1.0, 2.0, 3.0), (0.0, 30.0, 1.0))),
        wire.encode_hit(3, 0xA1, 502, wire.Hit(0xB2, 9, 1, 20, False, (0.0, 0.0, 1.0))),
        wire.encode_ping(4, 0xA1, 503),
        wire.encode_pong(5, 0xA1, 504, wire.Pong(0xB2, 400, 3)),
        wire.encode_bye(6, 0xA1, 505),
    ):
        assert data[4] == 1 and data[6:8] == b"\0\0"
        v1_decode(data)  # a 0.4 terminal still reads it
    v1_decode(plain)


def test_a_v04_terminal_drops_only_the_version_2_packets():
    for data in v2_packets():
        with pytest.raises(wire.WireError):
            v1_decode(data)
    # the same STATE without its block is what a 0.4 terminal sees 19 times a second
    assert v1_decode(wire.encode_state(1, 1, 0, state(with_stats=False))).body.aircraft


def test_version_2_framing_is_only_for_its_types():
    for data in (
        wire.encode_ping(1, 1, 0),
        wire.encode_shot(1, 1, 0, wire.Shot(1, 1, 0, (0, 0, 1), (0, 35, 0))),
    ):
        raw = bytearray(data)
        raw[4] = 2
        _expect_error(bytes(raw))  # SHOT, PING ... never carry version 2
    for data in v2_packets()[1:]:
        raw = bytearray(data)
        raw[4] = 1
        _expect_error(bytes(raw))  # COLLIDE and MATCH never carry version 1
    raw = bytearray(wire.encode_state(1, 1, 0, state(with_stats=False)))
    raw[4] = 2
    _expect_error(bytes(raw))  # a version 2 STATE ends with its block
    raw = bytearray(v2_packets()[0])
    raw[4] = 3
    _expect_error(bytes(raw))


def test_truncated_and_extended_version_2_packets_are_rejected():
    for good in v2_packets():
        wire.decode(good)
        for cut in range(len(good)):
            _expect_error(good[:cut])
        _expect_error(good + b"\0")


def _stat_offset(n_air=2, n_shot=1):
    return 24 + 27 + n_air * 32 + n_shot * 33


def test_bounds_of_the_statistics_block():
    good = wire.encode_state(1, 1, 0, state())
    off = _stat_offset()
    for offset, value in (
        (off + 4, 0x02),  # unknown match flag
        (off + 5, 9),  # more than 8 records
        (off + 6, 0),  # record index 0
        (off + 6, 9),  # record index 9
        (off + 7, 4),  # unknown controller kind
    ):
        raw = bytearray(good)
        raw[offset] = value
        _expect_error(bytes(raw))
    raw = bytearray(good)
    struct.pack_into(">I", raw, off + 6 + 48, wire.MAX_ALIVE_MS + 1)  # alive time above one day
    _expect_error(bytes(raw))
    raw = bytearray(good)
    raw[off + 6 + 56] = 1  # the second record repeats index 1
    _expect_error(bytes(raw))


def test_the_controller_name_is_plain_text_and_bounded():
    s = wire.Stats(0, False, (wire.Stat(1, "agent", "evil\x1b[31m‮" + "x" * 60),))
    data = wire.encode_state(1, 1, 0, wire.State("x", 0, None, 0, 0, 0, (aircraft(1),), (), s))
    name = wire.decode(data).body.stats.records[0].name
    assert "\x1b" not in name and "‮" not in name and len(name.encode("utf-8")) <= 32
    long_cjk = wire.clean_text("对局" * 30)
    assert len(long_cjk.encode("utf-8")) <= 32


def test_bounds_of_collide_and_match():
    for bad in (collide(to_instance=0), collide(flags=0x04)):
        _expect_error(wire.encode_collide(1, 1, 0, bad))
    raw = bytearray(wire.encode_collide(1, 1, 0, collide()))
    raw[24 + 12] = 9  # target index 9
    _expect_error(bytes(raw))
    raw = bytearray(wire.encode_collide(1, 1, 0, collide()))
    struct.pack_into(">f", raw, 24 + 19, float("nan"))  # point x
    _expect_error(bytes(raw))
    raw = bytearray(wire.encode_collide(1, 1, 0, collide()))
    struct.pack_into(">f", raw, 24 + 43, -1.0)  # negative closing speed
    _expect_error(bytes(raw))
    for bad in (
        match(match=0),
        match(origin=0),
        match(action=3),
        match(duration_s=10),
        match(duration_s=4000),
        match(elapsed_ms=(180 + 61) * 1000),
    ):
        _expect_error(wire.encode_match(1, 1, 0, bad))


def test_fuzz_flipped_version_2_bytes_decode_or_fail_cleanly():
    rng = random.Random(23)
    for good in v2_packets():
        for _ in range(400):
            data = bytearray(good)
            for _ in range(rng.randint(1, 4)):
                data[rng.randrange(len(data))] = rng.getrandbits(8)
            try:
                p = wire.decode(bytes(data))
            except wire.WireError:
                continue
            if isinstance(p.body, wire.State) and p.body.stats is not None:
                for r in p.body.stats.records:
                    assert 1 <= r.index <= 8 and r.kind in wire.CONTROLLER_KINDS
                    assert r.alive_ms <= wire.MAX_ALIVE_MS and len(r.name.encode("utf-8")) <= 32
            if isinstance(p.body, wire.Collide):
                assert 0 <= p.body.closing <= wire.MAX_SPEED


# ---------------------------------------------------------------------------- peer table


def test_a_peer_is_version_2_from_its_first_version_2_packet():
    now = [100.0]
    svc = PeerService(0xB2, clock=lambda: now[0])
    svc.receive(wire.encode_state(1, 0xA1, 500, state(with_stats=False)), "10.0.0.5", now[0])
    out = svc.tick(now[0])
    peer = svc.peers[0xA1]
    assert out.joined == [peer] and peer.version == 1 and peer.stats is None and not out.stats
    svc.receive(wire.encode_state(2, 0xA1, 550, state()), "10.0.0.5", now[0])
    out = svc.tick(now[0])
    assert peer.version == 2 and peer.stats == stats(2) and out.stats == [peer]
    svc.receive(wire.encode_state(3, 0xA1, 600, state(with_stats=False)), "10.0.0.5", now[0])
    out = svc.tick(now[0])
    assert peer.stats == stats(2) and not out.stats  # a plain STATE keeps the last block


def test_collide_and_match_reach_the_arena_only_from_joined_peers():
    now = [100.0]
    svc = PeerService(0xB2, clock=lambda: now[0])
    svc.receive(wire.encode_collide(1, 0xA1, 500, collide()), "10.0.0.5", now[0])
    svc.receive(wire.encode_match(2, 0xA1, 500, match()), "10.0.0.5", now[0])
    out = svc.tick(now[0])
    assert not out.collides and not out.matches and svc.counters["unknown"] == 2
    svc.receive(wire.encode_state(3, 0xA1, 500, state()), "10.0.0.5", now[0])
    svc.receive(wire.encode_collide(4, 0xA1, 600, collide(t_ms=500)), "10.0.0.5", now[0])
    svc.receive(wire.encode_collide(5, 0xA1, 600, collide(to_instance=0xC3)), "10.0.0.5", now[0])
    svc.receive(wire.encode_match(6, 0xA1, 600, match()), "10.0.0.5", now[0])
    out = svc.tick(now[0] + 0.05)
    assert len(out.collides) == 1 and len(out.matches) == 1  # the COLLIDE for another terminal is ignored
    _peer, c, age = out.collides[0]
    assert c.contact == 9 and age == pytest.approx(0.1 + 0.05)  # 100 ms before the send, 50 ms queued
    # an event "after" its own packet is far too old (a forged clock)
    svc.receive(wire.encode_collide(7, 0xA1, 600, collide(t_ms=700)), "10.0.0.5", now[0])
    out = svc.tick(now[0])
    assert out.collides[0][2] > 1000


def test_a_forged_instance_from_another_address_is_dropped():
    now = [100.0]
    svc = PeerService(0xB2, clock=lambda: now[0])
    svc.receive(wire.encode_state(1, 0xA1, 500, state()), "10.0.0.5", now[0])
    svc.tick(now[0])
    svc.receive(wire.encode_collide(2, 0xA1, 500, collide()), "10.0.0.66", now[0])
    out = svc.tick(now[0])
    assert not out.collides and svc.counters["ip_mismatch"] >= 1
