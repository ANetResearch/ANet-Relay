"""Gimbal pod (12 §5): the DYT protocol codec, the simulated pod, the real pod's link and the common face.

``dyt`` is the pure codec, ``lens`` the lens and field-of-view table, ``carrier`` the aircraft pose and the
geometry, ``sim`` the simulated pod's physics, ``link`` the real pod's TCP / serial transport and ``pod`` the
``Gimbal`` interface with ``SimPod`` and ``DytPod``. ``relay.fleet_gimbal`` mounts them on the fleet.
"""

from __future__ import annotations

from .carrier import Carrier
from .pod import ACTIONS, DytPod, Gimbal, SimPod

__all__ = ["ACTIONS", "Carrier", "DytPod", "Gimbal", "SimPod"]
