"""Arena wire codec (05 §7.3, 07 §4): fixed big-endian records, strict bounds, nothing optional.

Every datagram starts with the 12-byte header ``magic "ANRA" · version u8 · type u8 · flags u16 · seq
u32``, followed by ``instance u64`` (the sender's install id) and ``mono_ms u32`` (the sender's monotonic
clock).

Versions (WP-B, 07 §4): the protocol is version 2. Packets that a version 1 terminal (Relay 0.4) understands
keep version 1 on the wire, byte for byte, so 0.4 peers still see and shoot 0.5 terminals: ``STATE`` without
the statistics block, ``SHOT``, ``HIT``, ``PING``, ``PONG`` and ``BYE``. Version 2 carries what is new:
``COLLIDE``, ``MATCH`` and the ``STATE`` that ends with the statistics block (sent once per second in place of
one of the twenty plain ones; a 0.4 peer drops that one as malformed). A peer counts as version 2 from its
first valid version 2 packet; collisions and the leaderboard are exchanged only between version 2 peers.

Bodies:

- ``STATE`` (20 Hz): callsign 16 bytes UTF-8 (zero padded), seat u8 (255 while choosing), rtt_ms u16 (the
  sender's median round trip to its peers, 0xFFFF unknown), hits / downs / deaths u16, aircraft count u8 (<=
  8), shot count u8 (<= 4), the aircraft (32 bytes each), then the redundant copies of the last shots (33
  bytes each).
- aircraft record: index u8 (1..8, the ``sim-N`` number), flags u8 (ARMED / DOWN / ASSIST / RELOADING), hit
  points u8, reserved u8, position 3 x f32 (sender-local sim frame, ENU m), velocity 3 x i16 (cm/s), roll /
  pitch / yaw 3 x i16 (0.01 deg), throttle u8 (1/255), ammunition u8, padding u16.
- ``SHOT``: one shot record: shooter index u8, bullet id u32, fire time u32 (sender mono_ms), position 3 x f32
  and velocity 3 x f32 in arena coordinates.
- ``HIT`` (sent by the owner of the aircraft that was hit): shooter instance u64, bullet id u32, target index
  u8, damage u8, down u8, hit point 3 x f32 (arena coordinates).
- ``PING``: header only. ``PONG``: pinger instance u64, the PING's mono_ms u32, hold u16 (ms between receipt
  and reply). ``BYE``: header only.
- statistics block (version 2 ``STATE`` only, after the shots): match id u32 (0 outside a match), match flags
  u8 (RUNNING), record count u8 (<= 8), then 56-byte records: index u8, controller kind u8 (0 none, 1 program,
  2 agent, 3 human), controller name 32 bytes UTF-8 (zero padded, plain text), downs / hits / shots / rams /
  deaths / crashes / damage 7 x u16, alive_ms and best_alive_ms 2 x u32.
- ``COLLIDE`` (version 2, sent by a terminal that applied a contact between its aircraft and a peer's):
  to instance u64 (the peer that flies the other aircraft), contact id u32, target index u8 (that peer's
  aircraft), own index u8 (the sender's aircraft), flags u8 (CRASHED: the sender's aircraft crashed from this
  contact; REPLY: an answer to a COLLIDE, never answered again), contact time u32 (sender mono_ms), point 3 x
  f32 and the sender's velocity 3 x f32 (arena coordinates), closing speed f32 (m/s, >= 0).
- ``MATCH`` (version 2): match id u32 (non-zero), action u8 (1 start, 2 end), duration u16 (s, 30..1800),
  elapsed u32 (ms since the start on the sender's clock), origin instance u64 (the terminal that started it).

``decode`` raises ``WireError`` for anything that is not exactly one of these: wrong magic, version or length,
an unknown type, a count above its cap, a non-finite float, a coordinate beyond 1000 m, a speed beyond 200
m/s, an index outside 1..8, more than 100 hit points or damage, a version 2 type in a version 1 packet, an
unknown controller kind or flag, a survival time above one day, a match outside its bounds. Callers drop such
packets without logging.
"""

from __future__ import annotations

import math
import struct
import unicodedata
from dataclasses import dataclass, field

MAGIC = b"ANRA"
VERSION = 2  # WP-B (07 §4); version 1 framing stays for the packets a 0.4 peer understands
LEGACY_VERSION = 1
MAX_PACKET = 1200
MAX_AIRCRAFT = 8
MAX_STATE_SHOTS = 4
MAX_COORD = 1000.0
MAX_SPEED = 200.0
MAX_HP = 100
CALLSIGN_BYTES = 16
CALLSIGN_CHARS = 16
SEAT_NONE = 255
RTT_UNKNOWN = 0xFFFF

STATE, SHOT, HIT, BYE, PING, PONG = 1, 2, 3, 4, 5, 6
COLLIDE, MATCH = 7, 8  # WP-B (07 §4): version 2 only
TYPES = {
    STATE: "STATE",
    SHOT: "SHOT",
    HIT: "HIT",
    BYE: "BYE",
    PING: "PING",
    PONG: "PONG",
    COLLIDE: "COLLIDE",
    MATCH: "MATCH",
}

# aircraft flags
ARMED, DOWN, ASSIST, RELOADING = 0x01, 0x02, 0x04, 0x08
# WP-B (07 §4): statistics block, COLLIDE and MATCH
MATCH_RUNNING = 0x01
COLLIDE_CRASHED, COLLIDE_REPLY = 0x01, 0x02
MATCH_START, MATCH_END = 1, 2
MATCH_DURATION = (30, 1800)
CONTROLLER_KINDS = ("none", "program", "agent", "human")
CONTROLLER_BYTES = 32
MAX_ALIVE_MS = 86_400_000

_HEAD = struct.Struct(">4sBBHIQI")
_STATE = struct.Struct(">16sBHHHHBB")
_AIRCRAFT = struct.Struct(">BBBB3f3h3hBBH")
_SHOT = struct.Struct(">BII3f3f")
_HIT = struct.Struct(">QIBBB3f")
_PONG = struct.Struct(">QIH")
_STATS = struct.Struct(">IBB")
_STAT = struct.Struct(">BB32s7HII")
_COLLIDE = struct.Struct(">QIBBBI3f3ff")
_MATCH = struct.Struct(">IBHIQ")
HEADER_SIZE = _HEAD.size
AIRCRAFT_SIZE = _AIRCRAFT.size
SHOT_SIZE = _SHOT.size
STAT_SIZE = _STAT.size
assert HEADER_SIZE == 24 and AIRCRAFT_SIZE == 32 and SHOT_SIZE == 33
assert STAT_SIZE == 56 and _COLLIDE.size == 47 and _MATCH.size == 19


class WireError(ValueError):
    """The datagram is not a valid arena packet."""


@dataclass(frozen=True, slots=True)
class Header:
    type: int
    flags: int
    seq: int
    instance: int
    mono_ms: int
    version: int = LEGACY_VERSION  # WP-B


@dataclass(frozen=True, slots=True)
class Aircraft:
    index: int
    flags: int
    hp: int
    p: tuple[float, float, float]
    v: tuple[float, float, float]  # m/s
    att: tuple[float, float, float]  # roll, pitch, yaw in radians
    throttle: float  # 0..1
    ammo: int

    @property
    def armed(self) -> bool:
        return bool(self.flags & ARMED)

    @property
    def down(self) -> bool:
        return bool(self.flags & DOWN)


@dataclass(frozen=True, slots=True)
class Shot:
    shooter: int  # aircraft index of the shooter
    bullet: int
    t0_ms: int  # sender mono_ms at the moment the bullet left
    p: tuple[float, float, float]  # arena coordinates
    v: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class Stat:
    """One aircraft's leaderboard line in the statistics block (WP-B, 07 §4)."""

    index: int
    kind: str  # none | program | agent | human
    name: str  # controller name, plain text, at most 32 characters
    downs: int = 0
    hits: int = 0
    shots: int = 0
    rams: int = 0
    deaths: int = 0
    crashes: int = 0
    damage: int = 0
    alive_ms: int = 0
    best_alive_ms: int = 0


@dataclass(frozen=True, slots=True)
class Stats:
    match: int = 0  # match id, 0 outside a match
    running: bool = False
    records: tuple[Stat, ...] = ()


@dataclass(frozen=True, slots=True)
class State:
    callsign: str
    seat: int | None
    rtt_ms: int | None
    hits: int
    downs: int
    deaths: int
    aircraft: tuple[Aircraft, ...] = ()
    shots: tuple[Shot, ...] = ()
    stats: Stats | None = None  # WP-B: version 2 only; None in a version 1 STATE


@dataclass(frozen=True, slots=True)
class Hit:
    shooter_instance: int
    bullet: int
    target: int
    damage: int
    down: bool
    point: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class Pong:
    to_instance: int
    echo_ms: int
    hold_ms: int


@dataclass(frozen=True, slots=True)
class Collide:
    """A contact the sender applied to its aircraft ``own`` against the receiver's aircraft ``target``."""

    to_instance: int
    contact: int
    target: int
    own: int
    flags: int
    t_ms: int  # sender mono_ms at the contact
    point: tuple[float, float, float]  # arena coordinates
    v: tuple[float, float, float]  # the sender's aircraft velocity, arena coordinates
    closing: float  # m/s along the contact normal, as the sender saw it

    @property
    def crashed(self) -> bool:
        return bool(self.flags & COLLIDE_CRASHED)

    @property
    def reply(self) -> bool:
        return bool(self.flags & COLLIDE_REPLY)


@dataclass(frozen=True, slots=True)
class Match:
    match: int
    action: int  # MATCH_START | MATCH_END
    duration_s: int
    elapsed_ms: int  # since the start, on the sender's clock when it sent the packet
    origin: int  # instance id of the terminal that started the match


@dataclass(frozen=True, slots=True)
class Packet:
    header: Header
    body: State | Shot | Hit | Pong | Collide | Match | None = field(default=None)

    @property
    def type(self) -> int:
        return self.header.type


# ---------------------------------------------------------------------------- text


def clean_callsign(text: str) -> str:
    """Control and format characters removed, whitespace collapsed, at most 16 characters and 16 bytes."""
    kept = "".join(c for c in text if c.isspace() or not unicodedata.category(c).startswith("C"))
    out = " ".join(kept.split())[:CALLSIGN_CHARS]
    while len(out.encode("utf-8")) > CALLSIGN_BYTES:
        out = out[:-1]
    return out.strip()


def clean_text(text: str, chars: int = CONTROLLER_BYTES, limit: int = CONTROLLER_BYTES) -> str:
    """``clean_callsign`` with other bounds (controller names: 32 characters and 32 bytes)."""
    kept = "".join(c for c in text if c.isspace() or not unicodedata.category(c).startswith("C"))
    out = " ".join(kept.split())[:chars]
    while len(out.encode("utf-8")) > limit:
        out = out[:-1]
    return out.strip()


# ---------------------------------------------------------------------------- checks


def _finite(values: tuple[float, ...], limit: float) -> tuple[float, ...]:
    for x in values:
        if not math.isfinite(x) or abs(x) > limit:
            raise WireError("number out of range")
    return values


def _index(i: int) -> int:
    if not 1 <= i <= MAX_AIRCRAFT:
        raise WireError("aircraft index")
    return i


def _i16(value: float, scale: float) -> int:
    if not math.isfinite(value):
        return 0
    return max(-32767, min(32767, round(value * scale)))


def _u8(value: float) -> int:
    return max(0, min(255, round(value)))


# ---------------------------------------------------------------------------- encode


def header(
    kind: int, seq: int, instance: int, mono_ms: int, flags: int = 0, version: int = LEGACY_VERSION
) -> bytes:
    return _HEAD.pack(MAGIC, version, kind, flags & 0xFFFF, seq & 0xFFFFFFFF, instance, mono_ms & 0xFFFFFFFF)


def _shot_record(s: Shot) -> bytes:
    return _SHOT.pack(s.shooter, s.bullet & 0xFFFFFFFF, s.t0_ms & 0xFFFFFFFF, *s.p, *s.v)


def encode_state(seq: int, instance: int, mono_ms: int, state: State) -> bytes:
    aircraft = state.aircraft[:MAX_AIRCRAFT]
    shots = state.shots[-MAX_STATE_SHOTS:]
    name = clean_callsign(state.callsign).encode("utf-8")
    version = LEGACY_VERSION if state.stats is None else VERSION
    parts = [
        header(STATE, seq, instance, mono_ms, version=version),
        _STATE.pack(
            name,
            SEAT_NONE if state.seat is None else state.seat,
            RTT_UNKNOWN if state.rtt_ms is None else max(0, min(RTT_UNKNOWN - 1, state.rtt_ms)),
            min(0xFFFF, state.hits),
            min(0xFFFF, state.downs),
            min(0xFFFF, state.deaths),
            len(aircraft),
            len(shots),
        ),
    ]
    for a in aircraft:
        parts.append(
            _AIRCRAFT.pack(
                a.index,
                a.flags & 0xFF,
                max(0, min(MAX_HP, a.hp)),
                0,
                *(max(-MAX_COORD, min(MAX_COORD, x)) for x in a.p),
                *(_i16(x, 100.0) for x in a.v),
                *(_i16(math.degrees(x), 100.0) for x in a.att),
                _u8(a.throttle * 255.0),
                max(0, min(255, a.ammo)),
                0,
            )
        )
    parts.extend(_shot_record(s) for s in shots)
    if state.stats is not None:
        parts.append(_stats_block(state.stats))
    return b"".join(parts)


def _u16(n: int) -> int:
    return max(0, min(0xFFFF, int(n)))


def _stats_block(stats: Stats) -> bytes:
    records = stats.records[:MAX_AIRCRAFT]
    parts = [_STATS.pack(stats.match & 0xFFFFFFFF, MATCH_RUNNING if stats.running else 0, len(records))]
    for r in records:
        kind = CONTROLLER_KINDS.index(r.kind) if r.kind in CONTROLLER_KINDS else 0
        parts.append(
            _STAT.pack(
                r.index,
                kind,
                clean_text(r.name).encode("utf-8"),
                *(_u16(n) for n in (r.downs, r.hits, r.shots, r.rams, r.deaths, r.crashes, r.damage)),
                max(0, min(MAX_ALIVE_MS, int(r.alive_ms))),
                max(0, min(MAX_ALIVE_MS, int(r.best_alive_ms))),
            )
        )
    return b"".join(parts)


def encode_collide(seq: int, instance: int, mono_ms: int, c: Collide) -> bytes:
    body = _COLLIDE.pack(
        c.to_instance,
        c.contact & 0xFFFFFFFF,
        c.target,
        c.own,
        c.flags & 0xFF,
        c.t_ms & 0xFFFFFFFF,
        *(max(-MAX_COORD, min(MAX_COORD, x)) for x in c.point),
        *(max(-MAX_SPEED, min(MAX_SPEED, x)) for x in c.v),
        max(0.0, min(MAX_SPEED, c.closing)),
    )
    return header(COLLIDE, seq, instance, mono_ms, version=VERSION) + body


def encode_match(seq: int, instance: int, mono_ms: int, m: Match) -> bytes:
    body = _MATCH.pack(
        m.match & 0xFFFFFFFF,
        m.action,
        max(0, min(0xFFFF, m.duration_s)),
        max(0, min(0xFFFFFFFF, m.elapsed_ms)),
        m.origin,
    )
    return header(MATCH, seq, instance, mono_ms, version=VERSION) + body


def encode_shot(seq: int, instance: int, mono_ms: int, shot: Shot) -> bytes:
    return header(SHOT, seq, instance, mono_ms) + _shot_record(shot)


def encode_hit(seq: int, instance: int, mono_ms: int, hit: Hit) -> bytes:
    body = _HIT.pack(
        hit.shooter_instance,
        hit.bullet & 0xFFFFFFFF,
        hit.target,
        hit.damage,
        1 if hit.down else 0,
        *hit.point,
    )
    return header(HIT, seq, instance, mono_ms) + body


def encode_pong(seq: int, instance: int, mono_ms: int, pong: Pong) -> bytes:
    body = _PONG.pack(pong.to_instance, pong.echo_ms & 0xFFFFFFFF, max(0, min(0xFFFF, pong.hold_ms)))
    return header(PONG, seq, instance, mono_ms) + body


def encode_ping(seq: int, instance: int, mono_ms: int) -> bytes:
    return header(PING, seq, instance, mono_ms)


def encode_bye(seq: int, instance: int, mono_ms: int) -> bytes:
    return header(BYE, seq, instance, mono_ms)


# ---------------------------------------------------------------------------- decode


def _decode_shot(data: bytes, offset: int) -> Shot:
    shooter, bullet, t0, *rest = _SHOT.unpack_from(data, offset)
    p = _finite(tuple(rest[:3]), MAX_COORD)
    v = _finite(tuple(rest[3:]), MAX_SPEED)
    return Shot(_index(shooter), bullet, t0, p, v)  # type: ignore[arg-type]


def _decode_state(data: bytes, version: int = LEGACY_VERSION) -> State:
    off = HEADER_SIZE
    if len(data) < off + _STATE.size:
        raise WireError("length")
    raw_name, seat, rtt, hits, downs, deaths, n_air, n_shot = _STATE.unpack_from(data, off)
    if n_air > MAX_AIRCRAFT or n_shot > MAX_STATE_SHOTS:
        raise WireError("count")
    if seat != SEAT_NONE and seat >= 12:
        raise WireError("seat")
    end = off + _STATE.size + n_air * AIRCRAFT_SIZE + n_shot * SHOT_SIZE
    if version == LEGACY_VERSION and len(data) != end:
        raise WireError("length")
    if version != LEGACY_VERSION and len(data) < end + _STATS.size:
        raise WireError("length")
    off += _STATE.size
    aircraft = []
    seen: set[int] = set()
    for _ in range(n_air):
        index, flags, hp, _reserved, *rest = _AIRCRAFT.unpack_from(data, off)
        off += AIRCRAFT_SIZE
        _index(index)
        if index in seen:
            raise WireError("duplicate aircraft")
        seen.add(index)
        if hp > MAX_HP:
            raise WireError("hit points")
        p = _finite(tuple(rest[0:3]), MAX_COORD)
        v = tuple(x / 100.0 for x in rest[3:6])
        att = tuple(math.radians(x / 100.0) for x in rest[6:9])
        throttle, ammo = rest[9] / 255.0, rest[10]
        aircraft.append(Aircraft(index, flags, hp, p, v, att, throttle, ammo))  # type: ignore[arg-type]
    shots = []
    for _ in range(n_shot):
        shots.append(_decode_shot(data, off))
        off += SHOT_SIZE
    stats = _decode_stats(data, off) if version != LEGACY_VERSION else None
    name = clean_callsign(raw_name.rstrip(b"\0").decode("utf-8", "replace"))
    return State(
        callsign=name,
        seat=None if seat == SEAT_NONE else seat,
        rtt_ms=None if rtt == RTT_UNKNOWN else rtt,
        hits=hits,
        downs=downs,
        deaths=deaths,
        aircraft=tuple(aircraft),
        shots=tuple(shots),
        stats=stats,
    )


def _decode_stats(data: bytes, off: int) -> Stats:
    match, flags, count = _STATS.unpack_from(data, off)
    if flags & ~MATCH_RUNNING or count > MAX_AIRCRAFT:
        raise WireError("stats")
    off += _STATS.size
    if len(data) != off + count * STAT_SIZE:
        raise WireError("length")
    records = []
    seen: set[int] = set()
    for _ in range(count):
        index, kind, raw, *rest = _STAT.unpack_from(data, off)
        off += STAT_SIZE
        _index(index)
        if index in seen:
            raise WireError("duplicate stats")
        seen.add(index)
        if kind >= len(CONTROLLER_KINDS):
            raise WireError("controller kind")
        counts, alive, best = rest[:7], rest[7], rest[8]
        if alive > MAX_ALIVE_MS or best > MAX_ALIVE_MS:
            raise WireError("survival time")
        name = clean_text(raw.rstrip(b"\0").decode("utf-8", "replace"))
        records.append(Stat(index, CONTROLLER_KINDS[kind], name, *counts, alive, best))  # type: ignore[arg-type]
    return Stats(match, bool(flags & MATCH_RUNNING), tuple(records))


def _decode_collide(data: bytes) -> Collide:
    if len(data) != HEADER_SIZE + _COLLIDE.size:
        raise WireError("length")
    to, contact, target, own, flags, t_ms, *rest = _COLLIDE.unpack_from(data, HEADER_SIZE)
    if to == 0 or flags & ~(COLLIDE_CRASHED | COLLIDE_REPLY):
        raise WireError("collide")
    point = _finite(tuple(rest[0:3]), MAX_COORD)
    v = _finite(tuple(rest[3:6]), MAX_SPEED)
    closing = rest[6]
    if not math.isfinite(closing) or not 0.0 <= closing <= MAX_SPEED:
        raise WireError("collide")
    return Collide(to, contact, _index(target), _index(own), flags, t_ms, point, v, closing)  # type: ignore[arg-type]


def _decode_match(data: bytes) -> Match:
    if len(data) != HEADER_SIZE + _MATCH.size:
        raise WireError("length")
    match, action, duration, elapsed, origin = _MATCH.unpack_from(data, HEADER_SIZE)
    if match == 0 or origin == 0 or action not in (MATCH_START, MATCH_END):
        raise WireError("match")
    lo, hi = MATCH_DURATION
    if not lo <= duration <= hi or elapsed > (duration + 60) * 1000:
        raise WireError("match")
    return Match(match, action, duration, elapsed, origin)


def decode(data: bytes) -> Packet:
    """Parse one datagram. Raises WireError unless it is exactly a valid packet."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise WireError("type")
    data = bytes(data)
    if len(data) < HEADER_SIZE or len(data) > MAX_PACKET:
        raise WireError("length")
    magic, version, kind, flags, seq, instance, mono_ms = _HEAD.unpack_from(data, 0)
    if magic != MAGIC or version not in (LEGACY_VERSION, VERSION):
        raise WireError("magic")
    if instance == 0:
        raise WireError("instance")
    head = Header(kind, flags, seq, instance, mono_ms, version)
    body: State | Shot | Hit | Pong | Collide | Match | None
    if version == VERSION and kind not in (STATE, COLLIDE, MATCH):
        raise WireError("version")  # WP-B: everything else keeps the version 1 framing
    if kind == STATE:
        body = _decode_state(data, version)
    elif kind == COLLIDE and version == VERSION:
        body = _decode_collide(data)
    elif kind == MATCH and version == VERSION:
        body = _decode_match(data)
    elif kind == SHOT:
        if len(data) != HEADER_SIZE + SHOT_SIZE:
            raise WireError("length")
        body = _decode_shot(data, HEADER_SIZE)
    elif kind == HIT:
        if len(data) != HEADER_SIZE + _HIT.size:
            raise WireError("length")
        shooter, bullet, target, damage, down, *point = _HIT.unpack_from(data, HEADER_SIZE)
        if shooter == 0 or damage > MAX_HP or down > 1:
            raise WireError("hit")
        body = Hit(shooter, bullet, _index(target), damage, bool(down), _finite(tuple(point), MAX_COORD))  # type: ignore[arg-type]
    elif kind == PONG:
        if len(data) != HEADER_SIZE + _PONG.size:
            raise WireError("length")
        to, echo, hold = _PONG.unpack_from(data, HEADER_SIZE)
        body = Pong(to, echo, hold)
    elif kind in (PING, BYE):
        if len(data) != HEADER_SIZE:
            raise WireError("length")
        body = None
    else:
        raise WireError("type")
    return Packet(head, body)
