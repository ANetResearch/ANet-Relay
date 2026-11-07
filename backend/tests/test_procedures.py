"""Bench procedures (05 §4): analysis, catalogue, records, the state machine on the simulator, REST."""

from __future__ import annotations

import json
import math
import time

import pytest
from conftest import free_udp_port, open_agent
from fake_vehicle import FakeVehicle
from fastapi.testclient import TestClient

from relay.api.app import create_app
from relay.authority import Actor
from relay.config import Settings
from relay.errors import RelayError
from relay.fleet import FleetService
from relay.history import History
from relay.mav.mavlink import M
from relay.procedures import analysis, catalogue, layout, report, suggestions

OP = Actor.operator()


# ---------------------------------------------------------------------------- step-response analysis


def _signal(shape, t_on=1.0, t_off=1.4, amp=10.0, tail=1.5, dt=0.02):
    """Sampled response to a step from t_on to t_off; ``shape(tau)`` is the unit response after an edge."""
    t, y = [], []
    k = 0
    while k * dt <= t_off + tail:
        x = k * dt
        if x < t_on:
            v = 0.0
        elif x < t_off:
            v = amp * shape(x - t_on)
        else:
            v = amp * shape(t_off - t_on) - amp * shape(x - t_off)
        t.append(x)
        y.append(v)
        k += 1
    return t, y


def first_order(tc):
    return lambda s: 1.0 - math.exp(-s / tc)


def second_order(zeta, wn):
    wd = wn * math.sqrt(1 - zeta * zeta)

    def unit(s):
        return 1.0 - math.exp(-zeta * wn * s) * (
            math.cos(wd * s) + zeta / math.sqrt(1 - zeta * zeta) * math.sin(wd * s)
        )

    return unit


def test_first_order_response_is_clean():
    t, y = _signal(first_order(0.04))
    fig = analysis.step_response(t, y, 1.0, 1.4, expected=10.0)
    assert fig is not None and not fig["reversed"]
    assert 0.06 <= fig["rise_s"] <= 0.12  # 2.2 tau
    assert fig["overshoot_pct"] < 1.0
    assert fig["settle_s"] is not None and fig["settle_s"] < 0.15
    assert abs(fig["ss_error_pct"]) < 2.0
    level, findings, sugg = analysis.judge("roll", fig, "ArduPilot", {"ATC_RAT_RLL_P": 0.135})
    assert level == "ok" and findings == [] and sugg == []


@pytest.mark.parametrize(("zeta", "wn", "level"), [(0.3, 40.0, "warn"), (0.15, 80.0, "bad")])
def test_underdamped_response_overshoots_and_suggests_lower_p(zeta, wn, level):
    t, y = _signal(second_order(zeta, wn))
    fig = analysis.step_response(t, y, 1.0, 1.4, expected=10.0)
    expected = math.exp(-math.pi * zeta / math.sqrt(1 - zeta * zeta)) * 100.0
    assert abs(fig["overshoot_pct"] - expected) < 6.0
    got, findings, sugg = analysis.judge("roll", fig, "ArduPilot", {"ATC_RAT_RLL_P": 0.2})
    assert got == level
    assert any(f["code"] == "roll_overshoot" for f in findings)
    p = next(s for s in sugg if s["name"] == "ATC_RAT_RLL_P")
    assert p["direction"] == "decrease" and p["change_pct"] == -10 and p["suggested"] == pytest.approx(0.18)
    assert p["state"] == "proposed"


def test_sluggish_response_suggests_higher_p():
    t, y = _signal(first_order(0.25), t_off=2.0)
    fig = analysis.step_response(t, y, 1.0, 2.0, expected=10.0)
    assert fig["rise_s"] > analysis.SLUGGISH_S["pitch"]
    level, findings, sugg = analysis.judge("pitch", fig, "PX4", {"MC_PITCHRATE_P": 0.15})
    assert level == "warn" and findings[0]["code"] == "pitch_sluggish"
    assert sugg[0]["name"] == "MC_PITCHRATE_P" and sugg[0]["direction"] == "increase"


def test_a_ramp_that_never_settles_is_sluggish():
    t, y = _signal(lambda s: s, t_off=2.0)
    fig = analysis.step_response(t, y, 1.0, 2.0)
    assert fig["settle_s"] is None or fig["settle_s"] > 0.5
    level, findings, _ = analysis.judge("yaw", fig, "ArduPilot", {})
    assert level == "warn" and any(f["code"] == "yaw_sluggish" for f in findings)


def test_reversed_response_fails():
    t, y = _signal(first_order(0.04), amp=-8.0)
    fig = analysis.step_response(t, y, 1.0, 1.4, expected=10.0)
    assert fig["reversed"] is True
    level, findings, sugg = analysis.judge("roll", fig, "ArduPilot", {})
    assert level == "bad" and findings[0]["code"] == "roll_reversed" and sugg == []


def test_residual_oscillation_suggests_lower_p_and_d():
    base = first_order(0.04)

    def ringing(s):
        return base(s)

    t, y = _signal(ringing)
    y = [v + (1.5 * math.sin(2 * math.pi * 4.0 * x) if x > 1.4 else 0.0) for x, v in zip(t, y, strict=True)]
    fig = analysis.step_response(t, y, 1.0, 1.4, expected=10.0)
    assert fig["residual_pct"] > analysis.RESIDUAL_PCT and fig["crossings"] >= 2
    level, findings, sugg = analysis.judge(
        "roll", fig, "PX4", {"MC_ROLLRATE_P": 0.15, "MC_ROLLRATE_D": 0.003}
    )
    assert level == "warn" and any(f["code"] == "roll_residual" for f in findings)
    assert {s["name"] for s in sugg} == {"MC_ROLLRATE_P", "MC_ROLLRATE_D"}
    assert all(s["direction"] == "decrease" for s in sugg)


def test_too_few_samples_and_no_data():
    assert analysis.step_response([0.0, 1.0, 1.1], [0.0, 1.0, 1.0], 1.0, 1.4) is None
    level, findings, _ = analysis.judge("pitch", None, "PX4", {})
    assert level == "na" and findings[0]["level"] == "info"


# ---------------------------------------------------------------------------- catalogue and tables


def test_catalogue_lists_the_five_procedures():
    view = catalogue.catalogue_view()
    assert [p["id"] for p in view] == [
        "full_bench",
        "preflight_quick",
        "motor_check",
        "vibration_check",
        "hover_tune",
    ]
    full = view[0]
    assert [s["id"] for s in full["steps"]] == [f"S{i}" for i in range(1, 13)]
    assert all(s["title_en"] and s["title_zh"] for s in full["steps"])
    assert {s["kind"] for s in full["steps"]} == {"auto", "operator", "operator_device", "device", "flight"}
    assert all(p["steps"][-1]["id"] == "S12" and p["est_s"] > 0 for p in view)
    assert {"S5", "S6", "S8"} == catalogue.OPERATOR_ONLY


@pytest.mark.parametrize(
    ("mav_type", "name", "count"),
    [
        (None, "quad", 4),
        (M.MAV_TYPE_QUADROTOR, "quad", 4),
        (M.MAV_TYPE_HEXAROTOR, "hex", 6),
        (M.MAV_TYPE_OCTOROTOR, "octo", 8),
    ],
)
def test_motor_layout_from_the_frame(mav_type, name, count):
    lay = layout.layout(mav_type)
    assert lay["name"] == name and lay["count"] == count
    assert [m["n"] for m in lay["motors"]] == list(range(1, count + 1))
    dirs = [m["dir"] for m in lay["motors"]]
    assert dirs.count("cw") == dirs.count("ccw")  # torque balanced
    assert len({m["angle_deg"] for m in lay["motors"]}) == count


def test_parameter_ranges_and_hover_items():
    names = suggestions.baseline_names("ArduPilot")
    assert names[:2] == ["FRAME_CLASS", "FRAME_TYPE"] and len(names) == len(set(names))
    out = suggestions.check_ranges("ArduPilot", {"ANGLE_MAX": 6000.0, "FS_THR_ENABLE": 0.0})
    codes = {f["code"] for f in out}
    assert "param_range_ANGLE_MAX" in codes and "param_off_FS_THR_ENABLE" in codes
    assert "param_missing_ATC_RAT_RLL_P" in codes
    items = suggestions.hover_items("PX4", 38.0, {"MPC_THR_HOVER": 0.5}, 0.5)
    assert [i["name"] for i in items] == ["MPC_THR_HOVER", "calibration.hover"]
    assert items[0]["suggested"] == 0.38 and items[0]["change_pct"] == -24
    low = suggestions.hover_items("ArduPilot", 5.0, {}, 0.5)
    assert low[0]["state"] == "unavailable" and low[1]["suggested"] == suggestions.RELAY_HOVER[0]


def test_procedure_options_are_validated(svc):
    with pytest.raises(RelayError) as exc:
        svc.procedure_start("sim-1", OP, "no_such")
    assert exc.value.code == "procedure_unknown"
    for options in ({"hover_s": 2}, {"hover_s": 90}, {"hover_s": True}, {"skip": ["S12"]}, {"skip": ["S4"]}):
        proc = "motor_check" if options.get("skip") == ["S4"] else "full_bench"
        with pytest.raises(RelayError) as exc:
            svc.procedure_start("sim-1", OP, proc, options)
        assert exc.value.code == "invalid_request", options
    with pytest.raises(RelayError) as exc:
        svc.procedure_view("sim-9")
    assert exc.value.code == "vehicle_not_found"


# ---------------------------------------------------------------------------- records


def _record(**kw):
    base = {"vehicle_id": "sim-1", "host": "sim:1", "procedure": "motor_check", "started": 1.8e9}
    return {**base, **kw}


def test_history_keeps_test_runs():
    h = History(None)
    try:
        a = h.test_run_start(_record())
        b = h.test_run_start(_record(vehicle_id="real-1", host="10.0.0.2"))
        steps = [
            {"id": "S1", "verdict": "pass"},
            {"id": "S6", "verdict": "fail"},
            {"id": "S12", "verdict": "fail"},
        ]
        h.test_run_update(
            a,
            {
                "state": "failed",
                "verdict": "fail",
                "ended": 1.8e9 + 60,
                "steps": steps,
                "params": {"before": {"X": 1.0}},
                "meta": {"label": "Sim 1"},
            },
        )
        runs = h.test_runs(None, 10)
        assert [r["run_id"] for r in runs] == [b, a]
        assert h.test_runs("sim-1", 10)[0]["run_id"] == a
        assert h.test_runs("10.0.0.2", 10)[0]["run_id"] == b  # by address too
        summary = h.test_runs("sim-1", 10)[0]
        assert summary["counts"] == {"pass": 1, "warn": 0, "fail": 2, "skipped": 0} and summary["total"] == 3
        full = h.test_run(a)
        assert full["steps"][1]["id"] == "S6" and full["params"]["before"] == {"X": 1.0}
        assert full["label"] == "Sim 1" and full["state"] == "failed"
        assert h.test_run(9999) is None
    finally:
        h.close()


def test_runs_left_running_are_closed_at_startup(tmp_path):
    path = tmp_path / "history.db"
    h = History(path)
    run_id = h.test_run_start(_record())
    h.close()
    again = History(path)
    try:
        row = again.test_run(run_id)
        assert row["state"] == "aborted" and row["ended"] is not None
    finally:
        again.close()


def test_markdown_report_has_bilingual_tables():
    run = {
        "run_id": 7,
        "procedure": "hover_tune",
        "title_en": "Hover and stability",
        "title_zh": "悬停与稳定性",
        "vehicle": "sim-1",
        "label": "Sim | 1",
        "state": "warned",
        "verdict": "warn",
        "started": 1.8e9,
        "ended": 1.8e9 + 120,
        "simulated": True,
        "steps": [
            {
                "id": "S9",
                "title_en": "Hover test",
                "title_zh": "悬停测试",
                "state": "done",
                "verdict": "warn",
                "metrics": [
                    {
                        "key": "vibration",
                        "label_en": "Vibration",
                        "label_zh": "振动",
                        "value": 18.2,
                        "unit": "m/s2",
                        "level": "warn",
                        "mean": 12.0,
                        "peak": 18.2,
                        "warn": 15.0,
                        "bad": 30.0,
                    }
                ],
                "findings": [{"code": "x", "level": "warn", "text_en": "Balance", "text_zh": "平衡"}],
                "suggestions": [
                    {
                        "kind": "param",
                        "name": "MOT_THST_HOVER",
                        "current": 0.5,
                        "suggested": 0.42,
                        "direction": "set",
                        "change_pct": -16,
                        "reason_en": "r",
                        "reason_zh": "因",
                        "state": "kept",
                    }
                ],
            }
        ],
    }
    md = report.to_markdown(run)
    assert md.startswith("# Bench report / 标定与测试报告 #7")
    assert "Sim \\| 1" in md and "2027-01-15" in md and "UTC" in md
    assert "| S9 Hover test / 悬停测试 | passed with warnings / 有警告 | Vibration / 振动 |" in md
    assert "warn 15 / fail 30" in md and "MOT_THST_HOVER" in md and "kept / 保留" in md
    assert json.loads(report.to_json(run))["run_id"] == 7


# ---------------------------------------------------------------------------- state machine on the simulator


def boot(svc, stepper, flavour="ardupilot", position=True):
    (vid,) = svc.add_sims(1, flavour, position)
    stepper.run(2.5)
    v = svc.vehicles[vid]
    assert v.link_state == "ok"
    return vid, v


def answer_all(svc, vid, actor, view, step):
    """The answer a careful operator gives to every prompt."""
    p = step["prompt"]
    ids = [a["id"] for a in p["answers"]]
    if p["id"] in ("apply_param", "apply_relay"):
        return svc.procedure_answer(vid, actor, step["id"], "apply", confirm=p["confirm_word"])
    pick = "apply" if "apply" in ids else ids[0]
    return svc.procedure_answer(vid, actor, step["id"], pick, confirm=p.get("confirm_word"))


def drive(svc, stepper, vid, actor, budget=400.0, renew=None, answer=answer_all, until=None):
    """Run the clock, answering prompts, until the run ends (or ``until`` holds); returns the last view."""
    end = stepper.clock.t + budget
    answered = set()
    while stepper.clock.t < end:
        stepper.run(0.1, renew)
        view = svc.procedure_view(vid)
        if view["state"] not in ("running", "waiting") or (until and until(view)):
            return view
        step = next((s for s in view["steps"] if s["id"] == view["step"]), None)
        if step and step["state"] == "waiting" and step.get("prompt") and step["prompt"]["answers"]:
            key = (step["id"], step["prompt"]["id"], json.dumps(step["prompt"]["data"], sort_keys=True))
            if key in answered and step["prompt"]["id"] != "motor_check":
                continue
            answered.add(key)
            answer(svc, vid, actor, view, step)
    raise AssertionError(f"procedure still {view['state']} at {view['step']}")


def by_id(view, sid):
    return next(s for s in view["steps"] if s["id"] == sid)


@pytest.mark.parametrize("flavour", ["ardupilot", "px4"])
def test_full_bench_runs_end_to_end_on_the_simulator(svc, stepper, flavour):
    vid, v = boot(svc, stepper, flavour)
    svc.possess(vid, "keyboard")
    started = svc.procedure_start(vid, OP, "full_bench", {"auto_answer": True, "hover_s": 8})
    assert started["state"] in ("running", "waiting") and started["total"] == 12
    assert svc.vehicles[vid].procedure.brief()["procedure"] == "full_bench"
    view = drive(svc, stepper, vid, OP)
    assert view["state"] == "passed" and view["verdict"] == "pass", [
        (s["id"], s["verdict"], s["findings"]) for s in view["steps"] if s["verdict"] != "pass"
    ]
    assert [s["state"] for s in view["steps"]] == ["done"] * 12
    s6 = by_id(view, "S6")
    assert next(m for m in s6["metrics"] if m["key"] == "motors_ok")["value"] == "4 / 4"
    s7 = by_id(view, "S7")
    spun = next(m for m in s7["metrics"] if m["key"] == "motors_spun")
    assert spun["value"] == 4
    s9 = by_id(view, "S9")
    hover = next(m for m in s9["metrics"] if m["key"] == "hover_throttle")
    assert 40 <= hover["value"] <= 60 and hover["mean"] is not None and hover["peak"] is not None
    s10 = by_id(view, "S10")
    for axis in ("roll", "pitch", "yaw"):
        rise = next(m for m in s10["metrics"] if m["key"] == f"{axis}.rise_s")
        assert rise["value"] is not None and rise["value"] < 0.4
    s11 = by_id(view, "S11")
    assert [s["state"] for s in s11["suggestions"]] == ["applied", "applied"]
    hover_param = "MOT_THST_HOVER" if flavour == "ardupilot" else "MPC_THR_HOVER"
    assert v.state["params"][hover_param]["value"] == pytest.approx(
        s11["suggestions"][0]["suggested"], abs=1e-3
    )
    assert stepper.until(lambda: not v.armed, 10)
    kinds = [e["kind"] for e in svc.events.after(0, 5000) if e["vehicle"] == vid]
    assert kinds.count("procedure_started") == 1 and kinds.count("procedure_finished") == 1
    assert kinds.count("procedure_step") == 12 and "procedure_waiting" in kinds
    # the saved report
    runs = svc.test_runs(vid)["runs"]
    assert runs[0]["run_id"] == view["run_id"] and runs[0]["counts"]["pass"] == 12
    saved = svc.test_run(view["run_id"])
    assert saved["state"] == "passed" and "live" not in saved["steps"][8]
    assert saved["params"]["baseline"][hover_param] == pytest.approx(0.5)
    assert saved["params"]["changed"][0]["name"] == hover_param
    mime, md = svc.test_run_export(view["run_id"], "md")
    assert mime.startswith("text/markdown") and "| S10 Step response / 阶跃响应" in md


def test_an_agent_runs_a_procedure_and_answers_on_a_simulator(svc, stepper):
    vid, _v = boot(svc, stepper, "px4")
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    view = svc.procedure_start(vid, agent, "motor_check")
    assert view["by"] == {"kind": "session", "name": "agent"}
    view = drive(svc, stepper, vid, agent, budget=60, renew=[session])
    assert view["state"] == "passed"
    _other_session, other = open_agent(svc, "other")
    with pytest.raises(RelayError) as exc:
        svc.procedure_start(vid, other, "motor_check")
    assert exc.value.code in ("claimed_by_other", "not_controller", "claim_required")


def test_wrong_motor_answer_fails_the_step(svc, stepper):
    vid, _v = boot(svc, stepper)
    svc.possess(vid, "keyboard")
    svc.procedure_start(vid, OP, "motor_check")

    def wrong(svc_, vid_, actor, view, step):
        if step["prompt"]["id"] == "motor_check" and step["prompt"]["data"].get("motor") == 2:
            return svc_.procedure_answer(vid_, actor, step["id"], "wrong_direction")
        return answer_all(svc_, vid_, actor, view, step)

    view = drive(svc, stepper, vid, OP, budget=60, answer=wrong)
    assert view["state"] == "failed" and by_id(view, "S6")["verdict"] == "fail"
    assert any("Motor 2" in f["text_en"] for f in by_id(view, "S6")["findings"])


def test_answers_are_checked(svc, stepper):
    vid, _v = boot(svc, stepper)
    svc.possess(vid, "keyboard")
    svc.procedure_start(vid, OP, "motor_check")
    view = drive(svc, stepper, vid, OP, budget=30, answer=lambda *a: None, until=lambda w: w["step"] == "S6")
    stepper.run(0.2)
    for step, answer, code in (
        ("S1", "props_removed", "procedure_step_mismatch"),
        ("S6", "nonsense", "invalid_request"),
    ):
        with pytest.raises(RelayError) as exc:
            svc.procedure_answer(vid, OP, step, answer)
        assert exc.value.code == code
    with pytest.raises(RelayError) as exc:
        svc.procedure_start(vid, OP, "motor_check")
    assert exc.value.code == "procedure_running"
    view = svc.procedure_abort(vid, OP)
    assert view["state"] == "aborted" and view["reason"] == "operator"
    with pytest.raises(RelayError) as exc:
        svc.procedure_abort(vid, OP)
    assert exc.value.code == "no_procedure"


def test_emergency_stop_aborts_a_flight_step(svc, stepper):
    vid, v = boot(svc, stepper)
    svc.possess(vid, "keyboard")
    svc.procedure_start(vid, OP, "hover_tune", {"hover_s": 20})
    view = drive(svc, stepper, vid, OP, budget=60, until=lambda w: w["step"] == "S9" and v.airborne())
    stepper.run(1.0)
    svc.stop(vid, OP)
    stepper.run(0.2)
    view = svc.procedure_view(vid)
    assert view["state"] == "aborted" and view["reason"] == "stopped"
    assert by_id(view, "S9")["state"] == "aborted"
    assert v.skill is None or v.skill.name != "land"  # the stop is not followed by a procedure landing
    assert any(e["kind"] == "procedure_aborted" for e in svc.events.after(0, 5000))


def test_flight_steps_fail_clearly_with_hover_assist_off(svc, stepper):
    # WP-E seam (05 §4.3 x §5.1): a simulator without hover assist has no altitude or position source
    vid, v = boot(svc, stepper)
    svc.possess(vid, "keyboard")
    svc.set_sim_assist(vid, False, OP)
    svc.procedure_start(vid, OP, "hover_tune", {"auto_answer": True})
    view = drive(svc, stepper, vid, OP, budget=60)
    assert view["state"] == "failed"
    s9 = by_id(view, "S9")
    assert s9["verdict"] == "fail" and s9["findings"][0]["code"] == "hover_assist_off"
    assert "悬停辅助已关闭" in s9["findings"][0]["text_zh"]
    assert by_id(view, "S10")["verdict"] == "skipped"
    assert not v.armed and v.plan is None


def test_hover_assist_switched_off_mid_flight_lands_and_fails(svc, stepper):
    vid, v = boot(svc, stepper)
    svc.possess(vid, "keyboard")
    svc.procedure_start(vid, OP, "preflight_quick", {"auto_answer": True, "hover_s": 20})
    drive(svc, stepper, vid, OP, budget=90, until=lambda w: w["step"] == "S9" and v.airborne())
    stepper.run(2.0)
    svc.set_sim_assist(vid, False, OP)
    view = drive(svc, stepper, vid, OP, budget=60)
    s9 = by_id(view, "S9")
    assert view["state"] == "failed" and s9["verdict"] == "fail"
    assert any("Hover assist was switched off" in f["text_en"] for f in s9["findings"])
    assert stepper.until(lambda: not v.armed and v.landed == "ground", 30)


def test_arming_outside_the_procedure_aborts(svc, stepper):
    vid, _v = boot(svc, stepper)
    svc.possess(vid, "keyboard")
    svc.procedure_start(vid, OP, "motor_check")
    drive(svc, stepper, vid, OP, budget=30, answer=lambda *a: None, until=lambda w: w["step"] == "S6")
    svc.arm(vid, OP, True, force=True)
    stepper.run(0.5)
    view = svc.procedure_view(vid)
    assert view["state"] == "aborted" and view["reason"] == "armed"


def test_a_session_run_pauses_while_a_person_holds_the_aircraft(svc, stepper):
    vid, _v = boot(svc, stepper)
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    svc.procedure_start(vid, agent, "motor_check")
    drive(
        svc,
        stepper,
        vid,
        agent,
        budget=30,
        renew=[session],
        answer=lambda *a: None,
        until=lambda w: w["step"] == "S6",
    )
    svc.possess(vid, "keyboard")
    stepper.run(0.3, [session])
    view = svc.procedure_view(vid)
    assert view["paused"] is True and by_id(view, "S6")["prompt"]["id"] == "paused"
    with pytest.raises(RelayError) as exc:
        svc.procedure_answer(vid, agent, "S6", "confirm")
    assert exc.value.code == "procedure_not_waiting"
    svc.release(vid)
    stepper.run(0.3, [session])
    view = svc.procedure_view(vid)
    assert view["paused"] is False and by_id(view, "S6")["prompt"]["id"] == "props_removed"
    view = drive(svc, stepper, vid, agent, budget=60, renew=[session])
    assert view["state"] == "passed"


def test_rest_surface(tmp_path):
    svc = FleetService(listen=False, history=History(None), operator_token="op-token")
    op = {"X-Relay-Operator": "op-token"}
    with TestClient(create_app(svc), base_url="http://127.0.0.1") as c:
        cat = c.get("/api/v1/procedures").json()
        assert len(cat["procedures"]) == 5
        vid = c.post("/api/v1/sims", headers=op, json={"autopilot": "ardupilot"}).json()["added"][0]
        end = time.monotonic() + 8
        while svc.vehicles[vid].link_state != "ok" and time.monotonic() < end:
            time.sleep(0.05)
        base = f"/api/v1/vehicles/{vid}/procedure"
        assert c.get(base).status_code == 404
        assert c.post(base, json={"procedure": "motor_check"}).status_code == 401
        assert (
            c.post(f"/api/v1/vehicles/{vid}/possess", headers=op, json={"input": "keyboard"}).status_code
            == 200
        )
        bad = c.post(base, headers=op, json={"procedure": "nope"})
        assert bad.status_code == 400
        started = c.post(
            base, headers=op, json={"procedure": "motor_check", "options": {"auto_answer": True}}
        )
        assert started.status_code == 200, started.text
        assert started.json()["procedure"] == "motor_check"
        assert c.get(f"/api/v1/vehicles/{vid}").json()["procedure"]["procedure"] == "motor_check"
        end = time.monotonic() + 40
        view = started.json()
        while time.monotonic() < end:
            view = c.get(base).json()
            if view["state"] not in ("running", "waiting"):
                break
            step = next(s for s in view["steps"] if s["id"] == view["step"])
            prompt = step.get("prompt")
            if step["state"] == "waiting" and prompt and prompt["id"] == "props_removed":
                reply = c.post(f"{base}/answer", headers=op, json={"step": step["id"], "answer": "confirm"})
                assert reply.status_code in (200, 409), reply.text
            time.sleep(0.1)
        assert view["state"] == "passed", view
        runs = c.get("/api/v1/test-runs", params={"vehicle": vid}).json()["runs"]
        assert runs[0]["run_id"] == view["run_id"] and runs[0]["counts"]["pass"] == 3
        full = c.get(f"/api/v1/test-runs/{view['run_id']}").json()
        assert full["steps"][1]["id"] == "S6"
        md = c.get(f"/api/v1/test-runs/{view['run_id']}/export", params={"fmt": "md"})
        assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
        js = c.get(f"/api/v1/test-runs/{view['run_id']}/export")
        assert js.json()["run_id"] == view["run_id"]
        assert "attachment" in js.headers.get("content-disposition", "")
        assert c.get("/api/v1/test-runs/9999").status_code == 404
        assert c.post(f"{base}/abort", headers=op).json()["error"]["code"] == "no_procedure"
        assert c.get(base).json()["run_id"] == view["run_id"]


# ---------------------------------------------------------------------------- real aircraft


def wait_for(fn, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    port = free_udp_port()
    settings = Settings()
    settings.listen_port = port
    svc = FleetService(settings, None, operator_token="op", allow_loopback=True, history=History(None))
    svc.start()
    state = tmp_path_factory.mktemp("benchfakes")
    fake = FakeVehicle(
        "ardupilot", ("127.0.0.6", 14555), ("127.0.0.1", port), None, state_file=state / "a.json"
    )
    fake.start()
    vid = "real-127-0-0-6"
    assert wait_for(lambda: vid in svc.vehicles and svc.vehicles[vid].link_state == "ok", 10)
    svc.possess(vid, "keyboard")
    yield svc, fake, vid
    svc.shutdown()
    fake.stop()


def test_real_aircraft_keep_the_confirm_words_and_operator_prompts(rig):
    svc, fake, vid = rig
    with pytest.raises(RelayError) as exc:
        svc.procedure_start(vid, OP, "motor_check", {"auto_answer": True})
    assert exc.value.code == "invalid_request"
    svc.procedure_start(vid, OP, "motor_check")

    def waiting(prompt_id):
        def check():
            view = svc.procedure_view(vid)
            step = next((s for s in view["steps"] if s["id"] == view["step"]), None)
            return step is not None and (step.get("prompt") or {}).get("id") == prompt_id

        return check

    assert wait_for(waiting("props_removed"), 20)
    prompt = by_id(svc.procedure_view(vid), "S6")["prompt"]
    assert prompt["confirm_word"] == "MOTOR" and prompt["operator_only"] is True
    agent = Actor.session("s-x", "agent")
    with pytest.raises(RelayError) as exc:
        svc.procedure_answer(vid, agent, "S6", "confirm", confirm="MOTOR")
    assert exc.value.code == "operator_required"
    for word in (None, "WRONG"):
        with pytest.raises(RelayError) as exc:
            svc.procedure_answer(vid, OP, "S6", "confirm", confirm=word)
        assert exc.value.code == "confirm_required" and exc.value.detail["expected"] == "MOTOR"
    t0 = time.monotonic()
    svc.procedure_answer(vid, OP, "S6", "confirm", confirm="MOTOR")
    assert wait_for(lambda: fake.received("COMMAND_LONG", since=t0), 3)
    tests = [m for m in fake.received("COMMAND_LONG", since=t0) if m.command == M.MAV_CMD_DO_MOTOR_TEST]
    assert tests and all(m.param3 <= 30 and m.param4 <= 5 for m in tests)
    assert wait_for(waiting("motor_check"), 5)
    view = svc.procedure_abort(vid, OP)
    assert view["state"] == "aborted"
    assert svc.vehicles[vid].motor_until <= time.monotonic() + 0.01 or svc.vehicles[vid].motor_seq is None
