"""Real vehicles: frames go to the bridge over the shared UDP socket."""

from __future__ import annotations

from collections import deque
from typing import Any

from ..mav.link import UdpLink
from .base import MavCommands


class MavlinkDriver(MavCommands):
    kind = "real"

    def __init__(self, link: UdpLink, host: str, port: int) -> None:
        super().__init__()
        self.link = link
        self.host = host
        self.port = port
        self._inbox: deque[Any] = deque()

    def feed(self, msgs: list[Any]) -> None:
        self._inbox.extend(msgs)

    def tick(self, now: float) -> list[Any]:
        self.pace(now)
        out = list(self._inbox)
        self._inbox.clear()
        return out

    def _write(self, data: bytes) -> None:
        self.link.sendto(data, (self.host, self.port))
