"""Gimbal pod (12 §5.2).

Reads are anonymous. ``POST /vehicles/{id}/gimbal`` is for the operator or the agent session that controls the
aircraft (possession or a live claim), at any flight level. Attaching and detaching a pod are the operator's.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from . import schemas
from . import schemas_gimbal as sg
from .deps import actor, require_operator, service

router = APIRouter()
ERRORS = {s: {"model": schemas.ErrorBody} for s in (400, 401, 403, 404, 409, 429, 503)}
OPERATOR = [Depends(require_operator)]


@router.get("/vehicles/{vid}/gimbal", tags=["gimbal"], response_model=sg.GimbalView, responses=ERRORS)
def gimbal_view(request: Request, vid: str) -> dict[str, Any]:
    """The aircraft's gimbal pod; 404 ``gimbal_missing`` when it has none."""
    return service(request).gimbal_view(vid)


@router.post("/vehicles/{vid}/gimbal", tags=["gimbal"], response_model=sg.GimbalResult, responses=ERRORS)
def gimbal_command(request: Request, vid: str, body: sg.GimbalBody) -> dict[str, Any]:
    """Run a pod action: rate, stop, angle, center, follow, lock, motors, zoom, zoom_rate, digital_zoom, lens,
    palette, photo, record, track, track_stop, laser, focus, enhance, osd, low_light, scan, scan_stop,
    geo_track, look_at. Returns the pod's view right after."""
    return service(request).gimbal_command(vid, body.action, body.params(), actor(request, "control"))


@router.post(
    "/vehicles/{vid}/gimbal/attach",
    tags=["gimbal"],
    dependencies=OPERATOR,
    response_model=sg.GimbalResult,
    responses=ERRORS,
)
def gimbal_attach(request: Request, vid: str, body: sg.GimbalAttachBody) -> dict[str, Any]:
    """Attach a pod: ``tcp`` (host, port 2000) or ``serial`` (device, baud 115200) for a real aircraft,
    ``sim`` for a simulator."""
    return service(request).gimbal_attach(vid, body.model_dump(exclude_none=True), actor(request, "control"))


@router.delete(
    "/vehicles/{vid}/gimbal",
    tags=["gimbal"],
    dependencies=OPERATOR,
    response_model=sg.GimbalDetached,
    responses=ERRORS,
)
def gimbal_detach(request: Request, vid: str) -> dict[str, Any]:
    """Detach the pod and close its link."""
    return service(request).gimbal_detach(vid, actor(request, "control"))


@router.get(
    "/vehicles/{vid}/gimbal/captures", tags=["gimbal"], response_model=sg.GimbalCaptures, responses=ERRORS
)
def gimbal_captures(request: Request, vid: str, limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    """Photos and recording changes, newest last, each with the aircraft's pose, the gimbal angles, lens,
    zoom, laser range and target."""
    return service(request).gimbal_captures(vid, limit)
