"""Dogfight scenario, matches and the leaderboard (WP-B, 07 §3-§4).

Starting or stopping a dogfight and starting or ending a match are application operations (04 §3): the
operator, or an agent session while ``agents_app_control`` is on. Reading them is anonymous, like the arena
view.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request

from ..errors import err
from . import schemas
from .deps import actor, require_app_control, service
from .schemas_match import DogfightBody, DogfightView, Leaderboard, MatchBody, MatchView

router = APIRouter()
ERRORS = {s: {"model": schemas.ErrorBody} for s in (400, 401, 403, 404, 409, 429, 503)}
APP = [Depends(require_app_control)]


@router.post(
    "/scenarios/dogfight", response_model=DogfightView, tags=["arena"], dependencies=APP, responses=ERRORS
)
def dogfight_start(request: Request, body: DogfightBody | None = None) -> dict[str, Any]:
    """Start a dogfight on 2-8 simulators: spawn ring, airframe, roles and their programs, arena (the LAN
    match or local practice), a timer. Programs fly with sticks only; a person can possess any of them.
    """
    body = body or DogfightBody()
    raw = body.model_dump(exclude_none=True)
    return service(request).dogfights.start(raw, actor(request, "other"))


@router.get("/scenarios/dogfight", response_model=DogfightView, tags=["arena"])
def dogfight_status(request: Request) -> dict[str, Any]:
    return service(request).dogfights.view()


@router.delete(
    "/scenarios/dogfight", response_model=DogfightView, tags=["arena"], dependencies=APP, responses=ERRORS
)
def dogfight_stop(request: Request) -> dict[str, Any]:
    """Stop the dogfight: programs stop, airborne aircraft land, the board keeps the standings."""
    return service(request).dogfights.stop(actor(request, "other"))


@router.post("/arena/match", response_model=MatchView, tags=["arena"], dependencies=APP, responses=ERRORS)
def match_start(request: Request, body: MatchBody | None = None) -> dict[str, Any]:
    """Start a match of ``duration_s`` here and, on the LAN, on every version 2 terminal (MATCH)."""
    body = body or MatchBody()
    svc = service(request)

    def run() -> dict[str, Any]:
        arena = svc.arena
        if not arena.enabled:
            raise err("arena_off")
        board = arena.board
        if board.match is not None and board.match.running:
            raise err("match_running")
        now = svc.clock()
        board.start(body.duration_s, now, broadcast=arena.on_lan)
        return {"match": board.match_view(now), "ended": False}

    return svc.call(run)


@router.delete("/arena/match", response_model=MatchView, tags=["arena"], dependencies=APP, responses=ERRORS)
def match_end(request: Request) -> dict[str, Any]:
    """End the running match (everywhere on the LAN); without one, reset the accumulated statistics."""
    svc = service(request)

    def run() -> dict[str, Any]:
        arena = svc.arena
        now = svc.clock()
        ended = arena.board.end(now, broadcast=arena.on_lan)
        return {"match": arena.board.match_view(now), "ended": ended}

    return svc.call(run)


@router.get("/arena/leaderboard", response_model=Leaderboard, tags=["arena"])
def leaderboard(
    request: Request, by: Literal["downs", "survival", "accuracy", "rams"] = "downs"
) -> dict[str, Any]:
    """This terminal's aircraft and every peer's, sorted by ``by``; a 0.4 peer's rows have no statistics."""
    svc = service(request)
    return svc.call(lambda: svc.arena.board.leaderboard(by))
