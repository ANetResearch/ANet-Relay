"""LAN arena rules on the fleet thread (05 §7.1-§7.5): fire input, magazines, hits, shoot-down and respawn,
peer isolation from every control path, the arena view and its API."""

import math

import pytest
from conftest import open_agent
from fastapi.testclient import TestClient

from relay.api.app import create_app
from relay.api.schemas import ArenaView, Fleet
from relay.authority import Actor
from relay.errors import RelayError
from relay.fleet import FleetService, Vehicle
from relay.history import History
from relay.peers import arena as frame
from relay.peers import game, wire
from relay.peers.service import PeerService

OP = Actor.operator()
PEER = 0x0000_0000_0000_0042


def boot(svc, stepper, n=1):
    ids = svc.add_sims(n)
    stepper.run(2.5)
    return ids


def join(svc, stepper):
    svc.update_settings({"peer_enabled": True})
    stepper.run(game.SEAT_LISTEN_S + 0.1)
    assert svc.arena.enabled and svc.arena.seat == 0


def take_off(svc, stepper, ids, alt=1.0):
    session, agent = open_agent(svc)
    for vid in ids:
        svc.claim(vid, agent)
        svc.run_skill(vid, agent, "takeoff", {"alt": alt})
    assert stepper.until(lambda: all(svc.vehicles[v].skill.state == "done" for v in ids), 25, [session])
    return session, agent


def code(fn) -> str:
    with pytest.raises(RelayError) as exc:
        fn()
    return exc.value.code


def kinds(svc, kind):
    return [e for e in svc.events.after(0, 5000) if e["kind"] == kind]


class StubPeers(PeerService):
    """A peer table without a socket: packets go in through receive(), sends are recorded."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.sent = {"state": [], "hit": [], "shot": []}

    @property
    def ok(self) -> bool:
        return True

    def send(self, data: bytes) -> None:
        pass

    def send_state(self, state) -> None:
        self.sent["state"].append(state)

    def send_hit(self, hit) -> None:
        self.sent["hit"].append(hit)

    def send_shot(self, shot) -> None:
        self.sent["shot"].append(shot)


def stub_peers(svc):
    peers = StubPeers(svc.arena.instance, clock=svc.clock)
    svc.arena.peers = peers
    return peers, peers.sent


def feed(svc, peers, data, ip="10.9.9.9"):
    peers.receive(data, ip, svc.clock())


# ---------------------------------------------------------------------------- settings


def test_settings_defaults_and_validation(svc, tmp_path):
    s = svc.get_settings()
    assert s["peer_enabled"] is False and s["peer_port"] == 47114 and s["arena_auto_respawn"] is True
    assert s["peer_callsign"] == ""
    assert svc.arena.callsign == game.default_callsign() and 0 < len(svc.arena.callsign) <= 16
    for patch in ({"peer_port": 14550}, {"peer_port": 14555}, {"peer_port": 80}, {"peer_callsign": "x" * 17}):
        assert code(lambda p=patch: svc.update_settings(p)) == "invalid_request"
    svc.http_port = 8765
    assert code(lambda: svc.update_settings({"peer_port": 8765})) == "invalid_request"
    svc.update_settings({"peer_callsign": "Bay\x07 2"})
    assert svc.arena.callsign == "Bay 2"


# ---------------------------------------------------------------------------- fire rules


def test_fire_rules(svc, stepper):
    (vid,) = boot(svc, stepper)
    session, agent = open_agent(svc)
    assert code(lambda: svc.fire(vid, OP, True)) == "not_possessed"
    svc.claim(vid, agent)
    assert code(lambda: svc.fire(vid, agent, True)) == "arena_off"
    join(svc, stepper)
    assert code(lambda: svc.fire(vid, agent, True)) == "not_armed"
    svc.arm(vid, agent, True)
    stepper.run(0.3, [session])
    assert code(lambda: svc.fire(vid, agent, True)) == "not_airborne"
    assert svc.fire(vid, agent, False)["firing"] is False  # releasing the trigger is always accepted
    fake = Vehicle("real-10-0-0-9", "real", "Quad 09", driver=None)
    svc.vehicles[fake.id] = fake
    try:
        assert code(lambda: svc.fire(fake.id, OP, True)) == "sim_only"
    finally:
        del svc.vehicles[fake.id]


def test_rate_magazine_reload_and_hold_expiry(svc, stepper):
    (vid,) = boot(svc, stepper)
    join(svc, stepper)
    session, agent = take_off(svc, stepper, [vid])
    # the WebSocket input lapses 300 ms after the last refresh: rounds at 0, 125 and 250 ms
    svc.fire(vid, agent, True)
    stepper.run(1.0, [session])
    assert svc.arena.guns[vid].ammo == game.MAGAZINE - 3
    # three seconds of REST fire: 8 rounds per second empties the magazine, then 1.5 s to reload
    svc.arena.guns[vid].ammo = game.MAGAZINE
    svc.arena._own_shots.clear()
    svc.fire(vid, agent, True, seconds=3.0)
    stepper.run(3.05, [session])
    gun = svc.arena.guns[vid]
    assert len(svc.arena._own_shots) == 24 and gun.ammo == 0 and gun.reload_until is not None
    assert svc.arena_snapshot()["local"]["reloading"][vid] is True
    stepper.run(1.6, [session])
    assert gun.ammo == game.MAGAZINE and gun.reload_until is None


def test_hits_shoot_down_without_alert_and_respawn(svc, stepper):
    ids = boot(svc, stepper, 2)  # sim-1 at (0, 0), sim-2 two metres east
    join(svc, stepper)
    session, agent = take_off(svc, stepper, ids)
    svc.run_skill("sim-1", agent, "yaw", {"deg": 90})
    assert stepper.until(lambda: svc.vehicles["sim-1"].skill.state == "done", 10, [session])
    # park sim-2 1.5 m ahead of sim-1's nose at its height (the goto target is in the shared sim frame)
    shooter = svc.vehicles["sim-1"]
    x, y, z = shooter.world_enu()
    h = shooter.driver.sim.quad.s.yaw
    svc.run_skill("sim-2", agent, "goto", {"x": x + 1.5 * math.sin(h), "y": y + 1.5 * math.cos(h), "z": z})
    assert stepper.until(lambda: svc.vehicles["sim-2"].skill.state == "done", 10, [session])
    stepper.run(1.0, [session])
    for _ in range(8):  # keep firing until five rounds have landed
        svc.fire("sim-1", agent, True, seconds=1.0)
        if stepper.until(lambda: svc.arena.is_down("sim-2"), 1.0, [session]):
            break
    target = svc.vehicles["sim-2"]
    hits = kinds(svc, "arena_hit")
    assert len(hits) >= 5 and all(e["vehicle"] == "sim-2" and e["data"]["own_shot"] for e in hits)
    assert hits[0]["text_en"].startswith("SIM-2 was hit by SIM-1") and "命中" in hits[0]["text_zh"]
    assert svc.arena.guns["sim-2"].hp == 0 and svc.arena.score.hits >= 5 and svc.arena.score.downs == 1
    assert kinds(svc, "arena_down")[0]["vehicle"] == "sim-2"
    assert target.driver.sim.quad.s.crash_reason == game.SHOT_DOWN
    # free fall, then disarmed on the ground; the crash is not a safety alert in the arena
    assert stepper.until(lambda: not target.armed and target.landed == "ground", 5, [session])
    assert target.state["crashed"]
    assert not [a for a in svc.fleet()["alerts"] if a["code"] == "crashed"]
    assert code(lambda: svc.fire("sim-2", agent, True)) == "arena_down"
    # automatic respawn five seconds after the shoot-down
    assert stepper.until(lambda: not svc.arena.is_down("sim-2"), 6, [session])
    assert svc.arena.guns["sim-2"].hp == game.MAX_HP and not target.state["crashed"]
    assert target.driver.sim.quad.s.p == pytest.approx([0.0, 0.0, 0.0])


def test_manual_reset_revives_and_auto_respawn_can_be_off(svc, stepper):
    (vid,) = boot(svc, stepper)
    join(svc, stepper)
    svc.update_settings({"arena_auto_respawn": False})
    v = svc.vehicles[vid]
    gun = svc.arena.guns[vid]
    gun.hp = 20
    v.driver.sim.shoot_down(game.SHOT_DOWN)
    gun.down_at = svc.clock()
    stepper.run(game.RESPAWN_S + 1.0)
    assert svc.arena.is_down(vid) and v.state["crashed"]
    svc.reset_sim(vid)
    stepper.run(0.1)
    assert not svc.arena.is_down(vid) and gun.hp == game.MAX_HP


# ---------------------------------------------------------------------------- peers


def _peer_state(seat=3, n=2, seq=1, mono=1000):
    craft = tuple(
        wire.Aircraft(i + 1, wire.ARMED, 100, (2.0 * i, 0.0, 1.0), (0.0, 1.0, 0.0), (0.0, 0.0, 0.5), 0.5, 24)
        for i in range(n)
    )
    return wire.encode_state(seq, PEER, mono, wire.State("Bravo", seat, None, 0, 0, 0, craft))


def test_peer_packets_never_reach_a_control_path(svc, stepper, monkeypatch):
    ids = boot(svc, stepper, 2)
    join(svc, stepper)
    peers, _ = stub_peers(svc)
    stepper.run(0.5)
    before = {vid: (v.auth, v.sticks, v.skill, v.mode, v.armed) for vid, v in svc.vehicles.items()}
    labels = dict(svc._labels)

    def forbidden(*_a, **_k):
        raise AssertionError("a peer packet reached a control path")

    for name in (
        "sticks", "arm", "set_mode", "claim", "unclaim", "possess", "release", "run_skill", "run_plan",
        "set_param", "motor_test", "stop", "stop_all", "fire", "connect", "add_sims", "remove_sims",
        "remove_vehicle", "reset_sim", "refly", "course_start", "course_stop", "set_calibration",
        "update_settings", "_add_real", "_start_skill", "_possess_mode", "_apply_effects",
    ):  # fmt: skip
        monkeypatch.setattr(svc, name, forbidden)
    for v in svc.vehicles.values():
        for name in (
            "send_sticks", "arm", "set_mode", "set_param", "send_rc", "attitude_target", "velocity_target",
            "position_target_local", "command_int",
        ):  # fmt: skip
            monkeypatch.setattr(v.driver, name, forbidden)

    centre = svc.arena._centre(svc.vehicles["sim-1"])
    feed(svc, peers, _peer_state(n=8))
    shot = wire.Shot(1, 77, 1000, (centre[0], centre[1] - 1.0, centre[2] + 0.05), (0.0, 35.0, 0.0))
    feed(svc, peers, wire.encode_shot(2, PEER, 1000, shot))
    feed(svc, peers, wire.encode_hit(3, PEER, 1001, wire.Hit(PEER + 1, 5, 1, 100, True, (0, 0, 0))))
    feed(svc, peers, wire.encode_ping(4, PEER, 1002))
    feed(svc, peers, b"ANRA\x01\x01" + b"\xff" * 300)
    feed(svc, peers, wire.encode_state(5, 2**64 - 1, 0, wire.State("sim-1", 0, None, 0, 0, 0, ())))
    stepper.run(1.0)

    assert set(svc.vehicles) == set(ids) and svc._labels == labels and not svc.by_host
    after = {vid: (v.auth, v.sticks, v.skill, v.mode, v.armed) for vid, v in svc.vehicles.items()}
    assert after == before
    assert [v["id"] for v in svc.fleet()["vehicles"]] == ids
    view = svc.arena_snapshot()
    keys = [x["key"] for p in view["peers"] for x in p["vehicles"]]
    assert len(keys) == 8 and all(k.startswith(f"{PEER:016x}/sim-") for k in keys)
    assert peers.counters["malformed"] == 1
    # the bullet is virtual: it only changed the simulator's hit points (a ground hit does not move it)
    assert svc.arena.guns["sim-1"].hp == game.MAX_HP - 20


def test_remote_shot_hits_a_local_sim_and_the_owner_reports_it(svc, stepper):
    ids = boot(svc, stepper, 1)
    join(svc, stepper)
    session, _ = take_off(svc, stepper, ids)
    peers, sent = stub_peers(svc)
    feed(svc, peers, _peer_state(seat=3))
    stepper.run(0.1, [session])
    v = svc.vehicles["sim-1"]
    centre = svc.arena._centre(v)
    vz_before = v.driver.sim.quad.s.v[1]
    shot = wire.Shot(2, 9, 1000, (centre[0], centre[1] - 1.0, centre[2] + 0.02), (0.0, 35.0, 0.6))
    feed(svc, peers, wire.encode_shot(2, PEER, 1000, shot))
    stepper.run(0.1, [session])
    assert svc.arena.guns["sim-1"].hp == 80
    assert v.driver.sim.quad.s.v[1] > vz_before + 0.2  # pushed along the bullet's travel (north in seat 0)
    (hit,) = sent["hit"]
    assert hit.shooter_instance == PEER and hit.bullet == 9 and hit.target == 1 and not hit.down
    event = kinds(svc, "arena_hit")[-1]
    assert event["data"]["side"] == "taken" and event["data"]["shooter_callsign"] == "Bravo"
    assert event["text_en"] == "SIM-1 was hit by Bravo / SIM-2, 80 HP left."
    assert svc.arena.score.hits == 0  # the peer scores its own hit


def test_hit_reports_score_our_shots_and_peers_join_and_leave(svc, stepper):
    ids = boot(svc, stepper, 1)
    join(svc, stepper)
    session, agent = take_off(svc, stepper, ids)
    peers, sent = stub_peers(svc)
    feed(svc, peers, _peer_state())
    stepper.run(0.1, [session])
    assert kinds(svc, "peer_joined")[0]["text_en"] == "Bravo joined the arena."
    svc.fire("sim-1", agent, True)
    stepper.run(0.05, [session])
    (shot,) = sent["shot"]
    feed(
        svc,
        peers,
        wire.encode_hit(2, PEER, 1001, wire.Hit(svc.arena.instance, shot.bullet, 2, 20, True, (0, 0, 1))),
    )
    feed(
        svc,
        peers,
        wire.encode_hit(3, PEER, 1002, wire.Hit(svc.arena.instance, 999999, 2, 20, True, (0, 0, 1))),
    )
    stepper.run(0.1, [session])
    assert svc.arena.score.view() == {"hits": 1, "downs": 1, "deaths": 0}
    hit = [e for e in kinds(svc, "arena_hit") if e["data"]["side"] == "hit"]
    assert len(hit) == 1 and hit[0]["text_en"] == "SIM-1 hit Bravo / SIM-2." and hit[0]["vehicle"] == "sim-1"
    assert kinds(svc, "arena_down")[-1]["text_zh"] == "SIM-1 击落 Bravo / SIM-2。"
    # STATE copies the shot three times for lossy links, and carries only simulators
    stepper.run(0.3, [session])
    carried = [s for st in sent["state"] for s in st.shots if s.bullet == shot.bullet]
    assert len(carried) == game.STATE_SHOT_COPIES
    assert all([a.index for a in st.aircraft] == [1] for st in sent["state"])
    feed(svc, peers, wire.encode_bye(9, PEER, 2000))
    stepper.run(0.1, [session])
    assert kinds(svc, "peer_left")[0]["data"]["reason"] == "bye" and not svc.arena_snapshot()["peers"]


def test_seat_conflict_smaller_instance_keeps_the_seat(svc, stepper):
    boot(svc, stepper, 1)
    join(svc, stepper)
    peers, _ = stub_peers(svc)
    smaller = 1
    feed(svc, peers, wire.encode_state(1, smaller, 0, wire.State("Low", 0, None, 0, 0, 0, ())))
    stepper.run(0.1)
    assert svc.arena.instance > smaller and svc.arena.seat == 1
    larger = 2**64 - 1
    feed(svc, peers, wire.encode_state(1, larger, 0, wire.State("High", 1, None, 0, 0, 0, ())))
    stepper.run(0.1)
    assert svc.arena.seat == 1  # we keep it against a larger id


def test_view_converts_peers_into_the_local_frame_and_matches_the_schema(svc, stepper):
    ids = boot(svc, stepper, 1)
    join(svc, stepper)
    session, agent = take_off(svc, stepper, ids)
    peers, _ = stub_peers(svc)
    feed(svc, peers, _peer_state(seat=6, n=1))
    svc.fire("sim-1", agent, True)
    stepper.run(0.1, [session])
    view = svc.arena_snapshot()
    ArenaView.model_validate(view)
    Fleet.model_validate(svc.fleet())
    (peer,) = view["peers"]
    assert peer["callsign"] == "Bravo" and peer["seat"] == 6 and peer["id"] == f"{PEER:016x}"
    (craft,) = peer["vehicles"]
    expect = frame.peer_to_local((0.0, 0.0, 1.0), 6, 0)
    assert (craft["x"], craft["y"], craft["z"]) == pytest.approx(expect, abs=1e-3)
    assert expect[1] == pytest.approx(20.0)  # the opposite seat is 20 m north of seat 0's origin
    assert craft["yaw"] == pytest.approx(frame.peer_heading_to_local(0.5, 6, 0), abs=1e-3)
    (shot,) = [s for s in view["shots"] if s["mine"]]
    assert shot["owner"] == view["instance"] and shot["t0"] <= view["t"]
    assert math.dist(shot["p"], svc.vehicles["sim-1"].world_enu()) < 0.5  # spawned at the nose, local frame
    local = view["local"]
    assert local["ammo"]["sim-1"] == game.MAGAZINE - 1 and local["hp"]["sim-1"] == game.MAX_HP


def test_leaving_clears_the_arena(svc, stepper):
    boot(svc, stepper, 1)
    join(svc, stepper)
    svc.update_settings({"peer_enabled": False})
    view = svc.arena_snapshot()
    assert view["enabled"] is False and view["seat"] is None and view["peers"] == [] and view["shots"] == []


# ---------------------------------------------------------------------------- API


@pytest.fixture
def client():
    svc = FleetService(listen=False, history=History(None), operator_token="op-token")
    with TestClient(create_app(svc), base_url="http://127.0.0.1") as c:
        c.svc = svc
        yield c


def test_api_arena_view_fire_route_and_stream(client):
    hdr = {"X-Relay-Operator": "op-token"}
    view = client.get("/api/v1/arena").json()
    assert view["enabled"] is False and view["local"]["max_ammo"] == 24
    client.post("/api/v1/sims", headers=hdr)
    client.post("/api/v1/vehicles/sim-1/possess", headers=hdr, json={"input": "keyboard"})
    resp = client.post("/api/v1/vehicles/sim-1/fire", headers=hdr, json={"seconds": 1})
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "arena_off"
    assert client.post("/api/v1/vehicles/sim-1/fire", headers=hdr, json={"seconds": 4}).status_code == 400
    assert client.post("/api/v1/vehicles/sim-1/fire", json={"seconds": 1}).status_code == 401
    with client.websocket_connect("/api/v1/stream") as ws:
        ws.receive_json()
        seen = set()
        for _ in range(20):
            msg = ws.receive_json()
            seen.add(msg["type"])
            if msg["type"] == "arena":
                assert msg["data"]["enabled"] is False
                break
        assert {"fleet", "arena"} <= seen
        ws.send_json({"type": "fire", "vehicle": "sim-1", "on": True})
        for _ in range(30):
            msg = ws.receive_json()
            if msg["type"] == "error":
                assert msg["error"]["code"] == "auth_required"
                break
        else:
            pytest.fail("anonymous fire was not rejected")
