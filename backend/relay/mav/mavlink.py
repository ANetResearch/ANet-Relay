"""The MAVLink 2 dialect used everywhere, plus a small encoder/parser pair.

Relay, the simulator and the fake vehicle all encode and parse genuine wire
bytes with pymavlink, so the simulated path is the real path.
"""

from __future__ import annotations

import os

os.environ.setdefault("MAVLINK20", "1")

from pymavlink.dialects.v20 import ardupilotmega as M

__all__ = ["Encoder", "M", "Parser"]


class _Capture:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(bytes(data))


class Encoder:
    """Encodes message objects into MAVLink 2 frames with a running sequence number."""

    def __init__(self, sysid: int, compid: int) -> None:
        self._capture = _Capture()
        self.mav = M.MAVLink(self._capture, srcSystem=sysid, srcComponent=compid)

    def encode(self, msg) -> bytes:
        self.mav.send(msg)
        data = b"".join(self._capture.chunks)
        self._capture.chunks.clear()
        return data


class Parser:
    """Robust incremental parser; one instance per remote endpoint."""

    def __init__(self) -> None:
        self.mav = M.MAVLink(None)
        self.mav.robust_parsing = True

    def parse(self, data: bytes) -> list:
        try:
            msgs = self.mav.parse_buffer(data) or []
        except Exception:  # pymavlink raises on corrupt frames despite robust_parsing in some paths
            return []
        return [m for m in msgs if m.get_type() != "BAD_DATA"]
