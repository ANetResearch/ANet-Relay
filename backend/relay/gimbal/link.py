"""The real pod's transport (12 §5.3): a TCP client or a serial port on one background thread.

``DytLink`` connects to the pod's network control port (TCP, default 2000, every frame wrapped as
``EB 90 N <data> <checksum>``) or opens a serial port (115200 8N1, the plain protocol), reads the pod's
stream through ``dyt.Parser`` and queues the decoded frames; the fleet thread takes them with ``poll()`` and
queues frames to send with ``send()``. Neither call blocks: the socket or the port is only ever touched by the
link thread, which reconnects with a growing back-off (0.25 s doubling to 5 s) after any error and ends within
a fraction of a second of ``close()``.

``state``: ``ok`` while a telemetry frame arrived within ``LOST_S`` seconds, ``connecting`` until the first
one (or for ``CONNECT_GRACE_S`` after the start), ``lost`` after that without telemetry. The times are the
link's own monotonic clock, independent of the fleet's.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections import deque
from typing import Any

from . import dyt

log = logging.getLogger("relay.gimbal")

LOST_S = 1.0  # no valid telemetry for this long: the link is lost
CONNECT_GRACE_S = 3.0  # a pod that never answered counts as lost after this long
INBOX_MAX = 2048  # frames; the oldest go first if the fleet thread stalls
OUTBOX_MAX = 256
TCP_PORT = 2000
SERIAL_BAUD = 115200
BACKOFF_FIRST_S = 0.25
BACKOFF_MAX_S = 5.0
READ_S = 0.05


class _Tcp:
    def __init__(self, host: str, port: int) -> None:
        self.sock = socket.create_connection((host, port), timeout=2.0)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(READ_S)

    def read(self) -> bytes:
        try:
            data = self.sock.recv(4096)
        except TimeoutError:
            return b""
        if not data:
            raise ConnectionError("the pod closed the connection")
        return data

    def write(self, data: bytes) -> None:
        self.sock.settimeout(1.0)
        try:
            self.sock.sendall(data)
        finally:
            self.sock.settimeout(READ_S)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class _Serial:
    def __init__(self, device: str, baud: int, opener: Any) -> None:
        self.port = opener(device, baud)

    def read(self) -> bytes:
        data = self.port.read(4096)
        if not data:
            time.sleep(0.005)
        return bytes(data)

    def write(self, data: bytes) -> None:
        self.port.write(data)

    def close(self) -> None:
        try:
            self.port.close()
        except Exception:
            pass


class DytLink:
    def __init__(
        self,
        transport: str,
        *,
        host: str | None = None,
        port: int = TCP_PORT,
        device: str | None = None,
        baud: int = SERIAL_BAUD,
        serial_opener: Any = None,
    ) -> None:
        if transport not in ("tcp", "serial"):
            raise ValueError("transport must be tcp or serial")
        self.transport = transport
        self.host, self.port = host, port
        self.device, self.baud = device, baud
        self._serial_opener = serial_opener
        self.parser = dyt.Parser()
        self._inbox: deque[Any] = deque(maxlen=INBOX_MAX)
        self._outbox: deque[bytes] = deque(maxlen=OUTBOX_MAX)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._started = time.monotonic()
        self._last_valid: float | None = None
        self._connected = False
        self.error: str | None = None
        self.connects = 0
        self.rx_bytes = 0
        self.tx_frames = 0

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}" if self.transport == "tcp" else str(self.device)

    # ------------------------------------------------------------------ fleet-thread side

    def start(self) -> None:
        if self._thread is not None:
            return
        self._started = time.monotonic()
        self._thread = threading.Thread(target=self._run, name=f"relay-gimbal-{self.address}", daemon=True)
        self._thread.start()

    def close(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        self._thread = None

    def poll(self) -> list[Any]:
        """Decoded frames received since the last call, oldest first."""
        out = []
        while True:
            try:
                out.append(self._inbox.popleft())
            except IndexError:
                return out

    def send(self, frame: bytes) -> None:
        """Queue one plain protocol frame (the link wraps it for TCP). Never blocks."""
        self._outbox.append(bytes(frame))

    @property
    def state(self) -> str:
        now = time.monotonic()
        last = self._last_valid
        if last is not None:
            return "ok" if now - last < LOST_S else "lost"
        return "connecting" if now - self._started < CONNECT_GRACE_S else "lost"

    @property
    def age_s(self) -> float | None:
        last = self._last_valid
        return None if last is None else max(0.0, time.monotonic() - last)

    @property
    def connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------ link thread

    def _open(self) -> Any:
        if self.transport == "tcp":
            return _Tcp(str(self.host), int(self.port))
        opener = self._serial_opener
        if opener is None:
            from ..mav import serial_link

            opener = serial_link.open_port
        return _Serial(str(self.device), self.baud, opener)

    def _run(self) -> None:
        backoff = BACKOFF_FIRST_S
        while not self._stop.is_set():
            try:
                io = self._open()
            except Exception as exc:  # unreachable host, busy port ...: back off and try again
                self.error = str(exc)
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2.0, BACKOFF_MAX_S)
                continue
            backoff = BACKOFF_FIRST_S
            self.connects += 1
            self.error = None
            self._connected = True
            self.parser = dyt.Parser()
            try:
                self._serve(io)
            except Exception as exc:
                self.error = str(exc)
                log.info("gimbal link %s dropped: %s", self.address, exc)
            finally:
                self._connected = False
                io.close()
            if self._stop.wait(backoff):
                break

    def _serve(self, io: Any) -> None:
        while not self._stop.is_set():
            while self._outbox:
                frame = self._outbox.popleft()
                io.write(dyt.wrap(frame) if self.transport == "tcp" else frame)
                self.tx_frames += 1
            data = io.read()
            if not data:
                continue
            self.rx_bytes += len(data)
            for frame in self.parser.feed(data):
                if isinstance(frame, dyt.Telemetry):
                    self._last_valid = time.monotonic()
                self._inbox.append(frame)
