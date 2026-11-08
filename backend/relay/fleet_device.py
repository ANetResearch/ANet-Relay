"""Device-level operations of the fleet service (04 §2).

A mixin for ``FleetService``: single messages and parameters, raw commands, motors, actuators, RC channel
frames, reboot, calibration, home and flight termination. Everything that touches vehicle state runs on the
fleet thread through ``self.call``; waiting for a ``COMMAND_ACK`` happens on the caller's thread by polling.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

from . import authority as auth
from . import (
    device,
    flight_level,  # WP-1 (06 §5)
)
from .authority import Actor
from .errors import err
from .mav import decode, modes
from .mav.mavlink import M

KEY_MESSAGES = ("HEARTBEAT", "SYS_STATUS", "ATTITUDE", "VFR_HUD", "BATTERY_STATUS")
MOTION_COMMANDS = frozenset(
    {
        M.MAV_CMD_DO_SET_MODE,
        M.MAV_CMD_NAV_TAKEOFF,
        M.MAV_CMD_NAV_LAND,
        M.MAV_CMD_NAV_RETURN_TO_LAUNCH,
        M.MAV_CMD_NAV_LOITER_UNLIM,
        M.MAV_CMD_DO_REPOSITION,
        M.MAV_CMD_MISSION_START,
        M.MAV_CMD_DO_PAUSE_CONTINUE,
    }
)
MOTOR_GAP_S = 0.3  # pause between motors in an "all" sequence
REAL_MOTOR_PCT = 30.0  # motor-test throttle cap on a real aircraft
MOTOR_TEST_S = (0.2, 5.0)  # motor-test duration range
DONE_RESULTS = (M.MAV_RESULT_IN_PROGRESS,)
RC_AXES = ("roll", "pitch", "yaw", "throttle", "aux1", "aux2", "aux3", "aux4", "aux5", "aux6")
ACK_WAIT_S = 3.0
RESTREAM_AFTER_S = (3.0, 6.0, 10.0, 16.0)  # stream requests after a reboot, in case the first ones are lost


def _ack_view(ack: dict[str, Any] | None) -> dict[str, Any] | None:
    if ack is None:
        return None
    return {"result": ack["result"], "result_name": ack["result_name"], "progress": ack.get("progress", 0)}


def _number(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise err("invalid_request", reason=f"{name} must be a number")
    if not low <= float(value) <= high:
        raise err("invalid_request", reason=f"{name} must be {low:g} to {high:g}")
    return float(value)


class DeviceOps:
    """Mixed into ``FleetService``; relies on its ``call``, ``_vehicle``, ``events`` and ``clock``."""

    # ------------------------------------------------------------------ helpers

    def _gate(self, v: Any, actor: Actor, either: bool = False) -> None:
        """Controller only, or (``either``) the operator or the controller."""
        if either:
            auth.check_operator_or_controller(v.auth, actor, self._claim_alive(v))
        else:
            self._check_control(v, actor)

    def _emit_device(
        self, kind: str, v: Any, actor: Actor, data: dict[str, Any], labels: dict[str, Any] | None = None
    ) -> None:
        data = {**data, "by": actor.kind}
        self.events.emit(
            kind, v.id, session=actor.session_id, data=data, labels={"vehicle": v.label, **(labels or {})}
        )

    def _command_flow(
        self,
        vid: str,
        cmd: int,
        send: Callable[[Any, float], dict[str, Any]],
        timeout_s: float = ACK_WAIT_S,
    ) -> dict[str, Any]:
        """Run ``send`` on the fleet thread, then wait for the COMMAND_ACK of ``cmd`` (None on timeout)."""

        # Taken before the call: the fleet loop stamps replies with the time its tick began.
        t0 = self.clock()

        def start() -> dict[str, Any]:
            return send(self._vehicle(vid), self.clock())

        info = self.call(start)
        ack = self._wait_ack(vid, cmd, t0, timeout_s)
        return {**info, "ack": _ack_view(ack)}

    def _wait_ack(self, vid: str, cmd: int, t0: float, timeout_s: float) -> dict[str, Any] | None:
        if not self.threaded:
            return None
        seen: dict[str, Any] | None = None
        while self.clock() < t0 + timeout_s:
            ack = self.call(lambda: self._vehicle(vid).ack_after(cmd, t0))
            if ack is not None:
                seen = ack
                if ack["result"] not in DONE_RESULTS:
                    return ack
            time.sleep(0.05)
        return seen

    def _motor_count(self, v: Any) -> int:
        return device.motor_layout(v.state["mav_type"])[0]

    def _motor_command(self, v: Any, motor: int, pct: float, seconds: float) -> None:
        if v.autopilot == modes.PX4:
            v.driver.command_long(
                M.MAV_CMD_ACTUATOR_TEST, (pct / 100.0, seconds, 0.0, 0.0, 100.0 + motor, 0.0, 0.0)
            )
        else:
            v.driver.command_long(M.MAV_CMD_DO_MOTOR_TEST, (float(motor), 0.0, pct, seconds, 0.0, 0.0, 0.0))

    def _stop_motors(self, v: Any) -> None:
        v.motor_seq = None
        v.motor_until = 0.0
        for motor in range(1, self._motor_count(v) + 1):
            if v.autopilot == modes.PX4:
                v.driver.command_long(
                    M.MAV_CMD_ACTUATOR_TEST, (math.nan, 0.0, 0.0, 0.0, 100.0 + motor, 0.0, 0.0)
                )
            else:
                v.driver.command_long(M.MAV_CMD_DO_MOTOR_TEST, (float(motor), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0))

    def _restream(self, v: Any) -> None:
        if v.link_state == "ok":
            self._request_streams(v)

    def _device_tick(self, v: Any, now: float) -> None:
        """Deferred actions (restoring an output, the next motor of a sequence), 50 Hz on the fleet thread."""
        self._procedure_tick(v, now)  # type: ignore[attr-defined]  # bench procedure (WP-B, 05 §4.3)
        if v.deferred:
            due = [item for item in v.deferred if item[0] <= now]
            v.deferred = [item for item in v.deferred if item[0] > now]
            for _, action in due:
                action(v)
        seq = v.motor_seq
        if seq is None:
            return
        if v.armed or v.link_state not in ("ok", "delayed"):
            v.motor_seq = None
            return
        if now >= seq["next_at"]:
            if not seq["queue"]:
                v.motor_seq = None
                return
            motor = seq["queue"].pop(0)
            self._motor_command(v, motor, seq["pct"], seq["seconds"])
            seq["next_at"] = now + seq["seconds"] + MOTOR_GAP_S

    # ------------------------------------------------------------------ reads

    def device_overview(self, vid: str) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            st = v.state
            now = self.clock()
            count, layout = device.motor_layout(st["mav_type"])
            servo = st["servo16"]
            if st["ekf_flags"] is not None:
                estimator: dict[str, Any] | None = {"kind": "ekf", "flags": st["ekf_flags"]}
            elif st["estimator_flags"] is not None:
                estimator = {"kind": "estimator", "flags": st["estimator_flags"]}
            else:
                estimator = None
            if estimator is not None:
                estimator["position_ok"] = v.position_ok(now)
            return {
                "id": vid,
                "motors": {
                    "count": count,
                    "layout": layout,
                    "outputs_us": list(servo[:count]) if servo else [],
                    "test_supported": v.autopilot in (modes.PX4, modes.ARDUPILOT)
                    and v.link_state in ("ok", "delayed"),
                },
                "servos": [{"index": i + 1, "us": us} for i, us in enumerate(servo)] if servo else [],
                "rc": list(st["rc"] or []),
                "frame": device.frame_view(st["mav_type"]),
                "sensors": st["sensors"],
                "estimator": estimator,
                "vibration": st["vibration"],
                "home": st["home"],
                "params": {"loaded": len(st["params"]), "total": st["param_count"]},
                "message_rates": {
                    name: decode.message_rate(entry["times"], now)
                    for name, entry in sorted(st["messages"].items())
                },
            }

        return self.call(run)

    def message_get(self, vid: str, name: str) -> dict[str, Any]:
        key = str(name or "").strip().upper()

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            entry = v.state["messages"].get(key)
            if entry is None:
                raise err("message_not_seen", message=key, vehicle=vid)
            now = self.clock()
            return {
                "name": key,
                "fields": entry["fields"],
                "t": round(self.wall() - (now - entry["t"]), 3),
                "hz": decode.message_rate(entry["times"], now),
            }

        return self.call(run)

    def _param_view(self, name: str, entry: dict[str, Any]) -> dict[str, Any]:
        from .mav.params import type_name

        return {"name": name, "value": entry["value"], "type": type_name(entry["type"])}

    def _lookup_param(self, v: Any, name: str) -> dict[str, Any] | None:
        params = v.state["params"]
        entry = params.get(name) or params.get(name.upper())
        return entry

    def param_get(self, vid: str, name: str) -> dict[str, Any]:
        """One parameter; if the table lacks it, read it by name and wait up to 2 s."""
        name = str(name or "").strip()
        if not name or len(name) > 16:
            raise err("param_not_found", name=name or "-")

        def start() -> dict[str, Any] | None:
            v = self._vehicle(vid)
            entry = self._lookup_param(v, name)
            if entry is not None:
                return self._param_view(name.upper() if name.upper() in v.state["params"] else name, entry)
            self._require_link(v)
            v.driver.request_param_named(name)
            return None

        found = self.call(start)
        if found is not None:
            return found
        t0 = self.clock()
        resent = False
        while self.threaded and self.clock() < t0 + 2.0:
            time.sleep(0.05)
            view = self.call(lambda: self._peek_param(vid, name))
            if view is not None:
                return view
            if not resent and self.clock() > t0 + 1.0:
                resent = True
                self.call(lambda: self._vehicle(vid).driver.request_param_named(name))
        raise err("param_not_found", name=name)

    def _peek_param(self, vid: str, name: str) -> dict[str, Any] | None:
        v = self._vehicle(vid)
        entry = self._lookup_param(v, name)
        return None if entry is None else self._param_view(name, entry)

    def params_read(self, vid: str, actor: Actor, names: list[str]) -> dict[str, Any]:
        """Read up to 32 parameters by name, waiting up to 3 s for the ones the table lacks."""
        wanted = list(dict.fromkeys(str(n or "").strip() for n in names))
        if not wanted or len(wanted) > 32 or "" in wanted:
            raise err("invalid_request", reason="give 1 to 32 parameter names")

        def start() -> None:
            v = self._vehicle(vid)
            self._gate(v, actor, either=True)
            self._require_link(v)
            for name in wanted:
                if self._lookup_param(v, name) is None:
                    v.driver.request_param_named(name)

        self.call(start)

        def collect() -> dict[str, float]:
            v = self._vehicle(vid)
            out = {}
            for name in wanted:
                entry = self._lookup_param(v, name)
                if entry is not None:
                    out[name] = entry["value"]
            return out

        values = self.call(collect)
        t0 = self.clock()
        while self.threaded and len(values) < len(wanted) and self.clock() < t0 + 3.0:
            time.sleep(0.05)
            values = self.call(collect)
        return {"values": values, "missing": [n for n in wanted if n not in values]}

    # ------------------------------------------------------------------ raw commands

    def raw_command(
        self,
        vid: str,
        actor: Actor,
        command: Any,
        params: list[Any],
        frame: int | None = None,
        use_int: bool = False,
        confirm: str | None = None,
        timeout_s: float = ACK_WAIT_S,
    ) -> dict[str, Any]:
        entry = device.find_command(command)
        if len(params) > 7:
            raise err("invalid_request", reason="a command has at most 7 parameters")
        values = [
            math.nan if p is None else _number(p, f"params[{i}]", -1e12, 1e12) for i, p in enumerate(params)
        ]
        values += [0.0] * (7 - len(values))
        timeout = _number(timeout_s, "timeout_s", 0.1, 10.0)
        cmd = entry["id"]

        flight_level.check_command(self.settings, actor, entry["name"])  # WP-1 (06 §5): sticks_only

        def send(v: Any, now: float) -> dict[str, Any]:
            self._gate(v, actor)
            self._require_link(v)
            level = device.danger_level(entry, values, self._motor_count(v))
            device.require_confirm(level, v.kind, confirm)
            if entry["needs_disarmed"] or level == "motor":
                device.require_disarmed("motor" if level == "motor" else entry["danger"] or "motor", v.armed)
            if level == "arm" and values[0] > 0.5:
                self._arm_checks(v, confirm)
            if cmd in (M.MAV_CMD_DO_MOTOR_TEST, M.MAV_CMD_ACTUATOR_TEST):
                self._raw_motor_limits(v, cmd, values)
            if cmd in MOTION_COMMANDS:
                self.programs.on_superseded(v)  # WP-1
                self._stop_skill(v, "superseded by a raw command", "被原始命令取代")
            if use_int:
                v.driver.command_int(cmd, 0 if frame is None else int(frame), tuple(values))
            else:
                v.driver.command_long(cmd, tuple(values))
            self._emit_device(
                "device_command",
                v,
                actor,
                {"command": entry["name"], "id": cmd, "params": values, "danger": level},
                {"command": entry["name"].removeprefix("MAV_CMD_"), "result": "sent"},
            )
            return {"id": vid, "command": entry["name"]}

        out = self._command_flow(vid, cmd, send, timeout)
        out["command"] = entry["name"]
        return out

    def _raw_motor_limits(self, v: Any, cmd: int, values: list[float]) -> None:
        """Raw motor and actuator tests keep the /motors/test limits: 0.2 to 5 s, 30 % on a real aircraft."""

        def bad(reason: str) -> None:
            raise err("invalid_request", reason=f"{reason}; use /motors/test or /actuators/set")

        if cmd == M.MAV_CMD_DO_MOTOR_TEST:
            pct, secs = values[2], values[3]
            if pct == 0 and secs == 0:
                return  # stops the motor
            if v.kind == "real" and values[1] != 0:
                bad("a real aircraft takes the motor-test throttle as a percentage (param 2 = 0)")
            if v.kind == "real" and not 0 <= pct <= REAL_MOTOR_PCT:
                bad("motor-test throttle must be 0 to 30 % on a real aircraft")
            secs_limit = secs
        else:
            level, secs_limit, function = values[0], values[1], values[4]
            if math.isnan(level):
                return  # stops the output
            if v.kind == "real" and 101 <= function <= 108 and not 0 <= level <= REAL_MOTOR_PCT / 100:
                bad("motor output must be 0 to 0.30 on a real aircraft")
        if not MOTOR_TEST_S[0] <= secs_limit <= MOTOR_TEST_S[1]:
            bad("a motor test lasts 0.2 to 5 s")
        v.motor_until = self.clock() + secs_limit

    # ------------------------------------------------------------------ motors and actuators

    def motors_test(
        self,
        vid: str,
        actor: Actor,
        motor: int | str,
        throttle_pct: float,
        seconds: float,
        confirm: str | None,
        strict: bool = True,
    ) -> dict[str, Any]:
        """Spin one motor, or all in turn. ``strict=False`` is the legacy operator route (no possession)."""
        every = motor == "all"
        if not every and (isinstance(motor, bool) or not isinstance(motor, int) or not 1 <= motor <= 8):
            raise err("invalid_request", reason='motor must be 1 to 8 or "all"')
        pct = _number(throttle_pct, "throttle_pct", 0.0, 100.0)
        secs = _number(seconds, "seconds", *MOTOR_TEST_S)

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            if strict:
                self._gate(v, actor)
            self._require_link(v)
            if v.kind == "real" and pct > REAL_MOTOR_PCT:
                raise err("invalid_request", reason="throttle_pct must be 0 to 30 on a real aircraft")
            count = self._motor_count(v)
            if not every and motor > count:  # type: ignore[operator]
                raise err("invalid_request", reason=f"this aircraft has {count} motors")
            device.require_confirm("motor", v.kind, confirm)
            device.require_disarmed("motor", v.armed)
            if every:
                queue = list(range(1, count + 1))
                first = queue.pop(0)
                self._motor_command(v, first, pct, secs)
                v.motor_seq = {
                    "queue": queue,
                    "pct": pct,
                    "seconds": secs,
                    "next_at": self.clock() + secs + MOTOR_GAP_S,
                }
                total = count * secs + (count - 1) * MOTOR_GAP_S
            else:
                v.motor_seq = None
                self._motor_command(v, motor, pct, secs)  # type: ignore[arg-type]
                total = secs
            v.motor_until = self.clock() + total
            self._emit_device(
                "motor_test",
                v,
                actor,
                {"motor": motor, "throttle_pct": pct, "seconds": secs},
                {
                    "motor": f"motor {motor}" if not every else "all motors",
                    "throttle_pct": pct,
                    "seconds": secs,
                },
            )
            return {"id": vid, "motor": motor, "running_s": round(total, 2)}

        return self.call(run)

    def motors_stop(self, vid: str, actor: Actor) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            self._gate(v, actor)
            self._require_link(v)
            self._stop_motors(v)
            self._emit_device("motor_test_stopped", v, actor, {})
            return {"id": vid, "stopped": True}

        return self.call(run)

    def actuator_set(
        self,
        vid: str,
        actor: Actor,
        index: int,
        value: float | None,
        pwm: float | None,
        seconds: float,
        confirm: str | None,
    ) -> dict[str, Any]:
        """Drive one output: PX4 ACTUATOR_TEST, ArduPilot DO_SET_SERVO (04 §2)."""
        if isinstance(index, bool) or not isinstance(index, int) or not 1 <= index <= 16:
            raise err("invalid_request", reason="index must be 1 to 16")
        if (value is None) == (pwm is None):
            raise err("invalid_request", reason="give exactly one of value and pwm")
        val = None if value is None else _number(value, "value", -1.0, 1.0)
        us = None if pwm is None else _number(pwm, "pwm", 800.0, 2200.0)
        secs = _number(seconds, "seconds", 0.0, 10.0)

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            self._gate(v, actor)
            self._require_link(v)
            count = self._motor_count(v)
            motor = device.is_motor_output(index, count)
            level = "motor" if motor else None
            device.require_confirm(level, v.kind, confirm)
            device.require_disarmed(level, v.armed)
            if motor:
                # a motor output is a motor test: bounded in time, and in throttle on a real aircraft
                if not MOTOR_TEST_S[0] <= secs <= MOTOR_TEST_S[1]:
                    raise err("invalid_request", reason="a motor output needs seconds 0.2 to 5")
                share = val if val is not None else (us - 1000.0) / 1000.0  # type: ignore[operator]
                if v.kind == "real" and share > REAL_MOTOR_PCT / 100:
                    raise err(
                        "invalid_request",
                        reason="a motor output must stay at or below 30 % on a real aircraft",
                    )
                v.motor_until = self.clock() + secs
            if v.autopilot == modes.PX4:
                span = 1000.0 if motor else 500.0
                base = 1000.0 if motor else 1500.0
                norm = val if val is not None else (us - base) / span  # type: ignore[operator]
                lo = 0.0 if motor else -1.0
                norm = max(lo, min(1.0, norm))
                function = 100 + index if motor else 200 + index - count
                v.driver.command_long(
                    M.MAV_CMD_ACTUATOR_TEST, (norm, secs, 0.0, 0.0, float(function), 0.0, 0.0)
                )
                out_pwm = round(base + span * norm)
            else:
                if us is not None:
                    out_pwm = int(us)
                elif motor:
                    out_pwm = int(1000 + 1000 * max(0.0, val))  # type: ignore[arg-type]
                else:
                    out_pwm = int(1500 + 500 * val)  # type: ignore[operator]
                servo = v.state["servo16"] or []
                previous = servo[index - 1] if len(servo) >= index and servo[index - 1] else 0
                v.driver.command_long(M.MAV_CMD_DO_SET_SERVO, (float(index), float(out_pwm), 0, 0, 0, 0, 0))
                if secs > 0:
                    v.deferred.append(
                        (
                            self.clock() + secs,
                            lambda veh: veh.driver.command_long(
                                M.MAV_CMD_DO_SET_SERVO, (float(index), float(previous), 0, 0, 0, 0, 0)
                            ),
                        )
                    )
            setting = f"{out_pwm} us" + (f" for {secs:g} s" if secs else "")
            self._emit_device(
                "actuator_set",
                v,
                actor,
                {"index": index, "value": val, "pwm": out_pwm, "seconds": secs, "motor": motor},
                {"index": index, "setting": setting},
            )
            return {"id": vid, "index": index, "pwm": out_pwm, "value": val, "seconds": secs, "motor": motor}

        return self.call(run)

    # ------------------------------------------------------------------ RC channels

    def rc_override(
        self, vid: str, actor: Actor, channels: dict[Any, Any] | None, axes: dict[str, Any] | None
    ) -> dict[str, Any]:
        """One channel-override frame, resent at 50 Hz until it goes stale (04 §2). Replaces any sticks."""
        chans: dict[int, int] = {}
        for key, us in (channels or {}).items():
            try:
                number = int(key)
            except (TypeError, ValueError) as exc:
                raise err("invalid_request", reason=f"channel {key!r} is not a number") from exc
            if not 1 <= number <= 18:
                raise err("invalid_request", reason="channels are numbered 1 to 18")
            chans[number] = round(_number(us, f"channel {number}", 1000.0, 2000.0))
        ax: dict[str, float] = {}
        for key, value in (axes or {}).items():
            if key not in RC_AXES:
                raise err("invalid_request", reason=f"unknown axis {key}; use {', '.join(RC_AXES)}")
            ax[key] = _number(value, key, 0.0 if key == "throttle" else -1.0, 1.0)
        if not chans and not ax:
            raise err("invalid_request", reason="give channels or axes")
        flight_level.check_rc(self.settings, actor, chans, ax)  # WP-1 (06 §5): stick channels only

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            self._gate(v, actor)
            self._require_link(v)
            if v.autopilot == modes.PX4 and any(c > 10 for c in chans):
                raise err("invalid_request", reason="PX4 manual control carries channels 1 to 10")
            now = self.clock()
            first = v.rc_frame is None
            self.programs.on_superseded(v)  # WP-1: an RC override replaces the program
            self._stop_skill(v, "superseded by an RC override", "被遥控通道覆盖取代")
            v.sticks = None
            v.rc_frame = {"channels": chans, "axes": ax, "t": now}
            if first:
                self._emit_device("rc_override", v, actor, {"channels": sorted(chans), "axes": sorted(ax)})
            if now < v.stop_window_until:
                return {"accepted": False, "reason": "stop_window"}
            return {"accepted": True}

        return self.call(run)

    # ------------------------------------------------------------------ message rates

    def message_interval(self, vid: str, actor: Actor, message: str, hz: float) -> dict[str, Any]:
        msg_id = device.message_id(message)
        rate = _number(hz, "hz", 0.0, 50.0)
        name = str(message).strip().upper()

        def send(v: Any, now: float) -> dict[str, Any]:
            self._gate(v, actor, either=True)
            self._require_link(v)
            v.driver.set_message_interval(msg_id, rate)
            self._emit_device(
                "message_interval",
                v,
                actor,
                {"message": name, "hz": rate},
                {"message": name, "rate": "off" if rate == 0 else f"{rate:g} Hz"},
            )
            return {"id": vid, "message": name, "hz": rate}

        return self._command_flow(vid, M.MAV_CMD_SET_MESSAGE_INTERVAL, send)

    def request_message(self, vid: str, actor: Actor, message: str) -> dict[str, Any]:
        msg_id = device.message_id(message)
        name = str(message).strip().upper()

        def send(v: Any, now: float) -> dict[str, Any]:
            self._gate(v, actor, either=True)
            self._require_link(v)
            v.driver.request_message(msg_id)
            return {"id": vid, "message": name}

        return self._command_flow(vid, M.MAV_CMD_REQUEST_MESSAGE, send)

    # ------------------------------------------------------------------ reboot, calibration, home, terminate

    def reboot(self, vid: str, actor: Actor, confirm: str | None) -> dict[str, Any]:
        def send(v: Any, now: float) -> dict[str, Any]:
            self._gate(v, actor)
            self._require_link(v)
            device.require_confirm("reboot", v.kind, confirm)
            device.require_disarmed("reboot", v.armed)
            self._stop_skill(v, "flight controller reboot", "飞控重启")
            v.sticks = None
            v.rc_frame = None
            v.driver.command_long(M.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN, (1.0,))
            # The message streams are lost with the reboot: ask again once the controller is back.
            for name in KEY_MESSAGES:
                v.state["messages"].pop(name, None)
            v.stream_check_at = now + 2.0
            for delay in RESTREAM_AFTER_S:
                v.deferred.append((now + delay, self._restream))
            self._emit_device("rebooting", v, actor, {})
            return {"id": vid, "rebooting": True}

        return self._command_flow(vid, M.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN, send)

    def calibrate(self, vid: str, actor: Actor, sensor: str, confirm: str | None) -> dict[str, Any]:
        params = device.CALIBRATIONS.get(sensor)
        if params is None:
            raise err("invalid_request", reason=f"sensor must be one of {', '.join(device.CALIBRATIONS)}")

        def send(v: Any, now: float) -> dict[str, Any]:
            self._gate(v, actor)
            self._require_link(v)
            device.require_confirm("calibrate", v.kind, confirm)
            device.require_disarmed("calibrate", v.armed)
            v.driver.command_long(M.MAV_CMD_PREFLIGHT_CALIBRATION, params)
            self._emit_device("calibration_started", v, actor, {"sensor": sensor}, {"sensor": sensor})
            return {"id": vid, "sensor": sensor}

        return self._command_flow(vid, M.MAV_CMD_PREFLIGHT_CALIBRATION, send)

    def set_home(
        self,
        vid: str,
        actor: Actor,
        here: bool,
        lat: float | None = None,
        lon: float | None = None,
        alt: float | None = None,
    ) -> dict[str, Any]:
        if here:
            params: tuple[float, ...] = (1.0, 0, 0, 0, 0, 0, 0)
        else:
            if lat is None or lon is None or alt is None:
                raise err("invalid_request", reason="give here: true, or lat, lon and alt")
            params = (
                0.0,
                0,
                0,
                0,
                _number(lat, "lat", -90.0, 90.0),
                _number(lon, "lon", -180.0, 180.0),
                _number(alt, "alt", -1000.0, 10000.0),
            )

        def send(v: Any, now: float) -> dict[str, Any]:
            self._gate(v, actor)
            self._require_link(v)
            v.driver.command_long(M.MAV_CMD_DO_SET_HOME, params)
            self._emit_device("home_set", v, actor, {"here": here, "lat": lat, "lon": lon, "alt": alt})
            return {"id": vid, "here": here}

        return self._command_flow(vid, M.MAV_CMD_DO_SET_HOME, send)

    def terminate(self, vid: str, actor: Actor, confirm: str | None) -> dict[str, Any]:
        """Flight termination: motors stop at once, then a forced disarm (04 §2). Simulated aircraft too."""

        def send(v: Any, now: float) -> dict[str, Any]:
            self._gate(v, actor)
            self._require_link(v)
            device.require_confirm("terminate", v.kind, confirm)
            self._cancel_plan(v, "flight terminated", "飞行已终止")
            self._stop_skill(v, "flight terminated", "飞行已终止")
            self._abort_course(v, "stopped", now)
            v.sticks = None
            v.rc_frame = None
            v.motor_seq = None
            v.driver.stop_sticks()
            v.driver.command_long(M.MAV_CMD_DO_FLIGHTTERMINATION, (1.0,))
            v.driver.arm(False, force=True)
            self._emit_device("terminated", v, actor, {})
            return {"id": vid, "terminated": True}

        return self._command_flow(vid, M.MAV_CMD_DO_FLIGHTTERMINATION, send)
