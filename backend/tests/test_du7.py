"""ANetDU7 as the default simulator, control frames in flight and speed-limited tracking (12 §2-§4)."""

from __future__ import annotations

import itertools
import math

import pytest
from conftest import open_agent

from relay import plans, skills
from relay.authority import Actor
from relay.errors import RelayError
from relay.mav.mavlink import M

OP = Actor.operator()
pytestmark = pytest.mark.product_defaults


def boot(svc, stepper, **kwargs):
    svc.update_settings({"safety_volume": {"x": 12.0, "y": 12.0, "z": 4.0}})  # room for 2 m legs
    ids = svc.add_sims(1, autopilot_given=False, **kwargs)
    stepper.run(2.5)
    v = svc.vehicles[ids[0]]
    v.driver.sim.keep_received = 40000
    assert v.link_state == "ok" and v.capabilities.position
    return v


def targets(v, since=0.0):
    return [
        m for t, m in v.driver.sim.received if t >= since and m.get_type() == "SET_POSITION_TARGET_LOCAL_NED"
    ]


def airborne(svc, stepper, alt=1.5, **kwargs):
    v = boot(svc, stepper, **kwargs)
    session, agent = open_agent(svc)
    svc.claim(v.id, agent)
    svc.run_skill(v.id, agent, "takeoff", {"alt": alt})
    assert stepper.until(lambda: v.skill.state != "running", 40, [session]), v.skill.view()
    assert v.skill.state == "done", v.skill.view()
    return v, session, agent


# ------------------------------------------------------------------ the default simulator


def test_new_simulator_is_an_anet_du7_flying_px4(svc, stepper):
    v = boot(svc, stepper)
    view = svc.vehicle_detail(v.id)
    assert view["model"] == "anet-du7" and view["autopilot"] == "PX4"
    assert view["sim"]["frame"] == "anet-du7"
    frame = v.driver.sim.quad.frame
    assert (frame.mass_kg, frame.wheelbase_m, frame.cells, frame.capacity_mah) == (2.5, 0.5, 4, 5300.0)
    assert view["nav"]["frame"] == "flu" and view["nav"]["position"] is not None
    assert view["gimbal"] is not None and view["gimbal"]["kind"] == "sim"  # 12 §5: it carries its pod


def test_the_small_aircraft_is_still_available(svc, stepper):
    v = boot(svc, stepper, frame={"preset": "micro-120"})
    view = svc.vehicle_detail(v.id)
    assert view["model"] == "micro" and view["autopilot"] == "ArduPilot"
    assert view["sim"]["frame"] == "micro-120" and view["gimbal"] is None
    svc.update_settings({"sim_frame_default": "micro-120"})
    ids = svc.add_sims(1, autopilot_given=False)
    assert svc.vehicle_detail(ids[0])["model"] == "micro"


def test_an_explicit_autopilot_is_kept(svc, stepper):
    ids = svc.add_sims(1, "ardupilot")
    stepper.run(2.5)
    view = svc.vehicle_detail(ids[0])
    assert view["model"] == "anet-du7" and view["autopilot"] == "ArduPilot"


def test_settings_validate_the_new_fields(svc):
    for patch in ({"control_frame": "ned"}, {"sim_frame_default": "x500"}, {"real_model_default": "u8"}):
        with pytest.raises(RelayError):
            svc.update_settings(patch)
    assert svc.update_settings({"control_frame": "frd"})["control_frame"] == "frd"


def test_a_simulator_model_follows_its_frame(svc, stepper):
    v = boot(svc, stepper)
    with pytest.raises(RelayError):
        svc.set_model(v.id, "micro")


def test_a_real_aircraft_model_is_unconfirmed_until_chosen(svc):
    import types

    from relay.fleet import Vehicle

    v = Vehicle("real-1", "real", "Quad 01", types.SimpleNamespace(), host="10.0.0.9", port=14550)
    svc._attach(v)
    svc.vehicles[v.id] = v
    try:
        assert v.model == "anet-du7" and not svc.model_confirmed(v)  # the default: nobody has said what it is
        svc.set_model(v.id, "anet-du7")  # choosing the model already shown confirms it
        assert v.model == "anet-du7" and svc.model_confirmed(v)
        svc.set_model(v.id, "micro")
        assert v.model == "micro" and svc.model_confirmed(v)
    finally:
        svc.vehicles.pop(v.id, None)


# ------------------------------------------------------------------ take-off as the onboard node flies it


def test_takeoff_arms_in_posctl_then_offboard_on_body_velocity(svc, stepper):
    v = boot(svc, stepper)
    session, agent = open_agent(svc)
    svc.claim(v.id, agent)
    t0 = stepper.clock.t
    svc.run_skill(v.id, agent, "takeoff", {"alt": 2.0})
    assert v.skill.params["speed"] == 0.5 and v.skill.params["settle_s"] == 2.0
    modes_seen, armed_in = [], []
    for _ in range(40 * 20):
        stepper.run(0.05, [session])
        if not modes_seen or modes_seen[-1] != v.mode:
            modes_seen.append(v.mode)
        if v.armed and not armed_in:
            armed_in.append(v.mode)
        assert v.speed() is None or v.speed() <= 0.5 + 0.2  # never faster than the limit (plus overshoot)
        if v.skill.state != "running":
            break
    assert v.skill.state == "done", v.skill.view()
    assert armed_in == ["POSCTL"]  # armed before OFFBOARD, as u7_mavros_simple.py does
    assert modes_seen[-1] == "OFFBOARD" and modes_seen.index("POSCTL") < modes_seen.index("OFFBOARD")
    assert abs(v.altitude() - 2.0) <= skills.TRACK_TOLERANCE_M
    sent = targets(v, t0)
    assert sent and all(m.coordinate_frame == M.MAV_FRAME_BODY_NED for m in sent)
    assert all(m.type_mask == 0x05C7 and m.yaw_rate == 0.0 for m in sent)  # velocity + yaw rate only
    assert max(-m.vz for m in sent) <= 0.5 + 1e-6
    # the stream starts before arming: at least the 2 s of zero velocity the test node sends
    assert sent[0].vx == sent[0].vy == sent[0].vz == 0.0
    # the commanded velocity never changes faster than 0.5 m/s2
    for a, b in itertools.pairwise(sent):
        assert abs(b.vz - a.vz) <= 0.5 * 0.2 + 1e-6


def test_du7_box_plan_flies_the_test_route(svc, stepper):
    v = boot(svc, stepper)
    session, agent = open_agent(svc)
    svc.claim(v.id, agent)
    origin = v.world_enu()
    svc.run_plan(v.id, agent, [dict(s) for s in plans.builtin("du7_box")["steps"]], "du7_box")
    corners, seen = [], set()
    for _ in range(200 * 20):
        stepper.run(0.05, [session])
        plan = v.plan
        if plan is not None and plan.running and plan.index not in seen and plan.index >= 1:
            seen.add(plan.index)
            corners.append(tuple(c - o for c, o in zip(v.world_enu(), origin, strict=True)))
        if plan is None or not plan.running:
            break
    assert v.plan is not None and v.plan.state == "done", v.plan.view()
    assert stepper.until(lambda: not v.armed, 10, [session])
    # after the take-off, forward (north at heading 0), back, left (west), back
    expect = [(0.0, 0.0, 2.0), (0.0, 1.0, 2.0), (0.0, 0.0, 2.0), (-1.0, 0.0, 2.0), (0.0, 0.0, 2.0)]
    assert len(corners) == 5
    for got, want in zip(corners, expect, strict=True):
        assert math.dist(got, want) <= 0.3, (got, want)


# ------------------------------------------------------------------ control frames in flight


def displacement(svc, stepper, v, session, agent, params, seconds=4.0):
    start = v.world_enu()
    svc.run_skill(v.id, agent, "velocity", {"seconds": seconds, **params})
    assert stepper.until(lambda: v.skill.state != "running", seconds + 20, [session])
    assert v.skill.state == "done", v.skill.view()
    return tuple(c - s for c, s in zip(v.world_enu(), start, strict=True))


def test_body_velocity_defaults_to_flu(svc, stepper):
    v, session, agent = airborne(svc, stepper)
    assert abs(v.heading()) < 0.05  # nose north
    east, north, up = displacement(svc, stepper, v, session, agent, {"vx": 0.5})
    assert north > 1.2 and abs(east) < 0.3 and abs(up) < 0.3  # x is forward
    east, north, up = displacement(svc, stepper, v, session, agent, {"vy": 0.5})
    assert east < -1.2 and abs(north) < 0.3  # y is left: west with the nose north
    east, north, up = displacement(svc, stepper, v, session, agent, {"vz": 0.3})
    assert up > 0.7  # z is up


def test_frd_per_request_and_as_the_setting(svc, stepper):
    v, session, agent = airborne(svc, stepper)
    east, north, up = displacement(svc, stepper, v, session, agent, {"vy": 0.5, "frame": "frd"})
    assert east > 1.2 and abs(north) < 0.3  # y is right
    svc.update_settings({"control_frame": "frd"})
    east, north, up = displacement(svc, stepper, v, session, agent, {"vz": 0.3})
    assert up < -0.7  # z is down
    assert svc.vehicle_detail(v.id)["nav"]["frame"] == "frd"


def test_body_velocity_follows_the_nose(svc, stepper):
    v, session, agent = airborne(svc, stepper)
    svc.run_skill(v.id, agent, "yaw", {"deg": 90})  # flu: counter-clockwise, the nose turns west
    assert stepper.until(lambda: v.skill.state != "running", 20, [session]) and v.skill.state == "done"
    assert math.degrees(v.heading()) == pytest.approx(-90.0, abs=6.0)
    east, north, _up = displacement(svc, stepper, v, session, agent, {"vx": 0.5})
    assert east < -1.2 and abs(north) < 0.4  # forward is now west
    nav = svc.vehicle_detail(v.id)["nav"]
    assert nav["yaw_deg"] == pytest.approx(180.0, abs=6.0) or nav["yaw_deg"] == pytest.approx(-180.0, abs=6.0)


def test_local_velocity_reference(svc, stepper):
    v, session, agent = airborne(svc, stepper)
    east, north, _up = displacement(svc, stepper, v, session, agent, {"vx": 0.5, "reference": "local"})
    assert east > 1.2 and abs(north) < 0.3  # flu local x is east
    sent = targets(v)[-1]
    assert sent.coordinate_frame == M.MAV_FRAME_LOCAL_NED


def test_goto_in_both_frames(svc, stepper):
    v, session, agent = airborne(svc, stepper)
    home = v.world_enu()
    svc.run_skill(v.id, agent, "goto", {"x": home[0] + 1.0, "y": home[1], "z": 1.5})
    assert stepper.until(lambda: v.skill.state != "running", 60, [session]) and v.skill.state == "done"
    assert v.world_enu()[0] - home[0] == pytest.approx(1.0, abs=0.25)  # flu x is east
    # frd: x north, y east, z down
    svc.run_skill(v.id, agent, "goto", {"x": home[1] + 1.0, "y": home[0], "z": -1.5, "frame": "frd"})
    assert stepper.until(lambda: v.skill.state != "running", 60, [session]) and v.skill.state == "done"
    now = v.world_enu()
    assert now[1] - home[1] == pytest.approx(1.0, abs=0.25) and now[0] - home[0] == pytest.approx(
        0.0, abs=0.25
    )


def test_move_named_and_generic(svc, stepper):
    v, session, agent = airborne(svc, stepper)
    start = v.world_enu()
    svc.run_skill(v.id, agent, "move", {"y": 1.0})  # flu: left
    assert stepper.until(lambda: v.skill.state != "running", 60, [session]) and v.skill.state == "done"
    assert v.world_enu()[0] - start[0] == pytest.approx(-1.0, abs=0.25)
    svc.run_skill(v.id, agent, "move", {"left": -1.0})
    assert stepper.until(lambda: v.skill.state != "running", 60, [session]) and v.skill.state == "done"
    assert v.world_enu()[0] - start[0] == pytest.approx(0.0, abs=0.25)
    with pytest.raises(RelayError):
        svc.run_skill(v.id, agent, "move", {"x": 1.0, "forward": 1.0})


# ------------------------------------------------------------------ monitors


def test_tracking_lands_when_the_position_jumps(svc, stepper):
    v, session, agent = airborne(svc, stepper)
    svc.run_skill(v.id, agent, "move", {"forward": 2.0})
    stepper.run(1.0, [session])
    assert v.skill.state == "running"
    v.driver.sim.quad.s.p[0] += 1.0  # the estimate (here: the truth) steps by a metre
    assert stepper.until(lambda: v.skill.state != "running", 3, [session])
    assert v.skill.state == "failed" and "position jumped" in v.skill.detail
    assert stepper.until(lambda: v.mode == "LAND", 3, [session])


def test_tracking_stops_when_the_pilot_changes_the_mode(svc, stepper):
    v, session, agent = airborne(svc, stepper)
    svc.run_skill(v.id, agent, "move", {"forward": 2.0})
    stepper.run(1.0, [session])
    v.driver.set_mode("POSCTL")  # the pilot's switch
    assert stepper.until(lambda: v.skill.state != "running", 3, [session])
    assert v.skill.state == "failed" and "mode changed" in v.skill.detail
    t = stepper.clock.t
    stepper.run(1.0, [session])
    assert not targets(v, t + 0.2)  # no more setpoints: the aircraft is the pilot's
    assert v.mode == "POSCTL"


def test_speed_asks_for_tracking_on_any_aircraft(svc, stepper):
    v, session, agent = airborne(svc, stepper, frame={"preset": "micro-120"})
    # the small aircraft keeps the autopilot's own take-off
    assert v.model == "micro" and "speed" not in v.skill.params
    t = stepper.clock.t
    svc.run_skill(v.id, agent, "move", {"forward": 1.0, "speed": 0.4})
    assert stepper.until(lambda: v.skill.state != "running", 60, [session]) and v.skill.state == "done"
    sent = targets(v, t)
    assert sent and all(m.coordinate_frame == M.MAV_FRAME_BODY_NED for m in sent)
