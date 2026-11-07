"""Decoding, modes and parameter protocol helpers (03 §5)."""

import struct

import pytest

from relay.mav import decode, modes, params
from relay.mav.mavlink import Encoder, M, Parser


def wire(msg, sysid=1, compid=1):
    """Encode then parse, so headers (sysid, compid, seq) are real."""
    return Parser().parse(Encoder(sysid, compid).encode(msg))[0]


def hb(autopilot=M.MAV_AUTOPILOT_ARDUPILOTMEGA, mtype=M.MAV_TYPE_QUADROTOR, base=0, custom=0):
    return M.MAVLink_heartbeat_message(mtype, autopilot, base, custom, M.MAV_STATE_STANDBY, 3)


def test_identity_locks_on_first_autopilot_heartbeat_only():
    st = decode.fresh_state()
    # A bridge or GCS heartbeat never identifies the vehicle.
    assert decode.apply_message(st, wire(hb(M.MAV_AUTOPILOT_INVALID, M.MAV_TYPE_GCS), 1, 240), 1.0, 1.0) == []
    assert st["sysid"] is None
    notes = decode.apply_message(st, wire(hb(base=M.MAV_MODE_FLAG_SAFETY_ARMED, custom=5)), 2.0, 2.0)
    assert ("identity", None) in notes and st["autopilot"] == "ArduPilot"
    assert st["mode"] == "LOITER" and st["armed"]
    # A different component's autopilot heartbeat does not refresh the timer.
    decode.apply_message(st, wire(hb(), 1, 2), 9.0, 9.0)
    assert st["last_heartbeat"] == 2.0
    # Telemetry from another system is ignored.
    decode.apply_message(st, wire(M.MAVLink_attitude_message(0, 0.5, 0, 0, 0, 0, 0), 7, 1), 3.0, 3.0)
    assert st["attitude"] is None


def test_px4_mode_names():
    base, custom = modes.px4_custom_mode("POSCTL")
    st = decode.fresh_state()
    decode.apply_message(st, wire(hb(M.MAV_AUTOPILOT_PX4, base=base, custom=custom)), 1.0, 1.0)
    assert st["autopilot"] == "PX4" and st["mode"] == "POSCTL"
    assert modes.mode_class("PX4", "LOITER") == "auto"
    assert modes.mode_class("ArduPilot", "LOITER") == "position"


def test_battery_and_crash_text():
    st = decode.fresh_state()
    decode.apply_message(st, wire(hb()), 1.0, 1.0)
    sys_status = M.MAVLink_sys_status_message(
        M.MAV_SYS_STATUS_SENSOR_ABSOLUTE_PRESSURE | M.MAV_SYS_STATUS_PREARM_CHECK,
        0,
        M.MAV_SYS_STATUS_SENSOR_ABSOLUTE_PRESSURE,
        0,
        11800,
        420,
        77,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    decode.apply_message(st, wire(sys_status), 1.1, 1.1)
    assert st["battery"]["voltage"] == 11.8 and st["battery"]["current"] == 4.2
    assert st["prearm"] is False  # present but not healthy
    notes = decode.apply_message(
        st, wire(M.MAVLink_statustext_message(2, b"Crash: Disarming", 0, 0)), 1.2, 1.2
    )
    assert ("crash", "Crash: Disarming") in notes and st["crashed"]


def test_px4_param_bytewise_round_trip():
    encoded = decode.px4_param_encode(3, M.MAV_PARAM_TYPE_INT32)
    assert struct.unpack("<i", struct.pack("<f", encoded))[0] == 3
    assert decode.px4_param_decode(encoded, M.MAV_PARAM_TYPE_INT32) == 3.0
    assert params.wire_value(3, M.MAV_PARAM_TYPE_INT32, "ArduPilot") == 3.0


def test_param_normalise_types():
    assert params.normalise(255, M.MAV_PARAM_TYPE_INT16) == 255
    with pytest.raises(ValueError):
        params.normalise(1.5, M.MAV_PARAM_TYPE_INT8)
    with pytest.raises(ValueError):
        params.normalise(300, M.MAV_PARAM_TYPE_UINT8)
    with pytest.raises(ValueError):
        params.normalise(True, M.MAV_PARAM_TYPE_REAL32)
    assert params.same_value(0.1, 0.1000001, M.MAV_PARAM_TYPE_REAL32)


def test_param_fetch_rereads_missing_indices():
    fetch = params.ParamFetch(started=0.0, last_progress=0.0)
    table = {"A": {"index": 0}, "C": {"index": 2}}
    assert fetch.due_reads(table, 4, 0.5) == []  # not stalled yet
    assert fetch.due_reads(table, 4, 2.0) == [1, 3]


def test_possess_mode_choice():
    assert modes.possess_mode("ArduPilot", True) == "ALT_HOLD"
    assert modes.possess_mode("ArduPilot", False) == "STABILIZE"
    assert modes.possess_mode("PX4", True) == "ALTCTL"
    assert modes.possess_mode("PX4", True, "stabilize") == "STABILIZED"
    assert modes.accepts_sticks("PX4", "POSCTL") and not modes.accepts_sticks("PX4", "OFFBOARD")
    assert not modes.accepts_sticks("ArduPilot", "GUIDED")
