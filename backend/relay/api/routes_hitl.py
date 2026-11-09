"""Serial ports and PX4 hardware in the loop (11 §7, §10).

Authority: the port list and the status are anonymous reads. ``POST /vehicles/{id}/hitl`` is an application
operation (the operator, or an agent session while app control is on); ``start`` also needs the operator to
possess the aircraft and the word ``HITL`` (propellers removed). Connecting a serial port goes through
``POST /vehicles`` with ``serial`` (``routes_control``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from . import schemas
from . import schemas_hitl as sh
from .deps import actor, require_app_control, service

router = APIRouter()
ERRORS = {s: {"model": schemas.ErrorBody} for s in (400, 401, 403, 404, 409, 429, 503)}
APP = [Depends(require_app_control)]


@router.get("/serial/ports", tags=["fleet"], response_model=sh.SerialPorts)
def serial_ports(request: Request) -> dict[str, Any]:
    """The serial ports of this computer; flight controller USB interfaces are marked ``autopilot``."""
    return service(request).serial_ports()


@router.get("/vehicles/{vid}/hitl", tags=["fleet"], response_model=sh.HitlStatus, responses=ERRORS)
def hitl_status(request: Request, vid: str) -> dict[str, Any]:
    """Hardware-in-the-loop availability (and why not), the flow's steps, the backup and the run."""
    return service(request).hitl_status(vid)


@router.post(
    "/vehicles/{vid}/hitl", tags=["fleet"], dependencies=APP, response_model=sh.HitlStatus, responses=ERRORS
)
def hitl_action(request: Request, vid: str, body: sh.HitlBody) -> dict[str, Any]:
    """``start`` (confirm ``HITL``): back up, write SYS_HITL and SYS_AUTOSTART, reboot, run. ``stop``: restore
    the parameters, reboot and verify; also runs a restore owed from an earlier session."""
    return service(request).hitl_action(vid, body.action, body.confirm, actor(request, "control"))
