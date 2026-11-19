"""The simulated gimbal pod (12 §5.4): deterministic, stepped with explicit times, no threads.

``SimGimbal`` is the physics and state of the pod; ``pod.SimPod`` puts the common ``Gimbal`` face on it. The
caller advances it with ``step(now, carrier)`` (any monotonic ``now``; steps longer than 0.1 s are cut to
0.1 s) and drives it with ``execute(action, params, now)`` where the parameters are already validated.

* Each axis follows its rate command with a first-order lag (time constant 0.08 s, at most 100 deg/s).
  Target modes command a rate ``kp * error + feed-forward``; the feed-forward is the rate at which the
  target direction itself moves in the pod's frame, so an earth-fixed line of sight stays on target while
  the carrier turns. Pitch is limited to -120..+90 degrees, azimuth is continuous (shortest way round).
* ``follow`` holds the frame angles (the pod turns with the aircraft's head); ``lock`` holds the line of
  sight in space (earth fixed, the aircraft turns under it). ``rate`` and ``angle`` (frame or earth) are
  taken as commanded, ``scan`` sweeps the azimuth to either side of the nose, ``track`` holds the ground
  point behind a picture pixel (or a geographic point), ``off`` is motors off.
* Zoom slews at 2x per second to its target (or continuously at ``rate / 100 * 2`` x per second); each lens
  keeps its own zoom. The field of view comes from ``lens.fov_deg``.
* The laser measures along the line of sight to the ground plane (z = 0) and the solid rings of the world
  (at most 1200 m); a valid range gives the target's latitude, longitude and altitude.
"""

from __future__ import annotations

import math
from typing import Any

from ..sim import collide
from . import carrier as geo
from .carrier import Carrier, wrap180
from .lens import LENSES, clamp_zoom, fov_deg, pixel_angles, zoom_range

TAU_S = 0.08  # axis rate response
MAX_DPS = 100.0
KP = 6.0  # 1/s, angle error to rate command
MAX_DT = 0.1
PITCH_MIN, PITCH_MAX = -120.0, 90.0
ZOOM_SLEW = 2.0  # x per second
LASER_MAX_M = 1200.0
LASER_MIN_M = 1.0  # nearer than this there is no valid return
LASER_HZ = 20.0
SETTLE_DEG = 0.3
BOX_PX = 96  # tracking gate


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(max(v, lo), hi)


class SimGimbal:
    def __init__(
        self,
        *,
        yaw_deg: float = 0.0,
        pitch_deg: float = -30.0,
        selftest_s: float = 0.0,
        carrier: Carrier | None = None,
    ) -> None:
        self.yaw = yaw_deg
        self.pitch = pitch_deg
        self.yaw_dps = 0.0
        self.pitch_dps = 0.0
        self.hold = "follow"  # what a stop returns to: follow | lock
        self.mode = "follow"
        self.tgt: tuple[Any, ...] = ("frame", yaw_deg, pitch_deg)
        self._prev_desired: tuple[float, float] | None = None
        self._settle = False
        self.carrier = carrier or Carrier()
        self.lens = "visible1"
        self.pip: str | None = None
        self.zooms = {name: 1.0 for name in LENSES}
        self.zoom_target: float | None = None
        self.zoom_rate = 0.0
        self.digital_zoom = False
        self.palette = "white_hot"
        self.tracking: dict[str, Any] = {"state": "off", "algorithm": "auto", "x_deg": 0.0, "y_deg": 0.0}
        self.laser_on = False
        self.laser_range: float | None = None
        self.laser_hit: tuple[float, float, float] | None = None
        self._laser_t = -1e9
        self.recording = False
        self.photos = 0
        self.motors = True
        self.enhance = False
        self.osd = True
        self.low_light = False
        self.focus = {"mode": "auto", "position": None}
        self.target: dict[str, float] | None = None
        self._scan: dict[str, float] = {}
        self._t: float | None = None
        self._t0: float | None = None
        self.selftest_s = selftest_s
        self.selftest_done = selftest_s <= 0.0

    # ------------------------------------------------------------------ state helpers

    @property
    def zoom(self) -> float:
        return self.zooms[self.lens]

    def los(self) -> geo.Vec:
        """The line of sight, unit ENU."""
        return geo.frame_to_earth(self.carrier, self.yaw, self.pitch)

    def _set_target(self, tgt: tuple[Any, ...], mode: str) -> None:
        self.tgt = tgt
        self.mode = mode
        self._prev_desired = None
        self._settle = False

    def _enter_hold(self) -> None:
        """Hold the current line of sight the way the hold mode says: frame angles or space."""
        self.tracking.update(state="off", x_deg=0.0, y_deg=0.0)
        if self.hold == "lock":
            az, el = geo.ned_to_angles(geo.enu_to_ned(self.los()))
            self._set_target(("earth", az, el), "lock")
        else:
            self._set_target(("frame", self.yaw, self.pitch), "follow")

    # ------------------------------------------------------------------ commands

    def execute(self, action: str, p: dict[str, Any], now: float) -> list[tuple[str, dict[str, Any]]]:
        """Apply one validated command. Returns the events it caused: ``photo``, ``record``, ``lens``."""
        events: list[tuple[str, dict[str, Any]]] = []
        fn = getattr(self, f"_do_{action}", None)
        if fn is None:  # pragma: no cover - validation keeps unknown actions out
            raise ValueError(f"unknown gimbal action {action}")
        result = fn(**p)
        if result:
            events.extend(result)
        return events

    def _do_rate(self, yaw_dps: float, pitch_dps: float) -> None:
        if yaw_dps == 0.0 and pitch_dps == 0.0:
            self._do_stop()
            return
        self.tracking.update(state="off", x_deg=0.0, y_deg=0.0)
        self._set_target(("rate", yaw_dps, pitch_dps), "rate")

    def _do_stop(self) -> None:
        self._enter_hold()

    def _do_angle(self, yaw_deg: float, pitch_deg: float, space: str = "frame") -> None:
        self.tracking.update(state="off", x_deg=0.0, y_deg=0.0)
        if space == "earth":
            self._set_target(("earth", yaw_deg, pitch_deg), "angle")
        else:
            self._set_target(("frame", yaw_deg, _clamp(pitch_deg, PITCH_MIN, PITCH_MAX)), "angle")

    def _do_center(self) -> None:
        self._do_angle(0.0, 0.0)
        self._settle = True

    def _do_follow(self, on: bool) -> None:
        self.hold = "follow" if on else "lock"
        self._enter_hold()

    def _do_lock(self, on: bool) -> None:
        self.hold = "lock" if on else "follow"
        self._enter_hold()

    def _do_motors(self, on: bool) -> None:
        self.motors = bool(on)
        if self.motors:
            self._enter_hold()
        else:
            self.tracking.update(state="off", x_deg=0.0, y_deg=0.0)
            self._set_target(("none",), "off")
            self.yaw_dps = self.pitch_dps = 0.0  # unpowered: the axes stop at once
            self.zoom_rate = 0.0

    def _do_zoom(self, level: float) -> None:
        self.zoom_rate = 0.0
        self.zoom_target = clamp_zoom(self.lens, level)

    def _do_zoom_rate(self, rate: float) -> None:
        self.zoom_target = None
        self.zoom_rate = float(rate)

    def _do_digital_zoom(self, on: bool) -> None:
        self.digital_zoom = bool(on)

    def _do_lens(self, lens: str, pip: str | None = None) -> list[tuple[str, dict[str, Any]]]:
        self.zoom_rate = 0.0
        self.zoom_target = None
        self.lens = lens
        self.pip = None if pip == lens else pip
        return [("lens", {"lens": self.lens, "pip": self.pip})]

    def _do_palette(self, palette: str) -> None:
        self.palette = palette

    def _do_photo(self) -> list[tuple[str, dict[str, Any]]]:
        self.photos += 1
        return [("photo", {"index": self.photos})]

    def _do_record(self, on: bool) -> list[tuple[str, dict[str, Any]]]:
        if bool(on) == self.recording:
            return []
        self.recording = bool(on)
        return [("record", {"on": self.recording})]

    def _do_track(self, x: float, y: float, algorithm: str | None = None) -> None:
        if algorithm is not None:
            self.tracking["algorithm"] = algorithm
        ax, ay = pixel_angles(self.lens, self.zoom, x, y)
        c = self.carrier
        ray = (1.0, math.tan(math.radians(ax)), math.tan(math.radians(ay)))
        d = geo.camera_ray(c, self.yaw, self.pitch, ray)
        hit = self._ground_hit(d)
        if hit is None:  # above the horizon: follow that direction in space, nothing to lock onto
            az, el = geo.ned_to_angles(geo.enu_to_ned(geo.unit(d)))
            self._set_target(("earth", az, el), "track")
            self.tracking.update(state="searching", x_deg=0.0, y_deg=0.0)
            return
        self._set_target(("geo", hit), "track")
        self.tracking.update(state="locked")

    def _do_track_stop(self) -> None:
        self._enter_hold()

    def _do_laser(self, on: bool) -> None:
        self.laser_on = bool(on)
        if not self.laser_on:
            self.laser_range = None
            self.laser_hit = None
            self.target = None

    def _do_focus(self, mode: str, position: int | None = None) -> None:
        self.focus = {"mode": mode, "position": position if mode == "manual" else None}

    def _do_enhance(self, on: bool) -> None:
        self.enhance = bool(on)

    def _do_osd(self, on: bool) -> None:
        self.osd = bool(on)

    def _do_low_light(self, on: bool) -> None:
        self.low_light = bool(on)

    def _do_scan(self, yaw_deg: float, pitch_deg: float, speed_dps: float) -> None:
        self.tracking.update(state="off", x_deg=0.0, y_deg=0.0)
        self._scan = {"extent": abs(yaw_deg), "pitch": pitch_deg, "speed": speed_dps, "dir": 1.0}
        self._set_target(("scan",), "scan")

    def _do_scan_stop(self) -> None:
        self._enter_hold()

    def _do_geo_track(self, lat: float | None = None, lon: float | None = None, alt: float | None = None,
                      mode: str | None = None) -> None:  # fmt: skip
        c = self.carrier
        if mode == "exit":
            self._enter_hold()
            return
        if mode == "center":
            hit = self._ground_hit(self.los())
            if hit is None:
                return self._enter_hold()
            point = hit
        else:
            east, north = geo.geo_offset(c.lat, c.lon, float(lat), float(lon))  # type: ignore[arg-type]
            point = (c.pos[0] + east, c.pos[1] + north, c.pos[2] + float(alt) - c.alt)  # type: ignore[arg-type]
        self.tracking.update(state="off", x_deg=0.0, y_deg=0.0)
        self._set_target(("geo", point), "track")

    # ------------------------------------------------------------------ stepping

    def _ground_hit(self, d: geo.Vec) -> geo.Vec | None:
        c = self.carrier
        if d[2] >= -1e-9:
            return None
        t = -c.pos[2] / d[2]
        if t < 0 or t > 20000.0:
            return None
        return (c.pos[0] + d[0] * t, c.pos[1] + d[1] * t, 0.0)

    def _desired(self) -> tuple[float, float] | None:
        """The frame angles the target mode wants now, or None when there is no angle target."""
        kind = self.tgt[0]
        c = self.carrier
        if kind == "frame":
            return self.tgt[1], self.tgt[2]
        if kind == "earth":
            az, el = self.tgt[1], self.tgt[2]
            return geo.earth_to_frame(c, geo.ned_to_enu(geo.earth_vec(az, el)))
        if kind == "geo":
            p = self.tgt[1]
            d = (p[0] - c.pos[0], p[1] - c.pos[1], p[2] - c.pos[2])
            if math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2) < 0.05:
                return self.yaw, self.pitch
            return geo.earth_to_frame(c, geo.unit(d))
        return None

    def step(self, now: float, carrier: Carrier) -> None:
        dt = 0.0 if self._t is None else _clamp(now - self._t, 0.0, MAX_DT)
        if self._t0 is None:
            self._t0 = now
        self._t = now
        self.carrier = carrier
        if not self.selftest_done and now - self._t0 >= self.selftest_s:
            self.selftest_done = True
        if dt > 0.0:
            self._move(dt)
            self._zoom(dt)
        self._laser(now)
        self._track_error()

    def _move(self, dt: float) -> None:
        cmd_y = cmd_p = 0.0
        kind = self.tgt[0]
        if not self.motors or kind == "none":
            pass
        elif kind == "rate":
            cmd_y, cmd_p = self.tgt[1], self.tgt[2]
        elif kind == "scan":
            s = self._scan
            if self.yaw >= s["extent"]:
                s["dir"] = -1.0
            elif self.yaw <= -s["extent"]:
                s["dir"] = 1.0
            cmd_y = s["dir"] * s["speed"]
            cmd_p = KP * (s["pitch"] - self.pitch)
        else:
            desired = self._desired()
            if desired is not None:
                yt, pt = desired[0], _clamp(desired[1], PITCH_MIN, PITCH_MAX)
                ff_y = ff_p = 0.0
                if kind in ("earth", "geo") and self._prev_desired is not None:
                    ff_y = wrap180(yt - self._prev_desired[0]) / dt
                    ff_p = (pt - self._prev_desired[1]) / dt
                self._prev_desired = (yt, pt)
                err_y, err_p = wrap180(yt - self.yaw), pt - self.pitch
                cmd_y = KP * err_y + _clamp(ff_y, -300.0, 300.0)
                cmd_p = KP * err_p + _clamp(ff_p, -300.0, 300.0)
                if self._settle and abs(err_y) < SETTLE_DEG and abs(err_p) < SETTLE_DEG:
                    self._enter_hold()
        cmd_y = _clamp(cmd_y, -MAX_DPS, MAX_DPS)
        cmd_p = _clamp(cmd_p, -MAX_DPS, MAX_DPS)
        a = 1.0 - math.exp(-dt / TAU_S)
        self.yaw_dps += (cmd_y - self.yaw_dps) * a
        self.pitch_dps += (cmd_p - self.pitch_dps) * a
        self.yaw = wrap180(self.yaw + self.yaw_dps * dt)
        self.pitch += self.pitch_dps * dt
        if self.pitch > PITCH_MAX:
            self.pitch, self.pitch_dps = PITCH_MAX, min(0.0, self.pitch_dps)
        elif self.pitch < PITCH_MIN:
            self.pitch, self.pitch_dps = PITCH_MIN, max(0.0, self.pitch_dps)

    def _zoom(self, dt: float) -> None:
        lens = self.lens
        z = self.zooms[lens]
        if self.zoom_rate != 0.0:
            z += self.zoom_rate / 100.0 * ZOOM_SLEW * dt
        elif self.zoom_target is not None:
            step = ZOOM_SLEW * dt
            z = (
                self.zoom_target
                if abs(self.zoom_target - z) <= step
                else z + math.copysign(step, self.zoom_target - z)
            )
            if z == self.zoom_target:
                self.zoom_target = None
        lo, hi = zoom_range(lens)
        at_end = (z <= lo and self.zoom_rate < 0) or (z >= hi and self.zoom_rate > 0)
        if at_end:
            self.zoom_rate = 0.0
        self.zooms[lens] = _clamp(z, lo, hi)

    def _laser(self, now: float) -> None:
        if not self.laser_on:
            return
        if now - self._laser_t < 1.0 / LASER_HZ:
            return
        self._laser_t = now
        c = self.carrier
        d = self.los()
        best = LASER_MAX_M
        hit = None
        if d[2] < -1e-9:
            t = -c.pos[2] / d[2]
            if 0.0 <= t < best:
                best, hit = t, "ground"
        end = (c.pos[0] + d[0] * LASER_MAX_M, c.pos[1] + d[1] * LASER_MAX_M, c.pos[2] + d[2] * LASER_MAX_M)
        for ring in c.rings:
            centre, normal, radius = collide.ring_frame(ring)
            got = collide.sweep_torus(c.pos, end, centre, normal, radius, collide.RING_TUBE, 0.0)
            if got is not None and got[0] * LASER_MAX_M < best:
                best, hit = got[0] * LASER_MAX_M, "ring"
        if hit is None or best < LASER_MIN_M:
            self.laser_range = None
            self.laser_hit = None
            self.target = None
            return
        self.laser_range = best
        point = (c.pos[0] + d[0] * best, c.pos[1] + d[1] * best, c.pos[2] + d[2] * best)
        self.laser_hit = point
        lat, lon = geo.offset_geo(c.lat, c.lon, point[0] - c.pos[0], point[1] - c.pos[1])
        self.target = {
            "lat": round(lat, 7),
            "lon": round(lon, 7),
            "alt": round(c.alt + point[2] - c.pos[2], 2),
        }

    def _track_error(self) -> None:
        if self.tgt[0] != "geo" or self.tracking["state"] != "locked":
            return
        desired = self._desired()
        if desired is None:
            return
        dyaw = wrap180(desired[0] - self.yaw)
        self.tracking["x_deg"] = round(dyaw * math.cos(math.radians(self.pitch)), 2)
        self.tracking["y_deg"] = round(-(desired[1] - self.pitch), 2)

    # ------------------------------------------------------------------ view

    def fov(self) -> tuple[float, float]:
        return fov_deg(self.lens, self.zoom)

    def snapshot(self) -> dict[str, Any]:
        """The raw state ``Gimbal.view`` rounds and completes."""
        tracking = dict(self.tracking)
        tracking["box_px"] = [BOX_PX, BOX_PX] if tracking["state"] != "off" else [0, 0]
        return {
            "yaw_deg": self.yaw,
            "pitch_deg": self.pitch,
            "roll_deg": 0.0,
            "yaw_dps": self.yaw_dps,
            "pitch_dps": self.pitch_dps,
            "mode": self.mode,
            "lens": self.lens,
            "pip": self.pip,
            "zoom": self.zoom,
            "digital_zoom": self.digital_zoom,
            "palette": self.palette,
            "tracking": tracking,
            "laser": {"on": self.laser_on, "range_m": self.laser_range},
            "recording": self.recording,
            "photos": self.photos,
            "motors": self.motors,
            "enhance": self.enhance,
            "osd": self.osd,
            "low_light": self.low_light,
            "selftest": {"done": self.selftest_done, "gyro_ok": True, "servo_ok": True, "image_ok": True},
            "target": self.target,
            "focus": dict(self.focus),
            "t": self._t,
        }
