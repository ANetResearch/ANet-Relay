"""Bullet flight and hit geometry (05 §7.4). Pure functions; web/src/arena/ballistics.ts implements the same
integrator and both are checked against docs/design/vectors/ballistics.json, so every terminal draws the same
trajectory from the same spawn.

Integrator: fixed step DT = 1/240 s, semi-implicit Euler. Per step, in this order:

    speed = sqrt(vx^2 + vy^2 + vz^2)
    v += (-K * speed * v + (0, 0, -G)) * DT
    p += v * DT

with G = 9.81 m/s^2 and quadratic drag K = 0.015 /m. A bullet lives 2.5 s (600 steps) or until z <= 0.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

DT = 1.0 / 240.0
G = 9.81
K = 0.015
MUZZLE = 35.0
TTL_S = 2.5
TTL_STEPS = 600
HIT_RADIUS = 0.12
SELF_IMMUNE_S = 0.08
DAMAGE = 20
KICK_MAX = 1.5  # m/s, the cap of the hit impulse
IMPULSE_SHARE = 0.25

Vec3 = tuple[float, float, float]


def step(p: Sequence[float], v: Sequence[float]) -> tuple[Vec3, Vec3]:
    """One integrator step."""
    vx, vy, vz = v[0], v[1], v[2]
    speed = math.sqrt(vx * vx + vy * vy + vz * vz)
    vx = vx + (-K * speed * vx) * DT
    vy = vy + (-K * speed * vy) * DT
    vz = vz + (-K * speed * vz - G) * DT
    return (p[0] + vx * DT, p[1] + vy * DT, p[2] + vz * DT), (vx, vy, vz)


def advance(p: Sequence[float], v: Sequence[float], steps: int) -> tuple[Vec3, Vec3]:
    pp: Vec3 = (float(p[0]), float(p[1]), float(p[2]))
    vv: Vec3 = (float(v[0]), float(v[1]), float(v[2]))
    for _ in range(max(0, steps)):
        pp, vv = step(pp, vv)
    return pp, vv


def muzzle(heading: float, pitch: float, aircraft_v: Sequence[float]) -> Vec3:
    """Initial bullet velocity: the nose direction (with pitch) at 35 m/s plus the aircraft's velocity."""
    c = math.cos(pitch)
    return (
        MUZZLE * math.sin(heading) * c + aircraft_v[0],
        MUZZLE * math.cos(heading) * c + aircraft_v[1],
        MUZZLE * math.sin(pitch) + aircraft_v[2],
    )


def sweep(
    p0: Sequence[float], p1: Sequence[float], c0: Sequence[float], c1: Sequence[float], r: float
) -> float | None:
    """First time in [0, 1] at which a point moving p0 -> p1 is within r of a centre moving c0 -> c1.

    Both move linearly over the step, so the test is a segment against a sphere in the target's frame: the
    relative segment (p0 - c0) -> (p1 - c1) against a sphere of radius r at the origin. None on a miss.
    """
    a = [p0[i] - c0[i] for i in range(3)]
    b = [p1[i] - c1[i] for i in range(3)]
    d = [b[i] - a[i] for i in range(3)]
    dd = d[0] * d[0] + d[1] * d[1] + d[2] * d[2]
    ad = a[0] * d[0] + a[1] * d[1] + a[2] * d[2]
    aa = a[0] * a[0] + a[1] * a[1] + a[2] * a[2] - r * r
    if aa <= 0.0:
        return 0.0
    if dd <= 1e-12:
        return None
    disc = ad * ad - dd * aa
    if disc < 0.0:
        return None
    t = (-ad - math.sqrt(disc)) / dd
    return t if 0.0 <= t <= 1.0 else None


def hit_impulse(v_bullet: Sequence[float], v_target: Sequence[float]) -> Vec3:
    """Velocity change of the target: a quarter of the relative bullet velocity, at most 1.5 m/s."""
    dv = [IMPULSE_SHARE * (v_bullet[i] - v_target[i]) for i in range(3)]
    mag = math.sqrt(sum(x * x for x in dv))
    if mag > KICK_MAX:
        dv = [x * KICK_MAX / mag for x in dv]
    return (dv[0], dv[1], dv[2])


def hit_kick(offset: Sequence[float], heading: float, bullet: int) -> tuple[float, float]:
    """Attitude disturbance (roll, pitch) in radians, 6-12 degrees, from where the bullet struck.

    ``offset`` is the hit point relative to the aircraft centre (ENU). A strike on the right side rolls the
    aircraft left, one on the nose pitches it up; the magnitude is fixed per bullet id so it is reproducible.
    """
    fx, fy = math.sin(heading), math.cos(heading)  # nose
    rx, ry = math.cos(heading), -math.sin(heading)  # right wing
    right = offset[0] * rx + offset[1] * ry
    fwd = offset[0] * fx + offset[1] * fy
    norm = math.hypot(right, fwd)
    if norm < 1e-6:
        right, fwd, norm = 0.0, 1.0, 1.0
    mag = math.radians(6.0 + 6.0 * ((bullet * 2654435761) % 1000) / 999.0)
    return (-mag * right / norm, mag * fwd / norm)
