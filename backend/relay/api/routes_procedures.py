"""Bench procedures and their reports (WP-B, 05 §4.4)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from . import schemas
from . import schemas_procedures as sp
from .deps import actor, service

router = APIRouter(tags=["procedures"])
ERRORS = {s: {"model": schemas.ErrorBody} for s in (400, 401, 403, 404, 409, 429, 503)}


@router.get("/procedures", response_model=sp.ProcedureCatalogue)
def catalogue(request: Request) -> dict[str, Any]:
    """The procedures with their steps and a rough duration."""
    return service(request).procedure_catalogue()


@router.post("/vehicles/{vid}/procedure", response_model=sp.ProcedureRun, responses=ERRORS)
def start(request: Request, vid: str, body: sp.ProcedureStartBody) -> dict[str, Any]:
    """Start a procedure. Needs the controller (possession or claim); 409 procedure_running if one runs."""
    options = body.options.model_dump(exclude_none=True) if body.options else None
    return service(request).procedure_start(vid, actor(request, "control"), body.procedure, options)


@router.get("/vehicles/{vid}/procedure", response_model=sp.ProcedureRun, responses=ERRORS)
def view(request: Request, vid: str) -> dict[str, Any]:
    """The current or most recent run of this aircraft, with every step's state, metrics and prompt."""
    return service(request).procedure_view(vid)


@router.post("/vehicles/{vid}/procedure/answer", response_model=sp.ProcedureRun, responses=ERRORS)
def answer(request: Request, vid: str, body: sp.ProcedureAnswerBody) -> dict[str, Any]:
    """Answer the waiting step. Operator-only prompts on a real aircraft need the operator."""
    return service(request).procedure_answer(
        vid, actor(request, "control"), body.step, body.answer, body.value, body.confirm
    )


@router.post("/vehicles/{vid}/procedure/abort", response_model=sp.ProcedureRun, responses=ERRORS)
def abort(request: Request, vid: str) -> dict[str, Any]:
    """Abort: a flying step lands, a ground step stops the motors."""
    return service(request).procedure_abort(vid, actor(request, "control"))


@router.get("/test-runs", response_model=sp.TestRuns)
def test_runs(
    request: Request, vehicle: str | None = None, limit: int = Query(50, ge=1, le=500)
) -> dict[str, Any]:
    """Bench reports, newest first; ``vehicle`` matches the aircraft id or its address."""
    return service(request).test_runs(vehicle, limit)


@router.get("/test-runs/{run_id}", response_model=sp.ProcedureRun, responses=ERRORS)
def test_run(request: Request, run_id: int) -> dict[str, Any]:
    return service(request).test_run(run_id)


@router.get("/test-runs/{run_id}/export", responses=ERRORS)
def test_run_export(
    request: Request, run_id: int, fmt: str = Query("json", pattern="^(json|md)$")
) -> Response:
    """The report as JSON, or as Markdown with bilingual tables."""
    content_type, body = service(request).test_run_export(run_id, fmt)
    filename = f"anet-relay-bench-{run_id}.{fmt}"
    return Response(
        content=body,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
