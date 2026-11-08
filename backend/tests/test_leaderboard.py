"""Leaderboard and matches (07 §4, WP-B): per-aircraft statistics, the merged LAN board with 0.4 peers,
sorting, the board revision, the match lifecycle over the API and MATCH packets from peers."""

import pytest
from conftest import open_agent
from fastapi.testclient import TestClient

from relay.api.app import create_app
from relay.api.schemas import ArenaView
from relay.api.schemas_match import Leaderboard
from relay.peers import board as boardmod
from relay.peers import game, wire
from relay.peers.service import PeerService

PEER = 0x0000_0000_0000_0042
OLD = 0x0000_0000_0000_0043
API = "/api/v1"
OP = {"X-Relay-Operator": "op-token"}


class StubPeers(PeerService):
    """A peer table without a socket: packets go in through receive(), sends are recorded."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.sent = {"state": [], "hit": [], "shot": [], "collide": [], "match": []}

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

    def send_collide(self, collide) -> None:
        self.sent["collide"].append(collide)

    def send_match(self, match) -> None:
        self.sent["match"].append(match)


def join(svc, stepper):
    svc.update_settings({"peer_enabled": True})
    stepper.run(game.SEAT_LISTEN_S + 0.1)
    assert svc.arena.enabled and svc.arena.seat == 0
    peers = StubPeers(svc.arena.instance, clock=svc.clock)
    svc.arena.peers = peers
    svc.arena.network = True  # the stub stands in for the socket: this terminal is on the LAN
    return peers, peers.sent


def feed(svc, peers, data, ip="10.9.9.9"):
    peers.receive(data, ip, svc.clock())


def take_off(svc, stepper, ids, alt=1.0):
    session, agent = open_agent(svc)
    for vid in ids:
        svc.claim(vid, agent)
        svc.run_skill(vid, agent, "takeoff", {"alt": alt})
    assert stepper.until(lambda: all(svc.vehicles[v].skill.state == "done" for v in ids), 25, [session])
    return session, agent


def peer_state(instance=PEER, seat=3, n=2, seq=1, mono=1000, stats=None, callsign="Bravo"):
    craft = tuple(
        wire.Aircraft(i + 1, wire.ARMED, 100, (2.0 * i, 0.0, 1.0), (0.0, 1.0, 0.0), (0.0, 0.0, 0.5), 0.5, 24)
        for i in range(n)
    )
    return wire.encode_state(seq, instance, mono, wire.State(callsign, seat, None, 0, 0, 0, craft, (), stats))


def stat(index, downs=0, rams=0, alive_ms=0, best_ms=0, hits=0, shots=0, name="Hunter (hard) r1"):
    return wire.Stat(index, "program", name, downs, hits, shots, rams, 0, 0, 20 * hits, alive_ms, best_ms)


def kinds(svc, kind):
    return [e for e in svc.events.after(0, 5000) if e["kind"] == kind]


def own_bullet_at(svc, shooter_vid, target_vid, bullet):
    """A round of ``shooter_vid`` 1 m short of ``target_vid``'s centre, flying at it (as ``_spawn``
    adds it)."""
    arena = svc.arena
    centre = arena._centre(svc.vehicles[target_vid])
    index = game.sim_index(shooter_vid)
    p0 = (centre[0], centre[1] - 1.0, centre[2] + 0.02)
    arena._add_bullet(game.Bullet(arena.instance, index, bullet, p0, (0.0, 35.0, 0.6), svc.clock()))
    arena._own_shots.append((bullet, index))


# ---------------------------------------------------------------------------- local statistics


def test_local_hits_downs_deaths_crashes_and_survival(svc, stepper):
    ids = svc.add_sims(2)
    stepper.run(2.5)
    _peers, _sent = join(svc, stepper)
    session, agent = take_off(svc, stepper, ids)
    board = svc.arena.board
    stepper.run(1.0, [session])
    assert board.lines["sim-1"].alive_since is not None and board.lines["sim-2"].alive_since is not None
    # sim-1 fires one round (a shot) and five of its rounds bring sim-2 down
    svc.fire("sim-1", agent, True)
    stepper.run(0.05, [session])
    svc.fire("sim-1", agent, False)
    assert board.lines["sim-1"].shots == 1
    for k in range(5):
        own_bullet_at(svc, "sim-1", "sim-2", 900 + k)
        stepper.run(0.1, [session])
    one, two = board.lines["sim-1"], board.lines["sim-2"]
    assert (one.hits, one.downs, one.damage) == (5, 1, 100) and two.deaths == 1 and two.crashes == 0
    assert two.alive_since is None and two.best_alive_s > 1.0
    # a crash that is not a shoot-down counts as a crash
    svc.vehicles["sim-1"].driver.sim.shoot_down("collision")
    stepper.run(0.1, [session])
    assert board.lines["sim-1"].crashes == 1 and board.lines["sim-1"].deaths == 0
    rows = board.leaderboard("downs")["rows"]
    assert [r["key"] for r in rows] == ["sim-1", "sim-2"]
    first = rows[0]
    assert first["local"] and first["aircraft"] == "SIM-1" and first["callsign"] == svc.arena.callsign
    assert first["accuracy"] == 1.0 and first["downs"] == 1 and first["controller"]["kind"] == "agent"
    assert first["controller"]["name"] == "agent" and first["alive"] is False
    Leaderboard.model_validate(board.leaderboard("survival"))


def test_rams_between_local_aircraft_come_from_the_collision_events(svc, stepper):
    svc.add_sims(2)
    stepper.run(2.5)
    join(svc, stepper)
    stepper.run(0.3)
    svc.events.emit(
        "collision",
        "sim-1",
        data={"a": "sim-1", "b": "sim-2", "point": [0, 0, 1], "v_rel": 3.1, "crashed": ["sim-1", "sim-2"]},
    )
    svc.events.emit(
        "collision", "sim-1", data={"a": "sim-1", "b": "ring:2", "crashed": ["sim-1"], "kind": "ring"}
    )
    board = svc.arena.board
    assert board.lines["sim-1"].rams == 1 and board.lines["sim-2"].rams == 1
    rows = board.leaderboard("rams")["rows"]
    assert rows[0]["rams"] == 1


def test_a_downed_aircraft_keeps_its_program_as_the_controller(svc, stepper, monkeypatch):
    svc.add_sims(1)
    stepper.run(2.5)
    join(svc, stepper)
    stepper.run(0.3)
    board = svc.arena.board
    sims = {"sim-1": svc.vehicles["sim-1"]}  # on the ground: not alive
    who = {"now": ("program", "Rammer r1")}
    monkeypatch.setattr(boardmod, "controller", lambda fleet, v: who["now"])
    board._check_controllers(sims)
    # the crash stopped the program and the dogfight session holds the claim until the respawn
    who["now"] = ("agent", "dogfight")
    board._check_controllers(sims)
    (row,) = [r for r in board.leaderboard()["rows"] if r["key"] == "sim-1"]
    assert row["controller"] == {"kind": "program", "name": "Rammer r1"}
    who["now"] = ("human", "keyboard")  # a person takes over: shown at once
    board._check_controllers(sims)
    (row,) = [r for r in board.leaderboard()["rows"] if r["key"] == "sim-1"]
    assert row["controller"]["kind"] == "human"
    # a finished match keeps the controllers that flew it (the aircraft land under the scenario's session)
    monkeypatch.setattr(type(board), "frozen", property(lambda self: True))
    who["now"] = ("agent", "dogfight")
    board._check_controllers(sims)
    (row,) = [r for r in board.leaderboard()["rows"] if r["key"] == "sim-1"]
    assert row["controller"]["kind"] == "human"


def test_sorting_and_empty_rows_last():
    rows = [
        boardmod._row("a", "SIM-1", "A", True, "none", "", (2, 4, 10, 0, 1, 0, 80), 0.0, 30.0, False),
        boardmod._row("b", "SIM-2", "A", True, "none", "", (2, 5, 5, 3, 0, 1, 100), 12.0, 12.0, True),
        boardmod._row("c", "SIM-3", "B", False, "none", "", None, None, None, None),
        boardmod._row("d", "SIM-4", "A", True, "none", "", (0, 0, 0, 1, 0, 0, 0), 50.0, 50.0, True),
    ]
    order = {
        by: [r["key"] for r in sorted(rows, key=lambda r: boardmod._sort_key(r, by))] for by in boardmod.SORTS
    }
    assert order["downs"] == ["b", "a", "d", "c"]  # equal downs: more hits first
    assert order["survival"] == ["d", "a", "b", "c"]
    assert order["accuracy"] == ["b", "a", "d", "c"]  # no shots: no accuracy, after every number
    assert order["rams"] == ["b", "d", "a", "c"]
    assert rows[1]["accuracy"] == 1.0 and rows[3]["accuracy"] is None and rows[2]["downs"] is None


# ---------------------------------------------------------------------------- the merged LAN board


def test_the_board_merges_version_2_peers_and_lists_0_4_peers_without_statistics(svc, stepper):
    svc.add_sims(1)
    stepper.run(2.5)
    peers, _sent = join(svc, stepper)
    feed(svc, peers, peer_state(OLD, seat=4, n=1, callsign="Old"), "10.9.9.10")
    block = wire.Stats(0, False, (stat(1, downs=3, rams=1, best_ms=20_000), stat(2, rams=4, alive_ms=5_000)))
    feed(svc, peers, peer_state(PEER, stats=block))
    stepper.run(0.3)
    board = svc.arena.board.leaderboard("downs")
    keys = [r["key"] for r in board["rows"]]
    assert keys[0] == f"{PEER:016x}/sim-1" and keys[-1] == f"{OLD:016x}/sim-1"
    old = board["rows"][-1]
    assert (
        old["callsign"] == "Old"
        and old["downs"] is None
        and old["controller"] == {"kind": "none", "name": ""}
    )
    bravo = next(r for r in board["rows"] if r["key"] == f"{PEER:016x}/sim-2")
    assert bravo["rams"] == 4 and bravo["alive"] is True and bravo["alive_s"] == 5.0
    assert bravo["controller"] == {"kind": "program", "name": "Hunter (hard) r1"} and not bravo["local"]
    assert svc.arena.board.leaderboard("rams")["rows"][0]["key"] == f"{PEER:016x}/sim-2"
    Leaderboard.model_validate(board)
    # the revision follows counted changes, not the survival clock of a flying aircraft
    rev = svc.arena.board.rev
    later = wire.Stats(0, False, (stat(1, downs=3, rams=1, best_ms=20_000), stat(2, rams=4, alive_ms=5_900)))
    feed(svc, peers, peer_state(PEER, seq=2, mono=1050, stats=later))
    stepper.run(0.3)
    assert svc.arena.board.rev == rev
    more = wire.Stats(0, False, (stat(1, downs=4, rams=1, best_ms=20_000), stat(2, rams=4, alive_ms=6_000)))
    feed(svc, peers, peer_state(PEER, seq=3, mono=1100, stats=more))
    stepper.run(0.3)
    assert svc.arena.board.rev > rev
    view = svc.arena_snapshot()
    assert view["board_rev"] == svc.arena.board.rev and view["match"] is None
    ArenaView.model_validate(view)


def test_peers_in_another_match_show_no_statistics(svc, stepper):
    svc.add_sims(1)
    stepper.run(2.5)
    peers, _sent = join(svc, stepper)
    svc.arena.board.start(120, svc.clock(), broadcast=False)
    feed(svc, peers, peer_state(PEER, stats=wire.Stats(0x1234, True, (stat(1, downs=9),))))
    stepper.run(0.3)
    row = next(r for r in svc.arena.board.leaderboard()["rows"] if not r["local"])
    assert row["downs"] is None


def test_state_carries_the_statistics_block_once_per_second(svc, stepper):
    svc.add_sims(2)
    stepper.run(2.5)
    _peers, sent = join(svc, stepper)
    stepper.run(2.0)
    states = sent["state"]
    with_block = [s for s in states if s.stats is not None]
    assert len(states) >= 38 and 2 <= len(with_block) <= 3
    assert [r.index for r in with_block[-1].stats.records] == [1, 2]
    assert all(s.stats is None for s in states if s not in with_block)


# ---------------------------------------------------------------------------- matches


def test_match_lifecycle_over_the_api(svc, stepper):
    svc.add_sims(1)
    stepper.run(2.5)
    client = TestClient(create_app(svc), base_url="http://127.0.0.1")  # no lifespan: the stepper drives
    assert client.post(f"{API}/arena/match", headers=OP, json={"duration_s": 60}).json()["error"]["code"] == (
        "arena_off"
    )
    assert client.post(f"{API}/arena/match", json={"duration_s": 60}).status_code == 401
    _peers, sent = join(svc, stepper)
    out = client.post(f"{API}/arena/match", headers=OP, json={"duration_s": 60}).json()
    assert out["ended"] is False and out["match"]["running"] is True
    assert out["match"]["ends_at"] - out["match"]["started_at"] == pytest.approx(60.0)
    (start,) = sent["match"]
    assert start.action == wire.MATCH_START and start.duration_s == 60 and start.origin == svc.arena.instance
    assert client.post(f"{API}/arena/match", headers=OP, json={"duration_s": 60}).status_code == 409
    assert client.post(f"{API}/arena/match", headers=OP, json={"duration_s": 10}).status_code == 400
    stepper.run(2.1)
    assert len(sent["match"]) == 2  # repeated every 2 s for late joiners
    board = client.get(f"{API}/arena/leaderboard?by=survival").json()
    assert board["by"] == "survival" and board["match"]["id"] == out["match"]["id"]
    assert client.get(f"{API}/arena/leaderboard?by=bogus").status_code == 400
    assert client.get(f"{API}/arena").json()["match"]["running"] is True
    ended = client.delete(f"{API}/arena/match", headers=OP).json()
    assert ended["ended"] is True and ended["match"]["running"] is False
    assert sent["match"][-1].action == wire.MATCH_END
    assert kinds(svc, "match_finished")[-1]["data"]["reason"] == "stopped"
    reset = client.delete(f"{API}/arena/match", headers=OP).json()
    assert reset == {"match": None, "ended": False}
    # an agent session may start a match only while agents_app_control is on
    _session, _agent = open_agent(svc)
    token = client.post(f"{API}/sessions", json={"name": "bot"}).json()["token"]
    svc.update_settings({"agents_app_control": False})
    res = client.post(f"{API}/arena/match", headers={"X-Relay-Session": token}, json={"duration_s": 60})
    assert res.json()["error"]["code"] == "app_control_disabled"


def test_a_match_runs_out_and_freezes_the_board(svc, stepper):
    svc.add_sims(1)
    stepper.run(2.5)
    join(svc, stepper)
    board = svc.arena.board
    board.start(30, svc.clock(), broadcast=False)
    started = kinds(svc, "match_started")[-1]
    assert started["text_en"] == "Match started for 30 s." and started["text_zh"] == "对局开始，时长 30 s。"
    stepper.run(30.1)
    assert board.match is not None and not board.match.running and board.match.reason == "time"
    assert kinds(svc, "match_finished")[-1]["text_en"].startswith("Match ended: ")
    board.shot("sim-1")
    assert board.lines.get("sim-1") is None or board.lines["sim-1"].shots == 0  # frozen
    board.end(svc.clock())  # no running match: reset
    board.shot("sim-1")
    assert board.match is None and board.lines["sim-1"].shots == 1


def test_match_packets_from_a_peer(svc, stepper):
    svc.add_sims(1)
    stepper.run(2.5)
    peers, sent = join(svc, stepper)
    board = svc.arena.board
    board.shot("sim-1")
    feed(svc, peers, peer_state(PEER, stats=wire.Stats()))
    stepper.run(0.1)
    m = wire.Match(0xAB, wire.MATCH_START, 120, 10_000, PEER)
    feed(svc, peers, wire.encode_match(2, PEER, 1100, m))
    stepper.run(0.6)
    assert board.match is not None and board.match.id == 0xAB and board.match.running
    assert board.match.started_at == pytest.approx(svc.clock() - 10.6, abs=0.02)
    assert "sim-1" not in board.lines or board.lines["sim-1"].shots == 0  # the starter cleared the lines
    assert not sent["match"]  # only the starter repeats it
    # a repeat within the tolerance leaves the start alone; a larger difference follows the starter
    started = board.match.started_at
    feed(svc, peers, wire.encode_match(3, PEER, 1200, wire.Match(0xAB, wire.MATCH_START, 120, 10_900, PEER)))
    stepper.run(0.6)
    assert board.match.started_at == started
    feed(svc, peers, wire.encode_match(4, PEER, 1300, wire.Match(0xAB, wire.MATCH_START, 120, 13_000, PEER)))
    stepper.run(0.6)
    assert board.match.started_at < started - 1.5
    # a start bound to someone else is refused: no peer moves or claims another's match
    other = PEER + 7
    feed(svc, peers, peer_state(other, stats=wire.Stats()), "10.9.9.11")
    stepper.run(0.1)
    started = board.match.started_at
    feed(
        svc,
        peers,
        wire.encode_match(5, other, 1400, wire.Match(0xAB, wire.MATCH_START, 120, 0, PEER)),
        "10.9.9.11",
    )
    stepper.run(0.6)
    assert board.match.id == 0xAB and board.match.started_at == started  # not restarted by a third party
    feed(svc, peers, wire.encode_match(6, PEER, 1400, wire.Match(0xCD, wire.MATCH_START, 120, 1_000, other)))
    stepper.run(0.6)
    assert board.match.id == 0xAB
    # a later competing match loses; one that only claims a much earlier start loses too
    feed(
        svc,
        peers,
        wire.encode_match(7, other, 1500, wire.Match(0xCD, wire.MATCH_START, 120, 1_000, other)),
        "10.9.9.11",
    )
    stepper.run(0.6)
    assert board.match.id == 0xAB
    feed(
        svc,
        peers,
        wire.encode_match(8, other, 1600, wire.Match(0xEF, wire.MATCH_START, 120, 110_000, other)),
        "10.9.9.11",
    )
    stepper.run(0.6)
    assert board.match.id == 0xAB
    # a start at the same moment (here 1 s earlier) wins, as two operators pressing start together
    elapsed = round((svc.clock() - board.match.started_at + 1.0) * 1000)
    feed(
        svc,
        peers,
        wire.encode_match(9, other, 1700, wire.Match(0xEF, wire.MATCH_START, 120, elapsed, other)),
        "10.9.9.11",
    )
    stepper.run(0.6)
    assert board.match.id == 0xEF
    # the end of the match ends it here; the same id does not start again
    feed(
        svc,
        peers,
        wire.encode_match(10, other, 1800, wire.Match(0xEF, wire.MATCH_END, 120, 61_000, other)),
        "10.9.9.11",
    )
    stepper.run(0.6)
    assert not board.match.running and board.match.reason == "remote"
    feed(
        svc,
        peers,
        wire.encode_match(11, other, 1900, wire.Match(0xEF, wire.MATCH_START, 120, 62_000, other)),
        "10.9.9.11",
    )
    stepper.run(0.6)
    assert not board.match.running
    # a new match of the same starter right after: held for the cooldown, then opened on the starter's clock
    feed(
        svc,
        peers,
        wire.encode_match(12, other, 2000, wire.Match(0x99, wire.MATCH_START, 120, 0, other)),
        "10.9.9.11",
    )
    stepper.run(0.6)
    assert not board.match.running
    stepper.run(boardmod.NEW_MATCH_COOLDOWN_S)
    feed(
        svc,
        peers,
        wire.encode_match(13, other, 2100, wire.Match(0x99, wire.MATCH_START, 120, 6_000, other)),
        "10.9.9.11",
    )
    stepper.run(0.6)
    assert board.match.id == 0x99 and board.match.running


def test_a_forged_match_cannot_extend_or_take_over_a_running_one(svc, stepper):
    """After 25 s of a 30 s match a third peer repeats it with elapsed 0, then claims an older one (07 §4)."""
    svc.add_sims(1)
    stepper.run(2.5)
    peers, _sent = join(svc, stepper)
    board = svc.arena.board
    attacker = PEER + 9
    feed(svc, peers, peer_state(PEER, stats=wire.Stats()))
    feed(svc, peers, peer_state(attacker, stats=wire.Stats()), "10.9.9.12")
    stepper.run(0.1)
    feed(svc, peers, wire.encode_match(2, PEER, 1100, wire.Match(0xAB, wire.MATCH_START, 30, 25_000, PEER)))
    stepper.run(0.2)
    board.shot("sim-1")
    left = board.match.ends_at - svc.clock()
    assert left == pytest.approx(5.0, abs=0.3)
    feed(
        svc,
        peers,
        wire.encode_match(3, attacker, 1200, wire.Match(0xAB, wire.MATCH_START, 30, 0, PEER)),
        "10.9.9.12",
    )
    feed(
        svc,
        peers,
        wire.encode_match(4, attacker, 1200, wire.Match(0x1234, wire.MATCH_START, 30, 29_000, attacker)),
        "10.9.9.12",
    )
    stepper.run(0.2)
    assert board.match.id == 0xAB and board.match.ends_at - svc.clock() < left
    assert board.lines["sim-1"].shots == 1  # nobody's board was wiped


def test_match_packets_are_rate_limited(svc, stepper):
    svc.add_sims(1)
    stepper.run(2.5)
    peers, _sent = join(svc, stepper)
    feed(svc, peers, peer_state(PEER))
    stepper.run(0.1)
    board = svc.arena.board
    for k in range(6):
        feed(
            svc,
            peers,
            wire.encode_match(10 + k, PEER, 1100, wire.Match(0x100 + k, wire.MATCH_START, 120, k, PEER)),
        )
    stepper.run(0.1)
    # the bucket holds two: the first starts a match, the second (started 1 ms earlier) waits for the
    # starter's cooldown, the rest drop
    assert board.match.id == 0x100
    assert board._match_rate[PEER][0] < 1.0
