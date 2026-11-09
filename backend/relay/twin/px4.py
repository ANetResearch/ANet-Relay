"""PX4 SITL twin (11 §6.2): the process, its loopback links and the MAVLink HIL lockstep bridge.

Launch: ``PX4_SIM_MODEL=none_iris RELAY_PX4_PARAMS=<file> <pack>/bin/px4 -d -i <n> -w <workdir>
-s etc/init.d-posix/rcS <pack>/etc``. The pack's ``px4-rc.mavlink`` sources ``RELAY_PX4_PARAMS`` (the
baseline ``defaults/relay-quadx.params``, then the profile with ``param set``) and starts the GCS link
``mavlink start -x -u 18570+n -o 24550+n``; the pack's PX4 binds its MAVLink UDP sockets to 127.0.0.1.

Links, all on 127.0.0.1 (11 §2):

* HIL: Relay listens on TCP ``4560 + n``; PX4's ``simulator_mavlink`` connects to it and blocks until then.
* GCS: Relay binds UDP ``24550 + n`` and sends to PX4's ``18570 + n``.

Lockstep: per 250 Hz world step Relay sends ``HIL_SENSOR`` (and ``HIL_GPS`` at 10 Hz) stamped with the
physics time and waits for the ``HIL_ACTUATOR_CONTROLS`` that answers it; PX4's clock only moves with those
stamps. While PX4 boots it answers some sensor frames late or not at all: after ``REPLY_TIMEOUT_BOOT_S``
without an answer the step goes ahead with the last outputs.
"""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path
from typing import Any

from ..mav.mavlink import Parser
from .hil import decode_actuators
from .packs import Pack
from .process import (
    LOOPBACK,
    FirmwareProcess,
    TwinStartError,
    px4_lock_free,
    tcp_listener,
    udp_free,
    udp_socket,
)

HIL_BASE = 4560
GCS_LOCAL_BASE = 18570  # PX4's end
GCS_REMOTE_BASE = 24550  # Relay's end
PAYLOAD_BASE = 14280  # PX4's onboard payload link (loopback)
STEP_US = 4000
START_US = 1_000_000
REPLY_TIMEOUT_BOOT_S = 0.05


class Ports:
    def __init__(self, n: int) -> None:
        self.n = n
        self.hil_port = HIL_BASE + n
        self.gcs_local = GCS_LOCAL_BASE + n
        self.gcs_remote = GCS_REMOTE_BASE + n
        for port in (self.gcs_local, PAYLOAD_BASE + n):
            if not udp_free(port):
                raise OSError(f"UDP {port} is in use")
        if not px4_lock_free(n):
            raise OSError(f"PX4 instance {n} is running")
        self.listener = tcp_listener(self.hil_port)
        try:
            self.gcs = udp_socket(self.gcs_remote)
        except OSError:
            self.listener.close()
            raise
        self.gcs.setblocking(False)

    def close(self) -> None:
        for s in (self.listener, self.gcs):
            try:
                s.close()
            except OSError:
                pass


def argv(pack: Pack, n: int, workdir: Path) -> list[str]:
    romfs = pack.path / (pack.romfs or "etc")
    return [
        str(pack.executable),
        "-d",
        "-i",
        str(n),
        "-w",
        str(workdir),
        "-s",
        "etc/init.d-posix/rcS",
        str(romfs),
    ]


class Px4Bridge:
    family = "px4"

    def __init__(self, pack: Pack, n: int, ports: Ports, workdir: Path) -> None:
        self.pack = pack
        self.n = n
        self.ports = ports
        self.workdir = workdir
        self.process: FirmwareProcess | None = None
        self.hil: socket.socket | None = None
        self.parser = Parser()
        self.t_us = START_US
        self.awaiting = False  # a HIL_SENSOR is out and its answer has not arrived
        self.controls = [0.0, 0.0, 0.0, 0.0]
        self.armed = False
        self.frames = 0
        self.late = 0  # steps that went ahead without an answer (boot)
        self.booting = True
        self._gcs_buffer = bytearray()
        self._rx = bytearray()

    # ------------------------------------------------------------------ lifecycle

    def launch(self, params_file: Path) -> None:
        env = dict(os.environ)
        env.update(
            PX4_SIM_MODEL="none_iris",
            RELAY_PX4_PARAMS=str(params_file),
            RELAY_GCS_PORT_LOCAL=str(self.ports.gcs_local),
            RELAY_GCS_PORT_REMOTE=str(self.ports.gcs_remote),
        )
        env.pop("PX4_SIMULATOR", None)
        env.pop("PX4_GZ_WORLD", None)
        self.process = FirmwareProcess(argv(self.pack, self.n, self.workdir), self.workdir, env)
        self.process.start()

    def _accept(self, deadline: float) -> None:
        listener = self.ports.listener
        listener.settimeout(0.05)
        while self.hil is None:
            if time.monotonic() > deadline:
                raise TwinStartError("the firmware did not connect to the simulator port", self.tail())
            if not self.alive():
                raise TwinStartError(f"the firmware exited with code {self.returncode}", self.tail())
            try:
                conn, _addr = listener.accept()
            except TimeoutError:
                continue
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.hil = conn

    def boot(self, body: Any, deadline: float) -> None:
        self._accept(deadline)
        parser = Parser()
        while True:
            if time.monotonic() > deadline:
                raise TwinStartError("no heartbeat from the firmware within 15 s", self.tail())
            if not self.alive():
                raise TwinStartError(f"the firmware exited with code {self.returncode}", self.tail())
            self.advance(body, time.monotonic() + 0.5)
            data = self._read_gcs()
            if data:
                self._gcs_buffer += data
                if any(m.get_type() == "HEARTBEAT" and m.autopilot != 8 for m in parser.parse(data)):
                    self.booting = False
                    return

    def alive(self) -> bool:
        return self.process is not None and self.process.alive()

    @property
    def returncode(self) -> int | None:
        return None if self.process is None else self.process.returncode

    def tail(self) -> list[str]:
        return [] if self.process is None else self.process.tail()

    def close(self) -> None:
        if self.process is not None:
            self.process.stop()
        if self.hil is not None:
            try:
                self.hil.close()
            except OSError:
                pass
            self.hil = None
        self.ports.close()

    # ------------------------------------------------------------------ GCS link

    def gcs_write(self, data: bytes) -> None:
        if not data:
            return
        try:
            self.ports.gcs.sendto(data, (LOOPBACK, self.ports.gcs_local))
        except OSError:
            pass

    def _read_gcs(self) -> bytes:
        chunks = []
        while True:
            try:
                data, addr = self.ports.gcs.recvfrom(65536)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break
            if addr[0] == LOOPBACK:
                chunks.append(data)
        return b"".join(chunks)

    def gcs_read(self) -> bytes:
        data = bytes(self._gcs_buffer) + self._read_gcs()
        self._gcs_buffer.clear()
        return data

    # ------------------------------------------------------------------ lockstep

    def advance(self, body: Any, deadline: float, dt_world: float = 0.004) -> bool:
        """One world step: send the sensors at the physics time, wait for the answer, step the physics."""
        hil = self.hil
        if hil is None:
            return False
        if not self.awaiting:
            try:
                hil.sendall(body.hil_frame(self.t_us, dt_world))
            except OSError:
                return False
            self.awaiting = True
            self.sent_at = time.monotonic()
        got = self._wait_reply(deadline)
        if not got:
            if not (self.booting and time.monotonic() - self.sent_at >= REPLY_TIMEOUT_BOOT_S):
                return False
            self.late += 1  # PX4 is still starting: go ahead with the last outputs
        self.awaiting = False
        body.apply(self.controls, self.armed, dt_world)
        self.t_us += STEP_US
        self.frames += 1
        return True

    def _wait_reply(self, deadline: float) -> bool:
        hil = self.hil
        assert hil is not None
        while True:
            if self._take_reply():
                return True
            limit = deadline
            if self.booting:
                limit = min(deadline, self.sent_at + REPLY_TIMEOUT_BOOT_S)
            remaining = limit - time.monotonic()
            if remaining <= 0.0:
                return False
            hil.settimeout(remaining)
            try:
                data = hil.recv(65536)
            except TimeoutError:
                return False
            except OSError:
                return False
            if not data:
                return False
            self._rx += data

    def _take_reply(self) -> bool:
        if not self._rx:
            return False
        msgs = self.parser.parse(bytes(self._rx))
        self._rx.clear()
        got = False
        for msg in msgs:
            if msg.get_type() == "HIL_ACTUATOR_CONTROLS":
                self.controls, self.armed = decode_actuators(msg)
                got = True
        return got


__all__ = ["Ports", "Px4Bridge", "argv"]
