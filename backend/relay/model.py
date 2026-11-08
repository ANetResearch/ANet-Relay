"""Plain data types shared by the authority rules, the fleet service and the API."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

VehicleKind = Literal["real", "sim"]
LinkState = Literal["waiting", "ok", "delayed", "lost", "offline"]
Controller = Literal["none", "human", "agent", "program"]  # program: WP-1 onboard program (06 §2.6)
InputKind = Literal["keyboard", "screen", "gamepad"]
INPUT_KINDS = ("keyboard", "screen", "gamepad")
AXES = ("roll", "pitch", "yaw")


@dataclass(frozen=True)
class Claim:
    """An agent session's claim on a vehicle."""

    session_id: str
    name: str
    since: float  # wall clock

    def view(self, suspended: bool) -> dict:
        return {"session_id": self.session_id, "name": self.name, "since": self.since, "suspended": suspended}


@dataclass(frozen=True)
class Possession:
    """An operator input source flying a vehicle."""

    input: InputKind
    pad: int | None
    since: float  # wall clock

    @property
    def source(self) -> str:
        return input_source(self.input, self.pad)

    def view(self) -> dict:
        return {"input": self.input, "pad": self.pad, "since": self.since}


def input_source(kind: str, pad: int | None) -> str:
    """Each input is its own source: keyboard, screen, gamepad:1..4."""
    return f"gamepad:{pad or 1}" if kind == "gamepad" else kind


@dataclass
class StickInput:
    """Sticks in the API frame: roll right +, pitch forward +, yaw clockwise +, all -1..1; throttle 0..1."""

    roll: float
    pitch: float
    yaw: float
    throttle: float
    t: float = 0.0  # monotonic arrival time
    source: Literal["human", "agent", "program"] = "agent"
    conn: str | None = None  # WebSocket connection that sent it, if any

    @staticmethod
    def problem(roll: object, pitch: object, yaw: object, throttle: object) -> str | None:
        """Name of the first missing or out-of-range axis, or None."""
        for name, value, low in (("roll", roll, -1.0), ("pitch", pitch, -1.0), ("yaw", yaw, -1.0)):
            if not _number(value) or not low <= float(value) <= 1.0:  # type: ignore[arg-type]
                return name
        if not _number(throttle) or not 0.0 <= float(throttle) <= 1.0:  # type: ignore[arg-type]
            return "throttle"
        return None

    def deflection_from(self, other: StickInput | None) -> float:
        if other is None:
            return max(abs(self.roll), abs(self.pitch), abs(self.yaw))
        return max(
            abs(self.roll - other.roll),
            abs(self.pitch - other.pitch),
            abs(self.yaw - other.yaw),
            abs(self.throttle - other.throttle),
        )


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


@dataclass
class Signs:
    roll: int = 1
    pitch: int = 1
    yaw: int = 1

    def as_dict(self) -> dict[str, int]:
        return {"roll": self.roll, "pitch": self.pitch, "yaw": self.yaw}


@dataclass
class Calibration:
    signs: Signs = field(default_factory=Signs)
    hover: float = 0.35

    def view(self) -> dict:
        return {"signs": self.signs.as_dict(), "hover": round(self.hover, 3)}


@dataclass
class Confirmed:
    signs: bool = False
    props: bool = False

    def view(self) -> dict:
        return {"signs": self.signs, "props": self.props}


@dataclass
class Capability:
    manual: bool = False
    althold: bool = False
    position: bool = False

    def view(self) -> dict:
        return {"manual": self.manual, "althold": self.althold, "position": self.position}

    def has(self, name: str) -> bool:
        return name == "any" or bool(getattr(self, name, False))
