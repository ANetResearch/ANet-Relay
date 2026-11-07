"""Device-level surface: messages, parameters, raw commands, motors, actuators, RC, skills (04 §2)."""

import math
import time

import pytest
from conftest import free_udp_port, open_agent
from fake_vehicle import FakeVehicle
from fastapi.testclient import TestClient

from relay import device, plans
from relay.api.app import create_app
from relay.authority import Actor
from relay.config import Settings
from relay.errors import RelayError
from relay.fleet import FleetService
from relay.history import History
from relay.mav.mavlink import M

FLAVOURS = ["ardupilot", "px4"]
OP = Actor.operator()


def boot(svc, stepper, flavour="ardupilot", position=True):
    (vid,) = svc.add_sims(1, flavour, position)
    stepper.run(2.5)
    v = svc.vehicles[vid]
    assert v.link_state == "ok"
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    return vid, v, session, agent


def code(excinfo) -> str:
    return excinfo.value.code


def servo(v) -> list[int]:
    return list(v.state["servo16"] or [0] * 16)


def peak_servo(stepper, v, seconds, session):
    """Highest PWM seen on each output while the clock runs."""
    peaks = [0] * 16
    end = stepper.clock.t + seconds
    while stepper.clock.t < end:
        stepper.run(0.05, [session])
        peaks = [max(a, b) for a, b in zip(peaks, servo(v), strict=True)]
    return peaks


def true_state(v):
    s = v.driver.sim.quad.s
    return s


def fly(svc, stepper, vid, v, session, agent, alt=1.0):
    svc.run_skill(vid, agent, "takeoff", {"alt": alt})
    assert stepper.until(lambda: v.skill.state == "done", 30, [session]), v.skill.detail
    stepper.run(1.0, [session])


# ---------------------------------------------------------------------------- catalogue and reads


def test_catalogue_covers_the_contract_and_resolves_names():
    names = {c["name"] for c in device.CATALOGUE}
    for required in (
        "COMPONENT_ARM_DISARM",
        "DO_SET_MODE",
        "NAV_TAKEOFF",
        "ACTUATOR_TEST",
        "DO_FLIGHTTERMINATION",
        "PREFLIGHT_REBOOT_SHUTDOWN",
        "VIDEO_STOP_CAPTURE",
    ):
        assert f"MAV_CMD_{required}" in names
    for entry in device.CATALOGUE:
        assert {
            "id",
            "name",
            "label_en",
            "label_zh",
            "params",
            "danger",
            "needs_disarmed",
            "autopilots",
        } <= set(entry)
    assert device.find_command("NAV_TAKEOFF")["id"] == 22
    assert device.find_command("MAV_CMD_NAV_TAKEOFF")["id"] == 22
    assert device.find_command(22)["name"] == "MAV_CMD_NAV_TAKEOFF"
    with pytest.raises(RelayError) as exc:
        device.find_command("MAV_CMD_NAV_WAYPOINT")
    assert exc.value.code == "command_not_allowed"


@pytest.mark.parametrize("flavour", FLAVOURS)
def test_overview_and_single_message(svc, stepper, flavour):
    vid, _v, _s, _a = boot(svc, stepper, flavour)
    stepper.run(2.0)
    info = svc.device_overview(vid)
    assert info["motors"]["count"] == 4 and info["motors"]["layout"] == "quad"
    assert info["motors"]["test_supported"] is True and len(info["motors"]["outputs_us"]) == 4
    assert len(info["servos"]) == 16 and info["frame"]["name"] == "QUADROTOR"
    assert "ATTITUDE" in info["message_rates"] and info["message_rates"]["ATTITUDE"] > 5
    frame = svc.message_get(vid, "attitude")
    assert frame["name"] == "ATTITUDE" and "roll" in frame["fields"] and frame["hz"] > 5
    with pytest.raises(RelayError) as exc:
        svc.message_get(vid, "GPS2_RAW")
    assert code(exc) == "message_not_seen"


@pytest.mark.parametrize("flavour", FLAVOURS)
def test_param_by_name_without_waiting_returns_loaded_value(svc, stepper, flavour):
    vid, _v, _s, agent = boot(svc, stepper, flavour)
    stepper.run(8.0)  # the table loads
    name = "ANGLE_MAX" if flavour == "ardupilot" else "MPC_TILTMAX_AIR"
    assert svc.param_get(vid, name)["name"] == name
    out = svc.params_read(vid, agent, [name, "NO_SUCH_PARAM"])
    assert name in out["values"] and out["missing"] == ["NO_SUCH_PARAM"]
    with pytest.raises(RelayError) as exc:
        svc.param_get(vid, "NO_SUCH_PARAM")
    assert code(exc) == "param_not_found"


# ---------------------------------------------------------------------------- motors and actuators


@pytest.mark.parametrize("flavour", FLAVOURS)
def test_single_motor_test_shows_on_one_output_then_returns(svc, stepper, flavour):
    vid, v, session, agent = boot(svc, stepper, flavour)
    out = svc.motors_test(vid, agent, 2, 30, 1.0, None)  # a simulated aircraft needs no confirm word
    assert out["motor"] == 2
    peaks = peak_servo(stepper, v, 1.2, session)
    assert peaks[1] >= 1250 and peaks[0] == peaks[2] == peaks[3] == 1000
    stepper.run(1.0, [session])
    assert servo(v)[1] == 1000
    assert [e["kind"] for e in svc.events.after(0, 500)].count("motor_test") == 1


@pytest.mark.parametrize("flavour", FLAVOURS)
def test_all_motors_run_in_turn_and_stop_ends_the_test(svc, stepper, flavour):
    vid, v, session, agent = boot(svc, stepper, flavour)
    svc.motors_test(vid, agent, "all", 20, 0.4, None)
    order = []
    end = stepper.clock.t + 3.0
    while stepper.clock.t < end:
        stepper.run(0.05, [session])
        running = tuple(i for i, us in enumerate(servo(v)[:4]) if us > 1100)
        if running and (not order or order[-1] != running):
            order.append(running)
    assert [r for r in order if len(r) == 1] == [(0,), (1,), (2,), (3,)]
    svc.motors_test(vid, agent, 3, 30, 5.0, None)
    stepper.run(0.5, [session])
    assert servo(v)[2] > 1100
    svc.motors_stop(vid, agent)
    stepper.run(0.6, [session])
    assert servo(v)[:4] == [1000] * 4
    assert v.motor_seq is None


@pytest.mark.parametrize("flavour", FLAVOURS)
def test_motor_test_rules(svc, stepper, flavour):
    vid, v, session, agent = boot(svc, stepper, flavour)
    for bad in (0, 9, "x"):
        with pytest.raises(RelayError) as exc:
            svc.motors_test(vid, agent, bad, 10, 1.0, None)
        assert code(exc) == "invalid_request"
    with pytest.raises(RelayError) as exc:
        svc.motors_test(vid, agent, 1, 10, 9.0, None)
    assert code(exc) == "invalid_request"
    svc.arm(vid, agent, True, None, wait=False)
    assert stepper.until(lambda: v.armed, 5, [session])
    with pytest.raises(RelayError) as exc:
        svc.motors_test(vid, agent, 1, 10, 1.0, None)
    assert code(exc) == "must_be_disarmed" and exc.value.status == 409
    with pytest.raises(RelayError) as exc:
        svc.actuator_set(vid, agent, 1, 0.2, None, 0, None)
    assert code(exc) == "must_be_disarmed"
    # Another session cannot drive the aircraft.
    _other_session, other = open_agent(svc, "other")
    with pytest.raises(RelayError) as exc:
        svc.motors_stop(vid, other)
    assert code(exc) == "claimed_by_other"


@pytest.mark.parametrize("flavour", FLAVOURS)
def test_actuator_set_reaches_a_servo_output_and_releases(svc, stepper, flavour):
    vid, v, session, agent = boot(svc, stepper, flavour)
    svc.actuator_set(vid, agent, 6, None, 1200, 1.0, None)  # timed: released afterwards
    stepper.run(0.6, [session])
    assert servo(v)[5] == 1200
    assert stepper.until(lambda: servo(v)[5] == 0, 4, [session])
    out = svc.actuator_set(vid, agent, 6, None, 1700, 0, None)  # held until changed
    assert out["pwm"] == 1700 and out["motor"] is False
    stepper.run(3.0, [session])
    assert servo(v)[5] == 1700
    svc.actuator_set(vid, agent, 1, 0.5, None, 1.0, None)  # a motor output
    assert stepper.until(lambda: servo(v)[0] == 1500, 3, [session])
    for bad in ({"value": None, "pwm": None}, {"value": 0.1, "pwm": 1500}):
        with pytest.raises(RelayError) as exc:
            svc.actuator_set(vid, agent, 6, bad["value"], bad["pwm"], 0, None)
        assert code(exc) == "invalid_request"


# ---------------------------------------------------------------------------- raw commands


@pytest.mark.parametrize("flavour", FLAVOURS)
def test_catalogue_commands_are_acknowledged(svc, stepper, flavour):
    vid, v, session, agent = boot(svc, stepper, flavour)
    expect = {
        "DO_SET_RELAY": M.MAV_RESULT_ACCEPTED,
        "DO_CHANGE_SPEED": M.MAV_RESULT_ACCEPTED,
        "DO_SET_ROI_NONE": M.MAV_RESULT_ACCEPTED,
        "DO_SET_SERVO": M.MAV_RESULT_ACCEPTED,
        "REQUEST_MESSAGE": M.MAV_RESULT_ACCEPTED,
        "MISSION_START": M.MAV_RESULT_FAILED,
        "DO_GRIPPER": M.MAV_RESULT_UNSUPPORTED,
        "IMAGE_START_CAPTURE": M.MAV_RESULT_UNSUPPORTED,
    }
    params = {
        "DO_SET_SERVO": [8, 1600],
        "REQUEST_MESSAGE": [M.MAVLINK_MSG_ID_VIBRATION],
        "DO_SET_RELAY": [0, 1],
    }
    for name in expect:
        svc.raw_command(vid, agent, name, params.get(name, []))
    stepper.run(0.5, [session])
    for name, result in expect.items():
        ack = v.state["acks"][device.find_command(name)["id"]]
        assert ack["result"] == result, name
    assert servo(v)[7] == 1600
    events = [e for e in svc.events.after(0, 500) if e["kind"] == "device_command"]
    assert len(events) == len(expect)
    with pytest.raises(RelayError) as exc:
        svc.raw_command(vid, agent, "MAV_CMD_NAV_WAYPOINT", [])
    assert code(exc) == "command_not_allowed"
    with pytest.raises(RelayError) as exc:
        svc.raw_command(vid, agent, "DO_SET_RELAY", [0] * 8)
    assert code(exc) == "invalid_request"


@pytest.mark.parametrize("flavour", FLAVOURS)
def test_raw_command_int_and_danger_levels(svc, stepper, flavour):
    vid, v, session, agent = boot(svc, stepper, flavour)
    svc.raw_command(vid, agent, 179, [1], frame=0, use_int=True)  # DO_SET_HOME here, as COMMAND_INT
    stepper.run(0.5, [session])
    assert v.state["acks"][179]["result"] == M.MAV_RESULT_ACCEPTED
    # Motor-level and terminate-level raw commands: terminate needs its word even on a simulated aircraft.
    with pytest.raises(RelayError) as exc:
        svc.raw_command(vid, agent, "DO_FLIGHTTERMINATION", [1])
    assert code(exc) == "confirm_required" and exc.value.detail["expected"] == "TERMINATE"
    svc.raw_command(vid, agent, "DO_MOTOR_TEST", [1, 0, 25, 1])  # simulated: no word
    assert stepper.until(lambda: servo(v)[0] > 1100, 2, [session])
    assert device.danger_level(device.find_command("DO_SET_SERVO"), [2, 1500], 4) == "motor"
    assert device.danger_level(device.find_command("DO_SET_SERVO"), [8, 1500], 4) is None
    assert device.danger_level(device.find_command("COMPONENT_ARM_DISARM"), [1, 21196], 4) == "force_arm"


@pytest.mark.parametrize("flavour", FLAVOURS)
def test_message_interval_request_and_home(svc, stepper, flavour):
    vid, v, session, agent = boot(svc, stepper, flavour)
    stepper.run(2.0, [session])
    svc.message_interval(vid, agent, "ATTITUDE", 2)
    stepper.run(4.0, [session])
    rate = svc.device_overview(vid)["message_rates"]["ATTITUDE"]
    assert 1.0 <= rate <= 3.5
    svc.message_interval(vid, agent, "ATTITUDE", 0)
    stepper.run(3.0, [session])
    count = v.state["messages"]["ATTITUDE"]["count"]
    stepper.run(1.0, [session])
    assert v.state["messages"]["ATTITUDE"]["count"] == count
    svc.request_message(vid, agent, "ATTITUDE")
    stepper.run(0.5, [session])
    assert v.state["messages"]["ATTITUDE"]["count"] == count + 1
    with pytest.raises(RelayError) as exc:
        svc.message_interval(vid, agent, "NOT_A_MESSAGE", 5)
    assert code(exc) == "invalid_request"
    svc.set_home(vid, agent, False, 47.5, 8.6, 500.0)
    stepper.run(0.5, [session])
    assert v.state["home"]["lat"] == pytest.approx(47.5, abs=1e-5)
    with pytest.raises(RelayError) as exc:
        svc.set_home(vid, agent, False, 47.5)
    assert code(exc) == "invalid_request"


@pytest.mark.parametrize("flavour", FLAVOURS)
def test_calibrate_and_reboot_on_a_simulated_aircraft(svc, stepper, flavour):
    vid, v, session, agent = boot(svc, stepper, flavour)
    svc.calibrate(vid, agent, "gyro", None)
    stepper.run(0.5, [session])
    assert any("Calibrating gyroscope" in s["text"] for s in v.state["statustext"])
    with pytest.raises(RelayError) as exc:
        svc.calibrate(vid, agent, "compass", None)
    assert code(exc) == "invalid_request"
    stepper.run(2.0, [session])
    svc.reboot(vid, agent, None)
    stepper.run(0.3, [session])
    assert "ATTITUDE" not in v.state["messages"]  # the streams are gone with the reboot
    assert stepper.until(lambda: "ATTITUDE" in v.state["messages"], 20, [session])
    assert v.link_state == "ok" and "rebooting" in [e["kind"] for e in svc.events.after(0, 500)]


# ---------------------------------------------------------------------------- RC channels


def test_rc_override_ardupilot_channels_and_exclusion(svc, stepper):
    vid, v, session, agent = boot(svc, stepper, "ardupilot")
    out = svc.rc_override(vid, agent, {"8": 1800, "10": 1234}, {"aux1": 0.5})
    assert out["accepted"] is True and v.rc_frame is not None
    stepper.run(1.0, [session])
    rc = v.state["rc"]
    assert rc[7] == 1800 and rc[9] == 1234 and rc[4] == 1750
    # Sticks replace the frame, and the frame replaces sticks.
    svc.sticks(vid, agent, {"roll": 0.0, "pitch": 0.0, "yaw": 0.0, "throttle": 0.0})
    assert v.rc_frame is None and v.sticks is not None
    svc.rc_override(vid, agent, {"8": 1700}, None)
    assert v.sticks is None and v.rc_frame is not None
    stepper.run(2.0)  # no renewal: the frame goes stale after 0.8 s and the channels are released
    assert v.rc_frame is None
    assert stepper.until(lambda: len(v.state["rc"]) < 8 or v.state["rc"][7] == 0, 3)
    for bad in ({"0": 1500}, {"19": 1500}, {"1": 900}):
        with pytest.raises(RelayError) as exc:
            svc.rc_override(vid, agent, bad, None)
        assert code(exc) == "invalid_request"
    with pytest.raises(RelayError):
        svc.rc_override(vid, agent, None, {"bogus": 0.1})


def test_rc_override_px4_manual_control_with_aux(svc, stepper):
    vid, v, session, agent = boot(svc, stepper, "px4")
    svc.rc_override(vid, agent, None, {"roll": 0.2, "throttle": 0.5, "aux2": -0.5})
    stepper.run(1.0, [session])
    assert v.state["rc"][5] == 1250 and v.state["rc"][0] == 1600
    assert v.driver.sent.get("MANUAL_CONTROL", 0) > 20
    with pytest.raises(RelayError) as exc:
        svc.rc_override(vid, agent, {"12": 1500}, None)
    assert code(exc) == "invalid_request"


# ---------------------------------------------------------------------------- termination and force arm


@pytest.mark.parametrize("flavour", FLAVOURS)
def test_force_arm_and_terminate(svc, stepper, flavour):
    vid, v, session, agent = boot(svc, stepper, flavour)
    svc.arm(vid, agent, True, None, wait=False, force=True)
    assert stepper.until(lambda: v.armed, 5, [session])
    with pytest.raises(RelayError) as exc:
        svc.terminate(vid, agent, None)
    assert code(exc) == "confirm_required" and exc.value.detail["expected"] == "TERMINATE"
    assert v.armed
    svc.terminate(vid, agent, "TERMINATE")
    assert stepper.until(lambda: not v.armed, 5, [session])
    assert "terminated" in [e["kind"] for e in svc.events.after(0, 500)]


def test_terminate_stops_a_flying_aircraft_and_its_plan(svc, stepper):
    vid, v, session, agent = boot(svc, stepper, "ardupilot")
    fly(svc, stepper, vid, v, session, agent)
    svc.run_plan(vid, agent, [{"action": "hover", "seconds": 30}])
    svc.terminate(vid, agent, "TERMINATE")
    assert stepper.until(lambda: not v.armed, 5, [session])
    assert v.plan.state in ("cancelled", "failed")
    assert true_state(v).motor < 0.2


# ---------------------------------------------------------------------------- skills


@pytest.mark.parametrize("flavour", FLAVOURS)
def test_sticks_skill_runs_for_the_requested_time(svc, stepper, flavour):
    vid, v, session, agent = boot(svc, stepper, flavour, position=False)
    fly(svc, stepper, vid, v, session, agent)
    s = true_state(v)
    z0, yaw0 = s.p[2], s.yaw
    svc.run_skill(
        vid, agent, "sticks", {"roll": 0.0, "pitch": 0.0, "yaw": 0.5, "throttle": 0.5, "seconds": 1.5}
    )
    t0 = stepper.clock.t
    assert stepper.until(lambda: v.skill.state == "done", 6, [session])
    assert 1.4 <= stepper.clock.t - t0 <= 2.2
    turned = abs(math.degrees((s.yaw - yaw0 + math.pi) % (2 * math.pi) - math.pi))
    assert turned > 15 and abs(s.p[2] - z0) < 0.5
    with pytest.raises(RelayError) as exc:
        svc.run_skill(vid, agent, "sticks", {"throttle": 0.5, "seconds": 11})
    assert code(exc) == "invalid_request"
    with pytest.raises(RelayError) as exc:
        svc.run_skill(vid, agent, "sticks", {"roll": 2, "throttle": 0.5, "seconds": 1})
    assert code(exc) == "invalid_request"


def test_sticks_skill_needs_an_armed_aircraft(svc, stepper):
    vid, _v, _session, agent = boot(svc, stepper, "ardupilot", position=False)
    with pytest.raises(RelayError) as exc:
        svc.run_skill(vid, agent, "sticks", {"throttle": 0.5, "seconds": 1})
    assert code(exc) == "not_armed"


@pytest.mark.parametrize("flavour", FLAVOURS)
def test_attitude_skill_tilts_a_no_position_aircraft_and_returns_to_altitude_hold(svc, stepper, flavour):
    vid, v, session, agent = boot(svc, stepper, flavour, position=False)
    fly(svc, stepper, vid, v, session, agent)
    s = true_state(v)
    svc.run_skill(
        vid, agent, "attitude", {"roll_deg": 15, "pitch_deg": -10, "yaw_rate_dps": 20, "seconds": 2}
    )
    seen = {"roll": 0.0, "pitch": 0.0, "modes": set()}
    yaw0 = s.yaw
    end = stepper.clock.t + 20
    while stepper.clock.t < end and v.skill.state == "running":
        stepper.run(0.05, [session])
        seen["roll"] = max(seen["roll"], math.degrees(s.roll))
        seen["pitch"] = min(seen["pitch"], math.degrees(s.pitch))
        seen["modes"].add(v.mode)
    assert v.skill.state == "done", v.skill.detail
    assert 11 <= seen["roll"] <= 17 and -12 <= seen["pitch"] <= -7
    assert ("GUIDED_NOGPS" if flavour == "ardupilot" else "OFFBOARD") in seen["modes"]
    assert math.degrees(s.yaw - yaw0) > 10 or math.degrees(yaw0 - s.yaw) > 350
    stepper.run(1.5, [session])
    assert v.mode == ("ALT_HOLD" if flavour == "ardupilot" else "ALTCTL")
    assert abs(math.degrees(s.roll)) < 4 and v.armed and s.p[2] > 0.3


def test_attitude_skill_needs_the_air(svc, stepper):
    vid, _v, _session, agent = boot(svc, stepper, "ardupilot", position=False)
    with pytest.raises(RelayError) as exc:
        svc.run_skill(vid, agent, "attitude", {"roll_deg": 5, "seconds": 1})
    assert code(exc) == "not_airborne"


@pytest.mark.parametrize("flavour", FLAVOURS)
def test_velocity_skill_moves_a_position_aircraft_and_holds(svc, stepper, flavour):
    vid, v, session, agent = boot(svc, stepper, flavour, position=True)
    fly(svc, stepper, vid, v, session, agent)
    s = true_state(v)
    x0, y0 = s.p[0], s.p[1]
    svc.run_skill(vid, agent, "velocity", {"vx": 0.5, "vy": 0.0, "vz": 0.0, "seconds": 2})
    assert stepper.until(lambda: v.skill.state != "running", 15, [session])
    assert v.skill.state == "done", v.skill.detail
    assert s.p[0] - x0 > 0.6 and abs(s.p[1] - y0) < 0.4
    stepper.run(2.0, [session])
    moved = s.p[0]
    stepper.run(2.0, [session])
    assert abs(s.p[0] - moved) < 0.2  # it holds the point it braked to


def test_velocity_skill_stops_at_the_safety_volume(svc, stepper):
    vid, v, session, agent = boot(svc, stepper, "ardupilot", position=True)
    fly(svc, stepper, vid, v, session, agent)
    s = true_state(v)
    svc.run_skill(vid, agent, "velocity", {"vx": 1.5, "seconds": 10})
    assert stepper.until(lambda: v.skill.state != "running", 15, [session])
    assert v.skill.state == "failed" and v.skill.detail == "outside_safety_volume"
    stepper.run(2.0, [session])
    half = svc.settings.safety_volume.x / 2
    assert s.p[0] <= half + 0.3
    with pytest.raises(RelayError) as exc:
        svc.run_skill(vid, agent, "velocity", {"vx": 3.0, "seconds": 1})
    assert code(exc) == "invalid_request"


def test_velocity_skill_needs_a_position_estimate(svc, stepper):
    vid, v, session, agent = boot(svc, stepper, "ardupilot", position=False)
    fly(svc, stepper, vid, v, session, agent)
    with pytest.raises(RelayError) as exc:
        svc.run_skill(vid, agent, "velocity", {"vx": 0.3, "seconds": 1})
    assert code(exc) == "capability_missing"


# ---------------------------------------------------------------------------- plans


def test_plan_validation_of_the_new_actions():
    steps = plans.normalise_steps(
        [
            {"action": "sticks", "throttle": 0.5, "yaw": 0.3, "seconds": 1},
            {"action": "attitude", "roll_deg": 10, "seconds": 1},
            {"action": "velocity", "vx": 0.5, "seconds": 1},
        ]
    )
    assert steps[0]["yaw"] == 0.3 and plans.skill_for(steps[1])[0] == "attitude"
    for bad in (
        [{"action": "sticks", "seconds": 1}],
        [{"action": "sticks", "throttle": 0.5, "yaw": 5, "seconds": 1}],
        [{"action": "attitude", "roll_deg": 45, "seconds": 1}],
        [{"action": "attitude", "roll_deg": 5}],
        [{"action": "velocity", "vx": 2.5, "seconds": 1}],
        [{"action": "velocity", "vx": 1, "seconds": 11}],
    ):
        with pytest.raises(RelayError):
            plans.normalise_steps(bad)
    # A goto still takes a heading in degrees.
    assert plans.normalise_steps([{"action": "goto", "x": 0, "y": 0, "z": 1, "yaw": 90}])[0]["yaw"] == 90


def test_plan_flies_the_new_actions(svc, stepper):
    vid, v, session, agent = boot(svc, stepper, "ardupilot", position=True)
    svc.run_plan(
        vid,
        agent,
        [
            {"action": "takeoff", "alt": 1.0},
            {"action": "sticks", "throttle": 0.5, "seconds": 0.5},
            {"action": "attitude", "roll_deg": 6, "seconds": 0.8},
            {"action": "hover", "seconds": 2},
            {"action": "velocity", "vx": 0.4, "seconds": 1},
            {"action": "hover", "seconds": 1},
            {"action": "land"},
        ],
        name="device",
    )
    end = stepper.clock.t + 90
    while stepper.clock.t < end and v.plan.running:
        stepper.run(0.05, [session])
    assert v.plan.state == "done", (v.plan.detail, v.plan.index)
    assert [e["data"]["action"] for e in svc.events.after(0, 2000) if e["kind"] == "plan_step"] == [
        "takeoff",
        "sticks",
        "attitude",
        "hover",
        "velocity",
        "hover",
        "land",
    ]


# ---------------------------------------------------------------------------- REST (threaded service)


@pytest.fixture
def client():
    svc = FleetService(listen=False, history=History(None), operator_token="op-token")
    with TestClient(create_app(svc), base_url="http://127.0.0.1") as c:
        c.svc = svc
        yield c


def wait_for(fn, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


def agent_headers(client, vid):
    body = client.post("/api/v1/sessions", json={"name": "device"}).json()
    headers = {"X-Relay-Session": body["token"]}
    assert client.post(f"/api/v1/vehicles/{vid}/claim", headers=headers).status_code == 200
    return headers


@pytest.mark.parametrize("flavour", FLAVOURS)
def test_rest_surface_waits_for_acks_and_reads(client, flavour):
    op = {"X-Relay-Operator": "op-token"}
    vid = client.post("/api/v1/sims", headers=op, json={"autopilot": flavour}).json()["added"][0]
    assert wait_for(lambda: client.svc.vehicles[vid].link_state == "ok")
    headers = agent_headers(client, vid)
    base = f"/api/v1/vehicles/{vid}"
    catalogue = client.get("/api/v1/commands").json()
    assert len(catalogue["commands"]) >= 29 and catalogue["confirm_words"]["force_arm"] == "FORCE"
    reply = client.post(
        f"{base}/command", headers=headers, json={"command": "DO_SET_RELAY", "params": [0, 1]}
    )
    assert reply.status_code == 200
    ack = reply.json()["ack"]
    assert ack["result_name"] == "ACCEPTED" and reply.json()["command"] == "MAV_CMD_DO_SET_RELAY"
    reply = client.post(
        f"{base}/command", headers=headers, json={"command": "MAV_CMD_DO_GRIPPER", "params": [1, 1]}
    )
    assert reply.json()["ack"]["result_name"] == "UNSUPPORTED"
    bad = client.post(f"{base}/command", headers=headers, json={"command": "MAV_CMD_NAV_WAYPOINT"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "command_not_allowed"
    assert client.post(f"{base}/command", json={"command": "DO_SET_RELAY"}).status_code == 401
    # Parameters: by name, with the wait, and the missing list.
    name = "ANGLE_MAX" if flavour == "ardupilot" else "MPC_TILTMAX_AIR"
    got = client.get(f"{base}/params/{name}")
    assert got.status_code == 200 and got.json()["name"] == name
    many = client.post(f"{base}/params/read", headers=headers, json={"names": [name, "NO_SUCH_PARAM"]})
    assert many.status_code == 200 and many.json()["missing"] == ["NO_SUCH_PARAM"]
    miss = client.get(f"{base}/params/NO_SUCH_PARAM")
    assert miss.status_code == 404 and miss.json()["error"]["code"] == "param_not_found"
    assert client.get(f"{base}/messages/ATTITUDE").json()["name"] == "ATTITUDE"
    assert client.get(f"{base}/messages/GPS2_RAW").json()["error"]["code"] == "message_not_seen"
    over = client.get(f"{base}/device").json()
    assert over["motors"]["count"] == 4
    # Motors through the new routes and the legacy alias.
    ok = client.post(
        f"{base}/motors/test", headers=headers, json={"motor": 1, "throttle_pct": 20, "seconds": 0.5}
    )
    assert ok.status_code == 200 and ok.json()["motor"] == 1
    legacy = client.post(
        f"{base}/motor-test", headers=op, json={"motor": 2, "throttle_pct": 10, "seconds": 1}
    )
    assert legacy.status_code == 200
    assert client.post(f"{base}/motors/stop", headers=headers).json()["stopped"] is True
    msg = client.post(f"{base}/message-interval", headers=headers, json={"message": "VFR_HUD", "hz": 5})
    assert msg.json()["ack"]["result_name"] == "ACCEPTED"
    arm = client.post(f"{base}/arm", headers=headers, json={"arm": True, "force": True})
    assert arm.status_code == 200 and arm.json()["armed"] is True
    late = client.post(f"{base}/motors/test", headers=headers, json={"motor": 1, "throttle_pct": 5})
    assert late.status_code == 409 and late.json()["error"]["code"] == "must_be_disarmed"
    term = client.post(f"{base}/terminate", headers=headers, json={})
    assert term.status_code == 400 and term.json()["error"]["detail"]["expected"] == "TERMINATE"
    done = client.post(f"{base}/terminate", headers=headers, json={"confirm": "TERMINATE"})
    assert done.status_code == 200 and wait_for(lambda: not client.svc.vehicles[vid].armed)
    caps = client.get("/api/v1/capabilities").json()
    assert caps["confirm"]["terminate"] == "TERMINATE" and "attitude" in {s["name"] for s in caps["skills"]}
    assert "velocity" in caps["plan"]["actions"]


# ---------------------------------------------------------------------------- fake real aircraft


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    port = free_udp_port()
    settings = Settings()
    settings.listen_port = port
    svc = FleetService(settings, None, operator_token="op", allow_loopback=True, history=History(None))
    svc.start()
    state = tmp_path_factory.mktemp("devfakes")
    ap = FakeVehicle(
        "ardupilot", ("127.0.0.4", 14555), ("127.0.0.1", port), None, state_file=state / "a.json"
    )
    px4 = FakeVehicle("px4", ("127.0.0.5", 14555), ("127.0.0.1", port), None, state_file=state / "p.json")
    ap.start()
    px4.start()
    ids = ("real-127-0-0-4", "real-127-0-0-5")
    assert wait_for(lambda: all(i in svc.vehicles and svc.vehicles[i].link_state == "ok" for i in ids), 10)
    for vid, source in zip(ids, ("keyboard", "screen"), strict=True):
        svc.possess(vid, source)
    yield svc, ap, px4, ids
    svc.shutdown()
    ap.stop()
    px4.stop()


@pytest.mark.parametrize("which", [0, 1])
def test_real_aircraft_need_confirm_words_and_a_disarmed_state(rig, which):
    svc, ap, px4, ids = rig
    vid = ids[which]
    fake = (ap, px4)[which]
    for call, word in (
        (lambda c: svc.motors_test(vid, OP, 1, 10, 1.0, c), "MOTOR"),
        (lambda c: svc.actuator_set(vid, OP, 1, 0.1, None, 0, c), "MOTOR"),
        (lambda c: svc.reboot(vid, OP, c), "REBOOT"),
        (lambda c: svc.calibrate(vid, OP, "gyro", c), "CALIBRATE"),
        (lambda c: svc.terminate(vid, OP, c), "TERMINATE"),
        (lambda c: svc.arm(vid, OP, True, c, force=True), "FORCE"),
        (lambda c: svc.raw_command(vid, OP, "PREFLIGHT_CALIBRATION", [1], confirm=c), "CALIBRATE"),
    ):
        with pytest.raises(RelayError) as exc:
            call(None)
        assert exc.value.code == "confirm_required" and exc.value.detail["expected"] == word
        with pytest.raises(RelayError) as exc:
            call("WRONG")
        assert exc.value.code == "confirm_required"
    with pytest.raises(RelayError) as exc:  # the real-aircraft throttle ceiling
        svc.motors_test(vid, OP, 1, 50, 1.0, "MOTOR")
    assert exc.value.code == "invalid_request"
    t0 = time.monotonic()
    assert svc.motors_test(vid, OP, 1, 10, 0.5, "MOTOR")["motor"] == 1
    assert wait_for(lambda: fake.received("COMMAND_LONG", since=t0), 2)
    cmds = {m.command for m in fake.received("COMMAND_LONG", since=t0)}
    assert (M.MAV_CMD_ACTUATOR_TEST if which else M.MAV_CMD_DO_MOTOR_TEST) in cmds
    # Force arm: param2 21196, then everything that needs a disarmed aircraft refuses.
    t1 = time.monotonic()
    assert svc.arm(vid, OP, True, "FORCE", force=True)["armed"] is True
    arm = [m for m in fake.received("COMMAND_LONG", since=t1) if m.command == M.MAV_CMD_COMPONENT_ARM_DISARM]
    assert arm and arm[0].param2 == 21196
    for call in (
        lambda: svc.motors_test(vid, OP, 1, 10, 1.0, "MOTOR"),
        lambda: svc.reboot(vid, OP, "REBOOT"),
        lambda: svc.calibrate(vid, OP, "gyro", "CALIBRATE"),
        lambda: svc.actuator_set(vid, OP, 2, 0.1, None, 0, "MOTOR"),
    ):
        with pytest.raises(RelayError) as exc:
            call()
        assert exc.value.code == "must_be_disarmed"
    t2 = time.monotonic()
    out = svc.terminate(vid, OP, "TERMINATE")
    assert out["terminated"] is True
    assert wait_for(lambda: not svc.vehicles[vid].armed, 4)
    cmds = [m.command for m in fake.received("COMMAND_LONG", since=t2)]
    assert M.MAV_CMD_DO_FLIGHTTERMINATION in cmds and M.MAV_CMD_COMPONENT_ARM_DISARM in cmds
    kinds = [e["kind"] for e in svc.events.after(0, 5000) if e["vehicle"] == vid]
    assert "terminated" in kinds and "motor_test" in kinds


@pytest.mark.parametrize("which", [0, 1])
def test_real_aircraft_reboot_recovers_its_streams(rig, which):
    svc, _ap, _px4, ids = rig
    vid = ids[which]
    v = svc.vehicles[vid]
    assert wait_for(lambda: not v.armed and v.link_state == "ok", 6)
    out = svc.reboot(vid, OP, "REBOOT")
    assert out["rebooting"] is True and out["ack"]["result_name"] == "ACCEPTED"
    assert wait_for(lambda: v.link_state == "ok" and "ATTITUDE" in v.state["messages"], 25)
