"""Driver protocol (03 §3.2) and the shared MAVLink command encoder.

Both drivers turn the same calls into the same MAVLink 2 frames; they differ
only in where the bytes go (a UDP socket, or the in-process simulator).
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import Any, Literal, Protocol

from .. import frame
from ..config import GCS_COMPID, GCS_SYSID, RELEASE_FRAMES, STICK_HZ
from ..mav import modes
from ..mav.mavlink import Encoder, M
from ..mav.params import wire_value
from ..model import StickInput

POSITION_ONLY_MASK = 8 | 16 | 32 | 64 | 128 | 256 | 2048  # ignore velocity, acceleration, yaw rate
YAW_IGNORE = 1024


class Driver(Protocol):
    kind: Literal["real", "sim"]

    def tick(self, now: float) -> list[Any]: ...
    def send_sticks(self, s: StickInput, signs: Mapping[str, int] | None = None) -> None: ...
    def stop_sticks(self) -> None: ...
    def arm(self, on: bool) -> None: ...
    def set_mode(self, name: str) -> None: ...
    def command_long(self, cmd: int, params: tuple[float, ...]) -> None: ...
    def position_target_local(self, x: float, y: float, z: float, yaw: float | None) -> None: ...
    def request_params(self) -> None: ...
    def request_param(self, index: int) -> None: ...
    def set_param(self, name: str, value: float, ptype: int) -> None: ...
    def heartbeat(self) -> None: ...
    def send_message(self, msg: Any) -> None: ...
    def set_target(self, sysid: int, compid: int, autopilot: str | None) -> None: ...


class MavCommands:
    """Encodes driver calls as MAVLink frames and hands bytes to ``_write``."""

    kind: Literal["real", "sim"] = "real"

    def __init__(self) -> None:
        self.encoder = Encoder(GCS_SYSID, GCS_COMPID)
        self.target_system = 1
        self.target_component = 1
        self.autopilot: str | None = None
        self._release_left = 0
        self._release_next = 0.0
        self.sent: dict[str, int] = {}
        self.tx_times: list[float] = []
        self.clock = time.monotonic

    # transport -------------------------------------------------------------

    def _write(self, data: bytes) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def send_message(self, msg: Any) -> None:
        self.sent[msg.get_type()] = self.sent.get(msg.get_type(), 0) + 1
        self.tx_times.append(self.clock())
        if len(self.tx_times) > 512:
            del self.tx_times[:256]
        self._write(self.encoder.encode(msg))

    def set_target(self, sysid: int, compid: int, autopilot: str | None) -> None:
        self.target_system = sysid
        self.target_component = compid
        self.autopilot = autopilot

    def pace(self, now: float) -> None:
        """Send pending RC release frames at the stick rate (03 §5.3)."""
        if self._release_left > 0 and now >= self._release_next:
            self._release_left -= 1
            self._release_next = now + 1.0 / STICK_HZ
            self._override(frame.ARDUPILOT_RELEASE)

    # control ----------------------------------------------------------------

    def _override(self, channels: tuple[int, ...]) -> None:
        self.send_message(
            M.MAVLink_rc_channels_override_message(self.target_system, self.target_component, *channels[:8])
        )

    def send_sticks(self, s: StickInput, signs: Mapping[str, int] | None = None) -> None:
        self._release_left = 0
        if self.autopilot == modes.PX4:
            x, y, z, r = frame.px4_manual(s.roll, s.pitch, s.yaw, s.throttle, signs)
            self.send_message(M.MAVLink_manual_control_message(self.target_system, x, y, z, r, 0))
        else:
            self._override(frame.ardupilot_channels(s.roll, s.pitch, s.yaw, s.throttle, signs))

    def stop_sticks(self) -> None:
        """ArduPilot: 4 frames of channels 1-8 = 0 (release to RC). PX4: stop sending MANUAL_CONTROL."""
        if self.autopilot == modes.PX4:
            self._release_left = 0
            return
        self._release_left = RELEASE_FRAMES
        self._release_next = 0.0

    def command_long(self, cmd: int, params: tuple[float, ...]) -> None:
        p = (tuple(params) + (0.0,) * 7)[:7]
        self.send_message(
            M.MAVLink_command_long_message(self.target_system, self.target_component, cmd, 0, *p)
        )

    def arm(self, on: bool) -> None:
        # param2 = 0: never the 21196 force flag (03 §5.3, H11).
        self.command_long(M.MAV_CMD_COMPONENT_ARM_DISARM, (1.0 if on else 0.0, 0.0))

    def set_mode(self, name: str) -> None:
        self.command_long(M.MAV_CMD_DO_SET_MODE, modes.set_mode_params(self.autopilot, name))

    def position_target_local(self, x: float, y: float, z: float, yaw: float | None) -> None:
        """Target in local ENU metres; sent as LOCAL_NED. ``yaw`` is the NED heading in radians."""
        mask = POSITION_ONLY_MASK | (YAW_IGNORE if yaw is None else 0)
        self.send_message(
            M.MAVLink_set_position_target_local_ned_message(
                int(time.monotonic() * 1000) & 0xFFFFFFFF,
                self.target_system,
                self.target_component,
                M.MAV_FRAME_LOCAL_NED,
                mask,
                y,
                x,
                -z,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0 if yaw is None else yaw,
                0.0,
            )
        )

    def request_params(self) -> None:
        self.send_message(M.MAVLink_param_request_list_message(self.target_system, self.target_component))

    def request_param(self, index: int) -> None:
        self.send_message(
            M.MAVLink_param_request_read_message(self.target_system, self.target_component, b"", index)
        )

    def set_param(self, name: str, value: float, ptype: int) -> None:
        self.send_message(
            M.MAVLink_param_set_message(
                self.target_system,
                self.target_component,
                name.encode("ascii"),
                wire_value(value, ptype, self.autopilot),
                ptype,
            )
        )

    def heartbeat(self) -> None:
        self.send_message(
            M.MAVLink_heartbeat_message(M.MAV_TYPE_GCS, M.MAV_AUTOPILOT_INVALID, 0, 0, M.MAV_STATE_ACTIVE, 3)
        )

    def timesync(self, now_ns: int) -> None:
        self.send_message(M.MAVLink_timesync_message(0, now_ns))

    def request_data_stream(self, stream_id: int, hz: float) -> None:
        self.send_message(
            M.MAVLink_request_data_stream_message(
                self.target_system, self.target_component, stream_id, int(hz), 1 if hz > 0 else 0
            )
        )

    def set_message_interval(self, msg_id: int, hz: float) -> None:
        interval = -1.0 if hz <= 0 else 1e6 / hz
        self.command_long(M.MAV_CMD_SET_MESSAGE_INTERVAL, (float(msg_id), interval))

    def request_message(self, msg_id: int) -> None:
        self.command_long(M.MAV_CMD_REQUEST_MESSAGE, (float(msg_id),))


NAN = math.nan
