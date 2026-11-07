"""Preflight checks (03 §6.3)."""

from relay import preflight
from relay.preflight import Facts


def base(**kw) -> Facts:
    f = Facts(
        kind="real",
        link_state="ok",
        autopilot="ArduPilot",
        heartbeats=5,
        voltage=12.3,
        cells=3,
        prearm=True,
        params={"SYSID_MYGCS": 255, "RC_OVERRIDE_TIME": 3, "FS_THR_ENABLE": 1},
        params_complete=True,
        confirmed_signs=True,
        confirmed_props=True,
        mode="ALT_HOLD",
        mode_accepts_sticks=True,
        position=False,
    )
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def status(items, check):
    return next(i["status"] for i in items if i["id"] == check)


def test_all_pass():
    items = preflight.evaluate(base())
    assert preflight.summary(items) == {"ok": True, "blocking": []}
    assert status(items, "position") == "info"


def test_gcs_sysid_and_override_time():
    items = preflight.evaluate(base(params={"MAV_GCS_SYSID": 250, "RC_OVERRIDE_TIME": 3}))
    assert status(items, "gcs_input") == "fail"
    items = preflight.evaluate(base(params={"MAV_GCS_SYSID": 255, "RC_OVERRIDE_TIME": 0}))
    assert status(items, "gcs_input") == "fail"
    items = preflight.evaluate(base(params={"MAV_GCS_SYSID": 255, "RC_OVERRIDE_TIME": -1}))
    assert status(items, "gcs_input") == "pass"


def test_waiting_for_parameters_blocks_without_error():
    items = preflight.evaluate(base(params={}, params_complete=False))
    item = next(i for i in items if i["id"] == "gcs_input")
    assert item["status"] == "waiting" and item["detail_en"] == "Waiting for parameters"
    assert "gcs_input" in preflight.summary(items)["blocking"]


def test_px4_com_rc_in_mode():
    f = base(autopilot="PX4", params={"COM_RC_IN_MODE": 0, "NAV_RCL_ACT": 2})
    assert status(preflight.evaluate(f), "gcs_input") == "fail"
    f.params["COM_RC_IN_MODE"] = 4  # stick input disabled
    assert status(preflight.evaluate(f), "gcs_input") == "fail"
    f.params["COM_RC_IN_MODE"] = 3
    assert status(preflight.evaluate(f), "gcs_input") == "pass"
    f.params["NAV_RCL_ACT"] = 0
    assert status(preflight.evaluate(f), "failsafe") == "warn"


def test_battery_levels():
    assert status(preflight.evaluate(base(voltage=10.2)), "battery") == "fail"  # 3.40 V per cell
    assert status(preflight.evaluate(base(voltage=10.6)), "battery") == "warn"  # 3.53
    items = preflight.evaluate(base(voltage=10.6))
    assert "battery" not in preflight.summary(items)["blocking"]
    assert status(preflight.evaluate(base(voltage=None)), "battery") == "waiting"


def test_manual_confirmations_block_real_only():
    items = preflight.evaluate(base(confirmed_signs=False, confirmed_props=False))
    assert {"signs", "props"} <= set(preflight.summary(items)["blocking"])
    sim = preflight.evaluate(base(kind="sim", confirmed_signs=False, params={}, params_complete=False))
    assert preflight.summary(sim)["ok"]
    assert next(i for i in sim if i["id"] == "signs")["detail_en"] == "Simulated"


def test_prearm_and_link_and_mode_warn():
    items = preflight.evaluate(base(prearm=None, mode_accepts_sticks=False, mode="GUIDED"))
    assert status(items, "prearm") == "info" and status(items, "mode") == "warn"
    assert preflight.summary(items)["ok"]
    assert "link" in preflight.summary(preflight.evaluate(base(link_state="lost")))["blocking"]


def test_unreadable_parameter_warns_instead_of_waiting_forever():
    f = base(autopilot="PX4", params={"NAV_RCL_ACT": 2}, params_complete=False)
    assert status(preflight.evaluate(f), "gcs_input") == "waiting"
    f.params_unreadable = frozenset({"COM_RC_IN_MODE"})
    items = preflight.evaluate(f)
    item = next(i for i in items if i["id"] == "gcs_input")
    assert item["status"] == "warn" and "COM_RC_IN_MODE" in item["detail_zh"]
    assert "gcs_input" not in preflight.summary(items)["blocking"]
    # ArduPilot: only one of the two sysid names exists on a given firmware
    f = base(
        params={"SYSID_MYGCS": 255}, params_complete=False, params_unreadable=frozenset({"MAV_GCS_SYSID"})
    )
    assert status(preflight.evaluate(f), "gcs_input") == "waiting"  # RC_OVERRIDE_TIME may still arrive
    f.params_unreadable = frozenset({"MAV_GCS_SYSID", "RC_OVERRIDE_TIME"})
    assert status(preflight.evaluate(f), "gcs_input") == "warn"
