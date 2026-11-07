"""Hover assist off and the physical flight model (05 §5): the simulator side."""

import math

import pytest
from conftest import SimPilot

from relay.mav.mavlink import M
from relay.model import StickInput
from relay.sim import physics
from relay.sim.autopilot import ASSIST_DENIED_TEXT


def assisted(flavour="ardupilot", mode=None, **kw) -> SimPilot:
    p = SimPilot(flavour, name=kw.pop("name", "sim-1"), **kw)
    p.run(0.2)
    if mode:
        p.set_mode(mode)
    p.arm(True)
    p.run(0.2)
    assert p.sim.armed
    return p


def climb_manual(p: SimPilot, z: float, throttle: float = 0.65) -> None:
    p.sticks = StickInput(0, 0, 0, throttle)
    for _ in range(400):
        p.run(0.05)
        if p.z >= z:
            break
    assert p.z >= z


def test_assist_defaults_on_and_survives_reset():
    p = SimPilot("ardupilot")
    assert p.sim.hover_assist is True and p.sim.quad.effects is False
    off = SimPilot("ardupilot", hover_assist=False, turbulence=0.4)
    assert off.sim.hover_assist is False and off.sim.quad.effects is True
    off.sim.reset()
    assert off.sim.hover_assist is False and off.sim.turbulence == pytest.approx(0.4)


def test_assist_off_reports_no_baro_and_no_position():
    p = SimPilot("ardupilot", hover_assist=False)
    p.run(0.2)
    status = p.sim._build("SYS_STATUS", p.t)
    assert not status.onboard_control_sensors_present & M.MAV_SYS_STATUS_SENSOR_ABSOLUTE_PRESSURE
    assert not status.onboard_control_sensors_present & M.MAV_SYS_STATUS_SENSOR_OPTICAL_FLOW
    ekf = p.sim._build("EKF_STATUS_REPORT", p.t)
    assert not ekf.flags & M.EKF_POS_HORIZ_REL and ekf.flags & M.EKF_CONST_POS_MODE
    assert not ekf.flags & M.EKF_POS_VERT_ABS
    # Position messages keep flowing so the stage can draw the aircraft; the estimator flags decide.
    assert p.sim._build("LOCAL_POSITION_NED", p.t) is not None
    p.sim.set_hover_assist(True)
    status = p.sim._build("SYS_STATUS", p.t)
    assert status.onboard_control_sensors_present & M.MAV_SYS_STATUS_SENSOR_ABSOLUTE_PRESSURE


def test_px4_streams_estimator_status_once_assist_was_off():
    p = SimPilot("px4")
    p.set_message_interval(M.MAVLINK_MSG_ID_SYS_STATUS, 10)
    p.run(0.5)
    assert not [m for m in p.msgs if m.get_type() == "ESTIMATOR_STATUS"]
    p.sim.set_hover_assist(False)
    p.run(0.5)
    est = [m for m in p.msgs if m.get_type() == "ESTIMATOR_STATUS"]
    assert est and not est[-1].flags & M.ESTIMATOR_POS_HORIZ_REL
    p.sim.set_hover_assist(True)
    p.run(0.5)
    est = [m for m in p.msgs if m.get_type() == "ESTIMATOR_STATUS"]
    assert est[-1].flags & M.ESTIMATOR_POS_HORIZ_REL


@pytest.mark.parametrize(
    ("flavour", "mode"),
    [("ardupilot", "ALT_HOLD"), ("ardupilot", "LOITER"), ("ardupilot", "GUIDED"), ("px4", "ALTCTL")],
)
def test_assist_off_rejects_hold_modes(flavour, mode):
    p = SimPilot(flavour, hover_assist=False)
    p.run(0.2)
    p.set_mode(mode)
    p.run(0.2)
    acks = [m for m in p.msgs if m.get_type() == "COMMAND_ACK"]
    assert acks and acks[-1].result == M.MAV_RESULT_DENIED
    assert ASSIST_DENIED_TEXT in p.texts()
    assert p.sim.mode in ("STABILIZE", "STABILIZED")


def test_assist_off_allows_stabilize_and_land():
    p = SimPilot("ardupilot", hover_assist=False)
    p.run(0.2)
    p.set_mode("LAND")
    p.run(0.1)
    assert p.sim.mode == "LAND"
    p.set_mode("STABILIZE")
    p.run(0.1)
    assert p.sim.mode == "STABILIZE"


def test_assist_off_folds_hold_behaviour():
    p = SimPilot("ardupilot")
    p.run(0.2)
    p.set_mode("ALT_HOLD")
    p.run(0.1)
    assert p.sim.behaviour == "althold"
    p.sim.hover_assist = False  # the bare flag, without the mode switch set_hover_assist makes
    assert p.sim.behaviour == "stab"


def test_assist_off_mid_throttle_drifts():
    p = assisted(hover_assist=False)
    climb_manual(p, 2.0)
    p.sim.quad.battery.soc = 0.6  # thrust no longer equals weight at mid stick
    p.sticks = StickInput(0, 0, 0, 0.5)
    p.run(0.5)
    start = p.z
    p.run(10.0)
    assert abs(p.z - start) > 0.3


def test_assist_on_alt_hold_unchanged_by_battery():
    p = assisted(mode="ALT_HOLD")
    p.sticks = StickInput(0, 0, 0, 0.9)
    while p.z < 1.5:
        p.run(0.05)
    p.sticks = StickInput(0, 0, 0, 0.5)
    p.run(2.0)
    p.sim.quad.battery.soc = 0.6
    start = p.z
    p.run(10.0)
    assert abs(p.z - start) < 0.1


def test_lower_voltage_sinks_at_same_stick():
    def climb_rate(soc: float) -> float:
        p = assisted(hover_assist=False, turbulence=0.0)
        climb_manual(p, 2.0)
        p.sim.quad.battery.soc = soc
        p.sticks = StickInput(0, 0, 0, 0.5)
        p.run(1.0)
        start = p.z
        p.run(2.0)
        return (p.z - start) / 2.0

    full, low = climb_rate(1.0), climb_rate(0.25)
    assert low < full - 0.2
    assert low < 0.0


def test_assist_off_in_flight_switches_to_stabilize_without_thrust_jump():
    p = assisted(mode="ALT_HOLD")
    p.sticks = StickInput(0, 0, 0, 0.9)
    while p.z < 1.5:
        p.run(0.05)
    p.sticks = StickInput(0, 0, 0, 0.5)
    p.run(2.0)
    p.sticks = StickInput(0, 0, 0, 0.7)  # the stick says more than the hold loop was giving
    before = p.sim.quad.s.motor
    p.sim.set_hover_assist(False, p.t)
    assert p.sim.mode == "STABILIZE"
    worst = 0.0
    prev = before
    for _ in range(100):
        p.run(0.004, dt=0.004)
        motor = p.sim.quad.s.motor
        worst = max(worst, abs(motor - prev))
        prev = motor
    assert worst < 0.01  # a 0.3 s ramp; without it the motor lag alone steps ~0.03 per tick
    assert p.sim.quad.s.motor == pytest.approx(0.7, abs=0.02)


def test_assist_on_in_flight_holds_present_height():
    p = assisted(hover_assist=False, turbulence=0.0)
    climb_manual(p, 1.5)
    p.sticks = StickInput(0, 0, 0, 0.5)
    p.sim.set_hover_assist(True, p.t)
    assert p.sim.mode == "ALT_HOLD"
    start = p.z  # still climbing at the switch: the hold target is the height at that moment
    p.run(8.0)
    assert abs(p.z - start) < 0.1


def test_assist_on_on_ground_enters_hold_only_when_asked():
    p = SimPilot("ardupilot", hover_assist=False)
    p.run(0.2)
    p.sim.set_hover_assist(True, p.t, enter_hold=False)
    assert p.sim.mode == "STABILIZE"
    p.sim.set_hover_assist(False, p.t)
    p.sim.set_hover_assist(True, p.t)
    assert p.sim.mode == "ALT_HOLD"


def test_assist_off_radio_failsafe_lands_open_loop():
    p = assisted(hover_assist=False)
    climb_manual(p, 3.0)
    p.sticks = StickInput(0, 0, 0, 0.5)
    p.run(2.0)
    p.sticks = None
    landed = False
    worst = 0.0
    for _ in range(1200):
        p.run(0.05)
        worst = max(worst, -p.sim.quad.s.v[2])
        if not p.sim.armed:
            landed = True
            break
    assert landed and p.sim.mode == "LAND"
    assert "Radio Failsafe - Landing" in p.texts()
    assert not p.sim.crashed
    assert worst < physics.CRASH_VZ


def test_ground_effect_lowers_near_ground_hover_throttle():
    quad = physics.Quad(effects=True)
    assert quad.ground_effect(0.0) == pytest.approx(1.0 + physics.GROUND_EFFECT_GAIN)
    assert quad.ground_effect(physics.PROP_DIAMETER) == pytest.approx(1.0)
    assert quad.hover_throttle(0.03) < quad.hover_throttle(1.0) - 0.01
    plain = physics.Quad()
    assert plain.ground_effect(0.0) == 1.0
    assert plain.hover_throttle(0.03) == pytest.approx(plain.hover_throttle(1.0))


def test_motor_spins_down_slower_than_up_with_effects():
    def settle(effects: bool, start: float, target: float) -> float:
        quad = physics.Quad(effects=effects)
        quad.s.motor = start
        quad.s.on_ground = False
        quad.s.p[2] = 5.0
        quad.step(physics.Command(throttle=target), armed=True)
        return abs(quad.s.motor - start)

    assert settle(True, 0.3, 0.7) > settle(True, 0.7, 0.3)
    assert settle(False, 0.3, 0.7) == pytest.approx(settle(False, 0.7, 0.3))


def trajectory(name: str, turbulence: float = 0.6) -> list[tuple[float, float, float]]:
    p = assisted(hover_assist=False, turbulence=turbulence, name=name)
    climb_manual(p, 1.5)
    p.sticks = StickInput(0, 0, 0, 0.5)
    out = []
    for _ in range(40):
        p.run(0.1)
        out.append(tuple(round(c, 9) for c in p.sim.quad.s.p))
    return out


def test_turbulence_is_deterministic_per_aircraft():
    a, b = trajectory("sim-1"), trajectory("sim-1")
    assert a == b
    assert trajectory("sim-2") != a
    calm = trajectory("sim-1", turbulence=0.0)
    assert max(math.hypot(x, y) for x, y, _ in calm) < 0.01  # no gust: level hover stays put
    assert max(math.hypot(x, y) for x, y, _ in a) > 0.02


def test_effects_never_touch_the_assisted_model():
    quad = physics.Quad(turbulence=1.0, seed=7)
    quad.s.on_ground = False
    quad.s.p[2] = 2.0
    for _ in range(250):
        quad.step(physics.Command(throttle=0.5), armed=True)
    assert quad.gust == [0.0, 0.0, 0.0] and quad.wash == 0.0


def test_impulse_pushes_and_kicks_attitude():
    p = assisted(hover_assist=False, turbulence=0.0)
    climb_manual(p, 1.5)
    p.sticks = StickInput(0, 0, 0, 0.5)
    p.run(0.5)
    s = p.sim.quad.s
    vx = s.v[0]
    p.sim.quad.impulse((1.0, 0.0, 0.0), (math.radians(10), 0.0))
    assert s.v[0] == pytest.approx(vx + 1.0)
    assert s.roll > math.radians(8)
    p.run(1.0)
    assert abs(s.roll) < math.radians(2)  # the attitude loop levels it again


def test_impulse_bounds_and_ground_contact():
    quad = physics.Quad()
    quad.impulse((0.0, 50.0, -1.0))
    assert quad.s.v == [0.0, 0.0, 0.0] and quad.s.on_ground  # the ground absorbs a sideways push
    quad.impulse((0.0, 50.0, 1.0), (1.0, -1.0))
    assert quad.s.v[1] == physics.IMPULSE_MAX and not quad.s.on_ground
    assert quad.s.roll == pytest.approx(physics.KICK_MAX)
    quad.impulse((float("nan"), 0.0, 0.0))
    assert quad.s.v[1] == physics.IMPULSE_MAX


def test_shoot_down_falls_and_reports_crash():
    p = assisted(mode="ALT_HOLD")
    p.sticks = StickInput(0, 0, 0, 0.9)
    while p.z < 2.0:
        p.run(0.05)
    p.sticks = StickInput(0, 0, 0, 0.5)
    p.run(0.5)
    p.sim.shoot_down()
    assert p.sim.quad.s.motor == 0.0 and p.sim.crashed
    p.run(0.1)
    assert p.sim.armed and p.z > 1.5  # falling, still armed until it hits the ground
    assert any("Crash" in t and "Shot down" in t for t in p.texts())
    p.run(2.0)
    assert p.z == 0.0 and not p.sim.armed
    assert p.sim.quad.s.crash_reason == "Shot down"
    p.sim.reset()
    assert not p.sim.crashed
