"""Rigid ground contact and crash tumbling (11 §8.3)."""

from __future__ import annotations

import math

import pytest
from conftest import SimPilot

from relay.model import StickInput
from relay.sim import frame as framemod
from relay.sim.physics import DT, Quad


def released(roll_deg: float, height: float, *, rates=(0.0, 0.0, 0.0), v=(0.0, 0.0, 0.0), frame=None) -> Quad:
    quad = Quad(frame=frame)
    s = quad.s
    s.on_ground = s.resting = False
    s.p[2] = height
    s.set_attitude(math.radians(roll_deg), 0.0, 0.4)
    s.rates = list(rates)
    s.v = list(v)
    return quad


def settle(quad: Quad, seconds: float = 5.0) -> dict:
    s = quad.s
    peak_rate = peak_tilt = lowest = 0.0
    for _ in range(int(seconds / DT)):
        quad.step(None, armed=False)
        peak_rate = max(peak_rate, max(abs(r) for r in s.rates))
        peak_tilt = max(peak_tilt, math.degrees(quad.tilt))
        lowest = min(lowest, quad._lowest())
    return {"peak_rate": peak_rate, "peak_tilt": peak_tilt, "lowest": lowest}


@pytest.mark.parametrize("frame", [None, "small-250", "large-450"])
def test_tilted_drop_tumbles_and_rests_not_upright(frame):
    quad = released(135.0, 2.0, rates=(3.0, 1.0, 0.0), frame=framemod.preset(frame) if frame else None)
    out = settle(quad)
    s = quad.s
    assert s.crashed and s.crash_code == "ground" and s.crash_reason.startswith("vertical speed")
    assert s.motor == 0.0 and s.omega == [0.0, 0.0, 0.0, 0.0]
    assert out["peak_rate"] > 3.0  # the contact turned the body
    assert s.resting and s.on_ground and s.v == [0.0, 0.0, 0.0]
    assert math.degrees(quad.tilt) > 120.0  # lying on its back
    assert out["lowest"] > -0.06  # the contact never lets a point sink far into the ground
    # at rest it is held without the contact model and stays exactly where it lies
    q0, p0 = list(s.q), list(s.p)
    quad.step(None, armed=False)
    assert s.q == q0 and s.p == p0
    quad.reset()
    assert quad.s.on_ground and quad.tilt == 0.0 and quad.s.p == [0.0, 0.0, 0.0] and not quad.s.crashed


def test_slow_landing_stays_upright_and_settles_level():
    quad = released(4.0, 0.002, v=(0.1, 0.0, -0.3))
    out = settle(quad, 2.0)
    s = quad.s
    assert not s.crashed and s.resting and s.on_ground
    assert quad.tilt == 0.0 and s.p[2] == 0.0  # level on its legs at height 0
    assert out["peak_tilt"] < 4.5 and out["lowest"] > -0.005  # the legs level it, no bounce


def test_crash_thresholds_keep_their_codes():
    cases = [
        (released(0.0, 0.002, v=(0.0, 0.0, -3.0)), "vertical speed 3.0 m/s"),
        (released(0.0, 0.002, v=(4.0, 0.0, -0.2)), "horizontal speed 4.0 m/s"),
        (released(65.0, 0.15, v=(0.0, 0.0, -0.5)), "tilt"),
    ]
    for quad, reason in cases:
        settle(quad, 3.0)
        s = quad.s
        assert s.crashed and s.crash_code == "ground" and s.crash_reason.startswith(reason), s.crash_reason
        assert s.crash_point is not None and s.crash_point[2] == 0.0 and s.crash_speed > 0.0
        assert s.resting  # the wreck comes to rest
    for quad in (released(0.0, 0.002, v=(0.0, 0.0, -2.0)), released(0.0, 0.002, v=(2.5, 0.0, -0.2))):
        settle(quad, 2.0)
        assert not quad.s.crashed  # below the thresholds


def test_a_bouncing_wreck_keeps_its_first_cause():
    quad = released(0.0, 3.0)
    quad._crash("Shot down", "shot_down")
    settle(quad, 3.0)
    assert quad.s.crash_code == "shot_down" and quad.s.crash_reason == "Shot down" and quad.s.resting


def test_takeoff_from_rest_and_impulse_wake_the_body():
    quad = Quad()
    assert quad.s.resting and quad.s.on_ground
    quad.impulse((0.0, 2.0, 1.0))
    assert not quad.s.resting and not quad.s.on_ground
    settle(quad, 2.0)
    assert quad.s.resting and quad.tilt == 0.0 and not quad.s.crashed and quad.s.p[1] > 0.0


def test_landing_through_the_autopilot_rests_and_disarms():
    p = SimPilot("ardupilot", name="sim-1")
    p.run(0.2)
    p.set_mode("ALT_HOLD")
    p.arm(True)
    p.sticks = StickInput(0, 0, 0, 0.9)
    while p.z < 1.5:
        p.run(0.05)
    p.set_mode("LAND")
    p.run(6.0)
    s = p.sim.quad.s
    assert not p.sim.armed and not s.crashed
    assert s.on_ground and s.resting and p.z == 0.0 and p.sim.quad.tilt == 0.0


def test_shot_down_aircraft_tumbles_into_the_ground():
    p = SimPilot("px4", name="sim-2")
    p.run(0.2)
    p.set_mode("ALTCTL")
    p.arm(True)
    p.sticks = StickInput(0, 0, 0, 0.9)
    while p.z < 3.0:
        p.run(0.05)
    p.sticks = StickInput(0, 0, 0, 0.5)
    p.run(0.5)
    p.sim.shoot_down()
    p.sim.quad.s.rates[0] = 8.0  # the hit set it rolling
    p.run(3.0)
    s = p.sim.quad.s
    assert s.crash_code == "shot_down" and not p.sim.armed and s.resting
    assert any("Crash" in t for t in p.texts())


def test_an_aircraft_on_its_back_cannot_arm():
    p = SimPilot("ardupilot", name="sim-5")
    p.run(0.2)
    s = p.sim.quad.s
    s.set_attitude(math.radians(178.0), 0.0, 0.0)  # lying on its back, not crashed
    p.sticks = StickInput(0, 0, 0, 0.0)
    p.run(0.2)
    p.arm(True)
    p.run(0.2)
    assert not p.sim.armed and any("PreArm: Leaning" in t for t in p.texts())
