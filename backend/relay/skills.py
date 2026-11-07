"""Skill executor: takeoff, land, hold, rtl, goto, move, yaw (03 §5.6, §5.7).

A skill is the vehicle's single stick source while it is active. It streams
neutral sticks at the stick rate as an RC keep-alive (so an autopilot that
has seen Relay's input does not declare RC loss in GUIDED / OFFBOARD), and for
PX4 goto it streams the OFFBOARD setpoint at 10 Hz. After a skill finishes it
keeps holding (keep-alive, setpoint) until it is superseded, the controller
changes, the link is lost or the aircraft is disarmed on the ground.

Aircraft without a position estimate fly the stick variants (03 §5.7): takeoff
climbs on the throttle stick in altitude hold, move tilts for a timed
accelerate-brake profile (open loop, so the distance is an estimate), yaw closes
the loop on the attitude heading, and PX4 lands by descending on the stick.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol

from .mav import modes
from .mav.mavlink import M
from .model import StickInput

NEUTRAL = StickInput(roll=0.0, pitch=0.0, yaw=0.0, throttle=0.5, source="agent")
RETRY_S = 1.2
MAX_TRIES = 3
SETPOINT_PERIOD_S = 0.1
GOTO_TOLERANCE_M = 0.15
OFFBOARD_PRIME_S = 1.0

# Altitude-hold stick values (throttle 0.5 holds altitude in ALT_HOLD / ALTCTL).
CLIMB_STICK = 0.72
APPROACH_STICK = 0.62
DESCEND_STICK = 0.3
FLARE_STICK = 0.36
ALT_TOLERANCE_M = 0.06
# Open-loop move: tilt towards the target, brake when the dead-reckoned stopping
# point reaches it, settle (03 §5.7). Speed is integrated from the measured tilt.
MOVE_ACCEL = 0.8
MOVE_DRAG = 0.35  # 1/s, linear drag of a small quadrotor
MOVE_SETTLE_S = 1.2
MOVE_OPEN_LOOP_MAX_M = 2.0
YAW_TOLERANCE_DEG = 3.0
YAW_SETTLE_S = 0.3
GRAVITY = 9.81

REQUIREMENTS = {
    "takeoff": "althold",
    "land": "any",
    "hold": "althold",
    "rtl": "position",
    "goto": "position",
    "move": "althold",
    "yaw": "althold",
}
SKILL_NAMES = tuple(REQUIREMENTS)


class Host(Protocol):
    """What a skill needs from the vehicle (implemented by the fleet's vehicle record)."""

    autopilot: str | None
    driver: Any

    @property
    def armed(self) -> bool: ...
    @property
    def mode(self) -> str | None: ...
    @property
    def landed(self) -> str: ...
    def has_capability(self, name: str) -> bool: ...
    def local_enu(self) -> tuple[float, float, float] | None: ...
    def altitude(self) -> float | None: ...
    def home_amsl(self) -> float | None: ...
    def ack_after(self, command: int, t: float) -> dict | None: ...
    def heading(self) -> float | None: ...
    def tilt(self) -> tuple[float, float] | None: ...
    def param(self, name: str, default: float) -> float: ...


@dataclass
class Skill:
    name: str
    params: dict[str, Any]
    started: float
    state: str = "running"  # running | done | failed
    detail: str | None = None
    detail_zh: str | None = None
    holding: bool = True
    phase: str = "start"
    phase_t: float = 0.0
    tries: int = 0
    last_try: float = -1e9
    last_setpoint: float = -1e9
    expect_mode: str | None = None
    target_local: tuple[float, float, float] | None = None
    yaw: float | None = None
    timeout: float = 30.0
    id: int = 0
    events: list[tuple[str, dict]] = field(default_factory=list)
    out: StickInput | None = None  # this tick's sticks; None sends NEUTRAL
    alt_target: float | None = None
    heading_target: float | None = None
    settled_at: float | None = None
    profile: tuple[float, float, float] | None = None  # (pitch, roll, t1) of an open-loop move
    est_t: float | None = None  # open-loop move: dead-reckoned speed and distance along the move
    est_v: float = 0.0
    est_d: float = 0.0

    @property
    def active(self) -> bool:
        return self.state == "running" or (self.state == "done" and self.holding)

    def view(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "state": self.state, "detail": self.detail}

    # lifecycle ---------------------------------------------------------------

    def finish(self, detail: str | None = None) -> None:
        if self.state == "running":
            self.state = "done"
            self.detail = detail
            self.events.append(
                ("skill_finished", {"skill": self.name, "skill_id": self.id, "detail": detail})
            )

    def fail(self, en: str, zh: str) -> None:
        if self.state == "running":
            self.state = "failed"
            self.detail = en
            self.detail_zh = zh
            self.holding = False
            self.events.append(
                (
                    "skill_failed",
                    {"skill": self.name, "skill_id": self.id, "detail": en, "detail_en": en, "detail_zh": zh},
                )
            )
        else:
            self.holding = False

    def stop(self, en: str, zh: str) -> None:
        """Superseded or interrupted: running skills fail, finished ones stop holding."""
        self.fail(en, zh)

    def _phase(self, name: str, now: float) -> None:
        self.phase = name
        self.phase_t = now
        self.tries = 0
        self.last_try = -1e9

    def _retry(self, now: float) -> bool:
        """True when it is time for another attempt; fails the skill after MAX_TRIES."""
        if now - self.last_try < RETRY_S:
            return False
        if self.tries >= MAX_TRIES:
            return False
        self.tries += 1
        self.last_try = now
        return True

    def _exhausted(self, now: float) -> bool:
        return self.tries >= MAX_TRIES and now - self.last_try >= RETRY_S

    # per tick ----------------------------------------------------------------

    def tick(self, host: Host, now: float) -> StickInput | None:
        """Advance; return the sticks to send this tick (keep-alive) or None."""
        if not self.active:
            return None
        if self.state == "running" and now - self.started > self.timeout:
            self.fail("timed out", "超时")
            return None
        handler = getattr(self, f"_tick_{self.name}")
        self.out = None
        handler(host, now)
        if not self.active:
            return None
        if self.state == "done" and self.name in ("land", "rtl"):
            self.holding = False
            return None
        if self.state == "done" and not host.armed and host.landed in ("ground", "unknown"):
            self.holding = False
            return None
        if self.state == "done" and self.expect_mode and host.mode and host.mode != self.expect_mode:
            self.holding = False
            return None
        return self.out or NEUTRAL

    def _ensure_mode(self, host: Host, name: str, now: float) -> bool:
        """Request ``name`` until the heartbeat confirms it. Returns True once confirmed."""
        if host.mode == name:
            return True
        if self._retry(now):
            host.driver.set_mode(name)
        elif self._exhausted(now):
            self.fail(f"mode {name} not confirmed", f"未确认模式 {name}")
        return False

    def _ensure_armed(self, host: Host, now: float) -> bool:
        if host.armed:
            return True
        ack = host.ack_after(M.MAV_CMD_COMPONENT_ARM_DISARM, self.phase_t)
        if ack is not None and ack["result"] not in (M.MAV_RESULT_ACCEPTED, M.MAV_RESULT_IN_PROGRESS):
            self.fail(f"arming rejected ({ack['result_name']})", f"解锁被拒绝（{ack['result_name']}）")
            return False
        if self._retry(now):
            host.driver.arm(True)
        elif self._exhausted(now):
            self.fail("not armed", "未能解锁")
        return False

    def _check_running_mode(self, host: Host) -> bool:
        if (
            self.expect_mode
            and self.phase not in ("start", "mode")
            and host.mode
            and host.mode != self.expect_mode
        ):
            self.fail(f"mode changed to {host.mode}", f"模式被切换为 {host.mode}")
            return False
        return True

    # skills ------------------------------------------------------------------

    def _tick_takeoff(self, host: Host, now: float) -> None:
        if self.params.get("_stick"):
            self._tick_takeoff_sticks(host, now)
            return
        alt = float(self.params["alt"])
        px4 = host.autopilot == modes.PX4
        if self.state == "done":
            if px4 and self.target_local is not None and now - self.last_setpoint >= SETPOINT_PERIOD_S:
                self.last_setpoint = now
                host.driver.position_target_local(*self.target_local, None)
            return
        if self.phase == "start":
            if host.armed and host.landed in ("air", "takeoff"):
                self.expect_mode = host.mode
                self.finish("already airborne")
                return
            self._phase("arm" if px4 else "mode", now)
        if self.phase == "mode":
            self.expect_mode = "GUIDED"
            if self._ensure_mode(host, "GUIDED", now):
                self._phase("arm", now)
            return
        if self.phase == "arm":
            if host.armed or self._ensure_armed(host, now):
                self._phase("takeoff", now)
            return
        if self.phase == "takeoff":
            if not host.armed:
                self.fail("disarmed", "已上锁")
                return
            if px4 and host.home_amsl() is None:
                local = host.local_enu()
                if local is None:
                    self.fail("no local position", "没有本地位置")
                    return
                self.target_local = (local[0], local[1], alt)
                self.expect_mode = "OFFBOARD"
                self._phase("offboard", now)
                return
            ack = host.ack_after(M.MAV_CMD_NAV_TAKEOFF, self.phase_t)
            if ack is not None:
                if ack["result"] in (M.MAV_RESULT_ACCEPTED, M.MAV_RESULT_IN_PROGRESS):
                    self._phase("climb", now)
                    return
                if self._exhausted(now):
                    self.fail(
                        f"takeoff rejected ({ack['result_name']})", f"起飞被拒绝（{ack['result_name']}）"
                    )
                    return
            if self._retry(now):
                param7 = alt + (host.home_amsl() or 0.0) if px4 else alt
                host.driver.command_long(
                    M.MAV_CMD_NAV_TAKEOFF, (0, 0, 0, math.nan, math.nan, math.nan, param7)
                )
            elif self._exhausted(now):
                self.fail("takeoff not acknowledged", "起飞指令无应答")
            return
        if self.phase == "offboard":
            if now - self.last_setpoint >= SETPOINT_PERIOD_S:
                self.last_setpoint = now
                host.driver.position_target_local(*self.target_local, None)  # type: ignore[misc]
            if now - self.phase_t >= OFFBOARD_PRIME_S:
                self._ensure_mode(host, "OFFBOARD", now)
                if host.mode == "OFFBOARD":
                    self.phase = "climb"
            return
        if self.phase == "climb":
            if self.target_local is not None and now - self.last_setpoint >= SETPOINT_PERIOD_S:
                self.last_setpoint = now
                host.driver.position_target_local(*self.target_local, None)
            if not host.armed:
                self.fail("disarmed", "已上锁")
                return
            altitude = host.altitude()
            if altitude is not None and altitude >= alt - 0.1:
                self.expect_mode = host.mode
                self.finish()

    def _hold_mode(self, host: Host) -> str:
        position = host.has_capability("position")
        if host.autopilot == modes.PX4:
            return "POSCTL" if position else "ALTCTL"
        return "LOITER" if position else "ALT_HOLD"

    def _hold_altitude(self, host: Host) -> float:
        """Throttle stick that drives the altitude towards ``alt_target`` in altitude hold."""
        altitude = host.altitude()
        if self.alt_target is None or altitude is None:
            return 0.5
        error = self.alt_target - altitude
        if abs(error) <= ALT_TOLERANCE_M:
            return 0.5
        # Outside the autopilot's centre dead zone (PX4 MPC_HOLD_DZ, ArduPilot THR_DZ).
        return 0.5 + math.copysign(min(0.22, 0.08 + 0.35 * abs(error)), error)

    def _tick_takeoff_sticks(self, host: Host, now: float) -> None:
        """Takeoff without a position estimate: climb on the throttle stick in altitude hold."""
        alt = float(self.params["alt"])
        if self.state == "done":
            self.out = StickInput(0.0, 0.0, 0.0, self._hold_altitude(host), source="agent")
            return
        if self.phase == "start":
            if host.armed and host.landed in ("air", "takeoff"):
                self.expect_mode = host.mode
                self.finish("already airborne")
                return
            self._phase("mode", now)
        low = StickInput(0.0, 0.0, 0.0, 0.0, source="agent")
        if self.phase == "mode":
            self.out = low
            self.expect_mode = self._hold_mode(host)
            if self._ensure_mode(host, self.expect_mode, now):
                self._phase("arm", now)
            return
        if self.phase == "arm":
            self.out = low
            if host.armed or self._ensure_armed(host, now):
                ground = host.altitude()
                if ground is None:
                    self.fail("no altitude estimate", "没有高度估计")
                    return
                self.alt_target = ground + alt
                self._phase("climb", now)
            return
        if not host.armed:
            self.fail("disarmed", "已上锁")
            return
        if not self._check_running_mode(host):
            return
        altitude = host.altitude()
        assert self.alt_target is not None
        remaining = self.alt_target - (altitude if altitude is not None else -1e9)
        if remaining <= ALT_TOLERANCE_M:
            self.finish()
            self.out = StickInput(0.0, 0.0, 0.0, 0.5, source="agent")
            return
        stick = CLIMB_STICK if remaining > 0.3 else APPROACH_STICK
        self.out = StickInput(0.0, 0.0, 0.0, stick, source="agent")

    def _tick_land(self, host: Host, now: float) -> None:
        if self.params.get("_stick"):
            self._tick_land_sticks(host, now)
            return
        if self.phase == "start":
            self._phase("command", now)
        if self.phase == "command":
            if host.autopilot == modes.PX4:
                if host.mode == "LAND":
                    self._phase("descend", now)
                elif self._retry(now):
                    host.driver.command_long(
                        M.MAV_CMD_NAV_LAND, (0, 0, 0, math.nan, math.nan, math.nan, math.nan)
                    )
                elif self._exhausted(now):
                    self.fail("land not confirmed", "未确认降落")
            elif self._ensure_mode(host, "LAND", now):
                self._phase("descend", now)
            return
        if not host.armed or host.landed == "ground":
            self.finish()
            return
        if host.mode != "LAND":
            self.fail(f"mode changed to {host.mode}", f"模式被切换为 {host.mode}")

    def _tick_land_sticks(self, host: Host, now: float) -> None:
        """PX4 without a position estimate: descend on the stick in ALTCTL, then disarm."""
        if self.phase == "start":
            self.expect_mode = self._hold_mode(host)
            self._phase("mode", now)
        if self.phase == "mode":
            self.out = StickInput(0.0, 0.0, 0.0, 0.5, source="agent")
            if self._ensure_mode(host, self.expect_mode or "ALTCTL", now):
                self._phase("descend", now)
            return
        if not host.armed:
            self.finish()
            return
        if self.phase == "descend":
            if not self._check_running_mode(host):
                return
            altitude = host.altitude()
            ground = self.params.get("_ground")
            low = altitude is not None and ground is not None and altitude - float(ground) < 0.4
            self.out = StickInput(0.0, 0.0, 0.0, FLARE_STICK if low else DESCEND_STICK, source="agent")
            if host.landed == "ground":
                self._phase("disarm", now)
            return
        # Touched down: throttle fully down and ask for a disarm (PX4 also disarms on its own).
        self.out = StickInput(0.0, 0.0, 0.0, 0.0, source="agent")
        if self._retry(now):
            host.driver.arm(False)
        elif self._exhausted(now):
            self.fail("not disarmed after touchdown", "着地后未能上锁")

    def _tick_hold(self, host: Host, now: float) -> None:
        target = self._hold_mode(host)
        self.expect_mode = target
        if self.alt_target is None and host.mode == target and not host.has_capability("position"):
            self.alt_target = host.altitude()
        if self.alt_target is not None:
            self.out = StickInput(0.0, 0.0, 0.0, self._hold_altitude(host), source="agent")
        seconds = float(self.params.get("seconds") or 0.0)
        if self.state == "running" and self._ensure_mode(host, target, now):
            if self.settled_at is None:
                self.settled_at = now
            if now - self.settled_at >= seconds:
                self.finish(f"held {seconds:g} s" if seconds else None)

    def _tick_move(self, host: Host, now: float) -> None:
        if self.target_local is not None:  # closed loop on the position estimate
            self._tick_goto(host, now)
            return
        target_mode = self._hold_mode(host)
        self.expect_mode = target_mode
        if self.phase == "start":
            self._phase("mode", now)
        if self.phase == "mode":
            if self._ensure_mode(host, target_mode, now):
                altitude = host.altitude()
                if altitude is None:
                    self.fail("no altitude estimate", "没有高度估计")
                    return
                self.alt_target = altitude + float(self.params.get("up") or 0.0)
                self._phase("accel", now)
            return
        if not host.armed:
            self.fail("disarmed", "已上锁")
            return
        if not self._check_running_mode(host):
            return
        throttle = self._hold_altitude(host)
        if self.state == "done":
            self.out = StickInput(0.0, 0.0, 0.0, throttle, source="agent")
            return
        pitch, roll, t1 = self.profile or (0.0, 0.0, 0.0)
        distance = math.hypot(self.params.get("forward") or 0.0, self.params.get("right") or 0.0)
        elapsed = now - self.phase_t
        self._dead_reckon(host, now)
        if self.phase == "accel":
            self.out = StickInput(roll, pitch, 0.0, throttle, source="agent")
            v = self.est_v
            stopping = v * v / (2.0 * (MOVE_ACCEL + 0.5 * MOVE_DRAG * v)) if v > 0 else 0.0
            if distance == 0.0 or self.est_d + stopping >= distance or elapsed >= 2.0 * t1 + 1.0:
                self._phase("brake", now)
            return
        if self.phase == "brake":
            self.out = StickInput(-roll, -pitch, 0.0, throttle, source="agent")
            if distance == 0.0 or self.est_v <= 0.03 or elapsed >= 2.0 * t1 + 1.0:
                self._phase("settle", now)
            return
        self.out = StickInput(0.0, 0.0, 0.0, throttle, source="agent")
        altitude = host.altitude()
        level = altitude is None or self.alt_target is None or abs(self.alt_target - altitude) <= 0.12
        if elapsed >= MOVE_SETTLE_S and (level or elapsed >= MOVE_SETTLE_S + 4.0):
            self.finish(f"open loop, about {self.est_d:.1f} m" if distance else None)

    def _dead_reckon(self, host: Host, now: float) -> None:
        """Integrate speed along the move from the measured tilt and a linear drag model."""
        if self.est_t is None:
            self.est_t = now
            return
        dt = min(0.1, max(0.0, now - self.est_t))
        self.est_t = now
        tilt = host.tilt()
        forward = float(self.params.get("forward") or 0.0)
        right = float(self.params.get("right") or 0.0)
        distance = math.hypot(forward, right)
        if tilt is None or distance < 1e-3:
            return
        roll, pitch = tilt
        # Nose down (negative pitch) accelerates forward; right wing down accelerates right.
        accel = GRAVITY * (math.tan(-pitch) * forward + math.tan(roll) * right) / distance
        self.est_v += (accel - MOVE_DRAG * self.est_v) * dt
        self.est_d += self.est_v * dt

    def _tick_yaw(self, host: Host, now: float) -> None:
        target_mode = host.mode if host.mode in modes.HOLD_STICK_MODES else self._hold_mode(host)
        self.expect_mode = target_mode
        if self.phase == "start":
            self._phase("mode", now)
        if self.phase == "mode":
            if self._ensure_mode(host, target_mode, now):
                heading = host.heading()
                if heading is None:
                    self.fail("no heading", "没有航向")
                    return
                self.heading_target = heading + math.radians(float(self.params["deg"]))
                if not host.has_capability("position"):
                    self.alt_target = host.altitude()
                self._phase("turn", now)
            return
        if not host.armed:
            self.fail("disarmed", "已上锁")
            return
        if not self._check_running_mode(host):
            return
        throttle = self._hold_altitude(host) if self.alt_target is not None else 0.5
        heading = host.heading()
        if self.state == "done" or heading is None or self.heading_target is None:
            self.out = StickInput(0.0, 0.0, 0.0, throttle, source="agent")
            return
        error = math.degrees(_wrap(self.heading_target - heading))
        if abs(error) <= YAW_TOLERANCE_DEG:
            self.out = StickInput(0.0, 0.0, 0.0, throttle, source="agent")
            if self.settled_at is None:
                self.settled_at = now
            if now - self.settled_at >= YAW_SETTLE_S:
                self.finish(f"within {abs(error):.0f} deg")
            return
        self.settled_at = None
        stick = math.copysign(min(0.3, max(0.07, abs(error) / 90.0)), error)
        self.out = StickInput(0.0, 0.0, stick, throttle, source="agent")

    def _tick_rtl(self, host: Host, now: float) -> None:
        if self.phase == "start":
            self._phase("command", now)
        if self.phase == "command":
            if host.autopilot == modes.PX4:
                if host.mode == "RTL":
                    self._phase("return", now)
                elif self._retry(now):
                    host.driver.command_long(M.MAV_CMD_NAV_RETURN_TO_LAUNCH, ())
                elif self._exhausted(now):
                    self.fail("RTL not confirmed", "未确认返航")
            elif self._ensure_mode(host, "RTL", now):
                self._phase("return", now)
            return
        if not host.armed and host.landed in ("ground", "unknown"):
            self.finish()
            return
        if host.mode not in ("RTL", "LAND"):
            self.fail(f"mode changed to {host.mode}", f"模式被切换为 {host.mode}")

    def _tick_goto(self, host: Host, now: float) -> None:
        assert self.target_local is not None
        px4 = host.autopilot == modes.PX4
        mode = "OFFBOARD" if px4 else "GUIDED"
        self.expect_mode = mode
        if (px4 or host.mode == mode) and now - self.last_setpoint >= SETPOINT_PERIOD_S:
            self.last_setpoint = now
            host.driver.position_target_local(*self.target_local, self.yaw)
        if self.state == "done":
            return
        if self.phase == "start":
            self._phase("mode", now)
        if self.phase == "mode":
            if px4 and now - self.phase_t < OFFBOARD_PRIME_S and host.mode != mode:
                return
            if self._ensure_mode(host, mode, now):
                self._phase("fly", now)
            return
        if not host.armed:
            self.fail("disarmed", "已上锁")
            return
        if not self._check_running_mode(host):
            return
        local = host.local_enu()
        if local is None:
            return
        dist = math.dist(local, self.target_local)
        if dist <= GOTO_TOLERANCE_M:
            self.finish(f"reached within {dist:.2f} m")


def _wrap(angle: float) -> float:
    return (angle + math.pi) % (2 * math.pi) - math.pi


def max_tilt_deg(autopilot: str | None, param: Any) -> float:
    """Stick-to-tilt scale: full stick deflection tilts this far in the stick modes."""
    if autopilot == modes.PX4:
        value = param("MPC_MAN_TILT_MAX", 35.0)
    else:
        value = param("ANGLE_MAX", 3000.0) / 100.0
    return min(60.0, max(10.0, float(value)))


def open_loop_profile(forward: float, right: float, tilt_max_deg: float) -> tuple[float, float, float]:
    """(pitch, roll, t1) for an accelerate-brake move of the given body-frame offset."""
    distance = math.hypot(forward, right)
    if distance < 1e-3:
        return (0.0, 0.0, 0.0)
    tilt = math.degrees(math.atan(MOVE_ACCEL / GRAVITY))
    stick = min(0.4, max(0.05, tilt / tilt_max_deg))
    t1 = math.sqrt(distance / MOVE_ACCEL)
    return (stick * forward / distance, stick * right / distance, t1)


def make(
    name: str, params: dict[str, Any], now: float, target_local: tuple[float, float, float] | None = None
) -> Skill:
    skill = Skill(name=name, params=params, started=now)
    if name == "takeoff":
        skill.timeout = 30.0
    elif name == "land":
        skill.timeout = 90.0
    elif name == "hold":
        skill.timeout = 8.0 + float(params.get("seconds") or 0.0)
    elif name == "rtl":
        skill.timeout = 180.0
    elif name == "goto":
        skill.target_local = target_local
        yaw = params.get("yaw")
        skill.yaw = None if yaw is None else math.radians(float(yaw))
        skill.timeout = 15.0 + 2.5 * float(params.get("distance", 3.0))
    elif name == "move":
        skill.target_local = target_local
        profile = params.get("_profile")
        skill.profile = tuple(profile) if profile else None  # type: ignore[assignment]
        t1 = skill.profile[2] if skill.profile else 0.0
        skill.timeout = (
            15.0 + 2.5 * float(params.get("distance", 0.0)) + 2 * t1 + abs(float(params.get("up") or 0)) * 3
        )
    elif name == "yaw":
        skill.timeout = 6.0 + abs(float(params["deg"])) / 20.0
    return skill
