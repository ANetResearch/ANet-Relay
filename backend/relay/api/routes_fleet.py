"""Read-only state, courses, alerts, settings and network (03 §10.1)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from .. import device, plans, skills
from ..config import (
    ARM_CONFIRM,
    LEASE_S,
    LINK_DELAYED_S,
    LINK_LOST_S,
    LINK_OFFLINE_S,
    MOTOR_CONFIRM,
    RATE_CONTROL_PER_S,
    RATE_OTHER_PER_S,
    SIM_LIMIT,
    STICK_HZ,
    STICK_TIMEOUT_S,
    VERSION,
)
from ..errors import RelayError
from ..mav import modes
from . import schemas
from .deps import caller, require_app_control, service

router = APIRouter()
ERRORS = {s: {"model": schemas.ErrorBody} for s in (400, 401, 403, 404, 409, 429, 503)}
DOCS = "https://github.com/ANetResearch/ANet-Relay/blob/main/docs/agent-api.md"


@router.get("/health", response_model=schemas.Health, tags=["state"])
def health(request: Request) -> dict[str, Any]:
    return service(request).health()


@router.get("/capabilities", tags=["state"])
def capabilities(request: Request) -> dict[str, Any]:
    """Everything an agent needs to know before it starts: frames, limits, skills, modes, docs."""
    svc = service(request)
    sv = svc.settings.safety_volume
    return {
        "version": VERSION,
        "frames": {
            "position": "ENU metres: x east, y north, z up. Simulated aircraft share frame 'sim'; "
            "each real aircraft reports its own 'local' frame.",
            "attitude": "radians; roll right-wing-down +, pitch nose-up +, "
            "yaw = NED heading (0 north, clockwise +)",
            "sticks": "roll right +, pitch forward +, yaw clockwise +, all -1..1; "
            "throttle 0..1 (0.5 = hold in altitude-hold modes)",
            "goto_yaw": "degrees, compass heading (0 north, clockwise)",
        },
        "limits": {
            "stick_timeout_s": STICK_TIMEOUT_S,
            "stick_rate_hz": STICK_HZ,
            "link_delayed_s": LINK_DELAYED_S,
            "link_lost_s": LINK_LOST_S,
            "link_offline_s": LINK_OFFLINE_S,
            "lease_s": LEASE_S,
            "rate_limit_per_s": {"control": RATE_CONTROL_PER_S, "other": RATE_OTHER_PER_S},
            "sim_limit": SIM_LIMIT,
            "safety_volume": {"x": sv.x, "y": sv.y, "z": sv.z},
        },
        "skills": [
            {"name": name, "requires": req, "params": _skill_params(name)}
            for name, req in skills.REQUIREMENTS.items()
        ],
        "modes": {
            modes.ARDUPILOT: modes.available_modes(modes.ARDUPILOT),
            modes.PX4: modes.available_modes(modes.PX4),
        },
        "plan": {
            "endpoint": "POST /vehicles/{id}/plan",
            "actions": list(plans.ACTIONS),
            "example": [
                {"action": "takeoff", "alt": 1.0},
                {"action": "hover", "seconds": 3},
                {"action": "move", "forward": 1},
                {"action": "move", "forward": -1},
                {"action": "yaw", "deg": 45},
                {"action": "land"},
            ],
        },
        "confirm": {
            "arm_real": ARM_CONFIRM,
            "motor_test": MOTOR_CONFIRM,
            "param_set": "<parameter name>",
            **{level: word for level, word in device.CONFIRM_WORDS.items() if level != "motor"},
            "note": "Real aircraft need the word; simulated aircraft only for terminate (04 §1).",
        },
        "device": {
            "commands": "GET /commands",
            "endpoints": [
                "GET /vehicles/{id}/device",
                "GET /vehicles/{id}/messages/{name}",
                "GET /vehicles/{id}/params/{name}",
                "POST /vehicles/{id}/params/read",
                "POST /vehicles/{id}/command",
                "POST /vehicles/{id}/motors/test",
                "POST /vehicles/{id}/motors/stop",
                "POST /vehicles/{id}/actuators/set",
                "POST /vehicles/{id}/rc",
                "POST /vehicles/{id}/message-interval",
                "POST /vehicles/{id}/request-message",
                "POST /vehicles/{id}/reboot",
                "POST /vehicles/{id}/calibrate",
                "POST /vehicles/{id}/home",
                "POST /vehicles/{id}/terminate",
            ],
        },
        "control_levels": [
            {"level": 1, "name": "sticks", "requires": "manual"},
            {"level": 2, "name": "modes and commands", "requires": "manual / althold"},
            {"level": 3, "name": "position skills", "requires": "position"},
        ],
        "docs": {"openapi": "/openapi.json", "reference": "/docs", "guide": DOCS},
    }


def _skill_params(name: str) -> dict[str, str]:
    return {
        "takeoff": {
            "alt": "metres above the take-off point, 0.2 to the safety-volume height",
            "confirm": "ARM, real aircraft only",
        },
        "goto": {"x": "m east", "y": "m north", "z": "m up", "yaw": "optional heading in degrees"},
        "hold": {"seconds": "optional: report done after holding this long (hover)"},
        "move": {
            "forward": "m forward in the body frame (negative: back)",
            "right": "m right (negative: left)",
            "up": "m up (negative: down)",
            "note": "closed loop with a position estimate; otherwise an open-loop tilt profile, "
            "at most 2 m, whose distance is an estimate",
        },
        "yaw": {"deg": "degrees relative to the current heading, positive clockwise"},
        "sticks": {
            "roll": "-1..1 right +",
            "pitch": "-1..1 forward +",
            "yaw": "-1..1 clockwise +",
            "throttle": "0..1 (0.5 holds altitude in altitude-hold modes)",
            "seconds": "0 to 10; the sticks return to centre afterwards",
        },
        "attitude": {
            "roll_deg": "-30..30, right wing down +",
            "pitch_deg": "-30..30, nose up + (negative tilts forward)",
            "yaw_rate_dps": "-90..90, clockwise +",
            "thrust": "0..1, default the calibrated hover thrust",
            "seconds": "0 to 10; the aircraft returns to altitude hold afterwards",
            "note": "ArduPilot GUIDED_NOGPS or PX4 OFFBOARD with SET_ATTITUDE_TARGET at 20 Hz",
        },
        "velocity": {
            "vx": "m/s east, -2..2",
            "vy": "m/s north, -2..2",
            "vz": "m/s up, -2..2",
            "yaw_rate_dps": "optional, -90..90",
            "seconds": "0 to 10; stops before leaving the safety volume (outside_safety_volume)",
        },
    }.get(name, {})


@router.get("/fleet", tags=["state"], responses={200: {"model": schemas.Fleet}})
def fleet(request: Request) -> dict[str, Any]:
    return service(request).fleet()


@router.get("/vehicles/{vid}", tags=["state"], responses=ERRORS)
def vehicle(request: Request, vid: str) -> dict[str, Any]:
    return service(request).vehicle_detail(vid)


@router.get("/vehicles/{vid}/messages", tags=["state"], responses=ERRORS)
def messages(request: Request, vid: str) -> dict[str, Any]:
    return service(request).messages(vid)


@router.get("/vehicles/{vid}/params", tags=["state"], responses=ERRORS)
def params(request: Request, vid: str) -> dict[str, Any]:
    return service(request).params(vid)


@router.get("/vehicles/{vid}/preflight", tags=["state"], responses=ERRORS)
def preflight(request: Request, vid: str) -> dict[str, Any]:
    return service(request).preflight(vid)


@router.get("/topology", tags=["state"])
def topology(request: Request) -> dict[str, Any]:
    return service(request).topology()


@router.get("/events", response_model=schemas.Events, tags=["state"])
def events(
    request: Request,
    after: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    vehicle: str | None = None,
    tail: bool = Query(
        False, description="return the newest `limit` events after `after` instead of the oldest"
    ),
) -> dict[str, Any]:
    bus = service(request).events
    items = bus.after(after, bus.size, vehicle)[-limit:] if tail else bus.after(after, limit, vehicle)
    return {"events": items, "last_seq": bus.last_seq}


@router.get("/network", tags=["network"])
def network(request: Request) -> dict[str, Any]:
    return service(request).network()


@router.post(
    "/network/retry", tags=["network"], dependencies=[Depends(require_app_control)], responses=ERRORS
)
def network_retry(request: Request) -> dict[str, Any]:
    """Retry binding the UDP listen port (R-05)."""
    return service(request).retry_listen()


@router.get("/courses", tags=["courses"])
def courses(request: Request) -> dict[str, Any]:
    return service(request).courses()


@router.put(
    "/courses/{course_id}", tags=["courses"], dependencies=[Depends(require_app_control)], responses=ERRORS
)
def put_course(request: Request, course_id: str, body: schemas.CourseBody) -> dict[str, Any]:
    return service(request).put_course(course_id, body.model_dump())


@router.get("/alerts", tags=["alerts"])
def alerts(request: Request) -> dict[str, Any]:
    return {"alerts": service(request).alerts.view()}


@router.post(
    "/alerts/{alert_id}/ack", tags=["alerts"], dependencies=[Depends(require_app_control)], responses=ERRORS
)
def ack_alert(request: Request, alert_id: str) -> dict[str, Any]:
    return service(request).ack_alert(alert_id)


@router.get("/settings", tags=["settings"])
def get_settings(request: Request) -> dict[str, Any]:
    return service(request).get_settings()


@router.put("/settings", tags=["settings"], dependencies=[Depends(require_app_control)], responses=ERRORS)
def put_settings(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise RelayError("invalid_request", detail={"reason": "body must be an object"})
    svc = service(request)
    grant = body.get("agents_app_control")
    if grant is not None and grant != svc.settings.agents_app_control and not caller(request).operator:
        raise RelayError("operator_required")  # only the person at the window grants or withdraws app control
    return svc.update_settings(body)
