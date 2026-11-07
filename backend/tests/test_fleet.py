"""Fleet service rules on simulated aircraft, stepped with a fake clock (03 §4.3, §5, §6)."""

import pytest
from conftest import open_agent

from relay.authority import Actor
from relay.config import LEASE_S
from relay.errors import RelayError
from relay.mav.mavlink import M

OP = Actor.operator()


def boot(svc, stepper, n=1, flavour="ardupilot"):
    ids = svc.add_sims(n, flavour)
    stepper.run(2.5)
    for vid in ids:
        v = svc.vehicles[vid]
        v.driver.sim.keep_received = 20000
        assert v.link_state == "ok" and v.capabilities.position
    return ids


def frames(v, since, mtype="RC_CHANNELS_OVERRIDE"):
    return [m for t, m in v.driver.sim.received if t >= since and m.get_type() == mtype]


def fly_agent(svc, stepper, vid, actor, session, alt=1.0):
    svc.claim(vid, actor)
    svc.run_skill(vid, actor, "takeoff", {"alt": alt})
    v = svc.vehicles[vid]
    assert stepper.until(lambda: v.skill.state == "done", 15, [session])
    return v


def test_agent_takeoff_hold_land_cycle(svc, stepper):
    (vid,) = boot(svc, stepper)
    session, agent = open_agent(svc)
    v = fly_agent(svc, stepper, vid, agent, session, alt=1.2)
    assert v.mode == "GUIDED" and abs(v.altitude() - 1.2) < 0.15
    svc.run_skill(vid, agent, "hold", {})
    assert stepper.until(lambda: v.skill.state == "done" and v.mode == "LOITER", 5, [session])
    svc.run_skill(vid, agent, "land", {})
    assert stepper.until(lambda: not v.armed, 15, [session])
    kinds = [e["kind"] for e in svc.events.after(0, 1000)]
    assert kinds.count("skill_finished") == 3 and "disarmed" in kinds


def test_only_one_stick_source_and_possession_wins(svc, stepper):
    (vid,) = boot(svc, stepper)
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    svc.sticks(vid, agent, {"roll": 0.2, "pitch": 0, "yaw": 0, "throttle": 0})
    stepper.run(0.2, [session])
    v = svc.vehicles[vid]
    t0 = stepper.clock.t
    svc.possess(vid, "keyboard")
    with pytest.raises(RelayError) as exc:
        svc.sticks(vid, agent, {"roll": 0.9, "pitch": 0, "yaw": 0, "throttle": 0})
    assert exc.value.code == "claim_suspended"
    svc.sticks(vid, OP, {"roll": -0.4, "pitch": 0, "yaw": 0, "throttle": 0})
    stepper.run(0.3, [session])
    after = frames(v, t0 + 0.1)
    assert after and all(m.chan1_raw in (1300, 0) for m in after)  # only the operator's -0.4 (or release)
    assert v.controller(svc.sessions) == "human"
    assert svc.fleet()["vehicles"][0]["claim"]["suspended"] is True


def test_handback_sends_nothing_on_agents_behalf(svc, stepper):
    (vid,) = boot(svc, stepper)
    session, agent = open_agent(svc)
    v = fly_agent(svc, stepper, vid, agent, session)
    svc.possess(vid, "gamepad", 1)
    assert stepper.until(lambda: v.mode == "ALT_HOLD", 2.0, [session])  # possess mode (03 §5.4)
    for _ in range(20):
        svc.sticks(vid, OP, {"roll": 0, "pitch": 0, "yaw": 0, "throttle": 0.5})
        stepper.run(0.02, [session])
    t_release = stepper.clock.t
    result = svc.release(vid)
    assert result["event"] == "relay_handback" and result["controller"] == "agent"
    stepper.run(0.6, [session])
    sent = [m for t, m in v.driver.sim.received if t >= t_release]
    kinds = {m.get_type() for m in sent}
    assert kinds <= {"RC_CHANNELS_OVERRIDE", "HEARTBEAT", "TIMESYNC"}
    overrides = [m for m in sent if m.get_type() == "RC_CHANNELS_OVERRIDE"]
    assert len(overrides) == 4 and all(m.chan3_raw == 0 for m in overrides)  # release only
    assert v.mode == "ALT_HOLD"  # mode is not changed back for the agent
    events = [e["kind"] for e in svc.events.after(0, 2000)]
    assert "relay_handback" in events


def test_stale_sticks_stop_after_08s(svc, stepper):
    (vid,) = boot(svc, stepper)
    svc.possess(vid, "keyboard")
    svc.sticks(vid, OP, {"roll": 0.3, "pitch": 0, "yaw": 0, "throttle": 0})
    t0 = stepper.clock.t
    v = svc.vehicles[vid]
    stepper.run(1.5)
    active = [
        t
        for t, m in v.driver.sim.received
        if t >= t0 and m.get_type() == "RC_CHANNELS_OVERRIDE" and m.chan1_raw == 1650
    ]
    assert active and max(active) - t0 <= 0.8 + 0.03
    assert v.sticks is None


def test_session_expiry_stops_sticks_within_lease_plus_200ms(svc, stepper):
    (vid,) = boot(svc, stepper)
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    v = svc.vehicles[vid]
    t_last_renew = stepper.clock.t
    svc.sessions.renew(session)
    end = t_last_renew + LEASE_S + 1.0
    while stepper.clock.t < end:
        try:
            svc.sticks(vid, agent, {"roll": 0.1, "pitch": 0, "yaw": 0, "throttle": 0})
        except RelayError:
            pass
        stepper.run(0.1)
    active = [
        t
        for t, m in v.driver.sim.received
        if t >= t_last_renew and m.get_type() == "RC_CHANNELS_OVERRIDE" and m.chan1_raw == 1550
    ]
    assert max(active) <= t_last_renew + LEASE_S + 0.2
    assert v.auth.claim is None
    kinds = [e["kind"] for e in svc.events.after(0, 5000)]
    assert "agent_session_expired" in kinds and "claim_released" in kinds


def test_stop_keeps_authority_and_cuts_throttle(svc, stepper):
    (vid,) = boot(svc, stepper)
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    svc.possess(vid, "keyboard")
    stepper.run(0.2, [session])  # possession's own release frames go out first
    v = svc.vehicles[vid]
    before = (v.auth.claim, v.auth.possession)
    t0 = stepper.clock.t
    svc.stop(vid, OP)
    stepper.run(0.3, [session])
    assert (v.auth.claim, v.auth.possession) == before
    cut = frames(v, t0)
    assert cut[0].chan3_raw == 1000 and cut[0].chan1_raw == 1500
    disarm = [m for m in frames(v, t0, "COMMAND_LONG") if m.command == M.MAV_CMD_COMPONENT_ARM_DISARM]
    assert disarm and disarm[0].param1 == 0 and disarm[0].param2 == 0  # never forced
    assert all(m.chan3_raw == 0 for m in cut[-4:])  # then released


def test_stop_all_and_agent_stop_rules(svc, stepper):
    ids = boot(svc, stepper, 2)
    _session, agent = open_agent(svc)
    with pytest.raises(RelayError):
        svc.stop(ids[0], agent)  # not the controller
    svc.claim(ids[0], agent)
    svc.stop(ids[0], agent)
    result = svc.stop_all()
    assert set(result["stopped"]) == set(ids)
    kinds = [e["kind"] for e in svc.events.after(0, 5000)]
    assert kinds.count("stop_all") == 1 and kinds.count("stop") == 3


def test_skill_capability_and_safety_volume(svc, stepper):
    (vid,) = boot(svc, stepper)
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    with pytest.raises(RelayError) as exc:
        svc.run_skill(vid, agent, "goto", {"x": 0.5, "y": 0, "z": 1})
    assert exc.value.code == "not_armed"
    with pytest.raises(RelayError) as exc:
        svc.run_skill(vid, agent, "takeoff", {"alt": 5.0})
    assert exc.value.code == "outside_safety_volume"
    fly_agent(svc, stepper, vid, agent, session)
    with pytest.raises(RelayError) as exc:
        svc.run_skill(vid, agent, "goto", {"x": 2.0, "y": 0, "z": 1})
    assert exc.value.code == "outside_safety_volume"
    with pytest.raises(RelayError) as exc:
        svc.run_skill(vid, agent, "dance", {})
    assert exc.value.code == "unknown_skill"
    v = svc.vehicles[vid]
    v.state["local"] = None  # position estimate gone
    with pytest.raises(RelayError) as exc:
        svc.run_skill(vid, agent, "goto", {"x": 0.5, "y": 0, "z": 1})
    assert exc.value.code == "capability_missing"


def test_goto_reaches_target_in_world_frame(svc, stepper):
    ids = boot(svc, stepper, 2)
    session, agent = open_agent(svc)
    v = fly_agent(svc, stepper, ids[1], agent, session)  # sim-2 spawns at (2, 0)
    svc.run_skill(ids[1], agent, "goto", {"x": 2.8, "y": 0.6, "z": 1.3})
    assert stepper.until(lambda: v.skill.state == "done", 10, [session])
    x, y, z = v.world_enu()
    assert abs(x - 2.8) < 0.15 and abs(y - 0.6) < 0.15 and abs(z - 1.3) < 0.15
    view = next(x for x in svc.fleet()["vehicles"] if x["id"] == ids[1])
    assert view["position"]["frame"] == "sim" and abs(view["position"]["x"] - 2.8) < 0.15


def test_possess_mode_failure_raises_alert(svc, stepper):
    (vid,) = boot(svc, stepper)
    session, agent = open_agent(svc)
    v = fly_agent(svc, stepper, vid, agent, session)
    v.driver.set_mode = lambda name: None  # the autopilot never switches
    svc.possess(vid, "keyboard")
    assert v.auth.possession is not None
    stepper.run(2.0, [session])
    assert any(a["code"] == "mode_switch_failed" for a in svc.alerts.view())
    assert v.auth.possession is not None  # possession is kept


def test_airborne_without_controller_alert(svc, stepper):
    (vid,) = boot(svc, stepper)
    svc.possess(vid, "keyboard")
    svc.set_mode(vid, OP, "LOITER")
    stepper.run(1.2)
    svc.arm(vid, OP, True)
    stepper.run(0.5)
    v = svc.vehicles[vid]
    for _ in range(100):
        svc.sticks(vid, OP, {"roll": 0, "pitch": 0, "yaw": 0, "throttle": 0.85})
        stepper.run(0.02)
    assert v.airborne()
    svc.release(vid)
    stepper.run(0.5)
    assert any(a["code"] == "airborne_no_controller" for a in svc.alerts.view())


def test_arm_rules_for_sims_and_calibration(svc, stepper):
    (vid,) = boot(svc, stepper)
    _session, agent = open_agent(svc)
    with pytest.raises(RelayError) as exc:
        svc.arm(vid, agent, True)
    assert exc.value.code == "not_claimed"
    svc.set_calibration(vid, {"roll": -1}, 0.45)
    v = svc.vehicles[vid]
    assert v.calibration.signs.roll == -1 and v.calibration.hover == 0.45
    with pytest.raises(RelayError):
        svc.set_calibration(vid, {"roll": 2}, None)
    svc.possess(vid, "keyboard")
    svc.sticks(vid, OP, {"roll": 0.5, "pitch": 0, "yaw": 0, "throttle": 0})
    t0 = stepper.clock.t
    stepper.run(0.1)
    assert frames(v, t0)[-1].chan1_raw == 1250  # sign applied after the API frame


def test_ws_close_stops_its_sticks(svc, stepper):
    (vid,) = boot(svc, stepper)
    svc.possess(vid, "screen")
    svc.sticks(vid, OP, {"roll": 0.2, "pitch": 0, "yaw": 0, "throttle": 0}, conn="ws-1")
    stepper.run(0.1)
    svc.ws_closed("ws-2")
    assert svc.vehicles[vid].sticks is not None
    svc.ws_closed("ws-1")
    assert svc.vehicles[vid].sticks is None


def test_course_with_goto_sequence_finishes(svc, stepper):
    (vid,) = boot(svc, stepper)
    session, agent = open_agent(svc)
    v = fly_agent(svc, stepper, vid, agent, session)
    svc.course_start(vid, agent, "cage-loop")
    assert v.course.state == "running"  # already airborne
    for ring in v.course.rings:
        cx, cy, cz = ring["c"]
        import math

        nx, ny = math.sin(math.radians(ring["yaw_deg"])), math.cos(math.radians(ring["yaw_deg"]))
        for d in (-0.35, 0.35):
            svc.run_skill(vid, agent, "goto", {"x": cx + nx * d, "y": cy + ny * d, "z": cz})
            assert stepper.until(lambda: v.skill.state == "done", 10, [session])
    assert v.course.state == "finished" and len(v.course.splits) == 4
    kinds = [e["kind"] for e in svc.events.after(0, 5000)]
    assert kinds.count("ring_passed") == 4 and "course_finished" in kinds
    assert svc.history.best_course("cage-loop") is not None


def test_course_aborts_on_disarm_and_needs_position(svc, stepper):
    (vid,) = boot(svc, stepper)
    session, agent = open_agent(svc)
    v = fly_agent(svc, stepper, vid, agent, session)
    svc.course_start(vid, OP, "slalom")
    svc.stop(vid, agent)
    stepper.run(0.5, [session])
    assert v.course.state == "aborted" and v.course.reason == "stopped"
    v.state["local"] = None
    with pytest.raises(RelayError) as exc:
        svc.course_start(vid, agent, "cage-loop")
    assert exc.value.code == "no_position_estimate"


def test_sim_limit_reset_and_removal(svc, stepper):
    boot(svc, stepper, 8)
    with pytest.raises(RelayError) as exc:
        svc.add_sims(1)
    assert exc.value.code == "sim_limit"
    assert sorted(svc.remove_sims(["sim-3"])) == ["sim-3"]
    assert svc.add_sims(1) == ["sim-3"]
    assert svc.reset_sim("sim-1")["reset"]
    snap_ids = [v["id"] for v in svc.fleet()["vehicles"]]
    stepper.run(0.2)
    assert len(svc.fleet()["vehicles"]) == 8 and snap_ids


def test_sim_driver_has_no_socket(svc, stepper):
    import socket as socketmod

    (vid,) = boot(svc, stepper)
    driver = svc.vehicles[vid].driver
    assert not any(isinstance(value, socketmod.socket) for value in vars(driver).values())
    assert not hasattr(driver, "link")


def test_snapshot_matches_schema(svc, stepper):
    from relay.api.schemas import Fleet

    boot(svc, stepper, 2, "px4")
    session, agent = open_agent(svc)
    svc.claim("sim-1", agent)
    svc.possess("sim-1", "gamepad", 2)
    stepper.run(0.5, [session])
    Fleet.model_validate(svc.fleet())
    assert svc.fleet()["vehicles"][0]["autopilot"] == "PX4"


@pytest.mark.parametrize(
    ("flavour", "manual", "hold"), [("px4", "STABILIZED", "ALTCTL"), ("ardupilot", "STABILIZE", "ALT_HOLD")]
)
def test_possess_on_ground_leaves_manual_throttle(svc, stepper, flavour, manual, hold):
    """A keyboard script leaves STABILIZE behind; possession on the ground moves to altitude hold."""
    (vid,) = boot(svc, stepper, flavour=flavour)
    v = svc.vehicles[vid]
    v.driver.set_mode(manual)
    assert stepper.until(lambda: v.mode == manual, 2.0)
    svc.possess(vid, "keyboard")
    assert stepper.until(lambda: v.mode == hold, 2.0)
    svc.release(vid)


def test_possess_in_the_air_keeps_the_stick_mode(svc, stepper):
    (vid,) = boot(svc, stepper, flavour="ardupilot")
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    svc.run_skill(vid, agent, "takeoff", {"alt": 1.0})
    v = svc.vehicles[vid]
    assert stepper.until(lambda: v.skill.state == "done", 20, [session])
    v.driver.set_mode("STABILIZE")
    assert stepper.until(lambda: v.mode == "STABILIZE", 2.0, [session])
    svc.possess(vid, "keyboard")
    stepper.run(1.0, [session])
    assert v.mode == "STABILIZE"  # no mode change under the operator's hands in flight
