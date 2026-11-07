"""Parameter protocol helpers (03 §5.5, R-14).

PARAM_REQUEST_LIST fills the table; indices that did not arrive are fetched
again with PARAM_REQUEST_READ. PARAM_SET values are validated against the
type the autopilot reported and confirmed by the PARAM_VALUE echo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .decode import px4_param_encode
from .mavlink import M
from .modes import PX4

TYPE_NAMES = {
    M.MAV_PARAM_TYPE_UINT8: "uint8",
    M.MAV_PARAM_TYPE_INT8: "int8",
    M.MAV_PARAM_TYPE_UINT16: "uint16",
    M.MAV_PARAM_TYPE_INT16: "int16",
    M.MAV_PARAM_TYPE_UINT32: "uint32",
    M.MAV_PARAM_TYPE_INT32: "int32",
    M.MAV_PARAM_TYPE_REAL32: "float",
    M.MAV_PARAM_TYPE_REAL64: "double",
}
INT_RANGES = {
    M.MAV_PARAM_TYPE_UINT8: (0, 255),
    M.MAV_PARAM_TYPE_INT8: (-128, 127),
    M.MAV_PARAM_TYPE_UINT16: (0, 65535),
    M.MAV_PARAM_TYPE_INT16: (-32768, 32767),
    M.MAV_PARAM_TYPE_UINT32: (0, 4294967295),
    M.MAV_PARAM_TYPE_INT32: (-2147483648, 2147483647),
}
STALL_S = 1.0
# A full table over a Wi-Fi bridge loses frames; keep re-reading the gaps until it is complete.
MAX_ROUNDS = 80
READS_PER_ROUND = 25


def type_name(ptype: int) -> str:
    return TYPE_NAMES.get(ptype, str(ptype))


def normalise(value: object, ptype: int) -> float:
    """Validate ``value`` for ``ptype``; raises ValueError."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("not a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("not finite")
    if ptype in INT_RANGES:
        low, high = INT_RANGES[ptype]
        if not number.is_integer() or not low <= number <= high:
            raise ValueError("out of range")
    return number


def wire_value(value: float, ptype: int, autopilot: str | None) -> float:
    """The float placed in PARAM_SET.param_value (PX4 integers are bytewise)."""
    if autopilot == PX4:
        return px4_param_encode(value, ptype)
    return float(value)


def same_value(a: float, b: float, ptype: int) -> bool:
    if ptype in INT_RANGES:
        return round(a) == round(b)
    return math.isclose(a, b, rel_tol=1e-5, abs_tol=1e-6)


@dataclass
class ParamFetch:
    """Tracks one PARAM_REQUEST_LIST cycle and the re-reads of missing indices."""

    started: float
    rounds: int = 0
    last_progress: float = 0.0
    last_count: int = 0
    done: bool = False
    pending_reads: list[int] = field(default_factory=list)

    def progress(self, received: int, total: int, now: float) -> None:
        if received != self.last_count:
            self.last_count = received
            self.last_progress = now
        if total and received >= total:
            self.done = True

    def due_reads(self, params: dict, total: int, now: float) -> list[int]:
        """Indices to request now, or [] when not stalled or out of rounds."""
        if self.done or not total or self.rounds >= MAX_ROUNDS:
            return []
        if now - max(self.last_progress, self.started) < STALL_S:
            return []
        have = {entry["index"] for entry in params.values()}
        missing = [i for i in range(total) if i not in have][:READS_PER_ROUND]
        self.rounds += 1
        self.last_progress = now
        if not missing:
            self.done = True
        return missing


# Parameters the preflight and the skills depend on, read by name as soon as the autopilot is identified
# so that a lossy table fetch never leaves them unknown. Names absent on this firmware never answer.
ESSENTIAL = {
    "PX4": (
        "COM_RC_IN_MODE",
        "COM_RC_LOSS_T",
        "NAV_RCL_ACT",
        "NAV_DLL_ACT",
        "MPC_MAN_TILT_MAX",
        "MPC_THR_HOVER",
    ),
    "ArduPilot": (
        "MAV_GCS_SYSID",
        "SYSID_MYGCS",
        "RC_OVERRIDE_TIME",
        "FS_THR_ENABLE",
        "FS_GCS_ENABLE",
        "ANGLE_MAX",
        "MOT_THST_HOVER",
    ),
}
NAMED_TRIES = 6
NAMED_INTERVAL_S = 1.5


@dataclass
class NamedReads:
    """By-name reads of the essential parameters, retried until answered or out of tries."""

    names: tuple[str, ...]
    tries: dict[str, int] = field(default_factory=dict)
    next_at: float = 0.0

    def due(self, params: dict, now: float) -> list[str]:
        if now < self.next_at:
            return []
        out = [n for n in self.names if n not in params and self.tries.get(n, 0) < NAMED_TRIES]
        for n in out:
            self.tries[n] = self.tries.get(n, 0) + 1
        self.next_at = now + NAMED_INTERVAL_S
        return out

    def unreadable(self, params: dict) -> frozenset[str]:
        """Names asked for NAMED_TRIES times without an answer."""
        return frozenset(n for n in self.names if n not in params and self.tries.get(n, 0) >= NAMED_TRIES)
