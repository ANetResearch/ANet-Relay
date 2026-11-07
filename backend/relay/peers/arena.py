"""Arena seats and frames (05 §7.2). Pure functions; web/src/arena/frame.ts implements the same formulas
and both are checked against docs/design/vectors/arena.json.

Every terminal keeps its simulators in its own local sim frame (ENU metres, the SIM_SPAWNS grid). The arena
has 12 seats evenly spaced on a 10 m circle; seat k turns the local frame by phi_k = 2 pi k / 12
counter-clockwise about +z and places its origin at T_k = (10 sin phi_k, -10 cos phi_k), so the seat's local
+y points at the centre. A peer's local point maps into this terminal's local frame as

    p_local = R(seat_me)^-1 · (R(seat_peer) · p_peer + T(seat_peer) - T(seat_me))

Compass headings (0 north, clockwise) turn the other way: heading_arena = heading_local - phi.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

SEATS = 12
RADIUS = 10.0
Vec3 = tuple[float, float, float]


def seat_angle(seat: int) -> float:
    if not 0 <= seat < SEATS:
        raise ValueError("seat")
    return 2.0 * math.pi * seat / SEATS


def seat_origin(seat: int) -> tuple[float, float]:
    phi = seat_angle(seat)
    return (RADIUS * math.sin(phi), -RADIUS * math.cos(phi))


def rotate(v: Sequence[float], phi: float) -> Vec3:
    """Rotate an ENU vector counter-clockwise by phi about +z."""
    c, s = math.cos(phi), math.sin(phi)
    return (c * v[0] - s * v[1], s * v[0] + c * v[1], float(v[2]))


def to_arena(seat: int, p: Sequence[float]) -> Vec3:
    x, y, z = rotate(p, seat_angle(seat))
    tx, ty = seat_origin(seat)
    return (x + tx, y + ty, z)


def from_arena(seat: int, p: Sequence[float]) -> Vec3:
    tx, ty = seat_origin(seat)
    return rotate((p[0] - tx, p[1] - ty, p[2]), -seat_angle(seat))


def vec_to_arena(seat: int, v: Sequence[float]) -> Vec3:
    return rotate(v, seat_angle(seat))


def vec_from_arena(seat: int, v: Sequence[float]) -> Vec3:
    return rotate(v, -seat_angle(seat))


def heading_to_arena(seat: int, heading: float) -> float:
    return _wrap(heading - seat_angle(seat))


def heading_from_arena(seat: int, heading: float) -> float:
    return _wrap(heading + seat_angle(seat))


def peer_to_local(p_peer: Sequence[float], seat_peer: int, seat_me: int) -> Vec3:
    """A peer's local point in this terminal's local frame (the formula of 05 §7.2)."""
    return from_arena(seat_me, to_arena(seat_peer, p_peer))


def peer_vec_to_local(v_peer: Sequence[float], seat_peer: int, seat_me: int) -> Vec3:
    return rotate(v_peer, seat_angle(seat_peer) - seat_angle(seat_me))


def peer_heading_to_local(heading: float, seat_peer: int, seat_me: int) -> float:
    return _wrap(heading - seat_angle(seat_peer) + seat_angle(seat_me))


def free_seat(taken: Iterable[int], after: int = -1) -> int | None:
    """Smallest free seat above ``after`` (wrapping round), or None when all twelve are taken."""
    used = set(taken)
    start = 0 if after < 0 else after + 1
    for k in range(SEATS):
        seat = (start + k) % SEATS
        if seat not in used:
            return seat
    return None


def keeps_seat(mine: int, theirs: int) -> bool:
    """Two terminals chose the same seat: the smaller instance id keeps it (05 §7.2)."""
    return mine < theirs


def _wrap(angle: float) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi
