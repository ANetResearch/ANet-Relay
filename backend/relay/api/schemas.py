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
    source: Literal["human", "agent", "program", "relay"]  # program: WP-1 onboard program; relay: arming


class SkillView(_Model):
    id: int = Field(description="increments with every skill started on this backend")
    name: str
    state: Literal["running", "done", "failed"]
    detail: str | None
    progress_s: float | None = Field(default=None, description="sequence: seconds into the time line")
    duration_s: float | None = Field(default=None, description="sequence: length of the time line")
    phase: str | None = Field(default=None, description="sequence: mode | arm | play | end | hold")


PlanAction = Literal[
    "takeoff", "hover", "hold", "move", "yaw", "land", "goto", "rtl", "sticks", "attitude", "velocity"
]


HealthLevel = Literal["ok", "warn", "bad", "na"]


class HealthItem(_Model):
    key: Literal[
        "oscillation",
        "lean",
        "yaw_drift",
        "alt_hold",
        "hover_throttle",
        "motors",
        "vibration",
        "battery_sag",
        "link",
        "stick_stream",
    ]
    level: HealthLevel
    value: float | None
    unit: str
    detail: dict[str, Any] | None = None


class FlightHealthView(_Model):
    """Rolling diagnostics over the last few seconds (03 §5.9). Flight items need the aircraft airborne."""

    state: Literal["ok", "warn", "bad", "idle"]
    window_s: float
    airborne_samples: int
    items: list[HealthItem]


class PlanView(_Model):
    id: int
    name: str | None
    state: Literal["running", "done", "failed", "cancelled"]
    index: int = Field(description="step being flown (equals count once done)")
    count: int
    steps: list[dict[str, Any]]
    results: list[str | None]
    detail: str | None
    tuning: dict[str, float] = Field(
        default_factory=dict, description="throttle tuning of the stick steps: climb, hover (PlanTuning)"
    )


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


# WP-2a airframe defects (06 §4) ---------------------------------------------------------------------

AirframePreset = Literal["ideal", "typical", "rough", "custom"]


class AirframeRef(_Model):
    """The snapshot's view of a simulator airframe: preset and seed only, never the values (06 §4)."""

    preset: AirframePreset
    seed: int


class AirframeParams(_Model):
    mass_kg: float = Field(description="airframe mass without payload, kg")
    payload_kg: float = Field(description="payload, kg; take-off weight is mass_kg + payload_kg")
    cg_offset_cm: list[float] = Field(description="[forward, right] centre-of-gravity offset, cm")
    level_trim_deg: list[float] = Field(description="[roll, pitch] level calibration error, deg")
    motor_gain: list[float] = Field(description="efficiency deviation of motors 1-4 (0.05 = +5 %)")
    prop_damage: float = Field(description="damage of one propeller 0..1 (thrust loss, vibration)")
    damaged_prop: int = Field(description="the damaged propeller's motor number 1-4")
    imu_noise: float = Field(description="attitude, rate and acceleration noise multiplier")
    arm_bias_deg: float = Field(
        default=0.0, description="random level error of up to this many degrees per axis, new at every arming"
    )
    with_assist: bool = Field(
        default=False,
        description="the defects act with hover assist on as well (altitude hold keeps the height)",
    )


class SimAirframe(_Model):
    """GET / PUT /sims/{vid}/airframe: the full values (operator or controller)."""

    id: str
    preset: AirframePreset
    seed: int
    params: AirframeParams
    active: bool = Field(description="the defects act now (hover assist off, or with_assist)")
    level_now_deg: list[float] = Field(
        default_factory=lambda: [0.0, 0.0],
        description="[roll, pitch] level error of this flight: calibration error plus the arming bias, deg",
    )
    hover_throttle: float | None = Field(
        description="level hover throttle 0..1 out of ground effect at full battery; null: cannot lift"
    )
    limits: dict[str, list[float]] = Field(description="[min, max] of every parameter")


class SimView(_Model):
    """Simulator flight model (05 §5.2, WP-C); null on real aircraft."""

    hover_assist: bool = Field(description="altitude and position hold available; false: physical flight")
    turbulence: float = Field(description="turbulence strength 0..1 (acts only with hover assist off)")
    airframe: AirframeRef  # WP-2a (06 §4)


class ProcedureBrief(_Model):
    """The aircraft's bench procedure run, current or last (WP-B, 05 §4.4)."""

    run_id: int
    procedure: str
    state: Literal["running", "waiting", "passed", "warned", "failed", "aborted"]
    step: str | None = Field(description="step id while the run is active")
    index: int
    total: int


class ProgramStateMachine(_Model):
    """A program's ``StateMachine`` as the inspector shows it (WP-1, 06 §2.3)."""

    state: str
    transitions: int
    state_s: float = Field(description="seconds in the current state")


class ProgramOwner(_Model):
    kind: Literal["operator", "session"]
    name: str


class ProgramRunView(_Model):
    """The program deployed on an aircraft, running or last run (WP-1, 06 §2.6)."""

    id: str = Field(description="program id")
    name: str
    rev: int
    state: str = Field(description="ctx.state, at most 32 characters")
    status: Literal["running", "paused", "faulted", "stopped"]
    reason: str | None = Field(description="why it is paused, stopped or faulted (a code)")
    mode: Literal["pilot", "copilot"]
    fps: float = Field(description="frames run in the last second (50 when healthy)")
    frames: int
    faults: int = Field(description="failed frames since deployment")
    overruns: int = Field(description="frames over the 2 ms soft budget")
    last_error: str | None
    params: dict[str, Any]
    seed: int
    rates_ok: bool | None = Field(description="ATTITUDE reached 40 Hz after the rate request; null: not yet")
    sm: ProgramStateMachine | None
    log_seq: int = Field(description="sequence of the newest log line (GET .../program/log?after=)")
    owner: ProgramOwner
    started_at: float


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
    controller: Literal["none", "human", "agent", "program"]  # program: WP-1
    sticks: SticksView | None
    skill: SkillView | None
    plan: PlanView | None
    health: FlightHealthView
    calibration: CalibrationView
    confirmed: ConfirmedView
    preflight: PreflightSummary
    course: CourseRunView | None
    record_session: int | None
    sim: SimView | None
    procedure: ProcedureBrief | None = None  # bench procedure (WP-B)
    # WP-1 onboard programs (06 §2.6)
    program: ProgramRunView | None = None
    programs_allowed: bool = Field(
        default=True, description="real aircraft: the operator allowed programs (reset when the link drops)"
    )


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
    "plan_started",
    "plan_step",
    "plan_finished",
    "plan_failed",
    "plan_cancelled",
    "plan_tuned",
    "sequence_started",
    "sequence_edited",
    "sequence_recording",
    "device_command",
    "motor_test",
    "motor_test_stopped",
    "actuator_set",
    "rc_override",
    "message_interval",
    "home_set",
    "rebooting",
    "calibration_started",
    "terminated",
    "app_command",
    "refly_started",
    "refly_finished",
    "refly_failed",
    "sim_assist_changed",
    "sim_airframe_changed",  # WP-2a (06 §4)
    # bench procedures (WP-B, 05 §4.4)
    "procedure_started",
    "procedure_step",
    "procedure_waiting",
    "procedure_finished",
    "procedure_aborted",
    # LAN arena (05 §7.4, WP-D)
    "peer_joined",
    "peer_left",
    "arena_hit",
    "arena_down",
    # WP-1 onboard programs (06 §2.6)
    "program_started",
    "program_stopped",
    "program_paused",
    "program_resumed",
    "program_faulted",
    "program_host_restarted",
    "program_gate",
    # WP-2b challenges and duels (06 §3.4)
    "judge_finished",
    # WP-A collisions (07 §2)
    "collision",
    "ring_touch",
    # WP-B dogfight scenario and matches (07 §3-§4)
    "dogfight_started",
    "dogfight_finished",
    "match_started",
    "match_finished",
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


# ---------------------------------------------------------------------------- LAN arena (05 §7.5, WP-D)


class ArenaScore(_Model):
    hits: int
    downs: int
    deaths: int


class ArenaVehicle(_Model):
    """A remote simulator, converted into this terminal's local frame (ENU m, radians)."""

    key: str = Field(description="<peer id>/sim-N")
    label: str
    x: float
    y: float
    z: float
    vx: float
    vy: float
    vz: float
    roll: float
    pitch: float
    yaw: float = Field(description="rad, compass heading in the local frame")
    throttle: float
    armed: bool
    down: bool
    hp: int


class ArenaPeer(_Model):
    id: str = Field(description="the peer's install id, 16 hex digits")
    callsign: str
    seat: int | None
    rtt_ms: float | None
    stale: bool
    age_s: float
    score: ArenaScore
    vehicles: list[ArenaVehicle]


class ArenaShot(_Model):
    """A bullet in flight: integrate from (p, v) at t0 with the shared 1/240 s integrator."""

    id: str
    owner: str
    mine: bool
    t0: float = Field(description="backend wall clock at which p and v hold (same clock as Fleet.t)")
    p: list[float] = Field(min_length=3, max_length=3)
    v: list[float] = Field(min_length=3, max_length=3)


class ArenaLocal(_Model):
    ammo: dict[str, int]
    hp: dict[str, int]
    reloading: dict[str, bool]
    reload: dict[str, float] = Field(default_factory=dict, description="reload progress, 0..1")
    down: dict[str, bool]
    max_ammo: int
    max_hp: int
    score: ArenaScore


class ArenaMatch(_Model):
    """WP-B (07 §4): the current or the last match; times on the backend wall clock."""

    id: str = Field(description="8 hex digits")
    started_at: float
    ends_at: float
    running: bool


class ArenaView(_Model):
    t: float
    enabled: bool
    instance: str
    seat: int | None
    callsign: str
    error: str | None
    peers: list[ArenaPeer]
    shots: list[ArenaShot]
    local: ArenaLocal
    # WP-B (07 §4): refetch GET /arena/leaderboard when board_rev changes
    match: ArenaMatch | None = None
    board_rev: int = 0


class ArenaMessage(_Model):
    type: Literal["arena"]
    data: ArenaView


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
    force: bool = Field(
        default=False, description='skip the autopilot pre-arm checks; needs confirm "FORCE" (04 §2)'
    )


class ModeBody(_Body):
    name: str


class PlanStep(_Body):
    action: PlanAction
    alt: float | None = Field(default=None, description="takeoff: metres above the ground")
    seconds: float | None = Field(default=None, description="hover / hold: seconds to hold")
    forward: float | None = Field(default=None, description="move: metres forward (negative: back)")
    right: float | None = Field(default=None, description="move: metres right (negative: left)")
    up: float | None = Field(default=None, description="move: metres up (negative: down)")
    deg: float | None = Field(default=None, description="yaw: degrees, positive clockwise")
    x: float | None = None
    y: float | None = None
    z: float | None = None
    yaw: float | None = None
    roll: float | None = Field(default=None, description="sticks: -1..1")
    pitch: float | None = Field(default=None, description="sticks: -1..1")
    throttle: float | None = Field(default=None, description="sticks: 0..1")
    roll_deg: float | None = Field(default=None, description="attitude: roll in degrees, right wing down +")
    pitch_deg: float | None = Field(default=None, description="attitude: pitch in degrees, nose up +")
    yaw_rate_dps: float | None = Field(default=None, description="attitude / velocity: yaw rate, deg/s")
    thrust: float | None = Field(default=None, description="attitude: collective thrust 0..1")
    vx: float | None = Field(default=None, description="velocity: m/s east")
    vy: float | None = Field(default=None, description="velocity: m/s north")
    vz: float | None = Field(default=None, description="velocity: m/s up")


class SkillBody(_Body):
    model_config = ConfigDict(extra="allow")
    alt: float | None = None
    x: float | None = None
    y: float | None = None
    z: float | None = None
    yaw: float | None = Field(default=None, description="goto heading in degrees (0 north, clockwise)")
    forward: float | None = Field(default=None, description="move: metres forward (negative: back)")
    right: float | None = Field(default=None, description="move: metres right (negative: left)")
    up: float | None = Field(default=None, description="move: metres up (negative: down)")
    deg: float | None = Field(default=None, description="yaw: degrees, positive clockwise")
    seconds: float | None = Field(default=None, description="hold: seconds before the skill reports done")
    roll: float | None = Field(default=None, description="sticks: -1..1")
    pitch: float | None = Field(default=None, description="sticks: -1..1")
    throttle: float | None = Field(default=None, description="sticks: 0..1")
    roll_deg: float | None = Field(default=None, description="attitude: roll in degrees, right wing down +")
    pitch_deg: float | None = Field(default=None, description="attitude: pitch in degrees, nose up +")
    yaw_rate_dps: float | None = Field(default=None, description="attitude / velocity: yaw rate, deg/s")
    thrust: float | None = Field(default=None, description="attitude: collective thrust 0..1")
    vx: float | None = Field(default=None, description="velocity: m/s east")
    vy: float | None = Field(default=None, description="velocity: m/s north")
    vz: float | None = Field(default=None, description="velocity: m/s up")
    confirm: str | None = None


class PlanTuning(_Body):
    """Throttle sticks of the stick skills (aircraft without a position estimate, in altitude hold)."""

    climb: float | None = Field(
        default=None, ge=0.55, le=1.0, description="throttle stick for lift-off and climb (default 0.72)"
    )
    hover: float | None = Field(
        default=None, ge=0.35, le=0.65, description="throttle stick that holds the height (default 0.5)"
    )


class PlanBody(_Body):
    steps: list[PlanStep] = Field(min_length=1, max_length=24)
    name: str | None = Field(default=None, max_length=80)
    confirm: str | None = Field(default=None, description='"ARM" when the plan arms a real aircraft')
    tuning: PlanTuning | None = Field(default=None, description="throttle tuning, adjustable while flying")


class SequenceTrim(_Body):
    """Offsets added to every frame of the sequence (stick units); adjustable while it flies."""

    roll: float | None = Field(default=None, ge=-0.3, le=0.3)
    pitch: float | None = Field(default=None, ge=-0.3, le=0.3, description="+ forward; pull back with -")
    yaw: float | None = Field(default=None, ge=-0.3, le=0.3)
    throttle: float | None = Field(default=None, ge=-0.2, le=0.2)


class SequenceBody(_Body):
    sequence: dict[str, Any] = Field(description='an ``.ars`` document: {"format": "ars", "version": 1, ...}')
    confirm: str | None = Field(default=None, description='"ARM" when a real aircraft starts on the ground')
    speed: float = Field(default=1.0, ge=0.25, le=2.0, description="time scale")
    start_at: float = Field(default=0.0, ge=0.0, description="start this many seconds into the time line")


class SequenceEditBody(_Body):
    sequence: dict[str, Any] | None = Field(
        default=None, description="new tracks; the blocks not yet flown take effect at once"
    )
    trim: SequenceTrim | None = None


class SequenceRecordBody(_Body):
    action: Literal["start", "stop"]
    name: str | None = Field(default=None, max_length=80)


class ReflyBody(_Body):
    session_id: int
    mode: Literal["auto", "plan", "path", "sticks"] = "auto"
    speed: float = Field(default=1.0, ge=0.25, le=2.0, description="Time scale; applies to sticks only")
    confirm: str | None = Field(default=None, description='"ARM" when a real aircraft starts on the ground')


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
    position: bool = Field(default=True, description="false: no position estimate (barometer only)")
    hover_assist: bool | None = Field(
        default=None,
        description="false: physical flight, no altitude or position hold; omitted: setting sim_hover_assist",
    )
    airframe: AirframeBody | None = Field(  # WP-2a
        default=None, description="airframe defects for every new simulator; omitted: sim_airframe_default"
    )


class AirframeParamsBody(_Body):
    """Any subset of the airframe values (WP-2a, 06 §4); ranges as in ``SimAirframe.limits``."""

    mass_kg: float | None = None
    payload_kg: float | None = None
    cg_offset_cm: list[float] | None = None
    level_trim_deg: list[float] | None = None
    motor_gain: list[float] | None = None
    prop_damage: float | None = None
    damaged_prop: int | None = None
    imu_noise: float | None = None
    arm_bias_deg: float | None = None
    with_assist: bool | None = None


class AirframeBody(_Body):
    """``{preset | params, seed?}`` (WP-2a, 06 §4): a generated preset from a seed (fresh when omitted), or
    values overlaid on the named preset or on the current airframe, which makes it ``custom``."""

    preset: AirframePreset | None = None
    params: AirframeParamsBody | None = None
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)


class SimAssistBody(_Body):
    on: bool = Field(description="hover assist on (altitude and position hold) or off (physical flight)")


class SessionBody(_Body):
    name: str


class FireBody(_Body):
    """LAN arena (05 §7.4, WP-D)."""

    seconds: float = Field(default=1.0, ge=0, le=3, description="hold the trigger this long; 0 releases it")


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
    "ArenaView": ArenaView,
    "ArenaMessage": ArenaMessage,
    "SimAirframe": SimAirframe,  # WP-2a
}
