"""Simulated vehicles: frames go straight into the in-process simulated autopilot.

No socket is ever opened here (03 §6.1 H7): a simulated aircraft cannot reach
the network. Bytes still pass through a real MAVLink encoder and parser.
"""

from __future__ import annotations

from typing import Any

from ..mav.mavlink import Parser
from ..sim.autopilot import SimAutopilot
from .base import MavCommands


class SimDriver(MavCommands):
    kind = "sim"

    def __init__(self, autopilot: SimAutopilot) -> None:
        super().__init__()
        self.sim = autopilot
        self.parser = Parser()
        self._now = 0.0

    def tick(self, now: float) -> list[Any]:
        self._now = now
        self.pace(now)
        return self.parser.parse(self.sim.step(now))

    def _write(self, data: bytes) -> None:
        self.sim.receive(data, self._now)

    def reset(self) -> None:
        self.sim.reset()
