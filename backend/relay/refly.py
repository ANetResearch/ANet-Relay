"""Re-fly a recorded flight: derive plan, path and stick options from a record (04 §4).

Pure functions over the ``History.session_detail`` dict; the fleet applies them to a vehicle
(``FleetService.refly``). Three ways to fly a record again:

* ``plan``   the plan or skills the record shows, step by step;
* ``path``   the flown track as ``goto`` waypoints every 0.3 m, translated to the target's position;
* ``sticks`` the recorded sticks on the original time line (the ``replay`` skill).
"""

from __future__ import annotations

import itertools
import math
from collections import Counter
from typing import Any

from . import plans
from .errors import err
from .mav import modes

PATH_SPACING_M = 0.3
MAX_STEPS = 200  # a path plan is longer than the 24 steps of a hand-written one
GROUND_ALT_M = 0.3  # a first sample below this height means the record starts on the ground
MIN_STICK_ROWS = 3

# skill_started name -> plan action; the parameters are copied as far as the action takes them
_SKILL_ACTION = {
    "takeoff": "takeoff",
    "hold": "hover",
    "move": "move",
    "yaw": "yaw",
    "land": "land",
    "goto": "goto",
    "rtl": "rtl",
    "sticks": "sticks",
    "attitude": "attitude",
    "velocity": "velocity",
}


def _armed_rows(detail: dict[str, Any]) -> list[int]:
    cols = detail["samples"]
    return [i for i, armed in enumerate(cols["armed"]) if armed]


def plan_steps(
    detail: dict[str, Any], offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
) -> list[dict] | None:
    """Steps of the plans started in the record, else the skills in the order they started."""
    steps: list[dict[str, Any]] = []
    for event in detail["events"]:
        if event["kind"] == "plan_started":
            steps.extend(dict(s) for s in event["data"].get("steps") or [])
    if not steps:
        for event in detail["events"]:
            if event["kind"] != "skill_started":
                continue
            data = event["data"]
            action = _SKILL_ACTION.get(data.get("skill"))
            if action is None:
                continue
            params = {k: v for k, v in (data.get("params") or {}).items() if k != "confirm"}
            steps.append({"action": action, **params})
    if not steps:
        return None
    for step in steps:
        if step.get("action") == "goto":
            for axis, delta in zip("xyz", offset, strict=True):
                if isinstance(step.get(axis), (int, float)):
                    step[axis] = step[axis] + delta
    try:
        return plans.normalise_steps(_strip(steps), MAX_STEPS)
    except Exception:  # a record the plan rules no longer accept offers no plan
        return None


def _strip(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop parameters the plan action does not take (``distance`` and the like)."""
    out = []
    for step in steps:
        action = step.get("action")
        if action not in plans.ACTIONS:
            continue
        allowed = plans._SKILL[action][1] | {"action"}
        out.append({k: v for k, v in step.items() if k in allowed and v is not None})
    return out


def track(detail: dict[str, Any]) -> list[tuple[float, float, float, float, float | None]]:
    """Armed samples with a local position: ``(t, x, y, z, alt)``."""
    cols = detail["samples"]
    out = []
    for i in _armed_rows(detail):
        if cols["x"][i] is None or cols["y"][i] is None or cols["z"][i] is None:
            continue
        out.append((cols["t"][i], cols["x"][i], cols["y"][i], cols["z"][i], cols["alt"][i]))
    return out


def path_waypoints(detail: dict[str, Any], spacing: float = PATH_SPACING_M) -> dict[str, Any] | None:
    """The flown track relative to its first point, thinned to ``spacing`` metres."""
    points = track(detail)
    if len(points) < 2:
        return None
    t0, x0, y0, z0, alt0 = points[0]
    rel = [(p[1] - x0, p[2] - y0, p[3] - z0) for p in points]
    total = sum(math.dist(a, b) for a, b in itertools.pairwise(rel))
    spacing = max(spacing, total / (MAX_STEPS - 4))
    kept = [rel[0]]
    for p in rel[1:]:
        if math.dist(p, kept[-1]) >= spacing:
            kept.append(p)
    if kept[-1] != rel[-1] and math.dist(rel[-1], kept[-1]) > 0.05:
        kept.append(rel[-1])
    if len(kept) < 2:
        return None
    on_ground = (alt0 if alt0 is not None else 0.0) < GROUND_ALT_M
    return {
        "waypoints": [{"x": round(x, 3), "y": round(y, 3), "z": round(z, 3)} for x, y, z in kept],
        "duration_s": round(points[-1][0] - t0, 1),
        "from_ground": on_ground,
        "to_ground": rel[-1][2] < GROUND_ALT_M,
    }


def path_steps(
    path: dict[str, Any],
    base: tuple[float, float, float],
    airborne: bool,
    z_limits: tuple[float, float],
) -> list[dict[str, Any]]:
    """Plan steps for ``path`` translated to ``base`` (the target's current position).

    ``z_limits`` is the allowed world height range (safety volume); heights are clamped into it.
    """
    from_ground = path["from_ground"]
    if from_ground and airborne:
        raise err(
            "refly_unavailable",
            mode="path",
            reason="the record starts on the ground and the aircraft is airborne",
        )
    if not from_ground and not airborne:
        raise err(
            "refly_unavailable",
            mode="path",
            reason="the record starts in the air and the aircraft is on the ground",
        )
    low, high = z_limits
    waypoints = [(w["x"], w["y"], w["z"]) for w in path["waypoints"]]
    steps: list[dict[str, Any]] = []
    if from_ground:
        air = [w for w in waypoints if w[2] >= GROUND_ALT_M]
        if not air:
            raise err("refly_unavailable", mode="path", reason="the record never leaves the ground")
        steps.append(
            {"action": "takeoff", "alt": round(min(max(air[0][2], 0.5), max(0.5, high - base[2])), 2)}
        )
        waypoints = air
    for x, y, z in waypoints:
        steps.append(
            {
                "action": "goto",
                "x": round(base[0] + x, 3),
                "y": round(base[1] + y, 3),
                "z": round(min(max(base[2] + z, low), high), 3),
            }
        )
    if path["to_ground"]:
        steps.append({"action": "land"})
    return plans.normalise_steps(steps, MAX_STEPS)


def stick_rows(detail: dict[str, Any]) -> dict[str, Any] | None:
    """Recorded sticks from arming to disarming: rows ``(t, roll, pitch, yaw, throttle)`` from t = 0."""
    cols = detail["samples"]
    armed = _armed_rows(detail)
    if not armed:
        return None
    rows = []
    kept_modes: list[str] = []
    for i in range(armed[0], armed[-1] + 1):
        if cols["s_roll"][i] is None or cols["s_throttle"][i] is None:
            continue
        rows.append(
            (cols["t"][i], cols["s_roll"][i], cols["s_pitch"][i], cols["s_yaw"][i], cols["s_throttle"][i])
        )
        if cols["mode"][i]:
            kept_modes.append(cols["mode"][i])
    if len(rows) < MIN_STICK_ROWS or not kept_modes:
        return None
    rows_mode0 = kept_modes[0]
    mode = Counter(kept_modes).most_common(1)[0][0]
    autopilot = (detail.get("session") or {}).get("autopilot")
    sticky = modes.STICK_MODES.get(autopilot) or frozenset().union(*modes.STICK_MODES.values())
    if mode not in sticky:
        return None  # an autopilot-flown record: the sticks were only a keep-alive
    t0 = rows[0][0]
    # Mode changes after the first row (an autopilot landing, say) are replayed with the sticks.
    changes: list[tuple[float, str]] = []
    for i in range(armed[0], armed[-1] + 1):
        name = cols["mode"][i]
        if name and cols["t"][i] > t0 and (changes[-1][1] if changes else rows_mode0) != name:
            changes.append((round(cols["t"][i] - t0, 3), name))
    return {
        "rows": [(round(r[0] - t0, 3), *r[1:]) for r in rows],
        "duration_s": round(cols["t"][armed[-1]] - t0, 1),
        "mode": rows_mode0,
        "modes": changes,
        "autopilot": autopilot,
    }


def options(detail: dict[str, Any]) -> dict[str, Any]:
    """The ``GET /history/sessions/{sid}/refly`` body."""
    steps = plan_steps(detail)
    path = path_waypoints(detail)
    sticks = stick_rows(detail)
    recommended = "plan" if steps else "path" if path else "sticks" if sticks else None
    return {
        "plan": {"steps": steps} if steps else None,
        "path": {"waypoints": path["waypoints"], "duration_s": path["duration_s"]} if path else None,
        "sticks": {"duration_s": sticks["duration_s"], "mode": sticks["mode"]} if sticks else None,
        "recommended": recommended,
    }
