"""``TwinAutopilot``: a firmware twin as the fleet sees a simulator (11 §2).

The fleet's ``SimDriver`` and ``sim.world`` use a twin exactly like a ``SimAutopilot``:

* ``receive(bytes, now)`` hands Relay's MAVLink to the firmware's GCS link; ``step(now)`` returns what the
  firmware sent since the last call (and runs any physics step the world did not run);
* ``pending_steps(now)`` / ``physics_step()`` / ``t`` / ``quad``: the world advances every simulator one
  250 Hz step at a time; for a twin one ``physics_step`` is one lockstep exchange with the firmware
  (ArduPilot: four JSON frames at ``SIM_RATE_HZ 1000``; PX4: one HIL_SENSOR / HIL_ACTUATOR_CONTROLS pair);
* ``armed``, ``crashed``, ``p``, ``hover_assist``, ``turbulence``, ``airframe`` / ``set_airframe``,
  ``assist_ramp``, ``shoot_down``, ``reset``: what the arena, collisions, scenarios and the fleet read.

The physics is ``sim.physics.Quad`` with the physical effects on (the firmware is the brain: voltage, ground
effect, turbulence and the airframe defects all act), the twin's frame (``twin.profile.frame_model``) and
the sensors of ``sim.sensors.SensorModel``. An airframe's level error turns the IMU (and, for ArduPilot, the
attitude the SITL simulates its compass from) by the error, so the aircraft flies tilted while the firmware
reads level; its IMU noise adds white noise to the accelerometer and gyro. Hover assist does not exist on a
twin: ``set_hover_assist`` raises ``TwinUnsupported``.

Lifecycle (11 §6.3): ``start`` launches the firmware and runs the lockstep on the calling thread until the
first heartbeat (15 s wall, ``TwinStartError`` with the last 40 output lines). ``reset`` restarts the
process with the same configuration on a background thread (the link is lost meanwhile). ``close`` kills
the process and removes its working directory. A process that exits by itself raises the ``twin_exited``
notification on the next ``step``. Notifications go to ``notify(kind, data)`` on the fleet thread.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..mav.mavlink import Encoder, M
from ..sim import sensors as sensormod
from ..sim.airframe import Airframe
from ..sim.frame import FrameModel
from ..sim.physics import DT, Quad, euler_quaternion, seed_for, thrust_fraction
from ..sim.sensors import SensorModel
from . import ardupilot, px4, wmm
from . import params as tparams
from .hil import HilEncoder
from .packs import Pack
from .process import INSTANCES, TwinStartError, remove_tree, workdir

log = logging.getLogger("relay.twin")

BOOT_TIMEOUT_S = 15.0
MAX_STEPS = 125  # world steps per tick at most (0.5 s); a longer gap is skipped, as SimAutopilot does
TICK_WAIT_S = 0.02  # wall time a tick may wait for the firmware in total
NOTICE_COMPID = 25  # MAV_COMP_ID_USER1: the twin's own STATUSTEXT (crash), beside the firmware's sequence
DEFAULT_HOME = (47.397742, 8.545594, 488.0)  # the fast simulator's site (PX4 SITL default)
EARTH_RADIUS = sensormod.EARTH_RADIUS
# Sensor noise scale of a twin's board (``SensorModel(noise=...)``): a calibrated flight controller, half the
# noise and bias of the uncalibrated MEMS defaults. Readings still never repeat (PX4's stuck-sensor check).
SENSOR_NOISE = 0.5


class TwinUnsupported(Exception):
    """An operation a firmware twin does not have (hover assist)."""

    def __init__(self, operation: str) -> None:
        super().__init__(operation)
        self.operation = operation


@dataclass
class TwinConfig:
    """Everything a twin starts with; the fleet resolves it (``prepare``) before the process starts."""

    pack: Pack
    frame: FrameModel
    home: tuple[float, float, float]
    profile_id: str | None = None
    profile_name: str | None = None
    match: str | None = None  # identity match level of the pack against the profile's firmware
    applied: dict[str, float] = field(default_factory=dict)
    excluded: list[dict[str, str]] = field(default_factory=list)
    forced: dict[str, float] = field(default_factory=dict)
    types: dict[str, str] = field(default_factory=dict)
    effective: dict[str, float] = field(default_factory=dict)

    @property
    def family(self) -> str:
        return self.pack.family


def baseline_params(pack: Pack) -> dict[str, float]:
    """The parameters the pack's default files set (copter.parm and the Relay baseline)."""
    out: dict[str, float] = {}
    for path in pack.default_files():
        try:
            text = path.read_text("utf-8")
        except OSError:
            continue
        out.update(tparams.parse_parm(text) if pack.family == "ardupilot" else tparams.parse_px4(text))
    return out


def prepare(
    pack: Pack,
    profile: dict[str, Any] | None,
    frame: dict[str, Any] | None,
    home: tuple[float, float, float] | None,
    match: str | None = None,
) -> TwinConfig:
    """Resolve a twin's configuration: the profile's parameters through the hardware filter, Relay's forced
    values, and the frame derived from the effective hover thrust. Raises ValueError for a bad frame."""
    from .profile import frame_model

    family = pack.family
    applied: dict[str, float] = {}
    excluded: list[dict[str, str]] = []
    types: dict[str, str] = {}
    if profile is not None:
        applied, excluded = tparams.split(family, profile.get("params") or {})
        types = dict(profile.get("param_types") or {})
    forced = dict(tparams.FORCED.get(family, {}))
    effective = {**baseline_params(pack), **applied, **forced}
    spec = frame if frame is not None else (profile or {}).get("frame")
    model = frame_model(spec, effective, family)
    if home is None and profile is not None and profile.get("home"):
        h = profile["home"]
        home = (float(h["lat"]), float(h["lon"]), float(h.get("alt_m", 0.0)))
    return TwinConfig(
        pack=pack,
        frame=model,
        home=home or DEFAULT_HOME,
        profile_id=(profile or {}).get("id"),
        profile_name=(profile or {}).get("name"),
        match=match,
        applied=applied,
        excluded=excluded,
        forced=forced,
        types=types,
        effective=effective,
    )


def shifted_home(home: tuple[float, float, float], east: float, north: float) -> tuple[float, float, float]:
    """``home`` moved by ``east`` / ``north`` metres (a simulator's spawn offset in the shared world)."""
    lat, lon, alt = home
    lat2 = lat + math.degrees(north / EARTH_RADIUS)
    lon2 = lon + math.degrees(east / (EARTH_RADIUS * math.cos(math.radians(lat))))
    return (lat2, lon2, alt)


def _qmul(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, float, float, float]:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def _dcm(q: tuple[float, ...]) -> tuple[tuple[float, float, float], ...]:
    w, x, y, z = q
    return (
        (1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)),
        (2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)),
        (2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)),
    )


def _apply(m: tuple[tuple[float, float, float], ...], v: tuple[float, ...]) -> tuple[float, float, float]:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


class TwinAutopilot:
    engine = "sitl"

    def __init__(
        self,
        config: TwinConfig,
        *,
        name: str = "SIM",
        turbulence: float = 0.0,
        airframe: Airframe | None = None,
        notify: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.config = config
        self.flavour = config.family
        self.name = name
        self.notify = notify
        self.quad = Quad(effects=True, turbulence=turbulence, seed=seed_for(name), frame=config.frame)
        self.quad.set_airframe(airframe or Airframe())
        self.sensor_seed = seed_for(f"{name}:sensors")
        self.sensors = self._new_sensors()
        self.hil = HilEncoder(self.sensors)
        self.hover_assist = True
        self.assist_ramp: tuple[float, float] | None = None  # sim.world clears it on a crash; unused here
        self.t: float | None = None
        self.fw_target = 0.0  # firmware time at the end of the last world step (ArduPilot)
        self.armed = False
        self.state = "new"  # new | booting | running | restarting | exited | failed | stopped
        self.instance: int | None = None
        self.bridge: Any = None
        self.workdir: Path | None = None
        self.exit: dict[str, Any] | None = None
        self.started_wall: float | None = None
        self.boot_s: float | None = None
        self.restarts = 0
        self._crash_reported = False
        self._sysid = 1
        self._notice = Encoder(1, NOTICE_COMPID)
        self._out: list[bytes] = []
        self._pending: list[tuple[str, dict[str, Any]]] = []
        self._deadline = 0.0
        self._lock = threading.Lock()
        self.stalls = 0
        self.step_wall = 0.0  # wall seconds spent in physics_step (performance report)
        self.steps = 0

    # ------------------------------------------------------------------ configuration views

    @property
    def pack(self) -> Pack:
        return self.config.pack

    @property
    def firmware(self) -> dict[str, Any]:
        """``vehicle.sim.firmware`` (11 §10)."""
        return {
            "family": self.pack.family,
            "version": self.pack.version,
            "pack": self.pack.id,
            "match": self.config.match,
        }

    def _new_sensors(self) -> SensorModel:
        lat, lon, alt = self.config.home
        field = wmm.field(lat, lon) if self.flavour == "px4" else None
        return SensorModel((lat, lon, alt), seed=self.sensor_seed, noise=SENSOR_NOISE, field=field)

    # ------------------------------------------------------------------ simulator interface (fleet, world)

    @property
    def turbulence(self) -> float:
        return self.quad.turbulence

    @turbulence.setter
    def turbulence(self, value: float) -> None:
        self.quad.turbulence = min(1.0, max(0.0, float(value)))

    @property
    def airframe(self) -> Airframe:
        return self.quad.airframe_spec

    def set_airframe(self, airframe: Airframe) -> None:
        self.quad.set_airframe(airframe)

    def set_hover_assist(self, on: bool, now: float | None = None, *, enter_hold: bool = True) -> None:
        raise TwinUnsupported("hover_assist")

    @property
    def p(self) -> list[float]:
        return list(self.quad.s.p)

    @property
    def crashed(self) -> bool:
        return self.quad.s.crashed

    def shoot_down(self, reason: str = "Shot down") -> None:
        s = self.quad.s
        if s.crashed:
            return
        self.quad._crash(reason, "shot_down")

    @property
    def running(self) -> bool:
        return self.state == "running"

    def receive(self, data: bytes, now: float) -> None:
        if self.state == "running" and self.bridge is not None:
            self.bridge.gcs_write(data)

    def pending_steps(self, now: float) -> int:
        if self.t is None:
            return 0
        if self.state != "running":
            self.t = now  # no firmware to keep time with: nothing piles up
            return 0
        steps = int((now - self.t) / DT)
        if steps > MAX_STEPS:
            self.t = now - MAX_STEPS * DT
            steps = MAX_STEPS
        self._deadline = time.monotonic() + TICK_WAIT_S
        return max(0, steps)

    def physics_step(self) -> None:
        """One 250 Hz world step: one lockstep exchange with the firmware."""
        self.t = (self.t or 0.0) + DT
        if self.state != "running" or self.bridge is None:
            return
        started = time.monotonic()
        deadline = max(self._deadline, started + 0.001)
        if not self.bridge.advance(self, deadline, DT):
            self.stalls += 1
        self.step_wall += time.monotonic() - started
        self.steps += 1

    def step(self, now: float) -> bytes:
        if self.t is None:
            self.t = now
        self._flush_pending()
        if self.state == "running":
            if not self.bridge.alive():
                self._exited()
            else:
                pending = self.pending_steps(now)
                for _ in range(pending):
                    self.physics_step()
                self._supervise()
                self._out.append(self.bridge.gcs_read())
        data = b"".join(self._out)
        self._out.clear()
        return data

    # ------------------------------------------------------------------ physics callbacks (bridges)

    def apply(self, outputs: list[float], armed: bool, dt: float) -> None:
        """Step the physics with the firmware's four motor outputs (0..1, physics rotor order)."""
        if armed and not self.armed:
            af = self.quad.airframe
            if af is not None:
                af.rearm()
        self.armed = armed
        fractions = [thrust_fraction(u) for u in outputs]
        self.quad.step(fractions, armed, dt)

    def _trim(self) -> tuple[float, float, float, float] | None:
        af = self.quad.active_airframe()
        if af is None or (not af.trim[0] and not af.trim[1]):
            return None
        return euler_quaternion(af.trim[0], af.trim[1], 0.0)

    def board_imu(self, dt: float) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        accel, gyro = self.sensors.imu(self.quad, dt)
        af = self.quad.active_airframe()
        if af is not None:
            trim = self._trim()
            if trim is not None:
                m = _dcm(trim)
                accel, gyro = _apply(m, accel), _apply(m, gyro)
            motor = self.quad.s.motor
            na, ng = af.accel_noise(motor), af.gyro_noise(motor)
            if na or ng:
                accel = (accel[0] + af.gauss(na), accel[1] + af.gauss(na), accel[2] + af.gauss(na))
                gyro = (gyro[0] + af.gauss(ng), gyro[1] + af.gauss(ng), gyro[2] + af.gauss(ng))
        return accel, gyro

    def board_quaternion(self) -> tuple[float, float, float, float]:
        q = self.quad.s.quaternion()
        trim = self._trim()
        if trim is None:
            return q
        w, x, y, z = trim
        return _qmul(q, (w, -x, -y, -z))

    def json_state(self, t: float) -> str:
        """ArduPilot JSON physics reply for firmware time ``t``: NED position and velocity, FRD IMU, the
        attitude, the battery (formatted by hand: ``json.dumps`` is a third of the bridge's own time)."""
        s = self.quad.s
        (ax, ay, az), (gx, gy, gz) = self.board_imu(1.0 / max(1, getattr(self.bridge, "rate", 1000)))
        qw, qx, qy, qz = self.board_quaternion()
        p, v = s.p, s.v
        battery = self.quad.battery
        return (
            f'{{"timestamp":{t!r},"imu":{{"gyro":[{gx!r},{gy!r},{gz!r}],"accel_body":[{ax!r},{ay!r},{az!r}]}},'
            f'"position":[{p[1]!r},{p[0]!r},{-p[2]!r}],"velocity":[{v[1]!r},{v[0]!r},{-v[2]!r}],'
            f'"quaternion":[{qw!r},{qx!r},{qy!r},{qz!r}],'
            f'"battery":{{"voltage":{battery.voltage!r},"current":{battery.current!r}}}}}'
        )

    def hil_frame(self, t_us: int, dt: float) -> bytes:
        """PX4 HIL frame (HIL_SENSOR, HIL_GPS at 10 Hz) for physics time ``t_us``."""
        imu = self.board_imu(dt)
        mag = self.sensors.mag(self.quad)
        trim = self._trim()
        if trim is not None:
            mag = _apply(_dcm(trim), mag)
        return self.hil.frame(self.quad, t_us, dt, imu, mag)

    def firmware_rebooted(self) -> None:
        """ArduPilot's frame count went backwards: the firmware restarted; the physics starts over."""
        self.fw_target = 0.0
        self.quad.reset()
        self.armed = False
        self._crash_reported = False

    # ------------------------------------------------------------------ crash notice

    def _supervise(self) -> None:
        s = self.quad.s
        if s.crashed and not self._crash_reported:
            self._crash_reported = True
            text = f"Crash: airframe down ({s.crash_reason or 'impact'})"[:50]
            msg = M.MAVLink_statustext_message(M.MAV_SEVERITY_CRITICAL, text.encode("utf-8"))
            self._out.append(self._notice.encode(msg))

    # ------------------------------------------------------------------ lifecycle

    def start(self, timeout: float = BOOT_TIMEOUT_S) -> None:
        """Launch the firmware and run it to its first heartbeat (on the calling thread)."""
        self.state = "booting"
        started = time.monotonic()
        try:
            self._launch(started + timeout)
        except TwinStartError:
            self.state = "failed"
            self._teardown(release=True)
            raise
        except BaseException:
            self.state = "failed"
            self._teardown(release=True)
            raise
        self.boot_s = round(time.monotonic() - started, 2)
        self.started_wall = time.time()
        self.state = "running"

    def _open(self, n: int) -> Any:
        return (ardupilot.Ports if self.flavour == "ardupilot" else px4.Ports)(n)

    def _launch(self, deadline: float) -> None:
        if self.instance is None:
            self.instance, ports = INSTANCES.acquire(self._open)
        else:
            ports = self._open(self.instance)
        self.workdir = workdir()
        n = self.instance
        if self.flavour == "ardupilot":
            parm = self.workdir / "profile.parm"
            parm.write_text(
                tparams.ardupilot_parm(
                    {**self.config.applied, **self.config.forced},
                    header=f"ANet Relay twin {self.name}: profile parameters and Relay's lockstep values",
                ),
                "utf-8",
            )
            bridge: Any = ardupilot.ArduPilotBridge(self.pack, n, ports, self.workdir)
            self.bridge = bridge
            bridge.launch([*self.pack.default_files(), parm], self.config.home)
        else:
            script = self.workdir / "relay-params.sh"
            script.write_text(
                tparams.px4_params(
                    {**self.config.applied, **self.config.forced},
                    self.config.types,
                    baseline=[str(p) for p in self.pack.default_files()],
                    header=f"ANet Relay twin {self.name}: Relay baseline, then the profile parameters",
                ),
                "utf-8",
            )
            bridge = px4.Px4Bridge(self.pack, n, ports, self.workdir)
            self.bridge = bridge
            bridge.launch(script)
        self.fw_target = 0.0
        bridge.boot(self, deadline)

    def _teardown(self, release: bool) -> None:
        bridge, self.bridge = self.bridge, None
        if bridge is not None:
            try:
                bridge.close()
            except Exception:
                log.exception("closing twin %s failed", self.name)
        remove_tree(self.workdir)
        self.workdir = None
        if release and self.instance is not None:
            INSTANCES.release(self.instance)
            self.instance = None

    def close(self) -> None:
        """Kill the firmware, close its links, remove its files (simulator removed, Relay exiting)."""
        with self._lock:
            self.state = "stopped"
            self._teardown(release=True)

    def reset(self) -> None:
        """Back to the spawn point on a restarted firmware with the same configuration (11 §6.3)."""
        with self._lock:
            if self.state == "stopped":
                return
            self.state = "restarting"
            self._teardown(release=False)
        self.quad.reset()
        self.armed = False
        self._crash_reported = False
        self.sensors = self._new_sensors()
        self.hil = HilEncoder(self.sensors)
        self.restarts += 1
        thread = threading.Thread(target=self._restart, name=f"twin-restart-{self.name}", daemon=True)
        thread.start()

    def _restart(self) -> None:
        started = time.monotonic()
        try:
            self._launch(started + BOOT_TIMEOUT_S)
        except Exception as exc:
            with self._lock:
                if self.state == "stopped":
                    return
                lines = exc.lines if isinstance(exc, TwinStartError) else []
                reason = exc.reason if isinstance(exc, TwinStartError) else str(exc)
                self.state = "failed"
                self._teardown(release=True)
            self._pending.append(("twin_start_failed", {"reason": reason, "lines": lines}))
            return
        with self._lock:
            if self.state == "stopped":
                self._teardown(release=True)
                return
            self.boot_s = round(time.monotonic() - started, 2)
            self.started_wall = time.time()
            self.state = "running"
        self._pending.append(("twin_started", {"restart": True}))

    def _exited(self) -> None:
        code = self.bridge.returncode if self.bridge is not None else None
        lines = self.bridge.tail() if self.bridge is not None else []
        self.state = "exited"
        self.exit = {"code": code, "lines": lines}
        self._teardown(release=True)
        self._pending.append(("twin_exited", {"code": code, "lines": lines}))

    def _flush_pending(self) -> None:
        while self._pending:
            kind, data = self._pending.pop(0)
            if self.notify is not None:
                try:
                    self.notify(kind, data)
                except Exception:
                    log.exception("twin notification %s failed", kind)

    # ------------------------------------------------------------------ views

    @property
    def pid(self) -> int | None:
        bridge = self.bridge
        process = getattr(bridge, "process", None)
        return None if process is None else process.pid

    def view(self) -> dict[str, Any]:
        """``GET /sims/{id}/twin``."""
        cfg = self.config
        frame = cfg.frame
        bridge = self.bridge
        return {
            "engine": self.engine,
            "state": self.state,
            "pack": cfg.pack.view(),
            "firmware": self.firmware,
            "profile": None if cfg.profile_id is None else {"id": cfg.profile_id, "name": cfg.profile_name},
            "instance": self.instance,
            "pid": self.pid,
            "home": {"lat": cfg.home[0], "lon": cfg.home[1], "alt_m": cfg.home[2]},
            "frame": {
                "preset": frame.preset,
                "mass_kg": round(frame.mass_kg, 4),
                "wheelbase_m": round(frame.wheelbase_m, 4),
                "prop_in": round(frame.prop_in, 2),
                "cells": frame.cells,
                "capacity_mah": frame.capacity_mah,
                "thrust_to_weight": round(frame.thrust_to_weight, 3),
                "inertia": [frame.ixx, frame.iyy, frame.izz],
            },
            "params": {
                "applied": len(cfg.applied),
                "excluded": cfg.excluded,
                "forced": cfg.forced,
            },
            "ports": self._ports(),
            "boot_s": self.boot_s,
            "restarts": self.restarts,
            "exit": self.exit,
            "lockstep": {
                "steps": self.steps,
                "stalls": self.stalls,
                "frames": getattr(bridge, "frames", 0),
                "ms_per_step": round(1000.0 * self.step_wall / self.steps, 3) if self.steps else None,
            },
        }

    def _ports(self) -> dict[str, int] | None:
        n = self.instance
        if n is None:
            return None
        if self.flavour == "ardupilot":
            return {
                "gcs_tcp": ardupilot.GCS_BASE + 10 * n,
                "physics_udp": ardupilot.PHYSICS_BASE + 10 * n,
                "rc_in_udp": ardupilot.RC_IN_BASE + 10 * n,
            }
        return {
            "hil_tcp": px4.HIL_BASE + n,
            "gcs_udp_local": px4.GCS_LOCAL_BASE + n,
            "gcs_udp_remote": px4.GCS_REMOTE_BASE + n,
        }

    def set_frame(self, config: TwinConfig) -> None:
        """A new frame (disarmed, on the ground): the physics flies it from the next step."""
        self.config = config
        self.quad.set_frame(config.frame)


__all__ = ["TwinAutopilot", "TwinConfig", "TwinStartError", "TwinUnsupported", "prepare", "shifted_home"]
