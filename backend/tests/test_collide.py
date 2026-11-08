"""Collision volumes and contact rules (07 §2, WP-A): the pure functions of ``sim.collide``."""

import math

import pytest

from relay.sim import collide
from relay.sim.collide import CRAFT_RADIUS, RING_TUBE, response, sweep_spheres, sweep_torus
from relay.sim.physics import IXX, IYY, IZZ, MASS, euler_quaternion

R2 = 2 * CRAFT_RADIUS


def test_spheres_meet_at_the_first_contact():
    # 1 m apart, closing at 2 m per segment: contact when the gap of 0.7 m is closed
    t = sweep_spheres((0, 0, 1), (1, 0, 1), (1, 0, 1), (0, 0, 1), R2)
    assert t == pytest.approx(0.35)
    assert sweep_spheres((0, 0, 1), (0.1, 0, 1), (1, 0, 1), (1, 0, 1), R2) is None  # not far enough
    assert sweep_spheres((0, 0, 1), (-1, 0, 1), (1, 0, 1), (2, 0, 1), R2) is None  # moving apart
    assert sweep_spheres((0, 0, 1), (0, 0, 1), (0.2, 0, 1), (0.2, 0, 1), R2) == 0.0  # already touching
    assert sweep_spheres((0, 0, 1), (2, 0, 1), (1, 0.31, 1), (1, 0.31, 1), R2) is None  # passes beside


def test_a_fast_step_cannot_tunnel():
    # 15 m/s closing at 250 Hz is 6 cm per step; even a whole metre in one segment is caught
    t = sweep_spheres((0, 0, 1), (0.03, 0, 1), (0.33, 0, 1), (0.30, 0, 1), R2)
    assert t == pytest.approx(0.5, abs=1e-9)
    t = sweep_spheres((-0.5, 0, 1), (0.5, 0, 1), (0.5, 0, 1), (-0.5, 0, 1), R2)
    assert t == pytest.approx(0.35)


RING = {"c": (0.0, 0.0, 1.0), "n": (0.0, 1.0, 0.0), "R": 0.45}


def torus(p0, p1, ring=RING):
    return sweep_torus(p0, p1, ring["c"], ring["n"], ring["R"], RING_TUBE, CRAFT_RADIUS)


def test_ring_clean_pass_misses_the_tube():
    assert torus((0, -1, 1), (0, 1, 1)) is None  # through the middle
    assert torus((0.26, -1, 1), (0.26, 1, 1)) is None  # 0.45 - 0.18 = 0.27 m: the sphere clears
    assert torus((1.0, -1, 1), (1.0, 1, 1)) is None  # well outside


def test_ring_graze_and_head_on():
    hit = torus((0.33, -1, 1), (0.33, 1, 1))  # the sphere overlaps the tube by 6 cm
    assert hit is not None
    t, point, n = hit
    assert 0.0 < t < 0.5
    assert math.dist(point, (0.45, 0.0, 1.0)) == pytest.approx(RING_TUBE, abs=1e-6)  # on the tube surface
    assert n[1] < 0.0 and n[0] < 0.0  # toward the body: in front of the plane and inside the ring
    assert math.hypot(*n) == pytest.approx(1.0)
    t, point, n = torus((0.45, -1, 1), (0.45, 1, 1))  # straight at the tube
    assert n == pytest.approx((0.0, -1.0, 0.0), abs=1e-6)
    assert point == pytest.approx((0.45, -RING_TUBE, 1.0), abs=1e-6)
    assert t == pytest.approx((1.0 - CRAFT_RADIUS - RING_TUBE) / 2.0, abs=1e-3)


def test_ring_contact_in_a_single_fast_step():
    # 20 m/s through the tube in one 4 ms step of 8 cm, contact inside the step
    hit = torus((0.45, -0.20, 1), (0.45, -0.12, 1))
    assert hit is not None and hit[0] == pytest.approx(0.25, abs=0.02)
    assert torus((0.45, 0.5, 1), (0.45, 0.6, 1)) is None


def test_ring_with_any_orientation():
    yaw = math.radians(90.0)
    ring = {"c": (2.0, 3.0, 1.2), "n": (math.sin(yaw), math.cos(yaw), 0.0), "R": 0.4}
    assert torus((1.0, 3.0, 1.2), (3.0, 3.0, 1.2), ring) is None  # along the east-pointing normal
    hit = torus((1.0, 3.0, 1.6), (3.0, 3.0, 1.6), ring)  # at the top of the tube
    assert hit is not None and hit[2][0] < 0.0
    centre, normal, radius = collide.ring_frame({"c": [2.0, 3.0, 1.2], "yaw_deg": 90.0, "r": 0.4})
    assert centre == (2.0, 3.0, 1.2) and normal == pytest.approx((1.0, 0.0, 0.0)) and radius == 0.4


def test_response_restitution_and_threshold():
    n = (1.0, 0.0, 0.0)  # from b to a
    dva, dvb, closing = response((-1.0, 0, 0), (1.0, 0, 0), n, MASS, MASS)
    assert closing == pytest.approx(2.0)
    # equal masses, e = 0.4: each reverses with 40 % of its speed
    assert -1.0 + dva[0] == pytest.approx(0.4) and 1.0 + dvb[0] == pytest.approx(-0.4)
    assert collide.crashes(closing) and not collide.crashes(1.99)
    dva, dvb, closing = response((1.0, 0, 0), (0.0, 0, 0), n, MASS, MASS)
    assert closing == 0.0 and dva == (0.0, 0.0, 0.0)  # separating: nothing
    dva, dvb, closing = response((0, -3.0, 0), (0, 0, 0), (0, 1.0, 0), MASS, math.inf)
    assert dva[1] == pytest.approx(3.0 * 1.4) and dvb == (0.0, 0.0, 0.0)  # a ring does not move


def test_rate_kick_from_the_rim():
    level = euler_quaternion(0.0, 0.0, 0.0)
    inertia = (IXX, IYY, IZZ)
    # a push within the rotor plane passes through the centre: no rotation
    assert collide.rate_kick((1.0, 0, 0), (1.0, 0, 0), level, MASS, inertia) == pytest.approx((0, 0, 0))
    # straight from above (or below) at the centre: none either
    assert collide.rate_kick((0, 0, -1.0), (0, 0, -1.0), level, MASS, inertia) == pytest.approx((0, 0, 0))
    # hit at 45 degrees from the north-east below, heading north: the front-right rim takes it
    n = (-0.5, -0.5, -math.sqrt(0.5))
    p, q, r = collide.rate_kick(tuple(1.0 * c for c in n), n, level, MASS, inertia)
    assert abs(p) > 1.0 and abs(q) > 1.0 and abs(r) < 1e-9
    assert all(abs(c) < 30.0 for c in (p, q, r))  # a scrape, not an instant spin-up
