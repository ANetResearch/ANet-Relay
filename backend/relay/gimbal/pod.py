"""The ``Gimbal`` interface and its two pods (12 §5): ``SimPod`` (simulated) and ``DytPod`` (a real DYT pod).

Both are driven by the fleet thread only: ``command(action, params, now)`` (validated here, once, for both
pods), ``step(now, carrier)`` (once per fleet tick, ``carrier`` is the aircraft's pose) and ``view(now)``,
which returns exactly the ``GimbalView`` of 12 §5.1 as a dict. Each pod keeps a capture log (photos and
recording on / off, with the aircraft's ``nav`` pose, the gimbal angles, the lens, zoom, laser range and
target) and a queue of events (``take_events``: ``capture``, ``record``, ``lens``, ``link``) which the fleet
turns into bus events.

``DytPod`` sends the control frames of ``dyt`` through a ``DytLink`` and builds its view from the telemetry
the pod streams back; the things the pod does not report (palette, OSD, low-light, digital zoom, focus) are
what Relay last commanded. ``look_at`` is not a pod action: the fleet solves the angles and sends ``angle``.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections import deque
from typing import Any

from ..errors import err
from . import dyt
from .carrier import Carrier, wrap180
from .lens import IMAGE_PX, LENS_TABLE, LENSES, fov_deg, zoom_range
from .link import DytLink
from .sim import SimGimbal

ACTIONS = (
    "rate", "stop", "angle", "center", "follow", "lock", "motors", "zoom", "zoom_rate", "digital_zoom",
    "lens", "palette", "photo", "record", "track", "track_stop", "laser", "focus", "enhance", "osd",
    "low_light", "scan", "scan_stop", "geo_track", "look_at",
)  # fmt: skip
# actions that need the motors on and a finished self-test
MOTION = frozenset({"rate", "angle", "center", "follow", "lock", "track", "scan", "geo_track"})
PALETTES = ("white_hot", "black_hot", "pseudo")
ALGORITHMS = tuple(dyt.ALGORITHMS)
CAPTURE_LOG = 500
ATTITUDE_HZ = 10.0


def _bad(reason: str) -> Exception:
    return err("invalid_request", reason=reason)


def _num(p: dict[str, Any], key: str, lo: float, hi: float, default: float | None = None) -> float:
    v = p.get(key)
    if v is None:
        if default is None:
            raise _bad(f"{key} is required")
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise _bad(f"{key} must be a number")
    if not lo <= v <= hi:
        raise _bad(f"{key} must be between {lo:g} and {hi:g}")
    return float(v)


def _on(p: dict[str, Any]) -> bool:
    v = p.get("on")
    if not isinstance(v, bool):
        raise _bad("on must be true or false")
    return v


def _choice(p: dict[str, Any], key: str, options: tuple[str, ...], default: str | None = None) -> str:
    v = p.get(key, default)
    if v not in options:
        raise _bad(f"{key} must be one of {', '.join(options)}")
    return str(v)


def normalise(action: str, params: dict[str, Any], lens: str) -> dict[str, Any]:
    """Validate ``params`` of ``action`` and return the parameters the pods execute with. ``lens`` is the lens
    in use (zoom limits depend on it). Raises ``invalid_request`` or ``gimbal_unsupported``."""
    p = params
    if action in ("stop", "center", "photo", "track_stop", "scan_stop"):
        return {}
    if action == "rate":
        return {
            "yaw_dps": _num(p, "yaw_dps", -100, 100, 0.0),
            "pitch_dps": _num(p, "pitch_dps", -100, 100, 0.0),
        }
    if action == "angle":
        space = _choice(p, "space", ("frame", "earth"), "frame")
        yaw = _num(p, "yaw_deg", -180, 180)
        pitch = _num(p, "pitch_deg", -120, 90)
        if space == "earth" and pitch < -90:
            raise _bad("pitch_deg must be between -90 and 90 in earth space")
        return {"yaw_deg": yaw, "pitch_deg": pitch, "space": space}
    if action in (
        "follow",
        "lock",
        "motors",
        "digital_zoom",
        "record",
        "laser",
        "enhance",
        "osd",
        "low_light",
    ):
        return {"on": _on(p)}
    if action == "zoom":
        lo, hi = zoom_range(lens)
        level = _num(p, "level", 0.0, 1000.0)
        if lo == hi:
            raise err("gimbal_unsupported", reason=f"the {lens} lens has a fixed field of view",
                      reason_zh=f"{lens} 镜头视场固定，不能变焦")  # fmt: skip
        if not lo <= level <= hi:
            raise _bad(f"level must be between {lo:g} and {hi:g} on {lens}")
        return {"level": level}
    if action == "zoom_rate":
        rate = _num(p, "rate", -100, 100)
        if rate != 0 and LENS_TABLE[lens].fixed:
            raise err("gimbal_unsupported", reason=f"the {lens} lens has a fixed field of view",
                      reason_zh=f"{lens} 镜头视场固定，不能变焦")  # fmt: skip
        return {"rate": rate}
    if action == "lens":
        name = _choice(p, "lens", LENSES)
        pip = p.get("pip")
        if pip is not None and pip not in LENSES:
            raise _bad(f"pip must be null or one of {', '.join(LENSES)}")
        return {"lens": name, "pip": None if pip == name else pip}
    if action == "palette":
        return {"palette": _choice(p, "palette", PALETTES)}
    if action == "track":
        out: dict[str, Any] = {
            "x": _num(p, "x", -IMAGE_PX[0] / 2, IMAGE_PX[0] / 2),
            "y": _num(p, "y", -IMAGE_PX[1] / 2, IMAGE_PX[1] / 2),
            "algorithm": None,
        }
        if p.get("algorithm") is not None:
            out["algorithm"] = _choice(p, "algorithm", ALGORITHMS)
        return out
    if action == "focus":
        mode = _choice(p, "mode", ("auto", "manual"))
        if mode == "manual":
            return {"mode": mode, "position": int(_num(p, "position", 4090, 49152))}
        return {"mode": mode, "position": None}
    if action == "scan":
        return {
            "yaw_deg": _num(p, "yaw_deg", -180, 180),
            "pitch_deg": _num(p, "pitch_deg", -120, 90),
            "speed_dps": _num(p, "speed_dps", 0.2, 51.0),
        }
    if action == "geo_track":
        if p.get("mode") is not None:
            return {"mode": _choice(p, "mode", ("center", "exit"))}
        return {
            "lat": _num(p, "lat", -90, 90),
            "lon": _num(p, "lon", -180, 180),
            "alt": _num(p, "alt", -6000, 6000),
        }
    if action == "look_at":
        raise _bad("look_at is solved by Relay and sent as angle")
    raise _bad(f"unknown action {action}")


def _r(v: float | None, digits: int = 2) -> float | None:
    return None if v is None else round(v, digits)


class Gimbal(ABC):
    """What the fleet sees of a pod."""

    kind = "sim"

    def __init__(self) -> None:
        self.captures: deque[dict[str, Any]] = deque(maxlen=CAPTURE_LOG)
        self._events: list[tuple[str, dict[str, Any]]] = []
        self._carrier: Carrier | None = None
        self._cap_seq = 0

    # -- to implement
    @abstractmethod
    def _state(self, now: float) -> dict[str, Any]: ...

    @abstractmethod
    def _execute(self, action: str, p: dict[str, Any], now: float) -> None: ...

    @abstractmethod
    def _tick(self, now: float, carrier: Carrier) -> None: ...

    def _link(self) -> str:
        return "ok"

    def _address(self) -> str | None:
        return None

    def _debug(self) -> dict[str, Any] | None:
        return None

    def _age(self, now: float, st: dict[str, Any]) -> float | None:
        return 0.0

    def close(self) -> None:
        return None

    # -- common
    def command(self, action: str, params: dict[str, Any] | None, now: float) -> None:
        st = self._state(now)
        p = normalise(action, dict(params or {}), st["lens"])
        if action in MOTION and not (action == "geo_track" and p.get("mode") == "exit"):
            if not st["motors"]:
                raise err("gimbal_busy", reason="the gimbal motors are off", reason_zh="吊舱电机已关闭")
            if not st["selftest"]["done"]:
                raise err("gimbal_busy", reason="the self-test is still running", reason_zh="吊舱正在自检")
        self._execute(action, p, now)

    def step(self, now: float, carrier: Carrier) -> None:
        self._carrier = carrier
        self._tick(now, carrier)

    def take_events(self) -> list[tuple[str, dict[str, Any]]]:
        events, self._events = self._events, []
        return events

    def _capture(self, kind: str, now: float, **extra: Any) -> dict[str, Any]:
        """Log a photo or a recording change, with everything known about the moment."""
        st = self._state(now)
        c = self._carrier
        self._cap_seq += 1
        entry = {
            "seq": self._cap_seq,
            "kind": kind,  # photo | record_start | record_stop
            "t": c.wall if c is not None else 0.0,
            "nav": c.nav() if c is not None else None,
            "gimbal": {k: _r(st[k]) for k in ("yaw_deg", "pitch_deg", "roll_deg")},
            "lens": st["lens"],
            "zoom": _r(st["zoom"], 1),
            "laser_range_m": _r(st["laser"]["range_m"], 1),
            "target": st["target"],
            **extra,
        }
        self.captures.append(entry)
        return entry

    def view(self, now: float) -> dict[str, Any]:
        s = self._state(now)
        lens, zoom = s["lens"], s["zoom"]
        lo, hi = zoom_range(lens)
        tracking = s["tracking"]
        return {
            "kind": self.kind,
            "link": self._link(),
            "address": self._address(),
            "yaw_deg": _r(s["yaw_deg"]),
            "pitch_deg": _r(s["pitch_deg"]),
            "roll_deg": _r(s["roll_deg"]),
            "yaw_dps": _r(s["yaw_dps"]),
            "pitch_dps": _r(s["pitch_dps"]),
            "mode": s["mode"],
            "lens": lens,
            "pip": s["pip"],
            "zoom": round(zoom, 2),
            "zoom_range": [lo, hi],
            "digital_zoom": s["digital_zoom"],
            "fov_deg": list(fov_deg(lens, zoom)),
            "palette": s["palette"],
            "tracking": {
                "state": tracking["state"],
                "algorithm": tracking["algorithm"],
                "x_deg": _r(tracking["x_deg"]),
                "y_deg": _r(tracking["y_deg"]),
                "box_px": list(tracking["box_px"]),
            },
            "laser": {"on": s["laser"]["on"], "range_m": _r(s["laser"]["range_m"], 1)},
            "recording": s["recording"],
            "photos": s["photos"],
            "motors": s["motors"],
            "enhance": s["enhance"],
            "osd": s["osd"],
            "low_light": s["low_light"],
            "selftest": dict(s["selftest"]),
            "target": s["target"],
            "focus": dict(s["focus"]),
            "debug": self._debug(),
            "age_s": _r(self._age(now, s), 3),
        }


# ---------------------------------------------------------------------------- the simulated pod


class SimPod(Gimbal):
    kind = "sim"

    def __init__(self, sim: SimGimbal | None = None, **options: Any) -> None:
        super().__init__()
        self.sim = sim or SimGimbal(**options)

    def _state(self, now: float) -> dict[str, Any]:
        return self.sim.snapshot()

    def _age(self, now: float, st: dict[str, Any]) -> float | None:
        return 0.0 if st["t"] is None else max(0.0, now - st["t"])

    def _tick(self, now: float, carrier: Carrier) -> None:
        self.sim.step(now, carrier)

    def _execute(self, action: str, p: dict[str, Any], now: float) -> None:
        for kind, data in self.sim.execute(action, p, now):
            if kind == "photo":
                entry = self._capture("photo", now, index=data["index"])
                self._events.append(("capture", entry))
            elif kind == "record":
                entry = self._capture("record_start" if data["on"] else "record_stop", now)
                self._events.append(("record", {"on": data["on"], "capture": entry["seq"]}))
            elif kind == "lens":
                self._events.append(("lens", data))


# ---------------------------------------------------------------------------- the real pod


class DytPod(Gimbal):
    kind = "dyt"

    def __init__(self, link: DytLink) -> None:
        super().__init__()
        self.link = link
        self.tel: dyt.Telemetry | None = None
        self.target: dict[str, float] | None = None
        self.last_status: dyt.Status | None = None
        self.hint: str | None = None  # rate | angle | track | scan: the last motion command
        self.photos = 0
        self.palette = "white_hot"
        self.osd = True
        self.low_light = False
        self.digital_zoom = False
        self.focus: dict[str, Any] = {"mode": "auto", "position": None}
        self.algorithm = "auto"
        self._att_t = -1e9
        self._reported_link: str | None = None
        link.start()

    def close(self) -> None:
        self.link.close(timeout=0.5)  # called on the fleet thread: do not wait for a slow connect

    def _link(self) -> str:
        return self.link.state

    def _address(self) -> str | None:
        return self.link.address

    def _debug(self) -> dict[str, Any]:
        p = self.link.parser
        return {
            "frames": p.frames,
            "bad_frames": p.bad,
            "skipped_bytes": p.skipped,
            "rx_bytes": self.link.rx_bytes,
            "tx_frames": self.link.tx_frames,
            "connects": self.link.connects,
            "error": self.link.error,
        }

    def _age(self, now: float, st: dict[str, Any]) -> float | None:
        return self.link.age_s

    def _state(self, now: float) -> dict[str, Any]:
        t = self.tel or dyt.Telemetry()
        if not t.motors:
            mode = "off"
        elif self.hint is not None:
            mode = self.hint
        else:
            mode = "follow" if t.follow and not t.lock else "lock"
        tracking_on = self.hint == "track"
        state = "off"
        if tracking_on and t.motors:
            state = "locked" if t.locked else "searching"
        small = t.small if t.small != t.big else None
        return {
            "yaw_deg": t.yaw_deg,
            "pitch_deg": t.pitch_deg,
            "roll_deg": t.roll_deg,
            "yaw_dps": t.yaw_dps,
            "pitch_dps": t.pitch_dps,
            "mode": mode,
            "lens": t.big,
            "pip": small,
            "zoom": t.zoom,
            "digital_zoom": self.digital_zoom,
            "palette": self.palette,
            "tracking": {
                "state": state,
                "algorithm": t.track_algorithm if state == "locked" else self.algorithm,
                "x_deg": t.miss_x_deg if state != "off" else 0.0,
                "y_deg": t.miss_y_deg if state != "off" else 0.0,
                "box_px": [t.gate_w_px, t.gate_h_px] if state != "off" else [0, 0],
            },
            "laser": {"on": t.laser_on, "range_m": t.laser_range_m},
            "recording": t.recording,
            "photos": self.photos,
            "motors": t.motors,
            "enhance": t.enhance,
            "osd": self.osd,
            "low_light": self.low_light,
            "selftest": {
                "done": t.selftest_done,
                "gyro_ok": not t.gyro_fail,
                "servo_ok": not t.servo_error,
                "image_ok": not t.image_error,
            },
            "target": self.target,
            "focus": dict(self.focus),
        }

    # -- receive, and the carrier's attitude out
    def _tick(self, now: float, carrier: Carrier) -> None:
        for frame in self.link.poll():
            if isinstance(frame, dyt.Telemetry):
                self._on_telemetry(frame, now)
            elif isinstance(frame, dyt.TargetPosition):
                self.target = {
                    "lat": round(frame.lat, 7),
                    "lon": round(frame.lon, 7),
                    "alt": round(frame.alt_m, 2),
                }
            elif isinstance(frame, dyt.Status):
                self.last_status = frame
        link = self.link.state
        if link != self._reported_link and link in ("ok", "lost"):
            if self._reported_link is not None or link == "ok":
                self._events.append(("link", {"link": link, "error": self.link.error}))
            self._reported_link = link
        if self.link.connected and now - self._att_t >= 1.0 / ATTITUDE_HZ:
            self._att_t = now
            self.link.send(dyt.carrier_frame(self._attitude(carrier)))

    @staticmethod
    def _attitude(c: Carrier) -> dyt.CarrierAttitude:
        return dyt.CarrierAttitude(
            roll_deg=math.degrees(c.roll),
            pitch_deg=math.degrees(c.pitch),
            yaw_deg=wrap180(math.degrees(c.yaw)),
            lat=c.lat,
            lon=c.lon,
            alt_m=c.alt,
            rel_alt_m=c.rel_alt,
            utc=c.wall,
            airspeed=c.speed,
            groundspeed=c.speed,
        )

    def _on_telemetry(self, t: dyt.Telemetry, now: float) -> None:
        before = self.tel
        self.tel = t
        if before is None:
            return
        if (t.big, t.small if t.small != t.big else None) != (
            before.big,
            before.small if before.small != before.big else None,
        ):
            self._events.append(("lens", {"lens": t.big, "pip": t.small if t.small != t.big else None}))
        if t.recording != before.recording:
            entry = self._capture("record_start" if t.recording else "record_stop", now)
            self._events.append(("record", {"on": t.recording, "capture": entry["seq"]}))

    # -- commands
    def _execute(self, action: str, p: dict[str, Any], now: float) -> None:
        if self.link.state != "ok":
            raise err("gimbal_offline", address=self.link.address)
        send = self.link.send
        if action == "rate":
            send(dyt.rate(p["yaw_dps"], p["pitch_dps"]))
            self.hint = "rate"
        elif action == "stop":
            send(dyt.stop())
            self.hint = None
        elif action == "angle":
            send(dyt.earth_angle(p["yaw_deg"], p["pitch_deg"]) if p["space"] == "earth"
                 else dyt.frame_angle(p["yaw_deg"], p["pitch_deg"]))  # fmt: skip
            self.hint = "angle"
        elif action == "center":
            send(dyt.command(dyt.Code.CENTER))
            self.hint = None
        elif action == "follow":
            send(dyt.on_off(p["on"], dyt.Code.FOLLOW_ON, dyt.Code.FOLLOW_OFF))
            self.hint = None
        elif action == "lock":
            send(dyt.on_off(p["on"], dyt.Code.LOCK_ON, dyt.Code.LOCK_OFF))
            self.hint = None
        elif action == "motors":
            send(dyt.on_off(p["on"], dyt.Code.MOTORS_ON, dyt.Code.MOTORS_OFF))
            self.hint = None
        elif action == "zoom":
            send(dyt.zoom_to(p["level"]))
        elif action == "zoom_rate":
            send(dyt.zoom_rate(int(p["rate"])))
        elif action == "digital_zoom":
            send(dyt.on_off(p["on"], dyt.Code.DIGITAL_ZOOM_ON, dyt.Code.DIGITAL_ZOOM_OFF))
            self.digital_zoom = p["on"]
        elif action == "lens":
            send(dyt.lens(p["lens"], p["pip"]))
        elif action == "palette":
            code = {
                "white_hot": dyt.Code.WHITE_HOT,
                "black_hot": dyt.Code.BLACK_HOT,
                "pseudo": dyt.Code.PALETTE_PSEUDO,
            }
            send(dyt.command(code[p["palette"]]))
            self.palette = p["palette"]
        elif action == "photo":
            send(dyt.command(dyt.Code.PHOTO))
            self.photos += 1
            self._events.append(("capture", self._capture("photo", now, index=self.photos)))
        elif action == "record":
            send(dyt.on_off(p["on"], dyt.Code.RECORD_ON, dyt.Code.RECORD_OFF))  # the telemetry confirms it
        elif action == "track":
            if p["algorithm"] is not None:
                send(dyt.command(dyt.ALGORITHMS[p["algorithm"]]))
                self.algorithm = p["algorithm"]
            send(dyt.track_point(int(p["x"]), int(p["y"])))
            self.hint = "track"
        elif action == "track_stop":
            send(dyt.command(dyt.Code.TRACK_STOP))
            self.hint = None
        elif action == "laser":
            send(dyt.on_off(p["on"], dyt.Code.LASER_ON, dyt.Code.LASER_OFF))
        elif action == "focus":
            send(dyt.command(dyt.Code.FOCUS_MODE, 0 if p["mode"] == "auto" else 1))
            if p["mode"] == "manual":
                send(dyt.command(dyt.Code.FOCUS_POSITION, p["position"]))
            self.focus = {"mode": p["mode"], "position": p["position"]}
        elif action == "enhance":
            send(dyt.on_off(p["on"], dyt.Code.ENHANCE_ON, dyt.Code.ENHANCE_OFF))
        elif action == "osd":
            send(dyt.on_off(p["on"], dyt.Code.OSD_ON, dyt.Code.OSD_OFF))
            self.osd = p["on"]
        elif action == "low_light":
            send(dyt.on_off(p["on"], dyt.Code.LOW_LIGHT_ON, dyt.Code.LOW_LIGHT_OFF))
            self.low_light = p["on"]
        elif action == "scan":
            send(dyt.scan(p["yaw_deg"], p["pitch_deg"], p["speed_dps"]))
            self.hint = "scan"
        elif action == "scan_stop":
            send(dyt.command(dyt.Code.SCAN_STOP))
            self.hint = None
        elif action == "geo_track":
            if p.get("mode") == "exit":
                send(dyt.geo_command(dyt.GEO_EXIT))
                self.hint = None
            elif p.get("mode") == "center":
                send(dyt.geo_command(dyt.GEO_CENTER))
                self.hint = "track"
            else:
                send(dyt.geo_command(dyt.GEO_POINT, p["lat"], p["lon"], p["alt"]))
                self.hint = "track"
