"""Flight plans: an ordered list of skills run one after another (03 §5.8).

A plan is a convenience on top of the skill executor: each step starts the next
skill when the previous one finishes. A failed or superseded step fails the
plan; control changes stop the running skill and therefore the plan as well.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .errors import err

MAX_STEPS = 24
ACTIONS = ("takeoff", "hover", "hold", "move", "yaw", "land", "goto", "rtl", "sticks", "attitude", "velocity")

# 12 §4: speed-limited tracking of take-off, move and goto
_TRACK = ("speed", "settle_s")
# 12 §3: text parameters and their values (the control frame of a step and the velocity reference)
_CHOICES = {"frame": ("flu", "frd", "relay"), "reference": ("body", "local")}

# action -> (skill name, allowed parameters)
_SKILL = {
    "takeoff": ("takeoff", {"alt", *_TRACK}),
    "hover": ("hold", {"seconds"}),
    "hold": ("hold", {"seconds"}),
    "move": ("move", {"forward", "right", "left", "up", "down", "x", "y", "z", "frame", *_TRACK}),
    "yaw": ("yaw", {"deg", "frame"}),
    "land": ("land", set()),
    "goto": ("goto", {"x", "y", "z", "yaw", "frame", *_TRACK}),
    "rtl": ("rtl", set()),
    "sticks": ("sticks", {"roll", "pitch", "yaw", "throttle", "seconds"}),
    "attitude": ("attitude", {"roll_deg", "pitch_deg", "yaw_rate_dps", "thrust", "seconds"}),
    "velocity": ("velocity", {"vx", "vy", "vz", "yaw_rate_dps", "seconds", "frame", "reference"}),
}

_RANGES = {
    "alt": (0.2, 10.0),
    "seconds": (0.0, 120.0),
    "forward": (-5.0, 5.0),
    "right": (-5.0, 5.0),
    "up": (-2.0, 2.0),
    "left": (-5.0, 5.0),
    "down": (-2.0, 2.0),
    "speed": (0.1, 5.0),
    "settle_s": (0.0, 10.0),
    "deg": (-360.0, 360.0),
    "x": (-1e4, 1e4),
    "y": (-1e4, 1e4),
    "z": (-1e3, 1e3),
    "yaw": (-360.0, 360.0),
    "roll_deg": (-30.0, 30.0),
    "pitch_deg": (-30.0, 30.0),
    "yaw_rate_dps": (-90.0, 90.0),
    "thrust": (0.0, 1.0),
    "vx": (-2.0, 2.0),
    "vy": (-2.0, 2.0),
    "vz": (-2.0, 2.0),
}
# Parameters whose range differs from the shared table for one action.
_ACTION_RANGES = {
    "sticks": {"roll": (-1.0, 1.0), "pitch": (-1.0, 1.0), "yaw": (-1.0, 1.0), "throttle": (0.0, 1.0)},
    "move": {"x": (-5.0, 5.0), "y": (-5.0, 5.0), "z": (-5.0, 5.0)},  # body offsets in the step's frame
}
_SECONDS = {"sticks": (0.05, 10.0), "attitude": (0.05, 10.0), "velocity": (0.05, 10.0)}
_REQUIRED = {
    "sticks": ("throttle", "seconds"),
    "attitude": ("seconds",),
    "velocity": ("seconds",),
}

DEFAULTS = {"takeoff": {"alt": 1.0}, "hover": {"seconds": 3.0}}

# Throttle tuning of the stick skills (aircraft without a position estimate fly on the throttle stick in
# altitude hold): ``climb`` is the stick that lifts the aircraft off and climbs, ``hover`` the stick that
# holds its height (0.5 where the autopilot's altitude hold is trimmed). Adjustable while the plan flies.
TUNING_RANGES = {"climb": (0.55, 1.0), "hover": (0.35, 0.65)}


def normalise_tuning(raw: Any) -> dict[str, float]:
    """Validate ``{climb?, hover?}``; missing keys are left out (the skill defaults apply)."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise err("invalid_request", reason="tuning must be an object")
    extra = set(raw) - set(TUNING_RANGES)
    if extra:
        raise err("invalid_request", reason=f"tuning: unknown field {sorted(extra)[0]}")
    out: dict[str, float] = {}
    for key, (low, high) in TUNING_RANGES.items():
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise err("invalid_request", reason=f"tuning: {key} must be a number")
        if not low <= float(value) <= high:
            raise err("invalid_request", reason=f"tuning: {key} must be {low:g} to {high:g}")
        out[key] = float(value)
    hover = out.get("hover", 0.5)
    if "climb" in out and out["climb"] < hover + 0.05:
        raise err("invalid_request", reason="tuning: climb must be at least 0.05 above hover")
    return out


def normalise_steps(steps: Any, max_steps: int = MAX_STEPS) -> list[dict[str, Any]]:
    """Validate a plan's steps and return them as ``{"action": ..., **params}`` dicts."""
    if not isinstance(steps, list) or not steps:
        raise err("invalid_request", reason="a plan needs at least one step")
    if len(steps) > max_steps:
        raise err("invalid_request", reason=f"a plan has at most {max_steps} steps")
    out = []
    for i, raw in enumerate(steps, 1):
        if not isinstance(raw, dict):
            raise err("invalid_request", reason=f"step {i} must be an object")
        action = raw.get("action")
        if action not in _SKILL:
            raise err("invalid_request", reason=f"step {i}: action must be one of {', '.join(ACTIONS)}")
        allowed = _SKILL[action][1]
        extra = set(raw) - allowed - {"action"}
        if extra:
            raise err("invalid_request", reason=f"step {i} ({action}): unknown field {sorted(extra)[0]}")
        step: dict[str, Any] = {"action": action, **DEFAULTS.get(action, {})}
        for key in allowed:
            if key not in raw or raw[key] is None:
                continue
            value = raw[key]
            if key in _CHOICES:
                if value not in _CHOICES[key]:
                    choices = " or ".join(_CHOICES[key])
                    raise err("invalid_request", reason=f"step {i} ({action}): {key} must be {choices}")
                step[key] = value
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise err("invalid_request", reason=f"step {i} ({action}): {key} must be a number")
            low, high = _ACTION_RANGES.get(action, {}).get(key) or (
                _SECONDS[action] if key == "seconds" and action in _SECONDS else _RANGES[key]
            )
            if not low <= float(value) <= high:
                raise err("invalid_request", reason=f"step {i} ({action}): {key} must be {low:g} to {high:g}")
            step[key] = float(value)
        if action == "move" and not any(
            step.get(k) for k in ("forward", "right", "left", "up", "down", "x", "y", "z")
        ):
            raise err("invalid_request", reason=f"step {i} (move): give a direction or x, y, z")
        for key in _REQUIRED.get(action, ()):
            if key not in step:
                raise err("invalid_request", reason=f"step {i} ({action}): {key} is required")
        if action == "yaw" and "deg" not in step:
            raise err("invalid_request", reason=f"step {i} (yaw): deg is required")
        if action == "goto" and not all(k in step for k in ("x", "y", "z")):
            raise err("invalid_request", reason=f"step {i} (goto): x, y and z are required")
        out.append(step)
    return out


# Ready-made plans (12 §4). ``du7_box`` is the route of the ANetDU7 onboard test node
# (vehicles/anet-du7/tests/u7_mavros_simple.py): 2 m up, 1 m forward and back, 1 m left and back, land. The
# directions are named, so it reads the same in every control frame.
BUILTIN: dict[str, dict[str, Any]] = {
    "du7_box": {
        "title": "ANetDU7 box test",
        "title_zh": "ANetDU7 方框测试",
        "description": "Take off to 2 m, 1 m forward and back, 1 m left and back, land; 0.5 m/s.",
        "description_zh": "起飞到 2 米，前进 1 米并返回，左移 1 米并返回，降落；限速 0.5 m/s。",
        "steps": [
            {"action": "takeoff", "alt": 2.0, "speed": 0.5},
            {"action": "move", "forward": 1.0, "speed": 0.5},
            {"action": "move", "forward": -1.0, "speed": 0.5},
            {"action": "move", "left": 1.0, "speed": 0.5},
            {"action": "move", "left": -1.0, "speed": 0.5},
            {"action": "land"},
        ],
    },
}


def native(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``steps`` with ``frame: "relay"`` on every step that takes a frame and names none: plans built
    inside Relay (bench procedures, re-flies of records) are in Relay's own axes (12 §3)."""
    out = []
    for step in steps:
        step = dict(step)
        action = step.get("action")
        if action in _SKILL and "frame" in _SKILL[action][1] and step.get("frame") is None:
            step["frame"] = "relay"
        out.append(step)
    return out


def builtin(name: Any) -> dict[str, Any]:
    plan = BUILTIN.get(name) if isinstance(name, str) else None
    if plan is None:
        raise err("invalid_request", reason=f"builtin must be one of {', '.join(BUILTIN)}")
    return plan


def skill_for(step: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    name = _SKILL[step["action"]][0]
    return name, {k: v for k, v in step.items() if k != "action"}


@dataclass
class Plan:
    id: int
    steps: list[dict[str, Any]]
    actor: Any
    started: float
    name: str | None = None
    index: int = 0
    state: str = "running"  # running | done | failed | cancelled
    skill_id: int | None = None
    detail: str | None = None
    detail_zh: str | None = None
    finished: float | None = None
    results: list[str | None] = field(default_factory=list)
    # shared with the plan's skills, so a change applies to the step that is flying (normalise_tuning)
    tuning: dict[str, float] = field(default_factory=dict)

    @property
    def running(self) -> bool:
        return self.state == "running"

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state,
            "index": self.index,
            "count": len(self.steps),
            "steps": self.steps,
            "results": self.results,
            "detail": self.detail,
            "tuning": dict(self.tuning),
        }
