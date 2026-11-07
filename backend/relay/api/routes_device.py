"""Device-level surface: messages, parameters, raw commands, motors, actuators, RC, reboot (04 §2)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from .. import device
from . import schemas
from . import schemas_device as sd
from .deps import actor, service

router = APIRouter(tags=["device"])
ERRORS = {s: {"model": schemas.ErrorBody} for s in (400, 401, 403, 404, 409, 429, 503)}


@router.get("/vehicles/{vid}/device", response_model=sd.DeviceOverview, responses=ERRORS)
def overview(request: Request, vid: str) -> dict[str, Any]:
    """Motors, outputs, RC, frame, sensors, estimator, vibration, home, parameters and message rates."""
    return service(request).device_overview(vid)


@router.get("/vehicles/{vid}/messages/{name}", response_model=sd.MessageFrame, responses=ERRORS)
def message(request: Request, vid: str, name: str) -> dict[str, Any]:
    """The newest frame of one MAVLink message."""
    return service(request).message_get(vid, name)


@router.get("/vehicles/{vid}/params/{name}", response_model=sd.ParamValue, responses=ERRORS)
def param(request: Request, vid: str, name: str) -> dict[str, Any]:
    """One parameter; read by name (up to 2 s) when the table has not loaded it."""
    return service(request).param_get(vid, name)


@router.post("/vehicles/{vid}/params/read", response_model=sd.ParamsRead, responses=ERRORS)
def params_read(request: Request, vid: str, body: sd.ParamsReadBody) -> dict[str, Any]:
    return service(request).params_read(vid, actor(request, "other"), body.names)


@router.get("/commands", response_model=sd.CommandCatalogue)
def commands() -> dict[str, Any]:
    """The raw MAVLink command catalogue: labels, parameters, danger level and confirm word."""
    return {"commands": device.CATALOGUE, "confirm_words": device.CONFIRM_WORDS}


@router.post("/vehicles/{vid}/command", response_model=sd.CommandResult, responses=ERRORS)
def command(request: Request, vid: str, body: sd.CommandBody) -> dict[str, Any]:
    return service(request).raw_command(
        vid,
        actor(request, "control"),
        body.command,
        body.params,
        body.frame,
        body.use_int,
        body.confirm,
        body.timeout_s,
    )


@router.post("/vehicles/{vid}/motors/test", responses=ERRORS)
def motors_test(request: Request, vid: str, body: sd.MotorsTestBody) -> dict[str, Any]:
    """Spin one motor (or all in turn) on a disarmed aircraft. Real aircraft need confirm "MOTOR"."""
    return service(request).motors_test(
        vid, actor(request, "control"), body.motor, body.throttle_pct, body.seconds, body.confirm
    )


@router.post("/vehicles/{vid}/motors/stop", responses=ERRORS)
def motors_stop(request: Request, vid: str) -> dict[str, Any]:
    return service(request).motors_stop(vid, actor(request, "control"))


@router.post("/vehicles/{vid}/actuators/set", responses=ERRORS)
def actuators_set(request: Request, vid: str, body: sd.ActuatorBody) -> dict[str, Any]:
    return service(request).actuator_set(
        vid, actor(request, "control"), body.index, body.value, body.pwm, body.seconds, body.confirm
    )


@router.post("/vehicles/{vid}/rc", responses=ERRORS)
def rc(request: Request, vid: str, body: sd.RcBody) -> dict[str, Any]:
    """One channel-override frame; it expires after 0.8 s like sticks and replaces them."""
    return service(request).rc_override(vid, actor(request, "control"), body.channels, body.axes)


@router.post("/vehicles/{vid}/message-interval", responses=ERRORS)
def message_interval(request: Request, vid: str, body: sd.MessageIntervalBody) -> dict[str, Any]:
    return service(request).message_interval(vid, actor(request, "control"), body.message, body.hz)


@router.post("/vehicles/{vid}/request-message", responses=ERRORS)
def request_message(request: Request, vid: str, body: sd.RequestMessageBody) -> dict[str, Any]:
    return service(request).request_message(vid, actor(request, "control"), body.message)


@router.post("/vehicles/{vid}/reboot", responses=ERRORS)
def reboot(request: Request, vid: str, body: sd.ConfirmOnlyBody) -> dict[str, Any]:
    return service(request).reboot(vid, actor(request, "control"), body.confirm)


@router.post("/vehicles/{vid}/calibrate", responses=ERRORS)
def calibrate(request: Request, vid: str, body: sd.CalibrateBody) -> dict[str, Any]:
    return service(request).calibrate(vid, actor(request, "control"), body.sensor, body.confirm)


@router.post("/vehicles/{vid}/home", responses=ERRORS)
def home(request: Request, vid: str, body: sd.HomeBody) -> dict[str, Any]:
    return service(request).set_home(vid, actor(request, "control"), body.here, body.lat, body.lon, body.alt)


@router.post("/vehicles/{vid}/terminate", responses=ERRORS)
def terminate(request: Request, vid: str, body: sd.ConfirmOnlyBody) -> dict[str, Any]:
    """Flight termination: motors stop at once, then a forced disarm. Needs "TERMINATE" on every aircraft."""
    return service(request).terminate(vid, actor(request, "control"), body.confirm)
