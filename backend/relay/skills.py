"""Skill executor: takeoff, land, hold, rtl, goto (03 §5.6).

A skill is the vehicle's single stick source while it is active. It streams
neutral sticks at the stick rate as an RC keep-alive (so an autopilot that
has seen Relay's input does not declare RC loss in GUIDED / OFFBOARD), and for
PX4 goto it streams the OFFBOARD setpoint at 10 Hz. After a skill finishes it
keeps holding (keep-alive, setpoint) until it is superseded, the controller
changes, the link is lost or the aircraft is disarmed on the ground.
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

REQUIREMENTS = {"takeoff": "althold", "land": "any", "hold": "althold", "rtl": "position", "goto": "position"}
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
        return NEUTRAL

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

    def _tick_land(self, host: Host, now: float) -> None:
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

    def _tick_hold(self, host: Host, now: float) -> None:
        position = host.has_capability("position")
        if host.autopilot == modes.PX4:
            target = "POSCTL" if position else "ALTCTL"
        else:
            target = "LOITER" if position else "ALT_HOLD"
        self.expect_mode = target
        if self.state == "running" and self._ensure_mode(host, target, now):
            self.finish()

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


def make(
    name: str, params: dict[str, Any], now: float, target_local: tuple[float, float, float] | None = None
) -> Skill:
    skill = Skill(name=name, params=params, started=now)
    if name == "takeoff":
        skill.timeout = 30.0
    elif name == "land":
        skill.timeout = 90.0
    elif name == "hold":
        skill.timeout = 8.0
    elif name == "rtl":
        skill.timeout = 180.0
    elif name == "goto":
        skill.target_local = target_local
        yaw = params.get("yaw")
        skill.yaw = None if yaw is None else math.radians(float(yaw))
        skill.timeout = 15.0 + 2.5 * float(params.get("distance", 3.0))
    return skill
