#!/usr/bin/env python3
"""Fake PX4 flight controller on a serial port, for the serial link and hardware in the loop (11 §7).

``FakePx4`` speaks MAVLink 2 the way a PX4 board does over USB, as far as Relay's serial link and HITL flow
need it: a 1 Hz HEARTBEAT (autopilot PX4, quadrotor; ``MAV_MODE_FLAG_HIL_ENABLED`` while it booted with
``SYS_HITL 1``), AUTOPILOT_VERSION on request, the parameter protocol (list, read by index or name, PARAM_SET
with the PARAM_VALUE echo; integer values bytewise in the float field as PX4 does), COMMAND_ACK, arming,
MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN (the board goes away for ``reboot_s`` and boots again from its stored
parameters) and, in HITL mode, a HIL_ACTUATOR_CONTROLS stream. It counts the HIL_SENSOR and HIL_GPS frames
it receives. Hooks for tests: ``ignore_set`` (PARAM_SETs it acknowledges with the old value),
``on_boot`` (parameter values a boot changes by itself), ``baud`` (a UART that only answers at that rate).

Two transports:

* ``FakePort`` / ``opener``: an in-process stand-in for a pyserial port, driven by the caller's clock
  (``FleetService.serial_opener`` in the tests). While the board reboots the port raises ``OSError`` and
  the device cannot be opened, as a USB CDC device that disappears.
* the command line: a pseudo-terminal; connect Relay to the printed device path. A reboot there only
  stops the traffic for ``--reboot-s`` (a pseudo-terminal cannot disappear).

    python tools/fakes/fake_px4_serial.py [--reboot-s 2] [--version 1.16.2]
"""

from __future__ import annotations

import argparse
import errno
import os
import random
import select
import struct
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

try:
    from relay.mav.mavlink import Encoder, M, Parser
except ImportError:  # running from a checkout without the backend installed
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    from relay.mav.mavlink import Encoder, M, Parser

INT32 = M.MAV_PARAM_TYPE_INT32
REAL32 = M.MAV_PARAM_TYPE_REAL32
UINT8 = M.MAV_PARAM_TYPE_UINT8
PX4_CUSTOM_POSCTL = 3 << 16  # main mode POSCTL

# A small but representative PX4 table: integers of several widths and floats.
DEFAULT_PARAMS: dict[str, tuple[float, int]] = {
    "SYS_AUTOSTART": (4001.0, INT32),
    "SYS_HITL": (0.0, INT32),
    "SYS_AUTOCONFIG": (0.0, INT32),
    "MAV_SYS_ID": (1.0, INT32),
    "COM_RC_IN_MODE": (1.0, INT32),
    "MPC_THR_HOVER": (0.35, REAL32),
    "MC_ROLLRATE_P": (0.15, REAL32),
    "MC_PITCHRATE_P": (0.15, REAL32),
    "BAT1_N_CELLS": (3.0, INT32),
    "BAT1_CAPACITY": (850.0, REAL32),
    "CBRK_IO_SAFETY": (22027.0, INT32),
    "EKF2_GPS_CTRL": (7.0, INT32),
    "SDLOG_PROFILE": (1.0, INT32),
    "SENS_EN_BAROSIM": (0.0, INT32),
    "LND_FLIGHT_T_LO": (1234.0, INT32),
    "LND_FLIGHT_T_HI": (0.0, INT32),
    "NAV_DLL_ACT": (2.0, INT32),
    "UAVCAN_ENABLE": (0.0, INT32),
    "CAL_ACC0_ID": (1310988.0, INT32),
    "CAL_GYRO0_XOFF": (0.0021, REAL32),
    "MOT_ORDERING": (0.0, INT32),
    "PWM_MAIN_FUNC1": (101.0, INT32),
    "RC_MAP_THROTTLE": (3.0, INT32),
    "SER_TEL1_BAUD": (57600.0, INT32),
    "GF_ACTION": (1.0, INT32),
    "MPC_XY_VEL_MAX": (12.0, REAL32),
    "SYS_HAS_MAG": (1.0, INT32),
    "SYS_MC_EST_GROUP": (2.0, UINT8),
}


def encode_value(value: float, ptype: int) -> float:
    """PX4 puts integer parameters into the float field bytewise."""
    if ptype == REAL32:
        return float(value)
    fmt = "<I" if ptype in (M.MAV_PARAM_TYPE_UINT8, M.MAV_PARAM_TYPE_UINT16, M.MAV_PARAM_TYPE_UINT32) else "<i"
    return struct.unpack("<f", struct.pack(fmt, int(value) & 0xFFFFFFFF if fmt == "<I" else int(value)))[0]


def decode_value(value: float, ptype: int) -> float:
    if ptype == REAL32:
        return float(struct.unpack("<f", struct.pack("<f", value))[0])
    raw = struct.pack("<f", value)
    fmt = "<I" if ptype in (M.MAV_PARAM_TYPE_UINT8, M.MAV_PARAM_TYPE_UINT16, M.MAV_PARAM_TYPE_UINT32) else "<i"
    return float(struct.unpack(fmt, raw)[0])


class FakePx4:
    """The board. Not thread-safe; one caller drives it (``receive`` / ``poll``) with its own clock."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        *,
        params: dict[str, tuple[float, int]] | None = None,
        version: tuple[int, int, int] = (1, 16, 2),
        sysid: int = 1,
        autopilot: int = M.MAV_AUTOPILOT_PX4,
        reboot_s: float = 2.0,
        baud: int | None = None,
        serial_number: str = "3E0031000B51333130373938",
    ) -> None:
        self.clock = clock
        self.flash = {k: (float(v), int(t)) for k, (v, t) in (params or DEFAULT_PARAMS).items()}
        self.names = list(self.flash)
        self.params = dict(self.flash)
        self.version = version
        self.sysid = sysid
        self.autopilot = autopilot
        self.reboot_s = reboot_s
        self.baud = baud  # None: USB CDC, any rate works
        self.serial_number = serial_number
        self.encoder = Encoder(sysid, 1)
        self.parser = Parser()
        self.out = bytearray()
        self.up = True
        self.hil = round(self.params.get("SYS_HITL", (0.0, INT32))[0]) == 1
        self.armed = False
        self.throttle = 0.6  # actuator output while armed (HITL)
        self.last_hb = -1e9
        self.last_act = -1e9
        self.down_at: float | None = None
        self.up_at: float | None = None
        self.reboots = 0
        self.ignore_set: set[str] = set()
        self.on_boot: dict[str, float] = {}  # applied to the stored parameters at the next boot
        self.hil_sensor = 0
        self.hil_gps = 0
        self.received: list[str] = []
        self.ack_reboot = True

    # ------------------------------------------------------------------ timeline

    def update(self, now: float) -> None:
        if self.down_at is not None and now >= self.down_at:
            self.down_at = None
            self.up = False
            self.out.clear()
            self.up_at = now + self.reboot_s
        if not self.up and self.up_at is not None and now >= self.up_at:
            self._boot(now)

    def _boot(self, now: float) -> None:
        self.reboots += 1
        for name, value in self.on_boot.items():
            if name in self.flash:
                self.flash[name] = (float(value), self.flash[name][1])
        self.on_boot = {}
        self.params = dict(self.flash)
        self.hil = round(self.params.get("SYS_HITL", (0.0, INT32))[0]) == 1
        self.armed = False
        self.up = True
        self.up_at = None
        self.parser = Parser()
        self.last_hb = -1e9

    # ------------------------------------------------------------------ MAVLink

    def _send(self, msg: Any) -> None:
        if self.up:
            self.out += self.encoder.encode(msg)

    def heartbeat(self) -> Any:
        base = M.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED
        if self.hil:
            base |= M.MAV_MODE_FLAG_HIL_ENABLED
        if self.armed:
            base |= M.MAV_MODE_FLAG_SAFETY_ARMED
        return M.MAVLink_heartbeat_message(
            M.MAV_TYPE_QUADROTOR, self.autopilot, base, PX4_CUSTOM_POSCTL, M.MAV_STATE_STANDBY, 3
        )

    def _param_value(self, name: str) -> None:
        value, ptype = self.params[name]
        index = self.names.index(name)
        self._send(
            M.MAVLink_param_value_message(
                name.encode("ascii"), encode_value(value, ptype), ptype, len(self.names), index
            )
        )

    def _ack(self, command: int, result: int = M.MAV_RESULT_ACCEPTED) -> None:
        self._send(M.MAVLink_command_ack_message(command, result))

    def receive(self, data: bytes, now: float) -> None:
        self.update(now)
        if not self.up:
            return
        for msg in self.parser.parse(data):
            self._handle(msg, now)

    def _handle(self, msg: Any, now: float) -> None:
        mtype = msg.get_type()
        self.received.append(mtype)
        if len(self.received) > 4096:
            del self.received[:2048]
        if mtype == "HIL_SENSOR":
            self.hil_sensor += 1
        elif mtype == "HIL_GPS":
            self.hil_gps += 1
        elif mtype == "PARAM_REQUEST_LIST":
            for name in self.names:
                self._param_value(name)
        elif mtype == "PARAM_REQUEST_READ":
            name = msg.param_id if isinstance(msg.param_id, str) else msg.param_id.decode()
            name = name.split("\x00", 1)[0]
            if int(msg.param_index) >= 0 and int(msg.param_index) < len(self.names):
                self._param_value(self.names[int(msg.param_index)])
            elif name in self.params:
                self._param_value(name)
        elif mtype == "PARAM_SET":
            name = msg.param_id if isinstance(msg.param_id, str) else msg.param_id.decode()
            name = name.split("\x00", 1)[0]
            if name not in self.params:
                return
            ptype = self.params[name][1]
            if name not in self.ignore_set:
                value = decode_value(float(msg.param_value), ptype)
                self.params[name] = (value, ptype)
                self.flash[name] = (value, ptype)
            self._param_value(name)
        elif mtype == "COMMAND_LONG":
            self._command(msg, now)
        elif mtype == "MANUAL_CONTROL":
            pass

    def _command(self, msg: Any, now: float) -> None:
        cmd = int(msg.command)
        if cmd == M.MAV_CMD_REQUEST_MESSAGE and int(msg.param1) == M.MAVLINK_MSG_ID_AUTOPILOT_VERSION:
            self._ack(cmd)
            major, minor, patch = self.version
            self._send(
                M.MAVLink_autopilot_version_message(
                    0,
                    (major << 24) | (minor << 16) | (patch << 8) | 255,
                    0,
                    0,
                    0,
                    bytes.fromhex("54f0455f00000000"),
                    bytes(8),
                    bytes(8),
                    0x26AC,
                    0x0012,
                    0x3E0031000B513331,
                )
            )
        elif cmd == M.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN:
            if self.armed:
                self._ack(cmd, M.MAV_RESULT_DENIED)
                return
            if not self.ack_reboot:
                return
            self._ack(cmd)
            self.down_at = now + 0.05
        elif cmd == M.MAV_CMD_COMPONENT_ARM_DISARM:
            self.armed = float(msg.param1) > 0.5
            self._ack(cmd)
        else:
            self._ack(cmd)

    def poll(self, now: float) -> bytes:
        """What the board sends by ``now``: heartbeats, and actuator outputs in HITL mode."""
        self.update(now)
        if not self.up:
            return b""
        if now - self.last_hb >= 1.0:
            self.last_hb = now
            self._send(self.heartbeat())
        if self.hil and now - self.last_act >= 0.02:
            self.last_act = now
            u = self.throttle if self.armed else 0.0
            mode = M.MAV_MODE_FLAG_SAFETY_ARMED if self.armed else 0
            self._send(M.MAVLink_hil_actuator_controls_message(int(now * 1e6), [u] * 4 + [0.0] * 12, mode, 0))
        data = bytes(self.out)
        self.out.clear()
        return data

    # ------------------------------------------------------------------ in-process port

    def opener(self, device: str, baud: int) -> FakePort:
        """``FleetService.serial_opener``: the port while the board is present."""
        self.update(self.clock())
        if not self.up:
            from relay.mav.serial_link import SerialUnavailable

            raise SerialUnavailable("not_found", f"{device}: no such device")
        self.last_hb = -1e9  # a new connection hears a heartbeat at once (the caller's clock may stand still)
        return FakePort(self, baud)

    def set_param(self, name: str, value: float) -> None:
        ptype = self.flash[name][1]
        self.flash[name] = (float(value), ptype)
        self.params[name] = (float(value), ptype)


class FakePort:
    """The pyserial calls ``SerialLink`` makes, against a ``FakePx4``. A rate other than the board's UART
    rate reads as line noise."""

    def __init__(self, board: FakePx4, baud: int) -> None:
        self.board = board
        self.baud = baud
        self.open = True
        self.buffer = bytearray()
        self.noise = random.Random(baud)

    @property
    def garbled(self) -> bool:
        return self.board.baud is not None and self.baud != self.board.baud

    def _pump(self) -> None:
        if not self.open:
            raise OSError(errno.EBADF, "port closed")
        board = self.board
        now = board.clock()
        board.update(now)
        if not board.up:
            raise OSError(errno.EIO, "Input/output error")
        data = board.poll(now)
        if self.garbled and data:
            data = bytes(self.noise.randrange(256) for _ in range(len(data)))
        self.buffer += data

    @property
    def in_waiting(self) -> int:
        self._pump()
        return len(self.buffer)

    def read(self, n: int = 1) -> bytes:
        self._pump()
        out = bytes(self.buffer[:n])
        del self.buffer[:n]
        return out

    def write(self, data: bytes) -> int:
        if not self.open:
            raise OSError(errno.EBADF, "port closed")
        board = self.board
        now = board.clock()
        board.update(now)
        if not board.up:
            raise OSError(errno.EIO, "Input/output error")
        if not self.garbled:
            board.receive(bytes(data), now)
        return len(data)

    def close(self) -> None:
        self.open = False


# ---------------------------------------------------------------------------- command line (pty)


def serve_pty(board: FakePx4, stop: Callable[[], bool] | None = None, announce: bool = True) -> None:
    """Serve ``board`` on a new pseudo-terminal until ``stop()``; prints the device path."""
    master, slave = os.openpty()
    path = os.ttyname(slave)
    if announce:
        print(path, flush=True)
    serve_fd(board, master, stop)
    os.close(slave)


def serve_fd(board: FakePx4, fd: int, stop: Callable[[], bool] | None = None) -> None:
    os.set_blocking(fd, False)
    while not (stop and stop()):
        readable, _, _ = select.select([fd], [], [], 0.005)
        now = board.clock()
        if readable:
            try:
                data = os.read(fd, 65536)
            except BlockingIOError:
                data = b""
            except OSError:
                data = b""  # no reader on the other side yet
            if data:
                board.receive(data, now)
        out = board.poll(now)
        if out:
            try:
                os.write(fd, out)
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reboot-s", type=float, default=2.0)
    parser.add_argument("--version", default="1.16.2")
    args = parser.parse_args(argv)
    version = tuple(int(x) for x in args.version.split("."))
    board = FakePx4(reboot_s=args.reboot_s, version=version)  # type: ignore[arg-type]
    try:
        serve_pty(board)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
