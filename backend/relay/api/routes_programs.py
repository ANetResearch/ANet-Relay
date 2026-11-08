"""Onboard programs (WP-1, 06 §2, §3.1): the program store, validation, unit tests and deployment.

Reading programs is anonymous like the other read-only endpoints. Creating, changing, validating and testing
needs the operator or an agent session (the author of a revision is ``operator`` or the session's name).
Deploying, stopping, resuming and tuning a program on an aircraft needs the operator or the session that
controls that aircraft; allowing programs on a real aircraft is the operator's alone.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from ..errors import err
from . import schemas
from . import schemas_programs as sp
from .deps import actor, caller, require_operator, service

router = APIRouter()
ERRORS = {s: {"model": schemas.ErrorBody} for s in (400, 401, 403, 404, 409, 429, 503)}
OPERATOR = [Depends(require_operator)]


def _author(request: Request) -> str:
    who = caller(request)
    if who.operator:
        return "operator"
    assert who.session is not None
    return who.session.name


def _dump(body: Any) -> dict[str, Any]:
    return body.model_dump(exclude_none=True) if body is not None else {}


# ---------------------------------------------------------------------------- store


@router.get("/programs", tags=["programs"])
def list_programs(request: Request) -> dict[str, Any]:
    return {"programs": service(request).programs.store.list()}


@router.post("/programs", tags=["programs"], responses=ERRORS)
def create_program(request: Request, body: sp.ProgramCreateBody) -> dict[str, Any]:
    svc = service(request).programs
    program = svc.store.create(_dump(body), _author(request))
    return {"program": program, "validation": svc.validate(program["source"])}


@router.get("/programs/{pid}", tags=["programs"], responses=ERRORS)
def get_program(request: Request, pid: str) -> dict[str, Any]:
    return {"program": service(request).programs.store.get(pid)}


@router.put("/programs/{pid}", tags=["programs"], responses=ERRORS)
def update_program(request: Request, pid: str, body: sp.ProgramUpdateBody) -> dict[str, Any]:
    svc = service(request).programs
    program = svc.store.update(pid, _dump(body), _author(request))
    return {"program": program, "validation": svc.validate(program["source"])}


@router.delete("/programs/{pid}", tags=["programs"], responses=ERRORS)
def delete_program(request: Request, pid: str) -> dict[str, Any]:
    caller(request)
    return service(request).programs.store.delete(pid)


@router.get("/programs/{pid}/revisions", tags=["programs"], responses=ERRORS)
def program_revisions(request: Request, pid: str) -> dict[str, Any]:
    return {"id": pid, "revisions": service(request).programs.store.revisions(pid)}


@router.post("/programs/{pid}/validate", tags=["programs"], responses=ERRORS)
def validate_program(request: Request, pid: str, body: sp.ValidateBody | None = None) -> dict[str, Any]:
    """AST validation only (06 §2.4): every problem with line and column; nothing runs."""
    caller(request)
    svc = service(request).programs
    source = body.source if body is not None and body.source is not None else svc.store.get(pid)["source"]
    return {"id": pid, **svc.validate(source)}


@router.post("/programs/{pid}/test", tags=["programs"], responses=ERRORS)
async def test_program(request: Request, pid: str, body: sp.ProgramTestBody | None = None) -> dict[str, Any]:
    """Unit tests (06 §3.1): ``step`` once per test on fixed inputs, in a one-shot program host."""
    caller(request)
    svc = service(request).programs
    return await run_in_threadpool(svc.run_tests, pid, _dump(body))


# ---------------------------------------------------------------------------- deployment


@router.post("/vehicles/{vid}/program", tags=["programs"], responses=ERRORS)
async def deploy(request: Request, vid: str, body: sp.DeployBody) -> dict[str, Any]:
    """Deploy a program revision to an aircraft (06 §2.6); it then holds the sticks as ``PROGRAM``."""
    who = actor(request, "control")
    svc = service(request).programs
    return await run_in_threadpool(svc.deploy, vid, who, _dump(body))


@router.post("/vehicles/{vid}/program/stop", tags=["programs"], responses=ERRORS)
def stop(request: Request, vid: str) -> dict[str, Any]:
    return service(request).programs.stop(vid, actor(request, "control"))


@router.post("/vehicles/{vid}/program/resume", tags=["programs"], responses=ERRORS)
def resume(request: Request, vid: str) -> dict[str, Any]:
    """Resume a paused program; possession pauses it and release never resumes it by itself."""
    return service(request).programs.resume(vid, actor(request, "control"))


@router.patch("/vehicles/{vid}/program/params", tags=["programs"], responses=ERRORS)
def params(request: Request, vid: str, body: dict[str, Any]) -> dict[str, Any]:
    """Live parameters ``{name: value}``; type and range follow the program's PARAMS defaults."""
    if not isinstance(body, dict):
        raise err("invalid_request", reason="body must be an object")
    return service(request).programs.set_params(vid, actor(request, "control"), body)


@router.get("/vehicles/{vid}/program/log", tags=["programs"], responses=ERRORS)
def program_log(
    request: Request, vid: str, after: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=500)
) -> dict[str, Any]:
    return service(request).programs.log(vid, after, limit)


@router.post("/vehicles/{vid}/program/allow", tags=["programs"], dependencies=OPERATOR, responses=ERRORS)
def allow(request: Request, vid: str, body: sp.AllowBody) -> dict[str, Any]:
    """``Allow programs on this aircraft``: real aircraft, confirm word PROGRAM, reset when the link drops."""
    return service(request).programs.allow(vid, body.allow, body.confirm)
