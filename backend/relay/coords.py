"""Coordinate conventions of the control API (12 §3).

Inside Relay nothing changes: positions are local ENU metres (x east, y north, z up), the heading is the
NED yaw in radians (0 north, clockwise positive seen from above) and body offsets are forward / right / up.
Callers think in one of two conventions, chosen by the ``control_frame`` setting and overridable per
request with ``frame``:

========  ==========================  ==============================  ========================  ==========
frame     body x / y / z              local x / y / z                 heading ``yaw``           yaw rate +
========  ==========================  ==============================  ========================  ==========
``flu``   forward / left / up         east / north / up (ENU; ROS     0 deg along +x,           counter-
          (ROS ``base_link``)         ``map``, MAVROS local frame)    counter-clockwise +       clockwise
``frd``   forward / right / down      north / east / down (NED;       0 deg north,              clockwise
          (PX4 / aerospace body)      PX4 local frame)                clockwise +
========  ==========================  ==============================  ========================  ==========

``flu`` is the default: it is the convention of ROS 2 and of the ANetDU7 onboard stack (MAVROS). The
functions here are pure; ``normalise_skill`` turns the convention-dependent parameters of a skill request
into the executor's own (forward / right / up, ENU, compass degrees, clockwise rates).
"""

from __future__ import annotations

import math
from typing import Any

FLU = "flu"
FRD = "frd"
FRAMES = (FLU, FRD)
DEFAULT = FLU
# Relay's own axes, accepted on requests and echoed by ``skill.params`` (so a recorded skill can be flown
# again whatever the setting is by then): body forward / right / up, local ENU, compass heading, clockwise
# rates. It is the convention of the API before v0.6; velocity defaults to the local reference in it.
RELAY = "relay"
REQUEST_FRAMES = (FLU, FRD, RELAY)
REFERENCES = ("body", "local")

# Machine-readable description for GET /meta.
DESCRIPTIONS: dict[str, dict[str, Any]] = {
    FLU: {
        "body": ["forward", "left", "up"],
        "local": ["east", "north", "up"],
        "yaw": "0 deg along local +x (east), counter-clockwise positive",
        "yaw_rate": "counter-clockwise positive seen from above",
    },
    FRD: {
        "body": ["forward", "right", "down"],
        "local": ["north", "east", "down"],
        "yaw": "0 deg north, clockwise positive",
        "yaw_rate": "clockwise positive seen from above",
    },
}

Vec3 = tuple[float, float, float]


def check(frame: Any) -> str:
    """The frame name, or ValueError for anything else."""
    if frame not in FRAMES:
        raise ValueError("frame")
    return str(frame)


# ---------------------------------------------------------------- body axes


def body_to_fru(frame: str, x: float, y: float, z: float) -> Vec3:
    """Body vector of ``frame`` as (forward, right, up)."""
    if frame == RELAY:
        return (x, y, z)
    return (x, -y, z) if frame == FLU else (x, y, -z)


def body_from_fru(frame: str, forward: float, right: float, up: float) -> Vec3:
    return (forward, -right, up) if frame == FLU else (forward, right, -up)


# ---------------------------------------------------------------- local axes


def local_to_enu(frame: str, x: float, y: float, z: float) -> Vec3:
    """Local vector of ``frame`` as ENU."""
    return (y, x, -z) if frame == FRD else (x, y, z)


def local_from_enu(frame: str, east: float, north: float, up: float) -> Vec3:
    return (east, north, up) if frame == FLU else (north, east, -up)


# ---------------------------------------------------------------- heading and rates


def heading_to_compass(frame: str, yaw_deg: float) -> float:
    """A heading of ``frame`` in degrees as the compass heading (0 north, clockwise), 0..360."""
    return (90.0 - yaw_deg) % 360.0 if frame == FLU else yaw_deg % 360.0  # frd and relay: compass


def heading_from_compass(frame: str, compass_deg: float) -> float:
    """The compass heading in ``frame``: ``flu`` -180..180 (counter-clockwise from east), ``frd`` 0..360."""
    if frame == FLU:
        return (90.0 - compass_deg + 180.0) % 360.0 - 180.0
    return compass_deg % 360.0


def rate_to_clockwise(frame: str, rate: float) -> float:
    """A yaw rate (or a relative turn) of ``frame`` as clockwise positive."""
    return -rate if frame == FLU else rate


def rate_from_clockwise(frame: str, rate: float) -> float:
    return -rate if frame == FLU else rate


# ---------------------------------------------------------------- rotations about the vertical


def fru_to_enu(heading_rad: float, forward: float, right: float, up: float) -> Vec3:
    """A (forward, right, up) vector of an aircraft heading ``heading_rad`` (NED yaw) in ENU."""
    s, c = math.sin(heading_rad), math.cos(heading_rad)
    return (forward * s + right * c, forward * c - right * s, up)


def enu_to_fru(heading_rad: float, east: float, north: float, up: float) -> Vec3:
    s, c = math.sin(heading_rad), math.cos(heading_rad)
    return (east * s + north * c, east * c - north * s, up)


# ---------------------------------------------------------------- telemetry


def nav_view(
    frame: str,
    position_enu: Vec3 | None,
    velocity_enu: Vec3 | None,
    heading_rad: float | None,
    yaw_rate_rad: float | None,
) -> dict[str, Any] | None:
    """The ``vehicle.nav`` block (12 §3.3) in ``frame``; None without a position and without a heading."""
    if position_enu is None and heading_rad is None:
        return None
    body = None
    if velocity_enu is not None and heading_rad is not None:
        body = _rounded(body_from_fru(frame, *enu_to_fru(heading_rad, *velocity_enu)))
    return {
        "frame": frame,
        "position": None if position_enu is None else _rounded(local_from_enu(frame, *position_enu)),
        "velocity": None if velocity_enu is None else _rounded(local_from_enu(frame, *velocity_enu)),
        "body_velocity": body,
        "yaw_deg": None
        if heading_rad is None
        else round(heading_from_compass(frame, math.degrees(heading_rad)), 2) + 0.0,
        "yaw_rate_dps": None
        if yaw_rate_rad is None
        else round(rate_from_clockwise(frame, math.degrees(yaw_rate_rad)), 2) + 0.0,
    }


def _rounded(v: Vec3) -> list[float]:
    return [round(c + 0.0, 3) for c in v]  # + 0.0: no negative zero in the JSON


# ---------------------------------------------------------------- skill requests

_NAMED = ("forward", "right", "left", "up", "down")


class FrameError(ValueError):
    """A skill request that mixes or misuses frame parameters; ``reason`` is the message."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _num(params: dict[str, Any], key: str) -> float | None:
    value = params.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise FrameError(f"{key} must be a number")
    return float(value)


def normalise_skill(name: str, params: dict[str, Any], default: str = DEFAULT) -> dict[str, Any]:
    """``params`` of the skill ``name`` with the convention-dependent ones turned into the executor's own.

    * ``move``: ``x, y, z`` (body offsets of the frame) or the named directions ``forward`` / ``right`` /
      ``left`` / ``up`` / ``down`` become ``forward`` / ``right`` / ``up``;
    * ``velocity``: ``vx, vy, vz`` become ENU (``reference: "local"``) or stay body and become
      ``_body = [forward, right, up]`` (``reference: "body"``, the default); ``yaw_rate_dps`` becomes
      clockwise positive;
    * ``goto``: ``x, y, z`` become ENU, ``yaw`` the compass heading;
    * ``yaw``: ``deg`` becomes clockwise positive.

    The result is in Relay's own axes and says so (``frame: "relay"``, and ``reference`` for velocity),
    so feeding it back in (a re-fly of a recorded skill) gives the same flight under any setting. Other
    skills pass through with ``frame`` and ``reference`` dropped.
    """
    out = dict(params)
    frame = out.pop("frame", None)
    reference = out.pop("reference", None)
    if frame is None:
        frame = default
    if frame not in REQUEST_FRAMES:
        raise FrameError("frame must be flu, frd or relay")
    if reference is not None and reference not in REFERENCES:
        raise FrameError("reference must be body or local")
    if reference is not None and name != "velocity":
        raise FrameError("reference applies to velocity")

    if name == "move":
        generic = [k for k in ("x", "y", "z") if out.get(k) is not None]
        named = [k for k in _NAMED if out.get(k) is not None]
        if generic and named:
            raise FrameError("move takes x / y / z or named directions, not both")
        if out.get("left") is not None and out.get("right") is not None:
            raise FrameError("move takes left or right, not both")
        if out.get("down") is not None and out.get("up") is not None:
            raise FrameError("move takes up or down, not both")
        if generic:
            forward, right, up = body_to_fru(
                frame, _num(out, "x") or 0.0, _num(out, "y") or 0.0, _num(out, "z") or 0.0
            )
        else:
            forward = _num(out, "forward") or 0.0
            left, down = _num(out, "left"), _num(out, "down")
            right = -left if left is not None else (_num(out, "right") or 0.0)
            up = -down if down is not None else (_num(out, "up") or 0.0)
        for key in ("x", "y", "z", "left", "down"):
            out.pop(key, None)
        out.update(forward=forward + 0.0, right=right + 0.0, up=up + 0.0, frame=RELAY)
    elif name == "velocity":
        reference = reference or ("local" if frame == RELAY else "body")
        vx, vy, vz = (_num(out, k) or 0.0 for k in ("vx", "vy", "vz"))
        rate = _num(out, "yaw_rate_dps")
        if rate is not None:
            out["yaw_rate_dps"] = rate_to_clockwise(frame, rate) + 0.0
        if reference == "body":
            body = [c + 0.0 for c in body_to_fru(frame, vx, vy, vz)]
            out["_body"] = body
            out.update(vx=body[0], vy=body[1], vz=body[2])
        else:
            east, north, up = local_to_enu(frame, vx, vy, vz)
            out.update(vx=east + 0.0, vy=north + 0.0, vz=up + 0.0)
        out.update(frame=RELAY, reference=reference)
    elif name == "goto":
        if any(out.get(k) is not None for k in ("x", "y", "z")):
            east, north, up = local_to_enu(
                frame, _num(out, "x") or 0.0, _num(out, "y") or 0.0, _num(out, "z") or 0.0
            )
            missing = [k for k in ("x", "y", "z") if out.get(k) is None]
            if missing:
                raise FrameError(f"goto needs x, y and z (missing {missing[0]})")
            out.update(x=east + 0.0, y=north + 0.0, z=up + 0.0)
        yaw = _num(out, "yaw")
        if yaw is not None:
            if not -360.0 <= yaw <= 360.0:
                raise FrameError("yaw must be -360 to 360")
            out["yaw"] = heading_to_compass(frame, yaw)
        out["frame"] = RELAY
    elif name == "yaw":
        deg = _num(out, "deg")
        if deg is not None:
            out["deg"] = rate_to_clockwise(frame, deg) + 0.0
        out["frame"] = RELAY
    return out
