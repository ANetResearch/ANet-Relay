"""Resident memory of a program host, read from the backend (06 §2.4).

``RLIMIT_AS`` caps a host on Linux only: macOS does not enforce it and Windows has no such limit. The backend
therefore watches every host from outside (``HostClient``'s watchdog thread) and kills one whose resident set
grows past the limit. Reading from outside matters: a host stuck in one long C call (``str.join`` of a huge
list) holds its own interpreter lock, so nothing inside it could notice.

``rss(proc)`` returns bytes or ``None`` when the platform gives no answer (the watchdog then does nothing).
Linux reads ``/proc/<pid>/statm``, macOS ``proc_pidinfo(PROC_PIDTASKINFO)`` from libproc and Windows
``GetProcessMemoryInfo`` on the process handle, all through the standard library.
"""

from __future__ import annotations

import ctypes
import os
import sys
from typing import Any

_PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


def _linux(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/statm", "rb") as f:
            fields = f.read().split()
        return int(fields[1]) * _PAGE
    except (OSError, ValueError, IndexError):
        return None


class _TaskInfo(ctypes.Structure):  # struct proc_taskinfo (sys/proc_info.h), the leading fields
    _fields_ = [
        ("virtual_size", ctypes.c_uint64),
        ("resident_size", ctypes.c_uint64),
        ("rest", ctypes.c_uint8 * 80),
    ]


_libproc: Any = None


def _darwin(pid: int) -> int | None:
    global _libproc
    try:
        if _libproc is None:
            _libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        info = _TaskInfo()
        size = _libproc.proc_pidinfo(
            ctypes.c_int(pid), 4, ctypes.c_uint64(0), ctypes.byref(info), ctypes.sizeof(info)
        )  # PROC_PIDTASKINFO
        return int(info.resident_size) if size >= 16 else None
    except (OSError, AttributeError, ValueError):
        return None


class _Counters(ctypes.Structure):  # PROCESS_MEMORY_COUNTERS (psapi.h)
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("PageFaultCount", ctypes.c_uint32),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _windows(proc: Any) -> int | None:
    handle = getattr(proc, "_handle", None)
    if handle is None:
        return None
    try:
        counters = _Counters()
        counters.cb = ctypes.sizeof(counters)
        psapi = ctypes.WinDLL("psapi")  # type: ignore[attr-defined]
        if not psapi.GetProcessMemoryInfo(ctypes.c_void_p(int(handle)), ctypes.byref(counters), counters.cb):
            return None
        # committed private memory: what a runaway allocation grows, paged out or not
        return int(max(counters.WorkingSetSize, counters.PagefileUsage))
    except (OSError, AttributeError, ValueError):
        return None


def rss(proc: Any) -> int | None:
    """Resident bytes of the ``subprocess.Popen`` ``proc``, or ``None``."""
    pid = getattr(proc, "pid", None)
    if pid is None:
        return None
    if sys.platform.startswith("linux"):
        return _linux(pid)
    if sys.platform == "darwin":
        return _darwin(pid)
    if sys.platform == "win32":
        return _windows(proc)
    return None


__all__ = ["rss"]
