"""ANet Relay backend: ground control for agent swarms."""

import os

# pymavlink's mavutil loads the "all" dialect on import unless told otherwise. Relay speaks ardupilotmega
# (relay.mav.mavlink); the frozen backend ships only the dialects it names (relay-backend.spec).
os.environ.setdefault("MAVLINK_DIALECT", "ardupilotmega")
os.environ.setdefault("MAVLINK20", "1")

from .config import VERSION

__all__ = ["VERSION"]
