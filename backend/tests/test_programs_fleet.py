"""Onboard programs on the fleet (06 §2.2, §2.6): deployment, flight, safety rules and the program host."""

from __future__ import annotations

import math
import sys
import textwrap
from pathlib import Path

import pytest
from conftest import open_agent

from relay.api import schemas
from relay.authority import Actor
from relay.errors import RelayError
from relay.programs import service as servicemod
from relay.programs.hostclient import HostClient

pytestmark = pytest.mark.sticks_level  # programs fly at the default agent flight level

OP = Actor.operator()
CONST = "def step(obs, ctx):\n    return Sticks(throttle=0.5)\n"
RAISES = "def step(obs, ctx):\n    if obs.armed:\n        1 / 0\n    return Sticks(throttle=0.4)\n"


def boot(svc, stepper, count=1, assist=False):
    ids = svc.add_sims(count, hover_assist=assist)
    stepper.run(2.5)
    session, agent = open_agent(svc)
    for vid in ids:
        svc.claim(vid, agent)
    return ids, session, agent


def arm(svc, stepper, vid, agent, session):
    svc.arm(vid, agent, True)
    stepper.run(0.3, renew=[session])
    assert svc.vehicles[vid].armed


def custom(svc, source, name="t"):
    return svc.programs.store.create({"name": name, "source": source}, "operator")["id"]


def z(svc, vid):
    return svc.vehicles[vid].driver.sim.quad.s.p[2]


def events(svc, prefix="program"):
    return [e for e in svc.events.after(0) if e["kind"].startswith(prefix)]


def test_hover_trim_hovers_with_hover_assist_off(svc, stepper):
    (vid,), session, agent = boot(svc, stepper)
    v = svc.vehicles[vid]
    assert not v.driver.sim.hover_assist and not v.has_baro()
    arm(svc, stepper, vid, agent, session)
    out = svc.programs.deploy(vid, agent, {"program": "hover_trim", "seed": 7})
    assert out["program"]["status"] == "running" and out["program"]["name"] == "Hover trim"
    stepper.run(8.0, renew=[session])
    heights = []
    for _ in range(100):  # the next 10 s
        stepper.run(0.1, renew=[session])
        heights.append(z(svc, vid))
    assert not v.state["crashed"] and v.mode == "STABILIZE"
    assert min(heights) > 0.75 and max(heights) < 1.25, (min(heights), max(heights))
    assert abs(sum(heights) / len(heights) - 1.0) < 0.08
    view = svc._vehicle_view(v, svc.clock(), svc.wall())
    program = view["program"]
    assert (
        program["status"] == "running"
        and program["faults"] == 0
        and program["fps"] == pytest.approx(50, abs=2)
    )
    assert program["state"] == "hold" and program["rates_ok"] is True
    assert view["controller"] == "program" and view["sticks"]["source"] == "program"
    assert v.segment[:2] == ("program", "Hover trim")  # the timeline shows PROGRAM · Hover trim
    schemas.Vehicle.model_validate(view)


def test_a_constant_throttle_does_not_hold_height(svc, stepper):
    (vid,), session, agent = boot(svc, stepper)
    arm(svc, stepper, vid, agent, session)
    svc.programs.deploy_source(vid, agent, {"id": "c", "name": "const"}, 1, CONST, {})
    stepper.run(10.0, renew=[session])
    assert abs(z(svc, vid) - 1.0) > 0.25 or svc.vehicles[vid].state["crashed"]


def test_possession_pauses_and_release_does_not_resume(svc, stepper):
    (vid,), session, agent = boot(svc, stepper)
    arm(svc, stepper, vid, agent, session)
    svc.programs.deploy(vid, agent, {"program": "hover_trim"})
    stepper.run(3.0, renew=[session])
    svc.possess(vid, "keyboard")
    v = svc.vehicles[vid]
    run = svc.programs.runs[vid]
    assert run.status == "paused" and run.reason == "possessed" and v.controller(svc.sessions) == "human"
    assert v.sticks is None  # possession cleared the program's sticks; nothing writes them now
    stepper.run(0.2, renew=[session])
    assert v.sticks is None or v.sticks.source != "program"
    with pytest.raises(RelayError) as exc:
        svc.programs.resume(vid, agent)
    assert exc.value.code in ("claim_suspended", "possessed_by_human")
    svc.release(vid)
    stepper.run(0.2, renew=[session])
    assert run.status == "paused" and v.controller(svc.sessions) == "agent"
    assert svc.programs.resume(vid, agent)["program"]["status"] == "running"
    stepper.run(0.2, renew=[session])
    assert v.sticks.source == "program" and v.controller(svc.sessions) == "program"
    kinds = [e["kind"] for e in events(svc)]
    assert kinds == ["program_started", "program_paused", "program_resumed"]


def test_emergency_stop_link_loss_and_crash_stop_the_program(svc, stepper):
    (a, b), session, agent = boot(svc, stepper, count=2)
    for vid in (a, b):
        arm(svc, stepper, vid, agent, session)
        svc.programs.deploy(vid, agent, {"program": "hover_trim"})
    stepper.run(1.0, renew=[session])
    svc.stop(a, OP)
    assert svc.programs.runs[a].status == "stopped" and svc.programs.runs[a].reason == "emergency_stop"
    svc._set_link(svc.vehicles[b], "lost", svc.clock())
    assert svc.programs.runs[b].reason == "link_lost"
    stopped = [e for e in events(svc) if e["kind"] == "program_stopped"]
    assert [e["data"]["reason"] for e in stopped] == ["emergency_stop", "link_lost"]
    assert "急停" in stopped[0]["text_zh"]


def test_faulted_after_25_failed_frames_with_safe_sticks(svc, stepper):
    (vid,), session, agent = boot(svc, stepper)
    arm(svc, stepper, vid, agent, session)
    svc.programs.deploy_source(vid, agent, {"id": "r", "name": "raises"}, 1, RAISES, {})
    v = svc.vehicles[vid]
    stepper.run(0.1, renew=[session])
    run = svc.programs.runs[vid]
    assert run.status == "running" and run.faults >= 1 and "ZeroDivisionError" in run.last_error
    assert v.sticks.source == "program" and (v.sticks.roll, v.sticks.pitch, v.sticks.yaw) == (0, 0, 0)
    stepper.run(0.6, renew=[session])
    assert run.status == "faulted" and run.faults == servicemod.FAULT_LIMIT
    assert v.sticks is None and v.controller(svc.sessions) == "agent"
    (fault,) = [e for e in events(svc) if e["kind"] == "program_faulted"]
    assert "safety layer" in fault["text_en"] and "安全层" in fault["text_zh"]
    assert any(line["level"] == "error" for line in run.logs)


def test_sessions_skills_and_claim_end_supersede(svc, stepper):
    (vid,), session, agent = boot(svc, stepper)
    arm(svc, stepper, vid, agent, session)
    svc.programs.deploy(vid, agent, {"program": "hover_trim"})
    stepper.run(0.5, renew=[session])
    svc.sticks(vid, agent, {"roll": 0, "pitch": 0, "yaw": 0, "throttle": 0.5})
    assert svc.programs.runs[vid].reason == "superseded"
    svc.programs.deploy(vid, agent, {"program": "hover_trim"})
    svc.run_skill(vid, agent, "land", {})
    assert svc.programs.runs[vid].reason == "superseded"
    svc.programs.deploy(vid, agent, {"program": "hover_trim"})
    svc.unclaim(vid, agent)
    assert svc.programs.runs[vid].reason == "claim_ended"


def test_operator_deploys_without_a_claim_and_sessions_need_control(svc, stepper):
    (vid,), _session, agent = boot(svc, stepper)
    _, other = open_agent(svc, "other")
    with pytest.raises(RelayError) as exc:
        svc.programs.deploy(vid, other, {"program": "hover_trim"})
    assert exc.value.code == "claimed_by_other"
    svc.unclaim(vid, agent)
    out = svc.programs.deploy(vid, OP, {"program": "patrol", "mode": "pilot"})
    assert out["program"]["owner"] == {"kind": "operator", "name": "operator"}
    with pytest.raises(RelayError) as exc:
        svc.programs.stop(vid, other)
    assert exc.value.code == "not_claimed"
    assert svc.programs.stop(vid, OP)["program"]["status"] == "stopped"


def test_live_params_follow_the_defaults(svc, stepper):
    (vid,), session, agent = boot(svc, stepper)
    arm(svc, stepper, vid, agent, session)
    svc.programs.deploy(vid, agent, {"program": "hover_trim", "params": {"target_alt": 0.8}})
    stepper.run(8.0, renew=[session])
    assert abs(z(svc, vid) - 0.8) < 0.15
    assert svc.programs.set_params(vid, agent, {"target_alt": 1.5})["params"]["target_alt"] == 1.5
    stepper.run(8.0, renew=[session])
    assert abs(z(svc, vid) - 1.5) < 0.2
    for bad, code in (
        ({"target_alt": 9.0}, "program_param_invalid"),  # PARAM_RANGES [0.3, 3]
        ({"target_alt": "high"}, "program_param_invalid"),
        ({"nope": 1}, "program_param_invalid"),
        ({}, "invalid_request"),
    ):
        with pytest.raises(RelayError) as exc:
            svc.programs.set_params(vid, agent, bad)
        assert exc.value.code == code
    lines = svc.programs.log(vid)["lines"]
    assert any("target_alt = 1.5" in line["text"] for line in lines)


def test_deploy_validation(svc, stepper):
    (vid,), _session, agent = boot(svc, stepper)
    bad = custom(svc, "import os\n" + CONST, "bad")
    for body, code in (
        ({"program": bad}, "program_invalid"),
        ({"program": "nope"}, "program_not_found"),
        ({"program": "hover_trim", "rev": 9}, "revision_not_found"),
        ({"program": "hover_trim", "params": {"zzz": 1}}, "program_param_invalid"),
        ({"program": "hover_trim", "mode": "auto"}, "invalid_request"),
        ({"program": "hover_trim", "sensors": {"position": "lidar"}}, "invalid_request"),
        ({"program": "hover_trim", "arena": {"radius": 5, "floor": 2, "ceiling": 1}}, "invalid_request"),
    ):
        with pytest.raises(RelayError) as exc:
            svc.programs.deploy(vid, agent, body)
        assert exc.value.code == code, body
    boom = custom(svc, "def init(ctx):\n    1 / 0\n" + CONST, "boom")
    with pytest.raises(RelayError) as exc:
        svc.programs.deploy(vid, agent, {"program": boom})
    assert exc.value.code == "program_load_failed" and "ZeroDivisionError" in exc.value.message_en


def test_log_ring_and_print(svc, stepper):
    (vid,), session, agent = boot(svc, stepper)
    source = "def step(obs, ctx):\n    for i in range(6):\n        print('line', i)\n    return Sticks()\n"
    svc.programs.deploy_source(vid, agent, {"id": "p", "name": "printer"}, 1, source, {})
    stepper.run(4.0, renew=[session])
    log = svc.programs.log(vid)
    assert len(log["lines"]) == servicemod.LOG_RING and log["last_seq"] > servicemod.LOG_RING
    texts = [line["text"] for line in log["lines"]]
    assert "line 3" in texts and "line 4" not in texts  # at most 4 lines per frame
    assert any("dropped" in t for t in texts)
    newer = svc.programs.log(vid, after=log["last_seq"] - 3)["lines"]
    assert [line["seq"] for line in newer] == [log["last_seq"] - 2, log["last_seq"] - 1, log["last_seq"]]


def test_message_rates_follow_the_program(svc, stepper):
    (vid,), session, agent = boot(svc, stepper)
    sim = svc.vehicles[vid].driver.sim
    before = sim.intervals["ATTITUDE"]
    svc.programs.deploy(vid, agent, {"program": "hover_trim"})
    stepper.run(0.2, renew=[session])
    assert sim.intervals["ATTITUDE"] == pytest.approx(0.02) and sim.intervals["SCALED_IMU"] == pytest.approx(
        0.02
    )
    svc.programs.stop(vid, agent)
    stepper.run(0.2, renew=[session])
    assert sim.intervals["ATTITUDE"] == pytest.approx(before) and "SCALED_IMU" not in sim.intervals


def test_observation_fields(svc, stepper):
    (a, b), session, agent = boot(svc, stepper, count=2)
    svc.programs.deploy(a, agent, {"program": "hover_trim", "sensors": {"position": "uwb"}, "seed": 3})
    stepper.run(1.0, renew=[session])
    v = svc.vehicles[a]
    run = svc.programs.runs[a]
    obs = run.builder.build(svc, v, svc.clock())
    assert obs["armed"] is False and obs["airborne"] is False and obs["mode"] == "STABILIZE"
    assert abs(obs["alt"]) < 0.05 and abs(obs["az"] - 9.81) < 0.1 and abs(obs["ax"]) < 0.05
    assert obs["pos"] is not None and abs(obs["pos"][0]) < 0.3 and obs["vel"] is not None
    assert 3.5 < obs["battery"]["v_cell"] < 4.3 and obs["battery"]["pct"] > 90
    assert obs["hp"] is None and obs["arena"] is None and obs["sticks_in"] is None
    (contact,) = obs["contacts"]
    assert contact["id"] == b and contact["team"] == b and abs(contact["dist"] - 2.0) < 0.3
    assert abs(contact["bearing"] - 90.0) < 10 and abs(contact["rel"][1] - 2.0) < 0.3
    svc.programs.teams[b] = "blue"
    assert run.builder.build(svc, v, svc.clock(), teams=svc.programs.teams)["contacts"][0]["team"] == "blue"
    # default sensors with hover assist off: barometer yes, position no; no contacts sensor: None
    svc.programs.deploy(b, agent, {"program": "hover_trim", "sensors": {"contacts": False}})
    stepper.run(0.2, renew=[session])
    other = svc.programs.runs[b].builder.build(svc, svc.vehicles[b], svc.clock())
    assert other["pos"] is None and other["alt"] is not None and other["contacts"] is None
    no_baro = svc.programs.runs[b].builder
    no_baro.sensors.baro = False
    assert no_baro.build(svc, svc.vehicles[b], svc.clock())["alt"] is None


def test_same_seed_same_flight(svc, stepper, clock):
    results = []
    for _ in range(2):
        ids = svc.add_sims(1, hover_assist=False)
        stepper.run(2.5)
        vid = ids[0]
        svc.possess(vid, "keyboard")
        svc.arm(vid, Actor.operator("keyboard"), True)
        svc.release(vid)
        svc.programs.deploy(vid, OP, {"program": "hover_trim", "seed": 11})
        stepper.run(5.0)
        results.append(round(z(svc, vid), 6))
        svc.remove_sims([vid])
        assert vid not in svc.programs.runs
    assert results[0] == results[1]


def test_copilot_mode_transforms_the_persons_sticks(svc, stepper):
    (vid,), _session, agent = boot(svc, stepper)
    source = (
        "def step(obs, ctx):\n"
        "    s = obs.sticks_in\n"
        "    return Sticks(roll=s.roll * 0.5, throttle=s.throttle)\n"
    )
    svc.programs.deploy_source(vid, agent, {"id": "half", "name": "half"}, 1, source, {}, mode="copilot")
    svc.possess(vid, "keyboard")
    run = svc.programs.runs[vid]
    assert run.status == "running"  # copilot mode is not paused by possession
    pilot = Actor.operator("keyboard")
    svc.arm(vid, pilot, True)
    stepper.run(0.3)
    v = svc.vehicles[vid]
    for _ in range(25):
        svc.sticks(vid, pilot, {"roll": 0.8, "pitch": 0, "yaw": 0, "throttle": 0.3})
        stepper.run(0.02)
    assert v.sticks.source == "program" and v.sticks.roll == pytest.approx(0.4) and v.sticks.throttle == 0.3
    assert v.controller(svc.sessions) == "human"
    stepper.run(1.0)  # the person stops sending: the program has nothing to transform
    assert v.sticks is None


def test_real_aircraft_gate_and_fire_ignored(svc, stepper, monkeypatch):
    (vid,), session, agent = boot(svc, stepper)
    v = svc.vehicles[vid]
    v.kind = "real"
    with pytest.raises(RelayError) as exc:
        svc.programs.deploy(vid, agent, {"program": "hover_trim"})
    assert exc.value.code == "programs_not_allowed"
    with pytest.raises(RelayError) as exc:
        svc.programs.allow(vid, True, None)
    assert exc.value.code == "confirm_required" and exc.value.detail["expected"] == "PROGRAM"
    assert svc.programs.allow(vid, True, "PROGRAM") == {"id": vid, "programs_allowed": True}
    with pytest.raises(RelayError) as exc:
        svc.programs.deploy(vid, agent, {"program": "hover_trim"})
    assert exc.value.code == "preflight_blocked"
    monkeypatch.setattr(servicemod.preflight, "summary", lambda items: {"ok": True, "blocking": []})
    fire = "def step(obs, ctx):\n    return Sticks(throttle=0.5, fire=True)\n"
    fired = []
    monkeypatch.setattr(svc.arena, "fire", lambda *a, **k: fired.append(a))
    svc.programs.deploy_source(vid, agent, {"id": "f", "name": "fire"}, 1, fire, {})
    stepper.run(0.5, renew=[session])
    assert svc.programs.runs[vid].frames > 10 and fired == []
    svc._set_link(v, "lost", svc.clock())  # each connection starts with programs disallowed
    assert not v.programs_allowed and svc.programs.runs[vid].reason == "link_lost"
    assert [e["data"]["allowed"] for e in events(svc, "program_gate")] == [True]


def test_sim_fire_reaches_the_arena(svc, stepper):
    svc.update_settings({"peer_enabled": True})
    (vid,), session, agent = boot(svc, stepper)
    arm(svc, stepper, vid, agent, session)
    source = (
        "def step(obs, ctx):\n    return Sticks(throttle=0.7 if obs.alt < 1 else 0.45, fire=obs.alt > 0.6)\n"
    )
    svc.programs.deploy_source(vid, agent, {"id": "g", "name": "gunner"}, 1, source, {})
    stepper.run(4.0, renew=[session])
    assert svc.arena.guns[vid].ammo < 24


def hang_command(tmp_path: Path) -> list[str]:
    script = tmp_path / "hang.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import sys, time
            sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
            from relay.programs import host as p
            out = sys.stdout.buffer
            out.write(p.encode(p.HELLO, {{"pid": 0, "version": 1, "interrupt": "none", "limits": {{}}}}))
            out.flush()
            while True:
                frame = p.read_frame(sys.stdin.buffer)
                if frame is None:
                    break
                kind, msg = frame
                if kind == p.STEP:
                    time.sleep(30)
                out.write(p.encode(p.REPLY, {{"id": msg.get("id"), "ok": True, "state": "", "logs": []}}))
                out.flush()
            """
        )
    )
    return [sys.executable, str(script)]


def test_hung_host_is_restarted_and_the_others_reloaded(svc, stepper, tmp_path):
    hosts = [HostClient(command=hang_command(tmp_path))]
    svc.programs.host_factory = lambda: hosts.pop(0) if hosts else HostClient()
    (a, b), session, agent = boot(svc, stepper, count=2)
    svc.programs.deploy(a, agent, {"program": "hover_trim"})
    svc.programs.deploy(b, agent, {"program": "patrol"})
    stepper.run(0.2, renew=[session])
    ra, rb = svc.programs.runs[a], svc.programs.runs[b]
    assert ra.status == "faulted" and ra.reason == "host"  # it was running when the host hung
    assert rb.status == "running"
    stepper.run(1.0, renew=[session])
    assert rb.frames > 30 and any("reloaded" in line["text"] for line in rb.logs)
    (restart,) = [e for e in svc.events.after(0) if e["kind"] == "program_host_restarted"]
    assert restart["data"]["reason"] == "timeouts" and restart["data"]["culprit"] == ra.key
    assert "15 ms" in restart["text_en"] and "15 ms" in restart["text_zh"]


def test_crashed_host_is_restarted(svc, stepper):
    (vid,), session, agent = boot(svc, stepper)
    svc.programs.deploy(vid, agent, {"program": "hover_trim"})
    stepper.run(0.2, renew=[session])
    old = svc.programs.host
    old.proc.kill()
    old.proc.wait()
    stepper.run(0.5, renew=[session])
    run = svc.programs.runs[vid]
    assert svc.programs.host is not old and svc.programs.host.alive
    assert run.status == "running" and run.fps(svc.clock()) > 10
    assert [e["data"]["reason"] for e in svc.events.after(0) if e["kind"] == "program_host_restarted"] == [
        "exited"
    ]


def test_reset_removes_and_shutdown_stop(svc, stepper):
    (vid,), _session, agent = boot(svc, stepper)
    svc.programs.deploy(vid, agent, {"program": "hover_trim"})
    svc.reset_sim(vid)
    assert svc.programs.runs[vid].reason == "reset"
    svc.programs.deploy(vid, agent, {"program": "hover_trim"})
    svc.remove_sims([vid])
    assert vid not in svc.programs.runs


def test_step_time_is_bounded(svc, stepper):
    (vid,), session, agent = boot(svc, stepper)
    source = "def step(obs, ctx):\n    while True:\n        pass\n"
    svc.programs.deploy_source(vid, agent, {"id": "l", "name": "loop"}, 1, source, {})
    stepper.run(0.1, renew=[session])
    run = svc.programs.runs[vid]
    assert run.status == "running" and "limit" in run.last_error and run.overruns >= 1
    assert svc.programs.host.alive and math.isfinite(run.faults)
