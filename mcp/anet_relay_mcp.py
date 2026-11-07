"""ANet Relay MCP server (03 §10.7): stdio, JSON-RPC 2.0, standard library only.

    python mcp/anet_relay_mcp.py [--name mcp] [--base http://127.0.0.1:8765]

The server opens one agent session when it starts (named by ``--name``) and renews the lease in the
background; the session closes when the client closes stdin. Tools cover reading the fleet, claiming
aircraft and the high-level skills. There is deliberately no stick tool: a language model cannot fly
a 50 Hz control loop. Protocol messages go to stdout, one JSON object per line; logs go to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

try:
    from anet_relay_client import DEFAULT_BASE, RelayClient, RelayError
except ImportError:  # running from the repository checkout
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sdk" / "python"))
    from anet_relay_client import DEFAULT_BASE, RelayClient, RelayError

SERVER_NAME = "anet-relay"
SERVER_VERSION = "0.2.0"
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = (
    "ANet Relay controls small quadcopters (real and simulated) on the local network. "
    "Positions are metres in an East-North-Up frame (x east, y north, z up); headings are compass degrees. "
    "Workflow: relay_fleet to see aircraft, relay_claim one, then relay_takeoff, relay_hold, relay_move, "
    "relay_yaw and relay_land, or relay_plan for a whole sequence. relay_move and relay_yaw work without a "
    "position estimate (moves are then open-loop estimates); relay_goto needs one. "
    "A person can take over (possess) any aircraft at any time; control calls then fail with "
    "possessed_by_human and the claim resumes automatically when they hand it back (event relay_handback). "
    "Real aircraft need confirm='ARM' to arm and must pass preflight checks. goto targets must stay inside "
    "the safety volume reported by relay_fleet. Use relay_stop only in an emergency: it cuts the motors."
)

VID = {"type": "string", "description": "Vehicle id from relay_fleet, e.g. sim-1 or real-10-7-7-36"}
WAIT = {"type": "boolean", "description": "Wait until the skill finishes (default true)", "default": True}


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


class Server:
    def __init__(self, base: str, name: str, out=None) -> None:
        self.relay = RelayClient(base)
        self.name = name
        self.out = out or sys.stdout
        self.write_lock = threading.Lock()
        self.session_lock = threading.Lock()

    # ------------------------------------------------------------------ session

    def ensure_session(self, reopen: bool = False) -> None:
        with self.session_lock:
            if self.relay.session_id and not reopen:
                return
            self.relay.close()
            self.relay.open_session(self.name)
            _log(f"opened session {self.relay.session_id} as {self.name}")

    def call_tool(self, name: str, args: dict) -> Any:
        self.ensure_session()
        try:
            return self._dispatch(name, args)
        except RelayError as exc:
            if exc.code in ("session_expired", "session_required", "auth_required"):
                self.ensure_session(reopen=True)
                return self._dispatch(name, args)
            raise

    def _dispatch(self, name: str, a: dict) -> Any:
        r = self.relay
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
            events = batch["events"]
            return {
                "last_seq": batch["last_seq"],
                "events": [
                    {k: e[k] for k in ("seq", "t", "kind", "vehicle", "text_en", "data")} for e in events
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
            return {
                "skill": r.takeoff(vid, alt=float(a.get("alt", 1.0)), wait=wait, confirm=a.get("confirm"))
            }
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
            return {"plan": r.plan(vid, a["steps"], name=self.name, confirm=a.get("confirm"))}
        if name == "relay_goto":
            return {
                "skill": r.goto(vid, float(a["x"]), float(a["y"]), float(a["z"]), yaw=a.get("yaw"), wait=wait)
            }
        if name == "relay_stop":
            return r.stop(vid)
        if name == "relay_course_start":
            return r.course_start(vid, a.get("course_id") or "cage-loop")
        raise KeyError(name)

    # ------------------------------------------------------------------ JSON-RPC

    def send(self, message: dict) -> None:
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self.write_lock:
            self.out.write(line + "\n")
            self.out.flush()

    def reply(self, rid: Any, result: Any = None, error: tuple[int, str] | None = None) -> None:
        if error is not None:
            self.send({"jsonrpc": "2.0", "id": rid, "error": {"code": error[0], "message": error[1]}})
        else:
            self.send({"jsonrpc": "2.0", "id": rid, "result": result})

    def handle_line(self, line: str) -> threading.Thread | None:
        line = line.strip()
        if not line:
            return None
        try:
            msg = json.loads(line)
        except ValueError:
            self.reply(None, error=(-32700, "Parse error"))
            return None
        if isinstance(msg, list):  # batches (2025-03-26) are answered one by one
            for item in msg:
                self.handle_message(item)
            return None
        return self.handle_message(msg)

    def handle_message(self, msg: Any) -> threading.Thread | None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            if isinstance(msg, dict) and ("result" in msg or "error" in msg):
                return None  # a response to a request we never send
            self.reply(msg.get("id") if isinstance(msg, dict) else None, error=(-32600, "Invalid Request"))
            return None
        method, rid, params = msg["method"], msg.get("id"), msg.get("params") or {}
        if rid is None:  # notification: initialized, cancelled, ...
            return None
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            self.reply(
                rid,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "title": "ANet Relay", "version": SERVER_VERSION},
                    "instructions": INSTRUCTIONS,
                },
            )
        elif method == "ping":
            self.reply(rid, {})
        elif method == "tools/list":
            self.reply(rid, {"tools": TOOLS})
        elif method == "tools/call":
            name = params.get("name")
            if name not in TOOL_NAMES:
                self.reply(rid, error=(-32602, f"Unknown tool: {name}"))
                return None
            args = params.get("arguments") or {}
            if not isinstance(args, dict):
                self.reply(rid, error=(-32602, "arguments must be an object"))
                return None
            worker = threading.Thread(target=self._run_tool, args=(rid, name, args), daemon=True)
            worker.start()  # long skills must not block ping or other calls
            return worker
        elif method in ("resources/list", "prompts/list"):
            self.reply(rid, {method.split("/")[0]: []})
        else:
            self.reply(rid, error=(-32601, f"Method not found: {method}"))
        return None

    def _run_tool(self, rid: Any, name: str, args: dict) -> None:
        try:
            result = self.call_tool(name, args)
            text = json.dumps(result, ensure_ascii=False, indent=1)
            self.reply(rid, {"content": [{"type": "text", "text": text}], "isError": False})
        except RelayError as exc:
            body = {"error": exc.code, "message": exc.message}
            if exc.message_zh:
                body["message_zh"] = exc.message_zh
            if exc.detail:
                body["detail"] = exc.detail
            self.reply(
                rid,
                {
                    "content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}],
                    "isError": True,
                },
            )
        except (KeyError, TypeError, ValueError) as exc:
            self.reply(
                rid, {"content": [{"type": "text", "text": f"invalid arguments: {exc}"}], "isError": True}
            )
        except Exception:  # pragma: no cover - keep the server alive
            _log(traceback.format_exc())
            self.reply(
                rid,
                {"content": [{"type": "text", "text": "internal error in the MCP server"}], "isError": True},
            )

    def serve(self, stream=None) -> None:
        stream = stream or sys.stdin
        try:
            self.ensure_session()
        except RelayError as exc:
            _log(f"Relay not reachable yet ({exc.message}); the session opens on the first tool call")
        workers: list[threading.Thread] = []
        for line in stream:
            worker = self.handle_line(line)
            if worker is not None:
                workers.append(worker)
        for worker in workers:
            worker.join(timeout=120)
        self.relay.close()


def _log(text: str) -> None:
    sys.stderr.write(f"anet-relay-mcp: {text}\n")
    sys.stderr.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="anet-relay-mcp", description="ANet Relay MCP server (stdio)")
    parser.add_argument("--name", default="mcp", help="agent session name shown in Relay (default mcp)")
    parser.add_argument(
        "--base", default=os.environ.get("RELAY_API", DEFAULT_BASE), help="Relay API base URL"
    )
    args = parser.parse_args(argv)
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    Server(args.base, args.name).serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
