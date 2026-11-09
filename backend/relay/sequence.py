"""Stick timelines and the ``.ars`` sequence file (10 §2).

A sequence is four stick tracks on one time line, flown in one portable flight mode; the same file flies a
simulated and a real aircraft. File layout (UTF-8 JSON, suffix ``.ars``, "ANet Relay Sequence")::

    {
      "format": "ars", "version": 1,
      "name": "takeoff, hover 5 s, land",
      "mode": "althold",                      # stabilize | acro | althold | poshold
      "duration_s": 14.0,
      "trim": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0, "throttle": 0.0},
      "tracks": {
        "throttle": [{"t": 0, "v": 0.0, "ease": "hold", "label": "spool"}, {"t": 1.0, "v": 0.72}, ...],
        "roll": [...], "pitch": [...], "yaw": [...]
      },
      "end": "disarm",                        # disarm (on the ground) | hold
      "source": {...}                         # optional provenance, kept as written
    }

Each key starts a block, the operation the editor shows: ``{t, v, ease, label?}`` holds the stick at ``v``
from ``t`` until the next key (``hold``, the default) or moves it linearly from ``v`` to the next key's
value (``ramp``); the block lasts until the next key. Before the first key a track holds its first value,
after the last key its last value; a missing track is centred
(throttle: 0.5 in the hold modes, 0 in stabilize and acro). Sticks are the Relay convention: roll / pitch /
yaw -1..1 (right, forward, clockwise +), throttle 0..1. ``trim`` is added to every frame and can change
while the sequence flies; it is how a drift is cancelled without editing the tracks. ``acro`` (11 §9) flies
ArduPilot / PX4 ACRO: the roll / pitch / yaw sticks are body rates and nothing levels the aircraft.
"""

from __future__ import annotations

import math
from typing import Any

from .mav import modes

FORMAT = "ars"
VERSION = 1
SUFFIX = ".ars"
TRACKS = ("roll", "pitch", "yaw", "throttle")
EASES = ("ramp", "hold")
ENDS = ("disarm", "hold")
MAX_DURATION_S = 600.0
MAX_KEYS = 4000  # per track
MAX_NAME = 80
TRIM_LIMITS = {"roll": 0.3, "pitch": 0.3, "yaw": 0.3, "throttle": 0.2}
# portable mode -> the autopilot's own name
MODES: dict[str, dict[str, str]] = {
    "stabilize": {modes.ARDUPILOT: "STABILIZE", modes.PX4: "STABILIZED"},
    "acro": {modes.ARDUPILOT: "ACRO", modes.PX4: "ACRO"},
    "althold": {modes.ARDUPILOT: "ALT_HOLD", modes.PX4: "ALTCTL"},
    "poshold": {modes.ARDUPILOT: "LOITER", modes.PX4: "POSCTL"},
}
MANUAL_MODES = ("stabilize", "acro")  # the throttle stick is the collective output
RECORD_TOLERANCE = 0.015  # stick units a simplified recording may deviate from the samples
RECORD_MAX_GAP_S = 2.0  # longest stretch between kept keys of a recording


class SequenceError(ValueError):
    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


def _range(name: str) -> tuple[float, float]:
    return (0.0, 1.0) if name == "throttle" else (-1.0, 1.0)


def _num(field: str, value: Any, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SequenceError(field, "must be a number")
    x = float(value)
    if not math.isfinite(x) or not lo <= x <= hi:
        raise SequenceError(field, f"must be within {lo:g}..{hi:g}")
    return x


def normalise_trim(raw: Any, base: dict[str, float] | None = None) -> dict[str, float]:
    out = dict(base or {name: 0.0 for name in TRACKS})
    if raw is None:
        return out
    if not isinstance(raw, dict):
        raise SequenceError("trim", "must be an object")
    for name, value in raw.items():
        if name not in TRIM_LIMITS:
            raise SequenceError(f"trim.{name}", "unknown axis")
        if value is None:
            continue
        lim = TRIM_LIMITS[name]
        out[name] = round(_num(f"trim.{name}", value, -lim, lim), 4)
    return out


def normalise(raw: Any) -> dict[str, Any]:
    """Check a sequence document; returns the canonical form. Raises SequenceError."""
    if not isinstance(raw, dict):
        raise SequenceError("sequence", "must be an object")
    if raw.get("format") != FORMAT:
        raise SequenceError("format", f'must be "{FORMAT}"')
    version = raw.get("version")
    if version != VERSION:
        raise SequenceError("version", f"unsupported (this Relay reads version {VERSION})")
    mode = raw.get("mode", "althold")
    if mode not in MODES:
        raise SequenceError("mode", "must be stabilize, acro, althold or poshold")
    end = raw.get("end", "disarm")
    if end not in ENDS:
        raise SequenceError("end", "must be disarm or hold")
    name = raw.get("name") or ""
    if not isinstance(name, str):
        raise SequenceError("name", "must be text")
    tracks_raw = raw.get("tracks")
    if not isinstance(tracks_raw, dict):
        raise SequenceError("tracks", "must be an object")
    tracks: dict[str, list[dict[str, Any]]] = {}
    last_t = 0.0
    for track, keys in tracks_raw.items():
        if track not in TRACKS:
            raise SequenceError(f"tracks.{track}", "unknown track")
        if not isinstance(keys, list):
            raise SequenceError(f"tracks.{track}", "must be a list of keys")
        if len(keys) > MAX_KEYS:
            raise SequenceError(f"tracks.{track}", f"at most {MAX_KEYS} keys")
        lo, hi = _range(track)
        out: list[dict[str, Any]] = []
        prev = -1.0
        for i, key in enumerate(keys):
            where = f"tracks.{track}[{i}]"
            if not isinstance(key, dict):
                raise SequenceError(where, "must be {t, v, ease}")
            t = _num(f"{where}.t", key.get("t"), 0.0, MAX_DURATION_S)
            if t < prev:
                raise SequenceError(f"{where}.t", "keys must be in time order")
            v = _num(f"{where}.v", key.get("v"), lo, hi)
            ease = key.get("ease", "hold")
            if ease not in EASES:
                raise SequenceError(f"{where}.ease", "must be ramp or hold")
            item = {"t": round(t, 3), "v": round(v, 4), "ease": ease}
            label = key.get("label")
            if label is not None:
                if not isinstance(label, str):
                    raise SequenceError(f"{where}.label", "must be text")
                item["label"] = label[:40]
            out.append(item)
            prev = t
        if out:
            tracks[track] = out
            last_t = max(last_t, out[-1]["t"])
    if not tracks:
        raise SequenceError("tracks", "at least one track needs a key")
    duration = raw.get("duration_s")
    duration = last_t if duration is None else _num("duration_s", duration, 0.0, MAX_DURATION_S)
    if duration < last_t:
        raise SequenceError("duration_s", "ends before the last key")
    if duration <= 0.0:
        raise SequenceError("duration_s", "must be longer than 0 s")
    doc: dict[str, Any] = {
        "format": FORMAT,
        "version": VERSION,
        "name": name[:MAX_NAME],
        "mode": mode,
        "duration_s": round(duration, 3),
        "trim": normalise_trim(raw.get("trim")),
        "tracks": tracks,
        "end": end,
    }
    if isinstance(raw.get("source"), dict):
        doc["source"] = raw["source"]
    return doc


def autopilot_mode(portable: str, autopilot: str | None) -> str:
    table = MODES[portable]
    return table.get(autopilot or "", table[modes.ARDUPILOT])


def portable_mode(autopilot: str | None, name: str | None) -> str | None:
    for portable, table in MODES.items():
        if name in table.values():
            return portable
    if name and name in modes.HOLD_STICK_MODES:
        return "althold"
    if name and modes.STICK_MODES.get(autopilot or "") and name in modes.STICK_MODES[autopilot or ""]:
        return "stabilize"
    return None


def rest_value(track: str, mode: str) -> float:
    if track == "throttle":
        return 0.0 if mode in MANUAL_MODES else 0.5
    return 0.0


def value_at(keys: list[dict[str, Any]], t: float) -> float:
    if t <= keys[0]["t"]:
        return keys[0]["v"]
    if t >= keys[-1]["t"]:
        return keys[-1]["v"]
    lo, hi = 0, len(keys) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if keys[mid]["t"] <= t:
            lo = mid
        else:
            hi = mid
    a, b = keys[lo], keys[hi]
    if a["ease"] == "hold" or b["t"] <= a["t"]:
        return a["v"]
    return a["v"] + (b["v"] - a["v"]) * (t - a["t"]) / (b["t"] - a["t"])


def sample(doc: dict[str, Any], t: float, trim: dict[str, float] | None = None) -> tuple[float, ...]:
    """(roll, pitch, yaw, throttle) at ``t`` seconds, trim added, clamped to the stick range."""
    trim = trim or doc["trim"]
    out = []
    for track in TRACKS:
        keys = doc["tracks"].get(track)
        v = value_at(keys, t) if keys else rest_value(track, doc["mode"])
        lo, hi = _range(track)
        out.append(min(hi, max(lo, v + trim.get(track, 0.0))))
    return tuple(out)


# ---------------------------------------------------------------- recordings


def _simplify(points: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker on (t, v) with the deviation measured in v; keeps the ends."""
    if len(points) <= 2:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        ta, va = points[a]
        tb, vb = points[b]
        worst, at = -1.0, -1
        for i in range(a + 1, b):
            t, v = points[i]
            f = (t - ta) / (tb - ta) if tb > ta else 0.0
            d = abs(v - (va + (vb - va) * f))
            if d > worst:
                worst, at = d, i
        if at >= 0 and (worst > tol or tb - ta > RECORD_MAX_GAP_S):
            keep[at] = True
            stack.append((a, at))
            stack.append((at, b))
    return [p for p, k in zip(points, keep, strict=True) if k]


def from_rows(
    rows: list[tuple[float, ...]],
    *,
    mode: str | None,
    autopilot: str | None,
    name: str = "",
    source: dict[str, Any] | None = None,
    tolerance: float = RECORD_TOLERANCE,
) -> dict[str, Any]:
    """A sequence from recorded ``(t, roll, pitch, yaw, throttle)`` rows (t from 0), simplified to keys."""
    if len(rows) < 2:
        raise SequenceError("rows", "the recording holds fewer than two stick samples")
    t0 = rows[0][0]
    tracks: dict[str, list[dict[str, Any]]] = {}
    for i, track in enumerate(TRACKS, start=1):
        points = [(round(r[0] - t0, 3), float(r[i])) for r in rows]
        kept = _simplify(points, tolerance)
        tracks[track] = [{"t": t, "v": round(v, 4), "ease": "ramp"} for t, v in kept]
    doc = {
        "format": FORMAT,
        "version": VERSION,
        "name": name[:MAX_NAME],
        "mode": portable_mode(autopilot, mode) or "althold",
        "duration_s": round(rows[-1][0] - t0, 3),
        "trim": normalise_trim(None),
        "tracks": tracks,
        "end": "disarm",
        "source": dict(source or {}),
    }
    return normalise(doc)
