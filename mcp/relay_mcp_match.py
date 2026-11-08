"""Dogfight and leaderboard tools of the app MCP server (07 §3-§4, WP-B).

The same operations, names and parameters as Copilot's tools and the SDK: ``dogfight_start``,
``dogfight_stop``, ``dogfight_status`` and ``leaderboard``. Starting and stopping a dogfight are app
operations (they need agents_app_control); the programs that fly it use sticks only, and a person who
possesses an aircraft always wins.
"""

from __future__ import annotations

from relay_mcp_common import Tool, schema_tool

SORTS = ["downs", "survival", "accuracy", "rams"]

MATCH_INSTRUCTIONS = (
    " Dogfights (07 §3): dogfight_start sets up a timed fight of simulated aircraft in one call (it adds the "
    "simulators, arms them and deploys a program per role: gunners fire, rammers ram and never fire; the "
    "programs fly with sticks only), so do not arm or deploy anything first. For example 'a six-aircraft "
    "free-for-all, four shooting and two ramming' is dogfight_start {aircraft: 6, teams: 'ffa', roles: "
    "{gunner: 4, rammer: 2}}. dogfight_status shows the phase and every aircraft's state, dogfight_stop ends it "
    "early, leaderboard ranks every aircraft of this terminal and its LAN peers by downs, survival, accuracy "
    "or rams. Simulators only: a real aircraft id is refused."
)

DOGFIGHT_PROPS = {
    "aircraft": {"type": "integer", "minimum": 2, "maximum": 12, "description": "simulators (Relay runs 8)"},
    "teams": {"enum": ["ffa", 2, 3], "description": "ffa (default), or 2 or 3 teams"},
    "roles": {
        "type": "object",
        "properties": {
            "gunner": {"type": "integer", "minimum": 0, "maximum": 12},
            "rammer": {"type": "integer", "minimum": 0, "maximum": 12},
        },
        "required": ["gunner", "rammer"],
        "additionalProperties": False,
        "description": "gunner + rammer = aircraft; default two gunners to one rammer",
    },
    "programs": {
        "type": "object",
        "properties": {"gunner": {"type": "string"}, "rammer": {"type": "string"}},
        "additionalProperties": False,
        "description": "program id per role (default hunter_medium and rammer)",
    },
    "arena": {
        "type": "object",
        "properties": {
            "radius": {"type": "number", "minimum": 3, "maximum": 30},
            "ceiling": {"type": "number", "minimum": 2, "maximum": 10},
        },
        "additionalProperties": False,
    },
    "airframe": {"type": "string", "enum": ["ideal", "typical", "rough"]},
    "hover_assist": {"type": "boolean", "description": "default false: physical flight"},
    "duration_s": {"type": "number", "minimum": 30, "maximum": 1800, "description": "default 180"},
    "respawn": {"type": "boolean", "description": "back at the spawn 5 s after going down (default true)"},
    "seed": {"type": "integer", "minimum": 0},
    "vehicles": {"type": "array", "items": {"type": "string"}, "description": "simulators to use first"},
}


def match_tools() -> list[Tool]:
    return [
        schema_tool(
            "dogfight_start",
            "Start a timed dogfight of simulated aircraft: spawn ring facing the centre, a program per role "
            "(gunners fire, rammers ram), armed by Relay, flown by the programs with sticks only; on the LAN "
            "it joins the shared match, otherwise it runs as local practice. App operation.",
            DOGFIGHT_PROPS,
            handler=lambda r, a: r.dogfight_start(**a),
        ),
        schema_tool(
            "dogfight_stop",
            "End the running dogfight: programs stop, airborne aircraft land, the standings stay. App "
            "operation.",
            handler=lambda r, a: r.dogfight_stop(),
        ),
        schema_tool(
            "dogfight_status",
            "The dogfight: running, phase, times, configuration, every aircraft's role, program, team and "
            "state, and the match.",
            read_only=True,
            handler=lambda r, a: r.dogfight_status(),
        ),
        schema_tool(
            "leaderboard",
            "The arena leaderboard of this terminal and its LAN peers, one row per aircraft: controller, "
            "downs, hits, shots, accuracy, rams, deaths, crashes and survival times, sorted by downs "
            "(default), survival, accuracy or rams. A Relay 0.4 peer's rows have no statistics.",
            {"by": {"type": "string", "enum": SORTS, "default": "downs"}},
            read_only=True,
            handler=lambda r, a: r.leaderboard(a.get("by") or "downs"),
        ),
    ]


MATCH_TOOL_NAMES = tuple(t.name for t in match_tools())

__all__ = ["MATCH_INSTRUCTIONS", "MATCH_TOOL_NAMES", "match_tools"]
