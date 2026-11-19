"""The aircraft the pod hangs under, and the geometry between the two (12 §5).

``Carrier`` is the pose a pod needs each step: position in the world's ENU metres (x east, y north, z up),
attitude (roll right positive, pitch nose up positive, yaw as an NED heading: north 0, clockwise positive,
radians) and the geodetic position. The pod's own angles are frame angles of the carrier: azimuth right
positive, pitch up positive. ``earth`` angles are an azimuth from north (clockwise) and an elevation above
the horizon. Everything here is pure.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

EARTH_R = 6371000.0
Vec = tuple[float, float, float]


@dataclass
class Carrier:
    pos: Vec = (0.0, 0.0, 0.0)  # world ENU, metres
    roll: float = 0.0  # rad, right positive
    pitch: float = 0.0  # rad, nose up positive
    yaw: float = 0.0  # rad, NED heading
    lat: float = 0.0  # degrees
    lon: float = 0.0
    alt: float = 0.0  # metres above sea level
    rel_alt: float = 0.0  # metres above the take-off point
    speed: float = 0.0  # ground speed, m/s
    wall: float = 0.0  # epoch seconds
    frame: str = "flu"  # control frame of the nav block (12 §3)
    rings: Sequence[dict[str, Any]] = ()  # solid rings the laser can hit (world coordinates)

    def nav(self) -> dict[str, Any]:
        """The pose as the ``nav`` block of a capture, in the control frame (12 §3.3)."""
        x, y, z = self.pos
        heading = math.degrees(self.yaw) % 360.0
        if self.frame == "frd":
            position = [round(y, 3), round(x, 3), round(-z, 3)]
            yaw_deg = ((heading + 180.0) % 360.0) - 180.0
        else:
            position = [round(x, 3), round(y, 3), round(z, 3)]
            yaw_deg = ((90.0 - heading + 180.0) % 360.0) - 180.0
        return {
            "frame": self.frame,
            "position": position,
            "yaw_deg": round(yaw_deg, 2),
            "roll_deg": round(math.degrees(self.roll), 2),
            "pitch_deg": round(math.degrees(self.pitch), 2),
            "heading_deg": round(heading, 2),
            "lat": round(self.lat, 7),
            "lon": round(self.lon, 7),
            "alt": round(self.alt, 2),
        }


def wrap180(deg: float) -> float:
    return (deg + 180.0) % 360.0 - 180.0


def _mul(m: tuple[Vec, Vec, Vec], v: Vec) -> Vec:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def _mul_t(m: tuple[Vec, Vec, Vec], v: Vec) -> Vec:
    """``m`` transposed times ``v`` (the inverse rotation)."""
    return (
        m[0][0] * v[0] + m[1][0] * v[1] + m[2][0] * v[2],
        m[0][1] * v[0] + m[1][1] * v[1] + m[2][1] * v[2],
        m[0][2] * v[0] + m[1][2] * v[1] + m[2][2] * v[2],
    )


def body_to_ned(roll: float, pitch: float, yaw: float) -> tuple[Vec, Vec, Vec]:
    """Rotation matrix taking FRD body vectors to NED (yaw, then pitch, then roll)."""
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return (
        (cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr),
        (sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr),
        (-sp, cp * sr, cp * cr),
    )


def los_body(yaw_deg: float, pitch_deg: float) -> Vec:
    """Unit line of sight in the FRD body frame for the frame angles."""
    y, p = math.radians(yaw_deg), math.radians(pitch_deg)
    return (math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), -math.sin(p))


def earth_vec(az_deg: float, el_deg: float) -> Vec:
    """Unit NED vector for an azimuth from north (clockwise) and an elevation above the horizon."""
    a, e = math.radians(az_deg), math.radians(el_deg)
    return (math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), -math.sin(e))


def ned_to_angles(v: Vec) -> tuple[float, float]:
    """(azimuth from north clockwise, elevation) in degrees of a NED vector."""
    return math.degrees(math.atan2(v[1], v[0])), math.degrees(math.atan2(-v[2], math.hypot(v[0], v[1])))


def enu_to_ned(v: Vec) -> Vec:
    return (v[1], v[0], -v[2])


def ned_to_enu(v: Vec) -> Vec:
    return (v[1], v[0], -v[2])


def frame_to_earth(c: Carrier, yaw_deg: float, pitch_deg: float) -> Vec:
    """Unit line of sight, world ENU, for frame angles under the carrier's attitude."""
    return ned_to_enu(_mul(body_to_ned(c.roll, c.pitch, c.yaw), los_body(yaw_deg, pitch_deg)))


def earth_to_frame(c: Carrier, enu: Vec) -> tuple[float, float]:
    """Frame angles (azimuth, pitch) of the world ENU direction ``enu`` under the carrier's attitude."""
    b = _mul_t(body_to_ned(c.roll, c.pitch, c.yaw), enu_to_ned(enu))
    return math.degrees(math.atan2(b[1], b[0])), math.degrees(math.atan2(-b[2], math.hypot(b[0], b[1])))


def camera_ray(c: Carrier, yaw_deg: float, pitch_deg: float, ray_cam: Vec) -> Vec:
    """World ENU direction of ``ray_cam`` (forward, right, down in the camera frame) for a camera at the
    given frame angles."""
    y, p = math.radians(yaw_deg), math.radians(pitch_deg)
    cy, sy, cp, sp = math.cos(y), math.sin(y), math.cos(p), math.sin(p)
    rz_ry = ((cy * cp, -sy, cy * sp), (sy * cp, cy, sy * sp), (-sp, 0.0, cp))
    body = _mul(rz_ry, ray_cam)
    return ned_to_enu(_mul(body_to_ned(c.roll, c.pitch, c.yaw), body))


def unit(v: Vec) -> Vec:
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / n, v[1] / n, v[2] / n) if n > 1e-12 else (0.0, 0.0, 0.0)


def look_angles(src: Vec, dst: Vec) -> tuple[float, float]:
    """(azimuth from north clockwise, elevation) in degrees of the direction from ``src`` to ``dst`` (ENU)."""
    d = (dst[0] - src[0], dst[1] - src[1], dst[2] - src[2])
    if math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2) < 1e-9:
        raise ValueError("the point is at the aircraft")
    return ned_to_angles(enu_to_ned(d))


def offset_geo(lat: float, lon: float, east: float, north: float) -> tuple[float, float]:
    """The latitude and longitude ``east`` / ``north`` metres from (lat, lon)."""
    dlat = math.degrees(north / EARTH_R)
    dlon = math.degrees(east / (EARTH_R * max(1e-6, math.cos(math.radians(lat)))))
    return lat + dlat, lon + dlon


def geo_offset(lat0: float, lon0: float, lat: float, lon: float) -> tuple[float, float]:
    """(east, north) metres of (lat, lon) from (lat0, lon0)."""
    north = math.radians(lat - lat0) * EARTH_R
    east = math.radians(lon - lon0) * EARTH_R * math.cos(math.radians(lat0))
    return east, north
