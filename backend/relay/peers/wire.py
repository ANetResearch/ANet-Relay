"""Arena wire codec (05 §7.3): fixed big-endian records, strict bounds, nothing optional.

Every datagram starts with the 12-byte header ``magic "ANRA" · version u8 = 1 · type u8 · flags u16 · seq
u32``, followed by ``instance u64`` (the sender's install id) and ``mono_ms u32`` (the sender's monotonic
clock).

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

``decode`` raises ``WireError`` for anything that is not exactly one of these: wrong magic, version or length,
an unknown type, a count above its cap, a non-finite float, a coordinate beyond 1000 m, a speed beyond 200
m/s, an index outside 1..8, more than 100 hit points or damage. Callers drop such packets without logging.
"""

from __future__ import annotations

import math
import struct
import unicodedata
from dataclasses import dataclass, field

MAGIC = b"ANRA"
VERSION = 1
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
TYPES = {STATE: "STATE", SHOT: "SHOT", HIT: "HIT", BYE: "BYE", PING: "PING", PONG: "PONG"}

# aircraft flags
ARMED, DOWN, ASSIST, RELOADING = 0x01, 0x02, 0x04, 0x08

_HEAD = struct.Struct(">4sBBHIQI")
_STATE = struct.Struct(">16sBHHHHBB")
_AIRCRAFT = struct.Struct(">BBBB3f3h3hBBH")
_SHOT = struct.Struct(">BII3f3f")
_HIT = struct.Struct(">QIBBB3f")
_PONG = struct.Struct(">QIH")
HEADER_SIZE = _HEAD.size
AIRCRAFT_SIZE = _AIRCRAFT.size
SHOT_SIZE = _SHOT.size
assert HEADER_SIZE == 24 and AIRCRAFT_SIZE == 32 and SHOT_SIZE == 33


class WireError(ValueError):
    """The datagram is not a valid arena packet."""


@dataclass(frozen=True, slots=True)
class Header:
    type: int
    flags: int
    seq: int
    instance: int
    mono_ms: int


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
class State:
    callsign: str
    seat: int | None
    rtt_ms: int | None
    hits: int
    downs: int
    deaths: int
    aircraft: tuple[Aircraft, ...] = ()
    shots: tuple[Shot, ...] = ()


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
class Packet:
    header: Header
    body: State | Shot | Hit | Pong | None = field(default=None)

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


def header(kind: int, seq: int, instance: int, mono_ms: int, flags: int = 0) -> bytes:
    return _HEAD.pack(MAGIC, VERSION, kind, flags & 0xFFFF, seq & 0xFFFFFFFF, instance, mono_ms & 0xFFFFFFFF)


def _shot_record(s: Shot) -> bytes:
    return _SHOT.pack(s.shooter, s.bullet & 0xFFFFFFFF, s.t0_ms & 0xFFFFFFFF, *s.p, *s.v)


def encode_state(seq: int, instance: int, mono_ms: int, state: State) -> bytes:
    aircraft = state.aircraft[:MAX_AIRCRAFT]
    shots = state.shots[-MAX_STATE_SHOTS:]
    name = clean_callsign(state.callsign).encode("utf-8")
    parts = [
        header(STATE, seq, instance, mono_ms),
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
    return b"".join(parts)


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


def _decode_state(data: bytes) -> State:
    off = HEADER_SIZE
    if len(data) < off + _STATE.size:
        raise WireError("length")
    raw_name, seat, rtt, hits, downs, deaths, n_air, n_shot = _STATE.unpack_from(data, off)
    if n_air > MAX_AIRCRAFT or n_shot > MAX_STATE_SHOTS:
        raise WireError("count")
    if seat != SEAT_NONE and seat >= 12:
        raise WireError("seat")
    if len(data) != off + _STATE.size + n_air * AIRCRAFT_SIZE + n_shot * SHOT_SIZE:
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
    )


def decode(data: bytes) -> Packet:
    """Parse one datagram. Raises WireError unless it is exactly a valid packet."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise WireError("type")
    data = bytes(data)
    if len(data) < HEADER_SIZE or len(data) > MAX_PACKET:
        raise WireError("length")
    magic, version, kind, flags, seq, instance, mono_ms = _HEAD.unpack_from(data, 0)
    if magic != MAGIC or version != VERSION:
        raise WireError("magic")
    if instance == 0:
        raise WireError("instance")
    head = Header(kind, flags, seq, instance, mono_ms)
    body: State | Shot | Hit | Pong | None
    if kind == STATE:
        body = _decode_state(data)
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
