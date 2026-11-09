"""ArduPilot SITL twin (11 §6.1): the process, its loopback links and the JSON physics bridge.

Launch: ``arducopter --model JSON:127.0.0.1 --speedup 100 -I <n> --serial0 tcpclient:127.0.0.1:<5760+10n>
--serial1 none ... --defaults <copter.parm>,<relay-quadx.parm>,<profile.parm> --home <lat,lon,alt,hdg>`` in a
fresh working directory (``eeprom.bin`` is created anew at every start, so the parameters are the profile's).

Links, all on 127.0.0.1 (11 §2):

* GCS: Relay listens on TCP ``5760 + 10n``; the firmware's SERIAL0 connects out to it. Every other SERIAL
  port that stock SITL serves over TCP is ``none`` (SERIAL3 and 4 are the simulated GPS receivers), so the
  firmware opens no TCP server (stock SITL listens on 0.0.0.0 for them).
* Physics: Relay binds UDP ``9002 + 10n``; the firmware sends ``magic 18458, frame_rate, frame_count,
  pwm[16]`` per frame and waits for one ``\\n{json}\\n`` reply with the state after that frame.
* The official binary also binds two UDP sockets on 0.0.0.0 that no option moves: the RC input port
  ``5501 + 10n`` and the physics client socket. Relay checks the RC port is free before it starts the
  twin; the pack build is where a loopback-only binary would come from (11 §2, documented limitation).

Lockstep: ``advance`` runs firmware frames until the firmware's time reaches the next 250 Hz world step;
at ``SIM_RATE_HZ 1000`` that is four frames. A frame count that goes backwards is a firmware reboot: the
firmware time restarts and the bridge reports it, so the twin can reset its physics.
"""

from __future__ import annotations

import socket
import struct
import time
from pathlib import Path
from typing import Any

from ..mav.mavlink import Parser
from .packs import Pack
from .process import LOOPBACK, FirmwareProcess, TwinStartError, tcp_listener, udp_free, udp_socket

MAGIC = 18458
FRAME = struct.Struct("<HHI16H")
GCS_BASE = 5760
PHYSICS_BASE = 9002
RC_IN_BASE = 5501
# Serial ports to switch off: stock SITL serves SERIAL1, 2 and 5-8 as TCP servers; SERIAL3 and 4 are the
# simulated GPS receivers (no socket) and stay.
SERIALS_OFF = (1, 2, 5, 6, 7, 8)


class Ports:
    """Relay's sockets of one ArduPilot instance."""

    def __init__(self, n: int) -> None:
        self.n = n
        self.gcs_port = GCS_BASE + 10 * n
        self.physics_port = PHYSICS_BASE + 10 * n
        self.rc_port = RC_IN_BASE + 10 * n
        if not udp_free(self.rc_port, "0.0.0.0"):
            raise OSError(f"UDP {self.rc_port} is in use")
        self.listener = tcp_listener(self.gcs_port)
        try:
            self.physics = udp_socket(self.physics_port)
        except OSError:
            self.listener.close()
            raise

    def close(self) -> None:
        for s in (self.listener, self.physics):
            try:
                s.close()
            except OSError:
                pass


def argv(
    pack: Pack, n: int, ports: Ports, defaults: list[Path], home: tuple[float, float, float]
) -> list[str]:
    lat, lon, alt = home
    args = [
        str(pack.executable),
        "--model",
        f"JSON:{LOOPBACK}",
        "--speedup",
        "100",
        "-I",
        str(n),
        "--serial0",
        f"tcpclient:{LOOPBACK}:{ports.gcs_port}",
    ]
    for i in SERIALS_OFF:
        args += [f"--serial{i}", "none"]
    args += [
        "--defaults",
        ",".join(str(p) for p in defaults),
        "--home",
        f"{lat:.7f},{lon:.7f},{alt:.2f},0",
    ]
    return args


class ArduPilotBridge:
    family = "ardupilot"

    def __init__(self, pack: Pack, n: int, ports: Ports, workdir: Path) -> None:
        self.pack = pack
        self.n = n
        self.ports = ports
        self.workdir = workdir
        self.process: FirmwareProcess | None = None
        self.gcs: socket.socket | None = None
        self.peer: tuple[str, int] | None = None  # the firmware's physics socket
        self.fw_time = 0.0  # s of firmware time the physics has answered
        self.last_count: int | None = None
        self.rate = 1000
        self.reboots = 0
        self.frames = 0
        self._out = b""
        self._gcs_buffer = bytearray()
        self.ports.physics.setblocking(True)

    # ------------------------------------------------------------------ lifecycle

    def launch(self, defaults: list[Path], home: tuple[float, float, float]) -> None:
        self.process = FirmwareProcess(argv(self.pack, self.n, self.ports, defaults, home), self.workdir)
        self.process.start()

    def _accept(self, deadline: float) -> None:
        listener = self.ports.listener
        listener.settimeout(0.05)
        while self.gcs is None:
            if time.monotonic() > deadline:
                raise TwinStartError("the firmware did not connect its MAVLink link", self.tail())
            if not self.alive():
                raise TwinStartError(f"the firmware exited with code {self.returncode}", self.tail())
            try:
                conn, _addr = listener.accept()
            except TimeoutError:
                continue
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.setblocking(False)
            self.gcs = conn

    def boot(self, body: Any, deadline: float) -> None:
        """Run the lockstep until the firmware's first HEARTBEAT on the GCS link (wall ``deadline``)."""
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
        if self.gcs is not None:
            try:
                self.gcs.close()
            except OSError:
                pass
            self.gcs = None
        self.ports.close()

    # ------------------------------------------------------------------ GCS link

    def gcs_write(self, data: bytes) -> None:
        if self.gcs is None or not data:
            return
        try:
            self.gcs.sendall(data)
        except (BlockingIOError, InterruptedError):
            pass  # the firmware is behind on reading: drop, as a radio link would
        except OSError:
            self._gcs_lost()

    def _read_gcs(self) -> bytes:
        if self.gcs is None:
            self._reconnect()
            return b""
        chunks = []
        while True:
            try:
                data = self.gcs.recv(65536)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                self._gcs_lost()
                break
            if not data:
                self._gcs_lost()
                break
            chunks.append(data)
        return b"".join(chunks)

    def gcs_read(self) -> bytes:
        data = bytes(self._gcs_buffer) + self._read_gcs()
        self._gcs_buffer.clear()
        return data

    def _gcs_lost(self) -> None:
        if self.gcs is not None:
            try:
                self.gcs.close()
            except OSError:
                pass
        self.gcs = None

    def _reconnect(self) -> None:
        """After a firmware reboot SERIAL0 connects again."""
        listener = self.ports.listener
        listener.setblocking(False)
        try:
            conn, _addr = listener.accept()
        except (BlockingIOError, InterruptedError, OSError):
            return
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.setblocking(False)
        self.gcs = conn

    # ------------------------------------------------------------------ lockstep

    def advance(self, body: Any, deadline: float, dt_world: float = 0.004) -> bool:
        """Run frames until the firmware time reaches the next world step; False when the firmware did not
        answer before ``deadline`` (wall), in which case the step is resumed by the next call."""
        target = body.fw_target + dt_world
        sock = self.ports.physics
        while self.fw_time < target - 1e-9:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return False
            sock.settimeout(remaining)
            try:
                data, addr = sock.recvfrom(1024)
            except TimeoutError:
                return False
            except OSError:
                return False
            if len(data) < FRAME.size or addr[0] != LOOPBACK:
                continue
            magic, rate, count, *pwm = FRAME.unpack_from(data, 0)
            if magic != MAGIC:
                continue
            self.peer = addr
            if self.last_count is not None and count == self.last_count:
                sock.sendto(self._out, addr)  # the reply was lost: answer the same frame again
                continue
            if self.last_count is not None and count < self.last_count:
                self.reboots += 1
                self.fw_time = 0.0
                target = dt_world
                body.firmware_rebooted()
            self.last_count = count
            self.rate = max(1, int(rate))
            dt = 1.0 / self.rate
            outputs = [min(1.0, max(0.0, (p - 1000) / 1000.0)) if p else 0.0 for p in pwm[:4]]
            body.apply(outputs, any(o > 0.0 for o in outputs), dt)
            self.fw_time += dt
            self.frames += 1
            self._out = ("\n" + body.json_state(self.fw_time) + "\n").encode()
            sock.sendto(self._out, addr)
        body.fw_target = target
        return True


__all__ = ["ArduPilotBridge", "Ports", "argv"]
