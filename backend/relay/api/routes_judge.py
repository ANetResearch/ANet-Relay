"""Challenges, duels, evaluation jobs, the Elo ladder and replay traces (WP-2b, 06 §3.2-§3.4).

Reading challenges, jobs, the ladder and traces is anonymous like the other read-only endpoints. Submitting a
program to a challenge, starting a duel and cancelling a job need the operator or an agent session; the
programs come from the program store (built-in or stored) and never leave this machine.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from . import schemas
from . import schemas_judge as sj
from .deps import caller, service

router = APIRouter()
ERRORS = {s: {"model": schemas.ErrorBody} for s in (400, 401, 403, 404, 409, 429, 503)}


def _author(request: Request) -> str:
    who = caller(request)
    if who.operator:
        return "operator"
    assert who.session is not None
    return who.session.name


@router.get("/challenges", tags=["judge"])
def list_challenges(request: Request) -> dict[str, Any]:
    """The challenge set with each challenge's best result and number of attempts."""
    return {"challenges": service(request).judge.challenges()}


@router.get("/challenges/{cid}", tags=["judge"], responses=ERRORS)
def get_challenge(request: Request, cid: str) -> dict[str, Any]:
    """One challenge: scenario, pass condition, scoring, visible seeds and the last 20 submissions."""
    return {"challenge": service(request).judge.challenge(cid)}


@router.post("/challenges/{cid}/submit", tags=["judge"], responses=ERRORS)
def submit(request: Request, cid: str, body: sj.SubmitBody) -> dict[str, Any]:
    """Queue an evaluation of a program revision on every seed; poll ``GET /jobs/{id}``."""
    author = _author(request)
    payload = body.model_dump(exclude_none=True)
    return {"job": service(request).judge.submit(cid, payload, author)}


@router.post("/duels", tags=["judge"], responses=ERRORS)
def duel(request: Request, body: sj.DuelBody) -> dict[str, Any]:
    """Queue a duel of 2-8 programs over ``rounds`` free-for-all rounds; the result moves the Elo ladder."""
    author = _author(request)
    return {"job": service(request).judge.duel(body.model_dump(exclude_none=True), author)}


@router.get("/jobs", tags=["judge"])
def list_jobs(request: Request) -> dict[str, Any]:
    return {"jobs": service(request).judge.jobs.list()}


@router.get("/jobs/{jid}", tags=["judge"], responses=ERRORS)
def get_job(request: Request, jid: str) -> dict[str, Any]:
    """``{state, progress, result}``; ``result`` is set once ``state`` is ``done``."""
    return {"job": service(request).judge.job(jid)}


@router.delete("/jobs/{jid}", tags=["judge"], responses=ERRORS)
def cancel_job(request: Request, jid: str) -> dict[str, Any]:
    """Cancel a queued or running job (its worker process is stopped); a finished job is left as it is."""
    caller(request)
    return {"job": service(request).judge.cancel(jid)}


@router.get("/ladder", tags=["judge"])
def ladder(request: Request) -> dict[str, Any]:
    """The Elo ladder of the programs that took part in duels, best first, and the last 20 duels."""
    return service(request).judge.ladder()


@router.get("/traces/{tid}", tags=["judge"], responses=ERRORS)
def trace(request: Request, tid: str) -> dict[str, Any]:
    """A replay trace: 10 Hz frames of every aircraft, shots and events (shape in ``relay.judge``)."""
    return service(request).judge.trace(tid)
