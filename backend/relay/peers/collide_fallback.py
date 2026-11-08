"""Stand-in for ``relay.sim.collide`` (07 §2, owned by WP-A) on a tree that does not have it yet.

``peers.collisions`` imports ``relay.sim.collide`` first and only falls back to this module when that import
fails, so once WP-A's module is merged this file is unused and can be deleted. The constants and the three
functions follow the shared seam exactly; the formulas are the plain textbook ones (relative-motion sphere
sweep, a sampled and bisected torus sweep, an impulse with restitution along the contact normal).

Conventions: ENU metres and m/s; in ``response`` the normal ``n`` points from body b towards body a, so
``dv_a`` is along ``+n`` and ``dv_b`` along ``-n``; the closing speed is ``-(va - vb) . n``, never negative.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

CRAFT_RADIUS = 0.15
RING_TUBE = 0.03
RAM_CRASH_V = 2.0
RESTITUTION = 0.4

Vec3 = tuple[float, float, float]


def _sub(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def sweep_spheres(
    a0: Sequence[float], a1: Sequence[float], b0: Sequence[float], b1: Sequence[float], r: float
) -> float | None:
    """First contact fraction in [0, 1] of centres moving linearly a0->a1 and b0->b1 (combined radius r)."""
    d0 = _sub(a0, b0)
    dd = _sub(_sub(a1, a0), _sub(b1, b0))
    c = _dot(d0, d0) - r * r
    if c <= 0.0:
        return 0.0
    a = _dot(dd, dd)
    b = 2.0 * _dot(d0, dd)
    if a < 1e-12 or b >= 0.0:
        return None
    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        return None
    t = (-b - math.sqrt(disc)) / (2.0 * a)
    return t if 0.0 <= t <= 1.0 else None


def _torus_frame(normal: Sequence[float]) -> tuple[Vec3, Vec3, Vec3]:
    n = math.sqrt(_dot(normal, normal)) or 1.0
    w = (normal[0] / n, normal[1] / n, normal[2] / n)
    helper = (0.0, 0.0, 1.0) if abs(w[2]) < 0.9 else (1.0, 0.0, 0.0)
    u = (
        helper[1] * w[2] - helper[2] * w[1],
        helper[2] * w[0] - helper[0] * w[2],
        helper[0] * w[1] - helper[1] * w[0],
    )
    un = math.sqrt(_dot(u, u))
    u = (u[0] / un, u[1] / un, u[2] / un)
    v = (w[1] * u[2] - w[2] * u[1], w[2] * u[0] - w[0] * u[2], w[0] * u[1] - w[1] * u[0])
    return u, v, w


def _tube_point(
    p: Sequence[float], centre: Sequence[float], frame: tuple[Vec3, Vec3, Vec3], radius: float
) -> Vec3:
    """The point on the torus's core circle nearest to ``p``."""
    u, v, _w = frame
    d = _sub(p, centre)
    x, y = _dot(d, u), _dot(d, v)
    rho = math.hypot(x, y)
    if rho < 1e-9:
        x, y, rho = 1.0, 0.0, 1.0
    k = radius / rho
    return (
        centre[0] + (u[0] * x + v[0] * y) * k,
        centre[1] + (u[1] * x + v[1] * y) * k,
        centre[2] + (u[2] * x + v[2] * y) * k,
    )


def sweep_torus(
    p0: Sequence[float],
    p1: Sequence[float],
    centre: Sequence[float],
    normal: Sequence[float],
    R: float,
    tube: float,
    body_r: float,
) -> tuple[float, Vec3, Vec3] | None:
    """First contact of a sphere moving p0->p1 with a ring: (fraction, contact point, unit normal to it)."""
    frame = _torus_frame(normal)
    reach = tube + body_r

    def gap(t: float) -> tuple[float, Vec3, Vec3]:
        p = (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t, p0[2] + (p1[2] - p0[2]) * t)
        core = _tube_point(p, centre, frame, R)
        d = _sub(p, core)
        return math.sqrt(_dot(d, d)) - reach, p, core

    length = math.dist(p0, p1)
    steps = max(1, math.ceil(length / max(1e-3, 0.5 * min(tube + body_r, body_r))))
    lo, (g, _p, _c) = 0.0, gap(0.0)
    hit_t: float | None = 0.0 if g <= 0.0 else None
    if hit_t is None:
        for i in range(1, steps + 1):
            t = i / steps
            if gap(t)[0] <= 0.0:
                hi = t
                for _ in range(24):
                    mid = 0.5 * (lo + hi)
                    if gap(mid)[0] <= 0.0:
                        hi = mid
                    else:
                        lo = mid
                hit_t = hi
                break
            lo = t
    if hit_t is None:
        return None
    _g, p, core = gap(hit_t)
    d = _sub(p, core)
    n = math.sqrt(_dot(d, d)) or 1.0
    unit = (d[0] / n, d[1] / n, d[2] / n)
    point = (core[0] + unit[0] * tube, core[1] + unit[1] * tube, core[2] + unit[2] * tube)
    return hit_t, point, unit


def response(
    va: Sequence[float],
    vb: Sequence[float],
    n: Sequence[float],
    ma: float,
    mb: float,
    e: float = RESTITUTION,
) -> tuple[Vec3, Vec3, float]:
    """Velocity changes of a and b and the closing speed along ``n`` (b towards a) for an impulse contact."""
    vn = _dot(_sub(va, vb), n)
    if vn >= 0.0:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 0.0
    closing = -vn
    inv = (1.0 / ma if ma > 0 else 0.0) + (1.0 / mb if mb > 0 else 0.0)
    if inv <= 0.0:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), closing
    j = (1.0 + e) * closing / inv
    ka = j / ma if ma > 0 else 0.0
    kb = j / mb if mb > 0 else 0.0
    return (n[0] * ka, n[1] * ka, n[2] * ka), (-n[0] * kb, -n[1] * kb, -n[2] * kb), closing


__all__ = [
    "CRAFT_RADIUS",
    "RAM_CRASH_V",
    "RESTITUTION",
    "RING_TUBE",
    "response",
    "sweep_spheres",
    "sweep_torus",
]
