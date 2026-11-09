"""Stick timelines (.ars, 10 §2), sim airframe defects with hover assist on (10 §3), arming needs sticks."""

import math

import pytest
from conftest import open_agent

from relay import sequence as seqmod
from relay.errors import RelayError


def boot(svc, stepper, flavour="ardupilot", position=False):
    (vid,) = svc.add_sims(1, flavour, position)
    stepper.run(2.5)
    v = svc.vehicles[vid]
    assert v.link_state == "ok"
    return vid, v


def hover_doc(hold_s=5.0, trim=None):
    """Arm, climb, hover, descend, settle: the throttle track of a takeoff-hover-land in altitude hold."""
    climb_end = 3.0
    hover_end = climb_end + hold_s
    return {
        "format": "ars",
        "version": 1,
        "name": "takeoff hover land",
        "mode": "althold",
        "trim": trim or {},
        "tracks": {
            "throttle": [
                {"t": 0.0, "v": 0.5, "label": "spool"},
                {"t": 0.5, "v": 0.78, "label": "climb"},
                {"t": climb_end, "v": 0.5, "label": "hover"},
                {"t": hover_end, "v": 0.28, "label": "descend"},
                {"t": hover_end + 9.0, "v": 0.0, "label": "settle"},
            ]
        },
        "duration_s": hover_end + 11.0,
        "end": "disarm",
    }


def test_document_rules():
    doc = seqmod.normalise(hover_doc())
    assert doc["tracks"]["throttle"][1] == {"t": 0.5, "v": 0.78, "ease": "hold", "label": "climb"}
    assert doc["trim"] == {"roll": 0.0, "pitch": 0.0, "yaw": 0.0, "throttle": 0.0}
    # hold blocks step, ramp blocks move linearly to the next key, missing tracks are centred
    assert seqmod.sample(doc, 1.0) == (0.0, 0.0, 0.0, 0.78)
    ramp = seqmod.normalise(
        {
            "format": "ars",
            "version": 1,
            "mode": "stabilize",
            "tracks": {"pitch": [{"t": 0, "v": 0.0, "ease": "ramp"}, {"t": 2, "v": -0.2}]},
        }
    )
    assert ramp["duration_s"] == 2.0
    assert math.isclose(seqmod.sample(ramp, 1.0)[1], -0.1)
    assert seqmod.sample(ramp, 1.0)[3] == 0.0  # stabilize: no throttle track is zero throttle
    assert math.isclose(seqmod.sample(ramp, 1.0, {"pitch": -0.05})[1], -0.15)
    bad = [
        ({"format": "relayseq"}, "format"),
        ({**hover_doc(), "version": 2}, "version"),
        ({**hover_doc(), "mode": "ALT_HOLD"}, "mode"),
        (
            {**hover_doc(), "tracks": {"throttle": [{"t": 1, "v": 0.5}, {"t": 0.5, "v": 0.5}]}},
            "tracks.throttle[1].t",
        ),
        ({**hover_doc(), "tracks": {"roll": [{"t": 0, "v": 1.5}]}}, "tracks.roll[0].v"),
        ({**hover_doc(), "trim": {"pitch": 0.5}}, "trim.pitch"),
        ({**hover_doc(), "duration_s": 1.0}, "duration_s"),
    ]
    for raw, field in bad:
        with pytest.raises(seqmod.SequenceError) as info:
            seqmod.normalise(raw)
        assert info.value.field == field


def test_recording_simplifies_to_blocks():
    rows = [
        (i * 0.05, 0.0, -0.1 if 2.0 <= i * 0.05 < 4.0 else 0.0, 0.0, min(0.7, i * 0.01)) for i in range(121)
    ]
    doc = seqmod.from_rows(rows, mode="ALT_HOLD", autopilot="ArduPilot", name="rec")
    assert doc["mode"] == "althold" and doc["duration_s"] == 6.0
    assert len(doc["tracks"]["pitch"]) <= 8
    for t in (1.0, 3.0, 5.0):
        expect = rows[int(t / 0.05)]
        assert abs(seqmod.sample(doc, t)[1] - expect[2]) <= 0.02
        assert abs(seqmod.sample(doc, t)[3] - expect[4]) <= 0.02


@pytest.mark.parametrize("flavour", ["ardupilot", "px4"])
def test_sequence_takes_off_hovers_lands_and_disarms(svc, stepper, flavour):
    vid, v = boot(svc, stepper, flavour)
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    out = svc.run_sequence(vid, agent, hover_doc())
    assert out["skill"]["name"] == "sequence" and out["skill"]["duration_s"] == 19.0
    heights = []
    end = stepper.clock.t + 40
    while stepper.clock.t < end and v.skill.active:
        stepper.run(0.1, [session])
        view = v.skill.view()
        if 5.0 <= view["progress_s"] <= 7.5:
            heights.append(v.driver.sim.quad.s.p[2])
    assert v.skill.state == "done", v.skill.detail
    assert not v.armed
    assert heights and min(heights) >= 0.8 and max(heights) <= 3.5
    assert max(heights) - min(heights) < 0.4  # the hover block holds the height
    kinds = [e["kind"] for e in svc.events.after(0, 4000)]
    assert "sequence_started" in kinds


def _drift(svc, stepper, trim):
    vid, v = boot(svc, stepper)
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    svc.set_sim_airframe(vid, {"params": {"level_trim_deg": [2.0, 0.0], "with_assist": True}}, agent)
    svc.run_sequence(vid, agent, hover_doc(hold_s=6.0, trim=trim))
    start = None
    while v.skill.active and v.skill.view()["progress_s"] < 9.0:
        stepper.run(0.1, [session])
        if start is None and v.skill.view()["progress_s"] >= 3.0:
            start = list(v.driver.sim.quad.s.p)
    p = v.driver.sim.quad.s.p
    return math.hypot(p[0] - start[0], p[1] - start[1])


def test_level_error_drifts_with_hover_assist_on_and_trim_cancels_it(svc, stepper):
    drift = _drift(svc, stepper, None)
    assert drift > 2.0  # a 2 degree level error: the airframe slides off in altitude hold
    # full stick leans about 30 degrees: -2/30 of roll stick takes most of the error out
    trimmed = _drift(svc, stepper, {"roll": round(-2.0 / 30.0, 4)})
    assert trimmed < 0.3 * drift
    wrong = _drift(svc, stepper, {"roll": round(2.0 / 30.0, 4)})
    assert wrong > 1.5 * drift


def test_trim_and_tracks_change_in_flight(svc, stepper):
    vid, v = boot(svc, stepper)
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    svc.run_sequence(vid, agent, hover_doc(hold_s=8.0))
    stepper.until(lambda: v.skill.view()["progress_s"] >= 4.0, 15, [session])
    out = svc.edit_sequence(vid, agent, trim={"pitch": -0.05})
    assert out["trim"]["pitch"] == -0.05
    stepper.run(0.3, [session])
    assert math.isclose(v.skill.out.pitch, -0.05)
    later = hover_doc(hold_s=8.0)
    later["tracks"]["yaw"] = [{"t": 0.0, "v": 0.0}, {"t": 5.0, "v": 0.2}, {"t": 6.0, "v": 0.0}]
    svc.edit_sequence(vid, agent, document=later)
    stepper.until(lambda: v.skill.view()["progress_s"] >= 5.2, 5, [session])
    assert math.isclose(v.skill.out.yaw, 0.2) and math.isclose(v.skill.out.pitch, -0.05)
    with pytest.raises(RelayError) as info:
        svc.edit_sequence(vid, agent, document={**later, "mode": "stabilize"})
    assert info.value.code == "invalid_request"


def test_real_needs_confirm_and_assist_off_needs_stabilize(svc, stepper):
    vid, _v = boot(svc, stepper)
    _session, agent = open_agent(svc)
    svc.claim(vid, agent)
    svc.set_sim_assist(vid, False, agent)
    with pytest.raises(RelayError) as info:
        svc.run_sequence(vid, agent, hover_doc())
    assert "stabilize" in info.value.message_en
    with pytest.raises(RelayError) as info:
        svc.edit_sequence(vid, agent, trim={"pitch": 0.01})
    assert info.value.code == "no_sequence"


def test_record_then_fly_the_recording(svc, stepper):
    vid, v = boot(svc, stepper)
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    svc.record_sequence(vid, agent, "start", "flown by hand")
    svc.run_sequence(vid, agent, hover_doc(hold_s=2.0))
    stepper.until(lambda: not v.skill.active, 40, [session])
    out = svc.record_sequence(vid, agent, "stop")
    doc = out["sequence"]
    assert doc["format"] == "ars" and doc["name"] == "flown by hand" and doc["mode"] == "althold"
    assert doc["duration_s"] > 8.0
    assert 0.74 <= max(k["v"] for k in doc["tracks"]["throttle"]) <= 0.8


def test_arming_a_pilot_mode_needs_stick_input(svc, stepper):
    """Real autopilots refuse to arm STABILIZE / ALT_HOLD without pilot input, and so does the sim;
    Relay's arm sends centred zero-throttle sticks first when nothing feeds the aircraft (10 §1)."""
    vid, v = boot(svc, stepper, "px4")
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    svc.set_mode(vid, agent, "STABILIZED", wait=False)
    stepper.run(1.0, [session])
    v.driver.arm(True)  # the bare command, as a client that streams nothing would send it
    stepper.run(0.5, [session])
    assert not v.armed
    assert any("No manual control input" in s["text"] for s in v.state["statustext"])
    svc.arm(vid, agent, True, wait=False)
    assert v.sticks is not None and v.sticks.source == "relay" and v.sticks.throttle == 0.0
    stepper.run(0.5, [session])
    assert v.armed


def test_arm_bias_differs_between_flights():
    from relay.sim.airframe import Airframe, AirframeModel

    model = AirframeModel(Airframe(preset="custom", seed=7, arm_bias_deg=1.5))
    first = model.rearm()
    second = model.rearm()
    assert first != second and all(abs(x) <= 1.5 for x in (*first, *second))
    again = AirframeModel(Airframe(preset="custom", seed=7, arm_bias_deg=1.5))
    assert again.rearm() == first  # the same seed and arm count draw the same error


def test_stop_in_the_air_hands_over_to_a_hold(svc, stepper):
    vid, v = boot(svc, stepper)
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    svc.run_sequence(vid, agent, hover_doc(hold_s=8.0))
    stepper.until(lambda: v.skill.view()["progress_s"] >= 5.0, 15, [session])
    height = v.driver.sim.quad.s.p[2]
    out = svc.stop_sequence(vid, agent)
    assert out["after"] == "hold" and v.skill.name == "hold"
    stepper.run(3.0, [session])
    assert v.armed and abs(v.driver.sim.quad.s.p[2] - height) < 0.4
    with pytest.raises(RelayError) as info:
        svc.stop_sequence(vid, agent)
    assert info.value.code == "no_sequence"


def test_joining_and_stopping_in_the_air_never_drop_the_throttle(svc, stepper):
    """Review fixes (10 §2): a timeline started in the air begins on its own first frame, a stabilize one
    may not begin on a ground throttle, a stop holds the throttle of that moment, and arming an armed
    aircraft does not prime zero-throttle sticks."""
    vid, v = boot(svc, stepper)
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    svc.run_sequence(vid, agent, hover_doc(hold_s=20.0))
    stepper.until(lambda: v.skill.view()["progress_s"] >= 5.0, 15, [session])
    assert v.airborne()
    svc.arm(vid, agent, True, wait=False)
    assert v.sticks is None or v.sticks.source != "relay"
    cruise = {
        "format": "ars",
        "version": 1,
        "name": "stabilize cruise",
        "mode": "stabilize",
        "tracks": {"throttle": [{"t": 0.0, "v": 0.0}, {"t": 2.0, "v": 0.62}, {"t": 9.0, "v": 0.0}]},
        "duration_s": 10.0,
        "end": "hold",
    }
    with pytest.raises(RelayError) as info:
        svc.run_sequence(vid, agent, cruise)
    assert info.value.code == "invalid_request" and "in the air" in info.value.message_en
    svc.run_sequence(vid, agent, cruise, start_at=2.0)
    stepper.run(0.05, [session])
    assert v.skill.out.throttle == pytest.approx(0.62)  # mode phase: the first frame, not the arming idle
    stepper.until(lambda: v.skill.phase == "play" and v.skill.view()["progress_s"] >= 3.0, 10, [session])
    assert svc.stop_sequence(vid, agent)["after"] == "sticks"
    stepper.run(0.5, [session])
    assert v.skill.active and v.skill.out.throttle == pytest.approx(0.62)  # not the 0 the timeline ends on
