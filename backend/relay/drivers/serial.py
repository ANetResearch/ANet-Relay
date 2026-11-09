"""Real vehicles on a USB / UART serial port (11 §7.1): frames go to the port of their own ``SerialLink``.

The driver reads its port on every fleet tick (``tick``), so a serial aircraft needs no change to the
loop: what arrived since the last tick is parsed and handed to the fleet like UDP datagrams are. A broken
port reopens itself (``SerialLink.maintain``); the aircraft's link state follows its heartbeat as usual.
"""

from __future__ import annotations

from typing import Any

from ..mav.mavlink import Parser
from ..mav.serial_link import SerialLink
from .base import MavCommands


class SerialDriver(MavCommands):
    kind = "real"
    transport = "serial"

    def __init__(self, link: SerialLink) -> None:
        super().__init__()
        self.link = link
        self.parser = Parser()

    def tick(self, now: float) -> list[Any]:
        self.pace(now)
        data = self.link.poll()
        return self.parser.parse(data) if data else []

    def _write(self, data: bytes) -> None:
        self.link.write(data)

    def write_raw(self, data: bytes) -> bool:
        """Frames encoded elsewhere (hardware in the loop: HIL_SENSOR, HIL_GPS)."""
        return self.link.write(data)

    def close(self) -> None:
        self.link.close()
