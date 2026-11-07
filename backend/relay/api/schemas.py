"""Pydantic models of the public API (03 §10.3).

These models document the snapshot, event and error shapes in OpenAPI and are
exported to ``web/src/api/schema.json`` (``backend/tools/export_schema.py``)
for the renderer's TypeScript types. Request bodies are validated with the
``*Body`` models.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow", json_schema_serialization_defaults_required=True)


# ---------------------------------------------------------------------------- snapshot


class Listen(_Model):
    port: int
    ok: bool
    error: str | None


class Scan(_Model):
    running: bool
    done: int
    total: int
    networks: list[str]
    found: int
    error: str | None


class SessionSummary(_Model):
    session_id: str
    name: str
    expires_in_s: float
    vehicles: list[str]


class SafetyVolume(_Model):
    x: float
    y: float
    z: float


class Bridge(_Model):
    version: str | None
    source: Literal["udp", "http", "manual"]


class Link(_Model):
    state: Literal["waiting", "ok", "delayed", "lost", "offline"]
    heartbeat_age_s: float | None
    rx_hz: float
    tx_hz: float
    loss_pct: float | None
    rtt_ms: float | None


class Attitude(_Model):
    roll: float = Field(description="rad, right wing down positive")
    pitch: float = Field(description="rad, nose up positive")
    yaw: float = Field(description="rad, NED heading: 0 north, clockwise positive")


class Rates(_Model):
    roll: float
    pitch: float
    yaw: float


class Position(_Model):
    frame: Literal["local", "sim"]
    x: float = Field(description="east, m")
    y: float = Field(description="north, m")
    z: float = Field(description="up, m")
    vx: float
    vy: float
    vz: float
    age_s: float


class GlobalPosition(_Model):
    lat: float
    lon: float
    alt: float


class Battery(_Model):
    voltage: float | None
    current: float | None
    remaining_pct: int | None
    cells: int | None
    level: Literal["ok", "warn", "critical", "unknown"]


class Gps(_Model):
    fix: int
    sats: int | None
    hdop: float | None


class Sensor(_Model):
    name: str
    present: bool
    enabled: bool
    healthy: bool


class StatusText(_Model):
    t: float
    severity: int
    text: str


class Capabilities(_Model):
    manual: bool
    althold: bool
    position: bool


class ClaimView(_Model):
    session_id: str
    name: str
    since: float
    suspended: bool


class PossessionView(_Model):
    input: Literal["keyboard", "screen", "gamepad"]
    pad: int | None
    since: float


class SticksView(_Model):
    roll: float
    pitch: float
    yaw: float
    throttle: float
    age_s: float
    source: Literal["human", "agent"]


class SkillView(_Model):
    id: int = Field(description="increments with every skill started on this backend")
    name: str
    state: Literal["running", "done", "failed"]
    detail: str | None


class Signs(_Model):
    roll: Literal[1, -1]
    pitch: Literal[1, -1]
    yaw: Literal[1, -1]


class CalibrationView(_Model):
    signs: Signs
    hover: float


class ConfirmedView(_Model):
    signs: bool
    props: bool


class PreflightSummary(_Model):
    ok: bool
    blocking: list[str]


class Ring(_Model):
    c: list[float] = Field(description="centre [x east, y north, z up], m", min_length=3, max_length=3)
    yaw_deg: float = Field(description="compass heading of the ring normal")
    r: float = Field(description="radius, m")


class CourseRunView(_Model):
    course_id: str
    name_en: str | None = None
    name_zh: str | None = None
    state: Literal["waiting_takeoff", "running", "finished", "aborted"]
    next: int
    total: int
    started_at: float | None
    elapsed_s: float
    splits: list[float]
    best_s: float | None
    reason: str | None
    origin: list[float] = Field(
        description="[x, y, z, heading_deg] of the course origin", min_length=4, max_length=4
    )
    rings: list[Ring] = Field(description="rings placed in the vehicle's position frame")


class Vehicle(_Model):
    id: str
    kind: Literal["real", "sim"]
    label: str
    host: str | None
    autopilot: Literal["PX4", "ArduPilot"] | None
    firmware: str | None
    sysid: int | None
    bridge: Bridge | None
    link: Link
    armed: bool
    mode: str | None
    mode_class: Literal["manual", "althold", "position", "auto"] | None
    modes: list[str]
    landed: Literal["unknown", "ground", "air", "takeoff", "landing"]
    crashed: bool
    attitude: Attitude | None
    rates: Rates | None
    position: Position | None
    global_: GlobalPosition | None = Field(alias="global")
    altitude_m: float | None
    climb_ms: float | None
    groundspeed_ms: float | None
    heading_deg: float | None
    throttle_pct: float | None
    battery: Battery
    gps: Gps | None
    sensors: list[Sensor]
    statustext: list[StatusText]
    rc: list[int] | None
    servo: list[int] | None
    flight_time_s: float
    armed_time_s: float
    capabilities: Capabilities
    claim: ClaimView | None
    possession: PossessionView | None
    controller: Literal["none", "human", "agent"]
    sticks: SticksView | None
    skill: SkillView | None
    calibration: CalibrationView
    confirmed: ConfirmedView
    preflight: PreflightSummary
    course: CourseRunView | None
    record_session: int | None


class Alert(_Model):
    id: str
    vehicle: str | None
    level: Literal["info", "warn", "critical"]
    code: str
    text_en: str
    text_zh: str
    since: float
    acked: bool


class Fleet(_Model):
    t: float
    version: str
    listen: Listen
    scan: Scan
    sessions: list[SessionSummary]
    safety_volume: SafetyVolume
    vehicles: list[Vehicle]
    alerts: list[Alert]


EventKind = Literal[
    "vehicle_discovered",
    "vehicle_online",
    "vehicle_offline",
    "link_delayed",
    "link_lost",
    "link_restored",
    "armed",
    "disarmed",
    "mode_changed",
    "statustext",
    "command_ack",
    "possession_started",
    "possession_released",
    "relay_handback",
    "claim_started",
    "claim_released",
    "agent_session_started",
    "agent_session_closed",
    "agent_session_expired",
    "skill_started",
    "skill_finished",
    "skill_failed",
    "stop",
    "stop_all",
    "battery_warn",
    "battery_critical",
    "crashed",
    "airborne_no_controller",
    "param_set",
    "sim_added",
    "sim_removed",
    "bridge_configured",
    "course_started",
    "ring_passed",
    "course_finished",
    "course_aborted",
    "listen_error",
]


class Event(_Model):
    seq: int
    t: float
    kind: EventKind
    vehicle: str | None
    session: str | None
    text_en: str
    text_zh: str
    data: dict[str, Any]


class Events(_Model):
    events: list[Event]
    last_seq: int


class ErrorDetail(_Model):
    code: str
    message_en: str
    message_zh: str
    detail: dict[str, Any]


class ErrorBody(_Model):
    error: ErrorDetail


class Health(_Model):
    ok: bool
    version: str
    uptime_s: float


class Hello(_Model):
    type: Literal["hello"]
    version: str
    operator: bool
    session: str | None


class FleetMessage(_Model):
    type: Literal["fleet"]
    data: Fleet


class EventMessage(_Model):
    type: Literal["event"]
    data: Event


class ErrorMessage(_Model):
    type: Literal["error"]
    error: ErrorDetail


class SessionOpened(_Model):
    session_id: str
    name: str
    token: str
    lease_s: float


# ---------------------------------------------------------------------------- request bodies


class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ConnectBody(_Body):
    host: str
    port: int | None = None


class DiscoverBody(_Body):
    networks: list[str] | None = None
    http_port: int = Field(default=80, ge=1, le=65535)


class LabelBody(_Body):
    label: str


class CalibrationBody(_Body):
    signs: dict[str, int] | None = None
    hover: float | None = None


class ConfirmBody(_Body):
    item: Literal["signs", "props"]


class PossessBody(_Body):
    input: Literal["keyboard", "screen", "gamepad"]
    pad: int | None = Field(default=None, ge=1, le=4)


class SticksBody(_Body):
    roll: float = Field(ge=-1, le=1)
    pitch: float = Field(ge=-1, le=1)
    yaw: float = Field(ge=-1, le=1)
    throttle: float = Field(ge=0, le=1)
    input: Literal["keyboard", "screen", "gamepad"] | None = None
    pad: int | None = None


class ArmBody(_Body):
    arm: bool = True
    confirm: str | None = None


class ModeBody(_Body):
    name: str


class SkillBody(_Body):
    model_config = ConfigDict(extra="allow")
    alt: float | None = None
    x: float | None = None
    y: float | None = None
    z: float | None = None
    yaw: float | None = Field(default=None, description="goto heading in degrees (0 north, clockwise)")
    confirm: str | None = None


class CourseStartBody(_Body):
    course_id: str = "cage-loop"


class CourseBody(_Body):
    name_en: str | None = None
    name_zh: str | None = None
    rings: list[dict[str, Any]]


class ParamBody(_Body):
    name: str
    value: float
    confirm: str | None = None


class MotorTestBody(_Body):
    motor: int
    throttle_pct: float
    seconds: float = 2.0
    confirm: str | None = None


class SimsBody(_Body):
    count: int = Field(default=1, ge=1, le=8)
    autopilot: Literal["ardupilot", "px4"] = "ardupilot"


class SessionBody(_Body):
    name: str


class BridgeConfigureBody(_Body):
    model_config = ConfigDict(extra="ignore")
    mode: Literal["station", "ap"] = "station"
    ssid: str | None = None
    password: str | None = None
    ip: str | None = None
    gateway: str | None = None
    subnet: str | None = None
    host_port: int | None = None
    client_port: int | None = None
    baud: int | None = None
    channel: int | None = None
    ap_ssid: str | None = None
    ap_password: str | None = None
    form: dict[str, Any] | None = None


EXPORTED = {
    "Fleet": Fleet,
    "Vehicle": Vehicle,
    "Alert": Alert,
    "Event": Event,
    "Events": Events,
    "ErrorBody": ErrorBody,
    "Health": Health,
    "Hello": Hello,
    "FleetMessage": FleetMessage,
    "EventMessage": EventMessage,
    "ErrorMessage": ErrorMessage,
    "SessionOpened": SessionOpened,
}
