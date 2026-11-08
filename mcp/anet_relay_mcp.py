"""ANet Relay MCP server (03 §10.7): stdio, JSON-RPC 2.0, standard library only.

    python mcp/anet_relay_mcp.py [--name mcp] [--base http://127.0.0.1:8765]

The server opens one agent session when it starts (named by ``--name``) and renews the lease in the
background; the session closes when the client closes stdin. Tools cover reading the fleet, claiming
aircraft and the high-level skills. There is deliberately no stick tool: a language model cannot fly
a 50 Hz control loop. Protocol messages go to stdout, one JSON object per line; logs go to stderr.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from relay_mcp_common import VID, WAIT, McpServer, RelayClient, RelayError, Tool, log, run_main

SERVER_NAME = "anet-relay"

INSTRUCTIONS = (
    "ANet Relay controls small quadcopters (real and simulated) on the local network. "
    "Positions are metres in an East-North-Up frame (x east, y north, z up); headings are compass degrees. "
    "Workflow: relay_fleet to see aircraft, relay_claim one, then relay_takeoff, relay_hold, relay_move, "
    "relay_yaw and relay_land, or relay_plan for a whole sequence. relay_move and relay_yaw work without a "
    "position estimate (moves are then open-loop estimates); relay_goto needs one. "
    "A person can take over (possess) any aircraft at any time; control calls then fail with "
    "possessed_by_human and the claim resumes automatically when they hand it back (event relay_handback). "
    "Real aircraft need confirm='ARM' to arm and must pass preflight checks. goto targets must stay inside "
    "the safety volume reported by relay_fleet. Use relay_stop only in an emergency: it cuts the motors. "
    "relay_sticks_for pushes the sticks for a few seconds (a language model cannot fly a 50 Hz loop); "
    "relay_refly repeats an earlier recorded flight."
)
# 06 §5 (WP-1): at agent_flight_level "sticks" (the default) agent sessions fly with sticks only.
# Relay refuses everything else with 403 sticks_only; this server also lists only the tools that still
# work. The level is read from GET /settings on every tools/list: an operator change shows on the next one.
STICKS_TOOL_NAMES = frozenset(
    {
        "relay_fleet",
        "relay_vehicle",
        "relay_topology",
        "relay_events",
        "relay_claim",
        "relay_release_claim",
        "relay_arm",
        "relay_disarm",
        "relay_mode",
        "relay_land",
        "relay_stop",
        "relay_course_start",
        "relay_sticks_for",
    }
)
STICKS_INSTRUCTIONS = (
    "ANet Relay controls small quadcopters (real and simulated) on the local network. "
    "The operator has set the agent flight level to sticks: agents fly like a person with a radio. "
    "Workflow: relay_fleet to see aircraft, relay_claim one, relay_mode to a manual mode (STABILIZE, ACRO; "
    "PX4 MANUAL, STABILIZED), relay_arm, then relay_sticks_for to push the sticks for a few seconds. "
    "Takeoff, hold, move, yaw, goto, plans and re-fly are refused with sticks_only at this level; "
    "relay_land stays available as a safety action and relay_stop cuts the motors in an emergency. "
    "For closed-loop flight, deploy an onboard program (POST /vehicles/{id}/program). "
    "A person can take over (possess) any aircraft at any time; control calls then fail with "
    "possessed_by_human. Real aircraft need confirm='ARM' to arm and must pass preflight checks."
)


def _tool(
    name: str,
    description: str,
    properties: dict | None = None,
    required: list[str] | None = None,
    read_only: bool = False,
    destructive: bool = False,
) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": read_only, "destructiveHint": destructive, "openWorldHint": True},
    }


TOOLS = [
    _tool(
        "relay_fleet",
        "List every aircraft with link, mode, position, battery, controller and claim, plus the "
        "safety volume and open agent sessions.",
        read_only=True,
    ),
    _tool(
        "relay_vehicle",
        "Full state of one aircraft, including preflight results and course progress.",
        {"vehicle": VID},
        ["vehicle"],
        read_only=True,
    ),
    _tool(
        "relay_topology",
        "Network topology: station, aircraft, bridges, sessions, and distances between "
        "aircraft that share a frame.",
        read_only=True,
    ),
    _tool(
        "relay_events",
        "Recent events (possession, hand-back, skills, rings, alerts). Pass `after` = the last "
        "seq you saw to get only newer events.",
        {
            "after": {"type": "integer", "minimum": 0, "description": "omit for the most recent events"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
            "vehicle": VID,
        },
        read_only=True,
    ),
    _tool(
        "relay_claim",
        "Claim an aircraft for this agent session. Needed before any control tool.",
        {"vehicle": VID},
        ["vehicle"],
    ),
    _tool(
        "relay_release_claim", "Release this session's claim on an aircraft.", {"vehicle": VID}, ["vehicle"]
    ),
    _tool(
        "relay_arm",
        "Arm the motors. Real aircraft need confirm='ARM' and passing preflight checks.",
        {"vehicle": VID, "confirm": {"type": "string", "enum": ["ARM"]}},
        ["vehicle"],
    ),
    _tool(
        "relay_disarm",
        "Disarm the motors (refused by most autopilots while flying; use relay_land).",
        {"vehicle": VID},
        ["vehicle"],
    ),
    _tool(
        "relay_mode",
        "Set the flight mode by name (see `modes` in relay_vehicle).",
        {"vehicle": VID, "name": {"type": "string", "description": "e.g. LOITER, ALT_HOLD, POSCTL"}},
        ["vehicle", "name"],
    ),
    _tool(
        "relay_takeoff",
        "Take off to `alt` metres above the ground (arms simulators automatically) and hold.",
        {
            "vehicle": VID,
            "alt": {"type": "number", "minimum": 0.2, "maximum": 10, "default": 1.0},
            "confirm": {
                "type": "string",
                "enum": ["ARM"],
                "description": "required for real aircraft on the ground",
            },
            "wait": WAIT,
        },
        ["vehicle"],
    ),
    _tool("relay_land", "Land in place and disarm.", {"vehicle": VID, "wait": WAIT}, ["vehicle"]),
    _tool(
        "relay_hold",
        "Hold the current altitude (and position when estimated). With `seconds`, hover that long.",
        {"vehicle": VID, "seconds": {"type": "number", "minimum": 0, "maximum": 120, "default": 0}},
        ["vehicle"],
    ),
    _tool(
        "relay_move",
        "Move relative to the aircraft's nose: forward (negative: back), right (negative: left), up "
        "(negative: down), in metres. Closed loop with a position estimate; otherwise a timed tilt whose "
        "horizontal distance is an estimate (at most 2 m).",
        {
            "vehicle": VID,
            "forward": {"type": "number", "minimum": -5, "maximum": 5, "default": 0},
            "right": {"type": "number", "minimum": -5, "maximum": 5, "default": 0},
            "up": {"type": "number", "minimum": -2, "maximum": 2, "default": 0},
            "wait": WAIT,
        },
        ["vehicle"],
    ),
    _tool(
        "relay_yaw",
        "Turn in place by `deg` degrees relative to the current heading, positive clockwise (right).",
        {"vehicle": VID, "deg": {"type": "number", "minimum": -360, "maximum": 360}, "wait": WAIT},
        ["vehicle", "deg"],
    ),
    _tool(
        "relay_plan",
        "Fly a sequence of steps and wait for it to finish. Actions: takeoff {alt}, hover {seconds}, "
        "move {forward, right, up}, yaw {deg}, land. Example: takeoff 1 m, hover 3 s, move forward 1, "
        "move forward -1, yaw 45, land. A failed step stops the sequence and the aircraft holds.",
        {
            "vehicle": VID,
            "steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": 24,
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["takeoff", "hover", "move", "yaw", "land"]},
                        "alt": {"type": "number"},
                        "seconds": {"type": "number"},
                        "forward": {"type": "number"},
                        "right": {"type": "number"},
                        "up": {"type": "number"},
                        "deg": {"type": "number"},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
            "confirm": {"type": "string", "enum": ["ARM"], "description": "real aircraft on the ground"},
        },
        ["vehicle", "steps"],
    ),
    _tool(
        "relay_goto",
        "Fly to x (east), y (north), z (up) metres in the aircraft's position frame. The target "
        "must be inside the safety volume around the aircraft's origin.",
        {
            "vehicle": VID,
            "x": {"type": "number"},
            "y": {"type": "number"},
            "z": {"type": "number"},
            "yaw": {"type": "number", "description": "heading in degrees, 0 north, clockwise"},
            "wait": WAIT,
        },
        ["vehicle", "x", "y", "z"],
    ),
    _tool(
        "relay_stop",
        "EMERGENCY STOP: throttle to zero and disarm immediately. The aircraft falls if airborne.",
        {"vehicle": VID},
        ["vehicle"],
        destructive=True,
    ),
    _tool(
        "relay_course_start",
        "Start a training course (ring gates) on an aircraft. Returns the rings placed in "
        "the aircraft's frame (centre c, compass heading yaw_deg of the ring normal, radius r); fly through "
        "them in order with relay_goto.",
        {
            "vehicle": VID,
            "course_id": {
                "type": "string",
                "default": "cage-loop",
                "description": "cage-loop, slalom, climb or a custom course id",
            },
        },
        ["vehicle"],
    ),
    _tool(
        "relay_sticks_for",
        "Hold a stick position for `seconds` (at most 10), then centre the sticks. The aircraft must be "
        "armed in a manual mode. roll/pitch/yaw in [-1, 1] (roll right, pitch forward and yaw "
        "clockwise are positive), throttle in [0, 1] (0.5 holds altitude in "
        "altitude-hold modes). A person taking over cancels it.",
        {
            "vehicle": VID,
            "roll": {"type": "number", "minimum": -1, "maximum": 1, "default": 0},
            "pitch": {"type": "number", "minimum": -1, "maximum": 1, "default": 0},
            "yaw": {"type": "number", "minimum": -1, "maximum": 1, "default": 0},
            "throttle": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
            "seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 10},
            "wait": WAIT,
        },
        ["vehicle", "seconds"],
    ),
    _tool(
        "relay_refly",
        "Fly again from an earlier recording (session_id from the records list). mode auto picks the "
        "recommended source: plan (the recorded action sequence), path (the recorded track as waypoints, "
        "translated to the aircraft's current origin) or sticks (the recorded stick inputs on the original "
        "time axis, scaled by speed). Real aircraft on the ground need confirm='ARM'. A person taking over "
        "cancels it.",
        {
            "vehicle": VID,
            "session_id": {"type": "integer", "minimum": 1, "description": "record id"},
            "mode": {"type": "string", "enum": ["auto", "plan", "path", "sticks"], "default": "auto"},
            "speed": {"type": "number", "minimum": 0.25, "maximum": 2, "description": "sticks mode only"},
            "confirm": {"type": "string", "enum": ["ARM"], "description": "real aircraft on the ground"},
            "wait": WAIT,
        },
        ["vehicle", "session_id"],
    ),
]
TOOL_NAMES = {t["name"] for t in TOOLS}

COMPACT_KEYS = (
    "id",
    "kind",
    "label",
    "autopilot",
    "armed",
    "mode",
    "landed",
    "crashed",
    "position",
    "altitude_m",
    "heading_deg",
    "controller",
    "claim",
    "possession",
    "skill",
    "plan",
    "capabilities",
    "preflight",
)


def _compact(v: dict) -> dict:
    out = {k: v.get(k) for k in COMPACT_KEYS}
    out["link"] = v["link"]["state"]
    out["battery"] = {k: v["battery"].get(k) for k in ("voltage", "remaining_pct", "level")}
    if v.get("course"):
        c = v["course"]
        out["course"] = {k: c.get(k) for k in ("course_id", "state", "next", "total", "elapsed_s", "best_s")}
    return out


def _dispatch(r: RelayClient, name: str, a: dict) -> Any:
    vid = a.get("vehicle")
    wait = bool(a.get("wait", True))
    if name == "relay_fleet":
        fleet = r.fleet()
        return {
            "safety_volume": fleet["safety_volume"],
            "sessions": fleet["sessions"],
            "this_session": r.session_id,
            "vehicles": [_compact(v) for v in fleet["vehicles"]],
            "alerts": [x for x in fleet["alerts"] if not x["acked"]],
        }
    if name == "relay_vehicle":
        return r.vehicle(vid)
    if name == "relay_topology":
        return r.topology()
    if name == "relay_events":
        limit = int(a.get("limit", 50))
        after = a.get("after")
        # Without a cursor return the newest events; with one, the next page after it.
        batch = r.events(after=int(after or 0), limit=limit, vehicle=vid, tail=after is None)
        return {
            "last_seq": batch["last_seq"],
            "events": [
                {k: e[k] for k in ("seq", "t", "kind", "vehicle", "text_en", "data")} for e in batch["events"]
            ],
        }
    if name == "relay_claim":
        return r.claim(vid)
    if name == "relay_release_claim":
        return r.unclaim(vid)
    if name == "relay_arm":
        return r.arm(vid, confirm=a.get("confirm"))
    if name == "relay_disarm":
        return r.disarm(vid)
    if name == "relay_mode":
        return r.mode(vid, a["name"])
    if name == "relay_takeoff":
        return {"skill": r.takeoff(vid, alt=float(a.get("alt", 1.0)), wait=wait, confirm=a.get("confirm"))}
    if name == "relay_land":
        return {"skill": r.land(vid, wait=wait)}
    if name == "relay_hold":
        return {"skill": r.hold(vid, seconds=float(a.get("seconds") or 0.0), wait=True)}
    if name == "relay_move":
        forward, right, up = (float(a.get(k) or 0.0) for k in ("forward", "right", "up"))
        return {"skill": r.move(vid, forward, right, up, wait=wait)}
    if name == "relay_yaw":
        return {"skill": r.yaw(vid, float(a["deg"]), wait=wait)}
    if name == "relay_plan":
        return {"plan": r.plan(vid, a["steps"], name=r.session_name, confirm=a.get("confirm"))}
    if name == "relay_goto":
        return {
            "skill": r.goto(vid, float(a["x"]), float(a["y"]), float(a["z"]), yaw=a.get("yaw"), wait=wait)
        }
    if name == "relay_stop":
        return r.stop(vid)
    if name == "relay_course_start":
        return r.course_start(vid, a.get("course_id") or "cage-loop")
    if name == "relay_sticks_for":
        return {
            "skill": r.sticks_for(
                vid,
                roll=float(a.get("roll") or 0.0),
                pitch=float(a.get("pitch") or 0.0),
                yaw=float(a.get("yaw") or 0.0),
                throttle=float(a.get("throttle", 0.5)),
                seconds=float(a["seconds"]),
                wait=wait,
            )
        }
    if name == "relay_refly":
        return r.refly(
            vid,
            int(a["session_id"]),
            mode=a.get("mode") or "auto",
            speed=a.get("speed"),
            confirm=a.get("confirm"),
            wait=wait,
        )
    raise KeyError(name)


def _tools() -> list[Tool]:
    return [
        Tool(
            t["name"],
            t["description"],
            t["inputSchema"]["properties"],
            t["inputSchema"]["required"],
            (lambda r, a, n=t["name"]: _dispatch(r, n, a)),
            t["annotations"]["readOnlyHint"],
            t["annotations"]["destructiveHint"],
        )
        for t in TOOLS
    ]


class Server(McpServer):
    def __init__(self, base: str, name: str, out=None, operator_token: str | None = None) -> None:
        super().__init__(
            base,
            name,
            server_name=SERVER_NAME,
            title="ANet Relay",
            instructions=INSTRUCTIONS,
            tools=_tools(),
            operator_token=operator_token,
            out=out,
        )
        self.level: str | None = None  # last level read from Relay

    def flight_level(self) -> str:
        """``sticks`` or ``full`` from GET /settings; a Relay without the setting flies everything (``full``);
        when Relay cannot be reached, the last level read (``sticks``, the default, before the first read)."""
        try:
            settings = self.relay.settings()
        except (RelayError, OSError) as exc:
            log(SERVER_NAME, f"could not read the agent flight level: {exc}")
            return self.level or "sticks"
        level = settings.get("agent_flight_level") if isinstance(settings, dict) else None
        self.level = level if level in ("sticks", "full") else "full"
        return self.level

    def list_tools(self) -> list[dict]:
        if self.flight_level() == "full":
            return self.listing
        return [t for t in self.listing if t["name"] in STICKS_TOOL_NAMES]

    def current_instructions(self) -> str:
        return STICKS_INSTRUCTIONS if self.flight_level() == "sticks" else INSTRUCTIONS


def main(argv: list[str] | None = None) -> int:
    return run_main("anet-relay-mcp", "ANet Relay MCP server (stdio)", "mcp", Server, argv)


if __name__ == "__main__":
    raise SystemExit(main())
