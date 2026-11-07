"""Ring courses and pass judging (03 §8, R-43, R-44).

Coordinates are ENU metres (x east, y north, z up) relative to the course
origin. A ring's ``yaw_deg`` is the compass heading of its normal (0 = north,
90 = east); passing means crossing the ring plane from the negative to the
positive side with the crossing point within ``r`` of the centre. Only the
next ring counts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

DESIGN_VOLUME = (3.0, 3.0, 2.0)
MIN_RADIUS = 0.3
START_HEIGHT = 0.3
MAX_RINGS = 24


def _ring(x: float, y: float, z: float, yaw: float, r: float) -> dict[str, Any]:
    return {"c": [round(x, 4), round(y, 4), round(z, 4)], "yaw_deg": round(yaw % 360.0, 3), "r": r}


def _presets() -> list[dict[str, Any]]:
    cage = [
        _ring(0.0, 1.0, 1.0, 90.0, 0.45),
        _ring(1.0, 0.0, 1.0, 180.0, 0.45),
        _ring(0.0, -1.0, 1.0, 270.0, 0.45),
        _ring(-1.0, 0.0, 1.0, 0.0, 0.45),
    ]
    slalom = [_ring(0.6 if i % 2 == 0 else -0.6, -1.25 + 0.5 * i, 1.0, 0.0, 0.4) for i in range(6)]
    climb = []
    for i in range(5):
        bearing = 72.0 * i
        b = math.radians(bearing)
        climb.append(_ring(math.sin(b), math.cos(b), 0.6 + 0.25 * i, bearing + 90.0, 0.45))
    return [
        {"id": "cage-loop", "name_en": "Cage loop", "name_zh": "围网环线", "rings": cage, "preset": True},
        {"id": "slalom", "name_en": "Slalom", "name_zh": "绕桩", "rings": slalom, "preset": True},
        {"id": "climb", "name_en": "Climb", "name_zh": "螺旋爬升", "rings": climb, "preset": True},
    ]


PRESETS = {c["id"]: c for c in _presets()}


def scale_factor(volume: tuple[float, float, float]) -> float:
    return min(volume[0] / DESIGN_VOLUME[0], volume[1] / DESIGN_VOLUME[1], volume[2] / DESIGN_VOLUME[2])


def scaled(course: dict[str, Any], volume: tuple[float, float, float]) -> dict[str, Any]:
    """Presets scale uniformly with the safety volume; radii never shrink below 0.3 m."""
    if not course.get("preset"):
        return dict(course)
    s = scale_factor(volume)
    rings = [
        {
            "c": [round(v * s, 4) for v in ring["c"]],
            "yaw_deg": ring["yaw_deg"],
            "r": round(max(MIN_RADIUS, ring["r"] * s), 4),
        }
        for ring in course["rings"]
    ]
    return {**course, "rings": rings, "scale": round(s, 4)}


def validate_custom(
    course_id: str, body: dict[str, Any], volume: tuple[float, float, float]
) -> dict[str, Any]:
    """Return a normalised custom course or raise ValueError with a reason."""
    if course_id in PRESETS:
        raise PermissionError(course_id)
    if not course_id or len(course_id) > 40 or not all(c.isalnum() or c in "-_" for c in course_id):
        raise ValueError("id must be 1-40 letters, digits, '-' or '_'")
    rings_in = body.get("rings")
    if not isinstance(rings_in, list) or not 1 <= len(rings_in) <= MAX_RINGS:
        raise ValueError(f"rings must be a list of 1-{MAX_RINGS} rings")
    hx, hy, hz = volume[0] / 2.0, volume[1] / 2.0, volume[2]
    rings = []
    for i, ring in enumerate(rings_in):
        try:
            x, y, z = (float(v) for v in ring["c"])
            yaw = float(ring.get("yaw_deg", 0.0))
            r = float(ring.get("r", 0.45))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"ring {i + 1} needs c [x, y, z], yaw_deg and r") from exc
        if not (abs(x) <= hx and abs(y) <= hy and 0.2 <= z <= hz):
            raise ValueError(f"ring {i + 1} centre is outside the safety volume")
        if not MIN_RADIUS <= r <= 1.5:
            raise ValueError(f"ring {i + 1} radius must be {MIN_RADIUS}-1.5 m")
        rings.append(_ring(x, y, z, yaw, r))
    name_en = str(body.get("name_en") or course_id)[:60]
    name_zh = str(body.get("name_zh") or name_en)[:60]
    return {"id": course_id, "name_en": name_en, "name_zh": name_zh, "rings": rings, "preset": False}


def transform(rings: list[dict[str, Any]], origin: tuple[float, float, float, float]) -> list[dict[str, Any]]:
    """Place rings at ``origin`` (x, y, z, heading in degrees), rotating the course to that heading."""
    ox, oy, oz, heading = origin
    h = math.radians(heading)
    ch, sh = math.cos(h), math.sin(h)
    out = []
    for ring in rings:
        x, y, z = ring["c"]
        wx = ox + x * ch + y * sh
        wy = oy - x * sh + y * ch
        out.append(_ring(wx, wy, oz + z, ring["yaw_deg"] + heading, ring["r"]))
    return out


def normal(yaw_deg: float) -> tuple[float, float, float]:
    y = math.radians(yaw_deg)
    return (math.sin(y), math.cos(y), 0.0)


def crossing(
    p0: tuple[float, float, float], p1: tuple[float, float, float], ring: dict[str, Any]
) -> tuple[bool, float | None]:
    """(passed, fraction along p0->p1) for one position step against one ring."""
    c = ring["c"]
    n = normal(ring["yaw_deg"])
    d0 = sum((p0[i] - c[i]) * n[i] for i in range(3))
    d1 = sum((p1[i] - c[i]) * n[i] for i in range(3))
    if not (d0 < 0.0 <= d1):
        return False, None
    f = d0 / (d0 - d1) if d0 != d1 else 0.0
    q = [p0[i] + (p1[i] - p0[i]) * f for i in range(3)]
    dist = math.sqrt(sum((q[i] - c[i]) ** 2 for i in range(3)))
    return dist <= ring["r"], f


def fmt_time(seconds: float) -> str:
    minutes, rest = divmod(max(0.0, seconds), 60.0)
    return f"{int(minutes):02d}:{rest:05.2f}"


@dataclass
class CourseRun:
    """One attempt. Times are monotonic seconds; ``wall0`` maps them to wall clock for display."""

    course: dict[str, Any]
    origin: tuple[float, float, float, float]
    rings: list[dict[str, Any]]
    best_s: float | None = None
    state: str = "waiting_takeoff"
    next: int = 0
    started: float | None = None
    started_wall: float | None = None
    last_pass: float | None = None
    splits: list[float] = field(default_factory=list)
    finished: float | None = None
    reason: str | None = None
    ended: float | None = None
    last_p: tuple[float, float, float] | None = None
    controller: str | None = None

    @classmethod
    def begin(
        cls, course: dict[str, Any], origin: tuple[float, float, float, float], best_s: float | None
    ) -> CourseRun:
        return cls(course=course, origin=origin, rings=transform(course["rings"], origin), best_s=best_s)

    @property
    def active(self) -> bool:
        return self.state in ("waiting_takeoff", "running")

    def update(
        self, p: tuple[float, float, float], now: float, wall: float, airborne: bool
    ) -> list[tuple[str, dict]]:
        """Feed one position sample; returns events (ring_passed, course_finished)."""
        events: list[tuple[str, dict]] = []
        if not self.active:
            return events
        if self.state == "waiting_takeoff":
            if p[2] - self.origin[2] > START_HEIGHT or airborne:
                self.state = "running"
                self.started = now
                self.started_wall = wall
                self.last_pass = now
            self.last_p = p
            return events
        if self.last_p is not None:
            passed, _ = crossing(self.last_p, p, self.rings[self.next])
            if passed:
                assert self.last_pass is not None and self.started is not None
                split = round(now - self.last_pass, 3)
                self.splits.append(split)
                self.last_pass = now
                self.next += 1
                elapsed = round(now - self.started, 3)
                events.append(
                    (
                        "ring_passed",
                        {"ring": self.next, "total": len(self.rings), "split": split, "elapsed": elapsed},
                    )
                )
                if self.next >= len(self.rings):
                    self.state = "finished"
                    self.finished = now
                    self.ended = now
                    new_best = self.best_s is None or elapsed < self.best_s
                    events.append(
                        (
                            "course_finished",
                            {
                                "total_s": elapsed,
                                "total_text": fmt_time(elapsed),
                                "splits": list(self.splits),
                                "best_s": elapsed if new_best else self.best_s,
                                "new_best": new_best,
                            },
                        )
                    )
                    if new_best:
                        self.best_s = elapsed
        self.last_p = p
        return events

    def abort(self, reason: str, now: float) -> tuple[str, dict] | None:
        if not self.active:
            return None
        self.state = "aborted"
        self.reason = reason
        self.ended = now
        return ("course_aborted", {"reason": reason})

    def elapsed(self, now: float) -> float:
        if self.started is None:
            return 0.0
        end = self.finished if self.finished is not None else (self.ended if self.ended is not None else now)
        return round(end - self.started, 3)

    def total_s(self) -> float | None:
        if self.finished is None or self.started is None:
            return None
        return round(self.finished - self.started, 3)

    def view(self, now: float) -> dict[str, Any]:
        return {
            "course_id": self.course["id"],
            "name_en": self.course.get("name_en"),
            "name_zh": self.course.get("name_zh"),
            "state": self.state,
            "next": self.next,
            "total": len(self.rings),
            "started_at": self.started_wall,
            "elapsed_s": self.elapsed(now),
            "splits": list(self.splits),
            "best_s": self.best_s,
            "reason": self.reason,
            "origin": [round(v, 4) for v in self.origin],
            "rings": self.rings,
        }
