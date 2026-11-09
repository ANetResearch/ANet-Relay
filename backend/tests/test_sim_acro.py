"""ACRO in the fast simulator (11 §8.4): sticks are body rates, no self-levelling, no tilt limit."""

from __future__ import annotations

import math

import pytest
from conftest import SimPilot

from relay.model import StickInput
from relay.sim.autopilot import ARDUPILOT_PARAMS, PX4_PARAMS


def aloft(flavour: str, height: float = 12.0) -> SimPilot:
    p = SimPilot(flavour, name="sim-1")
    p.run(0.2)
    p.set_mode("ALT_HOLD" if flavour == "ardupilot" else "ALTCTL")
    p.arm(True)
    p.sticks = StickInput(0, 0, 0, 0.9)
    while p.z < height:
        p.run(0.05)
    p.sticks = StickInput(0, 0, 0, 0.5)
    p.run(2.0)
    p.set_mode("ACRO")
    p.run(0.1)
    assert p.sim.mode == "ACRO" and p.sim.behaviour == "acro"
    return p


def test_acro_rate_parameters_are_in_the_tables():
    ap = {name: value for name, value, _t in ARDUPILOT_PARAMS}
    px4 = {name: value for name, value, _t in PX4_PARAMS}
    assert ap["ACRO_RP_RATE"] == 360.0 and ap["ACRO_Y_RATE"] == 202.5
    assert px4["MC_ACRO_R_MAX"] == 720.0 and px4["MC_ACRO_P_MAX"] == 720.0 and px4["MC_ACRO_Y_MAX"] == 540.0


def test_ardupilot_acro_full_roll_stick_rolls_360_degrees():
    p = aloft("ardupilot")
    quad = p.sim.quad
    hover = quad.hover_throttle()
    p.sticks = StickInput(1.0, 0, 0, hover)
    rolled, t = 0.0, 0.0
    tilts, rates = [], []
    while rolled < 2.0 * math.pi and t < 2.0:
        p.run(0.004, dt=0.004)
        t += 0.004
        rolled += quad.s.rates[0] * 0.004
        rates.append(math.degrees(quad.s.rates[0]))
        tilts.append(math.degrees(quad.tilt))
    assert rolled >= 2.0 * math.pi and t < 1.3  # ACRO_RP_RATE 360 deg/s: about one second
    assert sorted(rates)[len(rates) // 2] == pytest.approx(360.0, abs=10.0)  # the rate it settles on
    assert max(tilts) > 175.0  # through inverted: no tilt limit
    assert not p.sim.crashed


def test_px4_acro_holds_inverted_flight_without_levelling():
    p = aloft("px4")
    quad = p.sim.quad
    p.sticks = StickInput(1.0, 0, 0, 0.5)
    rolled = 0.0
    while rolled < math.radians(170.0):
        p.run(0.004, dt=0.004)
        rolled += quad.s.rates[0] * 0.004
    p.sticks = StickInput(0, 0, 0, 0.2)  # centred sticks: hold the attitude, upside down
    tilts = []
    for _ in range(40):
        p.run(0.02)
        tilts.append(math.degrees(quad.tilt))
    assert min(tilts) > 150.0
    assert max(abs(r) for r in quad.s.rates) < math.radians(10.0)
    assert quad.s.v[2] < -2.0  # thrust pushes it down while inverted


def test_acro_pitch_and_yaw_follow_the_stick_rates():
    p = aloft("px4")
    quad = p.sim.quad
    p.sticks = StickInput(0, 0.5, 0, quad.hover_throttle())  # half forward stick: nose down at 360 deg/s
    p.run(0.3, dt=0.004)
    assert math.degrees(quad.s.rates[1]) == pytest.approx(-360.0, abs=15.0)
    p.sticks = StickInput(0, 0, 0, 0.5)
    p.run(0.3, dt=0.004)
    p.sticks = StickInput(0, 0, -1.0, 0.5)
    p.run(0.5, dt=0.004)
    assert math.degrees(quad.s.rates[2]) == pytest.approx(-540.0, abs=40.0)


def test_acro_is_a_manual_mode():
    p = SimPilot("ardupilot", name="sim-3")
    p.run(0.2)
    p.set_mode("ACRO")
    p.sticks = StickInput(0, 0, 0, 0.5)
    p.run(0.2)
    p.arm(True)
    p.run(0.2)
    assert not p.sim.armed and any("Throttle too high" in t for t in p.texts())  # manual throttle
    p.sticks = StickInput(0, 0, 0, 0.0)
    p.run(0.2)
    p.arm(True)
    p.run(0.2)
    assert p.sim.armed and p.sim.quad.s.on_ground
    off = SimPilot("ardupilot", name="sim-4", hover_assist=False)
    off.run(0.2)
    off.set_mode("ACRO")  # no barometer: ACRO is still available
    off.run(0.2)
    assert off.sim.mode == "ACRO"
