"""The lens table of the gimbal pod (12 §5.1): one place for both the simulated and the real pod.

``visible1`` is the zoom lens (1x horizontal 60 degrees, N times: ``2 * atan(tan(30 degrees) / N)``, 1-30x),
``visible2`` the wide lens (fixed 90 degrees), ``ir1`` the infrared zoom (1x horizontal 40 degrees, 1-8x
electronic) and ``ir2`` the fixed 24 degree infrared lens. Pictures are 16:9. A real DYT pod reports its
zoom factor but not its field of view, so Relay estimates the field of view from this table.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

Lens = Literal["visible1", "visible2", "ir1", "ir2"]
LENSES: tuple[str, ...] = ("visible1", "visible2", "ir1", "ir2")
ASPECT = (16, 9)
IMAGE_PX = (1280, 720)  # the picture ``track`` pixels refer to: centre is (0, 0), x right, y down


@dataclass(frozen=True)
class LensSpec:
    hfov_deg: float  # horizontal field of view at 1x
    zoom_min: float
    zoom_max: float
    infrared: bool

    @property
    def fixed(self) -> bool:
        return self.zoom_max <= self.zoom_min


LENS_TABLE: dict[str, LensSpec] = {
    "visible1": LensSpec(60.0, 1.0, 30.0, False),
    "visible2": LensSpec(90.0, 1.0, 1.0, False),
    "ir1": LensSpec(40.0, 1.0, 8.0, True),
    "ir2": LensSpec(24.0, 1.0, 1.0, True),
}
# DYT picture codes (telemetry bits 7-6 / 5-4 of byte 5, and control codes 0x01-0x04 minus one)
LENS_INDEX = {name: i for i, name in enumerate(LENSES)}


def zoom_range(lens: str) -> tuple[float, float]:
    spec = LENS_TABLE[lens]
    return spec.zoom_min, spec.zoom_max


def clamp_zoom(lens: str, zoom: float) -> float:
    lo, hi = zoom_range(lens)
    return min(max(zoom, lo), hi)


def fov_deg(lens: str, zoom: float) -> tuple[float, float]:
    """(horizontal, vertical) field of view in degrees at ``zoom`` times on ``lens``."""
    spec = LENS_TABLE[lens]
    zoom = clamp_zoom(lens, zoom)
    half = math.tan(math.radians(spec.hfov_deg / 2.0)) / zoom
    h = 2.0 * math.degrees(math.atan(half))
    v = 2.0 * math.degrees(math.atan(half * ASPECT[1] / ASPECT[0]))
    return round(h, 2), round(v, 2)


def pixel_angles(lens: str, zoom: float, x: float, y: float) -> tuple[float, float]:
    """Angles (degrees, right and down positive) of the picture pixel (x, y) from the optical axis."""
    h, v = fov_deg(lens, zoom)
    ax = math.degrees(math.atan(x / (IMAGE_PX[0] / 2.0) * math.tan(math.radians(h / 2.0))))
    ay = math.degrees(math.atan(y / (IMAGE_PX[1] / 2.0) * math.tan(math.radians(v / 2.0))))
    return ax, ay


def angles_pixel(lens: str, zoom: float, ax_deg: float, ay_deg: float) -> tuple[float, float]:
    """The inverse of ``pixel_angles``."""
    h, v = fov_deg(lens, zoom)
    x = math.tan(math.radians(ax_deg)) / math.tan(math.radians(h / 2.0)) * IMAGE_PX[0] / 2.0
    y = math.tan(math.radians(ay_deg)) / math.tan(math.radians(v / 2.0)) * IMAGE_PX[1] / 2.0
    return x, y
