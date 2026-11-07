"""ANet Relay device-level MCP server (04 §5): motors, actuators, RC channels, parameters, MAVLink.

    python mcp/anet_relay_device_mcp.py [--name mcp-device] [--base http://127.0.0.1:8765]

Tools are named ``device_*`` and map one to one onto the device endpoints of 04 §2 and the low level
skills (sticks, attitude, velocity). Danger levels are protected by confirm words, not by hidden tools.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from relay_mcp_common import (
    VID,
    WAIT,
    McpServer,
    RelayClient,
    Tool,
    http_tool,
    run_main,
    schema_tool,
)

SERVER_NAME = "anet-relay-device"

INSTRUCTIONS = (
    "ANet Relay device-level control: individual motors, servo and actuator outputs, RC channels, "
    "parameters, raw MAVLink commands and low level flight skills, for real and simulated aircraft. "
    "Positions are metres East-North-Up; angles in degrees. Every write needs this session to hold the "
    "aircraft: call device_claim first (claims belong to a session, so a claim made through another "
    "Relay MCP server does not carry over). A person who possesses the aircraft always wins and control "
    "calls then fail with possessed_by_human. Dangerous operations need a confirm word on real aircraft: "
    "MOTOR (motor test, actuators on motor channels, disarmed only), ARM, FORCE (force arm), REBOOT and "
    "CALIBRATE (disarmed only), TERMINATE (real and simulated). A missing word fails with confirm_required "
    "and detail.confirm names the word; a needed disarm fails with must_be_disarmed. Never use a confirm "
    "word unless the operator asked for that operation. Remove propellers before any motor test on a real "
    "aircraft. device_commands lists the raw commands device_command accepts. "
    # WP-B: bench procedures (05 §4)
    "Bench procedures (device_procedure_*) run calibration and stability checks step by step; poll "
    "device_procedure_status and answer the waiting prompt. Prompts with operator_only=true must be "
    "answered by the person at the Relay window: tell the operator and wait, never guess."
)

CONFIRM_MOTOR = "MOTOR on real aircraft (simulators need no word); the aircraft must be disarmed"
_ID_PARAMS = {"vehicle": VID}


def _t(name, description, method, path, props=None, required=None, **kw) -> Tool:
    props = {"vehicle": VID, **(props or {})}
    return http_tool(name, description, method, path, props, ["vehicle", *(required or [])], **kw)


def _confirm(words: list[str], note: str) -> dict:
    return {"type": "string", "enum": words, "description": note}


def _messages_post(result: dict, a: dict) -> dict:
    prefix = (a.get("prefix") or "").upper()
    full = bool(a.get("include_fields"))
    out = {}
    for name, m in (result.get("messages") or {}).items():
        if prefix and not name.startswith(prefix):
            continue
        out[name] = m if full else {k: m.get(k) for k in ("hz", "age_s", "count")}
    return {"id": result.get("id"), "count": len(out), "messages": out}


def _params_post(result: dict, a: dict) -> dict:
    prefix = (a.get("prefix") or "").upper()
    limit = int(a.get("limit") or 200)
    items = [p for p in result.get("params", []) if p["name"].upper().startswith(prefix)]
    return {
        "id": result.get("id"),
        "received": result.get("received"),
        "total": result.get("total"),
        "complete": result.get("complete"),
        "fetching": result.get("fetching"),
        "matched": len(items),
        "truncated": len(items) > limit,
        "values": {p["name"]: p["value"] for p in items[:limit]},
    }


def _list_tool(name, description, path, post, props) -> Tool:
    base = http_tool(name, description, "GET", path, {"vehicle": VID, **props}, ["vehicle"], read_only=True)
    inner = base.handler

    def handler(r: RelayClient, a: dict) -> Any:
        shaped = {k: v for k, v in a.items() if k not in props}
        return post(inner(r, shaped), a)

    base.handler = handler
    return base


def _modes(r: RelayClient, a: dict) -> dict:
    v = r.vehicle(a["vehicle"])
    return {"id": v["id"], "autopilot": v.get("autopilot"), "mode": v.get("mode"), "modes": v.get("modes")}


def _skill_tool(name, skill, description, props, required) -> Tool:
    defaults = {k: p["default"] for k, p in props.items() if "default" in p}

    def handler(r: RelayClient, a: dict) -> Any:
        # the defaults the schema advertises are the ones sent: the backend requires some of these keys
        given = {k: v for k, v in a.items() if k not in ("vehicle", "wait") and v is not None}
        params = {**defaults, **given}
        seconds = float(params.get("seconds") or 0.0)
        return {"skill": r.skill(a["vehicle"], skill, params, bool(a.get("wait", True)), 15.0 + seconds)}

    return schema_tool(
        name, description, {"vehicle": VID, **props, "wait": WAIT}, ["vehicle", *required], handler=handler
    )


def _arm(r: RelayClient, a: dict) -> dict:
    return r.arm(a["vehicle"], confirm=a.get("confirm"), force=bool(a.get("force")))


def _claim(r: RelayClient, a: dict) -> dict:
    return r.claim(a["vehicle"])


def _unclaim(r: RelayClient, a: dict) -> dict:
    return r.unclaim(a["vehicle"])


SECONDS10 = {"type": "number", "exclusiveMinimum": 0, "maximum": 10, "description": "duration in seconds"}

# ---------------------------------------------------------------------------- bench procedures (WP-B, 05 §4)

PROCEDURE_IDS = ["full_bench", "preflight_quick", "motor_check", "vibration_check", "hover_tune"]


def _procedure_post(result: Any, _a: dict) -> Any:
    """Drop the live mini-chart series: they are for the Relay window, not for the model."""
    if isinstance(result, dict):
        for step in result.get("steps") or []:
            step.pop("live", None)
    return result


def _test_run(r: RelayClient, a: dict) -> Any:
    run_id = int(a["run_id"])
    if a.get("fmt") == "md":
        return r.request("GET", f"/test-runs/{run_id}/export", None, auth="session", query={"fmt": "md"})
    return r.request("GET", f"/test-runs/{run_id}", None, auth="session")


def procedure_tools() -> list[Tool]:
    return [
        http_tool(
            "device_procedure_list",
            "Catalogue of bench procedures: id, titles, steps (S1..S12 with kind auto, operator, "
            "operator_device, device or flight) and an estimated duration in seconds.",
            "GET",
            "/procedures",
            read_only=True,
        ),
        _t(
            "device_procedure_start",
            "Start a bench procedure on an aircraft this session controls (device_claim first). The aircraft "
            "must be disarmed and on the ground. Steps that spin motors or fly ask a prompt first; flight "
            "steps take off to 0.6 m and land by themselves. auto_answer (simulators only) answers the motor "
            "check from the servo outputs.",
            "POST",
            "/vehicles/{vehicle}/procedure",
            {
                "procedure": {"type": "string", "enum": PROCEDURE_IDS},
                "options": {
                    "type": "object",
                    "properties": {
                        "hover_s": {"type": "number", "minimum": 5, "maximum": 60},
                        "skip": {"type": "array", "items": {"type": "string"}, "description": "step ids"},
                        "auto_answer": {"type": "boolean", "default": False},
                    },
                },
            },
            ["procedure"],
            destructive=True,
            post=_procedure_post,
        ),
        _t(
            "device_procedure_status",
            "The current or most recent run: state (running, waiting, passed, warned, failed, aborted), the "
            "current step and its prompt (answers, confirm_word, operator_only), and per step the verdict, "
            "metrics, findings and suggested parameter changes. Suggestions are never written automatically.",
            "GET",
            "/vehicles/{vehicle}/procedure",
            read_only=True,
            post=_procedure_post,
        ),
        _t(
            "device_procedure_answer",
            "Answer the prompt of the waiting step. answer is one of prompt.answers[].id. On real aircraft "
            "the prompt's confirm_word is required (MOTOR, ARM, CALIBRATE, or the parameter name), and "
            "operator_only prompts (stick directions, motor order, propellers) refuse agents with "
            "operator_required: ask the operator instead.",
            "POST",
            "/vehicles/{vehicle}/procedure/answer",
            {
                "step": {"type": "string", "description": "id of the waiting step, e.g. S6"},
                "answer": {"type": "string"},
                "confirm": {"type": "string", "description": "the prompt's confirm_word, when it has one"},
            },
            ["step", "answer"],
            destructive=True,
            post=_procedure_post,
        ),
        _t(
            "device_procedure_abort",
            "Abort the running procedure: motor tests stop and an airborne aircraft lands.",
            "POST",
            "/vehicles/{vehicle}/procedure/abort",
            post=_procedure_post,
        ),
        http_tool(
            "device_test_runs",
            "Saved bench reports, newest first: run_id, procedure, state, verdict, step counts.",
            "GET",
            "/test-runs",
            {
                "vehicle": {"type": "string", "description": "aircraft id or address (optional)"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
            },
            read_only=True,
        ),
        schema_tool(
            "device_test_run",
            "One saved bench report with every step, metric, finding, suggestion and the parameter baseline. "
            "fmt=md returns the Markdown report instead.",
            {
                "run_id": {"type": "integer"},
                "fmt": {"type": "string", "enum": ["json", "md"], "default": "json"},
            },
            ["run_id"],
            handler=_test_run,
            read_only=True,
        ),
    ]


def build_tools() -> list[Tool]:
    return [
        schema_tool(
            "device_claim",
            "Claim an aircraft for this session; required before any device write. Fails while a person "
            "possesses it.",
            _ID_PARAMS,
            ["vehicle"],
            handler=_claim,
        ),
        schema_tool(
            "device_release_claim",
            "Release this session's claim on an aircraft.",
            _ID_PARAMS,
            ["vehicle"],
            handler=_unclaim,
        ),
        _t(
            "device_overview",
            "Device overview: motor count, layout, current motor outputs (us) and whether motor test is "
            "supported; servo outputs; RC input; frame type; sensors; estimator; vibration; home; "
            "parameter progress; message rates.",
            "GET",
            "/vehicles/{vehicle}/device",
            read_only=True,
        ),
        _list_tool(
            "device_messages",
            "List the MAVLink messages received from the aircraft with rate (hz), age and count. "
            "Set include_fields for the latest field values of every listed message (large); prefer "
            "device_message for a single one.",
            "/vehicles/{vehicle}/messages",
            _messages_post,
            {
                "prefix": {"type": "string", "description": "only names starting with this, e.g. SERVO"},
                "include_fields": {"type": "boolean", "default": False},
            },
        ),
        _t(
            "device_message",
            "Latest frame of one MAVLink message: {name, fields, t, hz}. 404 message_not_seen if it was "
            "never received (use device_request_message or device_message_interval to start it).",
            "GET",
            "/vehicles/{vehicle}/messages/{name}",
            {"name": {"type": "string", "description": "message name, e.g. ATTITUDE, SERVO_OUTPUT_RAW"}},
            ["name"],
            read_only=True,
        ),
        _t(
            "device_param",
            "Read one parameter {name, value, type}. Waits up to 2 s when the table is not loaded; 404 "
            "param_not_found if the autopilot does not answer.",
            "GET",
            "/vehicles/{vehicle}/params/{name}",
            {"name": {"type": "string", "description": "parameter name, e.g. BATT_CAPACITY"}},
            ["name"],
            read_only=True,
        ),
        _t(
            "device_params_read",
            "Read up to 32 parameters by name (waits up to 3 s). Returns {values, missing}. Needs the "
            "controller or operator role.",
            "POST",
            "/vehicles/{vehicle}/params/read",
            {
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 32,
                }
            },
            ["names"],
        ),
        _list_tool(
            "device_params_list",
            "The loaded parameter table as {NAME: value}, filtered by name prefix. Check complete / total "
            "first; device_params_refresh reloads the table.",
            "/vehicles/{vehicle}/params",
            _params_post,
            {
                "prefix": {"type": "string", "description": "only names starting with this, e.g. MOT_"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200},
            },
        ),
        _t(
            "device_param_set",
            "Write one parameter. Needs control of the aircraft (device_claim first). confirm must equal "
            "the parameter name. Read the old value first and report it.",
            "POST",
            "/vehicles/{vehicle}/params",
            {
                "name": {"type": "string"},
                "value": {"type": "number"},
                "confirm": {"type": "string", "description": "must equal `name`"},
            },
            ["name", "value", "confirm"],
            destructive=True,
        ),
        _t(
            "device_params_refresh",
            "Reload the whole parameter table from the autopilot (takes seconds to minutes).",
            "POST",
            "/vehicles/{vehicle}/params/refresh",
        ),
        http_tool(
            "device_commands",
            "Catalogue of raw MAVLink commands device_command accepts: id, name, labels, parameter "
            "definitions, danger level (motor, arm, reboot, calibrate, terminate), whether the aircraft "
            "must be disarmed, and supported autopilots.",
            "GET",
            "/commands",
            read_only=True,
        ),
        _t(
            "device_command",
            "Send one catalogued MAVLink COMMAND_LONG / COMMAND_INT and wait for COMMAND_ACK (ack is null "
            "on timeout). Only commands listed by device_commands are allowed (400 command_not_allowed). "
            "Dangerous entries need the confirm word shown in the catalogue (MOTOR, ARM, REBOOT, CALIBRATE, "
            "TERMINATE) on real aircraft; TERMINATE always. Needs the controller role.",
            "POST",
            "/vehicles/{vehicle}/command",
            {
                "command": {
                    "type": ["string", "integer"],
                    "description": "name such as MAV_CMD_NAV_TAKEOFF or the numeric id",
                },
                "params": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 0,
                    "maxItems": 7,
                    "description": "param1..param7 (missing ones are 0)",
                },
                "frame": {"type": "integer", "description": "coordinate frame for COMMAND_INT"},
                "use_int": {"type": "boolean", "description": "send COMMAND_INT instead of COMMAND_LONG"},
                "confirm": {"type": "string", "description": "confirm word from the catalogue entry"},
                "timeout_s": {"type": "number", "minimum": 0.1, "maximum": 10, "default": 3},
            },
            ["command"],
            destructive=True,
        ),
        _t(
            "device_motor_test",
            "Spin one motor or all motors in turn at a throttle percentage. The aircraft must be disarmed "
            "(409 must_be_disarmed). Real aircraft need confirm='MOTOR' and propellers removed; throttle is "
            "limited to 30 percent on real aircraft (simulators 100). 'all' tests motors in order.",
            "POST",
            "/vehicles/{vehicle}/motors/test",
            {
                "motor": {
                    "oneOf": [
                        {"type": "integer", "minimum": 1, "maximum": 8},
                        {"type": "string", "enum": ["all"]},
                    ],
                    "description": "motor number 1..8 in the autopilot's motor order, or 'all'",
                },
                "throttle_pct": {"type": "number", "minimum": 0, "maximum": 100},
                "seconds": {"type": "number", "minimum": 0.2, "maximum": 5, "default": 1},
                "confirm": _confirm(["MOTOR"], CONFIRM_MOTOR),
            },
            ["motor", "throttle_pct"],
            destructive=True,
        ),
        _t(
            "device_motors_stop",
            "Stop every running motor test immediately.",
            "POST",
            "/vehicles/{vehicle}/motors/stop",
        ),
        _t(
            "device_actuator",
            "Drive one output (servo, gimbal, gripper, motor channel) by index. PX4 uses "
            "MAV_CMD_ACTUATOR_TEST, ArduPilot MAV_CMD_DO_SET_SERVO. Give `value` (-1..1) or `pwm` "
            "(800..2200 us). seconds 0 holds the output. An index on a motor channel is treated as a motor "
            "test: disarmed only and confirm='MOTOR' on real aircraft.",
            "POST",
            "/vehicles/{vehicle}/actuators/set",
            {
                "index": {"type": "integer", "minimum": 1, "maximum": 16, "description": "output number"},
                "value": {"type": "number", "minimum": -1, "maximum": 1},
                "pwm": {"type": "integer", "minimum": 800, "maximum": 2200},
                "seconds": {"type": "number", "minimum": 0, "maximum": 10, "default": 0},
                "confirm": _confirm(["MOTOR"], CONFIRM_MOTOR),
            },
            ["index"],
            destructive=True,
        ),
        _t(
            "device_rc",
            "Send one RC override frame: raw channels (1..18, 1000..2000 us) and/or named axes. It expires "
            "after 0.8 s, so repeat it to hold. Shares the stick path: the last writer wins. Motion on real "
            "aircraft: the operator must approve.",
            "POST",
            "/vehicles/{vehicle}/rc",
            {
                "channels": {
                    "type": "object",
                    "description": 'channel number as string to pulse in us, e.g. {"5": 1500}',
                    "patternProperties": {
                        "^([1-9]|1[0-8])$": {"type": "integer", "minimum": 1000, "maximum": 2000}
                    },
                    "additionalProperties": False,
                },
                "axes": {
                    "type": "object",
                    "properties": {
                        "roll": {"type": "number", "minimum": -1, "maximum": 1},
                        "pitch": {"type": "number", "minimum": -1, "maximum": 1},
                        "yaw": {"type": "number", "minimum": -1, "maximum": 1},
                        "throttle": {"type": "number", "minimum": 0, "maximum": 1},
                        **{f"aux{i}": {"type": "number", "minimum": -1, "maximum": 1} for i in range(1, 7)},
                    },
                    "additionalProperties": False,
                },
            },
        ),
        _t(
            "device_message_interval",
            "Set how often the autopilot streams a MAVLink message (0 Hz stops it).",
            "POST",
            "/vehicles/{vehicle}/message-interval",
            {
                "message": {"type": "string", "description": "message name, e.g. ATTITUDE"},
                "hz": {"type": "number", "minimum": 0, "maximum": 50},
            },
            ["message", "hz"],
        ),
        _t(
            "device_request_message",
            "Ask the autopilot to send one message once (MAV_CMD_REQUEST_MESSAGE).",
            "POST",
            "/vehicles/{vehicle}/request-message",
            {"message": {"type": "string"}},
            ["message"],
        ),
        _t(
            "device_reboot",
            "Reboot the flight controller. Disarmed only; confirm='REBOOT' on real aircraft. The link drops "
            "for several seconds.",
            "POST",
            "/vehicles/{vehicle}/reboot",
            {"confirm": _confirm(["REBOOT"], "REBOOT on real aircraft")},
            destructive=True,
        ),
        _t(
            "device_calibrate",
            "Run a sensor calibration. Disarmed only, aircraft still and level; confirm='CALIBRATE' on real "
            "aircraft.",
            "POST",
            "/vehicles/{vehicle}/calibrate",
            {
                "sensor": {"type": "string", "enum": ["gyro", "level", "baro", "accel_simple"]},
                "confirm": _confirm(["CALIBRATE"], "CALIBRATE on real aircraft"),
            },
            ["sensor"],
            destructive=True,
        ),
        _t(
            "device_set_home",
            "Set the home position: here=true uses the current position, otherwise give lat, lon (degrees) "
            "and alt (metres).",
            "POST",
            "/vehicles/{vehicle}/home",
            {
                "here": {"type": "boolean"},
                "lat": {"type": "number", "minimum": -90, "maximum": 90},
                "lon": {"type": "number", "minimum": -180, "maximum": 180},
                "alt": {"type": "number"},
            },
        ),
        _t(
            "device_terminate",
            "FLIGHT TERMINATION: motors stop at once, then the aircraft is force-disarmed; it falls if "
            "airborne. confirm='TERMINATE' is required on real AND simulated aircraft.",
            "POST",
            "/vehicles/{vehicle}/terminate",
            {"confirm": _confirm(["TERMINATE"], "always required")},
            ["confirm"],
            destructive=True,
        ),
        schema_tool(
            "device_arm",
            "Arm the motors. Real aircraft need confirm='ARM' and passing preflight checks. force=true "
            "skips the autopilot's own pre-arm checks and needs confirm='FORCE'; use it only when the "
            "operator asked for it.",
            {
                "vehicle": VID,
                "force": {"type": "boolean", "default": False},
                "confirm": _confirm(["ARM", "FORCE"], "ARM on real aircraft; FORCE together with force=true"),
            },
            ["vehicle"],
            handler=_arm,
        ),
        _t(
            "device_disarm",
            "Disarm the motors (most autopilots refuse while flying).",
            "POST",
            "/vehicles/{vehicle}/arm",
            body_extra={"arm": False},
        ),
        schema_tool(
            "device_modes",
            "Current flight mode and the list of modes the aircraft supports.",
            _ID_PARAMS,
            ["vehicle"],
            read_only=True,
            handler=_modes,
        ),
        _t(
            "device_set_mode",
            "Set the flight mode by name (see device_modes).",
            "POST",
            "/vehicles/{vehicle}/mode",
            {"name": {"type": "string", "description": "e.g. STABILIZE, ALT_HOLD, LOITER, POSCTL, OFFBOARD"}},
            ["name"],
        ),
        _skill_tool(
            "device_sticks",
            "sticks",
            "Hold a stick position for `seconds`, then centre the sticks (throttle returns to 0.5 in "
            "altitude-hold modes, otherwise stays). Needs a manual mode and an armed aircraft. roll right, "
            "pitch forward and yaw clockwise are positive. Motion: real aircraft need operator approval.",
            {
                "roll": {"type": "number", "minimum": -1, "maximum": 1, "default": 0},
                "pitch": {"type": "number", "minimum": -1, "maximum": 1, "default": 0},
                "yaw": {"type": "number", "minimum": -1, "maximum": 1, "default": 0},
                "throttle": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
                "seconds": SECONDS10,
            },
            ["seconds"],
        ),
        _skill_tool(
            "device_attitude",
            "attitude",
            "Command a roll / pitch attitude, yaw rate and thrust for `seconds` while airborne "
            "(SET_ATTITUDE_TARGET at 20 Hz; ArduPilot GUIDED_NOGPS, PX4 OFFBOARD), then return to the "
            "altitude-hold mode. Needs an armed aircraft in the air. Motion: real aircraft need operator "
            "approval.",
            {
                "roll_deg": {"type": "number", "minimum": -30, "maximum": 30, "default": 0},
                "pitch_deg": {"type": "number", "minimum": -30, "maximum": 30, "default": 0},
                "yaw_rate_dps": {"type": "number", "minimum": -90, "maximum": 90, "default": 0},
                "thrust": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "collective thrust; omit for the calibrated hover thrust",
                },
                "seconds": SECONDS10,
            },
            ["seconds"],
        ),
        _skill_tool(
            "device_velocity",
            "velocity",
            "Command a velocity in the East-North-Up frame for `seconds` (SET_POSITION_TARGET_LOCAL_NED at "
            "10 Hz). Needs a position estimate in a position mode. It stops and fails with "
            "outside_safety_volume before leaving the safety volume. Motion: real aircraft need operator "
            "approval.",
            {
                "vx": {"type": "number", "minimum": -2, "maximum": 2, "description": "east, m/s"},
                "vy": {"type": "number", "minimum": -2, "maximum": 2, "description": "north, m/s"},
                "vz": {"type": "number", "minimum": -2, "maximum": 2, "description": "up, m/s"},
                "yaw_rate_dps": {"type": "number", "minimum": -90, "maximum": 90},
                "seconds": SECONDS10,
            },
            ["seconds"],
        ),
        *procedure_tools(),
    ]


class Server(McpServer):
    def __init__(self, base: str, name: str, out=None, operator_token: str | None = None) -> None:
        super().__init__(
            base,
            name,
            server_name=SERVER_NAME,
            title="ANet Relay Device",
            instructions=INSTRUCTIONS,
            tools=build_tools(),
            operator_token=operator_token,
            out=out,
        )


def main(argv: list[str] | None = None) -> int:
    return run_main(
        "anet-relay-device-mcp", "ANet Relay device-level MCP server (stdio)", "mcp-device", Server, argv
    )


if __name__ == "__main__":
    raise SystemExit(main())
