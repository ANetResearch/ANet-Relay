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
ACTIONS = ("takeoff", "hover", "hold", "move", "yaw", "land", "goto", "rtl")

# action -> (skill name, allowed parameters)
_SKILL = {
    "takeoff": ("takeoff", {"alt"}),
    "hover": ("hold", {"seconds"}),
    "hold": ("hold", {"seconds"}),
    "move": ("move", {"forward", "right", "up"}),
    "yaw": ("yaw", {"deg"}),
    "land": ("land", set()),
    "goto": ("goto", {"x", "y", "z", "yaw"}),
    "rtl": ("rtl", set()),
}

_RANGES = {
    "alt": (0.2, 10.0),
    "seconds": (0.0, 120.0),
    "forward": (-5.0, 5.0),
    "right": (-5.0, 5.0),
    "up": (-2.0, 2.0),
    "deg": (-360.0, 360.0),
    "x": (-1e4, 1e4),
    "y": (-1e4, 1e4),
    "z": (-1e3, 1e3),
    "yaw": (-360.0, 360.0),
}

DEFAULTS = {"takeoff": {"alt": 1.0}, "hover": {"seconds": 3.0}}


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
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise err("invalid_request", reason=f"step {i} ({action}): {key} must be a number")
            low, high = _RANGES[key]
            if not low <= float(value) <= high:
                raise err("invalid_request", reason=f"step {i} ({action}): {key} must be {low:g} to {high:g}")
            step[key] = float(value)
        if action == "move" and not any(step.get(k) for k in ("forward", "right", "up")):
            raise err("invalid_request", reason=f"step {i} (move): give forward, right or up")
        if action == "yaw" and "deg" not in step:
            raise err("invalid_request", reason=f"step {i} (yaw): deg is required")
        if action == "goto" and not all(k in step for k in ("x", "y", "z")):
            raise err("invalid_request", reason=f"step {i} (goto): x, y and z are required")
        out.append(step)
    return out


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
        }
