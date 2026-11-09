"""Firmware processes and loopback ports of firmware twins (11 §2, §6.3).

``FirmwareProcess`` starts one SITL executable as a child of Relay in its own process group, keeps the last
lines of its output, and kills the whole group on ``stop``. Every live process is registered; ``stop_all``
runs at interpreter exit and from the fleet's shutdown, so no firmware outlives Relay.

``Instances`` hands out instance numbers ``n`` (0, 1, ...): a twin's ports derive from ``n`` (11 §2), and an
instance is used only when Relay can open its own loopback sockets for it and the ports the firmware binds
are free. ``bound_sockets`` lists the sockets a process holds (Linux ``/proc``), for the network-boundary
tests.
"""

from __future__ import annotations

import atexit
import collections
import contextlib
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

log = logging.getLogger("relay.twin")

LOOPBACK = "127.0.0.1"
TAIL_LINES = 40
MAX_INSTANCES = 64
_live: set[FirmwareProcess] = set()
_live_lock = threading.Lock()


class TwinStartError(RuntimeError):
    """The firmware did not come up; ``lines`` is the end of its output."""

    def __init__(self, reason: str, lines: list[str] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.lines = list(lines or [])


def workdir() -> Path:
    """A fresh working directory under the system temp directory, with no spaces in its path (PX4's ``rcS``
    expands it unquoted)."""
    base = Path(tempfile.gettempdir())
    if " " in str(base) and os.name != "nt":
        base = Path("/tmp")
    return Path(tempfile.mkdtemp(prefix="relay-twin-", dir=base))


class FirmwareProcess:
    def __init__(
        self, argv: list[str], cwd: Path, env: dict[str, str] | None = None, label: str = ""
    ) -> None:
        self.argv = [str(a) for a in argv]
        self.cwd = cwd
        self.env = env
        self.label = label or Path(self.argv[0]).name
        self.lines: collections.deque[str] = collections.deque(maxlen=400)
        self.proc: subprocess.Popen[bytes] | None = None
        self.stopping = False
        self._reader: threading.Thread | None = None

    def start(self) -> None:
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            kwargs["start_new_session"] = True
        self.proc = subprocess.Popen(
            self.argv,
            cwd=str(self.cwd),
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            **kwargs,
        )
        with _live_lock:
            _live.add(self)
        self._reader = threading.Thread(target=self._read, name=f"twin-out-{self.label}", daemon=True)
        self._reader.start()

    def _read(self) -> None:
        proc = self.proc
        assert proc is not None and proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self.lines.append(line)
        proc.stdout.close()

    @property
    def pid(self) -> int | None:
        return None if self.proc is None else self.proc.pid

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def returncode(self) -> int | None:
        return None if self.proc is None else self.proc.poll()

    def tail(self, n: int = TAIL_LINES) -> list[str]:
        return list(self.lines)[-n:]

    def stop(self, timeout: float = 3.0) -> None:
        """Kill the process group and wait for it."""
        self.stopping = True
        proc = self.proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                if os.name == "nt":
                    proc.kill()
                else:
                    os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                with contextlib.suppress(OSError):
                    proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout)
        if os.name != "nt":
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(proc.pid, signal.SIGKILL)  # PX4's client helpers of the same group
        if self._reader is not None:
            self._reader.join(timeout=1.0)
        with _live_lock:
            _live.discard(self)


def live() -> list[FirmwareProcess]:
    with _live_lock:
        return list(_live)


def stop_all() -> None:
    """Kill every firmware process Relay started (interpreter exit, fleet shutdown)."""
    for proc in live():
        try:
            proc.stop(timeout=1.0)
        except Exception:  # pragma: no cover - best effort at exit
            log.exception("stopping %s failed", proc.label)


atexit.register(stop_all)


def remove_tree(path: Path | None) -> None:
    if path is not None:
        shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------------------------- ports


def udp_free(port: int, host: str = LOOPBACK) -> bool:
    """True when a UDP ``port`` can be bound on ``host`` now (the firmware binds it itself)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def tcp_listener(port: int) -> socket.socket:
    """A TCP socket listening on 127.0.0.1:``port`` (raises OSError when taken)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name != "nt":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # TIME_WAIT of a previous twin
        s.bind((LOOPBACK, port))
        s.listen(1)
    except OSError:
        s.close()
        raise
    return s


def udp_socket(port: int) -> socket.socket:
    """A UDP socket bound to 127.0.0.1:``port`` (raises OSError when taken)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind((LOOPBACK, port))
    except OSError:
        s.close()
        raise
    return s


def px4_lock_free(instance: int) -> bool:
    """PX4 SITL refuses to start an instance whose server lock another PX4 holds."""
    if os.name == "nt":
        return True
    path = Path(tempfile.gettempdir()) / f"px4_lock-{instance}"
    if not path.exists():
        path = Path("/tmp") / f"px4_lock-{instance}"
        if not path.exists():
            return True
    try:
        import fcntl

        fd = os.open(str(path), os.O_RDONLY)
    except (OSError, ImportError):
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


class Instances:
    """Instance numbers of the running twins of this Relay."""

    def __init__(self) -> None:
        self._used: set[int] = set()
        self._lock = threading.Lock()

    def acquire(self, opener: Callable[[int], Any]) -> tuple[int, Any]:
        """The first free instance ``n`` for which ``opener(n)`` succeeds (it opens Relay's sockets and checks
        the firmware's ports, raising OSError when one is taken); returns ``(n, opener's result)``."""
        with self._lock:
            last: OSError | None = None
            for n in range(MAX_INSTANCES):
                if n in self._used:
                    continue
                try:
                    result = opener(n)
                except OSError as exc:
                    last = exc
                    continue
                self._used.add(n)
                return n, result
        raise OSError(f"no free twin instance ({last})")

    def release(self, n: int) -> None:
        with self._lock:
            self._used.discard(n)

    @property
    def used(self) -> set[int]:
        with self._lock:
            return set(self._used)


INSTANCES = Instances()


# ---------------------------------------------------------------------------------------------- inspection


def bound_sockets(pid: int) -> list[tuple[str, str, int, str]]:
    """``(protocol, local address, local port, state)`` of every IPv4 / IPv6 socket of process ``pid``
    (Linux only: ``/proc/<pid>/fd`` and ``/proc/<pid>/net``). State is the kernel's hex code (``0A`` listen,
    ``07`` an unconnected UDP socket)."""
    if not sys.platform.startswith("linux"):
        return []
    inodes = set()
    fd_dir = Path(f"/proc/{pid}/fd")
    for fd in fd_dir.iterdir() if fd_dir.is_dir() else []:
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if target.startswith("socket:["):
            inodes.add(target[8:-1])
    out = []
    for proto in ("tcp", "udp", "tcp6", "udp6"):
        try:
            rows = Path(f"/proc/{pid}/net/{proto}").read_text().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            cols = row.split()
            if len(cols) > 9 and cols[9] in inodes:
                address, port = cols[1].rsplit(":", 1)
                out.append((proto, _address(address), int(port, 16), cols[3]))
    return out


def _address(hexaddr: str) -> str:
    raw = bytes.fromhex(hexaddr)
    if len(raw) == 4:
        return socket.inet_ntop(socket.AF_INET, raw[::-1])
    words = b"".join(raw[i : i + 4][::-1] for i in range(0, 16, 4))
    return socket.inet_ntop(socket.AF_INET6, words)


def wait_for(predicate: Callable[[], bool], timeout: float, step: float = 0.01) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


def descendants(pid: int) -> Iterable[int]:  # pragma: no cover - diagnostics on Linux
    """Process ids in the process group of ``pid`` (Linux ``/proc``)."""
    if not sys.platform.startswith("linux"):
        return []
    out = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
        except OSError:
            continue
        fields = stat.rsplit(")", 1)[-1].split()
        if len(fields) > 2 and int(fields[2]) == pid:
            out.append(int(entry.name))
    return out


__all__ = [
    "INSTANCES",
    "FirmwareProcess",
    "TwinStartError",
    "bound_sockets",
    "stop_all",
    "tcp_listener",
    "udp_free",
    "udp_socket",
    "workdir",
]
