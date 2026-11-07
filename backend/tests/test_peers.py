"""LAN arena building blocks (05 §7.2-§7.4): wire codec and fuzzing, seat frames, ballistics, peers."""

import json
import math
import random
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from relay.peers import arena, ballistics, wire
from relay.peers.game import load_instance
from relay.peers.service import (
    GLOBAL_RATE_PER_S,
    HOST_INSTANCES,
    IP_RATE_PER_S,
    MAX_PEERS,
    RATE_PER_S,
    REMOVE_S,
    STALE_S,
    PeerService,
)

REPO = Path(__file__).resolve().parents[2]
VECTORS = REPO / "docs" / "design" / "vectors"


def aircraft(index=1, p=(1.0, 2.0, 0.5)):
    return wire.Aircraft(index, wire.ARMED, 80, p, (0.5, -0.25, 0.0), (0.1, -0.05, 1.2), 0.5, 20)


def state(n=2, shots=1):
    shot = wire.Shot(1, 7, 1234, (0.0, -10.0, 1.0), (0.0, 35.0, 0.0))
    return wire.State("Alpha", 3, 12, 4, 1, 2, tuple(aircraft(i + 1) for i in range(n)), (shot,) * shots)


def packets() -> list[bytes]:
    return [
        wire.encode_state(1, 0xA1, 500, state()),
        wire.encode_shot(2, 0xA1, 501, wire.Shot(2, 9, 500, (1.0, 2.0, 3.0), (0.0, 30.0, 1.0))),
        wire.encode_hit(3, 0xA1, 502, wire.Hit(0xB2, 9, 1, 20, False, (0.0, 0.0, 1.0))),
        wire.encode_ping(4, 0xA1, 503),
        wire.encode_pong(5, 0xA1, 504, wire.Pong(0xB2, 400, 3)),
        wire.encode_bye(6, 0xA1, 505),
    ]


# ---------------------------------------------------------------------------- wire


def test_header_layout_is_exactly_as_specified():
    data = wire.encode_ping(0x01020304, 0x1122334455667788, 0x0A0B0C0D)
    assert data[:4] == b"ANRA" and data[4] == 1 and data[5] == wire.PING
    assert data[6:8] == b"\0\0" and data[8:12] == bytes([1, 2, 3, 4])
    assert data[12:20] == bytes.fromhex("1122334455667788") and data[20:24] == bytes.fromhex("0a0b0c0d")
    assert len(data) == wire.HEADER_SIZE == 24
    assert wire.AIRCRAFT_SIZE == 32 and wire.SHOT_SIZE == 33


def test_every_type_round_trips():
    st, shot, hit, ping, pong, bye = (wire.decode(p) for p in packets())
    assert st.type == wire.STATE and st.header.seq == 1 and st.header.instance == 0xA1
    body = st.body
    assert body.callsign == "Alpha" and body.seat == 3 and body.rtt_ms == 12
    assert (body.hits, body.downs, body.deaths) == (4, 1, 2)
    a = body.aircraft[0]
    assert a.index == 1 and a.armed and not a.down and a.hp == 80 and a.ammo == 20
    assert a.p == pytest.approx((1.0, 2.0, 0.5)) and a.v == pytest.approx((0.5, -0.25, 0.0))
    assert a.att == pytest.approx((0.1, -0.05, 1.2), abs=2e-4) and a.throttle == pytest.approx(0.5, abs=0.003)
    assert body.shots[0].bullet == 7 and body.shots[0].v == pytest.approx((0.0, 35.0, 0.0))
    assert shot.body.shooter == 2 and shot.body.p == pytest.approx((1.0, 2.0, 3.0))
    assert hit.body.shooter_instance == 0xB2 and hit.body.target == 1 and hit.body.damage == 20
    assert ping.body is None and bye.body is None
    assert pong.body == wire.Pong(0xB2, 400, 3)
    longest = wire.encode_state(1, 1, 0, state(8, 4))
    assert len(longest) == 24 + 27 + 8 * 32 + 4 * 33 <= wire.MAX_PACKET


def test_callsign_is_cleaned_and_bounded():
    assert wire.clean_callsign("Al\x00pha\x1b[31m‮") == "Alpha[31m"
    assert wire.clean_callsign("  a\tb\nc  ") == "a b c"
    assert len(wire.clean_callsign("x" * 40)) == 16
    long_cjk = wire.clean_callsign("联机" * 10)
    assert len(long_cjk.encode("utf-8")) <= 16
    raw = bytearray(wire.encode_state(1, 1, 0, state(0, 0)))
    raw[24:40] = b"\x07bad\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    assert wire.decode(bytes(raw)).body.callsign == "bad"


def _expect_error(data: bytes) -> None:
    with pytest.raises(wire.WireError):
        wire.decode(data)


def test_fuzz_random_bytes_never_raise_anything_but_wire_error():
    rng = random.Random(7)
    for _ in range(4000):
        size = rng.choice([0, 1, 11, 23, 24, 25, 51, 83, 200, 1199, 1200, 1201, 4000])
        data = bytes(rng.getrandbits(8) for _ in range(size))
        if rng.random() < 0.5 and size >= 6:  # a valid prefix gets further into the parser
            data = b"ANRA\x01" + bytes([rng.randint(0, 8)]) + data[6:]
        try:
            wire.decode(data)
        except wire.WireError:
            pass


def test_fuzz_truncated_and_extended_packets_are_rejected():
    for good in packets():
        wire.decode(good)
        for cut in range(len(good)):
            _expect_error(good[:cut])
        _expect_error(good + b"\0")
    _expect_error(b"\0" * (wire.MAX_PACKET + 1))
    _expect_error(wire.encode_state(1, 1, 0, state()) + b"\0" * 1200)


def test_fuzz_flipped_bytes_decode_or_fail_cleanly():
    rng = random.Random(11)
    for good in packets():
        for _ in range(300):
            data = bytearray(good)
            for _ in range(rng.randint(1, 4)):
                data[rng.randrange(len(data))] = rng.getrandbits(8)
            try:
                p = wire.decode(bytes(data))
            except wire.WireError:
                continue
            if isinstance(p.body, wire.State):
                for a in p.body.aircraft:
                    assert all(math.isfinite(x) and abs(x) <= wire.MAX_COORD for x in a.p)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, 1000.5, -5e6, 3.4e38])
def test_non_finite_and_huge_coordinates_are_rejected(bad):
    raw = bytearray(wire.encode_state(1, 1, 0, state(1, 0)))
    struct.pack_into(">f", raw, 24 + 27 + 4, bad)  # x of the first aircraft
    _expect_error(bytes(raw))
    shot = bytearray(wire.encode_shot(1, 1, 0, wire.Shot(1, 1, 0, (0, 0, 1), (0, 35, 0))))
    struct.pack_into(">f", shot, 24 + 9 + 12 + 4, bad)  # vy
    if abs(bad) > wire.MAX_SPEED or not math.isfinite(bad):
        _expect_error(bytes(shot))
    hit = bytearray(wire.encode_hit(1, 1, 0, wire.Hit(2, 1, 1, 20, False, (0, 0, 1))))
    struct.pack_into(">f", hit, 24 + 15, bad)
    _expect_error(bytes(hit))


def test_bounds_on_counts_indices_and_header():
    good = wire.encode_state(1, 1, 0, state(1, 0))
    for offset, value in (
        (24 + 25, 9),  # aircraft count above 8
        (24 + 26, 5),  # shot count above 4
        (24 + 16, 12),  # seat 12
        (24 + 27, 0),  # aircraft index 0
        (24 + 27, 9),  # aircraft index 9
        (24 + 27 + 2, 101),  # hit points above 100
        (4, 2),  # version 2
        (5, 99),  # unknown type
    ):
        raw = bytearray(good)
        raw[offset] = value
        _expect_error(bytes(raw))
    _expect_error(b"ANRB" + good[4:])
    _expect_error(good[:12] + b"\0" * 8 + good[20:])  # instance 0
    dup = wire.State("x", 0, None, 0, 0, 0, (aircraft(1), aircraft(1)))
    _expect_error(wire.encode_state(1, 1, 0, dup))
    hit = bytearray(wire.encode_hit(1, 1, 0, wire.Hit(2, 1, 1, 20, False, (0, 0, 1))))
    hit[24 + 13] = 200  # damage
    _expect_error(bytes(hit))


def test_encoder_clamps_what_it_cannot_carry():
    a = wire.Aircraft(1, 0, 250, (5000.0, -5000.0, 0.0), (900.0, 0.0, math.nan), (0.0, 0.0, 0.0), 2.0, 999)
    body = wire.decode(wire.encode_state(1, 1, 0, wire.State("x", None, None, 0, 0, 0, (a,)))).body
    b = body.aircraft[0]
    assert b.hp == 100 and b.p[:2] == (1000.0, -1000.0) and b.ammo == 255 and b.throttle == 1.0
    assert b.v[0] == pytest.approx(327.67) and b.v[2] == 0.0 and body.seat is None and body.rtt_ms is None


# ---------------------------------------------------------------------------- seats and frames


def test_seat_vectors_match_the_shared_file():
    data = json.loads((VECTORS / "arena.json").read_text("utf-8"))
    assert data["radius"] == arena.RADIUS
    for s in data["seats"]:
        assert arena.seat_angle(s["seat"]) == pytest.approx(s["phi"], abs=1e-9)
        assert arena.seat_origin(s["seat"]) == pytest.approx(tuple(s["origin"]), abs=1e-9)
    for c in data["cases"]:
        assert arena.peer_to_local(c["p_peer"], c["seat_peer"], c["seat_me"]) == pytest.approx(
            tuple(c["p_local"]), abs=1e-9
        )
        assert arena.to_arena(c["seat_peer"], c["p_peer"]) == pytest.approx(tuple(c["p_arena"]), abs=1e-9)
        assert arena.peer_vec_to_local(c["v_peer"], c["seat_peer"], c["seat_me"]) == pytest.approx(
            tuple(c["v_local"]), abs=1e-9
        )
        assert arena.peer_heading_to_local(c["heading_peer"], c["seat_peer"], c["seat_me"]) == pytest.approx(
            c["heading_local"], abs=1e-9
        )
    for f in data["free_seat"]:
        assert arena.free_seat(f["taken"], f["after"]) == f["seat"]


def test_vector_files_are_current():
    result = subprocess.run(
        [sys.executable, str(REPO / "backend" / "tools" / "arena_vectors.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_every_seat_faces_the_centre_and_round_trips():
    for k in range(arena.SEATS):
        tx, ty = arena.seat_origin(k)
        assert math.hypot(tx, ty) == pytest.approx(10.0)
        ahead = arena.to_arena(k, (0.0, 1.0, 0.0))
        towards = (ahead[0] - tx, ahead[1] - ty)
        assert towards[0] * -tx + towards[1] * -ty == pytest.approx(10.0)  # local +y points at the centre
        p = (1.5, -0.5, 2.0)
        assert arena.from_arena(k, arena.to_arena(k, p)) == pytest.approx(p)
        # a nose pointing at +y locally (heading 0) points at the centre in the arena as well
        h = arena.heading_to_arena(k, 0.0)
        assert (math.sin(h), math.cos(h)) == pytest.approx((-tx / 10.0, -ty / 10.0), abs=1e-9)
    assert arena.peer_to_local((1.0, 2.0, 3.0), 4, 4) == pytest.approx((1.0, 2.0, 3.0))
    assert arena.free_seat(range(12)) is None and arena.free_seat([0, 1], after=0) == 2
    assert arena.keeps_seat(5, 9) and not arena.keeps_seat(9, 5)


# ---------------------------------------------------------------------------- ballistics


def test_ballistics_vectors_match_the_shared_file():
    data = json.loads((VECTORS / "ballistics.json").read_text("utf-8"))
    c = data["constants"]
    assert c["dt"] == ballistics.DT and c["k"] == ballistics.K and c["g"] == ballistics.G
    assert c["ttl_steps"] == ballistics.TTL_STEPS == round(ballistics.TTL_S / ballistics.DT)
    for case in data["cases"]:
        for sample in case["samples"]:
            p, v = ballistics.advance(case["p0"], case["v0"], sample["steps"])
            assert p == pytest.approx(tuple(sample["p"]), abs=1e-9)
            assert v == pytest.approx(tuple(sample["v"]), abs=1e-9)
    for m in data["muzzle"]:
        assert ballistics.muzzle(m["heading"], m["pitch"], m["aircraft_v"]) == pytest.approx(tuple(m["v"]))
    for s in data["sweep"]:
        t = ballistics.sweep(s["p0"], s["p1"], s["c0"], s["c1"], c["hit_radius"])
        assert (t is None) == (s["t"] is None) and (t is None or t == pytest.approx(s["t"]))


def test_drag_and_gravity_shape_the_flight():
    p, v = ballistics.advance((0, 0, 1), (0, 35, 0), 240)
    assert 25 < p[1] < 35 and v[1] < 35 * 0.7 and p[2] < 1 - 4.0  # one second: slowed and falling
    assert ballistics.muzzle(math.pi / 2, 0.0, (0, 0, 0)) == pytest.approx((35.0, 0.0, 0.0))
    assert ballistics.muzzle(0.0, math.radians(-10), (1, 0, 0))[2] < 0  # nose down shoots down


def test_sweep_does_not_tunnel_and_follows_the_target():
    # 35 m/s over one step crosses 14.6 cm, more than the sphere's diameter is not needed: a grazing pass hits
    assert ballistics.sweep((0, -0.07, 1), (0, 0.075, 1), (0.1, 0, 1), (0.1, 0, 1), 0.12) is not None
    assert ballistics.sweep((0, -0.07, 1), (0, 0.075, 1), (0.13, 0, 1), (0.13, 0, 1), 0.12) is None
    # the target moves into the path during the step
    assert ballistics.sweep((0, 0, 1), (0, 0.1, 1), (0.3, 0.05, 1), (0.05, 0.05, 1), 0.12) is not None
    dv = ballistics.hit_impulse((0, 40, 0), (0, 0, 0))
    assert math.hypot(*dv) == pytest.approx(ballistics.KICK_MAX)
    assert ballistics.hit_impulse((0, 2, 0), (0, 0, 0)) == pytest.approx((0, 0.5, 0))
    roll, pitch = ballistics.hit_kick((0.05, 0.0, 0.0), 0.0, 3)  # right side, nose north
    assert roll < 0 and pitch == pytest.approx(0.0, abs=1e-12)
    assert math.radians(6) <= abs(roll) <= math.radians(12)


# ---------------------------------------------------------------------------- the peer service


class Clock:
    def __init__(self, t: float = 100.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_instance_id_persists_in_the_data_dir(tmp_path):
    a = load_instance(tmp_path)
    assert a == load_instance(tmp_path) and 0 < a < 2**64
    assert (tmp_path / "peer-id").read_text("utf-8").strip() == f"{a:016x}"
    (tmp_path / "peer-id").write_text("not hex", "utf-8")
    assert load_instance(tmp_path) != a
    assert load_instance(None) != load_instance(None)


def test_receive_drops_own_duplicates_garbage_and_floods():
    clock = Clock()
    svc = PeerService(0xAA, clock=clock)
    svc.receive(b"junk", "10.0.0.2", clock.t)
    svc.receive(b"\0" * 1300, "10.0.0.2", clock.t)
    svc.receive(wire.encode_ping(1, 0xAA, 0), "10.0.0.2", clock.t)
    svc.receive(wire.encode_ping(1, 0xBB, 0), "10.0.0.2", clock.t)
    svc.receive(wire.encode_ping(1, 0xBB, 0), "10.0.0.3", clock.t)  # the broadcast copy of the same packet
    c = svc.counters
    assert (c["malformed"], c["oversize"], c["own"], c["duplicate"]) == (1, 1, 1, 1)
    for seq in range(2, 200):
        svc.receive(wire.encode_ping(seq, 0xBB, 0), "10.0.0.2", clock.t)
    assert c["rate_limited"] == 199 - RATE_PER_S
    clock.t += 1.0
    svc.receive(wire.encode_ping(500, 0xBB, 0), "10.0.0.2", clock.t)
    assert c["rate_limited"] == 199 - RATE_PER_S  # the bucket refills


def test_peer_cap_stale_and_removal():
    clock = Clock()
    svc = PeerService(0xAA, clock=clock)
    for i in range(MAX_PEERS + 5):
        svc.receive(wire.encode_state(1, 0x100 + i, 0, state(1, 0)), f"10.0.0.{i + 2}", clock.t)
    out = svc.tick(clock.t)
    assert len(svc.peers) == MAX_PEERS and len(out.joined) == MAX_PEERS and svc.counters["peer_cap"] == 5
    clock.t += STALE_S + 0.1
    assert all(p.stale(clock.t) for p in svc.peers.values())
    clock.t += REMOVE_S
    out = svc.tick(clock.t)
    assert not svc.peers and len(out.left) == MAX_PEERS and {r for _, r in out.left} == {"timeout"}


def test_state_ordering_shot_dedupe_and_bye():
    clock = Clock()
    svc = PeerService(0xAA, clock=clock)
    svc.receive(wire.encode_state(5, 0xBB, 1000, state(2, 1)), "10.0.0.2", clock.t)
    svc.receive(wire.encode_state(4, 0xBB, 990, state(1, 1)), "10.0.0.2", clock.t)  # late, older
    svc.receive(
        wire.encode_shot(6, 0xBB, 1001, wire.Shot(1, 7, 1234, (0, -10, 1), (0, 35, 0))), "10.0.0.2", clock.t
    )
    out = svc.tick(clock.t)
    peer = svc.peers[0xBB]
    assert len(peer.aircraft) == 2 and peer.callsign == "Alpha" and peer.seat == 3
    assert [s.bullet for _, s, _ in out.shots] == [7]  # three copies, one bullet
    svc.receive(wire.encode_bye(7, 0xBB, 1002), "10.0.0.2", clock.t)
    out = svc.tick(clock.t)
    assert not svc.peers and out.left[0][1] == "bye"


def test_rotating_instance_ids_cannot_bypass_the_rate_limit_or_fill_the_table():
    clock = Clock()
    svc = PeerService(0xAA, clock=clock)
    svc.receive(wire.encode_state(1, 0xBB, 0, state(1, 0)), "10.0.0.2", clock.t)  # a real peer
    svc.receive(wire.encode_ping(2, 0xBB, 0), "10.0.0.2", clock.t)
    for i in range(20000):  # one host, a fresh instance id on every packet
        svc.receive(wire.encode_ping(1, 0x1000 + i, 0), "10.0.0.66", clock.t)
    c = svc.counters
    # the address budget stops the flood before parsing; at most HOST_INSTANCES ids bind to it
    assert c["rate_limited"] == 20000 - IP_RATE_PER_S and c["flood"] == 0
    assert c["source_cap"] >= IP_RATE_PER_S - 2 - HOST_INSTANCES
    assert c["inbox_full"] == 0 and len(svc._inbox) <= 2 + HOST_INSTANCES
    out = svc.tick(clock.t)
    # PINGs from ids without a STATE create no peer; only the real peer joined
    assert list(svc.peers) == [0xBB] and [p.instance for p in out.joined] == [0xBB]
    assert c["unknown"] == HOST_INSTANCES
    # the real peer's duplicate window survived: a repeated sequence number is still dropped
    svc.receive(wire.encode_ping(2, 0xBB, 0), "10.0.0.2", clock.t)
    assert c["duplicate"] == 1


def test_forged_ids_never_evict_a_live_peer_or_take_its_slot():
    clock = Clock()
    svc = PeerService(0xAA, clock=clock)
    svc.receive(wire.encode_state(1, 0xBB, 0, state(1, 0)), "10.0.0.2", clock.t)
    for host in range(64):  # many addresses, each at its own cap of forged ids
        for k in range(HOST_INSTANCES + 2):
            svc.receive(wire.encode_ping(1, 0x10000 + host * 16 + k, 0), f"10.1.{host}.1", clock.t)
    assert 0xBB in svc._sources and svc.counters["source_cap"] > 0
    svc.receive(wire.encode_ping(1, 0xBB, 0), "10.0.0.2", clock.t)
    assert svc.counters["duplicate"] == 1  # seq 1 of the real peer is still remembered
    svc.tick(clock.t)
    assert list(svc.peers) == [0xBB]
    # ids silent for REMOVE_S are released and new ones bind again
    clock.t += REMOVE_S + 0.5
    svc.receive(wire.encode_state(5, 0x99, 0, state(1, 0)), "10.2.0.1", clock.t)
    assert 0x99 in svc._sources


def test_an_instance_stays_bound_to_its_address():
    clock = Clock()
    svc = PeerService(0xAA, clock=clock)
    svc.receive(wire.encode_state(1, 0xBB, 0, state(1, 0)), "10.0.0.2", clock.t)
    svc.tick(clock.t)
    # another host forges a BYE, a HIT and a STATE with the known id: all dropped, the peer stays
    svc.receive(wire.encode_bye(2, 0xBB, 0), "10.0.0.9", clock.t)
    svc.receive(wire.encode_hit(3, 0xBB, 0, wire.Hit(0xAA, 1, 1, 20, True, (0, 0, 0))), "10.0.0.9", clock.t)
    svc.receive(wire.encode_state(4, 0xBB, 0, state(2, 0)), "10.0.0.9", clock.t)
    out = svc.tick(clock.t)
    assert 0xBB in svc.peers and not out.left and not out.hits and len(svc.peers[0xBB].aircraft) == 1
    assert svc.counters["ip_mismatch"] == 3
    # the real host can still leave
    svc.receive(wire.encode_bye(5, 0xBB, 0), "10.0.0.2", clock.t)
    out = svc.tick(clock.t)
    assert not svc.peers and out.left[0][1] == "bye"
    # after the id has been silent for REMOVE_S it may come back from a new address
    clock.t += REMOVE_S + 0.5
    svc.receive(wire.encode_state(6, 0xBB, 0, state(1, 0)), "10.0.0.9", clock.t)
    svc.tick(clock.t)
    assert svc.peers[0xBB].ip == "10.0.0.9"


def test_global_budget_drops_a_flood_before_parsing():
    clock = Clock()
    svc = PeerService(0xAA, clock=clock)
    for i in range(5000):
        svc.receive(b"junk", f"10.{i // 250}.{i % 250}.1", clock.t)
    c = svc.counters
    assert c["flood"] > 0 and c["malformed"] + c["flood"] == 5000
    assert c["malformed"] <= GLOBAL_RATE_PER_S


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _loopback():
    return [("127.0.0.1", "127.255.255.255")]


def _pump(services, predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for s in services:
            s.tick(time.monotonic())
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_two_instances_on_one_host_see_each_other():
    port = _free_port()
    a = PeerService(0xA0, port, interfaces=_loopback)
    b = PeerService(0xB0, port, interfaces=_loopback)
    try:
        assert a.open() and b.open(), (a.error, b.error)
        assert _pump([a, b], lambda: bool(a.interfaces and b.interfaces), 2.0)
        assert _pump(
            [a, b],
            lambda: (
                a.send_state(wire.State("Alpha", 0, None, 1, 0, 0, (aircraft(1),))),
                0xA0 in b.peers and b.peers[0xA0].joined,
            )[1],
        )
        peer = b.peers[0xA0]
        assert peer.callsign == "Alpha" and peer.aircraft[1].p == pytest.approx((1.0, 2.0, 0.5))
        assert a.counters["own"] > 0  # multicast loopback brings our own packets back; they are dropped
        assert b.counters["duplicate"] > 0  # the group and the directed broadcast both delivered
        # PING every 2 s, PONG from the other side: both learn the round trip (a peer joins with its STATE)
        assert _pump(
            [a, b],
            lambda: (
                b.send_state(wire.State("Bravo", 1, None, 0, 0, 0, ())),
                0xB0 in a.peers and a.peers[0xB0].rtt_ms is not None,
            )[1],
            6.0,
        )
        assert 0 <= a.peers[0xB0].rtt_ms < 500
        a.close()
        assert _pump([b], lambda: 0xA0 not in b.peers, 2.0)
    finally:
        a.close(bye=False)
        b.close(bye=False)


def test_open_reports_a_port_it_cannot_bind():
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("", 0))  # no SO_REUSEADDR / SO_REUSEPORT: nobody may share it
    try:
        svc = PeerService(0xA0, blocker.getsockname()[1], interfaces=_loopback)
        assert not svc.open() and svc.error and not svc.ok
    finally:
        blocker.close()
