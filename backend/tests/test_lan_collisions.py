"""Collisions with peers' aircraft (07 §2, WP-B): remote spheres in the local frame, swept by WP-A's collision
world, COLLIDE out and in with its checks (forged, stale, far, rate), the reply and ram credit, and 0.4 peers
that never collide."""

import math
from typing import ClassVar

import pytest
from conftest import open_agent
from test_leaderboard import OLD, PEER, StubPeers, feed

from relay.peers import arena as frame
from relay.peers import collisions as col
from relay.peers import game, wire

SEAT = 3


def join(svc, stepper):
    svc.update_settings({"peer_enabled": True})
    stepper.run(game.SEAT_LISTEN_S + 0.1)
    peers = StubPeers(svc.arena.instance, clock=svc.clock)
    svc.arena.peers = peers
    svc.arena.network = True
    return peers, peers.sent


def hover(svc, stepper, ids, alt=1.0):
    session, agent = open_agent(svc)
    for vid in ids:
        svc.claim(vid, agent)
        svc.run_skill(vid, agent, "takeoff", {"alt": alt})
    assert stepper.until(lambda: all(svc.vehicles[v].skill.state == "done" for v in ids), 25, [session])
    return session, agent


def to_peer(p_local):
    """A point of this terminal's local frame (seat 0) in the peer's frame (seat 3)."""
    return frame.from_arena(SEAT, frame.to_arena(0, p_local))


def vec_to_peer(v_local):
    return frame.vec_from_arena(SEAT, frame.vec_to_arena(0, v_local))


def peer_aircraft(p_local, v_local, index=1):
    return wire.Aircraft(
        index, wire.ARMED, 100, to_peer(p_local), vec_to_peer(v_local), (0.0, 0.0, 0.0), 0.5, 24
    )


def state(seq, mono, craft, instance=PEER, stats=wire.Stats()):
    return wire.encode_state(
        seq, instance, mono, wire.State("Bravo", SEAT, None, 0, 0, 0, tuple(craft), (), stats)
    )


def here(svc, vid="sim-1"):
    v = svc.vehicles[vid]
    s = v.driver.sim.quad.s
    ox, oy, oz = v.frame_offset
    return (s.p[0] + ox, s.p[1] + oy, s.p[2] + oz)


def collide(svc, target=1, own=1, t_ms=1000, contact=5, flags=0, v=(0.0, 0.0, 0.0), point=(0.0, 0.0, 1.0)):
    """A COLLIDE from Bravo to this terminal (sent at Bravo's mono_ms 1000 unless ``t_ms`` says otherwise)."""
    return wire.Collide(svc.arena.instance, contact, target, own, flags, t_ms, point, v, 2.5)


@pytest.fixture
def lan(svc, stepper):
    svc.add_sims(1)
    stepper.run(2.5)
    peers, sent = join(svc, stepper)
    session, _agent = hover(svc, stepper, ["sim-1"])
    return peers, sent, session


def test_version_2_peers_become_moving_spheres_and_0_4_peers_do_not(svc, stepper, lan):
    peers, _sent, session = lan
    p = here(svc)
    far = (p[0] + 3.0, p[1], p[2])
    before = (far[0], far[1] - 0.05, far[2])  # 50 ms earlier at 1 m/s: a track, so it is collidable
    feed(svc, peers, state(1, 950, [peer_aircraft(before, (0.0, 1.0, 0.0))]))
    stepper.run(0.02, [session])
    feed(svc, peers, state(2, 1000, [peer_aircraft(far, (0.0, 1.0, 0.0))]))
    for seq, mono, q in ((1, 950, before), (2, 1000, far)):
        feed(svc, peers, state(seq, mono, [peer_aircraft(q, (0.0, 1.0, 0.0))], OLD, None), "10.9.9.10")
    stepper.run(0.1, [session])
    ext = svc.arena.collisions.external
    assert list(ext) == [f"{PEER:016x}/sim-1"]  # the 0.4 peer's aircraft never collide
    body = ext[f"{PEER:016x}/sim-1"]
    assert body.label == "Bravo / SIM-1"
    assert body.p1[0] == pytest.approx(far[0], abs=1e-3)
    assert far[1] < body.p1[1] <= far[1] + 1.0 * col.LEAD_MAX_S + 1e-6  # carried forward, 0.15 s at most
    assert body.v == pytest.approx((0.0, 1.0, 0.0), abs=1e-3)


def test_a_fast_remote_aircraft_crashes_ours_and_the_peer_hears_about_it(svc, stepper, lan):
    peers, sent, session = lan
    p = here(svc)
    v = svc.vehicles["sim-1"]
    start = (p[0] - 1.0, p[1], p[2])
    feed(svc, peers, state(1, 1000, [peer_aircraft(start, (3.0, 0.0, 0.0))]))
    for k in range(1, 8):  # it flies through our aircraft at 3 m/s, one STATE every 50 ms
        stepper.run(0.05, [session])
        q = (start[0] + 0.15 * k, start[1], start[2])
        feed(svc, peers, state(1 + k, 1000 + 50 * k, [peer_aircraft(q, (3.0, 0.0, 0.0))]))
    stepper.run(0.05, [session])
    assert v.driver.sim.quad.s.crashed and v.driver.sim.quad.s.crash_code == "collision"
    (c,) = sent["collide"]
    assert c.to_instance == PEER and c.target == 1 and c.own == 1 and c.crashed and not c.reply
    assert c.closing >= 2.0
    # our velocity from before the contact (a hover), not the bounce it took: the peer's rule needs it
    assert max(abs(x) for x in c.v) < 0.3 and v.driver.sim.quad.s.v[0] > 1.0
    events = [e for e in svc.events.after(0, 5000) if e["kind"] == "collision"]
    (e,) = events
    assert (
        e["vehicle"] == "sim-1"
        and e["data"]["b"] == f"{PEER:016x}/sim-1"
        and e["data"]["crashed"] == ["sim-1"]
    )
    assert e["data"]["remote"] is True and e["text_en"].startswith("SIM-1 collided with Bravo / SIM-1 at")
    assert e["text_zh"].startswith("SIM-1 与 Bravo / SIM-1 相撞")
    stepper.run(0.3, [session])
    assert svc.arena.board.lines["sim-1"].crashes == 1


def test_a_slow_touch_bounces_without_a_crash(svc, stepper, lan):
    peers, sent, session = lan
    p = here(svc)
    v = svc.vehicles["sim-1"]
    start = (p[0] - 0.6, p[1], p[2])
    feed(svc, peers, state(1, 1000, [peer_aircraft(start, (1.0, 0.0, 0.0))]))
    for k in range(1, 12):
        stepper.run(0.05, [session])
        feed(
            svc,
            peers,
            state(1 + k, 1000 + 50 * k, [peer_aircraft((start[0] + 0.05 * k, *start[1:]), (1.0, 0.0, 0.0))]),
        )
        if sent["collide"]:
            break
    (c,) = sent["collide"]
    assert not c.crashed and 0.0 < c.closing < 2.0
    assert not v.driver.sim.quad.s.crashed
    assert v.driver.sim.quad.s.v[0] > 0.2  # pushed away along +x


def _peer_near(svc, stepper, peers, session, offset=(0.3, 0.0, 0.0), velocity=(-2.5, 0.0, 0.0)):
    p = here(svc)
    q = tuple(p[i] + offset[i] for i in range(3))
    # two STATEs 50 ms apart that fly the claimed velocity: a continuous track
    q0 = tuple(q[i] - velocity[i] * 0.05 for i in range(3))
    feed(
        svc, peers, state(0, 950, [peer_aircraft(q0, velocity)])
    )  # sequence numbers 2 and up are the COLLIDEs'
    stepper.run(0.01, [session])
    feed(svc, peers, state(1, 1000, [peer_aircraft(q, velocity)]))
    svc.arena.collisions._pairs.clear()
    # keep it from being swept by our own test: it stays where it is, as far as our tick knows
    svc.arena.collisions._last.clear()
    stepper.run(0.01, [session])


def test_received_collides_are_checked_before_they_touch_our_aircraft(svc, stepper, lan, monkeypatch):
    peers, sent, session = lan
    monkeypatch.setattr(svc.world, "_externals", lambda *a: None)  # only COLLIDE in this test
    _peer_near(svc, stepper, peers, session, offset=(2.0, 0.0, 0.0))  # 2 m away: not a contact
    counters = svc.arena.collisions.counters
    feed(svc, peers, wire.encode_collide(2, PEER, 1000, collide(svc, own=4)))  # Bravo does not fly SIM-4
    feed(svc, peers, wire.encode_collide(3, PEER, 1000, collide(svc, t_ms=500)))  # 0.5 s old
    feed(svc, peers, wire.encode_collide(4, PEER, 1000, collide(svc, contact=6)))  # 2 m apart here
    stepper.run(0.02, [session])
    assert (counters["unknown"], counters["late"], counters["far"]) == (1, 1, 1)
    assert not sent["collide"] and not svc.vehicles["sim-1"].driver.sim.quad.s.crashed


def test_an_accepted_collide_applies_the_rule_replies_and_credits_the_ram(svc, stepper, lan, monkeypatch):
    peers, sent, session = lan
    monkeypatch.setattr(svc.world, "_externals", lambda *a: None)
    _peer_near(svc, stepper, peers, session)
    v = svc.vehicles["sim-1"]
    seat_v = frame.vec_to_arena(0, (-2.5, 0.0, 0.0))
    point = frame.to_arena(0, here(svc))
    feed(
        svc,
        peers,
        wire.encode_collide(2, PEER, 1000, collide(svc, flags=wire.COLLIDE_CRASHED, v=seat_v, point=point)),
    )
    stepper.run(0.02, [session])
    s = v.driver.sim.quad.s
    assert s.crashed and s.crash_code == "collision"  # 2.5 m/s closing: at the crash threshold or above
    assert s.crash_reason == "collision with Bravo / SIM-1"
    (reply,) = sent["collide"]
    assert (
        reply.reply and reply.crashed and reply.to_instance == PEER and reply.own == 1 and reply.target == 1
    )
    assert svc.arena.board.lines["sim-1"].rams == 1  # their aircraft crashed from ours
    # the same contact again: neither applied nor credited twice
    feed(
        svc,
        peers,
        wire.encode_collide(3, PEER, 1000, collide(svc, flags=wire.COLLIDE_CRASHED, v=seat_v, point=point)),
    )
    stepper.run(0.02, [session])
    assert len(sent["collide"]) == 1 and svc.arena.board.lines["sim-1"].rams == 1
    # a reply is never answered
    feed(
        svc,
        peers,
        wire.encode_collide(
            4, PEER, 1000, collide(svc, contact=8, flags=wire.COLLIDE_REPLY, v=seat_v, point=point)
        ),
    )
    stepper.run(0.02, [session])
    assert len(sent["collide"]) == 1


def test_collides_are_rate_limited_per_peer_aircraft(svc, stepper, lan, monkeypatch):
    peers, _sent, session = lan
    monkeypatch.setattr(svc.world, "_externals", lambda *a: None)
    monkeypatch.setattr(svc.world, "apply_contact", lambda *a: None)  # our aircraft flies through the burst
    _peer_near(svc, stepper, peers, session)
    for k in range(8):
        feed(svc, peers, wire.encode_collide(10 + k, PEER, 1000, collide(svc, contact=100 + k)))
    stepper.run(0.02, [session])
    counters = svc.arena.collisions.counters
    assert counters["rate_limited"] == 8 - col.COLLIDE_RATE
    assert len(svc.arena.collisions._seen) == col.COLLIDE_RATE


def test_a_contact_found_on_both_sides_is_applied_once(svc, stepper, lan):
    peers, sent, session = lan
    arena = svc.arena
    _peer_near(svc, stepper, peers, session)
    key = f"{PEER:016x}/sim-1"
    arena.collisions.on_contact(key, "sim-1", here(svc), 2.4, True)  # as WP-A's world reports it
    assert len(sent["collide"]) == 1
    arena.collisions.on_contact(key, "sim-1", here(svc), 2.4, True)  # still touching: reported once
    assert len(sent["collide"]) == 1
    point = frame.to_arena(0, here(svc))
    feed(
        svc, peers, wire.encode_collide(2, PEER, 1000, collide(svc, flags=wire.COLLIDE_CRASHED, point=point))
    )
    stepper.run(0.02, [session])
    assert len(sent["collide"]) == 1  # the peer has our COLLIDE already: no reply
    assert arena.board.lines["sim-1"].rams == 1  # but its crash is credited


def test_wp_a_world_takes_the_bodies_and_reports_contacts(svc, stepper, lan):
    peers, sent, session = lan

    class World:
        external: ClassVar[dict] = {}
        on_external_contact = None

    svc.world = World()
    try:
        p = here(svc)
        feed(svc, peers, state(1, 950, [peer_aircraft((p[0] + 3.0, p[1], p[2]), (0.0, 0.0, 0.0))]))
        stepper.run(0.05, [session])
        feed(svc, peers, state(2, 1000, [peer_aircraft((p[0] + 3.0, p[1], p[2]), (0.0, 0.0, 0.0))]))
        stepper.run(0.05, [session])
        world = svc.world
        assert list(world.external) == [f"{PEER:016x}/sim-1"] and world.on_external_contact is not None
        world.on_external_contact(f"{PEER:016x}/sim-1", "sim-1", p, 1.2, False)
        (c,) = sent["collide"]
        assert not c.crashed and c.closing == pytest.approx(1.2)
    finally:
        del svc.world


def test_the_shared_collision_rules_are_used():
    from relay.sim import collide, world

    assert col.collide is collide and col.ExternalBody is world.ExternalBody
    assert (collide.CRAFT_RADIUS, collide.RING_TUBE, collide.RAM_CRASH_V, collide.RESTITUTION) == (
        0.15,
        0.03,
        2.0,
        0.4,
    )
    with pytest.raises(ImportError):
        __import__("relay.peers.collide_fallback")


# ---------------------------------------------------------------------------- review fixes (v0.5.0)


def test_the_receiver_sees_the_true_closing_speed_from_the_senders_approach(svc, stepper, lan, monkeypatch):
    """Two terminals' view of one 2.5 m/s ram: the sender's COLLIDE carries its approach, so both crash."""
    peers, sent, session = lan
    monkeypatch.setattr(svc.world, "_externals", lambda *a: None)
    _peer_near(svc, stepper, peers, session)
    arena = svc.arena
    key = f"{PEER:016x}/sim-1"
    # as the sender's world reports it: the impulse is applied already, the approach is passed alongside
    arena.collisions.on_contact(key, "sim-1", here(svc), 2.5, True, (0.0, 0.0, 0.0))
    (out,) = sent["collide"]
    assert out.v == pytest.approx((0.0, 0.0, 0.0), abs=1e-6)
    arena.collisions._pairs.clear()
    point = frame.to_arena(0, here(svc))
    seat_v = frame.vec_to_arena(0, (-2.5, 0.0, 0.0))
    feed(svc, peers, wire.encode_collide(2, PEER, 1000, collide(svc, contact=9, v=seat_v, point=point)))
    stepper.run(0.02, [session])
    assert svc.vehicles["sim-1"].driver.sim.quad.s.crashed  # 2.5 m/s, not the 0.75 a post-bounce speed gives


def test_a_crash_shortly_after_a_bounce_still_reaches_the_peer(svc, stepper, lan, monkeypatch):
    peers, sent, session = lan
    monkeypatch.setattr(svc.world, "_externals", lambda *a: None)
    _peer_near(svc, stepper, peers, session)
    key = f"{PEER:016x}/sim-1"
    col_ = svc.arena.collisions
    col_.on_contact(key, "sim-1", here(svc), 1.0, False)  # a graze
    col_.on_contact(key, "sim-1", here(svc), 1.0, False)  # still touching
    assert len(sent["collide"]) == 1
    col_.on_contact(key, "sim-1", here(svc), 3.0, True)  # 0.3 s later it comes back at 3 m/s
    assert len(sent["collide"]) == 2 and sent["collide"][1].crashed
    col_.on_contact(key, "sim-1", here(svc), 3.0, True)
    assert len(sent["collide"]) == 2


def test_a_peer_crash_after_a_registered_bounce_is_applied_here(svc, stepper, lan, monkeypatch):
    peers, _sent, session = lan
    monkeypatch.setattr(svc.world, "_externals", lambda *a: None)
    _peer_near(svc, stepper, peers, session)
    key = f"{PEER:016x}/sim-1"
    svc.arena.collisions._pairs[("sim-1", key)] = (svc.clock() - 0.4, False)  # our own graze 0.4 s ago
    point = frame.to_arena(0, here(svc))
    seat_v = frame.vec_to_arena(0, (-2.5, 0.0, 0.0))
    data = collide(svc, contact=11, flags=wire.COLLIDE_CRASHED, v=seat_v, point=point)
    feed(svc, peers, wire.encode_collide(2, PEER, 1000, data))
    stepper.run(0.02, [session])
    assert svc.vehicles["sim-1"].driver.sim.quad.s.crashed
    assert svc.arena.board.lines["sim-1"].rams == 1


def test_a_claimed_speed_alone_never_crashes_our_aircraft(svc, stepper, lan, monkeypatch):
    """A peer shows a hovering aircraft by ours and claims 150 m/s in a COLLIDE, or 200 m/s in a STATE."""
    peers, sent, session = lan
    _peer_near(svc, stepper, peers, session, offset=(0.3, 0.0, 0.0), velocity=(0.0, 0.0, 0.0))
    s = svc.vehicles["sim-1"].driver.sim.quad.s
    point = frame.to_arena(0, here(svc))
    claim = frame.vec_to_arena(0, (-150.0, 0.0, 0.0))
    forged = collide(svc, contact=12, flags=wire.COLLIDE_CRASHED, v=claim, point=point)
    monkeypatch.setattr(svc.world, "_externals", lambda *a: None)
    feed(svc, peers, wire.encode_collide(2, PEER, 1000, forged))
    stepper.run(0.02, [session])
    assert not s.crashed
    (reply,) = sent["collide"]  # checked and applied as the bounce it can only have been
    assert reply.reply and not reply.crashed and reply.closing < 2.0
    # the STATE claim of a stationary track is held to the track: no ram from a standing sphere
    monkeypatch.undo()
    key = f"{PEER:016x}/sim-1"
    q = svc.arena.collisions._tracks[key].samples[-1][1]  # it stays where it was
    feed(svc, peers, state(3, 1050, [peer_aircraft(q, (-200.0, 0.0, 0.0))]))
    stepper.run(0.02, [session])
    body = svc.arena.collisions.external.get(key)
    assert body is not None and math.hypot(*body.v) <= col.VEL_TOL + 1e-3
    stepper.run(0.3, [session])
    assert not s.crashed


def test_a_jump_makes_the_aircraft_untouchable_for_a_moment(svc, stepper, lan, monkeypatch):
    peers, sent, session = lan
    monkeypatch.setattr(svc.world, "_externals", lambda *a: None)
    p = here(svc)
    far = (p[0] + 30.0, p[1], p[2])
    for seq, mono in ((0, 900), (1, 950)):
        feed(svc, peers, state(seq, mono, [peer_aircraft(far, (0.0, 0.0, 0.0))]))
        stepper.run(0.01, [session])
    key = f"{PEER:016x}/sim-1"
    assert key in svc.arena.collisions.external
    near = (p[0] + 0.3, p[1], p[2])  # 30 m in 50 ms: a teleport onto our aircraft
    feed(svc, peers, state(2, 1000, [peer_aircraft(near, (-2.5, 0.0, 0.0))]))
    stepper.run(0.01, [session])
    feed(svc, peers, state(3, 1050, [peer_aircraft(near, (-2.5, 0.0, 0.0))]))
    stepper.run(0.01, [session])
    assert key not in svc.arena.collisions.external
    data = collide(
        svc, contact=13, flags=wire.COLLIDE_CRASHED, t_ms=1050, v=frame.vec_to_arena(0, (-2.5, 0.0, 0.0))
    )
    feed(svc, peers, wire.encode_collide(4, PEER, 1050, data))
    stepper.run(0.02, [session])
    assert svc.arena.collisions.counters["untracked"] == 1
    assert not svc.vehicles["sim-1"].driver.sim.quad.s.crashed and not sent["collide"]
    for k in range(12):  # after the hold the track is a track again
        feed(svc, peers, state(5 + k, 1100 + 50 * k, [peer_aircraft(near, (0.0, 0.0, 0.0))]))
        stepper.run(0.05, [session])
    assert key in svc.arena.collisions.external


def test_a_restarted_peer_numbers_its_contacts_afresh(svc, stepper, lan, monkeypatch):
    peers, _sent, session = lan
    monkeypatch.setattr(svc.world, "_externals", lambda *a: None)
    monkeypatch.setattr(svc.world, "apply_contact", lambda *a: None)
    _peer_near(svc, stepper, peers, session)
    point = frame.to_arena(0, here(svc))
    data = collide(svc, contact=1, flags=wire.COLLIDE_CRASHED, point=point)
    feed(svc, peers, wire.encode_collide(2, PEER, 1000, data))
    stepper.run(0.02, [session])
    assert svc.arena.board.lines["sim-1"].rams == 1
    svc.arena.collisions.forget_peer(PEER)  # it left (BYE or timeout) and comes back with the same id
    assert not any(k[0] == PEER for k in svc.arena.collisions._seen)
    svc.arena.collisions._pairs.clear()
    p = here(svc)
    for seq, mono in ((10, 1050), (11, 1100)):  # its aircraft again, a fresh track
        feed(svc, peers, state(seq, mono, [peer_aircraft((p[0] + 0.3, p[1], p[2]), (0.0, 0.0, 0.0))]))
        stepper.run(0.01, [session])
    again = collide(svc, contact=1, t_ms=1100, flags=wire.COLLIDE_CRASHED, point=point)
    feed(svc, peers, wire.encode_collide(20, PEER, 1100, again))
    stepper.run(0.02, [session])
    assert svc.arena.board.lines["sim-1"].rams == 2
