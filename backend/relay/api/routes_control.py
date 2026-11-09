"""Fleet management, authority and control (03 §10.1)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from .. import flight_level
from ..errors import err
from . import schemas
from .deps import actor, require_app_control, require_operator, service

router = APIRouter()
ERRORS = {s: {"model": schemas.ErrorBody} for s in (400, 401, 403, 404, 409, 429, 503)}
OPERATOR = [Depends(require_operator)]
# Fleet and network operations an agent may run when the operator allows app control (04 §3). Possession and
# the operator's own attestations (stick directions, propellers) stay with the person at the window.
APP = [Depends(require_app_control)]


# ---------------------------------------------------------------------------- fleet management


@router.post("/discover", tags=["network"], dependencies=APP, responses=ERRORS)
def discover(request: Request, body: schemas.DiscoverBody | None = None) -> dict[str, Any]:
    body = body or schemas.DiscoverBody()
    return service(request).discover(body.networks, body.http_port)


@router.post("/vehicles", tags=["fleet"], dependencies=APP, responses=ERRORS)
def connect(request: Request, body: schemas.ConnectBody) -> dict[str, Any]:
    return service(request).connect(body.host, body.port)


@router.delete("/vehicles/{vid}", tags=["fleet"], dependencies=APP, responses=ERRORS)
def remove(request: Request, vid: str) -> dict[str, Any]:
    return service(request).remove_vehicle(vid)


@router.post("/vehicles/{vid}/label", tags=["fleet"], dependencies=APP, responses=ERRORS)
def label(request: Request, vid: str, body: schemas.LabelBody) -> dict[str, Any]:
    return service(request).set_label(vid, body.label)


@router.post("/vehicles/{vid}/calibration", tags=["fleet"], dependencies=OPERATOR, responses=ERRORS)
def calibration(request: Request, vid: str, body: schemas.CalibrationBody) -> dict[str, Any]:
    return service(request).set_calibration(vid, body.signs, body.hover)


@router.post("/vehicles/{vid}/confirm", tags=["fleet"], dependencies=OPERATOR, responses=ERRORS)
def confirm(request: Request, vid: str, body: schemas.ConfirmBody) -> dict[str, Any]:
    return service(request).confirm(vid, body.item)


@router.post("/sims", tags=["sims"], dependencies=APP, responses=ERRORS)
def add_sims(request: Request, body: schemas.SimsBody | None = None) -> dict[str, Any]:
    body = body or schemas.SimsBody()
    airframe = body.airframe.model_dump(exclude_none=True) if body.airframe else None  # WP-2a
    # WP-1 (06 §5): a session at the sticks level cannot ask for hover assist on a new simulator either
    if body.hover_assist:
        flight_level.check_sim_assist(service(request).settings, actor(request, "other"), True)
    return {
        "added": service(request).add_sims(
            body.count,
            body.autopilot or "ardupilot",
            body.position,
            body.hover_assist,
            airframe,
            engine=body.engine,  # 11 §10: firmware twins
            profile=body.profile,
            pack=body.pack,
            frame=body.frame.model_dump(exclude_none=True) if body.frame else None,
            home=body.home.model_dump() if body.home else None,
            autopilot_given=body.autopilot is not None,
        )
    }


@router.delete("/sims", tags=["sims"], dependencies=APP, responses=ERRORS)
def remove_sims(request: Request, id: list[str] | None = Query(None)) -> dict[str, Any]:
    return {"removed": service(request).remove_sims(id)}


@router.post("/sims/{vid}/reset", tags=["sims"], dependencies=APP, responses=ERRORS)
def reset_sim(request: Request, vid: str) -> dict[str, Any]:
    return service(request).reset_sim(vid)


@router.post("/sims/{vid}/assist", tags=["sims"], responses=ERRORS)
def sim_assist(request: Request, vid: str, body: schemas.SimAssistBody) -> dict[str, Any]:
    """Hover assist on or off (05 §5.2): the operator or the controller; allowed armed and in flight."""
    return service(request).set_sim_assist(vid, body.on, actor(request, "control"), app_control=True)


# WP-2a airframe defects (06 §4): the operator or the controller (an agent also on an uncontrolled simulator
# while app control is on), the same rule as the hover-assist switch.
@router.put("/sims/{vid}/airframe", tags=["sims"], response_model=schemas.SimAirframe, responses=ERRORS)
def set_sim_airframe(request: Request, vid: str, body: schemas.AirframeBody) -> dict[str, Any]:
    """Set the airframe defects: ``{preset | params, seed?}``; allowed armed and in flight."""
    spec = body.model_dump(exclude_none=True)
    if not spec.get("preset") and "params" not in spec and "seed" not in spec:
        raise err("invalid_request", reason="airframe needs preset or params")
    return service(request).set_sim_airframe(vid, spec, actor(request, "control"), app_control=True)


@router.get("/sims/{vid}/airframe", tags=["sims"], response_model=schemas.SimAirframe, responses=ERRORS)
def get_sim_airframe(request: Request, vid: str) -> dict[str, Any]:
    """The full airframe values; programs never see them (06 §4)."""
    return service(request).get_sim_airframe(vid, actor(request, "other"), app_control=True)


# ---------------------------------------------------------------------------- authority


@router.post("/vehicles/{vid}/possess", tags=["authority"], dependencies=OPERATOR, responses=ERRORS)
def possess(request: Request, vid: str, body: schemas.PossessBody) -> dict[str, Any]:
    return service(request).possess(vid, body.input, body.pad)


@router.post("/vehicles/{vid}/release", tags=["authority"], dependencies=OPERATOR, responses=ERRORS)
def release(request: Request, vid: str) -> dict[str, Any]:
    return service(request).release(vid)


def _session_actor(request: Request):
    who = actor(request, "other")
    if who.kind != "session":
        raise err("session_required")
    return who


@router.post("/vehicles/{vid}/claim", tags=["authority"], responses=ERRORS)
def claim(request: Request, vid: str) -> dict[str, Any]:
    return service(request).claim(vid, _session_actor(request))


@router.post("/vehicles/{vid}/unclaim", tags=["authority"], responses=ERRORS)
def unclaim(request: Request, vid: str) -> dict[str, Any]:
    return service(request).unclaim(vid, _session_actor(request))


# ---------------------------------------------------------------------------- control (controller only)


@router.post("/vehicles/{vid}/sticks", tags=["control"], responses=ERRORS)
def sticks(request: Request, vid: str, body: schemas.SticksBody) -> dict[str, Any]:
    who = actor(request, "control", body.input, body.pad)
    values = body.model_dump(include={"roll", "pitch", "yaw", "throttle"})
    return service(request).sticks(vid, who, values)


@router.post("/vehicles/{vid}/arm", tags=["control"], responses=ERRORS)
def arm(request: Request, vid: str, body: schemas.ArmBody) -> dict[str, Any]:
    return service(request).arm(vid, actor(request, "control"), body.arm, body.confirm, force=body.force)


@router.post("/vehicles/{vid}/mode", tags=["control"], responses=ERRORS)
def mode(request: Request, vid: str, body: schemas.ModeBody) -> dict[str, Any]:
    return service(request).set_mode(vid, actor(request, "control"), body.name)


@router.post("/vehicles/{vid}/skills/{name}", tags=["control"], responses=ERRORS)
def skill(request: Request, vid: str, name: str, body: schemas.SkillBody | None = None) -> dict[str, Any]:
    params = (body or schemas.SkillBody()).model_dump(exclude_none=True)
    return service(request).run_skill(vid, actor(request, "control"), name, params)


@router.post("/vehicles/{vid}/plan", tags=["control"], responses=ERRORS)
def plan(request: Request, vid: str, body: schemas.PlanBody) -> dict[str, Any]:
    """Fly a sequence of skills: takeoff, hover, move, yaw, land, goto, rtl (03 §5.8)."""
    steps = [step.model_dump(exclude_none=True) for step in body.steps]
    tuning = body.tuning.model_dump(exclude_none=True) if body.tuning else None
    return service(request).run_plan(
        vid, actor(request, "control"), steps, body.name, body.confirm, tuning=tuning
    )


@router.patch("/vehicles/{vid}/plan/tuning", tags=["control"], responses=ERRORS)
def plan_tuning(request: Request, vid: str, body: schemas.PlanTuning) -> dict[str, Any]:
    """Change the plan's throttle tuning while it flies; the step in flight takes it at once."""
    return service(request).tune_plan(vid, actor(request, "control"), body.model_dump(exclude_none=True))


@router.post("/vehicles/{vid}/sequence", tags=["control"], responses=ERRORS)
def sequence_run(request: Request, vid: str, body: schemas.SequenceBody) -> dict[str, Any]:
    """Fly an ``.ars`` stick timeline (10 §2); arms first on the ground (real aircraft: confirm "ARM")."""
    return service(request).run_sequence(
        vid, actor(request, "control"), body.sequence, body.confirm, body.speed, body.start_at
    )


@router.patch("/vehicles/{vid}/sequence", tags=["control"], responses=ERRORS)
def sequence_edit(request: Request, vid: str, body: schemas.SequenceEditBody) -> dict[str, Any]:
    """Change the flying sequence: new tracks (blocks not yet flown) and / or the trim, at once."""
    trim = body.trim.model_dump(exclude_none=True) if body.trim else None
    return service(request).edit_sequence(vid, actor(request, "control"), body.sequence, trim)


@router.post("/vehicles/{vid}/sequence/stop", tags=["control"], responses=ERRORS)
def sequence_stop(request: Request, vid: str) -> dict[str, Any]:
    """Stop the timeline; in the air an altitude-hold sequence hands over to the hold skill."""
    return service(request).stop_sequence(vid, actor(request, "control"))


@router.post("/vehicles/{vid}/sequence/record", tags=["control"], responses=ERRORS)
def sequence_record(request: Request, vid: str, body: schemas.SequenceRecordBody) -> dict[str, Any]:
    """Record the sticks sent while armed; ``stop`` returns them as an ``.ars`` document."""
    return service(request).record_sequence(vid, actor(request, "control"), body.action, body.name or "")


@router.post("/vehicles/{vid}/refly", tags=["control"], responses=ERRORS)
def refly(request: Request, vid: str, body: schemas.ReflyBody) -> dict[str, Any]:
    """Fly a record again as a plan, a path or its recorded sticks (04 §4)."""
    return service(request).refly(
        vid, actor(request, "control"), body.session_id, body.mode, body.speed, body.confirm
    )


@router.post("/vehicles/{vid}/plan/cancel", tags=["control"], responses=ERRORS)
def plan_cancel(request: Request, vid: str) -> dict[str, Any]:
    """Cancel the running plan; an airborne aircraft holds where it is."""
    return service(request).cancel_plan(vid, actor(request, "control"))


@router.post("/vehicles/{vid}/course/start", tags=["courses"], responses=ERRORS)
def course_start(request: Request, vid: str, body: schemas.CourseStartBody | None = None) -> dict[str, Any]:
    body = body or schemas.CourseStartBody()
    return service(request).course_start(vid, actor(request, "control"), body.course_id)


@router.post("/vehicles/{vid}/course/stop", tags=["courses"], responses=ERRORS)
def course_stop(request: Request, vid: str) -> dict[str, Any]:
    return service(request).course_stop(vid, actor(request, "control"))


@router.post("/vehicles/{vid}/stop", tags=["control"], responses=ERRORS)
def stop(request: Request, vid: str) -> dict[str, Any]:
    """Emergency stop: throttle to zero, then a disarm request. Claim and possession are unchanged."""
    return service(request).stop(vid, actor(request, "control"))


@router.post("/stop-all", tags=["control"], dependencies=APP, responses=ERRORS)
def stop_all(request: Request) -> dict[str, Any]:
    return service(request).stop_all()


@router.post("/vehicles/{vid}/params/refresh", tags=["params"], responses=ERRORS)
def params_refresh(request: Request, vid: str) -> dict[str, Any]:
    return service(request).params_refresh(vid, actor(request, "other"))


@router.post("/vehicles/{vid}/params", tags=["params"], responses=ERRORS)
def set_param(request: Request, vid: str, body: schemas.ParamBody) -> dict[str, Any]:
    """Write one parameter: the operator, or the session that controls the aircraft (04 §2)."""
    return service(request).set_param(
        vid, body.name, body.value, body.confirm, actor=actor(request, "control")
    )


@router.post("/vehicles/{vid}/motor-test", tags=["control"], dependencies=OPERATOR, responses=ERRORS)
def motor_test(request: Request, vid: str, body: schemas.MotorTestBody) -> dict[str, Any]:
    return service(request).motor_test(vid, body.motor, body.throttle_pct, body.seconds, body.confirm)
