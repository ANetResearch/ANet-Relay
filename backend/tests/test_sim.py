"""Simulated flight model and autopilot behaviour (03 §7)."""

import math

import pytest
from conftest import SimPilot

from relay.mav.mavlink import M
from relay.model import StickInput
from relay.sim import physics


def armed_pilot(flavour="ardupilot", mode=None, **kw) -> SimPilot:
    p = SimPilot(flavour, **kw)
    p.run(0.2)
    if mode:
        p.set_mode(mode)
    p.arm(True)
    p.run(0.2)
    assert p.sim.armed
    return p


def climb_to(p: SimPilot, z: float) -> None:
    p.sticks = StickInput(0, 0, 0, 0.9)
    for _ in range(400):
        p.run(0.05)
        if p.z >= z:
            break
    p.sticks = StickInput(0, 0, 0, 0.5)


def test_thrust_curve_hover_point():
    assert physics.thrust_fraction(0.5) * physics.THRUST_TO_WEIGHT == pytest.approx(1.0)
    assert physics.throttle_for_fraction(physics.thrust_fraction(0.37)) == pytest.approx(0.37)


def test_alt_hold_hover_within_10cm_for_10s():
    p = armed_pilot(mode="ALT_HOLD")
    climb_to(p, 1.0)
    p.run(2.0)  # settle
    ref = p.z
    lo = hi = ref
    for _ in range(500):
        p.run(0.02)
        lo, hi = min(lo, p.z), max(hi, p.z)
    assert hi - ref <= 0.1 and ref - lo <= 0.1
    assert 0.9 < ref < 1.6


def test_loiter_brakes_after_stick_release():
    p = armed_pilot(mode="LOITER")
    climb_to(p, 1.0)
    p.run(1.0)
    p.sticks = StickInput(0, 1.0, 0, 0.5)  # full forward
    p.run(2.0)
    s = p.sim.quad.s
    assert math.hypot(s.v[0], s.v[1]) > 2.0
    p.sticks = StickInput(0, 0, 0, 0.5)
    p.run(2.5)
    assert math.hypot(s.v[0], s.v[1]) < 0.2
    stop = list(s.p)
    p.run(3.0)
    assert math.dist(stop[:2], s.p[:2]) < 0.15


def test_guided_reaches_target_within_15cm():
    p = armed_pilot(mode="GUIDED")
    p.command_long(M.MAV_CMD_NAV_TAKEOFF, (0, 0, 0, 0, 0, 0, 1.0))
    p.run(4.0)
    assert p.z == pytest.approx(1.0, abs=0.1)
    p.position_target_local(1.2, -0.8, 1.5, None)
    p.run(6.0)
    assert math.dist(p.sim.quad.s.p, (1.2, -0.8, 1.5)) <= 0.15


def test_land_disarms_on_touchdown():
    p = armed_pilot(mode="GUIDED")
    p.command_long(M.MAV_CMD_NAV_TAKEOFF, (0, 0, 0, 0, 0, 0, 1.0))
    p.run(4.0)
    p.set_mode("LAND")
    p.run(5.0)
    assert p.sim.quad.s.on_ground and not p.sim.armed
    assert any("Disarming" in t for t in p.texts())


def test_hard_landing_is_a_crash():
    p = armed_pilot(mode="STABILIZE")
    p.sticks = StickInput(0, 0, 0, 0.7)
    while p.z < 2.5:
        p.run(0.05)
    p.sticks = StickInput(0, 0, 0, 0.0)  # cut throttle: free fall
    p.run(4.0)
    assert p.sim.crashed and not p.sim.armed
    assert any("Crash" in t for t in p.texts())
    p.arm(True)
    p.run(0.2)
    assert not p.sim.armed  # needs a reset
    p.sim.reset()
    p.sticks = StickInput(0, 0, 0, 0)
    p.run(0.2)
    p.arm(True)
    p.run(0.2)
    assert p.sim.armed


def test_battery_sags_under_load_and_drains():
    p = armed_pilot(mode="ALT_HOLD")
    rest = p.sim.quad.battery.voltage
    climb_to(p, 1.0)
    p.run(1.0)
    loaded = p.sim.quad.battery.voltage
    assert loaded < rest - 0.2  # internal resistance sag
    soc0 = p.sim.quad.battery.soc
    p.run(30.0, dt=0.05)
    assert p.sim.quad.battery.soc < soc0
    assert p.sim.quad.battery.current > 3.0


def test_rc_failsafe_lands_when_sticks_stop_in_alt_hold():
    p = armed_pilot(mode="ALT_HOLD")
    climb_to(p, 1.0)
    p.run(0.5)
    p.sticks = None
    p.stop_sticks()
    p.run(1.5)
    assert p.sim.mode == "LAND"
    assert any("Failsafe" in t for t in p.texts())


def test_loiter_holds_without_pilot_input():
    p = armed_pilot(mode="LOITER")
    climb_to(p, 1.0)
    p.run(0.5)
    p.run(2.0)
    p.sticks = None
    p.stop_sticks()
    z = p.z
    p.run(3.0)
    assert p.sim.mode == "LOITER" and abs(p.z - z) < 0.15


def test_override_only_from_sysid_mygcs():
    p = SimPilot("ardupilot")
    p.run(0.1)
    p.sim.params["SYSID_MYGCS"][0] = 200
    p.sticks = StickInput(0.5, 0, 0, 0)
    p.run(0.2)
    assert p.sim.pilot is None


def test_arming_checks_throttle_by_mode():
    p = SimPilot("ardupilot")
    p.run(0.1)
    p.sticks = StickInput(0, 0, 0, 0.5)
    p.run(0.1)
    p.arm(True)
    p.run(0.1)
    assert not p.sim.armed  # STABILIZE needs throttle down
    p.set_mode("ALT_HOLD")
    p.run(0.1)
    p.arm(True)
    p.run(0.1)
    assert p.sim.armed  # mid stick is fine in altitude hold


def test_disarm_denied_in_flight():
    p = armed_pilot(mode="ALT_HOLD")
    climb_to(p, 1.0)
    p.arm(False)
    p.run(0.2)
    assert p.sim.armed


def test_px4_manual_control_gated_by_com_rc_in_mode():
    p = SimPilot("px4")
    p.run(0.1)
    p.sim.params["COM_RC_IN_MODE"][0] = 0
    p.sticks = StickInput(0.3, 0, 0, 0)
    p.run(0.2)
    assert p.sim.pilot is None
    p.sim.params["COM_RC_IN_MODE"][0] = 1
    p.run(0.2)
    assert p.sim.pilot is not None and p.sim.pilot[0] == pytest.approx(0.3)


def test_px4_offboard_requires_setpoints_and_flies():
    p = armed_pilot("px4", mode="POSCTL")
    p.sticks = StickInput(0, 0, 0, 0.5)
    p.set_mode("OFFBOARD")
    p.run(0.2)
    assert p.sim.mode == "POSCTL"  # rejected: no setpoints yet
    for _ in range(15):
        p.position_target_local(0.0, 0.0, 1.0, None)
        p.run(0.1)
    p.set_mode("OFFBOARD")
    for _ in range(50):
        p.position_target_local(0.5, 0.0, 1.0, None)
        p.run(0.1)
    assert p.sim.mode == "OFFBOARD"
    assert math.dist(p.sim.quad.s.p, (0.5, 0.0, 1.0)) < 0.15


def test_no_position_flavour_rejects_position_modes():
    p = SimPilot("px4", position=False)
    p.run(0.1)
    p.set_mode("POSCTL")
    p.run(0.2)
    assert p.sim.mode != "POSCTL"
    assert any("requires position" in t for t in p.texts())


def test_parameters_change_dynamics():
    p = armed_pilot(mode="GUIDED")
    p.param_value = None
    p.set_param("WPNAV_SPEED", 50.0, M.MAV_PARAM_TYPE_REAL32)
    p.command_long(M.MAV_CMD_NAV_TAKEOFF, (0, 0, 0, 0, 0, 0, 1.0))
    p.run(4.0)
    p.position_target_local(3.0, 0.0, 1.0, None)
    peak = 0.0
    for _ in range(100):
        p.run(0.05)
        s = p.sim.quad.s
        peak = max(peak, math.hypot(s.v[0], s.v[1]))
    assert peak <= 0.6


def test_px4_params_are_bytewise():
    p = SimPilot("px4")
    p.request_params()
    p.run(0.1)
    values = {m.param_id: m for m in p.msgs if m.get_type() == "PARAM_VALUE"}
    entry = values["COM_RC_IN_MODE"]
    assert entry.param_type == M.MAV_PARAM_TYPE_INT32
    import struct

    assert struct.unpack("<i", struct.pack("<f", entry.param_value))[0] == 3
