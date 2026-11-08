"""Collision volumes and contact rules (07 §2): pure functions, no I/O.

Every collision judgement uses this module: the fleet's collision world (``sim.world``) for live flight
and the evaluator, and the LAN arena for remote aircraft. Coordinates are ENU metres, velocities m/s.

Volumes: an aircraft is a sphere of ``CRAFT_RADIUS`` (25 cm wheelbase and the propeller discs); a ring is a
torus with the ring's radius as major radius and ``RING_TUBE`` as tube radius, its axis the ring normal; the
ground is the plane z = 0 (``physics.Quad`` judges touchdown itself). Detection is a sweep over each physics
step (the segment from the previous position to the new one), so speed never tunnels through a volume.

Response: the closing speed along the contact normal decides. At or above ``RAM_CRASH_V`` the contact
destroys the aircraft (both aircraft in an aircraft-to-aircraft contact); below it an impulse with
restitution ``RESTITUTION`` separates the bodies, and ``rate_kick`` turns the part of the impulse that acts
off the centre of mass (the flat frame takes it at its rim) into a body-rate change.
"""

from __future__ import annotations

import math

Vec3 = tuple[float, float, float]

CRAFT_RADIUS = 0.15
RING_TUBE = 0.03
RAM_CRASH_V = 2.0
RESTITUTION = 0.4
CONTACT_SLOP = 1e-6
_MAX_ITER = 64


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def sweep_spheres(a0: Vec3, a1: Vec3, b0: Vec3, b1: Vec3, r: float) -> float | None:
    """First contact fraction in [0, 1] of two centres moving linearly a0 -> a1 and b0 -> b1.

    ``r`` is the combined radius. Already in contact at the start gives 0.0; no contact gives None.
    """
    dx, dy, dz = a0[0] - b0[0], a0[1] - b0[1], a0[2] - b0[2]
    c = dx * dx + dy * dy + dz * dz - r * r
    if c <= 0.0:
        return 0.0
    ux = (a1[0] - a0[0]) - (b1[0] - b0[0])
    uy = (a1[1] - a0[1]) - (b1[1] - b0[1])
    uz = (a1[2] - a0[2]) - (b1[2] - b0[2])
    a = ux * ux + uy * uy + uz * uz
    b = dx * ux + dy * uy + dz * uz
    if a <= 0.0 or b >= 0.0:
        return None  # not moving relative to each other, or moving apart
    disc = b * b - a * c
    if disc < 0.0:
        return None
    t = (-b - math.sqrt(disc)) / a
    return t if t <= 1.0 else None


def _torus_distance(
    x: Vec3, centre: Vec3, normal: Vec3, R: float
) -> tuple[float, tuple[float, float, float], tuple[float, float, float]]:
    """(distance to the centre circle, nearest point on it, unit vector from it to ``x``)."""
    rx, ry, rz = x[0] - centre[0], x[1] - centre[1], x[2] - centre[2]
    a = rx * normal[0] + ry * normal[1] + rz * normal[2]
    px, py, pz = rx - a * normal[0], ry - a * normal[1], rz - a * normal[2]
    rho = math.sqrt(px * px + py * py + pz * pz)
    if rho < 1e-12:  # on the axis: every point of the circle is as near; pick a stable one
        ref = (1.0, 0.0, 0.0) if abs(normal[0]) < 0.9 else (0.0, 1.0, 0.0)
        k = _dot(ref, normal)
        px, py, pz = ref[0] - k * normal[0], ref[1] - k * normal[1], ref[2] - k * normal[2]
        rho_ref = math.sqrt(px * px + py * py + pz * pz)
        ux, uy, uz = px / rho_ref, py / rho_ref, pz / rho_ref
        rho = 0.0
    else:
        ux, uy, uz = px / rho, py / rho, pz / rho
    q = (centre[0] + R * ux, centre[1] + R * uy, centre[2] + R * uz)
    dx, dy, dz = x[0] - q[0], x[1] - q[1], x[2] - q[2]
    d = math.sqrt(dx * dx + dy * dy + dz * dz)
    if d < 1e-12:
        return 0.0, q, (normal[0], normal[1], normal[2])
    return d, q, (dx / d, dy / d, dz / d)


def sweep_torus(
    p0: Vec3, p1: Vec3, centre: Vec3, normal: Vec3, R: float, tube: float, body_r: float
) -> tuple[float, Vec3, Vec3] | None:
    """First contact of a sphere (radius ``body_r``) moving p0 -> p1 with a torus.

    ``normal`` is the unit ring axis, ``R`` the major radius, ``tube`` the tube radius. Returns
    ``(fraction, contact point on the tube surface, unit normal pointing toward the body)`` or None.
    Conservative advancement on the distance to the centre circle (1-Lipschitz in the position); a graze
    shallower than 1 / 64 of the step length (under a millimetre at 15 m/s) may go unseen.
    """
    reach = tube + body_r
    # Quick rejection: the swept segment never comes within reach of the ring's slab and bounding sphere.
    a0 = _dot((p0[0] - centre[0], p0[1] - centre[1], p0[2] - centre[2]), normal)
    a1 = _dot((p1[0] - centre[0], p1[1] - centre[1], p1[2] - centre[2]), normal)
    if (a0 > reach and a1 > reach) or (a0 < -reach and a1 < -reach):
        return None
    if sweep_spheres(p0, p1, centre, centre, R + reach) is None:
        return None
    ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
    length = math.sqrt(ux * ux + uy * uy + uz * uz)
    t = 0.0
    floor = 1.0 / _MAX_ITER  # at most about a millimetre of a fast step: the loop always reaches the end
    for _ in range(_MAX_ITER + 1):
        x = (p0[0] + ux * t, p0[1] + uy * t, p0[2] + uz * t)
        d, q, n = _torus_distance(x, centre, normal, R)
        gap = d - reach
        if gap <= CONTACT_SLOP:
            point = (q[0] + tube * n[0], q[1] + tube * n[1], q[2] + tube * n[2])
            return t, point, n
        if length <= 0.0:
            return None
        t += max(gap / length, floor)
        if t > 1.0:
            break
    # the end of the segment (advancement can stop just short of a grazing contact at t = 1)
    d, q, n = _torus_distance(p1, centre, normal, R)
    if d - reach <= CONTACT_SLOP:
        return 1.0, (q[0] + tube * n[0], q[1] + tube * n[1], q[2] + tube * n[2]), n
    return None


def response(
    va: Vec3, vb: Vec3, n: Vec3, ma: float, mb: float, e: float = RESTITUTION
) -> tuple[Vec3, Vec3, float]:
    """Impulse response of a contact: ``(dv_a, dv_b, closing speed along n, >= 0)``.

    ``n`` is the unit contact normal pointing from b toward a. ``mb = math.inf`` is an immovable body (a
    ring). Separating bodies (closing speed <= 0) get no impulse.
    """
    closing = -((va[0] - vb[0]) * n[0] + (va[1] - vb[1]) * n[1] + (va[2] - vb[2]) * n[2])
    if closing <= 0.0:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 0.0
    inv_a = 1.0 / ma
    inv_b = 0.0 if math.isinf(mb) else 1.0 / mb
    j = (1.0 + e) * closing / (inv_a + inv_b)
    ka, kb = j * inv_a, j * inv_b
    return (ka * n[0], ka * n[1], ka * n[2]), (-kb * n[0], -kb * n[1], -kb * n[2]), closing


def crashes(closing: float) -> bool:
    """A contact at or above ``RAM_CRASH_V`` along the normal destroys the aircraft."""
    return closing >= RAM_CRASH_V


def rate_kick(
    dv: Vec3,
    n: Vec3,
    q: tuple[float, float, float, float] | list[float],
    mass: float,
    inertia: Vec3,
    radius: float = CRAFT_RADIUS,
) -> Vec3:
    """Body-rate change (p, q, r) from a contact impulse ``mass * dv`` along the normal ``n`` (ENU, toward
    the body) on an aircraft with attitude ``q`` (FRD -> NED quaternion, w x y z) and diagonal ``inertia``.

    The frame is flat: the impulse acts at the rim of the rotor plane on the side of the contact, so a push
    within the rotor plane passes through the centre (no rotation) and one from above or below tilts the
    frame. The kick is the rigid-body angular impulse, reduced by the rotation's share of the effective
    mass at the contact.
    """
    w, x, y, z = q
    # world ENU -> NED -> body FRD (transpose of the FRD -> NED rotation)
    r00 = 1.0 - 2.0 * (y * y + z * z)
    r01 = 2.0 * (x * y - w * z)
    r02 = 2.0 * (x * z + w * y)
    r10 = 2.0 * (x * y + w * z)
    r11 = 1.0 - 2.0 * (x * x + z * z)
    r12 = 2.0 * (y * z - w * x)
    r20 = 2.0 * (x * z - w * y)
    r21 = 2.0 * (y * z + w * x)
    r22 = 1.0 - 2.0 * (x * x + y * y)

    def body(v: Vec3) -> Vec3:
        vn, ve, vd = v[1], v[0], -v[2]
        return (
            r00 * vn + r10 * ve + r20 * vd,
            r01 * vn + r11 * ve + r21 * vd,
            r02 * vn + r12 * ve + r22 * vd,
        )

    nb = body(n)
    jb = body((mass * dv[0], mass * dv[1], mass * dv[2]))
    plane = math.hypot(nb[0], nb[1])
    if plane < 1e-6:
        return (0.0, 0.0, 0.0)
    # contact on the rim, toward the other body (opposite to the normal), in the rotor plane
    rx, ry, rz = -radius * nb[0] / plane, -radius * nb[1] / plane, 0.0
    lx = ry * jb[2] - rz * jb[1]
    ly = rz * jb[0] - rx * jb[2]
    lz = rx * jb[1] - ry * jb[0]
    ix, iy, iz = inertia
    # effective-mass share of the rotation at the contact point (1 / (1 + m (r x n) I^-1 (r x n)))
    cx = ry * nb[2] - rz * nb[1]
    cy = rz * nb[0] - rx * nb[2]
    cz = rx * nb[1] - ry * nb[0]
    share = 1.0 / (1.0 + mass * (cx * cx / ix + cy * cy / iy + cz * cz / iz))
    return (share * lx / ix, share * ly / iy, share * lz / iz)


def ring_frame(ring: dict) -> tuple[Vec3, Vec3, float]:
    """(centre, unit normal, radius) of a course ring ``{c, yaw_deg, r}`` (03 §8: a horizontal normal)."""
    yaw = math.radians(float(ring["yaw_deg"]))
    c = ring["c"]
    return (float(c[0]), float(c[1]), float(c[2])), (math.sin(yaw), math.cos(yaw), 0.0), float(ring["r"])


__all__ = [
    "CRAFT_RADIUS",
    "RAM_CRASH_V",
    "RESTITUTION",
    "RING_TUBE",
    "crashes",
    "rate_kick",
    "response",
    "ring_frame",
    "sweep_spheres",
    "sweep_torus",
]
