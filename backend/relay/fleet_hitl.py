"""Serial flight controllers and PX4 hardware in the loop through the fleet service (11 §7, §10).

A mixin for ``FleetService``:

* serial ports: ``GET /serial/ports`` (``serial_ports``) and ``POST /vehicles {serial: {device, baud}}``
  (``connect_serial``). The port is opened and probed for an autopilot heartbeat on the calling (API) thread
  (up to 2 s per rate), then the aircraft joins the fleet with a ``SerialDriver``. Its id is ``usb-<port
  name>``; labels and stick calibration are stored under ``usb:<USB serial number>`` (or
  ``serial:<device>``), so a board keeps them across ports. ``DELETE /vehicles/{id}`` closes the port.
* hardware in the loop: ``GET/POST /vehicles/{id}/hitl`` (``hitl_status``, ``hitl_action``); the flow
  itself is ``twin.hitl.HitlSession``, ticked here every fleet tick (``_hitl_step``) and fed the aircraft's
  messages (``_hitl_message``). ``_hitl_housekeeping`` finds a restore owed from an earlier session the
  first time a board is online on each port opening, and starts it on its own (11 §7.2 step 5).

Who may start or stop: the operator, or an agent session while app control is on (the route's
dependency); in either case the operator must possess the aircraft for ``start`` (someone is at the
sticks). Stopping and the automatic restore need no possession.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .authority import Actor
from .errors import RelayError, err
from .mav import modes
from .mav import serial_link as sl
from .model import Calibration, Signs
from .twin import hitl as hitlmod
from .twin import identity as ident
from .twin import profile as tprofile
from .twin.autopilot import DEFAULT_HOME

log = logging.getLogger("relay.fleet")

CONFIRM_WORD = "HITL"
SLUG = re.compile(r"[^A-Za-z0-9]+")

UNAVAILABLE = {
    "sim": (
        "hardware in the loop runs a real flight controller; a simulator already flies in Relay's physics",
        "硬件在环针对真实飞控；模拟机本身已在 Relay 的物理模型中飞行",
    ),
    "unidentified": ("the aircraft's firmware is not identified yet", "尚未识别飞机的固件"),
    "ardupilot": (
        "ArduPilot removed its hardware-in-the-loop support and the firmware cannot be changed; "
        "use a firmware twin of this aircraft (its firmware version and parameters in Relay's physics)",
        "ArduPilot 已移除硬件在环（HIL）功能，且飞控固件不能修改；请改用该飞机的固件孪生"
        "（同版本固件与该机参数在 Relay 物理模型中运行）",
    ),
    "other_firmware": ("hardware in the loop supports PX4 only", "硬件在环仅支持 PX4"),
    "not_serial": (
        "connect the flight controller over USB: a Wi-Fi link cannot carry the 250 Hz sensor stream",
        "须经 USB 串口连接飞控：Wi-Fi 数传无法承载 250 Hz 的传感器数据",
    ),
    "busy": ("hardware in the loop is already active on this aircraft", "这架飞机的硬件在环正在进行"),
    "pending": (
        "a parameter restore from an earlier session is owed; it runs when the link is ready",
        "上次会话的参数恢复尚未完成，链路就绪后自动执行",
    ),
    "link": ("the link to the aircraft is not ready", "与飞机的链路未就绪"),
    "armed": ("the aircraft is armed; disarm it first", "飞机已解锁，须先上锁"),
    "possession": (
        "the operator must possess the aircraft (take the sticks) first",
        "须由操作员先附身这架飞机（接管摇杆）",
    ),
}


def _serial_error(exc: sl.SerialUnavailable, device: str) -> RelayError:
    return RelayError(
        "serial_unavailable",
        status=409 if exc.cause == "busy" else 400,
        detail={"device": device, "cause": exc.cause, "reason": exc.reason, "reason_zh": exc.reason_zh},
    )


def _unavailable(key: str) -> RelayError:
    en, zh = UNAVAILABLE[key]
    return err("hitl_unavailable", reason=en, reason_zh=zh, cause=key)


def _idle_view() -> dict[str, Any]:
    return {
        "state": "idle",
        "steps": [
            {"key": k, "state": "pending", "detail": "waiting", "detail_zh": hitlmod.zh("waiting")}
            for k in hitlmod.STEP_KEYS
        ],
        "backup": None,
        "changed": {},
        "mismatches": [],
        "volatile": [],
        "error": None,
        "run": None,
        "pending_restore": False,
    }


class HitlOps:
    def _hitl_setup(self) -> None:
        data: Path | None = self.data_dir  # type: ignore[attr-defined]
        # Without a data directory (tests, throwaway runs) backups still go to disk, in a temporary directory
        # chosen when the first session starts (sandboxed judge workers have no temporary directory at all).
        self.hitl_dir: Path | None = data / "hitl-backups" if data is not None else None
        self.by_serial: dict[str, str] = {}
        self.serial_opener: sl.Opener = sl.open_port  # test seam: a fake port
        self.serial_probe_s: float | None = None  # test seam: shorter heartbeat wait per rate
        self._hitl_pending_cache: dict[str, tuple[Any, list[Path]]] = {}

    # ------------------------------------------------------------------ serial ports

    def serial_ports(self) -> dict[str, Any]:
        """``GET /serial/ports``."""
        out = sl.list_ports()
        connected = self.call(self._serial_devices)  # type: ignore[attr-defined]
        for port in out["ports"]:
            port["vehicle"] = connected.get(port["device"])
        return out

    def connect_serial(self, device: str, baud: int | str = "auto") -> dict[str, Any]:
        """``POST /vehicles {serial}``: open, wait for an autopilot heartbeat, add the aircraft."""
        device = str(device or "").strip()
        if not device or len(device) > 256 or "\x00" in device or not sl.is_port_name(device):
            raise err("invalid_request", reason="serial.device must name a serial port")
        bad_rate = isinstance(baud, bool) or not isinstance(baud, int) or baud not in sl.BAUD_RATES
        if baud != "auto" and bad_rate:
            rates = ", ".join(str(r) for r in sl.BAUD_RATES)
            raise err("invalid_request", reason=f"serial.baud must be auto or one of {rates}")
        existing = self.call(lambda: self._serial_devices().get(device))  # type: ignore[attr-defined]
        if existing is not None:
            return {"id": existing}
        info = sl.find_port(device)
        usb = None
        if info is not None and info.get("vid") is not None:
            usb = {k: info.get(k) for k in ("vid", "pid", "serial_number")}
        cdc = info is not None and info.get("autopilot") and info.get("vid") is not None
        try:
            port, rate, _beat = sl.probe(
                device, baud, opener=self.serial_opener, cdc=bool(cdc), seconds=self.serial_probe_s
            )
        except sl.SerialUnavailable as exc:
            raise _serial_error(exc, device) from None

        def run() -> dict[str, Any]:
            vid = self._serial_devices().get(device)
            if vid is not None:
                sl._close(port)
                return {"id": vid}
            link = sl.SerialLink(device, rate, port, opener=self.serial_opener, usb=usb, clock=self.clock)  # type: ignore[attr-defined]
            v = self._add_serial(link, usb)
            v.driver.heartbeat()
            return {"id": v.id}

        try:
            return self.call(run)  # type: ignore[attr-defined]
        except BaseException:
            sl._close(port)
            raise

    def _serial_devices(self) -> dict[str, str]:
        """Device name -> aircraft, under the name it was connected with and its current one."""
        out = dict(self.by_serial)
        for v in self.vehicles.values():  # type: ignore[attr-defined]
            link = self._serial_link(v)
            if link is not None:
                out[link.device] = v.id
        return out

    def _serial_id(self, device: str) -> str:
        base = device.replace("\\", "/").rsplit("/", 1)[-1] or device
        slug = SLUG.sub("-", base).strip("-")[:32] or "port"
        vid = f"usb-{slug}"
        n = 2
        while vid in self.vehicles:  # type: ignore[attr-defined]
            vid = f"usb-{slug}-{n}"
            n += 1
        return vid

    def _add_serial(self, link: sl.SerialLink, usb: dict[str, Any] | None) -> Any:
        from .drivers.serial import SerialDriver
        from .fleet import Vehicle

        device = link.device
        vid = self._serial_id(device)
        number = (usb or {}).get("serial_number")
        key = f"usb:{number}" if number else f"serial:{device}"
        base = device.replace("\\", "/").rsplit("/", 1)[-1]
        label = self._labels.get(key) or f"USB {base}"  # type: ignore[attr-defined]
        v = Vehicle(vid, "real", label, SerialDriver(link))
        v.store_key = key
        self._attach(v)  # type: ignore[attr-defined]
        v.created = self.clock()  # type: ignore[attr-defined]
        stored = self.history.calibration(key)  # type: ignore[attr-defined]
        if stored is not None:
            signs, hover = stored
            v.calibration = Calibration(
                Signs(**{k: int(signs.get(k, 1)) for k in ("roll", "pitch", "yaw")}), hover
            )
        self.vehicles[vid] = v  # type: ignore[attr-defined]
        self.by_serial[device] = vid
        self.events.emit(  # type: ignore[attr-defined]
            "vehicle_discovered",
            vid,
            data={"host": None, "via": "USB", "label": label, "device": device, "baud": link.baud},
            labels={"vehicle": label},
        )
        return v

    def _serial_link(self, v: Any) -> sl.SerialLink | None:
        driver = v.driver
        return driver.link if getattr(driver, "transport", None) == "serial" else None

    def _transport(self, v: Any) -> str:
        if v.kind == "sim":
            return "sim"
        return "serial" if self._serial_link(v) is not None else "udp"

    def _serial_view(self, v: Any) -> dict[str, Any] | None:
        link = self._serial_link(v)
        return None if link is None else link.view()

    def _serial_removed(self, v: Any) -> None:
        link = self._serial_link(v)
        if link is None:
            return
        session = getattr(v, "hitl", None)
        if session is not None and session.active:
            self._hitl_notice(
                v,
                "hitl_failed",
                {
                    "step": session._current_step(),
                    "reason_en": "the aircraft was removed; the restore runs when it connects again",
                    "reason_zh": "飞机已移除；下次连接时自动恢复参数",
                },
            )
        link.close()
        for device, vid in list(self.by_serial.items()):
            if vid == v.id:
                del self.by_serial[device]

    def _serial_shutdown(self) -> None:
        for v in list(self.vehicles.values()):  # type: ignore[attr-defined]
            link = self._serial_link(v)
            if link is not None:
                link.close()

    # ------------------------------------------------------------------ hardware in the loop (11 §7)

    def _hitl_check(self, v: Any, pending: bool) -> str | None:
        """Why ``start`` is not possible now, as an UNAVAILABLE key, or None."""
        if v.kind != "real":
            return "sim"
        if v.autopilot is None:
            return "unidentified"
        if v.autopilot == modes.ARDUPILOT:
            return "ardupilot"
        if v.autopilot != modes.PX4:
            return "other_firmware"
        if self._serial_link(v) is None:
            return "not_serial"
        session = getattr(v, "hitl", None)
        if session is not None and session.active:
            return "busy"
        if pending:
            return "pending"
        if v.link_state not in ("ok", "delayed"):
            return "link"
        if v.armed:
            return "armed"
        if v.auth.possession is None:
            return "possession"
        return None

    def _board(self, v: Any) -> dict[str, Any]:
        link = self._serial_link(v)
        usb = (link.usb if link is not None else None) or {}
        return {
            "device": None if link is None else link.device,
            "vid": usb.get("vid"),
            "pid": usb.get("pid"),
            "serial_number": usb.get("serial_number"),
        }

    def _hitl_pending(self, v: Any) -> list[Path]:
        if self._serial_link(v) is None:
            return []
        match = {**self._board(v), "vehicle": v.id}
        if self.hitl_dir is None:
            return []
        try:
            stamp = self.hitl_dir.stat().st_mtime_ns  # a file written or replaced changes the directory
        except OSError:
            return []
        key = (stamp, tuple(sorted((k, str(x)) for k, x in match.items())))
        cached = self._hitl_pending_cache.get(v.id)
        if cached is not None and cached[0] == key:
            return cached[1]
        found = hitlmod.pending_backups(self.hitl_dir, match)
        self._hitl_pending_cache[v.id] = (key, found)
        return found

    def _hitl_view(self, v: Any) -> dict[str, Any]:
        session = getattr(v, "hitl", None)
        view = session.view() if session is not None else _idle_view()
        pending = view["pending_restore"] or (
            (session is None or not session.active) and bool(self._hitl_pending(v))
        )
        key = self._hitl_check(v, pending)
        reason = None if key is None else UNAVAILABLE[key]
        return {
            "id": v.id,
            "available": key is None,
            "reason": None if reason is None else reason[0],
            "reason_zh": None if reason is None else reason[1],
            "cause": key,
            **view,
            "pending_restore": bool(pending),
        }

    def hitl_status(self, vid: str) -> dict[str, Any]:
        """``GET /vehicles/{id}/hitl``."""
        return self.call(lambda: self._hitl_view(self._vehicle(vid)))  # type: ignore[attr-defined]

    def hitl_action(self, vid: str, action: str, confirm: str | None, actor: Actor) -> dict[str, Any]:
        """``POST /vehicles/{id}/hitl {action: start | stop, confirm}``."""
        if action not in ("start", "stop"):
            raise err("invalid_request", reason="action must be start or stop")

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)  # type: ignore[attr-defined]
            now = self.clock()  # type: ignore[attr-defined]
            wall = self.wall()  # type: ignore[attr-defined]
            session = getattr(v, "hitl", None)
            if action == "stop":
                if session is not None and session.active:
                    session.stop(now)
                elif self._serial_link(v) is not None and v.kind == "real":
                    self._hitl_resume(v, now, wall)  # a restore owed and not running: run it now
                return self._hitl_view(v)
            pending = bool(self._hitl_pending(v)) and not (session is not None and session.active)
            key = self._hitl_check(v, pending)
            if key is not None:
                raise _unavailable(key)
            if confirm != CONFIRM_WORD:
                raise err("confirm_required", expected=CONFIRM_WORD)
            session = self._new_hitl(v, now, wall)
            session.start(now)
            log.info("hitl start on %s by %s", v.id, actor.kind)
            return self._hitl_view(v)

        return self.call(run)  # type: ignore[attr-defined]

    def _hitl_backup_dir(self) -> Path:
        if self.hitl_dir is None:
            self.hitl_dir = Path(tempfile.gettempdir()) / f"relay-hitl-backups-{os.getpid()}"
        return self.hitl_dir

    def _new_hitl(self, v: Any, now: float, wall: float) -> hitlmod.HitlSession:
        session = hitlmod.HitlSession(
            v,
            self._hitl_backup_dir(),
            notify=lambda kind, data, v=v: self._hitl_notice(v, kind, data),
            body_factory=lambda params, v=v: self._hitl_body(v, params),
            board=self._board(v),
            now=now,
            wall=wall,
        )
        v.hitl = session
        return session

    def _hitl_resume(self, v: Any, now: float, wall: float) -> bool:
        """Start the newest owed restore of this board, if any."""
        for path in self._hitl_pending(v):
            data = hitlmod.read_backup(path)
            if data is None:
                continue
            session = self._new_hitl(v, now, wall)
            session.resume(path, data, now)
            log.info("hitl restore owed by %s resumed from %s", v.id, path.name)
            return True
        return False

    def _hitl_body(self, v: Any, params: dict[str, dict[str, Any]]) -> hitlmod.HitlBody:
        """The physics the board flies in: the aircraft's frame guessed from its battery parameters (the
        twin's rule, 11 §5), the hover thrust from its ``MPC_THR_HOVER``, home at its last position."""
        values = {name: float(e["value"]) for name, e in params.items()}
        cells = int(values.get("BAT1_N_CELLS", 0) or 0) or v.cells()
        capacity = values.get("BAT1_CAPACITY")
        capacity = capacity if capacity is not None and capacity > 0 else None
        spec: dict[str, Any] = {"preset": tprofile.guess_preset(cells, capacity)}
        try:
            full = tprofile.normalise_frame(
                {
                    **spec,
                    **({"cells": cells} if cells else {}),
                    **({"capacity_mah": capacity} if capacity else {}),
                }
            )
            frame = tprofile.frame_model(full, values, "px4")
        except (tprofile.ProfileError, ValueError):
            frame = tprofile.frame_model(tprofile.normalise_frame(spec), values, "px4")
        home = DEFAULT_HOME
        glob = v.state.get("global")
        if glob and glob.get("valid"):
            alt = v.home_amsl()
            home = (float(glob["lat"]), float(glob["lon"]), float(alt if alt is not None else glob["alt"]))
        return hitlmod.HitlBody(frame, home, v.id)

    def _hitl_firmware(self, v: Any) -> dict[str, Any]:
        info = ident.identify(v.state)
        return {"family": "px4", "version": info.get("version")}

    def _hitl_notice(self, v: Any, kind: str, data: dict[str, Any]) -> None:
        """A session notification, on the fleet thread: events, crashes and the physics reset."""
        labels = {"vehicle": v.label}
        if kind == "hitl_started":
            fw = self._hitl_firmware(v)
            self.events.emit(  # type: ignore[attr-defined]
                "hitl_started",
                v.id,
                data={"vehicle": v.id, **fw, "backup": data.get("backup")},
                labels={**labels, "firmware": f"PX4 {fw['version'] or ''}".strip()},
            )
        elif kind == "hitl_restored":
            mismatches = data.get("mismatches") or []
            self.events.emit(  # type: ignore[attr-defined]
                "hitl_restored",
                v.id,
                data={
                    "vehicle": v.id,
                    "restored": data.get("restored") or [],
                    "mismatches": mismatches,
                    "detail_en": f"; {len(mismatches)} parameters differ from the backup"
                    if mismatches
                    else "",
                    "detail_zh": f"；{len(mismatches)} 项参数与备份不一致" if mismatches else "",
                },
                labels=labels,
            )
        elif kind == "hitl_failed":
            self.events.emit(  # type: ignore[attr-defined]
                "hitl_failed",
                v.id,
                data={
                    "vehicle": v.id,
                    "step": data.get("step"),
                    "step_zh": hitlmod.STEP_ZH.get(str(data.get("step")), str(data.get("step"))),
                    "reason_en": data.get("reason_en"),
                    "reason_zh": data.get("reason_zh"),
                },
                labels=labels,
            )
        elif kind == "crash":
            v.state["crashed"] = True
            self._on_crash(v, str(data.get("text") or "Crash"), self.clock())  # type: ignore[attr-defined]
        elif kind == "reset":
            v.state["crashed"] = False
            v.crash_event = False

    def _hitl_step(self, now: float) -> None:
        """Every fleet tick: advance the sessions and send their HIL frames."""
        wall = None
        for v in list(self.vehicles.values()):  # type: ignore[attr-defined]
            session = getattr(v, "hitl", None)
            if session is None or not session.active:
                continue
            if wall is None:
                wall = self.wall()  # type: ignore[attr-defined]
            try:
                data = session.tick(now, wall)
            except Exception:
                log.exception("hardware in the loop failed on %s", v.id)
                continue
            if data:
                v.driver.write_raw(data)

    def _hitl_message(self, v: Any, msg: Any, now: float) -> None:
        session = getattr(v, "hitl", None)
        if session is not None and session.active:
            session.on_message(msg, now)

    def _hitl_housekeeping(self, now: float) -> None:
        """An owed restore starts on its own the first time a board is online after its port opened."""
        for v in list(self.vehicles.values()):  # type: ignore[attr-defined]
            link = self._serial_link(v)
            if link is None or v.autopilot != modes.PX4 or v.link_state != "ok":
                continue
            if getattr(v, "hitl_checked", None) == link.generation:
                continue
            v.hitl_checked = link.generation
            session = getattr(v, "hitl", None)
            if session is not None and session.active:
                continue
            self._hitl_resume(v, now, self.wall())  # type: ignore[attr-defined]

    def _hitl_snapshot(self, v: Any) -> dict[str, Any] | None:
        """The snapshot's ``hitl`` block: null without a session."""
        session = getattr(v, "hitl", None)
        if session is None or session.state == "idle":
            return None
        fw = self._hitl_firmware(v)
        name = f"PX4 {fw['version']}" if fw["version"] else "PX4"
        body = session.body
        return {
            "state": session.state,
            "engine": "hitl",
            "label_en": f"Hardware in the loop {name}",
            "label_zh": f"硬件在环 {name}",
            "firmware": fw,
            "pending_restore": session.pending_restore,
            "pose": body.pose() if body is not None and session.state == "running" else None,
        }

    def _hitl_overlay(self, v: Any, view: dict[str, Any], now: float) -> None:
        """While a session runs, the stage shows the physics: position, attitude and rates are the truth
        the board is fed, not its estimate of them."""
        session = getattr(v, "hitl", None)
        if session is None or session.state != "running" or session.body is None:
            return
        s = session.body.quad.s
        view["position"] = {
            "frame": "local",
            "x": round(s.p[0], 3),
            "y": round(s.p[1], 3),
            "z": round(s.p[2], 3),
            "vx": round(s.v[0], 3),
            "vy": round(s.v[1], 3),
            "vz": round(s.v[2], 3),
            "age_s": 0.0,
        }
        view["attitude"] = {"roll": round(s.roll, 4), "pitch": round(s.pitch, 4), "yaw": round(s.yaw, 4)}
        view["rates"] = {
            "roll": round(s.rates[0], 4),
            "pitch": round(s.rates[1], 4),
            "yaw": round(s.rates[2], 4),
        }
        view["crashed"] = bool(view["crashed"] or s.crashed)


__all__ = ["CONFIRM_WORD", "HitlOps"]
