"""Simulated autopilot speaking real MAVLink 2 (03 §7.2, §7.3).

Two flavours share one flight model:

* ``ardupilot``: ArduPilot Copter 4.5 subset (autopilot=3, QUADROTOR), RC_CHANNELS_OVERRIDE from
  SYSID_MYGCS only, REQUEST_DATA_STREAM groups, modes STABILIZE / ALT_HOLD / LOITER / GUIDED / LAND / RTL.
* ``px4``: PX4 subset (autopilot=12), MANUAL_CONTROL gated by COM_RC_IN_MODE, SET_MESSAGE_INTERVAL,
  PX4 mode names, OFFBOARD position setpoints, bytewise integer parameters.

Input is wire bytes (``receive``); output is wire bytes (``step``). The fleet's SimDriver and the fake
vehicle both drive this class, so simulated aircraft follow the same code path as real ones.

Sim behaviour notes: when pilot input stops for longer than the stick timeout in a mode that needs a
pilot (STABILIZE, ALT_HOLD and their PX4 equivalents), the RC failsafe lands the aircraft (any non-zero
FS_THR_ENABLE lands; PX4 follows NAV_RCL_ACT). LOITER keeps its position without pilot input.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from ..mav import modes as modetab
from ..mav.decode import px4_param_decode, px4_param_encode
from ..mav.mavlink import Encoder, M, Parser
from .physics import DT, MASS, Command, G, Quad, throttle_for_fraction, wrap_pi

ARDUPILOT = "ardupilot"
PX4 = "px4"
HOME = (47.397742, 8.545594, 488.0)  # lat, lon, AMSL m (PX4 SITL default site)
STICK_TIMEOUT_S = 0.8
LAND_SPEED = 0.6
TAKEOFF_SPEED = 1.0
SPIN_ARM = 0.08
DEADBAND = 0.1
A_BRAKE = 2.5
KP_POS = 1.5
KP_VEL = 3.0
KP_Z = 1.5
KP_VZ = 4.0
KI_VZ = 2.0
KP_YAW = 2.5
MAX_STEPS = 500

# Behaviour per mode name.
_AP_BEHAVIOUR = {
    "STABILIZE": "stab",
    "ACRO": "stab",
    "DRIFT": "stab",
    "ALT_HOLD": "althold",
    "SPORT": "althold",
    "LOITER": "loiter",
    "POSHOLD": "loiter",
    "FLOWHOLD": "loiter",
    "BRAKE": "hold",
    "GUIDED": "guided",
    "GUIDED_NOGPS": "attitude",
    "AUTO": "hold",
    "CIRCLE": "hold",
    "LAND": "land",
    "RTL": "rtl",
    "SMART_RTL": "rtl",
}
_PX4_BEHAVIOUR = {
    "MANUAL": "stab",
    "STABILIZED": "stab",
    "ACRO": "stab",
    "ALTCTL": "althold",
    "POSCTL": "loiter",
    "LOITER": "hold",
    "MISSION": "hold",
    "OFFBOARD": "guided",
    "TAKEOFF": "takeoff",
    "LAND": "land",
    "RTL": "rtl",
}
PILOT_NEEDED = {"stab", "althold"}
POSITION_BEHAVIOURS = {"loiter", "guided", "rtl", "hold", "takeoff"}
MANUAL_THROTTLE = {"stab"}

# ArduPilot REQUEST_DATA_STREAM groups.
STREAM_GROUPS = {
    M.MAV_DATA_STREAM_EXTENDED_STATUS: ("SYS_STATUS", "GPS_RAW_INT"),
    M.MAV_DATA_STREAM_POSITION: ("LOCAL_POSITION_NED", "GLOBAL_POSITION_INT"),
    M.MAV_DATA_STREAM_EXTRA1: ("ATTITUDE",),
    M.MAV_DATA_STREAM_EXTRA2: ("VFR_HUD",),
    M.MAV_DATA_STREAM_EXTRA3: ("BATTERY_STATUS", "EKF_STATUS_REPORT", "EXTENDED_SYS_STATE", "VIBRATION"),
    M.MAV_DATA_STREAM_RC_CHANNELS: ("RC_CHANNELS", "SERVO_OUTPUT_RAW"),
    M.MAV_DATA_STREAM_RAW_SENSORS: (),
    M.MAV_DATA_STREAM_RAW_CONTROLLER: (),
}
EMITTABLE = {
    "SYS_STATUS",
    "BATTERY_STATUS",
    "ATTITUDE",
    "LOCAL_POSITION_NED",
    "GLOBAL_POSITION_INT",
    "VFR_HUD",
    "EXTENDED_SYS_STATE",
    "RC_CHANNELS",
    "SERVO_OUTPUT_RAW",
    "EKF_STATUS_REPORT",
    "VIBRATION",
    "AUTOPILOT_VERSION",
    "HOME_POSITION",
}
REBOOT_SILENCE_S = 2.5
SETPOINT_STALE_S = 1.0
MOTOR_OUTPUTS = 4
FORCE_MAGIC = 21196

I8, I16, I32, F = (
    M.MAV_PARAM_TYPE_INT8,
    M.MAV_PARAM_TYPE_INT16,
    M.MAV_PARAM_TYPE_INT32,
    M.MAV_PARAM_TYPE_REAL32,
)

ARDUPILOT_PARAMS: list[tuple[str, float, int]] = [
    ("SYSID_THISMAV", 1, I16),
    ("SYSID_MYGCS", 255, I16),
    ("RC_OVERRIDE_TIME", 3.0, F),
    ("FS_THR_ENABLE", 1, I8),
    ("FS_THR_VALUE", 975, I16),
    ("FS_GCS_ENABLE", 0, I8),
    ("FS_OPTIONS", 0, I32),
    ("BATT_MONITOR", 4, I8),
    ("BATT_CAPACITY", 850, I32),
    ("BATT_LOW_VOLT", 10.5, F),
    ("BATT_CRT_VOLT", 9.9, F),
    ("BATT_FS_LOW_ACT", 0, I8),
    ("BATT_FS_CRT_ACT", 0, I8),
    ("PILOT_SPEED_UP", 150, I16),
    ("PILOT_SPEED_DN", 0, I16),
    ("PILOT_ACCEL_Z", 250, I16),
    ("WPNAV_SPEED", 200.0, F),
    ("WPNAV_SPEED_UP", 100.0, F),
    ("WPNAV_SPEED_DN", 80.0, F),
    ("WPNAV_ACCEL", 250.0, F),
    ("LOIT_SPEED", 300.0, F),
    ("ANGLE_MAX", 3000, I16),
    ("RTL_ALT", 150, I32),
    ("LAND_SPEED", 60, I16),
    ("DISARM_DELAY", 10, I8),
    ("ARMING_CHECK", 1, I32),
    ("FRAME_CLASS", 1, I8),
    ("FRAME_TYPE", 1, I8),
    ("MOT_SPIN_ARM", 0.08, F),
    ("MOT_SPIN_MIN", 0.12, F),
    ("MOT_THST_HOVER", 0.5, F),
    ("ATC_ANG_RLL_P", 4.5, F),
    ("ATC_ANG_PIT_P", 4.5, F),
    ("ATC_ANG_YAW_P", 4.5, F),
    ("ATC_RAT_RLL_P", 0.135, F),
    ("ATC_RAT_RLL_I", 0.135, F),
    ("ATC_RAT_RLL_D", 0.0036, F),
    ("ATC_RAT_PIT_P", 0.135, F),
    ("ATC_RAT_PIT_I", 0.135, F),
    ("ATC_RAT_PIT_D", 0.0036, F),
    ("ATC_RAT_YAW_P", 0.18, F),
    ("ATC_RAT_YAW_I", 0.018, F),
    ("EK3_SRC1_POSXY", 6, I8),
    ("FLTMODE1", 0, I8),
    ("FLTMODE2", 2, I8),
    ("FLTMODE3", 5, I8),
    ("SERIAL1_BAUD", 921, I32),
]

PX4_PARAMS: list[tuple[str, float, int]] = [
    ("MAV_SYS_ID", 1, I32),
    ("COM_RC_IN_MODE", 3, I32),
    ("COM_RC_LOSS_T", 0.5, F),
    ("NAV_RCL_ACT", 2, I32),
    ("NAV_DLL_ACT", 0, I32),
    ("COM_OF_LOSS_T", 1.0, F),
    ("COM_OBL_RC_ACT", 0, I32),
    ("COM_DISARM_LAND", 2.0, F),
    ("COM_ARM_WO_GPS", 1, I32),
    ("MIS_TAKEOFF_ALT", 1.0, F),
    ("MPC_XY_VEL_MAX", 2.0, F),
    ("MPC_VEL_MANUAL", 3.0, F),
    ("MPC_Z_VEL_MAX_UP", 1.5, F),
    ("MPC_Z_VEL_MAX_DN", 1.0, F),
    ("MPC_LAND_SPEED", 0.6, F),
    ("MPC_TILTMAX_AIR", 25.0, F),
    ("MPC_MAN_TILT_MAX", 30.0, F),
    ("MPC_THR_HOVER", 0.5, F),
    ("RTL_RETURN_ALT", 1.5, F),
    ("BAT1_N_CELLS", 3, I32),
    ("BAT1_V_CHARGED", 4.2, F),
    ("BAT1_V_EMPTY", 3.5, F),
    ("BAT_LOW_THR", 0.15, F),
    ("BAT_CRIT_THR", 0.07, F),
    ("MC_ROLL_P", 6.5, F),
    ("MC_PITCH_P", 6.5, F),
    ("MC_YAW_P", 2.8, F),
    ("MC_ROLLRATE_P", 0.15, F),
    ("MC_PITCHRATE_P", 0.15, F),
    ("MC_YAWRATE_P", 0.2, F),
    ("EKF2_OF_CTRL", 1, I32),
    ("SYS_AUTOSTART", 4001, I32),
]


def _text_bytes(text: str) -> bytes:
    return text.encode("utf-8")[:50]


class SimAutopilot:
    def __init__(
        self,
        flavour: str = ARDUPILOT,
        *,
        sysid: int = 1,
        compid: int = 1,
        position: bool = True,
        position_hz: float | None = None,
        params: dict[str, float] | None = None,
        on_param_change: Callable[[str, float], None] | None = None,
        name: str = "SIM",
    ) -> None:
        if flavour not in (ARDUPILOT, PX4):
            raise ValueError(flavour)
        self.flavour = flavour
        self.name = name
        self.sysid = sysid
        self.compid = compid
        self.has_position = position
        self.position_hz = position_hz
        self.encoder = Encoder(sysid, compid)
        self.parser = Parser()
        self.on_param_change = on_param_change
        table = ARDUPILOT_PARAMS if flavour == ARDUPILOT else PX4_PARAMS
        self.params: dict[str, list[float]] = {name: [float(v), t] for name, v, t in table}
        self.param_order = [name for name, _, _ in table]
        self.param_list_skip: set[str] = set()  # tests: names lost from the table reply, as over a lossy link
        for key, value in (params or {}).items():
            if key in self.params:
                self.params[key][0] = float(value)
        self.quad = Quad()
        self.t: float | None = None
        self.boot: float | None = None
        self.out: list[bytes] = []
        self.intervals: dict[str, float] = {}
        self.due: dict[str, float] = {}
        self.next_heartbeat = 0.0
        self.received: list[tuple[float, Any]] = []
        self.keep_received = 0
        self._reset_state()

    # ------------------------------------------------------------------ state

    def _reset_state(self) -> None:
        self.home = list(HOME)
        self.armed = False
        self.mode = "STABILIZE" if self.flavour == ARDUPILOT else "STABILIZED"
        self.pilot: tuple[float, float, float, float] | None = None  # roll, pitch, yaw, throttle
        self.pilot_t: float | None = None
        self.mode_t = self.t or 0.0
        self.rc_raw: list[int] = [0] * 18
        self.z_target = 0.0
        self.i_vz = 0.0
        self.hold: list[float] | None = None
        self.target: list[float] | None = None  # ENU x, y, z
        self.target_yaw: float | None = None
        self.offboard_t: float | None = None
        self.offboard_target: tuple[list[float], float | None] | None = None
        self.rtl_phase = "climb"
        self.ground_t: float | None = None
        self.airborne_t: float | None = None
        self.takeoff_alt: float | None = None
        self.motor_tests: dict[int, tuple[float, float]] = {}  # output -> (throttle 0..1, until)
        # WP-B (bench, 05 §4.2 S7) observability hook: when a test in a DO_MOTOR_TEST sequence starts
        self.motor_test_from: dict[int, float] = {}
        self.servo_set: dict[
            int, tuple[int, float | None]
        ] = {}  # output -> (PWM, until) from DO_SET_SERVO / ACTUATOR_TEST
        self.relays: dict[int, bool] = {}
        self.att_target: tuple[float, float, float, float, float] | None = (
            None  # roll, pitch, yaw, rate, thrust
        )
        self.att_t: float | None = None
        self.vel_target: list[float] | None = None  # ENU m/s
        self.vel_yaw_rate: float | None = None
        self.vel_t: float | None = None
        self.pos_sp_t: float | None = None
        self.reboot_until: float | None = None
        self.low_batt_warned = False
        self.crash_reported = False
        self.failsafe = False

    def reset(self) -> None:
        """Back to the spawn point, disarmed, full battery, crash cleared. Parameters are kept."""
        self.quad.reset()
        self._reset_state()
        self._text(M.MAV_SEVERITY_INFO, "Simulation reset")

    def param(self, name: str, default: float = 0.0) -> float:
        entry = self.params.get(name)
        return entry[0] if entry else default

    @property
    def behaviour(self) -> str:
        table = _AP_BEHAVIOUR if self.flavour == ARDUPILOT else _PX4_BEHAVIOUR
        return table.get(self.mode, "hold")

    @property
    def p(self) -> list[float]:
        return list(self.quad.s.p)

    @property
    def crashed(self) -> bool:
        return self.quad.s.crashed

    def _boot_ms(self, now: float) -> int:
        return int(max(0.0, now - (self.boot or now)) * 1000) & 0xFFFFFFFF

    # ------------------------------------------------------------------ input

    def receive(self, data: bytes, now: float) -> None:
        for msg in self.parser.parse(data):
            self.handle(msg, now)

    def handle(self, msg: Any, now: float) -> None:
        if self.reboot_until is not None:
            return  # the flight controller is rebooting and hears nothing
        if self.keep_received:
            self.received.append((now, msg))
            del self.received[: -self.keep_received]
        mtype = msg.get_type()
        target = getattr(msg, "target_system", None)
        if target is None:
            target = getattr(msg, "target", None)
        if target not in (None, 0, self.sysid):
            return
        handler = getattr(self, f"_on_{mtype.lower()}", None)
        if handler is not None:
            handler(msg, now)

    def _on_heartbeat(self, msg, now):
        pass

    def _on_rc_channels_override(self, msg, now):
        if self.flavour != ARDUPILOT:
            return
        if msg.get_srcSystem() != int(self.param("SYSID_MYGCS", 255)):
            return
        raw = [getattr(msg, f"chan{i}_raw") for i in range(1, 19)]
        if all(v == 0 for v in raw[:4]):
            self.pilot = None
            self.rc_raw = [0] * 18
            return
        had_pilot = self.pilot is not None
        channels = list(self.rc_raw) if had_pilot else [1500, 1500, 1000, 1500] + [0] * 14
        for i, value in enumerate(raw):
            if value == 65535:
                continue
            if value == 0:
                channels[i] = 0
            elif 800 <= value <= 2200:
                channels[i] = value
        self.rc_raw = channels
        if not had_pilot and all(v == 65535 for v in raw[:4]):
            self.rc_raw[:4] = [0] * 4  # only aux channels were overridden: no pilot input
            return
        roll = (channels[0] - 1500) / 500.0
        pitch = -(channels[1] - 1500) / 500.0
        throttle = (channels[2] - 1000) / 1000.0
        yaw = (channels[3] - 1500) / 500.0
        self.pilot = (_c(roll), _c(pitch), _c(yaw), min(1.0, max(0.0, throttle)))
        self.pilot_t = now

    def _on_manual_control(self, msg, now):
        if self.flavour != PX4:
            return
        if int(self.param("COM_RC_IN_MODE", 3)) not in (1, 2, 3):
            return
        roll = msg.y / 1000.0
        pitch = msg.x / 1000.0
        yaw = msg.r / 1000.0
        throttle = msg.z / 1000.0
        self.pilot = (_c(roll), _c(pitch), _c(yaw), min(1.0, max(0.0, throttle)))
        self.pilot_t = now
        self.rc_raw = [
            int(1500 + 500 * roll),
            int(1500 - 500 * pitch),
            int(1000 + 1000 * throttle),
            int(1500 + 500 * yaw),
        ]
        self.rc_raw += [0] * 14
        aux = (msg.aux1, msg.aux2, msg.aux3, msg.aux4, msg.aux5, msg.aux6)
        for i, value in enumerate(aux):
            if msg.enabled_extensions & (1 << (2 + i)):
                self.rc_raw[4 + i] = int(1500 + value / 2)

    def _on_set_mode(self, msg, now):
        custom = int(msg.custom_mode)
        if self.flavour == ARDUPILOT:
            name = self._mode_from_numbers(msg.base_mode, custom)
        else:
            name = self._mode_from_numbers(msg.base_mode, (custom >> 16) & 0xFF, (custom >> 24) & 0xFF)
        if name:
            self._set_mode(name, now)

    def _on_set_position_target_local_ned(self, msg, now):
        if msg.coordinate_frame not in (M.MAV_FRAME_LOCAL_NED,):
            return
        mask = msg.type_mask
        if mask & 0b111 == 0b111 and not mask & 0b111000:  # velocity setpoint
            self._velocity_setpoint(msg, now)
            return
        if mask & 0b111:  # position ignored: other combinations are not modelled
            return
        target = [msg.y, msg.x, -msg.z]
        yaw = None if mask & 1024 else msg.yaw
        self.vel_target = None
        self.pos_sp_t = now
        if self.flavour == ARDUPILOT:
            if self.mode == "GUIDED" and self.armed:
                self.target = target
                self.target_yaw = yaw
        else:
            self.offboard_target = (target, yaw)
            self.offboard_t = now
            if self.mode == "OFFBOARD":
                self.target = target
                self.target_yaw = yaw

    def _velocity_setpoint(self, msg, now):
        if self.flavour == PX4:
            self.offboard_t = now
            self.pos_sp_t = now
            if self.mode != "OFFBOARD":
                return
        elif self.mode != "GUIDED" or not self.armed:
            return
        self.vel_target = [msg.vy, msg.vx, -msg.vz]
        self.vel_yaw_rate = None if msg.type_mask & 2048 else msg.yaw_rate
        self.vel_t = now

    def _on_set_attitude_target(self, msg, now):
        if msg.type_mask & 128:  # attitude ignored: body-rate-only targets are not modelled
            return
        w, x, y, z = msg.q
        roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
        pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
        yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
        rate = 0.0 if msg.type_mask & 4 else msg.body_yaw_rate
        self.att_target = (roll, pitch, yaw, rate, min(1.0, max(0.0, msg.thrust)))
        self.att_t = now
        if self.flavour == PX4:
            self.offboard_t = now

    def _on_request_data_stream(self, msg, now):
        if self.flavour != ARDUPILOT:
            return
        groups = list(STREAM_GROUPS) if msg.req_stream_id == M.MAV_DATA_STREAM_ALL else [msg.req_stream_id]
        for group in groups:
            for name in STREAM_GROUPS.get(group, ()):
                if msg.start_stop and msg.req_message_rate > 0:
                    self._set_interval(name, 1.0 / msg.req_message_rate, now)
                else:
                    self.intervals.pop(name, None)

    def _on_param_request_list(self, msg, now):
        for index, name in enumerate(self.param_order):
            if name not in self.param_list_skip:
                self._send_param(name, index)

    def _on_param_request_read(self, msg, now):
        if msg.param_index >= 0 and msg.param_index < len(self.param_order):
            name = self.param_order[msg.param_index]
        else:
            name = _text(msg.param_id)
        if name in self.params:
            self._send_param(name, self.param_order.index(name))

    def _on_param_set(self, msg, now):
        name = _text(msg.param_id)
        entry = self.params.get(name)
        if entry is None:
            return  # unknown parameters are ignored, as ArduPilot does
        value = float(msg.param_value)
        if self.flavour == PX4:
            value = px4_param_decode(value, entry[1])
        if entry[1] != F:
            value = float(round(value))
        entry[0] = value
        if self.on_param_change is not None:
            self.on_param_change(name, value)
        self._send_param(name, self.param_order.index(name))

    def _on_timesync(self, msg, now):
        if msg.tc1 == 0:
            self._send(M.MAVLink_timesync_message(tc1=int(now * 1e9), ts1=msg.ts1))

    def _on_command_long(self, msg, now):
        p = (msg.param1, msg.param2, msg.param3, msg.param4, msg.param5, msg.param6, msg.param7)
        self._ack(msg, self._command(msg.command, p, now))

    def _on_command_int(self, msg, now):
        geographic = msg.frame in (0, 3, 5, 6, 10, 11)
        x = msg.x / 1e7 if geographic else float(msg.x)
        y = msg.y / 1e7 if geographic else float(msg.y)
        p = (msg.param1, msg.param2, msg.param3, msg.param4, x, y, msg.z)
        self._ack(msg, self._command(msg.command, p, now))

    def _ack(self, msg, result: int) -> None:
        self._send(
            M.MAVLink_command_ack_message(
                command=msg.command,
                result=result,
                progress=0,
                result_param2=0,
                target_system=msg.get_srcSystem(),
                target_component=msg.get_srcComponent(),
            )
        )

    def _command(self, cmd: int, p: tuple[float, ...], now: float) -> int:
        """Run one MAV_CMD and return its MAV_RESULT. Unknown commands are UNSUPPORTED."""
        if cmd == M.MAV_CMD_COMPONENT_ARM_DISARM:
            return self._arm(p[0] > 0.5, now, force=p[1] == FORCE_MAGIC)
        if cmd == M.MAV_CMD_DO_SET_MODE:
            name = self._mode_from_numbers(int(p[0]), int(p[1]), int(p[2]))
            return self._set_mode(name, now) if name else M.MAV_RESULT_DENIED
        if cmd == M.MAV_CMD_NAV_TAKEOFF:
            return self._takeoff(p[6], now)
        if cmd == M.MAV_CMD_NAV_LAND:
            return self._set_mode("LAND", now)
        if cmd == M.MAV_CMD_NAV_RETURN_TO_LAUNCH:
            return self._set_mode("RTL", now)
        if cmd == M.MAV_CMD_NAV_LOITER_UNLIM:
            return self._set_mode("LOITER", now)
        if cmd == M.MAV_CMD_SET_MESSAGE_INTERVAL:
            return self._message_interval(int(p[0]), p[1], now)
        if cmd == M.MAV_CMD_REQUEST_MESSAGE:
            return self._request_message(int(p[0]), now)
        if cmd == M.MAV_CMD_REQUEST_AUTOPILOT_CAPABILITIES:
            return self._request_message(M.MAVLINK_MSG_ID_AUTOPILOT_VERSION, now)
        if cmd == M.MAV_CMD_DO_MOTOR_TEST:
            return self._motor_test(int(p[0]), p[2] / 100.0, p[3], now, int(p[4]))
        if cmd == M.MAV_CMD_ACTUATOR_TEST:
            return self._actuator_test(p[0], p[1], int(p[4]), now)
        if cmd in (M.MAV_CMD_DO_SET_SERVO, M.MAV_CMD_DO_REPEAT_SERVO):
            return self._set_servo(int(p[0]), p[1])
        if cmd == M.MAV_CMD_DO_SET_RELAY:
            self.relays[int(p[0])] = p[1] > 0.5
            return M.MAV_RESULT_ACCEPTED
        if cmd == M.MAV_CMD_DO_FLIGHTTERMINATION:
            return self._terminate(p[0] > 0.5)
        if cmd == M.MAV_CMD_PREFLIGHT_CALIBRATION:
            return self._calibrate(p)
        if cmd == M.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN:
            return self._reboot(int(p[0]), now)
        if cmd == M.MAV_CMD_DO_SET_HOME:
            return self._set_home(p)
        if cmd == M.MAV_CMD_CONDITION_YAW:
            return self._condition_yaw(p)
        if cmd == M.MAV_CMD_DO_REPOSITION:
            return self._reposition(p)
        if cmd == M.MAV_CMD_MISSION_START:
            return M.MAV_RESULT_FAILED  # no mission is stored
        if cmd in (
            M.MAV_CMD_DO_CHANGE_SPEED,
            M.MAV_CMD_DO_SET_ROI_LOCATION,
            M.MAV_CMD_DO_SET_ROI_NONE,
            M.MAV_CMD_DO_PAUSE_CONTINUE,
        ):
            return M.MAV_RESULT_ACCEPTED
        return M.MAV_RESULT_UNSUPPORTED  # payload commands: no gripper or camera is fitted

    # ------------------------------------------------------------------ commands

    def _mode_from_numbers(self, base: int, main: int, sub: int = 0) -> str | None:
        if self.flavour == ARDUPILOT:
            return modetab.ARDUPILOT_BY_NUMBER.get(int(main))
        for name, (_b, m, s) in modetab.mavutil.px4_map.items():
            if m == main and (s == sub or (m != 4 and s == 0)):
                return name if name in modetab.PX4_MODES else None
        return None

    def _set_mode(self, name: str | None, now: float) -> int:
        if not name:
            return M.MAV_RESULT_DENIED
        table = _AP_BEHAVIOUR if self.flavour == ARDUPILOT else _PX4_BEHAVIOUR
        if name not in table:
            return M.MAV_RESULT_DENIED
        behaviour = table[name]
        attitude_ok = name == "OFFBOARD" and self.att_t is not None and now - self.att_t <= 0.5
        if behaviour in POSITION_BEHAVIOURS and not self.has_position and not attitude_ok:
            self._text(M.MAV_SEVERITY_WARNING, f"Mode change to {name} failed: requires position")
            return M.MAV_RESULT_DENIED
        if name == "OFFBOARD" and (self.offboard_t is None or now - self.offboard_t > 0.5):
            self._text(M.MAV_SEVERITY_WARNING, "Offboard mode rejected: no setpoints")
            return M.MAV_RESULT_DENIED
        if name == self.mode:
            return M.MAV_RESULT_ACCEPTED
        self.mode = name
        self.mode_t = now
        self.failsafe = False
        self.next_heartbeat = 0.0
        s = self.quad.s
        self.z_target = s.p[2]
        self.i_vz = 0.0
        self.hold = None
        if behaviour == "guided":
            if name == "OFFBOARD" and self.offboard_target is not None:
                self.target, self.target_yaw = list(self.offboard_target[0]), self.offboard_target[1]
            else:
                self.target, self.target_yaw = list(s.p), None
        elif behaviour == "rtl":
            self.rtl_phase = "climb"
        elif behaviour == "land":
            self.hold = [s.p[0], s.p[1], s.p[2]]
        return M.MAV_RESULT_ACCEPTED

    def _arm(self, on: bool, now: float, force: bool = False) -> int:
        s = self.quad.s
        if not on:
            if not self.armed:
                return M.MAV_RESULT_ACCEPTED
            if force:
                s.motor = 0.0
                self._disarm("Disarming motors (forced)")
                return M.MAV_RESULT_ACCEPTED
            if not s.on_ground:
                self._text(M.MAV_SEVERITY_WARNING, "Disarm denied: vehicle is flying")
                return M.MAV_RESULT_DENIED
            self._disarm("Disarming motors")
            return M.MAV_RESULT_ACCEPTED
        if self.armed:
            return M.MAV_RESULT_ACCEPTED
        problem = None
        behaviour = self.behaviour
        if s.crashed:
            problem = "Crash detected, reset required"
        elif force:
            problem = None  # 21196: the pre-arm checks are skipped
        elif not s.on_ground:
            problem = "Vehicle is not on the ground"
        elif self.quad.battery.voltage < self._crit_voltage():
            problem = "Battery critical"
        elif behaviour in ("land", "rtl", "takeoff"):
            problem = f"Mode {self.mode} not armable"
        elif (self.pilot is not None and behaviour in MANUAL_THROTTLE and self.pilot[3] > 0.05) or (
            self.pilot is not None
            and behaviour in ("althold", "loiter")
            and self.pilot[3] > 0.5 + DEADBAND / 2
        ):
            problem = "Throttle too high"
        if problem:
            self._text(
                M.MAV_SEVERITY_CRITICAL,
                f"PreArm: {problem}" if self.flavour == ARDUPILOT else f"Arming denied: {problem}",
            )
            return M.MAV_RESULT_FAILED if self.flavour == ARDUPILOT else M.MAV_RESULT_DENIED
        self.armed = True
        self.ground_t = now
        self.airborne_t = None
        self.next_heartbeat = 0.0
        self.z_target = 0.0
        self.i_vz = 0.0
        self.low_batt_warned = False
        self._text(
            M.MAV_SEVERITY_INFO, "Arming motors" if self.flavour == ARDUPILOT else "Armed by external command"
        )
        return M.MAV_RESULT_ACCEPTED

    def _disarm(self, text: str) -> None:
        self.armed = False
        self.next_heartbeat = 0.0
        self.takeoff_alt = None
        self.target = None
        self._text(M.MAV_SEVERITY_INFO, text)

    def _takeoff(self, alt_param: float, now: float) -> int:
        s = self.quad.s
        if not self.has_position or not self.armed or s.crashed:
            return M.MAV_RESULT_DENIED
        if self.flavour == ARDUPILOT:
            if self.mode != "GUIDED" or not s.on_ground:
                return M.MAV_RESULT_TEMPORARILY_REJECTED
            alt = alt_param
        else:
            if math.isnan(alt_param):
                alt = self.param("MIS_TAKEOFF_ALT", 1.0)
            else:
                alt = alt_param - HOME[2]
            self._set_mode("TAKEOFF", now)
        alt = min(10.0, max(0.2, alt))
        self.takeoff_alt = alt
        self.target = [s.p[0], s.p[1], alt]
        self.target_yaw = None
        self.airborne_t = now
        return M.MAV_RESULT_ACCEPTED

    def _motor_test(self, motor: int, throttle: float, seconds: float, now: float, count: int = 0) -> int:
        if self.armed or not 1 <= motor <= MOTOR_OUTPUTS:
            return M.MAV_RESULT_DENIED
        if throttle <= 0.0 or seconds <= 0.0:
            self.motor_tests.pop(motor, None)
            self.motor_test_from.pop(motor, None)
            return M.MAV_RESULT_ACCEPTED
        # WP-B: param5 (motor count) runs the motors one after another, as ArduPilot does
        seconds = min(10.0, seconds)
        for k in range(max(1, count)):
            output = motor + k
            if output > MOTOR_OUTPUTS:
                break
            self.motor_test_from[output] = now + k * seconds
            self.motor_tests[output] = (min(1.0, throttle), now + (k + 1) * seconds)
        return M.MAV_RESULT_ACCEPTED

    def _actuator_test(self, value: float, timeout: float, function: int, now: float) -> int:
        """PX4 ACTUATOR_TEST: functions 101.. are motors, 201.. servos (after the motor outputs)."""
        if 101 <= function <= 116:
            if self.armed:
                return M.MAV_RESULT_DENIED
            output = function - 100
            if output > MOTOR_OUTPUTS:
                return M.MAV_RESULT_DENIED
            self.motor_test_from.pop(output, None)
            if math.isnan(value) or value <= 0.0:
                self.motor_tests.pop(output, None)
            else:
                until = now + (min(10.0, timeout) if timeout > 0 else 1e9)
                self.motor_tests[output] = (min(1.0, value), until)
            return M.MAV_RESULT_ACCEPTED
        if 201 <= function <= 216:
            output = MOTOR_OUTPUTS + function - 200
            if output > 16:
                return M.MAV_RESULT_DENIED
            if math.isnan(value):
                self.servo_set.pop(output, None)
            else:
                pwm = round(1500 + 500 * max(-1.0, min(1.0, value)))
                self.servo_set[output] = (pwm, now + min(10.0, timeout) if timeout > 0 else None)
            return M.MAV_RESULT_ACCEPTED
        return M.MAV_RESULT_DENIED

    def _set_servo(self, output: int, pwm: float) -> int:
        if not 1 <= output <= 16:
            return M.MAV_RESULT_DENIED
        if pwm == 0:
            self.servo_set.pop(output, None)
        elif 800 <= pwm <= 2200:
            self.servo_set[output] = (int(pwm), None)
        else:
            return M.MAV_RESULT_DENIED
        return M.MAV_RESULT_ACCEPTED

    def _terminate(self, on: bool) -> int:
        if on and self.armed:
            self.quad.s.motor = 0.0
            self._disarm("Flight termination: motors stopped")
        return M.MAV_RESULT_ACCEPTED

    def _calibrate(self, p: tuple[float, ...]) -> int:
        if self.armed:
            return M.MAV_RESULT_DENIED
        what = (
            "gyroscope"
            if p[0]
            else "barometer"
            if p[2]
            else "level"
            if p[4] == 2
            else "accelerometer"
            if p[4]
            else None
        )
        if what is None:
            return M.MAV_RESULT_UNSUPPORTED
        self._text(M.MAV_SEVERITY_INFO, f"Calibrating {what}")
        if self.flavour != ARDUPILOT:
            # WP-B (bench, 05 §4.2 S4) observability hook: PX4 reports the end of a calibration in text
            self._text(M.MAV_SEVERITY_INFO, f"[cal] calibration done: {what}")
        return M.MAV_RESULT_ACCEPTED

    def _reboot(self, autopilot: int, now: float) -> int:
        if autopilot == 0:
            return M.MAV_RESULT_ACCEPTED
        if self.armed:
            return M.MAV_RESULT_DENIED
        if autopilot != 1:
            return M.MAV_RESULT_UNSUPPORTED
        self.reboot_until = now + REBOOT_SILENCE_S
        return M.MAV_RESULT_ACCEPTED

    def _boot(self, now: float) -> None:
        """Back from a reboot: fresh boot clock, default mode, disarmed, no message streams."""
        self.reboot_until = None
        self.boot = now
        self.intervals.clear()
        self.due.clear()
        self._reset_state()
        self.mode_t = now
        self.next_heartbeat = 0.0

    def _geo(self) -> tuple[float, float]:
        s = self.quad.s
        lat = HOME[0] + math.degrees(s.p[1] / 6371000.0)
        lon = HOME[1] + math.degrees(s.p[0] / (6371000.0 * math.cos(math.radians(HOME[0]))))
        return lat, lon

    def _set_home(self, p: tuple[float, ...]) -> int:
        if p[0] > 0.5:
            lat, lon = self._geo()
            self.home = [lat, lon, HOME[2] + self.quad.s.p[2]]
        elif abs(p[4]) <= 90 and abs(p[5]) <= 180:
            self.home = [p[4], p[5], p[6]]
        else:
            return M.MAV_RESULT_DENIED
        msg = self._build("HOME_POSITION", self.t or 0.0)
        if msg is not None:
            self._send(msg)
        return M.MAV_RESULT_ACCEPTED

    def _condition_yaw(self, p: tuple[float, ...]) -> int:
        if self.armed and self.behaviour == "guided":
            base = self.quad.s.yaw if p[3] else 0.0
            self.target_yaw = wrap_pi(base + math.radians(p[0]))
        return M.MAV_RESULT_ACCEPTED

    def _reposition(self, p: tuple[float, ...]) -> int:
        if not (self.armed and self.behaviour == "guided" and self.has_position):
            return M.MAV_RESULT_DENIED
        x = math.radians(p[5] - HOME[1]) * 6371000.0 * math.cos(math.radians(HOME[0]))
        y = math.radians(p[4] - HOME[0]) * 6371000.0
        self.target = [x, y, max(0.0, p[6] - HOME[2])]
        self.vel_target = None
        return M.MAV_RESULT_ACCEPTED

    def _set_interval(self, name: str, interval: float, now: float) -> None:
        self.intervals[name] = max(0.005, interval)
        self.due[name] = now

    def _message_interval(self, msg_id: int, interval_us: float, now: float) -> int:
        cls = M.mavlink_map.get(msg_id)
        if cls is None:
            return M.MAV_RESULT_DENIED
        name = cls.msgname
        if name not in EMITTABLE:
            return M.MAV_RESULT_UNSUPPORTED
        if interval_us < 0:
            self.intervals.pop(name, None)
        elif interval_us == 0:
            self._set_interval(name, 0.25, now)
        else:
            self._set_interval(name, interval_us / 1e6, now)
        return M.MAV_RESULT_ACCEPTED

    def _request_message(self, msg_id: int, now: float) -> int:
        cls = M.mavlink_map.get(msg_id)
        if cls is None or cls.msgname not in EMITTABLE:
            return M.MAV_RESULT_UNSUPPORTED
        msg = self._build(cls.msgname, now)
        if msg is None:
            return M.MAV_RESULT_FAILED
        self._send(msg)
        return M.MAV_RESULT_ACCEPTED

    def _crit_voltage(self) -> float:
        if self.flavour == ARDUPILOT:
            return self.param("BATT_CRT_VOLT", 9.9)
        return 3 * 3.3

    # ------------------------------------------------------------------ output

    def _send(self, msg: Any) -> None:
        self.out.append(self.encoder.encode(msg))

    def _text(self, severity: int, text: str) -> None:
        self._send(M.MAVLink_statustext_message(severity=severity, text=_text_bytes(text), id=0, chunk_seq=0))

    def _send_param(self, name: str, index: int) -> None:
        value, ptype = self.params[name]
        wire = px4_param_encode(value, ptype) if self.flavour == PX4 else float(value)
        self._send(
            M.MAVLink_param_value_message(
                param_id=name.encode("ascii"),
                param_value=wire,
                param_type=ptype,
                param_count=len(self.param_order),
                param_index=index,
            )
        )

    def _heartbeat(self) -> Any:
        if self.flavour == ARDUPILOT:
            base = M.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED | M.MAV_MODE_FLAG_STABILIZE_ENABLED
            base |= M.MAV_MODE_FLAG_MANUAL_INPUT_ENABLED
            custom = modetab.ARDUPILOT_MODES.get(self.mode, 0)
            autopilot = M.MAV_AUTOPILOT_ARDUPILOTMEGA
        else:
            base, custom = modetab.px4_custom_mode(self.mode)
            autopilot = M.MAV_AUTOPILOT_PX4
        if self.armed:
            base |= M.MAV_MODE_FLAG_SAFETY_ARMED
        if self.quad.s.crashed:
            status = M.MAV_STATE_EMERGENCY
        else:
            status = M.MAV_STATE_ACTIVE if self.armed else M.MAV_STATE_STANDBY
        return M.MAVLink_heartbeat_message(
            type=M.MAV_TYPE_QUADROTOR,
            autopilot=autopilot,
            base_mode=base,
            custom_mode=custom,
            system_status=status,
            mavlink_version=3,
        )

    def landed_state(self) -> int:
        s = self.quad.s
        if s.on_ground:
            return M.MAV_LANDED_STATE_ON_GROUND
        if self.behaviour == "land" or (self.behaviour == "rtl" and self.rtl_phase == "land"):
            return M.MAV_LANDED_STATE_LANDING
        if self.airborne_t is not None and self.takeoff_alt is not None and s.p[2] < self.takeoff_alt - 0.15:
            return M.MAV_LANDED_STATE_TAKEOFF
        return M.MAV_LANDED_STATE_IN_AIR

    def _build(self, name: str, now: float) -> Any | None:
        s = self.quad.s
        b = self.quad.battery
        boot_ms = self._boot_ms(now)
        if name == "SYS_STATUS":
            sensors = (
                M.MAV_SYS_STATUS_SENSOR_3D_GYRO
                | M.MAV_SYS_STATUS_SENSOR_3D_ACCEL
                | M.MAV_SYS_STATUS_SENSOR_3D_MAG
                | M.MAV_SYS_STATUS_SENSOR_ABSOLUTE_PRESSURE
                | M.MAV_SYS_STATUS_AHRS
                | M.MAV_SYS_STATUS_SENSOR_MOTOR_OUTPUTS
                | M.MAV_SYS_STATUS_SENSOR_BATTERY
                | M.MAV_SYS_STATUS_PREARM_CHECK
            )
            if self.has_position:
                sensors |= M.MAV_SYS_STATUS_SENSOR_OPTICAL_FLOW
            health = sensors
            if s.crashed or b.voltage < self._crit_voltage():
                health &= ~M.MAV_SYS_STATUS_PREARM_CHECK
            return M.MAVLink_sys_status_message(
                onboard_control_sensors_present=sensors,
                onboard_control_sensors_enabled=sensors,
                onboard_control_sensors_health=health,
                load=200,
                voltage_battery=int(b.voltage * 1000),
                current_battery=int(b.current * 100),
                battery_remaining=b.remaining_pct,
                drop_rate_comm=0,
                errors_comm=0,
                errors_count1=0,
                errors_count2=0,
                errors_count3=0,
                errors_count4=0,
            )
        if name == "BATTERY_STATUS":
            cell_mv = int(b.voltage / 3 * 1000)
            return M.MAVLink_battery_status_message(
                id=0,
                battery_function=M.MAV_BATTERY_FUNCTION_ALL,
                type=M.MAV_BATTERY_TYPE_LIPO,
                temperature=32767,
                voltages=[cell_mv, cell_mv, cell_mv] + [65535] * 7,
                current_battery=int(b.current * 100),
                current_consumed=int(b.consumed_mah),
                energy_consumed=-1,
                battery_remaining=b.remaining_pct,
            )
        if name == "ATTITUDE":
            return M.MAVLink_attitude_message(
                time_boot_ms=boot_ms,
                roll=s.roll,
                pitch=s.pitch,
                yaw=s.yaw,
                rollspeed=s.rates[0],
                pitchspeed=s.rates[1],
                yawspeed=s.rates[2],
            )
        if name == "LOCAL_POSITION_NED":
            if not self.has_position:
                return None
            return M.MAVLink_local_position_ned_message(
                time_boot_ms=boot_ms, x=s.p[1], y=s.p[0], z=-s.p[2], vx=s.v[1], vy=s.v[0], vz=-s.v[2]
            )
        if name == "GLOBAL_POSITION_INT":
            if not self.has_position:
                return None
            lat, lon = self._geo()
            return M.MAVLink_global_position_int_message(
                time_boot_ms=boot_ms,
                lat=int(lat * 1e7),
                lon=int(lon * 1e7),
                alt=int((HOME[2] + s.p[2]) * 1000),
                relative_alt=int(s.p[2] * 1000),
                vx=int(s.v[1] * 100),
                vy=int(s.v[0] * 100),
                vz=int(-s.v[2] * 100),
                hdg=int(math.degrees(s.yaw) % 360 * 100),
            )
        if name == "HOME_POSITION":
            if not self.has_position:
                return None
            return M.MAVLink_home_position_message(
                latitude=int(self.home[0] * 1e7),
                longitude=int(self.home[1] * 1e7),
                altitude=int(self.home[2] * 1000),
                x=0,
                y=0,
                z=0,
                q=[1, 0, 0, 0],
                approach_x=0,
                approach_y=0,
                approach_z=0,
            )
        if name == "VFR_HUD":
            return M.MAVLink_vfr_hud_message(
                airspeed=math.hypot(s.v[0], s.v[1]),
                groundspeed=math.hypot(s.v[0], s.v[1]),
                heading=int(math.degrees(s.yaw) % 360),
                throttle=round(s.motor * 100) if self.armed else 0,
                alt=HOME[2] + s.p[2],
                climb=s.v[2],
            )
        if name == "EXTENDED_SYS_STATE":
            return M.MAVLink_extended_sys_state_message(
                vtol_state=M.MAV_VTOL_STATE_UNDEFINED, landed_state=self.landed_state()
            )
        if name == "RC_CHANNELS":
            raw = self.rc_raw
            count = 18 if any(raw[8:]) else 8 if any(raw) else 0
            return M.MAVLink_rc_channels_message(
                time_boot_ms=boot_ms,
                chancount=count,
                rssi=255,
                **{f"chan{i + 1}_raw": raw[i] for i in range(18)},
            )
        if name == "SERVO_OUTPUT_RAW":
            return M.MAVLink_servo_output_raw_message(
                time_usec=int(boot_ms * 1000) & 0xFFFFFFFF,
                port=0,
                **{f"servo{i}_raw": self._servo_pwm(i, now) for i in range(1, 17)},
            )
        if name == "VIBRATION":
            # WP-B (bench, 05 §4.2 S7) observability hook: a running motor test shakes the frame on the ground
            spin = max(
                (t for o, (t, _) in self.motor_tests.items() if self._motor_testing(o, now)), default=0.0
            )
            level = 4.0 + 3.0 * s.motor if self.armed else 0.3 + 6.0 * spin
            return M.MAVLink_vibration_message(
                time_usec=int(boot_ms * 1000),
                vibration_x=level,
                vibration_y=level * 0.9,
                vibration_z=level * 1.4,
                clipping_0=0,
                clipping_1=0,
                clipping_2=0,
            )
        if name == "EKF_STATUS_REPORT":
            if self.flavour != ARDUPILOT:
                return None
            flags = M.EKF_ATTITUDE | M.EKF_VELOCITY_VERT | M.EKF_POS_VERT_ABS
            if self.has_position:
                flags |= M.EKF_VELOCITY_HORIZ | M.EKF_POS_HORIZ_REL | M.EKF_PRED_POS_HORIZ_REL
            else:
                flags |= M.EKF_CONST_POS_MODE
            return M.MAVLink_ekf_status_report_message(
                flags=flags,
                velocity_variance=0.05,
                pos_horiz_variance=0.05,
                pos_vert_variance=0.05,
                compass_variance=0.05,
                terrain_alt_variance=0.0,
            )
        if name == "AUTOPILOT_VERSION":
            if self.flavour == ARDUPILOT:
                version = (4 << 24) | (5 << 16) | (7 << 8) | 255
                caps = M.MAV_PROTOCOL_CAPABILITY_PARAM_ENCODE_C_CAST
            else:
                version = (1 << 24) | (15 << 16) | (4 << 8) | 255
                caps = M.MAV_PROTOCOL_CAPABILITY_PARAM_ENCODE_BYTEWISE
            caps |= (
                M.MAV_PROTOCOL_CAPABILITY_MAVLINK2 | M.MAV_PROTOCOL_CAPABILITY_SET_POSITION_TARGET_LOCAL_NED
            )
            caps |= M.MAV_PROTOCOL_CAPABILITY_COMMAND_INT
            return M.MAVLink_autopilot_version_message(
                capabilities=caps,
                flight_sw_version=version,
                middleware_sw_version=0,
                os_sw_version=0,
                board_version=0,
                flight_custom_version=[0] * 8,
                middleware_custom_version=[0] * 8,
                os_custom_version=[0] * 8,
                vendor_id=0x1209,
                product_id=0x5741,
                uid=self.sysid,
            )
        return None

    def _motor_testing(self, output: int, now: float) -> bool:
        test = self.motor_tests.get(output)
        return test is not None and self.motor_test_from.get(output, 0.0) <= now < test[1]

    def _servo_pwm(self, output: int, now: float) -> int:
        """PWM on one output: DO_SET_SERVO wins, then a running motor test, then the flight controller."""
        held = self.servo_set.get(output)
        if (
            held is not None
            and (held[1] is None or now < held[1])
            and not (self.armed and output <= MOTOR_OUTPUTS)
        ):
            return held[0]
        test = self.motor_tests.get(output)
        if test is not None and self._motor_testing(output, now):
            return int(1000 + 1000 * test[0])
        if output <= MOTOR_OUTPUTS:
            return int(1000 + 1000 * self.quad.s.motor) if self.armed else 1000
        return 0

    def _emit_due(self, now: float) -> None:
        if now >= self.next_heartbeat:
            self._send(self._heartbeat())
            self.next_heartbeat = now + 1.0
        for name, interval in list(self.intervals.items()):
            if name == "LOCAL_POSITION_NED" and self.position_hz:
                interval = min(interval, 1.0 / self.position_hz)
            if now + 1e-6 >= self.due.get(name, 0.0):
                msg = self._build(name, now)
                if msg is not None:
                    self._send(msg)
                due = self.due.get(name, now) + interval
                self.due[name] = due if due > now else now + interval

    # ------------------------------------------------------------------ simulation

    def step(self, now: float) -> bytes:
        """Advance to ``now`` (monotonic seconds) and return the wire bytes produced since the last call."""
        if self.t is None:
            self.t = now
            self.boot = now
            self.mode_t = now
        if self.reboot_until is not None:
            self.t = now
            data = b"".join(self.out)  # the COMMAND_ACK queued before the reboot goes out; then silence
            self.out.clear()
            if now >= self.reboot_until:
                self._boot(now)
            return data
        steps = int((now - self.t) / DT)
        if steps > MAX_STEPS:
            self.t = now - MAX_STEPS * DT
            steps = MAX_STEPS
        for _ in range(max(0, steps)):
            self.t += DT
            self._inner(self.t)
        self._supervise(now)
        self._emit_due(now)
        data = b"".join(self.out)
        self.out.clear()
        return data

    def _pilot(self, now: float) -> tuple[float, float, float, float] | None:
        if self.pilot is None or self.pilot_t is None:
            return None
        timeout = STICK_TIMEOUT_S if self.flavour == ARDUPILOT else self.param("COM_RC_LOSS_T", 0.5)
        if now - self.pilot_t > max(0.1, timeout):
            return None
        return self.pilot

    def _supervise(self, now: float) -> None:
        s = self.quad.s
        b = self.quad.battery
        if s.crashed and not self.crash_reported:
            self.crash_reported = True
            if self.armed:
                self.armed = False
            text = "Crash: Disarming" if self.flavour == ARDUPILOT else "Crash detected, disarming"
            self._text(M.MAV_SEVERITY_CRITICAL, f"{text} ({s.crash_reason})")
        if not self.armed:
            self.ground_t = None
            return
        # RC failsafe: pilot input stopped in a mode that needs a pilot.
        if not s.on_ground and self.behaviour in PILOT_NEEDED and self._pilot(now) is None:
            since = self.pilot_t if self.pilot_t is not None and self.pilot_t > self.mode_t else self.mode_t
            timeout = STICK_TIMEOUT_S if self.flavour == ARDUPILOT else self.param("COM_RC_LOSS_T", 0.5)
            if now - since > timeout:
                self._radio_failsafe(now)
        # PX4 offboard loss.
        if (
            self.flavour == PX4
            and self.mode == "OFFBOARD"
            and (self.offboard_t is None or now - self.offboard_t > self.param("COM_OF_LOSS_T", 1.0))
        ):
            self._text(M.MAV_SEVERITY_WARNING, "Failsafe: offboard signal lost, holding")
            self._set_mode("LOITER" if self.has_position else "LAND", now)
        if not self.low_batt_warned and b.voltage < self._low_voltage():
            self.low_batt_warned = True
            self._text(
                M.MAV_SEVERITY_WARNING, f"Battery 1 is low {b.voltage:.2f}V used {b.consumed_mah:.0f} mAh"
            )
        # Auto-disarm on the ground.
        if s.on_ground:
            if self.ground_t is None:
                self.ground_t = now
            idle = now - self.ground_t
            if self.behaviour == "land" or (self.behaviour == "rtl" and self.rtl_phase == "land"):
                landed_delay = 0.5 if self.flavour == ARDUPILOT else self.param("COM_DISARM_LAND", 2.0)
                if idle > landed_delay and self.airborne_t is not None:
                    self._disarm("Disarming motors" if self.flavour == ARDUPILOT else "Disarmed by landing")
                    return
            delay = self.param("DISARM_DELAY", 10) if self.flavour == ARDUPILOT else 10.0
            if delay > 0 and idle > delay and not self._wants_climb(now) and self.takeoff_alt is None:
                self._disarm(
                    "Disarming motors"
                    if self.flavour == ARDUPILOT
                    else "Disarmed by auto preflight disarming"
                )
        else:
            self.ground_t = None
            if self.airborne_t is None:
                self.airborne_t = now

    def _low_voltage(self) -> float:
        if self.flavour == ARDUPILOT:
            return self.param("BATT_LOW_VOLT", 10.5)
        return 3 * 3.5

    def _radio_failsafe(self, now: float) -> None:
        if self.failsafe:
            return
        if self.flavour == ARDUPILOT:
            if self.param("FS_THR_ENABLE", 1) == 0:
                return
            self._text(M.MAV_SEVERITY_WARNING, "Radio Failsafe - Landing")
            target = "LAND"
        else:
            action = int(self.param("NAV_RCL_ACT", 2))
            if action == 0:
                return
            target = {1: "LOITER", 2: "RTL"}.get(action, "LAND")
            if target != "LAND" and not self.has_position:
                target = "LAND"
            self._text(M.MAV_SEVERITY_WARNING, "Failsafe: manual control lost")
        self._set_mode(target, now)
        self.failsafe = True

    def _wants_climb(self, now: float) -> bool:
        pilot = self._pilot(now)
        if self.behaviour in MANUAL_THROTTLE:
            return pilot is not None and pilot[3] > 0.05
        if self.behaviour in ("althold", "loiter"):
            return pilot is not None and pilot[3] > 0.5 + DEADBAND / 2
        return self.target is not None and self.target[2] > 0.1

    # ------------------------------------------------------------------ control laws

    def _max_tilt(self) -> float:
        if self.flavour == ARDUPILOT:
            limit = math.radians(self.param("ANGLE_MAX", 3000) / 100.0)
        else:
            limit = math.radians(self.param("MPC_MAN_TILT_MAX", 30.0))
        return min(math.radians(60.0), max(math.radians(5.0), limit))

    def _auto_tilt(self) -> float:
        return min(math.radians(25.0), self._max_tilt())

    def _climb_limits(self) -> tuple[float, float]:
        if self.flavour == ARDUPILOT:
            up = self.param("PILOT_SPEED_UP", 150) / 100.0
            dn = self.param("PILOT_SPEED_DN", 0) / 100.0 or up
        else:
            up = self.param("MPC_Z_VEL_MAX_UP", 1.5)
            dn = self.param("MPC_Z_VEL_MAX_DN", 1.0)
        return max(0.1, up), max(0.1, dn)

    def _xy_speed(self) -> float:
        if self.flavour == ARDUPILOT:
            return max(0.2, self.param("WPNAV_SPEED", 200) / 100.0)
        return max(0.2, self.param("MPC_XY_VEL_MAX", 2.0))

    def _loiter_speed(self) -> float:
        if self.flavour == ARDUPILOT:
            return max(0.5, self.param("LOIT_SPEED", 300) / 100.0)
        return max(0.5, self.param("MPC_VEL_MANUAL", 3.0))

    def _thrust_throttle(self, az_cmd: float) -> float:
        s = self.quad.s
        tilt = max(0.5, math.cos(s.roll) * math.cos(s.pitch))
        needed = MASS * (G + az_cmd) / tilt
        sag = min(1.0, self.quad.battery.cell_voltage / 3.3) if self.quad.battery.cell_voltage > 0 else 1.0
        fraction = needed / (self.quad.max_thrust * max(0.2, sag))
        return min(1.0, max(0.0, throttle_for_fraction(fraction)))

    def _vertical(self, z_target: float, vz_ff: float, dt: float, up: float, dn: float) -> float:
        s = self.quad.s
        vz_cmd = vz_ff + KP_Z * (z_target - s.p[2])
        vz_cmd = min(up, max(-dn, vz_cmd))
        error = vz_cmd - s.v[2]
        self.i_vz = min(3.0, max(-3.0, self.i_vz + KI_VZ * error * dt))
        az = KP_VZ * error + self.i_vz
        return self._thrust_throttle(min(8.0, max(-6.0, az)))

    def _accel_to_tilt(self, ax: float, ay: float) -> tuple[float, float]:
        """World ENU acceleration -> (roll, pitch) targets for the current heading."""
        limit = self._auto_tilt()
        a_max = G * math.tan(limit)
        mag = math.hypot(ax, ay)
        if mag > a_max:
            ax, ay = ax * a_max / mag, ay * a_max / mag
        yaw = self.quad.s.yaw
        forward = ay * math.cos(yaw) + ax * math.sin(yaw)
        right = ax * math.cos(yaw) - ay * math.sin(yaw)
        pitch = -math.atan2(forward, G)
        roll = math.atan2(right * math.cos(pitch), G)
        return roll, pitch

    def _velocity_tilt(self, vx_cmd: float, vy_cmd: float) -> tuple[float, float]:
        s = self.quad.s
        return self._accel_to_tilt(KP_VEL * (vx_cmd - s.v[0]), KP_VEL * (vy_cmd - s.v[1]))

    def _position_velocity(self, target: list[float], vmax: float) -> tuple[float, float]:
        s = self.quad.s
        ex, ey = target[0] - s.p[0], target[1] - s.p[1]
        dist = math.hypot(ex, ey)
        if dist < 1e-6:
            return 0.0, 0.0
        speed = min(vmax, math.sqrt(2.0 * A_BRAKE * dist), KP_POS * dist)
        return ex / dist * speed, ey / dist * speed

    def _yaw_rate_to(self, yaw_target: float | None, stick_yaw: float) -> float:
        if yaw_target is None:
            return stick_yaw * math.radians(120.0)
        err = wrap_pi(yaw_target - self.quad.s.yaw)
        return max(-math.radians(90.0), min(math.radians(90.0), KP_YAW * err))

    def _inner(self, now: float) -> None:
        s = self.quad.s
        dt = DT
        pilot = self._pilot(now)
        roll_s, pitch_s, yaw_s, thr_s = pilot if pilot is not None else (0.0, 0.0, 0.0, None)
        behaviour = self.behaviour
        cmd = Command()
        up, dn = self._climb_limits()
        if not self.armed:
            self.quad.step(cmd, armed=False, dt=dt)
            return
        if behaviour == "stab":
            limit = self._max_tilt()
            cmd.roll = roll_s * limit
            cmd.pitch = -pitch_s * limit
            cmd.yaw_rate = yaw_s * math.radians(120.0)
            cmd.throttle = thr_s if thr_s is not None else (self.pilot[3] if self.pilot else 0.0)
            if s.on_ground and cmd.throttle < 0.05:
                cmd.throttle = SPIN_ARM
        elif behaviour in ("althold", "loiter"):
            rate = 0.0
            if thr_s is not None:
                stick = (thr_s - 0.5) * 2.0
                if abs(stick) > DEADBAND:
                    mag = (abs(stick) - DEADBAND) / (1.0 - DEADBAND)
                    rate = mag * (up if stick > 0 else dn) * (1 if stick > 0 else -1)
            if behaviour == "althold":
                limit = self._auto_tilt()
                cmd.roll = roll_s * limit
                cmd.pitch = -pitch_s * limit
            else:
                cmd.roll, cmd.pitch = self._loiter(roll_s, pitch_s)
            cmd.yaw_rate = yaw_s * math.radians(120.0)
            if s.on_ground and rate <= 0.0:
                self.z_target = 0.0
                self.i_vz = 0.0
                cmd.throttle = SPIN_ARM
            else:
                if rate != 0.0:
                    self.z_target = s.p[2]
                cmd.throttle = self._vertical(self.z_target, rate, dt, up, dn)
        elif behaviour == "attitude" or (behaviour == "guided" and self._attitude_active(now)):
            cmd = self._attitude_command(now)
        elif behaviour == "guided" and self._velocity_active(now):
            cmd = self._velocity_command(now, dt, up, dn)
        elif behaviour in ("guided", "takeoff"):
            target = self.target or [s.p[0], s.p[1], s.p[2]]
            if s.on_ground and target[2] <= 0.05:
                cmd.throttle = SPIN_ARM
            else:
                vx, vy = self._position_velocity(target, self._xy_speed())
                cmd.roll, cmd.pitch = self._velocity_tilt(vx, vy)
                cmd.yaw_rate = self._yaw_rate_to(self.target_yaw, 0.0)
                cmd.throttle = self._vertical(target[2], 0.0, dt, min(up, TAKEOFF_SPEED), min(dn, 0.8))
            if (
                behaviour == "takeoff"
                and self.takeoff_alt is not None
                and abs(s.p[2] - self.takeoff_alt) < 0.1
            ):
                self._set_mode("LOITER", now)
        elif behaviour == "hold":
            if self.hold is None:
                self.hold = [s.p[0], s.p[1], s.p[2]]
            if s.on_ground:
                cmd.throttle = SPIN_ARM
            else:
                vx, vy = self._position_velocity(self.hold, 1.0)
                cmd.roll, cmd.pitch = self._velocity_tilt(vx, vy)
                cmd.throttle = self._vertical(self.hold[2], 0.0, dt, up, dn)
        elif behaviour == "land":
            point = self.hold or [s.p[0], s.p[1], s.p[2]]
            if self.has_position:
                vx, vy = self._position_velocity(point, 1.0)
                cmd.roll, cmd.pitch = self._velocity_tilt(vx, vy)
            cmd.yaw_rate = yaw_s * math.radians(60.0)
            if s.on_ground:
                cmd.throttle = SPIN_ARM
            else:
                self.z_target = s.p[2]
                cmd.throttle = self._vertical(s.p[2], -LAND_SPEED, dt, up, max(dn, LAND_SPEED))
        elif behaviour == "rtl":
            cmd = self._rtl(now, dt, up, dn)
        self.quad.step(cmd, armed=True, dt=dt)

    def _attitude_active(self, now: float) -> bool:
        """PX4 OFFBOARD follows attitude setpoints while newer than the last position or velocity one."""
        if self.att_t is None or now - self.att_t > SETPOINT_STALE_S:
            return False
        return self.pos_sp_t is None or self.att_t >= self.pos_sp_t

    def _attitude_command(self, now: float) -> Command:
        """Follow SET_ATTITUDE_TARGET; without a fresh setpoint stay level at the last thrust."""
        s = self.quad.s
        cmd = Command()
        if self.att_target is None:
            return cmd
        roll, pitch, yaw, rate, thrust = self.att_target
        fresh = self.att_t is not None and now - self.att_t <= SETPOINT_STALE_S
        limit = self._max_tilt()
        if fresh:
            cmd.roll = max(-limit, min(limit, roll))
            cmd.pitch = max(-limit, min(limit, pitch))
            cmd.yaw_rate = rate + KP_YAW * wrap_pi(yaw - s.yaw)
        cmd.throttle = SPIN_ARM if s.on_ground and thrust < 0.05 else thrust
        return cmd

    def _velocity_active(self, now: float) -> bool:
        if self.vel_target is None:
            return False
        if self.vel_t is not None and now - self.vel_t <= SETPOINT_STALE_S:
            return True
        s = self.quad.s
        self.vel_target = None  # setpoint stream ended: hold where the aircraft is
        self.target, self.target_yaw = [s.p[0], s.p[1], s.p[2]], None
        return False

    def _velocity_command(self, now: float, dt: float, up: float, dn: float) -> Command:
        s = self.quad.s
        cmd = Command()
        assert self.vel_target is not None
        vx, vy, vz = self.vel_target
        if s.on_ground and vz <= 0.0:
            cmd.throttle = SPIN_ARM
            return cmd
        cmd.roll, cmd.pitch = self._velocity_tilt(vx, vy)
        cmd.yaw_rate = self.vel_yaw_rate or 0.0
        if vz != 0.0:
            self.z_target = s.p[2]
        else:
            self.z_target = self.z_target if self.z_target > 0.0 else s.p[2]
        cmd.throttle = self._vertical(self.z_target, vz, dt, up, dn)
        return cmd

    def _loiter(self, roll_s: float, pitch_s: float) -> tuple[float, float]:
        s = self.quad.s
        if abs(roll_s) > 0.05 or abs(pitch_s) > 0.05:
            self.hold = None
            speed = self._loiter_speed()
            yaw = s.yaw
            fwd, right = pitch_s * speed, roll_s * speed
            vx = fwd * math.sin(yaw) + right * math.cos(yaw)
            vy = fwd * math.cos(yaw) - right * math.sin(yaw)
            return self._velocity_tilt(vx, vy)
        if self.hold is None:
            if math.hypot(s.v[0], s.v[1]) > 0.2:
                return self._velocity_tilt(0.0, 0.0)
            self.hold = [s.p[0], s.p[1], s.p[2]]
        vx, vy = self._position_velocity(self.hold, 1.0)
        return self._velocity_tilt(vx, vy)

    def _rtl(self, now: float, dt: float, up: float, dn: float) -> Command:
        s = self.quad.s
        cmd = Command()
        rtl_alt = (
            self.param("RTL_ALT", 150) / 100.0
            if self.flavour == ARDUPILOT
            else self.param("RTL_RETURN_ALT", 1.5)
        )
        if self.rtl_phase == "climb":
            if self.hold is None:
                self.hold = [s.p[0], s.p[1], max(s.p[2], rtl_alt)]
            target = self.hold
            if abs(s.p[2] - target[2]) < 0.1:
                self.rtl_phase = "return"
        elif self.rtl_phase == "return":
            target = [0.0, 0.0, self.hold[2] if self.hold else rtl_alt]
            if math.hypot(s.p[0], s.p[1]) < 0.1 and math.hypot(s.v[0], s.v[1]) < 0.2:
                self.rtl_phase = "land"
                self.hold = [0.0, 0.0, s.p[2]]
        else:
            target = self.hold or [0.0, 0.0, s.p[2]]
        vx, vy = self._position_velocity(target, self._xy_speed())
        cmd.roll, cmd.pitch = self._velocity_tilt(vx, vy)
        if self.rtl_phase == "land":
            if s.on_ground:
                cmd.throttle = SPIN_ARM
            else:
                cmd.throttle = self._vertical(s.p[2], -LAND_SPEED, dt, up, max(dn, LAND_SPEED))
        else:
            cmd.throttle = self._vertical(target[2], 0.0, dt, up, dn)
        return cmd


def _c(value: float) -> float:
    return max(-1.0, min(1.0, value))


def _text(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", "replace")
    return str(value).split("\x00", 1)[0].strip()
