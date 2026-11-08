"""Physics v2 cross-check against Gazebo's MulticopterMotorModel (07 §1, WP-A).

``GazeboRotor`` below is an independent re-implementation of the equations of gazebo_motor_model.cpp and
its FirstOrderFilter (PX4-SITL_gazebo-classic), written from the plugin, not from ``relay.sim.physics``:

* the rotor speed filter: ``x = alpha * x + (1 - alpha) * ref`` with ``alpha = exp(-dt / tau_up)`` while
  speeding up and ``exp(-dt / tau_down)`` otherwise;
* thrust ``F = w * |w| * motor_constant`` along the rotor axis;
* the drag torque ``(0, 0, -turning_direction * F * moment_constant)`` (rotor axis z up);
* rotor drag ``-|w| * rotor_drag_coefficient * v_perp`` and rolling moment
  ``-|w| * turning_direction * rolling_moment_coefficient * v_perp`` with ``v_perp`` the airspeed
  perpendicular to the axis (CW and CCW rotors cancel at equal speeds).

The simulator is compared with it to better than 1 %: the single-rotor step response, thrust, reaction
torque and rotor drag (with the documented body drag taken out), plus the rolling moment. The envelope
tests check hover throttle, climb, level speed and yaw response stay in their contract ranges.
"""

from __future__ import annotations

import math

import pytest
from conftest import SimPilot

from relay.model import StickInput
from relay.sim import physics
from relay.sim.autopilot import FlightController
from relay.sim.physics import DT, Command, Quad

CCW, CW = 1, -1  # Gazebo turning_direction


class GazeboRotor:
    def __init__(self, turning_direction: int) -> None:
        self.motor_constant = physics.K_F
        self.moment_constant = physics.K_M
        self.time_constant_up = physics.TAU_UP
        self.time_constant_down = physics.TAU_DOWN
        self.rotor_drag_coefficient = physics.ROTOR_DRAG
        self.rolling_moment_coefficient = physics.ROLLING_MOMENT
        self.turning_direction = turning_direction
        self.previous_state = 0.0

    def update_filter(self, input_state: float, sampling_time: float) -> float:
        if input_state > self.previous_state:
            alpha = math.exp(-sampling_time / self.time_constant_up)
        else:
            alpha = math.exp(-sampling_time / self.time_constant_down)
        self.previous_state = alpha * self.previous_state + (1.0 - alpha) * input_state
        return self.previous_state

    def thrust(self, w: float) -> float:
        return w * abs(w) * self.motor_constant

    def drag_torque_z_up(self, w: float) -> float:
        return -self.turning_direction * self.thrust(w) * self.moment_constant

    def air_drag(self, w: float, v: tuple[float, float, float]) -> tuple[float, float, float]:
        axis = (0.0, 0.0, 1.0)
        along = sum(v[i] * axis[i] for i in range(3))
        perp = tuple(v[i] - along * axis[i] for i in range(3))
        return tuple(-abs(w) * self.rotor_drag_coefficient * c for c in perp)  # type: ignore[return-value]

    def rolling_moment(self, w: float, v: tuple[float, float, float]) -> tuple[float, float, float]:
        perp = (v[0], v[1], 0.0)
        k = -abs(w) * self.turning_direction * self.rolling_moment_coefficient
        return tuple(k * c for c in perp)  # type: ignore[return-value]


ROTORS = (CCW, CCW, CW, CW)  # quad-x: 1 front-right CCW, 2 rear-left CCW, 3 front-left CW, 4 rear-right CW


def aloft(yaw: float = 0.0) -> Quad:
    quad = Quad()
    quad.s.on_ground = False
    quad.s.p[2] = 50.0
    quad.s.set_attitude(0.0, 0.0, yaw)
    return quad


def ref_speed(fraction: float, quad: Quad) -> float:
    """The ESC's speed reference: thrust fraction -> rotor speed at the present voltage."""
    return physics.OMEGA_MAX * quad.voltage_factor() * math.sqrt(fraction)


def rel(a: float, b: float) -> float:
    return abs(a - b) / max(abs(b), 1e-12)


# ---------------------------------------------------------------------------- rotor


def test_single_rotor_step_response_matches_gazebo():
    quad = aloft()
    refs = [GazeboRotor(d) for d in ROTORS]
    worst = 0.0
    for fraction, steps in ((1.0, 60), (0.2, 80), (0.6, 50), (0.0, 60)):  # up, down, up, off
        cmd = (fraction,) * 4
        for _ in range(steps):
            target = ref_speed(fraction, quad)  # the battery sags a little with the load
            quad.step(cmd, armed=True)
            for i, g in enumerate(refs):
                g.update_filter(target, DT)
                if g.previous_state > 50.0:
                    worst = max(worst, rel(quad.s.omega[i], g.previous_state))
            quad.s.p[2] = 50.0  # stay aloft; only the rotors matter here
    assert worst < 0.01
    # the time constants themselves: 63 % of a step after tau
    up = GazeboRotor(CCW)
    n_up = next(n for n in range(1, 100) if up.update_filter(1000.0, DT) >= 632.0)
    assert n_up * DT == pytest.approx(physics.TAU_UP, abs=DT)
    down = GazeboRotor(CCW)
    down.previous_state = 1000.0
    n_dn = next(n for n in range(1, 100) if down.update_filter(0.0, DT) <= 368.0)
    assert n_dn * DT == pytest.approx(physics.TAU_DOWN, abs=DT)


def test_thrust_and_reaction_torque_match_gazebo():
    quad = aloft()
    fractions = (0.55, 0.35, 0.45, 0.30)  # unequal on purpose: a net yaw torque
    for _ in range(200):  # settle the rotor speeds
        quad.step(fractions, armed=True)
        quad.s.p, quad.s.v = [0.0, 0.0, 50.0], [0.0, 0.0, 0.0]
        quad.s.set_attitude(0.0, 0.0, 0.0)
        quad.s.rates = [0.0, 0.0, 0.0]
    quad.step(fractions, armed=True)
    refs = [GazeboRotor(d) for d in ROTORS]
    omega = list(quad.s.omega)
    for i, g in enumerate(refs):
        assert rel(quad.thrusts[i], g.thrust(omega[i])) < 0.01
    # reaction torque: Gazebo's z-up torque is -z in the body FRD frame; from rest, I_zz * dr/dt
    tz_ref = -sum(g.drag_torque_z_up(omega[i]) for i, g in enumerate(refs))
    tz = physics.IZZ * quad.s.rates[2] / DT
    assert rel(tz, tz_ref) < 0.01
    # and the thrust moments about roll and pitch follow the quad-x lever arms
    t = [refs[i].thrust(omega[i]) for i in range(4)]
    tx_ref = -sum(physics.ROTOR_Y[i] * t[i] for i in range(4))
    ty_ref = sum(physics.ROTOR_X[i] * t[i] for i in range(4))
    assert rel(physics.IXX * quad.s.rates[0] / DT, tx_ref) < 0.01
    assert rel(physics.IYY * quad.s.rates[1] / DT, ty_ref) < 0.01


@pytest.mark.parametrize(("yaw_deg", "yawing"), [(0.0, False), (90.0, False), (30.0, False), (30.0, True)])
def test_rotor_drag_and_rolling_moment_match_gazebo(yaw_deg, yawing):
    quad = aloft(math.radians(yaw_deg))
    f = physics.thrust_fraction(0.5)
    g = physics.thrust_fraction(0.35)
    # yawing: the CCW pair (1, 2) faster than the CW pair (3, 4); diagonal pairs keep the roll and pitch
    # thrust level, so only the rolling moment's CCW / CW imbalance turns the body about x and y
    cmd = (f, f, g, g) if yawing else (f,) * 4
    for _ in range(200):
        quad.step(cmd, armed=True)
        quad.s.p = [0.0, 0.0, 50.0]
        quad.s.set_attitude(0.0, 0.0, math.radians(yaw_deg))
        quad.s.rates = [0.0, 0.0, 0.0]
        quad.s.v = [0.0, 3.0, 0.0]  # 3 m/s north through still air
    quad.step(cmd, armed=True)
    omega = list(quad.s.omega)
    yaw = math.radians(yaw_deg)
    # air velocity in the body frame (FRD): north 3 m/s seen from heading yaw
    bx, by = 3.0 * math.cos(yaw), -3.0 * math.sin(yaw)
    refs = [GazeboRotor(d) for d in ROTORS]
    drag = [sum(g.air_drag(omega[i], (bx, by, 0.0))[k] for i, g in enumerate(refs)) for k in range(2)]
    roll_m = [sum(g.rolling_moment(omega[i], (bx, by, 0.0))[k] for i, g in enumerate(refs)) for k in range(2)]
    speed = math.hypot(bx, by)
    body = [-(physics.BODY_DRAG_LIN[k] + physics.BODY_DRAG_QUAD[k] * speed) * (bx, by)[k] for k in range(2)]
    # measured horizontal force in the body frame, minus the documented body drag = the rotor drag
    a = quad.s.accel
    fn, fe = physics.MASS * a[1], physics.MASS * a[0]
    fx = fn * math.cos(yaw) + fe * math.sin(yaw)
    fy = -fn * math.sin(yaw) + fe * math.cos(yaw)
    for k, measured in enumerate((fx, fy)):
        if abs(drag[k]) > 1e-6:
            assert rel(measured - body[k], drag[k]) < 0.01
    # rolling moment: the rotor drag forces at the hubs also turn the body in yaw, so compare roll / pitch
    for k, inertia in enumerate((physics.IXX, physics.IYY)):
        measured = inertia * quad.s.rates[k] / DT
        if not yawing:
            assert abs(roll_m[k]) < 1e-12 and abs(measured) < 1e-9  # equal rotors: CW and CCW cancel
        elif abs(roll_m[k]) > 1e-9:
            assert rel(measured, roll_m[k]) < 0.01
    if yawing:
        assert max(abs(c) for c in roll_m) > 1e-7  # the case really has a rolling moment to compare


# ---------------------------------------------------------------------------- envelope


def test_hover_throttle_is_half():
    assert Quad().hover_throttle() == pytest.approx(0.50, abs=0.03)
    p = SimPilot("ardupilot", name="sim-1")
    p.run(0.2)
    p.set_mode("ALT_HOLD")
    p.arm(True)
    p.sticks = StickInput(0, 0, 0, 0.9)
    while p.z < 1.0:
        p.run(0.05)
    p.sticks = StickInput(0, 0, 0, 0.5)
    p.run(3.0)
    outputs = []
    for _ in range(100):
        p.run(0.02)
        outputs.append(p.sim.quad.s.motor)
    assert sum(outputs) / len(outputs) == pytest.approx(0.50, abs=0.03)


def flight(cmd_for, seconds: float) -> Quad:
    quad = aloft()
    fc = FlightController()
    for _ in range(int(seconds / DT)):
        quad.step(fc.update(cmd_for(quad), quad, DT), armed=True)
    return quad


def test_max_climb_rate():
    quad = flight(lambda q: Command(throttle=1.0), 8.0)
    assert 6.0 < quad.s.v[2] < 14.0  # thrust-to-weight 2.4 against the vertical drag


def test_level_speed_at_30_degrees_is_about_7_ms():
    state = {"i": 0.0}

    def hold(q: Quad) -> Command:  # pitch forward 30 degrees, thrust to keep the height
        state["i"] += -q.s.v[2] * DT
        f = physics.MASS * physics.G / math.cos(math.radians(30.0)) / q.max_thrust
        f *= 1.0 + 0.5 * -q.s.v[2] + 0.5 * state["i"]
        return Command(pitch=-math.radians(30.0), throttle=physics.throttle_for_fraction(f))

    quad = flight(hold, 20.0)
    assert abs(quad.s.v[2]) < 0.05
    assert math.hypot(quad.s.v[0], quad.s.v[1]) == pytest.approx(7.0, rel=0.15)


def test_attitude_and_yaw_response():
    quad = aloft()
    fc = FlightController()
    rolls, yaws = [], []
    cmd = Command(roll=math.radians(20.0), yaw_rate=math.radians(120.0), throttle=0.55)
    for _ in range(int(0.8 / DT)):
        quad.step(fc.update(cmd, quad, DT), armed=True)
        rolls.append(math.degrees(quad.s.roll))
        yaws.append(math.degrees(quad.s.rates[2]))
    t63_roll = next(i for i, r in enumerate(rolls) if r >= 0.632 * 20.0) * DT
    t63_yaw = next(i for i, r in enumerate(yaws) if r >= 0.632 * 120.0 * math.cos(math.radians(20))) * DT
    assert 0.04 < t63_roll < 0.12  # about the v0.4 attitude time constant (0.08 s)
    assert max(rolls) < 22.0 and rolls[-1] == pytest.approx(20.0, abs=0.5)  # little overshoot
    assert t63_yaw < 0.2
    # the heading rate settles on the command (body r = cos(roll) * heading rate)
    heading_rate = yaws[-1] / math.cos(math.radians(rolls[-1]))
    assert heading_rate == pytest.approx(120.0, abs=3.0)


def test_same_inputs_same_trajectory_bit_for_bit():
    def fly():
        quad = Quad(effects=True, turbulence=0.7, seed=11)
        quad.s.on_ground = False
        quad.s.p[2] = 3.0
        fc = FlightController()
        out = []
        for i in range(1000):
            cmd = Command(roll=0.2 * math.sin(i / 50), pitch=0.1, yaw_rate=0.3, throttle=0.52)
            quad.step(fc.update(cmd, quad, DT), armed=True)
            out.append((*quad.s.p, *quad.s.q, *quad.s.omega))
        return out

    assert fly() == fly()
