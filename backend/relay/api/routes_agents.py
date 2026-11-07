"""Agent sessions and leases (03 §4, §10.1)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from . import schemas
from .deps import require_session, service

router = APIRouter()
ERRORS = {s: {"model": schemas.ErrorBody} for s in (400, 401, 403, 429)}


@router.post("/sessions", response_model=schemas.SessionOpened, tags=["agents"], responses=ERRORS)
def open_session(request: Request, body: schemas.SessionBody) -> dict[str, Any]:
    """Open an agent session. Keep it alive with /sessions/heartbeat at least once per lease."""
    return service(request).open_session(body.name)


@router.post("/sessions/heartbeat", tags=["agents"], responses=ERRORS)
def heartbeat(request: Request) -> dict[str, Any]:
    session = require_session(request)
    return service(request).renew_session(session)


@router.delete("/sessions", tags=["agents"], responses=ERRORS)
def close(request: Request) -> dict[str, Any]:
    session = require_session(request)
    return service(request).close_session(session)


@router.get("/sessions", tags=["agents"])
def sessions(request: Request) -> dict[str, Any]:
    return {"sessions": service(request).session_views()}
