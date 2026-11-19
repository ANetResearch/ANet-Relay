"""Gimbal pods on the fleet (12 §5): a mixin for ``FleetService``.

Each aircraft has at most one pod. A simulator whose ``model`` is ``anet-du7`` gets a simulated pod when it is
created (``_gimbal_autoattach``); any other simulator gets one only through ``gimbal_attach`` with transport
``sim``; a real aircraft gets a DYT pod through ``gimbal_attach`` with transport ``tcp`` or ``serial``. The
pods live in a table beside the vehicle records (``_gimbals``), so the ``Vehicle`` record is not touched.

Every fleet tick ``_gimbal_step`` builds the carrier pose (a simulator's ground truth from its physics, a real
aircraft's from its telemetry), steps the simulated pods (at most 100 Hz), lets the real pods drain their
link's frames and send the 10 Hz carrier attitude frame, and turns each pod's events into bus events. All
of it runs on the fleet thread; the real pod's socket or port belongs to its own link thread and never
blocks the tick.

Who may command: the operator, or the agent session that controls the aircraft (possession or a live claim),
at any flight level; attaching and detaching are the operator's. ``look_at`` takes a point in the request's
frame (or the ``control_frame`` setting), converts it to ENU and sends the pod the earth angles that point
at it from where the aircraft is now.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from . import authority as auth
from .authority import Actor
from .errors import err
from .gimbal import Carrier, DytPod, Gimbal, SimPod
from .gimbal import carrier as geo
from .gimbal.link import SERIAL_BAUD, TCP_PORT, DytLink
from .mav import serial_link as sl
from .sim.autopilot import HOME

log = logging.getLogger("relay.gimbal")
SIM_STEP_S = 0.01  # simulated pods step at most this often (100 Hz)
LINK_TEXT_ZH = {"ok": "已恢复", "lost": "已丢失"}


@dataclass
class _Slot:
    pod: Gimbal
    spec: dict[str, Any]
    stepped: float = -1e9


class GimbalOps:
    @property
    def _gimbals(self) -> dict[str, _Slot]:
        table = self.__dict__.get("_gimbal_table")
        if table is None:
            table = self.__dict__["_gimbal_table"] = {}
        return table

    # ------------------------------------------------------------------ lookups

    def _gimbal_slot(self, v: Any) -> _Slot:
        slot = self._gimbals.get(v.id)
        if slot is None:
            raise err("gimbal_missing", vehicle=v.label)
        return slot

    def _check_gimbal_control(self, v: Any, actor: Actor) -> None:
        auth.check_operator_or_controller(v.auth, actor, self._claim_alive(v))  # type: ignore[attr-defined]

    @staticmethod
    def _check_gimbal_operator(actor: Actor) -> None:
        if actor.kind != "operator":
            raise err("operator_required")

    # ------------------------------------------------------------------ the snapshot block

    def _gimbal_view(self, v: Any, now: float) -> dict[str, Any] | None:
        slot = self._gimbals.get(v.id)
        return None if slot is None else slot.pod.view(now)

    # ------------------------------------------------------------------ lifecycle hooks

    def _gimbal_install(self, v: Any, pod: Gimbal, spec: dict[str, Any]) -> _Slot:
        old = self._gimbals.pop(v.id, None)
        if old is not None:
            old.pod.close()
        slot = self._gimbals[v.id] = _Slot(pod, spec)
        return slot

    def _gimbal_autoattach(self, v: Any) -> None:
        """A new ANetDU7 simulator carries a simulated pod. Call it once the record's ``model`` is set."""
        if v.kind == "sim" and getattr(v, "model", "micro") == "anet-du7" and v.id not in self._gimbals:
            self._gimbal_install(v, SimPod(), {"transport": "sim"})

    def _gimbal_drop(self, v: Any) -> None:
        """The aircraft is gone: close its pod's link."""
        slot = self._gimbals.pop(v.id, None)
        if slot is not None:
            slot.pod.close()

    def _gimbal_shutdown(self) -> None:
        for slot in list(self._gimbals.values()):
            slot.pod.close()
        self._gimbals.clear()

    # ------------------------------------------------------------------ carrier pose

    def _gimbal_carrier(self, v: Any, with_rings: bool = False) -> Carrier | None:
        frame = getattr(self.settings, "control_frame", "flu")  # type: ignore[attr-defined]
        wall = self.wall()  # type: ignore[attr-defined]
        ox, oy, oz = v.frame_offset
        sim = getattr(v.driver, "sim", None) if v.kind == "sim" else None
        s = getattr(getattr(sim, "quad", None), "s", None)
        if s is not None:  # the simulator's ground truth
            lat, lon = sim._geo()
            rings = [r for _name, r in self._world_rings()] if with_rings else ()  # type: ignore[attr-defined]
            return Carrier(
                pos=(s.p[0] + ox, s.p[1] + oy, s.p[2] + oz),
                roll=s.roll,
                pitch=s.pitch,
                yaw=s.yaw,
                lat=lat,
                lon=lon,
                alt=HOME[2] + s.p[2],
                rel_alt=s.p[2],
                speed=math.hypot(s.v[0], s.v[1]),
                wall=wall,
                frame=frame,
                rings=rings,
            )
        st = v.state
        att, local, glob = st["attitude"], st["local"], st["global"]
        if att is None:
            return None
        pos = (0.0, 0.0, 0.0)
        speed = 0.0
        rel = 0.0
        if local is not None:  # NED -> ENU, plus the aircraft's offset into the shared frame
            pos = (local["y"] + ox, local["x"] + oy, -local["z"] + oz)
            speed = math.hypot(local["vx"], local["vy"])
            rel = -local["z"]
        valid = glob is not None and glob["valid"]
        return Carrier(
            pos=pos,
            roll=att["roll"],
            pitch=att["pitch"],
            yaw=att["yaw"],
            lat=glob["lat"] if valid else 0.0,
            lon=glob["lon"] if valid else 0.0,
            alt=glob["alt"] if valid else 0.0,
            rel_alt=rel,
            speed=speed,
            wall=wall,
            frame=frame,
        )

    # ------------------------------------------------------------------ the tick

    def _gimbal_step(self, now: float) -> None:
        """Once per fleet tick: step every pod with its aircraft's pose and publish what they report."""
        for vid, slot in list(self._gimbals.items()):
            v = self.vehicles.get(vid)  # type: ignore[attr-defined]
            if v is None:
                self._gimbals.pop(vid, None)
                slot.pod.close()
                continue
            if isinstance(slot.pod, SimPod):
                if now - slot.stepped < SIM_STEP_S:
                    continue
                slot.stepped = now
            try:
                carrier = self._gimbal_carrier(
                    v, with_rings=isinstance(slot.pod, SimPod) and slot.pod.sim.laser_on
                )
                if carrier is not None:
                    slot.pod.step(now, carrier)
                self._gimbal_events(v, slot.pod)
            except Exception:
                log.exception("gimbal step failed for %s", vid)

    def _gimbal_events(self, v: Any, pod: Gimbal) -> None:
        for kind, data in pod.take_events():
            labels = {"vehicle": v.label}
            if kind == "capture":
                self.events.emit(  # type: ignore[attr-defined]
                    "gimbal_capture",
                    v.id,
                    data={
                        k: data[k]
                        for k in ("seq", "kind", "index", "t", "gimbal", "lens", "zoom", "target")
                        if k in data
                    },
                    labels=labels,
                )
            elif kind == "record":
                on = data["on"]
                self.events.emit(  # type: ignore[attr-defined]
                    "gimbal_record",
                    v.id,
                    data={"on": on, "capture": data.get("capture")},
                    labels={
                        **labels,
                        "detail_en": "started" if on else "stopped",
                        "detail_zh": "已开始" if on else "已停止",
                    },
                )
            elif kind == "lens":
                self.events.emit("gimbal_lens", v.id, data=dict(data), labels=labels)  # type: ignore[attr-defined]
            elif kind == "link":
                state = data["link"]
                self.events.emit(  # type: ignore[attr-defined]
                    "gimbal_link",
                    v.id,
                    data=dict(data),
                    labels={**labels, "link_zh": LINK_TEXT_ZH.get(state, state)},
                )

    # ------------------------------------------------------------------ operations

    def gimbal_view(self, vid: str) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            v = self._vehicle(vid)  # type: ignore[attr-defined]
            return self._gimbal_slot(v).pod.view(self.clock())  # type: ignore[attr-defined]

        return self.call(run)  # type: ignore[attr-defined]

    def gimbal_command(
        self, vid: str, action: str, params: dict[str, Any] | None, actor: Actor
    ) -> dict[str, Any]:
        """Run one pod action (12 §5.2). Returns ``{"id", "gimbal"}``, the view right after the command."""

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)  # type: ignore[attr-defined]
            self._check_gimbal_control(v, actor)
            slot = self._gimbal_slot(v)
            now = self.clock()  # type: ignore[attr-defined]
            p = dict(params or {})
            if action == "look_at":
                action_, p = "angle", self._look_at(v, p)
            else:
                action_ = action
            slot.pod.command(action_, p, now)
            self._gimbal_events(v, slot.pod)
            return {"id": vid, "gimbal": slot.pod.view(now)}

        return self.call(run)  # type: ignore[attr-defined]

    def _look_at(self, v: Any, p: dict[str, Any]) -> dict[str, Any]:
        """The ``angle`` parameters (earth space) that point the pod at the point (x, y, z) of ``p``."""
        frame = p.get("frame") or getattr(self.settings, "control_frame", "flu")  # type: ignore[attr-defined]
        if frame not in ("flu", "frd"):
            raise err("invalid_request", reason="frame must be flu or frd")
        try:
            x, y, z = (float(p[k]) for k in ("x", "y", "z"))
        except (KeyError, TypeError, ValueError):
            raise err("invalid_request", reason="look_at needs the numbers x, y and z") from None
        if not all(math.isfinite(n) for n in (x, y, z)):
            raise err("invalid_request", reason="look_at needs finite numbers")
        enu = (x, y, z) if frame == "flu" else (y, x, -z)  # frd local coordinates are NED
        carrier = self._gimbal_carrier(v)
        if carrier is None:
            raise err("invalid_request", reason="the aircraft has no attitude or position estimate yet")
        try:
            az, el = geo.look_angles(carrier.pos, enu)
        except ValueError:
            raise err("invalid_request", reason="the point is at the aircraft") from None
        return {"yaw_deg": geo.wrap180(az), "pitch_deg": el, "space": "earth"}

    def gimbal_attach(self, vid: str, spec: dict[str, Any], actor: Actor) -> dict[str, Any]:
        """Attach a pod (operator): ``{"transport": "sim"}``, ``{"transport": "tcp", "host", "port"}`` or
        ``{"transport": "serial", "device", "baud"}``. Replaces the pod the aircraft has."""
        self._check_gimbal_operator(actor)
        transport = spec.get("transport")
        if transport not in ("sim", "tcp", "serial"):
            raise err("invalid_request", reason="transport must be sim, tcp or serial")
        link = None
        if transport == "tcp":
            host = str(spec.get("host") or "").strip()
            if not host or len(host) > 253 or any(c.isspace() or c in "/\\\x00" for c in host):
                raise err("invalid_request", reason="host must name the pod's address")
            port = int(spec.get("port") or TCP_PORT)
            if not 1 <= port <= 65535:
                raise err("invalid_request", reason="port must be 1-65535")
            link = DytLink("tcp", host=host, port=port)
        elif transport == "serial":
            device = str(spec.get("device") or "").strip()
            if not device or len(device) > 256 or "\x00" in device or not sl.is_port_name(device):
                raise err(
                    "serial_unavailable", device=device or "-", reason="not a local serial port",
                    reason_zh="不是本机串口",
                )  # fmt: skip
            link = DytLink(
                "serial",
                device=device,
                baud=int(spec.get("baud") or SERIAL_BAUD),
                serial_opener=getattr(self, "serial_opener", None),
            )

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)  # type: ignore[attr-defined]
            if transport == "sim" and v.kind != "sim":
                raise err("gimbal_unsupported", reason="a simulated pod needs a simulated aircraft",
                          reason_zh="模拟吊舱只能装在模拟机上")  # fmt: skip
            if transport != "sim" and v.kind == "sim":
                raise err("gimbal_unsupported", reason="a simulated aircraft carries a simulated pod",
                          reason_zh="模拟机只能装模拟吊舱")  # fmt: skip
            if transport == "sim":
                pod: Gimbal = SimPod()
                detail_en, detail_zh = "simulated pod", "模拟吊舱"
            else:
                assert link is not None
                pod = DytPod(link)
                detail_en, detail_zh = f"DYT pod at {link.address}", f"DYT 吊舱 {link.address}"
            self._gimbal_install(
                v, pod, {k: spec.get(k) for k in ("transport", "host", "port", "device", "baud")}
            )
            self.events.emit(  # type: ignore[attr-defined]
                "gimbal_attached",
                v.id,
                data={"transport": transport, "address": getattr(link, "address", None)},
                labels={"vehicle": v.label, "detail_en": detail_en, "detail_zh": detail_zh},
            )
            return {"id": vid, "gimbal": pod.view(self.clock())}  # type: ignore[attr-defined]

        return self.call(run)  # type: ignore[attr-defined]

    def gimbal_detach(self, vid: str, actor: Actor) -> dict[str, Any]:
        self._check_gimbal_operator(actor)

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)  # type: ignore[attr-defined]
            self._gimbal_slot(v)
            self._gimbal_drop(v)
            self.events.emit("gimbal_detached", v.id, labels={"vehicle": v.label})  # type: ignore[attr-defined]
            return {"id": vid, "detached": True}

        return self.call(run)  # type: ignore[attr-defined]

    def gimbal_captures(self, vid: str, limit: int = 100) -> dict[str, Any]:
        """The pod's photo and recording log, newest last (the last ``limit`` entries)."""

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)  # type: ignore[attr-defined]
            pod = self._gimbal_slot(v).pod
            items = list(pod.captures)
            limit_ = max(1, int(limit))
            return {"id": vid, "total": pod._cap_seq, "captures": items[-limit_:]}

        return self.call(run)  # type: ignore[attr-defined]
