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

from . import sequence as sequence_mod
from .mav import modes
from .mav.mavlink import M
from .model import StickInput

NEUTRAL = StickInput(roll=0.0, pitch=0.0, yaw=0.0, throttle=0.5, source="agent")
RETRY_S = 1.2
ARM_PRIME_S = 0.3  # sticks reach the autopilot before a skill sends the arm command
LANDED_THROTTLE = 0.3  # a timeline throttle at or below this lets the autopilot disarm after landing
REPLAY_MODE_GRACE_S = 3.0  # time a replay gives the autopilot to take a recorded mode change
MAX_TRIES = 3
SETPOINT_PERIOD_S = 0.1
GOTO_TOLERANCE_M = 0.15
OFFBOARD_PRIME_S = 1.0

# Altitude-hold stick values (throttle 0.5 holds altitude in ALT_HOLD / ALTCTL). A plan's tuning moves the
# climb stick and the hold centre; the approach, descent and flare sticks keep their distance to the centre.
CLIMB_STICK = 0.72
APPROACH_SHARE = 0.55  # approach = centre + share x (climb - centre): 0.62 with the defaults
DESCEND_STICK = 0.3
FLARE_STICK = 0.36
ALT_TOLERANCE_M = 0.06
# Open-loop move: tilt towards the target, brake when the dead-reckoned stopping
# point reaches it, settle (03 §5.7). Speed is integrated from the measured tilt.
MOVE_ACCEL = 0.8
MOVE_DRAG = 0.35  # 1/s, linear drag of a small quadrotor
MOVE_SETTLE_S = 1.2
MOVE_OPEN_LOOP_MAX_M = 2.0
PX4_ARMABLE = frozenset({"MANUAL", "STABILIZED", "ACRO", "ALTCTL", "POSCTL", "LOITER", "OFFBOARD", "TAKEOFF"})
YAW_TOLERANCE_DEG = 3.0
YAW_SETTLE_S = 0.3
GRAVITY = 9.81
ATTITUDE_PERIOD_S = 0.05  # SET_ATTITUDE_TARGET at 20 Hz (04 §2.2)
VELOCITY_PERIOD_S = 0.1  # velocity setpoints at 10 Hz
BRAKE_SPEED_MPS = 0.2
BRAKE_MAX_S = 2.5
BRAKE_DECEL = 3.0  # m/s2 assumed when looking ahead for the safety volume

REQUIREMENTS = {
    "takeoff": "althold",
    "land": "any",
    "hold": "althold",
    "rtl": "position",
    "goto": "position",
    "move": "althold",
    "yaw": "althold",
    "sticks": "manual",
    "attitude": "manual",
    "velocity": "position",
}
SKILL_NAMES = tuple(REQUIREMENTS)
# Skills only the fleet starts itself; not part of the public catalogue (04 §4).
INTERNAL_REQUIREMENTS = {"replay": "manual", "sequence": "manual"}


def clamp01(x: float) -> float:
    return min(1.0, max(0.0, x))


def requirement(name: str) -> str:
    return REQUIREMENTS.get(name) or INTERNAL_REQUIREMENTS[name]


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
    def speed(self) -> float | None: ...
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
    mode_at: float = 0.0  # when the replay last asked for a mode
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
    # throttle tuning of the stick variants: {climb?, hover?} (plans.normalise_tuning); a plan shares its dict
    tuning: dict[str, float] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.state == "running" or (self.state == "done" and self.holding)

    def view(self) -> dict[str, Any]:
        out = {"id": self.id, "name": self.name, "state": self.state, "detail": self.detail}
        if self.name == "sequence":
            out["progress_s"] = round(float(self.params.get("_progress") or 0.0), 2)
            out["duration_s"] = self.params["_seq"]["duration_s"]
            out["phase"] = self.phase
        return out

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
        return self.out or self._neutral()

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
        if self.out is None:
            # a stick mode arms only with pilot input present and the throttle down (10 §1)
            self.out = StickInput(0.0, 0.0, 0.0, 0.0, source="agent")
        if now - self.phase_t < ARM_PRIME_S:
            return False  # let the autopilot see the sticks before the arm command
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
            # PX4 refuses to arm in LAND, RTL and other automatic modes, where a previous land leaves it.
            armable = not px4 or host.mode in PX4_ARMABLE
            self._phase("arm" if armable and px4 else "mode", now)
        if self.phase == "mode":
            target = "POSCTL" if px4 else "GUIDED"
            self.expect_mode = target
            if self._ensure_mode(host, target, now):
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

    def _centre(self) -> float:
        """The throttle stick that holds the height: 0.5, or the plan's ``hover`` tuning."""
        return float(self.tuning.get("hover", 0.5))

    def _climb(self) -> float:
        return float(self.tuning.get("climb", CLIMB_STICK))

    def _neutral(self) -> StickInput:
        if "hover" not in self.tuning:
            return NEUTRAL
        return StickInput(0.0, 0.0, 0.0, self._centre(), source="agent")

    def _hold_altitude(self, host: Host) -> float:
        """Throttle stick that drives the altitude towards ``alt_target`` in altitude hold."""
        centre = self._centre()
        altitude = host.altitude()
        if self.alt_target is None or altitude is None:
            return centre
        error = self.alt_target - altitude
        if abs(error) <= ALT_TOLERANCE_M:
            return centre
        # Outside the autopilot's centre dead zone (PX4 MPC_HOLD_DZ, ArduPilot THR_DZ).
        return clamp01(centre + math.copysign(min(0.22, 0.08 + 0.35 * abs(error)), error))

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
        centre, climb = self._centre(), self._climb()
        if remaining <= ALT_TOLERANCE_M:
            self.finish()
            self.out = StickInput(0.0, 0.0, 0.0, centre, source="agent")
            return
        stick = climb if remaining > 0.3 else centre + APPROACH_SHARE * (climb - centre)
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
            self.out = StickInput(0.0, 0.0, 0.0, self._centre(), source="agent")
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
            shift = self._centre() - 0.5
            stick = (FLARE_STICK if low else DESCEND_STICK) + shift
            self.out = StickInput(0.0, 0.0, 0.0, stick, source="agent")
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
        throttle = self._hold_altitude(host) if self.alt_target is not None else self._centre()
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

    def _tick_sticks(self, host: Host, now: float) -> None:
        """Timed stick output; afterwards the sticks return to centre (04 §2.2)."""
        p = self.params
        rest = StickInput(
            0.0,
            0.0,
            0.0,
            self._centre() if host.mode in modes.HOLD_STICK_MODES else float(p["throttle"]),
            source="agent",
        )
        if self.state == "done":
            self.out = rest
            return
        if self.phase == "start":
            self.expect_mode = host.mode
            self._phase("run", now)
        if not host.armed:
            self.fail("disarmed", "已上锁")
            return
        if not self._check_running_mode(host):
            return
        seconds = float(p["seconds"])
        if now - self.phase_t >= seconds:
            self.out = rest
            self.finish(f"sticks held {seconds:g} s")
            return
        self.out = StickInput(
            float(p["roll"]), float(p["pitch"]), float(p["yaw"]), float(p["throttle"]), source="agent"
        )

    def _althold_mode(self, host: Host) -> str:
        return modes.possess_mode(host.autopilot, host.has_capability("althold")) or "ALT_HOLD"

    def _tick_attitude(self, host: Host, now: float) -> None:
        """Attitude targets at 20 Hz in GUIDED_NOGPS / OFFBOARD, then back to altitude hold (04 §2.2)."""
        px4 = host.autopilot == modes.PX4
        mode = "OFFBOARD" if px4 else "GUIDED_NOGPS"
        if self.state == "done":
            self.out = StickInput(0.0, 0.0, 0.0, self._hold_altitude(host), source="agent")
            return
        if self.phase == "start":
            heading = host.heading()
            if heading is None:
                self.fail("no heading", "没有航向")
                return
            self.heading_target = heading
            self._phase("prime" if px4 else "mode", now)
        if not host.armed:
            self.fail("disarmed", "已上锁")
            return
        if self.phase in ("prime", "mode", "run"):
            self._send_attitude(host, now, streaming=self.phase == "run")
        if self.phase == "prime":
            if now - self.phase_t >= OFFBOARD_PRIME_S:
                self._phase("mode", now)
            return
        if self.phase == "mode":
            if self._ensure_mode(host, mode, now):
                self.expect_mode = mode
                self._phase("run", now)
            return
        if self.phase == "run":
            if not self._check_running_mode(host):
                return
            if now - self.phase_t >= float(self.params["seconds"]):
                self.expect_mode = None
                self._phase("restore", now)
            return
        target = self._althold_mode(host)
        if self._ensure_mode(host, target, now):
            self.alt_target = host.altitude()
            self.expect_mode = target
            self.finish(f"attitude held {float(self.params['seconds']):g} s")

    def _send_attitude(self, host: Host, now: float, streaming: bool) -> None:
        if now - self.last_setpoint < ATTITUDE_PERIOD_S:
            return
        p = self.params
        dt = min(0.2, now - self.last_setpoint) if streaming and self.last_setpoint > 0 else 0.0
        self.last_setpoint = now
        rate = math.radians(float(p.get("yaw_rate_dps") or 0.0))
        if streaming and self.heading_target is not None:
            self.heading_target = _wrap(self.heading_target + rate * dt)
        roll = math.radians(float(p.get("roll_deg") or 0.0)) if streaming else 0.0
        pitch = math.radians(float(p.get("pitch_deg") or 0.0)) if streaming else 0.0
        thrust = p.get("thrust")
        thrust = float(p["_hover"] if thrust is None else thrust)
        host.driver.attitude_target(
            roll, pitch, self.heading_target or 0.0, rate if streaming else 0.0, thrust
        )

    def _tick_velocity(self, host: Host, now: float) -> None:
        """Velocity setpoints at 10 Hz inside the safety volume (04 §2.2)."""
        px4 = host.autopilot == modes.PX4
        mode = "OFFBOARD" if px4 else "GUIDED"
        self.expect_mode = mode
        if self.state == "done":
            streaming = self.target_local is not None and (px4 or host.mode == mode)
            if streaming and now - self.last_setpoint >= SETPOINT_PERIOD_S:
                self.last_setpoint = now
                host.driver.position_target_local(*self.target_local, None)  # type: ignore[misc]
            return
        local = host.local_enu()
        if local is None:
            self.fail("no local position", "没有本地位置")
            return
        if self.phase == "start":
            self._phase("prime" if px4 else "mode", now)
        if not host.armed:
            self.fail("disarmed", "已上锁")
            return
        if self.phase in ("prime", "mode", "brake", "violation"):
            self._send_velocity(host, now, 0.0, 0.0, 0.0, None)
        if self.phase == "prime":
            if now - self.phase_t >= OFFBOARD_PRIME_S:
                self._phase("mode", now)
            return
        if self.phase == "mode":
            if self._ensure_mode(host, mode, now):
                self._phase("run", now)
            return
        if not self._check_running_mode(host):
            return
        p = self.params
        if self.phase == "run":
            vx, vy, vz = (float(p[k]) for k in ("vx", "vy", "vz"))
            box = p.get("_box")
            ahead = tuple(c + _reach(v) for c, v in zip(local, (vx, vy, vz), strict=True))
            if box and not _inside(box, ahead):
                self._phase("violation", now)
                return
            rate = p.get("yaw_rate_dps")
            self._send_velocity(host, now, vx, vy, vz, None if rate is None else math.radians(float(rate)))
            if now - self.phase_t >= float(p["seconds"]):
                self._phase("brake", now)
            return
        speed = host.speed()
        if (speed is not None and speed <= BRAKE_SPEED_MPS) or now - self.phase_t >= BRAKE_MAX_S:
            self.target_local = local
            if self.phase == "violation":  # braked inside the volume: hold here and report why it stopped
                host.driver.position_target_local(*local, None)
                self.fail("outside_safety_volume", "超出安全区")
                return
            self.finish(f"velocity held {float(p['seconds']):g} s")

    def _send_velocity(
        self, host: Host, now: float, vx: float, vy: float, vz: float, yaw_rate: float | None
    ) -> None:
        if now - self.last_setpoint < VELOCITY_PERIOD_S:
            return
        self.last_setpoint = now
        host.driver.velocity_target(vx, vy, vz, yaw_rate)

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

    def _idle(self, host: Host) -> StickInput:
        """Neutral sticks while arming: zero throttle, except mid throttle where PX4 holds altitude.

        ArduPilot (4.6 and later) refuses to arm with any throttle input in every mode ("Throttle (RC3) is
        not neutral") unless the throttle is centre-sprung (PILOT_THR_BHV bit 0)."""
        hold = self.expect_mode in modes.HOLD_STICK_MODES and host.autopilot == modes.PX4
        return StickInput(0.0, 0.0, 0.0, 0.5 if hold else 0.0, source="agent")

    def _rest(self, last: tuple[float, ...]) -> StickInput:
        """After a replay: centred sticks; mid throttle where the mode holds altitude, else the last one."""
        hold = self.expect_mode in modes.HOLD_STICK_MODES
        return StickInput(0.0, 0.0, 0.0, 0.5 if hold else last[4], source="agent")

    def _tick_replay(self, host: Host, now: float) -> None:
        """Recorded sticks on the original time line: mode, arm, then play (04 §4)."""
        samples = self.params["_samples"]
        speed = float(self.params.get("speed") or 1.0)
        duration = float(self.params["duration_s"])
        if self.state == "done":
            self.out = self._rest(samples[-1])
            return
        if self.phase == "start":
            self.expect_mode = self.params["mode"]
            self._phase("mode", now)
        # joining in the air: hold the height, not the zero-throttle arming idle (records start on the ground)
        airborne = host.armed and host.landed != "ground"
        hold = StickInput(0.0, 0.0, 0.0, self._hold_altitude(host), source="agent") if airborne else None
        if self.phase == "mode":
            self.out = hold or self._idle(host)
            if self._ensure_mode(host, self.expect_mode, now):
                self._phase("arm", now)
            return
        if self.phase == "arm":
            self.out = hold or self._idle(host)
            if host.armed or self._ensure_armed(host, now):
                self._phase("play", now)
                self.mode_at = now
            return
        if not host.armed:
            self.fail("disarmed", "已上锁")
            return
        elapsed = (now - self.phase_t) * speed
        wanted = self.expect_mode
        for t, name in self.params.get("_modes") or ():
            if t <= elapsed:
                wanted = name
        if wanted != self.expect_mode:  # the record changed mode here (an autopilot landing, say)
            self.expect_mode = wanted
            self.last_try = -1e9
            self.mode_at = now
        if host.mode and host.mode != self.expect_mode:
            if now - self.mode_at > REPLAY_MODE_GRACE_S:
                # a mode the record never asked for (a failsafe, the operator): the replay must not fight it
                self.fail(f"mode changed to {host.mode}", f"模式被切换为 {host.mode}")
                return
            if now - self.last_try >= RETRY_S:
                self.last_try = now
                host.driver.set_mode(self.expect_mode)
        if elapsed >= duration:
            self.out = self._rest(samples[-1])
            self.finish(f"replayed {duration:.1f} s of sticks")
            return
        self.out = stick_row(sticks_at(samples, elapsed))

    def _tick_sequence(self, host: Host, now: float) -> None:
        """An ``.ars`` timeline (10 §2): mode, arm, play the tracks with the live trim, then end."""
        seq = self.params["_seq"]
        trim = self.params["_trim"]  # shared with the fleet: PATCH /sequence/trim changes it in flight
        speed = float(self.params.get("speed") or 1.0)
        start_at = float(self.params.get("start_at") or 0.0)
        duration = float(seq["duration_s"])
        if self.state == "done":
            # stopped early: hold what the timeline was sending at that moment, not its end
            rest_at = min(duration, float(self.params.get("_rest_at", duration)))
            self.out = self._sequence_rest(seq, trim, rest_at)
            return
        if self.phase == "start":
            self.expect_mode = self.params["mode"]
            self._phase("mode", now)
        # joining in the air: the first frame of the timeline, never the zero-throttle arming idle
        airborne = host.armed and host.landed != "ground"
        first = StickInput(*sequence_mod.sample(seq, start_at, trim), source="agent") if airborne else None
        if self.phase == "mode":
            self.out = first or self._idle(host)
            if self._ensure_mode(host, self.expect_mode, now):
                self._phase("arm", now)
            return
        if self.phase == "arm":
            self.out = first or self._idle(host)
            if host.armed or self._ensure_armed(host, now):
                self._phase("play", now)
            return
        if self.phase == "end":
            self.out = StickInput(0.0, 0.0, 0.0, 0.0, source="agent")
            if not host.armed:
                self.finish(f"played {duration:.1f} s; disarmed on the ground")
            elif host.landed != "ground":
                self.finish(f"played {duration:.1f} s; still airborne, holding")
                self.phase = "hold"
            elif self._retry(now):
                host.driver.arm(False)
            elif self._exhausted(now):
                self.finish(f"played {duration:.1f} s; the autopilot kept the motors armed")
            return
        if not host.armed:
            elapsed = start_at + (now - self.phase_t) * speed
            if host.landed in ("ground", "unknown") and sample_throttle(seq, elapsed) <= LANDED_THROTTLE:
                self.finish(f"disarmed by the autopilot at {elapsed:.1f} s")  # auto-disarm after the landing
                self.holding = False
            else:
                self.fail("disarmed", "已上锁")
            return
        if not self._check_running_mode(host):
            return
        elapsed = start_at + (now - self.phase_t) * speed
        self.params["_progress"] = min(elapsed, duration)
        if elapsed >= duration:
            # only a confirmed landing ends with zero throttle: "unknown" may be in the air
            if seq["end"] == "disarm" and host.landed == "ground":
                self._phase("end", now)
                self.out = StickInput(0.0, 0.0, 0.0, 0.0, source="agent")
                return
            self.finish(f"played {duration:.1f} s")
            self.out = self._sequence_rest(seq, trim, duration)
            return
        roll, pitch, yaw, throttle = sequence_mod.sample(seq, elapsed, trim)
        self.out = StickInput(roll, pitch, yaw, throttle, source="agent")

    def _sequence_rest(self, seq: dict[str, Any], trim: dict[str, float], at: float) -> StickInput:
        """After the timeline: attitude trim only; mid throttle where the mode holds altitude, else the
        throttle at ``at`` (the end, or where it was stopped)."""
        hold = self.expect_mode in modes.HOLD_STICK_MODES
        last = sequence_mod.sample(seq, at, trim)
        throttle = clamp01(0.5 + trim.get("throttle", 0.0)) if hold else last[3]
        return StickInput(
            trim.get("roll", 0.0), trim.get("pitch", 0.0), trim.get("yaw", 0.0), throttle, source="agent"
        )


def sample_throttle(seq: dict[str, Any], t: float) -> float:
    return sequence_mod.sample(seq, t)[3]


def stick_row(row: tuple[float, ...]) -> StickInput:
    return StickInput(row[1], row[2], row[3], row[4], source="agent")


def sticks_at(samples: list[tuple[float, ...]], t: float) -> tuple[float, ...]:
    """Linear interpolation of ``(t, roll, pitch, yaw, throttle)`` rows; clamped to the ends."""
    if t <= samples[0][0]:
        return samples[0]
    if t >= samples[-1][0]:
        return samples[-1]
    lo, hi = 0, len(samples) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if samples[mid][0] <= t:
            lo = mid
        else:
            hi = mid
    a, b = samples[lo], samples[hi]
    f = (t - a[0]) / (b[0] - a[0]) if b[0] > a[0] else 0.0
    return (t, *(a[i] + (b[i] - a[i]) * f for i in range(1, 5)))


def _reach(v: float) -> float:
    """Distance an aircraft moving at ``v`` m/s covers before it can stop: reaction time plus braking."""
    return v * 0.3 + math.copysign(v * v / (2.0 * BRAKE_DECEL), v)


def _inside(box: Any, p: tuple[float, float, float]) -> bool:
    """``box`` is (xmin, xmax, ymin, ymax, zmin, zmax) in the vehicle's local ENU frame."""
    return box[0] <= p[0] <= box[1] and box[2] <= p[1] <= box[3] and box[4] <= p[2] <= box[5]


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
    elif name == "sticks":
        skill.timeout = float(params["seconds"]) + 3.0
    elif name == "attitude":
        skill.timeout = float(params["seconds"]) + 12.0
    elif name == "velocity":
        skill.timeout = float(params["seconds"]) + 15.0
    elif name == "replay":
        skill.timeout = 40.0 + params["_samples"][-1][0] / float(params.get("speed") or 1.0)
    elif name == "sequence":
        rest = params["_seq"]["duration_s"] - float(params.get("start_at") or 0.0)
        skill.timeout = 40.0 + max(0.0, rest) / float(params.get("speed") or 1.0)
    return skill
