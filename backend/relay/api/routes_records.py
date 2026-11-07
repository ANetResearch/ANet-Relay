"""Flight records (03 §11) and bridge provisioning (03 §9)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from ..errors import err
from . import schemas
from .deps import require_app_control, service

router = APIRouter()
ERRORS = {s: {"model": schemas.ErrorBody} for s in (400, 401, 403, 404, 409, 503)}


@router.get("/history/sessions", tags=["records"])
def history_sessions(
    request: Request,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    vehicle: str | None = None,
) -> dict[str, Any]:
    return {"sessions": service(request).history.list_sessions(limit, offset, vehicle)}


@router.get("/history/sessions/{sid}", tags=["records"], responses=ERRORS)
def history_session(request: Request, sid: int) -> dict[str, Any]:
    detail = service(request).history.session_detail(sid)
    if detail is None:
        raise err("record_not_found", record=sid)
    return detail


@router.get("/history/sessions/{sid}/export", tags=["records"], responses=ERRORS)
def history_export(request: Request, sid: int, fmt: str = Query("csv", pattern="^(csv|json)$")) -> Response:
    result = service(request).history.export(sid, fmt)
    if result is None:
        raise err("record_not_found", record=sid)
    content_type, body = result
    filename = f"anet-relay-record-{sid}.{fmt}"
    return Response(
        content=body,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/history/sessions/{sid}/refly", tags=["records"], responses=ERRORS)
def history_refly(request: Request, sid: int) -> dict[str, Any]:
    """How a record can be flown again: plan, path or recorded sticks (04 §4)."""
    return service(request).refly_options(sid)


@router.get("/bridges/{ip}", tags=["network"], dependencies=[Depends(require_app_control)], responses=ERRORS)
def bridge_read(request: Request, ip: str, port: int = Query(80, ge=1, le=65535)) -> dict[str, Any]:
    """Read a bridge's current settings (passwords are never returned)."""
    return service(request).bridge_read(ip, port)


@router.post(
    "/bridges/{ip}/configure", tags=["network"], dependencies=[Depends(require_app_control)], responses=ERRORS
)
def bridge_configure(
    request: Request, ip: str, body: schemas.BridgeConfigureBody, port: int = Query(80, ge=1, le=65535)
) -> dict[str, Any]:
    """Write settings and restart the bridge. The password is sent to the bridge only."""
    return service(request).bridge_configure(ip, port, body.model_dump(exclude_none=True))
