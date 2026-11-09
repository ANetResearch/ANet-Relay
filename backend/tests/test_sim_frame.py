"""Airframe types of the simulated quadrotor (11 §8.1): presets, validation, hover derivation, Quad(frame)."""

from __future__ import annotations

import math

import pytest
from conftest import SimPilot

from relay.model import StickInput
from relay.sim import frame as framemod
from relay.sim import physics
from relay.sim.autopilot import FlightController
from relay.sim.frame import FrameModel, derive_from_hover, inertia_from
from relay.sim.physics import DT, Command, Quad


def test_micro_120_is_exactly_the_v053_airframe():
    q = Quad()
    assert q.frame is framemod.DEFAULT_FRAME and q.frame.nominal
    assert Quad(frame=framemod.preset("micro-120")).frame.nominal
    assert (q.mass, q.ixx, q.iyy, q.izz) == (physics.MASS, physics.IXX, physics.IYY, physics.IZZ)
    assert q.arm == physics.ARM and q.rotor_x == physics.ROTOR_X and q.rotor_y == physics.ROTOR_Y
    assert (q.tau_up, q.tau_down, q.omega_max) == (physics.TAU_UP, physics.TAU_DOWN, physics.OMEGA_MAX)
    assert q.rotor_thrust_max == physics.ROTOR_THRUST_MAX and q.k_f == physics.K_F and q.k_m == physics.K_M
    assert q.omega_hover == physics.OMEGA_HOVER and q.rotor_drag == physics.ROTOR_DRAG
    assert q.body_drag_lin == physics.BODY_DRAG_LIN and q.body_drag_quad == physics.BODY_DRAG_QUAD
    assert q.prop_diameter == physics.PROP_DIAMETER and q.twr == physics.THRUST_TO_WEIGHT
    b = q.battery
    assert (b.cells, b.capacity_ah, b.r_internal, b.load_current) == (
        physics.CELLS,
        physics.CAPACITY_AH,
        physics.R_INTERNAL,
        physics.LOAD_CURRENT,
    )
    assert b.voltage == physics.CELLS * 4.2


def test_default_and_explicit_micro_frame_fly_the_same_bits():
    def fly(frame):
        quad = Quad(effects=True, turbulence=0.5, seed=3, frame=frame)
        quad.s.on_ground = False
        quad.s.p[2] = 4.0
        fc = FlightController()
        for i in range(1500):
            cmd = Command(roll=0.2 * math.sin(i / 40), pitch=0.1, yaw_rate=0.3, throttle=0.52)
            quad.step(fc.update(cmd, quad, DT), armed=True)
        return (*quad.s.p, *quad.s.q, *quad.s.omega, quad.battery.voltage)

    assert fly(None) == fly(FrameModel.from_dict({"preset": "micro-120"}))


def test_presets_are_valid_and_scaled():
    small, large = framemod.preset("small-250"), framemod.preset("large-450")
    for f in framemod.PRESETS.values():
        f.validate()
        assert FrameModel.from_dict(f.as_dict()) == f
    assert small.cells == 3 and small.wheelbase_m == 0.25 and small.prop_in == 5.0
    assert large.cells == 4 and large.wheelbase_m == 0.45 and large.prop_in == 10.0
    assert (large.ixx, large.iyy, large.izz) == inertia_from(1.5, 0.45)
    assert large.ixx == pytest.approx(0.0076, rel=0.01) and large.izz == pytest.approx(1.8 * large.ixx)
    assert Quad(frame=large).battery.voltage == pytest.approx(16.8)
    assert large.rotor_thrust_n == pytest.approx(2.2 * 1.5 * physics.G / 4.0)
    with pytest.raises(ValueError):
        framemod.preset("hexa-550")


def test_from_dict_rules():
    base = framemod.preset("small-250")
    heavier = FrameModel.from_dict({"preset": "small-250", "mass_kg": 0.84})
    # the rotors stay: the thrust-to-weight ratio halves; the inertia scales with the mass
    assert heavier.rotor_thrust_n == pytest.approx(base.rotor_thrust_n)
    assert heavier.thrust_to_weight == pytest.approx(base.thrust_to_weight / 2.0)
    assert heavier.ixx == pytest.approx(2.0 * base.ixx) and heavier.izz == pytest.approx(2.0 * base.izz)
    wider = FrameModel.from_dict({"wheelbase_m": 0.3}, base=base)
    assert wider.ixx == pytest.approx(base.ixx * 1.44)
    thrust = FrameModel.from_dict({"preset": "large-450", "rotor_thrust_n": 9.0})
    assert thrust.rotor_thrust_n == pytest.approx(9.0)
    for bad, name in [
        ({"preset": "hexa"}, "preset"),
        ({"mass_kg": -1}, "mass_kg"),
        ({"mass_kg": True}, "mass_kg"),
        ({"cells": 3.5}, "cells"),
        ({"body_drag_lin": [0.1, 0.1]}, "body_drag_lin"),
        ({"prop_in": 9.0}, "prop_in"),  # 9 inch propellers overlap on a 25 cm frame
        ({"tau_up_s": 0.1, "tau_down_s": 0.05}, "tau_down_s"),
        ({"izz": 1e-5}, "izz"),
        ({"wingspan": 1.0}, "wingspan"),
    ]:
        with pytest.raises(ValueError) as info:
            FrameModel.from_dict(bad)
        assert str(info.value) == name


@pytest.mark.parametrize(
    "hover, expo, spin_min, spin_max",
    [(0.31, 0.65, 0.15, 0.95), (0.5, 0.0, 0.0, 1.0), (0.42, 0.2, 0.0, 1.0)],
)
def test_derive_from_hover_hovers_at_the_learned_throttle(hover, expo, spin_min, spin_max):
    """ArduPilot MOT_THST_HOVER / MOT_THST_EXPO / MOT_SPIN_MIN..MAX, PX4 MPC_THR_HOVER / THR_MDL_FAC: the
    output the controller sends at its hover throttle balances the weight of the derived frame."""
    frame = derive_from_hover(
        framemod.preset("small-250"), hover, expo=expo, spin_min=spin_min, spin_max=spin_max
    )
    actuator = framemod._actuator_from_thrust(hover, expo)
    assert framemod._thrust_from_actuator(actuator, expo) == pytest.approx(hover)
    output = spin_min + (spin_max - spin_min) * actuator
    quad = Quad(frame=frame)
    assert quad.hover_throttle() == pytest.approx(output, abs=1e-9)
    assert physics.thrust_fraction(output) * quad.max_thrust == pytest.approx(frame.mass_kg * physics.G)
    with pytest.raises(ValueError):
        derive_from_hover(frame, 0.99)
    with pytest.raises(ValueError):
        derive_from_hover(frame, 0.3, spin_min=0.5, spin_max=0.5)


@pytest.mark.parametrize("name", ["small-250", "large-450"])
def test_other_frames_hover_steadily_in_the_fast_simulator(name):
    p = SimPilot("ardupilot", name="sim-1", frame=framemod.preset(name))
    sim = p.sim
    p.run(0.2)
    p.set_mode("ALT_HOLD")
    p.arm(True)
    p.sticks = StickInput(0, 0, 0, 0.85)
    while p.z < 2.0:
        p.run(0.05)
        assert p.t < 20.0
    p.sticks = StickInput(0, 0, 0, 0.5)
    p.run(4.0)
    rates, heights = [], []
    for _ in range(100):
        p.run(0.02)
        rates.append(max(abs(r) for r in sim.quad.s.rates))
        heights.append(p.z)
    assert max(rates) < math.radians(5.0)  # no oscillation
    assert max(heights) - min(heights) < 0.1 and not sim.crashed
    assert sim.quad.battery.cells == framemod.preset(name).cells
