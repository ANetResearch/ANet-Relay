"""Leaderboard and matches (07 §4, WP-B), on the fleet thread as part of the arena.

Statistics: one line per local simulator (``sim-N``): downs (aircraft it brought down with its gun), hits,
shots, accuracy (hits / shots), rams (aircraft that crashed from a collision with it), deaths (shot down),
crashes (any other crash, collisions included), the current and the longest survival time (armed and
airborne without being shot down or crashing) and the damage it dealt. The arena calls the counting hooks
(``shot``, ``hit``, ``death``, ``ram``); crashes and survival are read from the simulators every tick. Hits on
a peer's aircraft are credited when its terminal reports them (HIT), rams when it reports the crash (COLLIDE
with CRASHED), exactly as the target's terminal decides (05 §7.3).

The merged board is the local lines plus each version 2 peer's statistics block (STATE, once per second); a
0.4 peer's aircraft are listed with empty statistics. ``rev`` changes whenever a line, the set of rows or the
match changes (survival times only count when an aircraft takes off or goes down, not every second), so a
client refetches the board only on change.

Matches: ``start`` clears the lines and opens a match of ``duration_s``; on the LAN it sends MATCH start, and
repeats it every 2 s while it runs, so late joiners and lost packets catch up. A terminal that receives a
start clears its lines and follows the starter's clock (its start moved by the packet age, adjusted again when
a repeat differs by more than ``TOLERANCE_S``). Two matches started at once (within ``CONCURRENT_S``): the
earlier start wins (then the smaller starter id). A start is bound to its sender: only the starter sends a
start and its repeats (the packet's origin is the sending peer), a running match is never replaced by a start
that merely claims to be older, and one starter opens at most one new match per ``NEW_MATCH_COOLDOWN_S`` here,
so no peer can extend, take over or keep restarting the others' matches. Any terminal may end a match
(``DELETE /arena/match`` ends it everywhere). When a match ends (its time is up, a MATCH end arrives, or
``end``) the board freezes and keeps the final standings until the next start or ``reset``; outside a match
the lines accumulate.
"""

from __future__ import annotations

import logging
import math
import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import wire

if TYPE_CHECKING:
    from .game import Arena
    from .service import Peer

log = logging.getLogger("relay.arena")

SORTS = ("downs", "survival", "accuracy", "rams")
STATS_EVERY = 20  # STATE packets: the statistics block rides on every 20th (once per second at 20 Hz)
RESEND_S = 2.0
TOLERANCE_S = 0.5
MATCH_RATE = 2.0  # MATCH packets per peer and second
CONCURRENT_S = (
    3.0  # two starts this close are one decision; a start further from the running one never replaces it
)
NEW_MATCH_COOLDOWN_S = 5.0  # new matches accepted from one starter
ENDED_KEEP = 32
CONTROLLER_NAME_MAX = 32
SLOW_PERIOD_S = 0.2  # controllers and the peers' rows are compared at 5 Hz


@dataclass
class Line:
    """Statistics of one local simulator."""

    downs: int = 0
    hits: int = 0
    shots: int = 0
    rams: int = 0
    deaths: int = 0
    crashes: int = 0
    damage: int = 0
    alive_since: float | None = None
    best_alive_s: float = 0.0

    def alive_s(self, now: float) -> float:
        return 0.0 if self.alive_since is None else max(0.0, now - self.alive_since)

    def best_s(self, now: float) -> float:
        return max(self.best_alive_s, self.alive_s(now))


@dataclass
class MatchState:
    id: int
    origin: int  # instance id of the starter
    started_at: float  # fleet clock
    duration_s: float
    running: bool = True
    ended_at: float | None = None
    reason: str | None = None  # time | stopped | remote

    @property
    def ends_at(self) -> float:
        return self.started_at + self.duration_s

    def wins_over(self, other: MatchState) -> bool:
        return (self.started_at, self.origin, self.id) < (other.started_at, other.origin, other.id)


@dataclass(eq=False)
class Board:
    arena: Arena
    lines: dict[str, Line] = field(default_factory=dict)
    rev: int = 1
    match: MatchState | None = None
    _seen: dict[str, tuple[bool, bool]] = field(default_factory=dict)  # vid -> (alive, crashed)
    _controllers: dict[str, tuple[str, str]] = field(default_factory=dict)
    _rows_sig: tuple = ()
    _ended: list[int] = field(default_factory=list)
    _match_rate: dict[int, tuple[float, float]] = field(default_factory=dict)
    _opened_by: dict[int, float] = field(default_factory=dict)  # starter instance -> last new match accepted
    _next_resend: float = 0.0
    _next_slow: float = 0.0
    _state_count: int = 0

    # ------------------------------------------------------------------ helpers

    @property
    def fleet(self) -> Any:
        return self.arena.fleet

    @property
    def frozen(self) -> bool:
        """A finished match keeps its final standings."""
        return self.match is not None and not self.match.running

    def _line(self, vid: str) -> Line | None:
        if self.frozen:
            return None
        line = self.lines.get(vid)
        if line is None:
            line = self.lines[vid] = Line()
        return line

    def bump(self) -> None:
        self.rev += 1

    # ------------------------------------------------------------------ counting hooks (arena rules)

    def shot(self, vid: str) -> None:
        line = self._line(vid)
        if line is not None:
            line.shots += 1
            self.bump()

    def hit(self, vid: str, damage: int, down: bool) -> None:
        line = self._line(vid)
        if line is not None:
            line.hits += 1
            line.damage += max(0, int(damage))
            line.downs += 1 if down else 0
            self.bump()

    def death(self, vid: str) -> None:
        line = self._line(vid)
        if line is not None:
            line.deaths += 1
            self.bump()

    def ram(self, vid: str) -> None:
        line = self._line(vid)
        if line is not None:
            line.rams += 1
            self.bump()

    def on_event(self, event: dict[str, Any]) -> None:
        """Rams between two local aircraft, from the ``collision`` events of the local collision world (WP-A).

        A contact with a peer's aircraft is credited through COLLIDE instead (``peers.collisions``)."""
        if event.get("kind") != "collision":
            return
        data = event.get("data") or {}
        if data.get("kind", "craft") != "craft" or data.get("remote"):
            return
        a, b = data.get("a"), data.get("b")
        for vid in data.get("crashed") or ():
            other = b if vid == a else a if vid == b else None
            if isinstance(other, str) and other != vid and other in self.fleet.vehicles:
                self.ram(other)

    # ------------------------------------------------------------------ the tick

    def tick(self, sims: dict[str, Any], now: float) -> None:
        for vid in [k for k in self._seen if k not in sims]:
            del self._seen[vid]
            self._controllers.pop(vid, None)
            self.lines.pop(vid, None)
            self.bump()
        for vid, v in sims.items():
            sim = v.driver.sim
            crashed = bool(sim.quad.s.crashed)
            alive = bool(sim.armed) and v.airborne() and not crashed and not self.arena.is_down(vid)
            was_alive, was_crashed = self._seen.get(vid, (False, crashed))
            self._seen[vid] = (alive, crashed)
            line = self._line(vid)
            if crashed and not was_crashed and not self.arena.is_down(vid) and line is not None:
                line.crashes += 1  # a shoot-down is counted as a death when the hit lands
                self.bump()
            if line is None:
                continue
            if alive and not was_alive:
                line.alive_since = now
                self.bump()
            elif was_alive and not alive and line.alive_since is not None:
                line.best_alive_s = max(line.best_alive_s, now - line.alive_since)
                line.alive_since = None
                self.bump()
            elif not alive and line.alive_since is not None:
                line.alive_since = None  # a new line for an aircraft that was already down
        if now >= self._next_slow:
            self._next_slow = now + SLOW_PERIOD_S
            self._check_controllers(sims)
            self._check_rows()
        m = self.match
        if m is not None and m.running:
            if now >= m.ends_at:
                self._finish(now, "time", broadcast=m.origin == self.arena.instance)
            elif m.origin == self.arena.instance and now >= self._next_resend:
                self._next_resend = now + RESEND_S
                self._send(wire.MATCH_START, now)

    def _check_controllers(self, sims: dict[str, Any]) -> None:
        if self.frozen:
            return  # WP-D: the final standings keep the controllers that flew the match
        for vid, v in sims.items():
            ctrl = controller(self.fleet, v)
            prev = self._controllers.get(vid)
            down = not self._seen.get(vid, (True, False))[0]
            if down and prev is not None and prev[0] == "program" and ctrl[0] == "agent":
                continue  # WP-D: a crash stops the program until the respawn; the line still names it
            if prev != ctrl:
                self._controllers[vid] = ctrl
                self.bump()

    def _check_rows(self) -> None:
        peers = self.arena.peers
        sig: list[Any] = []
        if peers is not None and self.arena.enabled:
            for p in peers.peers.values():
                if not p.joined:
                    continue
                stats = self._peer_stats(p)
                lines = None if stats is None else tuple(_stat_sig(r) for r in stats.records)
                sig.append((p.instance, p.callsign, tuple(sorted(p.aircraft)), lines))
        sig.sort(key=lambda item: item[0])
        out = tuple(sig)
        if out != self._rows_sig:
            self._rows_sig = out
            self.bump()

    # ------------------------------------------------------------------ matches

    def start(self, duration_s: float, now: float, broadcast: bool = True) -> MatchState:
        """Open a match here (and on the LAN when ``broadcast``); clears the lines."""
        match_id = 0
        while match_id == 0:
            match_id = secrets.randbits(32)
        m = MatchState(match_id, self.arena.instance, now, float(duration_s))
        self._open(m, now)
        if broadcast:
            self._next_resend = now + RESEND_S
            self._send(wire.MATCH_START, now)
        return m

    def _open(self, m: MatchState, now: float) -> None:
        self.match = m
        self.lines = {}
        for vid, (alive, _crashed) in self._seen.items():
            if alive:
                self.lines[vid] = Line(alive_since=now)  # a life in progress counts from the start
        self.bump()
        self.fleet.events.emit(
            "match_started",
            data={
                "match": f"{m.id:08x}",
                "duration_s": round(m.duration_s, 1),
                "origin": f"{m.origin:016x}",
                "local": m.origin == self.arena.instance,
            },
            labels={"duration": f"{m.duration_s:g}"},
        )

    def end(self, now: float, broadcast: bool = True) -> bool:
        """``DELETE /arena/match``: end a running match (everywhere when ``broadcast``); otherwise reset."""
        m = self.match
        if m is not None and m.running:
            self._finish(now, "stopped", broadcast=broadcast)
            return True
        self.reset()
        return False

    def reset(self) -> None:
        self.match = None
        self.lines = {}
        now = self.fleet.clock()
        for vid, (alive, _crashed) in self._seen.items():
            if alive:
                self.lines[vid] = Line(alive_since=now)
        self.bump()

    def _finish(self, now: float, reason: str, broadcast: bool) -> None:
        m = self.match
        if m is None or not m.running:
            return
        # the final survival times: every life still going ends with the match
        for line in self.lines.values():
            if line.alive_since is not None:
                line.best_alive_s = max(line.best_alive_s, min(now, m.ends_at) - line.alive_since)
                line.alive_since = None
        m.running = False
        m.ended_at = now
        m.reason = reason
        self._ended.append(m.id)
        del self._ended[:-ENDED_KEEP]
        self.bump()
        if broadcast:
            self._send(wire.MATCH_END, now)
        board = self.leaderboard("downs", now)
        top = next((r for r in board["rows"] if r["downs"] is not None), None)
        leader = f"{top['callsign']} / {top['aircraft']}" if top is not None else "-"
        self.fleet.events.emit(
            "match_finished",
            data={
                "match": f"{m.id:08x}",
                "reason": reason,
                "leader": top["key"] if top is not None else None,
                "detail_en": f"leader {leader}" if top is not None else "no aircraft scored",
                "detail_zh": f"领先 {leader}" if top is not None else "无飞机得分",
            },
        )

    def _send(self, action: int, now: float) -> None:
        peers, m = self.arena.peers, self.match
        if peers is None or not peers.ok or m is None:
            return
        elapsed = max(0.0, min(now, m.ended_at or now) - m.started_at)
        lo, hi = wire.MATCH_DURATION
        duration = max(lo, min(hi, round(m.duration_s)))
        peers.send_match(wire.Match(m.id, action, duration, round(elapsed * 1000.0), m.origin))

    def on_match(self, peer: Peer, packet: wire.Match, age: float, now: float) -> None:
        """A MATCH packet from a joined version 2 peer (rate limited, bounded by the codec)."""
        tokens, at = self._match_rate.get(peer.instance, (MATCH_RATE, now))
        tokens = min(MATCH_RATE, tokens + max(0.0, now - at) * MATCH_RATE)
        if tokens < 1.0:
            self._match_rate[peer.instance] = (tokens, now)
            return
        self._match_rate[peer.instance] = (tokens - 1.0, now)
        if not math.isfinite(age) or age > 2.0:
            return  # far too late to align anything with it
        m = self.match
        if packet.action == wire.MATCH_END:
            if m is not None and m.id == packet.match and m.running:
                self._finish(now, "remote", broadcast=False)
            return
        if packet.origin != peer.instance:
            return  # a start or its repeat comes from its starter only
        started = now - packet.elapsed_ms / 1000.0 - age
        if started + packet.duration_s <= now or packet.match in self._ended:
            return  # over already, or one this terminal has seen end
        if m is not None and m.id == packet.match:
            if m.running and abs(m.started_at - started) > TOLERANCE_S and m.origin == packet.origin:
                m.started_at = started  # follow the starter's clock
                self.bump()
            return
        incoming = MatchState(packet.match, packet.origin, started, float(packet.duration_s))
        if (
            m is not None
            and m.running
            and (not incoming.wins_over(m) or abs(started - m.started_at) > CONCURRENT_S)
        ):
            return  # the running match stays: a later start, or one that only claims an earlier time
        if now - self._opened_by.get(peer.instance, -math.inf) < NEW_MATCH_COOLDOWN_S:
            return  # its repeat opens it once the cooldown is over, on the starter's clock
        self._opened_by[peer.instance] = now
        self._open(incoming, now)

    def forget_peer(self, instance: int) -> None:
        self._match_rate.pop(instance, None)
        self.bump()

    def leave(self, now: float) -> None:
        """The arena closed: a running match ends here only."""
        self._finish(now, "stopped", broadcast=False)

    # ------------------------------------------------------------------ views

    def match_view(self, now: float) -> dict[str, Any] | None:
        m = self.match
        if m is None:
            return None
        wall = self.fleet.wall()
        return {
            "id": f"{m.id:08x}",
            "started_at": round(wall - (now - m.started_at), 3),
            "ends_at": round(wall - (now - m.ends_at), 3),
            "running": m.running,
        }

    def stats_block(self, now: float) -> wire.Stats:
        records = []
        for vid in sorted(self._seen, key=_index_of)[: wire.MAX_AIRCRAFT]:
            index = _index_of(vid)
            if not 1 <= index <= wire.MAX_AIRCRAFT:
                continue
            line = self.lines.get(vid) or Line()
            kind, name = self._controllers.get(vid, ("none", ""))
            records.append(
                wire.Stat(
                    index,
                    kind,
                    name,
                    line.downs,
                    line.hits,
                    line.shots,
                    line.rams,
                    line.deaths,
                    line.crashes,
                    line.damage,
                    round(line.alive_s(now) * 1000.0),
                    round(line.best_s(now) * 1000.0),
                )
            )
        m = self.match
        return wire.Stats(m.id if m is not None else 0, bool(m is not None and m.running), tuple(records))

    def next_state_carries_stats(self) -> bool:
        self._state_count += 1
        return self._state_count % STATS_EVERY == 1

    def _peer_stats(self, peer: Peer) -> wire.Stats | None:
        stats = peer.stats
        if peer.version < wire.VERSION or stats is None:
            return None
        ours = self.match.id if self.match is not None else 0
        return stats if stats.match == ours else None

    def leaderboard(self, by: str = "downs", now: float | None = None) -> dict[str, Any]:
        now = self.fleet.clock() if now is None else now
        if by not in SORTS:
            by = "downs"
        rows: list[dict[str, Any]] = []
        callsign = self.arena.callsign
        for vid in sorted(self._seen, key=_index_of):
            v = self.fleet.vehicles.get(vid)
            if v is None:
                continue
            line = self.lines.get(vid) or Line()
            kind, name = self._controllers.get(vid, ("none", ""))
            rows.append(
                _row(
                    vid,
                    v.label,
                    callsign,
                    True,
                    kind,
                    name,
                    (line.downs, line.hits, line.shots, line.rams, line.deaths, line.crashes, line.damage),
                    line.alive_s(now),
                    line.best_s(now),
                    line.alive_since is not None,
                )
            )
        peers = self.arena.peers
        if peers is not None and self.arena.enabled:
            for p in sorted(peers.peers.values(), key=lambda x: x.instance):
                if not p.joined:
                    continue
                stats = self._peer_stats(p)
                records = {r.index: r for r in stats.records} if stats is not None else {}
                for index in sorted(p.aircraft):
                    key = f"{p.key}/sim-{index}"
                    r = records.get(index)
                    if r is None:
                        rows.append(
                            _row(key, f"SIM-{index}", p.callsign, False, "none", "", None, None, None, None)
                        )
                        continue
                    rows.append(
                        _row(
                            key,
                            f"SIM-{index}",
                            p.callsign,
                            False,
                            r.kind,
                            r.name,
                            (r.downs, r.hits, r.shots, r.rams, r.deaths, r.crashes, r.damage),
                            r.alive_ms / 1000.0,
                            r.best_alive_ms / 1000.0,
                            r.alive_ms > 0,
                        )
                    )
        rows.sort(key=lambda row: _sort_key(row, by))
        return {"by": by, "match": self.match_view(now), "rows": rows}


def _stat_sig(r: wire.Stat) -> tuple:
    """What a peer's line shows, with survival only as alive or not and whole seconds of the best."""
    counts = (r.downs, r.hits, r.shots, r.rams, r.deaths, r.crashes, r.damage)
    return (r.index, r.kind, r.name, *counts, r.alive_ms > 0, r.best_alive_ms // 1000)


def _index_of(vid: str) -> int:
    try:
        return int(vid.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return 0


def controller(fleet: Any, v: Any) -> tuple[str, str]:
    """(kind, name) of who flies ``v``: program name and revision, session name or the operator's input."""
    kind = v.controller(fleet.sessions)
    name = ""
    if kind == "program" and v.program is not None:
        name = f"{v.program.name} r{v.program.rev}"
    elif kind == "agent" and v.auth.claim is not None:
        name = v.auth.claim.name
    elif kind == "human" and v.auth.possession is not None:
        pos = v.auth.possession
        name = f"PAD {pos.pad}" if pos.input == "gamepad" and pos.pad else str(pos.input)
    return kind, wire.clean_text(name, CONTROLLER_NAME_MAX, CONTROLLER_NAME_MAX)


def _row(
    key: str,
    aircraft: str,
    callsign: str,
    local: bool,
    kind: str,
    name: str,
    counts: tuple[int, ...] | None,
    alive_s: float | None,
    best_s: float | None,
    alive: bool | None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "key": key,
        "aircraft": aircraft,
        "callsign": callsign,
        "local": local,
        "controller": {"kind": kind, "name": name},
    }
    names = ("downs", "hits", "shots", "rams", "deaths", "crashes", "damage")
    if counts is None:
        row.update({n: None for n in names})
        row.update(accuracy=None, alive_s=None, best_alive_s=None, alive=None)
        return row
    row.update(dict(zip(names, counts, strict=True)))
    shots, hits = row["shots"], row["hits"]
    row["accuracy"] = round(min(1.0, hits / shots), 3) if shots else None
    row["alive_s"] = round(alive_s or 0.0, 1)
    row["best_alive_s"] = round(best_s or 0.0, 1)
    row["alive"] = bool(alive)
    return row


def _sort_key(row: dict[str, Any], by: str) -> tuple:
    empty = row["downs"] is None
    if empty:
        return (1, 0, 0, 0, row["key"])
    if by == "survival":
        main = (-row["best_alive_s"], -row["alive_s"], -row["downs"])
    elif by == "accuracy":
        acc = row["accuracy"]
        main = (acc is None, -(acc or 0.0), -row["shots"])
    elif by == "rams":
        main = (-row["rams"], -row["downs"], row["crashes"])
    else:
        main = (-row["downs"], -row["hits"], row["deaths"])
    return (0, *main, row["key"])


__all__ = ["SORTS", "Board", "Line", "MatchState", "controller"]
