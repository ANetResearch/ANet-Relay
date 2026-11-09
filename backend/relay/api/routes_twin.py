"""Firmware twins (11 §10): runtime packs, firmware identity, twin profiles, twin details and frames.

Authority: reads are anonymous like the other state reads. Importing and deleting a pack is the operator's
(it installs an executable). Capturing, editing and deleting profiles are application operations (the
operator, or an agent session while app control is on), like adding simulators. The frame of a simulator
follows the airframe rule (the operator, the controller, or an agent on an uncontrolled simulator).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from . import schemas
from . import schemas_twin as st
from .deps import actor, require_app_control, require_operator, service

router = APIRouter()
ERRORS = {s: {"model": schemas.ErrorBody} for s in (400, 401, 403, 404, 409, 429, 503)}
OPERATOR = [Depends(require_operator)]
APP = [Depends(require_app_control)]


# ---------------------------------------------------------------------------- runtime packs (11 §3)


@router.get("/autopilots", tags=["twins"], response_model=st.AutopilotPacks)
def autopilots(request: Request) -> dict[str, Any]:
    """The runtime packs of this platform: bundled with Relay, then imported."""
    return service(request).autopilots()


@router.post(
    "/autopilots/import",
    tags=["twins"],
    dependencies=OPERATOR,
    response_model=st.AutopilotPack,
    responses=ERRORS,
)
def import_autopilot(request: Request, body: st.AutopilotImportBody) -> dict[str, Any]:
    """Import a pack tarball from a local path after verifying its sha256 and contents (operator)."""
    return service(request).import_autopilot(body.path, body.sha256)


@router.delete(
    "/autopilots/{pack_id}",
    tags=["twins"],
    dependencies=OPERATOR,
    response_model=st.Deleted,
    responses=ERRORS,
)
def delete_autopilot(request: Request, pack_id: str) -> dict[str, Any]:
    """Delete an imported pack; packs installed with Relay stay (operator)."""
    return service(request).delete_autopilot(pack_id)


# ---------------------------------------------------------------------------- identity (11 §4)


@router.get("/vehicles/{vid}/firmware", tags=["twins"], response_model=st.VehicleFirmware, responses=ERRORS)
def vehicle_firmware(request: Request, vid: str) -> dict[str, Any]:
    """The aircraft's firmware identity, the matching packs and whether a twin can fly its frame."""
    return service(request).vehicle_firmware(vid)


# ---------------------------------------------------------------------------- profiles (11 §5)


@router.get("/twins/profiles", tags=["twins"], response_model=st.TwinProfiles)
def twin_profiles(request: Request) -> dict[str, Any]:
    return service(request).twin_profiles_list()


@router.post(
    "/twins/profiles", tags=["twins"], dependencies=APP, response_model=st.TwinProfile, responses=ERRORS
)
def capture_twin_profile(request: Request, body: st.TwinProfileCaptureBody) -> dict[str, Any]:
    """Capture a profile from a connected aircraft (its parameter table read completely)."""
    frame = body.frame.model_dump(exclude_none=True) if body.frame else None
    return service(request).capture_twin_profile(body.from_vehicle, body.name, frame, body.profile)


@router.get("/twins/profiles/{pid}", tags=["twins"], response_model=st.TwinProfile, responses=ERRORS)
def twin_profile(request: Request, pid: str) -> dict[str, Any]:
    """One profile with every parameter (also the JSON export)."""
    return service(request).twin_profile(pid)


@router.put(
    "/twins/profiles/{pid}", tags=["twins"], dependencies=APP, response_model=st.TwinProfile, responses=ERRORS
)
def update_twin_profile(request: Request, pid: str, body: st.TwinProfilePatch) -> dict[str, Any]:
    """Rename, change the frame, choose another pack of the same firmware, set or clear the home."""
    patch = body.model_dump(exclude_unset=True)
    if "frame" in patch and patch["frame"] is not None:
        patch["frame"] = {k: v for k, v in patch["frame"].items() if v is not None}
    for key in ("name", "frame", "pack"):
        if key in patch and patch[key] is None:
            del patch[key]
    return service(request).update_twin_profile(pid, patch)


@router.delete(
    "/twins/profiles/{pid}", tags=["twins"], dependencies=APP, response_model=st.Deleted, responses=ERRORS
)
def delete_twin_profile(request: Request, pid: str) -> dict[str, Any]:
    return service(request).delete_twin_profile(pid)


# ---------------------------------------------------------------------------- simulators (11 §6, §8.1)


@router.get("/sims/{vid}/twin", tags=["sims"], response_model=st.SimTwin, responses=ERRORS)
def sim_twin(request: Request, vid: str) -> dict[str, Any]:
    """A firmware twin's pack, profile, parameter application, frame, process and lockstep figures."""
    return service(request).twin_view(vid)


@router.put("/sims/{vid}/frame", tags=["sims"], response_model=st.SimFrame, responses=ERRORS)
def set_sim_frame(request: Request, vid: str, body: schemas.FrameBody) -> dict[str, Any]:
    """A new frame for a disarmed simulator on the ground (twin or fast simulator)."""
    spec = body.model_dump(exclude_none=True)
    return service(request).set_sim_frame(vid, spec, actor(request, "control"), app_control=True)
