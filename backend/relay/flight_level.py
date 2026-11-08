"""Agent flight level (06 §5, WP-1): what agent sessions may fly.

``agent_flight_level`` is ``sticks`` (the default) or ``full``; only the operator changes it. At ``sticks``
an agent session flies like a person with a radio: sticks (WebSocket stream, ``POST /sticks``, the
``sticks`` skill), arm / disarm, the manual modes (STABILIZE / ACRO, PX4 MANUAL / STABILIZED / ACRO; not
DRIFT, which damps velocity and holds the climb rate), fire, onboard programs, and the safety actions
(emergency stop; ``land`` is kept as a safety action and its event is tagged ``safety``). Everything that
flies for it is refused with 403 ``sticks_only`` (``detail.level = "sticks"``): the high-level skills, plans
containing them, switches to altitude, position or automatic modes, re-flying a plan or a path (or recorded
sticks in such modes), the high-level raw MAVLink commands of the device interface, RC overrides beyond
channels 1-4 (a mode switch channel) and switching a simulator's hover assist on (on a new simulator too):
it would hold the hover for the session. The operator is never limited.

The checks are plain functions called by the fleet service at each entry point (REST, WebSocket and the
MCP servers all reach these), so every surface enforces the same rule.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .authority import Actor
from .errors import err
from .mav import modes

LEVELS = ("sticks", "full")
STICKS_SKILLS = frozenset({"sticks", "land"})
SAFETY_SKILLS = frozenset({"land"})
STICKS_PLAN_ACTIONS = frozenset({"sticks", "land"})
# raw device commands that fly for the agent (device MCP / POST /vehicles/{id}/command)
FLIGHT_COMMANDS = frozenset(
    {
        "MAV_CMD_NAV_TAKEOFF",
        "MAV_CMD_NAV_RETURN_TO_LAUNCH",
        "MAV_CMD_NAV_LOITER_UNLIM",
        "MAV_CMD_NAV_LOITER_TIME",
        "MAV_CMD_NAV_WAYPOINT",
        "MAV_CMD_NAV_GUIDED_ENABLE",
        "MAV_CMD_DO_CHANGE_SPEED",
        "MAV_CMD_CONDITION_YAW",
        "MAV_CMD_DO_REPOSITION",
        "MAV_CMD_DO_SET_MODE",
        "MAV_CMD_MISSION_START",
        "MAV_CMD_DO_PAUSE_CONTINUE",
        "MAV_CMD_DO_ORBIT",
    }
)
STICK_CHANNELS = 4


def limited(settings: Any, actor: Actor) -> bool:
    """True when ``actor`` is an agent session and the level is ``sticks``."""
    return actor.kind == "session" and getattr(settings, "agent_flight_level", "sticks") != "full"


def _refuse(action: str) -> None:
    raise err("sticks_only", level="sticks", action=action)


def check_skill(settings: Any, actor: Actor, name: str) -> bool:
    """Raise for a refused skill; returns True when the skill runs as a safety action (``land``)."""
    if not limited(settings, actor):
        return False
    if name not in STICKS_SKILLS:
        _refuse(name)
    return name in SAFETY_SKILLS


def check_plan(settings: Any, actor: Actor, steps: Iterable[dict[str, Any]]) -> None:
    if not limited(settings, actor):
        return
    for step in steps:
        action = str(step.get("action"))
        if action not in STICKS_PLAN_ACTIONS:
            _refuse(f"plan step {action}")


# 06 §5: STABILIZE / MANUAL / ACRO (PX4 STABILIZED is its STABILIZE). ArduPilot DRIFT is classed manual for
# possession (it takes sticks) but assists: it damps velocity and holds the climb rate.
STICKS_MODES = frozenset({"STABILIZE", "ACRO", "MANUAL", "STABILIZED"})


def manual_mode(autopilot: str | None, mode: str) -> bool:
    return mode in STICKS_MODES and modes.mode_class(autopilot, mode) == "manual"


def check_sim_assist(settings: Any, actor: Actor, on: bool) -> None:
    """Hover assist holds the hover for the pilot: a session at ``sticks`` may switch it off, not on."""
    if on and limited(settings, actor):
        _refuse("hover assist")


def check_mode(settings: Any, actor: Actor, autopilot: str | None, mode: str) -> None:
    if limited(settings, actor) and not manual_mode(autopilot, mode):
        _refuse(f"mode {mode}")


def check_refly(
    settings: Any, actor: Actor, mode: str, autopilot: str | None, recorded_modes: Iterable[str]
) -> None:
    if not limited(settings, actor):
        return
    if mode != "sticks":
        _refuse(f"re-fly as {mode}")
    for name in recorded_modes:
        if not manual_mode(autopilot, name):
            _refuse(f"re-fly in mode {name}")


def check_command(settings: Any, actor: Actor, command_name: str) -> None:
    if limited(settings, actor) and command_name in FLIGHT_COMMANDS:
        _refuse(command_name)


def check_rc(settings: Any, actor: Actor, channels: dict[int, int], axes: dict[str, float]) -> None:
    """RC override at ``sticks``: channels 1-4 and the four stick axes only (an aux channel can be the
    autopilot's mode switch)."""
    if not limited(settings, actor):
        return
    for number in channels:
        if int(number) > STICK_CHANNELS:
            _refuse(f"RC channel {number}")
    for name in axes:
        if name not in ("roll", "pitch", "yaw", "throttle"):
            _refuse(f"RC axis {name}")


def view(settings: Any) -> dict[str, Any]:
    level = getattr(settings, "agent_flight_level", "sticks")
    return {
        "level": level,
        "session_skills": sorted(STICKS_SKILLS) if level == "sticks" else None,
        "safety_skills": sorted(SAFETY_SKILLS),
    }


__all__ = [
    "LEVELS",
    "check_command",
    "check_mode",
    "check_plan",
    "check_rc",
    "check_refly",
    "check_skill",
    "limited",
    "view",
]
