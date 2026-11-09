"""Request bodies and response shapes of the firmware-twin API (11 §3-§6, §10)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .schemas import FirmwareFamily, FrameBody, HomeBody, MatchLevel, TwinState


class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow", json_schema_serialization_defaults_required=True)


# ---------------------------------------------------------------------------- runtime packs (11 §3)


class AutopilotPack(_Model):
    id: str = Field(description="ardupilot-copter-<version> or px4-<version>")
    family: FirmwareFamily
    vehicle: Literal["copter"]
    version: str
    git: str = Field(description="source commit, 8 to 40 hex digits")
    platform: Literal["linux-x64", "mac-arm64", "mac-x64", "win-x64"]
    size_bytes: int
    license: str
    source: str = Field(description="URL of the source tag")
    built: str = Field(description="official (upstream binary) or ci")
    origin: Literal["bundled", "imported"] = Field(
        description="installed with Relay, or imported (deletable)"
    )
    sha256: str | None = Field(description="digest of the pack's tarball, when the index lists it")
    usable: bool = Field(description="the pack can run on this computer")
    problem: str | None = Field(description="why it cannot (English)")
    problem_zh: str | None
    twins: int = Field(default=0, description="firmware twins running on this pack (GET /autopilots)")


class AutopilotPacks(_Model):
    """``GET /autopilots``: the packs of this platform, bundled first."""

    platform: Literal["linux-x64", "mac-arm64", "mac-x64", "win-x64"] | None
    bundled_dir: str
    imported_dir: str | None
    packs: list[AutopilotPack]


class AutopilotImportBody(_Body):
    path: str = Field(description="a local <id>-<platform>.tar.gz")
    sha256: str | None = Field(
        default=None, description="expected digest; omitted: <path>.sha256 or an index*.json beside the file"
    )


# ---------------------------------------------------------------------------- identity (11 §4)


class FirmwareIdentity(_Model):
    family: FirmwareFamily | None
    vehicle: str | None
    frame: str | None = Field(description="quad / hexa / ... from HEARTBEAT type")
    version: str | None
    type: Literal["dev", "alpha", "beta", "rc", "official"] | None
    git: str | None
    board: str | None
    board_id: int | None = Field(description="ArduPilot bootloader board id, PX4 USB product id")
    identified: bool = Field(description="AUTOPILOT_VERSION received")


class FirmwareCandidate(_Model):
    pack: str
    version: str
    git: str
    match: MatchLevel


class FirmwareMatch(_Model):
    recommended: str | None = Field(description="pack id; null when no pack of this firmware is installed")
    match: MatchLevel
    warning: bool = Field(description="series and below: the control loop may differ from the aircraft's")
    warning_en: str | None
    warning_zh: str | None
    candidates: list[FirmwareCandidate] = Field(
        description="every usable pack of the same firmware, best first"
    )


class ParamProgress(_Model):
    received: int
    total: int
    complete: bool


class VehicleFirmware(_Model):
    """``GET /vehicles/{id}/firmware``."""

    id: str
    kind: Literal["real", "sim"]
    label: str
    firmware: str | None = Field(description="the snapshot's version string")
    identity: FirmwareIdentity
    params: ParamProgress
    frame_problem: str | None = Field(description="why a twin cannot fly this frame; null for quad X")
    frame_problem_zh: str | None
    match: FirmwareMatch


# ---------------------------------------------------------------------------- profiles (11 §5)


class TwinFrame(_Model):
    preset: Literal["micro-120", "small-250", "large-450"]
    mass_kg: float
    wheelbase_m: float
    prop_in: float
    cells: int
    capacity_mah: float


class TwinHome(_Model):
    lat: float
    lon: float
    alt_m: float


class TwinProfileFirmware(_Model):
    family: FirmwareFamily
    vehicle: str | None
    version: str | None
    type: str | None
    git: str | None
    board: str | None
    board_id: int | None = None


class TwinProfileSource(_Model):
    vehicle: str
    label: str


class TwinProfileSummary(_Model):
    id: str = Field(description="tp-<8 hex>")
    name: str
    captured_at: float
    updated_at: float
    source: TwinProfileSource
    firmware: TwinProfileFirmware
    pack: str | None = Field(description="the runtime pack the twin starts")
    match: MatchLevel
    frame: TwinFrame
    home: TwinHome | None
    params_total: int
    params_applied: int
    params_excluded: int


class ExcludedParam(_Model):
    name: str
    reason_en: str
    reason_zh: str


class TwinProfile(TwinProfileSummary):
    """``GET /twins/profiles/{id}`` (also the export): every parameter and the ones a twin leaves out."""

    params: dict[str, float]
    excluded: list[ExcludedParam]


class TwinProfiles(_Model):
    profiles: list[TwinProfileSummary]


class TwinProfileCaptureBody(_Body):
    from_vehicle: str = Field(description="a connected real aircraft whose parameter table is read")
    name: str | None = Field(default=None, max_length=64)
    frame: FrameBody | None = Field(default=None, description="omitted: a preset guessed from the battery")
    profile: str | None = Field(default=None, description="recapture into this profile (name and frame kept)")


class TwinProfilePatch(_Body):
    name: str | None = Field(default=None, max_length=64)
    frame: FrameBody | None = None
    pack: str | None = Field(default=None, description="an installed pack of the same firmware")
    home: HomeBody | None = Field(default=None, description="null clears it")


# ---------------------------------------------------------------------------- twin simulators (11 §6)


class SimFrameView(_Model):
    preset: str
    mass_kg: float
    wheelbase_m: float
    prop_in: float
    cells: int
    capacity_mah: float
    thrust_to_weight: float = Field(description="derived from the hover thrust parameter on a twin")


class SimFrame(_Model):
    """``PUT /sims/{id}/frame``."""

    id: str
    engine: Literal["relay", "sitl"]
    frame: SimFrameView


class TwinFrameView(SimFrameView):
    inertia: list[float] = Field(description="[Ixx, Iyy, Izz] kg m^2")


class TwinFirmwareRef(_Model):
    family: FirmwareFamily
    version: str
    pack: str
    match: MatchLevel | None


class TwinParams(_Model):
    applied: int = Field(description="profile parameters the twin takes")
    excluded: list[ExcludedParam] = Field(description="profile parameters it leaves out, with the reason")
    forced: dict[str, float] = Field(description="values Relay sets for the lockstep (SIM_RATE_HZ)")


class TwinExit(_Model):
    code: int | None
    lines: list[str] = Field(description="the last 40 lines of the firmware's output")


class TwinLockstep(_Model):
    steps: int = Field(description="250 Hz world steps")
    stalls: int = Field(description="steps the firmware did not answer within the tick's wait")
    frames: int = Field(description="firmware frames (ArduPilot JSON frames, PX4 HIL exchanges)")
    ms_per_step: float | None = Field(description="wall time per world step, ms")


class TwinProfileRef(_Model):
    id: str
    name: str | None


class SimTwin(_Model):
    """``GET /sims/{id}/twin``."""

    id: str
    label: str
    engine: Literal["sitl"]
    state: TwinState
    pack: AutopilotPack
    firmware: TwinFirmwareRef
    profile: TwinProfileRef | None
    instance: int | None = Field(description="n of the loopback ports (11 §2)")
    pid: int | None
    home: TwinHome
    frame: TwinFrameView
    params: TwinParams
    ports: dict[str, int] | None
    boot_s: float | None
    restarts: int
    exit: TwinExit | None
    lockstep: TwinLockstep


class Deleted(_Model):
    id: str
    deleted: bool


EXPORTED: dict[str, Any] = {
    "AutopilotPacks": AutopilotPacks,
    "AutopilotPack": AutopilotPack,
    "VehicleFirmware": VehicleFirmware,
    "TwinProfiles": TwinProfiles,
    "TwinProfile": TwinProfile,
    "SimTwin": SimTwin,
    "SimFrame": SimFrame,
}
