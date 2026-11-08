"""Onboard-program tools of the application MCP server (06 §6, WP-4b).

The same operations as Copilot's program tools and the SDK: write, check, test, judge and deploy flight
programs, tune them live, run duels and fit simulator airframes. Names follow the contract table
(``program_*``, ``challenge_*``, ``job_status``, ``duel_run``, ``ladder``, ``airframe_set``) plus
``program_guide``, which returns ``docs/programs-guide.md``, the guide Copilot carries in its prompt.

Program tools that act on an aircraft need control of it: they claim it for this server's session when
nobody holds it (a person who possesses it always wins). The judge routes (06 §3.4) answer
``judge_unavailable`` on a Relay that does not have them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from relay_mcp_common import VID, RelayClient, RelayError, Tool, schema_tool

GUIDE_PATH = Path(__file__).resolve().parent.parent / "docs" / "programs-guide.md"
MANUAL_MODE = {"px4": "STABILIZED"}  # anything else (ArduPilot): STABILIZE
MANUAL_MODES = frozenset({"STABILIZE", "ACRO", "DRIFT", "MANUAL", "STABILIZED"})
REVISIONS_SHOWN = 20

PROGRAM_INSTRUCTIONS = (
    "Onboard programs (06 §6): at the agent flight level sticks (the default) agents cannot use high-level "
    "flight; they write programs that fly the aircraft at 50 Hz from observations to sticks. Read "
    "program_guide first. Workflow: program_list / program_get (start from a built-in such as hover_trim), "
    "program_create or program_update (whole source or a line patch; every change is a revision with a "
    "note), "
    "program_validate, program_test, challenge_submit (headless judge) or program_deploy to a simulator "
    "(arm: true sets a manual mode and arms first; airframe_set chooses the simulator's defects), then "
    "program_status, program_log and program_params to tune it live, program_stop to end it. Program tools "
    "on "
    "an aircraft claim it for this server's session; if the anet-relay server holds the claim, release it "
    "there first. A person who takes over pauses the program; real aircraft accept programs only after the "
    "operator allows them."
)

PROGRAM = {"type": "string", "description": "Program id from program_list, e.g. hover_trim"}
REV = {"type": "integer", "minimum": 1, "description": "revision; omitted: the current one"}
OBJ = {"type": "object"}
TESTS = {
    "type": "array",
    "items": {"type": "object"},
    "description": "unit tests [{name, obs, mem?, state?, params?, expect: {roll|pitch|yaw|throttle: [lo, "
    "hi], "
    "fire, state}}]; missing obs fields hover level at 1 m",
}
AIRFRAME = {
    "type": "object",
    "description": "simulator defects {preset: ideal|typical|rough|custom, seed?} or {params: {mass_kg, "
    "payload_kg, cg_offset_cm, level_trim_deg, motor_gain, prop_damage, damaged_prop, imu_noise}}",
}
PRESET = {"type": "string", "enum": ["ideal", "typical", "rough", "custom"]}
SEED = {"type": "integer", "minimum": 0, "maximum": 2**31 - 1}
WAIT = {"type": "boolean", "default": True, "description": "wait for the job's result (default true)"}


def numbered(source: str) -> str:
    """The source with its line numbers in front ("12| code"); the numbers are not part of the code."""
    lines = str(source).split("\n")
    width = len(str(len(lines)))
    return "\n".join(f"{i:>{width}}| {line}" for i, line in enumerate(lines, 1))


def guide_text() -> str:
    try:
        return GUIDE_PATH.read_text("utf-8").strip()
    except OSError:
        return "The programs guide is docs/programs-guide.md in the ANet Relay repository."


def _summary(p: dict | None) -> dict | None:
    if not isinstance(p, dict):
        return p
    keys = ("id", "name", "builtin", "rev", "author", "description")
    out = {k: p.get(k) for k in keys if k in p}
    out["tests"] = p.get("test_count", len(p.get("tests") or []))
    return out


def claim(r: RelayClient, vehicle: str) -> dict:
    """Claim ``vehicle`` for this session unless a person flies it or another session holds it."""
    v = r.vehicle(vehicle)
    if v.get("possession"):
        raise RelayError(
            "possessed_by_human",
            409,
            f"The operator is flying {v.get('label') or vehicle}; ask them to release it.",
            detail={"possession": v["possession"]},
        )
    held = v.get("claim")
    if held and held.get("session_id") != r.session_id:
        raise RelayError(
            "claimed_by_other",
            409,
            f"{held.get('name')} holds {v.get('label') or vehicle}; release it there first.",
            detail={"claim": held},
        )
    if not held:
        r.claim(vehicle)
    return v


def _get(r: RelayClient, a: dict) -> dict:
    program = r.program_get(a["program"])
    revisions = r.program_revisions(a["program"])
    source, params, rev = program.get("source", ""), program.get("params"), program.get("rev")
    if a.get("rev") is not None and a["rev"] != rev:
        old = next((x for x in revisions if x.get("rev") == a["rev"]), None)
        if old is None:
            raise RelayError(
                "revision_not_found",
                404,
                f"{a['program']} has no revision {a['rev']}.",
                detail={"rev": a["rev"]},
            )
        source, params, rev = old.get("source", ""), old.get("params"), old["rev"]
    out: dict[str, Any] = {**(_summary(program) or {}), "rev": rev, "current_rev": program.get("rev")}
    if a.get("numbered") is False:
        out["source"] = source
    else:
        out["source_numbered"] = numbered(source)
        out["lines"] = len(str(source).split("\n"))
    out["params"] = params
    out["tests"] = program.get("tests")
    out["revisions"] = [
        {k: x.get(k) for k in ("rev", "author", "note", "t")}
        for x in sorted(revisions, key=lambda x: -int(x.get("rev") or 0))[:REVISIONS_SHOWN]
    ]
    return out


def _create(r: RelayClient, a: dict) -> dict:
    res = r.program_create(
        a["name"],
        source=a.get("source"),
        description=a.get("description"),
        params=a.get("params"),
        tests=a.get("tests"),
        note=a.get("note"),
        copy_of=a.get("copy_of"),
    )
    return {"program": _summary(res.get("program")), "validation": res.get("validation")}


def _update(r: RelayClient, a: dict) -> dict:
    res = r.program_update(
        a["program"],
        source=a.get("source"),
        patch=a.get("patch"),
        note=a.get("note"),
        name=a.get("name"),
        description=a.get("description"),
        params=a.get("params"),
        tests=a.get("tests"),
    )
    out = {"program": _summary(res.get("program")), "validation": res.get("validation")}
    if a.get("patch") is not None:
        out["source_numbered"] = numbered((res.get("program") or {}).get("source", ""))
    return out


def _deploy(r: RelayClient, a: dict) -> dict:
    v = claim(r, a["vehicle"])
    if a.get("arm"):
        mode = str(v.get("mode") or "").upper()
        if mode not in MANUAL_MODES:
            r.mode(a["vehicle"], MANUAL_MODE.get(str(v.get("autopilot") or "").lower(), "STABILIZE"))
        if not v.get("armed"):
            r.arm(a["vehicle"], confirm=a.get("confirm"))
    return r.program_deploy(
        a["vehicle"],
        a["program"],
        rev=a.get("rev"),
        params=a.get("params"),
        mode=a.get("mode") or "pilot",
        sensors=a.get("sensors"),
        arena=a.get("arena"),
        seed=a.get("seed"),
        airframe=a.get("airframe"),
    )


def _on_claimed(fn):
    def handler(r: RelayClient, a: dict) -> Any:
        claim(r, a["vehicle"])
        return fn(r, a)

    return handler


def _status(r: RelayClient, a: dict) -> dict:
    v = r.vehicle(a["vehicle"])
    sim = v.get("sim")
    return {
        "vehicle": v.get("id"),
        "program": v.get("program"),
        "programs_allowed": v.get("programs_allowed"),
        "controller": v.get("controller"),
        "armed": v.get("armed"),
        "landed": v.get("landed"),
        "mode": v.get("mode"),
        "altitude_m": v.get("altitude_m"),
        "possessed": bool(v.get("possession")),
        "sim": {"hover_assist": sim.get("hover_assist"), "airframe": sim.get("airframe")} if sim else None,
    }


def program_tools() -> list[Tool]:
    return [
        schema_tool(
            "program_guide",
            "How to write flight programs: program shape, Sticks, the observation fields, the relay library, "
            "language limits and the method (measure the hover throttle, integral trim, PID structure, state "
            "machines). Read it before writing a program.",
            read_only=True,
            handler=lambda r, a: {"guide": guide_text()},
        ),
        schema_tool(
            "program_list",
            "Flight programs: built-in examples (read-only; copy with program_create copy_of) and stored "
            "ones, "
            "with revision and test count.",
            read_only=True,
            handler=lambda r, a: {"programs": [_summary(p) for p in r.program_list()]},
        ),
        schema_tool(
            "program_get",
            "Read a program: source, PARAMS, unit tests and recent revisions. The source comes as "
            "source_numbered, each line prefixed with its number and '| ' for line patches (the prefixes are "
            "not part of the code); numbered false returns the plain source; rev reads an older revision.",
            {"program": PROGRAM, "rev": REV, "numbered": {"type": "boolean", "default": True}},
            ["program"],
            read_only=True,
            handler=_get,
        ),
        schema_tool(
            "program_create",
            "Create a program from source, or copy_of an existing or built-in one. Returns its id, revision "
            "1 "
            "and the validation result.",
            {
                "name": {"type": "string", "minLength": 1, "maxLength": 200},
                "description": {"type": "string"},
                "source": {"type": "string", "description": "the whole program (omitted with copy_of)"},
                "params": OBJ,
                "tests": TESTS,
                "note": {"type": "string"},
                "copy_of": {"type": "string"},
            },
            ["name"],
            handler=_create,
        ),
        schema_tool(
            "program_update",
            "Change a program; every change is a new revision with a note. Give source (the whole text) or "
            "patch: line edits [{start, end, text}], 1-based and inclusive, all numbered against the current "
            "revision (end = start - 1 inserts before start; empty text deletes). Returns the validation, "
            "and "
            "after a patch the new numbered source.",
            {
                "program": PROGRAM,
                "source": {"type": "string"},
                "patch": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "start": {"type": "integer", "minimum": 1},
                            "end": {"type": "integer", "minimum": 0},
                            "text": {"type": "string"},
                        },
                        "required": ["start", "end", "text"],
                    },
                },
                "note": {"type": "string", "description": "what changed and why"},
                "name": {"type": "string", "maxLength": 200},
                "description": {"type": "string"},
                "params": OBJ,
                "tests": TESTS,
            },
            ["program", "note"],
            handler=_update,
        ),
        schema_tool(
            "program_validate",
            "Check a program against the language rules without running it: every problem with line, column "
            "and reason, and the PARAMS found. source checks that text instead of the stored source.",
            {"program": PROGRAM, "source": {"type": "string"}},
            ["program"],
            read_only=True,
            handler=lambda r, a: r.program_validate(a["program"], a.get("source")),
        ),
        schema_tool(
            "program_test",
            "Run the program's unit tests (or the tests given) in the program host: output and pass or fail "
            "per test.",
            {"program": PROGRAM, "rev": REV, "source": {"type": "string"}, "tests": TESTS, "seed": SEED},
            ["program"],
            read_only=True,
            handler=lambda r, a: r.program_test(
                a["program"],
                rev=a.get("rev"),
                source=a.get("source"),
                tests=a.get("tests"),
                seed=a.get("seed"),
            ),
        ),
        schema_tool(
            "program_deploy",
            "Run a program revision on an aircraft at 50 Hz; it holds the sticks as PROGRAM until it is "
            "stopped. Claims the aircraft for this session. arm true first sets a manual mode (STABILIZE) "
            "and "
            "arms (a real aircraft needs confirm ARM, the operator's Allow programs switch and passed "
            "preflight). mode pilot or copilot. Simulators: sensors {baro, position none|uwb|gps, contacts}, "
            "arena {radius, floor, ceiling}, seed, airframe.",
            {
                "vehicle": VID,
                "program": PROGRAM,
                "rev": REV,
                "params": OBJ,
                "mode": {"type": "string", "enum": ["pilot", "copilot"], "default": "pilot"},
                "sensors": OBJ,
                "arena": OBJ,
                "seed": {"type": "integer", "minimum": 0},
                "airframe": AIRFRAME,
                "arm": {"type": "boolean", "default": False},
                "confirm": {"type": "string", "description": "ARM to arm a real aircraft"},
            },
            ["vehicle", "program"],
            destructive=True,
            handler=_deploy,
        ),
        schema_tool(
            "program_stop",
            "Stop the program on an aircraft; the sticks go back to the safety layer.",
            {"vehicle": VID},
            ["vehicle"],
            handler=_on_claimed(lambda r, a: r.program_stop(a["vehicle"])),
        ),
        schema_tool(
            "program_resume",
            "Resume a paused program (possession pauses it; releasing never resumes it by itself).",
            {"vehicle": VID},
            ["vehicle"],
            destructive=True,
            handler=_on_claimed(lambda r, a: r.program_resume(a["vehicle"])),
        ),
        schema_tool(
            "program_params",
            "Change parameters of the running program live: params {name: value}; type and range follow its "
            "PARAMS defaults and PARAM_RANGES.",
            {"vehicle": VID, "params": {"type": "object", "minProperties": 1}},
            ["vehicle", "params"],
            handler=_on_claimed(lambda r, a: r.program_params(a["vehicle"], a["params"])),
        ),
        schema_tool(
            "program_status",
            "The program on an aircraft (state, status, frame rate, faults, last error, mode, live params) "
            "and "
            "the aircraft's armed state, mode, height and controller.",
            {"vehicle": VID},
            ["vehicle"],
            read_only=True,
            handler=_status,
        ),
        schema_tool(
            "program_log",
            "Lines the program logged (ctx.log, print), its errors and events; after: the last seq read.",
            {
                "vehicle": VID,
                "after": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
            },
            ["vehicle"],
            read_only=True,
            handler=lambda r, a: r.program_log(
                a["vehicle"], int(a.get("after") or 0), int(a.get("limit") or 100)
            ),
        ),
        schema_tool(
            "challenge_list",
            "Flight challenges (liftoff, hover, heading, square, rings, pursuit, duels) with scenario, pass "
            "condition, best score and status.",
            read_only=True,
            handler=lambda r, a: r.challenge_list(),
        ),
        schema_tool(
            "challenge_submit",
            "Judge a program revision on a challenge in the headless evaluator (faster than real time; "
            "visible "
            "and hidden seeds with other airframes). wait (default) returns the score 0-100, passed, metrics "
            "per seed and the trace id.",
            {
                "challenge": {"type": "string"},
                "program": PROGRAM,
                "rev": REV,
                "wait": WAIT,
                "timeout_s": {"type": "number", "minimum": 1, "maximum": 600, "default": 180},
            },
            ["challenge", "program"],
            handler=lambda r, a: r.challenge_submit(
                a["challenge"],
                a["program"],
                rev=a.get("rev"),
                wait=a.get("wait", True),
                timeout=float(a.get("timeout_s") or 180),
            ),
        ),
        schema_tool(
            "job_status",
            "State, progress and result of an evaluation job (challenge submission or duel).",
            {"job": {"type": "string"}},
            ["job"],
            read_only=True,
            handler=lambda r, a: r.job_status(a["job"]),
        ),
        schema_tool(
            "duel_run",
            "Pit 2 to 8 programs against each other for some rounds in the headless evaluator: each round's "
            "winner, hits and survival times and the Elo table.",
            {
                "programs": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 8},
                "rounds": {"type": "integer", "minimum": 1, "maximum": 20},
                "seed": {"type": "integer", "minimum": 0},
                "scenario": OBJ,
                "wait": WAIT,
                "timeout_s": {"type": "number", "minimum": 1, "maximum": 600, "default": 300},
            },
            ["programs"],
            handler=lambda r, a: r.duel_run(
                a["programs"],
                rounds=a.get("rounds"),
                seed=a.get("seed"),
                scenario=a.get("scenario"),
                wait=a.get("wait", True),
                timeout=float(a.get("timeout_s") or 300),
            ),
        ),
        schema_tool(
            "ladder",
            "The Elo ladder of programs from all duels so far.",
            read_only=True,
            handler=lambda r, a: r.ladder(),
        ),
        schema_tool(
            "airframe_set",
            "Fit assembly defects to a simulator (they act with hover assist off): preset ideal, typical or "
            "rough with a seed, or params for a custom airframe. Returns the airframe and its hover "
            "throttle; "
            "sim_only for a real aircraft.",
            {"vehicle": VID, "preset": PRESET, "params": OBJ, "seed": SEED},
            ["vehicle"],
            handler=lambda r, a: r.airframe_set(
                a["vehicle"], preset=a.get("preset"), params=a.get("params"), seed=a.get("seed")
            ),
        ),
    ]


PROGRAM_TOOL_NAMES = tuple(t.name for t in program_tools())

__all__ = ["PROGRAM_INSTRUCTIONS", "PROGRAM_TOOL_NAMES", "claim", "guide_text", "numbered", "program_tools"]
