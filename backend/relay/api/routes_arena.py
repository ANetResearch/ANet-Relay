"""LAN arena (05 §7, WP-D): the read-only arena view and the agents' fire input."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from . import schemas
from .deps import actor, service

router = APIRouter()
ERRORS = {s: {"model": schemas.ErrorBody} for s in (400, 401, 403, 404, 409, 429, 503)}


@router.get("/arena", response_model=schemas.ArenaView, tags=["arena"])
def arena(request: Request) -> dict[str, Any]:
    """Peers, their simulators and the bullets in flight, in this terminal's local frame (05 §7.5)."""
    return service(request).arena_snapshot()


@router.post("/vehicles/{vid}/fire", tags=["arena"], responses=ERRORS)
def fire(request: Request, vid: str, body: schemas.FireBody | None = None) -> dict[str, Any]:
    """Hold the trigger of a simulator for ``seconds`` (at most 3): 8 rounds per second, 24 per magazine.

    The controller only; simulated aircraft only (409 ``sim_only``), armed and airborne, while this terminal
    is in the arena (409 ``arena_off``).
    """
    body = body or schemas.FireBody()
    on = body.seconds > 0
    return service(request).fire(vid, actor(request, "control"), on, body.seconds)
