"""Observation builder (06 §2.2): what a program sees each frame, from the fleet's vehicle record.

One builder serves simulators and real aircraft: everything is read from the decoded MAVLink state
(``v.state``, the same messages a real autopilot sends), so a field the aircraft does not report is ``None``.
Simulator-only additions are the scenario sensors (06 §3.3): a barometer (on by default), a position
source (``none``, or ``uwb`` / ``gps`` sampled at 10 Hz with Gaussian noise) and small sensor noise on
altitude, climb and contacts, all from a random generator seeded at deployment, so a run is repeatable.

Contacts are the other aircraft within 30 m (at most 16, nearest first): local aircraft of the same kind
(simulators see simulators, real aircraft see real aircraft) and, when the LAN arena is joined, the peers'
aircraft. ``rel`` / ``vrel`` are (forward, right, up) in the observer's heading frame; ``bearing`` is degrees
right of the nose, ``elevation`` degrees above the horizon. ``team`` is the contact's id unless the service
assigns teams (``ProgramService.teams``).

Body accelerations come from ``SCALED_IMU`` (mG) or ``HIGHRES_IMU`` (m/s²) when the aircraft streams one, as
specific force in (forward, right, up): about +9.81 on ``az`` at rest.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..mav.mavlink import M

if TYPE_CHECKING:
    from ..fleet import FleetService, Vehicle

G = 9.80665
POSITION_HZ = 10.0
POSITION_NOISE = {"uwb": (0.05, 0.05), "gps": (0.3, 0.1)}  # sigma of position (m) and velocity (m/s)
ALT_NOISE_M = 0.01
VZ_NOISE = 0.02
CONTACT_RANGE_M = 30.0
CONTACT_MAX = 16
IMU_FRESH_S = 0.5
POSITION_SOURCES = ("none", "uwb", "gps")


@dataclass
class Sensors:
    """Scenario sensors of a deployment; ``None`` means "what the aircraft has"."""

    baro: bool | None = None
    position: str | None = None
    contacts: bool = True

    @classmethod
    def parse(cls, raw: Any) -> Sensors:
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise ValueError("sensors must be an object")
        baro = raw.get("baro")
        position = raw.get("position")
        contacts = raw.get("contacts", True)
        if baro is not None and not isinstance(baro, bool):
            raise ValueError("sensors.baro must be true or false")
        if position is not None and position not in POSITION_SOURCES:
            raise ValueError("sensors.position must be none, uwb or gps")
        if not isinstance(contacts, bool):
            raise ValueError("sensors.contacts must be true or false")
        return cls(baro, position, contacts)

    def view(self) -> dict[str, Any]:
        return {"baro": self.baro, "position": self.position, "contacts": self.contacts}


def parse_arena(raw: Any) -> dict[str, float] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("arena must be an object")
    out = {}
    for key in ("radius", "floor", "ceiling"):
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"arena.{key} must be a number")
        out[key] = float(value)
    if out["radius"] <= 0 or out["ceiling"] <= out["floor"]:
        raise ValueError("arena needs radius > 0 and ceiling above floor")
    return out


def _r(value: float | None, digits: int = 4) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def _heading_frame(d: tuple[float, float, float], yaw: float) -> tuple[float, float, float]:
    """ENU vector -> (forward, right, up) for a compass heading ``yaw`` (rad, 0 north, clockwise)."""
    s, c = math.sin(yaw), math.cos(yaw)
    return (d[0] * s + d[1] * c, d[0] * c - d[1] * s, d[2])


def _enu_velocity(v: Vehicle) -> tuple[float, float, float] | None:
    local = v.state["local"]
    if local is None:
        return None
    return (float(local["vy"]), float(local["vx"]), -float(local["vz"]))


class ObsBuilder:
    """Per deployment: keeps the program clock, the 10 Hz position sample and the noise generator."""

    def __init__(
        self,
        seed: int,
        sensors: Sensors | None = None,
        arena: dict[str, float] | None = None,
        t0: float = 0.0,
    ) -> None:
        self.rng = random.Random((int(seed) * 2654435761) & 0xFFFFFFFF)
        self.sensors = sensors or Sensors()
        self.arena = arena
        self.t0 = t0
        self.last: float | None = None
        self.pos_at: float | None = None
        self.pos: tuple[float, float, float] | None = None
        self.vel: tuple[float, float, float] | None = None

    def _noise(self, sigma: float) -> float:
        return self.rng.gauss(0.0, sigma) if sigma > 0 else 0.0

    def build(
        self,
        fleet: FleetService,
        v: Vehicle,
        now: float,
        sticks_in: dict[str, Any] | None = None,
        teams: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        sim = v.kind == "sim"
        st = v.state
        dt = 0.02 if self.last is None else max(0.0, now - self.last)
        self.last = now
        obs: dict[str, Any] = {
            "t": round(now - self.t0, 4),
            "dt": round(dt, 4),
            "armed": v.armed,
            "airborne": v.airborne(),
            "mode": v.mode,
        }
        att = st["attitude"]
        if att is None:
            obs.update(roll=None, pitch=None, yaw=None, p=None, q=None, r=None)
        else:
            obs.update(
                roll=_r(math.degrees(att["roll"])),
                pitch=_r(math.degrees(att["pitch"])),
                yaw=_r(math.degrees(att["yaw"]) % 360.0),
                p=_r(math.degrees(att["rollspeed"])),
                q=_r(math.degrees(att["pitchspeed"])),
                r=_r(math.degrees(att["yawspeed"])),
            )
        obs.update(self._imu(v, now))
        obs.update(self._baro(v, sim))
        obs.update(self._position(v, now, sim))
        obs["battery"] = self._battery(v)
        obs["throttle_out"] = self._throttle_out(v)
        obs.update(self._arena_state(fleet, v))
        obs["contacts"] = self._contacts(fleet, v, sim, teams or {}) if self.sensors.contacts else None
        obs["arena"] = dict(self.arena) if self.arena is not None else None
        obs["sticks_in"] = sticks_in
        return obs

    # ------------------------------------------------------------------ fields

    def _imu(self, v: Vehicle, now: float) -> dict[str, Any]:
        msgs = v.state["messages"]
        for name, scale in (("SCALED_IMU", G / 1000.0), ("HIGHRES_IMU", 1.0), ("SCALED_IMU2", G / 1000.0)):
            entry = msgs.get(name)
            if entry is None or now - entry["t"] > IMU_FRESH_S:
                continue
            f = entry["fields"]
            try:
                return {
                    "ax": _r(float(f["xacc"]) * scale),
                    "ay": _r(float(f["yacc"]) * scale),
                    "az": _r(-float(f["zacc"]) * scale),
                }
            except (KeyError, TypeError, ValueError):
                continue
        return {"ax": None, "ay": None, "az": None}

    def _baro(self, v: Vehicle, sim: bool) -> dict[str, Any]:
        has = self.sensors.baro
        if has is None:
            has = True if sim else v.has_baro()
        elif has and not sim:
            has = v.has_baro()  # a scenario cannot add a sensor to a real aircraft
        if not has:
            return {"alt": None, "vz": None}
        alt = v.altitude()
        if alt is not None:
            alt -= v.ground_alt if v.ground_alt is not None else 0.0
        local = v.state["local"]
        hud = v.state["hud"] or {}
        vz = -float(local["vz"]) if local is not None else hud.get("climb")
        if sim:
            alt = None if alt is None else alt + self._noise(ALT_NOISE_M)
            vz = None if vz is None else float(vz) + self._noise(VZ_NOISE)
        return {"alt": _r(alt), "vz": _r(None if vz is None else float(vz))}

    def _position(self, v: Vehicle, now: float, sim: bool) -> dict[str, Any]:
        source = self.sensors.position
        if not sim:
            if source == "none" or not v.position_ok(now):
                return {"pos": None, "vel": None}
            p, vel = v.world_enu(), _enu_velocity(v)
            return {
                "pos": None if p is None else [_r(x) for x in p],
                "vel": None if vel is None else [_r(x) for x in vel],
            }
        if source is None:
            source = "gps" if v.capabilities.position else "none"
        if source == "none":
            return {"pos": None, "vel": None}
        if self.pos_at is None or now - self.pos_at >= 1.0 / POSITION_HZ - 1e-6:
            p, vel = v.world_enu(), _enu_velocity(v)
            self.pos_at = now
            if p is None or vel is None:
                self.pos = self.vel = None
            else:
                sp, sv = POSITION_NOISE[source]
                self.pos = tuple(x + self._noise(sp) for x in p)  # type: ignore[assignment]
                self.vel = tuple(x + self._noise(sv) for x in vel)  # type: ignore[assignment]
        return {
            "pos": None if self.pos is None else [_r(x) for x in self.pos],
            "vel": None if self.vel is None else [_r(x) for x in self.vel],
        }

    @staticmethod
    def _battery(v: Vehicle) -> dict[str, Any]:
        battery = v.state["battery"]
        voltage = battery["voltage"]
        cells = v.cells()
        v_cell = None if voltage is None or not cells else voltage / cells
        return {"v_cell": _r(v_cell, 3), "pct": battery["remaining_pct"]}

    @staticmethod
    def _throttle_out(v: Vehicle) -> float | None:
        hud = v.state["hud"] or {}
        if hud.get("throttle") is not None:
            return _r(float(hud["throttle"]) / 100.0, 3) if v.armed else 0.0
        servo = v.state["servo"]
        if servo:
            pwms = [p for p in servo[:4] if p]
            if pwms:
                return _r(min(1.0, max(0.0, (sum(pwms) / len(pwms) - 1000.0) / 1000.0)), 3)
        return None

    @staticmethod
    def _arena_state(fleet: FleetService, v: Vehicle) -> dict[str, Any]:
        arena = fleet.arena
        gun = arena.guns.get(v.id) if arena.enabled else None
        if gun is None:
            return {"hp": None, "ammo": None, "reloading": None}
        return {"hp": gun.hp, "ammo": gun.ammo, "reloading": gun.reload_until is not None}

    def _contacts(
        self, fleet: FleetService, v: Vehicle, sim: bool, teams: dict[str, str]
    ) -> list[dict] | None:
        own = v.world_enu()
        att = v.state["attitude"]
        if own is None or att is None:
            return None
        yaw = float(att["yaw"])
        own_vel = _enu_velocity(v) or (0.0, 0.0, 0.0)
        found: list[tuple[float, str, tuple, tuple, int | None]] = []
        arena = fleet.arena
        for other in fleet.vehicles.values():
            if other is v or other.kind != v.kind:
                continue
            p = other.world_enu()
            if p is None:
                continue
            d = (p[0] - own[0], p[1] - own[1], p[2] - own[2])
            dist = math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2)
            if dist > CONTACT_RANGE_M:
                continue
            vel = _enu_velocity(other) or (0.0, 0.0, 0.0)
            gun = arena.guns.get(other.id) if arena.enabled else None
            found.append((dist, other.id, d, tuple(vel), None if gun is None else gun.hp))
        if sim and arena.enabled and arena.seat is not None and arena.peers is not None:
            from ..peers import arena as frame

            for peer in arena.peers.peers.values():
                if not peer.joined or peer.seat is None:
                    continue
                for a in peer.aircraft.values():
                    if a.down:
                        continue
                    p = frame.peer_to_local(a.p, peer.seat, arena.seat)
                    d = (p[0] - own[0], p[1] - own[1], p[2] - own[2])
                    dist = math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2)
                    if dist > CONTACT_RANGE_M:
                        continue
                    vel = frame.peer_vec_to_local(a.v, peer.seat, arena.seat)
                    found.append((dist, f"{peer.key}/sim-{a.index}", d, tuple(vel), a.hp))
        found.sort(key=lambda item: (item[0], item[1]))
        out = []
        for dist, cid, d, vel, hp in found[:CONTACT_MAX]:
            rel = _heading_frame(d, yaw)
            vrel = _heading_frame(tuple(vel[i] - own_vel[i] for i in range(3)), yaw)  # type: ignore[arg-type]
            if sim:
                sigma = 0.02 * dist + 0.03
                rel = tuple(x + self._noise(sigma) for x in rel)  # type: ignore[assignment]
                vrel = tuple(x + self._noise(0.1) for x in vrel)  # type: ignore[assignment]
            horiz = math.hypot(rel[0], rel[1])
            out.append(
                {
                    "id": cid,
                    "team": teams.get(cid, cid),
                    "dist": _r(math.sqrt(horiz**2 + rel[2] ** 2), 3),
                    "bearing": _r(math.degrees(math.atan2(rel[1], rel[0])), 2),
                    "elevation": _r(math.degrees(math.atan2(rel[2], horiz)), 2),
                    "rel": [_r(x, 3) for x in rel],
                    "vrel": [_r(x, 3) for x in vrel],
                    "hp": hp,
                }
            )
        return out


# Message rates while a program flies the aircraft (06 §2.2) and the rates restored afterwards.
PROGRAM_RATES = (
    (M.MAVLINK_MSG_ID_ATTITUDE, 50.0),
    (M.MAVLINK_MSG_ID_LOCAL_POSITION_NED, 20.0),
    (M.MAVLINK_MSG_ID_VFR_HUD, 20.0),
    (M.MAVLINK_MSG_ID_SCALED_IMU, 50.0),
)
DEFAULT_RATES = (
    (M.MAVLINK_MSG_ID_ATTITUDE, 20.0),
    (M.MAVLINK_MSG_ID_LOCAL_POSITION_NED, 10.0),
    (M.MAVLINK_MSG_ID_VFR_HUD, 4.0),
    (M.MAVLINK_MSG_ID_SCALED_IMU, 0.0),
)

__all__ = ["DEFAULT_RATES", "PROGRAM_RATES", "ObsBuilder", "Sensors", "parse_arena"]
