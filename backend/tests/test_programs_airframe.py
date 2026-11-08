"""Programs on simulators with airframe defects (06 §2.2, §3.3, §4): the defects reach ``obs`` only through
physics, and a deployment may fit the simulator's airframe for the run."""

from __future__ import annotations

import json
import math

import pytest
from conftest import open_agent

from relay.errors import RelayError
from relay.programs import host as hostmod
from relay.programs import service as servicemod
from relay.programs.obs import G

pytestmark = pytest.mark.sticks_level

TRIM = {"preset": "ideal", "params": {"level_trim_deg": [0.0, 4.0]}}
LEAKS = ("airframe", "preset", "trim", "payload", "cg_offset", "motor_gain", "prop_damage", "imu_noise")


def boot(svc, stepper, count=1):
    ids = svc.add_sims(count, hover_assist=False, airframe={"preset": "ideal"})
    stepper.run(2.5)
    session, agent = open_agent(svc)
    for vid in ids:
        svc.claim(vid, agent)
    return ids, session, agent


def obs_of(svc, vid):
    return svc.programs.runs[vid].builder.build(svc, svc.vehicles[vid], svc.clock())


def test_level_error_shows_only_as_the_board_measures_it(svc, stepper):
    (vid,), session, agent = boot(svc, stepper)
    sim = svc.vehicles[vid].driver.sim
    svc.programs.deploy(vid, agent, {"program": "hover_trim", "seed": 3, "airframe": TRIM})
    assert sim.airframe.ref()["preset"] == "custom" and sim.airframe.params()["level_trim_deg"] == [0.0, 4.0]
    kinds = [e["kind"] for e in svc.events.after(0)]
    assert kinds.index("sim_airframe_changed") < kinds.index("program_started")
    stepper.run(1.0, renew=[session])
    obs = obs_of(svc, vid)
    # the frame sits level on the ground; the board, mounted 4 degrees off, reads nose-down
    assert abs(sim.quad.s.pitch) < 1e-6
    assert obs["pitch"] == pytest.approx(-4.0, abs=0.05)
    assert obs["ax"] == pytest.approx(-G * math.sin(math.radians(4.0)), abs=0.02)
    # the observation has exactly the 06 §2.2 fields and none of the airframe values
    assert set(obs) == set(hostmod.TEST_DEFAULTS)
    text = json.dumps(obs)
    assert not any(word in text for word in LEAKS)


def test_payload_shows_as_a_higher_hover_throttle(svc, stepper):
    (light, heavy), session, agent = boot(svc, stepper, count=2)
    for vid, airframe in (
        (light, {"preset": "ideal"}),
        (heavy, {"preset": "ideal", "params": {"payload_kg": 0.04}}),
    ):
        svc.arm(vid, agent, True)
        svc.programs.deploy(vid, agent, {"program": "hover_trim", "seed": 5, "airframe": airframe})
    stepper.run(12.0, renew=[session])
    throttles = {light: [], heavy: []}
    for _ in range(50):
        stepper.run(0.1, renew=[session])
        for vid in throttles:
            throttles[vid].append(obs_of(svc, vid)["throttle_out"])
    for vid in throttles:
        assert abs(svc.vehicles[vid].driver.sim.quad.s.p[2] - 1.0) < 0.3
    mean = {vid: sum(values) / len(values) for vid, values in throttles.items()}
    assert mean[heavy] > mean[light] + 0.02, mean


def test_deploy_airframe_is_checked_before_anything_changes(svc, stepper, monkeypatch):
    (vid,), _session, agent = boot(svc, stepper)
    sim = svc.vehicles[vid].driver.sim
    svc.programs.deploy(vid, agent, {"program": "hover_trim", "seed": 1})
    before = sim.airframe.ref()
    for airframe, code in (({"preset": "nope"}, "invalid_request"), ("rough", "invalid_request")):
        with pytest.raises(RelayError) as exc:
            svc.programs.deploy(vid, agent, {"program": "hover_trim", "airframe": airframe})
        assert exc.value.code == code
    assert sim.airframe.ref() == before and svc.programs.runs[vid].status == "running"
    svc.programs.deploy(vid, agent, {"program": "hover_trim", "airframe": {"preset": "rough", "seed": 9}})
    assert sim.airframe.ref() == {"preset": "rough", "seed": 9}
    svc.vehicles[vid].kind = "real"
    svc.programs.allow(vid, True, "PROGRAM")
    monkeypatch.setattr(servicemod.preflight, "summary", lambda items: {"ok": True, "blocking": []})
    with pytest.raises(RelayError) as exc:
        svc.programs.deploy(vid, agent, {"program": "hover_trim", "airframe": {"preset": "ideal"}})
    assert exc.value.code == "sim_only"
