"""Plans and the stick skills for aircraft without a position estimate (03 §5.7, §5.8)."""

import math

import pytest
from conftest import open_agent

from relay import plans
from relay.authority import Actor
from relay.errors import RelayError

OP = Actor.operator()

DEMO = [
    {"action": "takeoff", "alt": 1.0},
    {"action": "hover", "seconds": 2},
    {"action": "move", "forward": 1},
    {"action": "move", "forward": -1},
    {"action": "yaw", "deg": 45},
    {"action": "land"},
]


def boot(svc, stepper, flavour="ardupilot", position=True):
    (vid,) = svc.add_sims(1, flavour, position)
    stepper.run(2.5)
    v = svc.vehicles[vid]
    assert v.link_state == "ok" and v.capabilities.althold
    assert v.capabilities.position is position
    return vid, v


def truth(v):
    s = v.driver.sim.quad.s
    return list(s.p), s.yaw


def heading_change(a, b):
    return math.degrees((b - a + math.pi) % (2 * math.pi) - math.pi)


def step_positions(svc, stepper, v, session, budget=90.0):
    """Run the plan, recording the true position each time a step finishes."""
    marks = {}
    seen = 0
    end = stepper.clock.t + budget
    while stepper.clock.t < end and v.plan.running:
        stepper.run(0.05, [session] if session else None)
        if len(v.plan.results) > seen:
            seen = len(v.plan.results)
            marks[seen] = truth(v)
    return marks


@pytest.mark.parametrize(
    ("flavour", "position"), [("px4", False), ("ardupilot", False), ("ardupilot", True), ("px4", True)]
)
def test_demo_plan_flies_end_to_end(svc, stepper, flavour, position):
    vid, v = boot(svc, stepper, flavour, position)
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    start, _ = truth(v)
    svc.run_plan(vid, agent, DEMO, name="demo")
    marks = step_positions(svc, stepper, v, session)
    assert v.plan.state == "done", (v.plan.detail, v.plan.index)
    assert stepper.until(lambda: not v.armed, 5, [session])  # the autopilot disarms after touchdown
    # 1 takeoff, 2 hover, 3 forward, 4 back, 5 yaw, 6 land
    assert 0.85 <= marks[2][0][2] - start[2] <= 1.25
    forward = math.hypot(marks[3][0][0] - marks[2][0][0], marks[3][0][1] - marks[2][0][1])
    back = math.hypot(marks[4][0][0] - marks[2][0][0], marks[4][0][1] - marks[2][0][1])
    if position:
        assert abs(forward - 1.0) < 0.2 and back < 0.25
    else:  # open loop: an estimate, but in the right direction and roughly the right size
        assert 0.7 <= forward <= 1.4 and back <= 0.4
    assert abs(heading_change(marks[4][1], marks[5][1]) - 45.0) <= 5.0
    kinds = [e["kind"] for e in svc.events.after(0, 2000)]
    assert kinds.count("plan_step") == 6 and "plan_finished" in kinds


def test_no_position_takeoff_uses_altitude_hold(svc, stepper):
    vid, v = boot(svc, stepper, "px4", False)
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    svc.run_skill(vid, agent, "takeoff", {"alt": 1.2})
    assert stepper.until(lambda: v.skill.state == "done", 20, [session])
    assert v.mode == "ALTCTL" and abs(v.altitude() - 1.2) < 0.15
    stepper.run(3.0, [session])
    assert abs(v.altitude() - 1.2) < 0.2  # holds on the neutral throttle


def test_operator_stick_override_fails_the_plan(svc, stepper):
    vid, v = boot(svc, stepper, "ardupilot", False)
    svc.possess(vid, "keyboard")
    svc.run_plan(vid, OP, DEMO[:3])
    assert stepper.until(lambda: v.plan.index >= 1, 20)
    svc.sticks(vid, OP, {"roll": 0, "pitch": 0, "yaw": 0, "throttle": 0.5})  # baseline
    svc.sticks(vid, OP, {"roll": 0.6, "pitch": 0, "yaw": 0, "throttle": 0.5})
    stepper.run(0.2)
    assert v.plan.state == "failed" and v.sticks is not None


def test_possession_stops_an_agent_plan(svc, stepper):
    vid, v = boot(svc, stepper, "ardupilot", True)
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    svc.run_plan(vid, agent, DEMO)
    assert stepper.until(lambda: v.plan.index >= 1, 20, [session])
    svc.possess(vid, "keyboard")
    stepper.run(0.2, [session])
    assert v.plan.state == "failed"


def test_cancel_holds_in_place(svc, stepper):
    vid, v = boot(svc, stepper, "px4", False)
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    svc.run_plan(vid, agent, [{"action": "takeoff", "alt": 1.0}, {"action": "hover", "seconds": 30}])
    assert stepper.until(lambda: v.plan.index >= 1, 20, [session])
    svc.cancel_plan(vid, agent)
    assert v.plan.state == "cancelled" and v.skill.name == "hold"
    alt = v.altitude()
    stepper.run(2.0, [session])
    assert v.armed and abs(v.altitude() - alt) < 0.2


def test_plan_validation():
    with pytest.raises(RelayError):
        plans.normalise_steps([])
    with pytest.raises(RelayError):
        plans.normalise_steps([{"action": "flip"}])
    with pytest.raises(RelayError):
        plans.normalise_steps([{"action": "move"}])
    with pytest.raises(RelayError):
        plans.normalise_steps([{"action": "yaw", "deg": 720}])
    with pytest.raises(RelayError):
        plans.normalise_steps([{"action": "takeoff", "speed": 3}])
    steps = plans.normalise_steps([{"action": "takeoff"}, {"action": "hover"}])
    assert steps == [{"action": "takeoff", "alt": 1.0}, {"action": "hover", "seconds": 3.0}]


def test_plan_needs_airborne_unless_it_takes_off(svc, stepper):
    vid, _ = boot(svc, stepper)
    _session, agent = open_agent(svc)
    svc.claim(vid, agent)
    with pytest.raises(RelayError) as exc:
        svc.run_plan(vid, agent, [{"action": "move", "forward": 1}])
    assert exc.value.code == "not_airborne"


def test_open_loop_move_limit(svc, stepper):
    vid, v = boot(svc, stepper, "px4", False)
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    svc.run_skill(vid, agent, "takeoff", {"alt": 1.0})
    assert stepper.until(lambda: v.skill.state == "done", 20, [session])
    with pytest.raises(RelayError) as exc:
        svc.run_skill(vid, agent, "move", {"forward": 3})
    assert exc.value.code == "outside_safety_volume"


def test_px4_takes_off_again_after_landing(svc, stepper):
    """A land leaves PX4 in LAND, where it refuses to arm; the next take-off switches mode first."""
    vid, v = boot(svc, stepper, "px4", True)
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    for _ in range(2):
        svc.run_plan(vid, agent, [{"action": "takeoff", "alt": 1.0}, {"action": "land"}], name="hop")
        assert stepper.until(lambda: v.plan.state != "running", 60, [session])
        assert v.plan.state == "done", v.plan.detail
        assert stepper.until(lambda: not v.armed, 5, [session])
        assert v.mode == "LAND"


def test_tuning_validation():
    assert plans.normalise_tuning(None) == {}
    assert plans.normalise_tuning({"climb": 0.8}) == {"climb": 0.8}
    assert plans.normalise_tuning({"climb": 0.8, "hover": 0.55}) == {"climb": 0.8, "hover": 0.55}
    bad_inputs = ({"climb": 0.5}, {"hover": 0.7}, {"climb": True}, {"gain": 1}, {"climb": 0.6, "hover": 0.58})
    for bad in (*bad_inputs, []):
        with pytest.raises(RelayError):
            plans.normalise_tuning(bad)


def test_tuning_reaches_the_flying_step(svc, stepper):
    """The climb and hold throttle of a plan change while it flies (the stick variants, no position)."""
    vid, v = boot(svc, stepper, "ardupilot", False)
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    with pytest.raises(RelayError) as exc:
        svc.tune_plan(vid, agent, {"climb": 0.8})
    assert exc.value.code == "no_plan"
    steps = [{"action": "takeoff", "alt": 2.0}, {"action": "hover", "seconds": 30}]
    view = svc.run_plan(vid, agent, steps, tuning={"climb": 0.9})["plan"]
    assert view["tuning"] == {"climb": 0.9}
    assert stepper.until(lambda: v.armed and v.skill.phase == "climb", 10, [session])
    stepper.run(0.1, [session])
    assert v.skill.out.throttle == pytest.approx(0.9)
    svc.tune_plan(vid, agent, {"climb": 0.8})
    stepper.run(0.1, [session])
    assert v.skill.out.throttle == pytest.approx(0.8)
    assert stepper.until(lambda: v.plan.index >= 1, 20, [session])
    svc.tune_plan(vid, agent, {"hover": 0.54})
    stepper.run(0.5, [session])
    assert v.skill.name == "hold" and v.skill.tuning == {"climb": 0.8, "hover": 0.54}
    assert v.plan.view()["tuning"] == {"climb": 0.8, "hover": 0.54}
    with pytest.raises(RelayError):
        svc.tune_plan(vid, agent, {"hover": 0.78})  # outside the range: nothing changes
    with pytest.raises(RelayError):
        svc.tune_plan(vid, agent, {"climb": 0.56})  # not above the hover throttle
    assert v.plan.tuning == {"climb": 0.8, "hover": 0.54}
    assert "plan_tuned" in [e["kind"] for e in svc.events.after(0, 2000)]
