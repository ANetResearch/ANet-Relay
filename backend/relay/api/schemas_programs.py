"""Request bodies and response shapes of the onboard-program API (WP-1, 06 §2, §3.1)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .schemas import AirframeBody  # WP-2a


class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow", json_schema_serialization_defaults_required=True)


# ---------------------------------------------------------------------------- bodies


class ProgramTest(BaseModel):
    """One unit test (06 §3.1): fixed observation in, expected output ranges."""

    model_config = ConfigDict(extra="ignore")
    name: str | None = None
    obs: dict[str, Any] = Field(default_factory=dict, description="observation fields; the rest hover at 1 m")
    mem: dict[str, Any] | None = None
    state: str | None = None
    params: dict[str, Any] | None = None
    expect: dict[str, Any] = Field(
        default_factory=dict, description="roll / pitch / yaw / throttle: [low, high]; fire: bool; state: str"
    )


class LinePatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    start: int = Field(description="first line replaced, 1-based")
    end: int = Field(description="last line replaced (start - 1 inserts before start)")
    text: str = ""


class ProgramCreateBody(_Body):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    source: str | None = None
    params: dict[str, Any] | None = None
    tests: list[ProgramTest] | None = None
    note: str | None = None
    copy_of: str | None = Field(default=None, description="id of a program (built-in or not) to copy")


class ProgramUpdateBody(_Body):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    source: str | None = None
    patch: list[LinePatch] | None = Field(default=None, description="line edits instead of a full source")
    params: dict[str, Any] | None = None
    tests: list[ProgramTest] | None = None
    note: str | None = None


class ValidateBody(_Body):
    source: str | None = Field(default=None, description="validate this text instead of the stored source")


class ProgramTestBody(_Body):
    rev: int | None = None
    source: str | None = None
    tests: list[ProgramTest] | None = None
    seed: int | None = None


class Sensors(BaseModel):
    model_config = ConfigDict(extra="ignore")
    baro: bool | None = None
    position: Literal["none", "uwb", "gps"] | None = None
    contacts: bool = True


class ArenaBox(BaseModel):
    model_config = ConfigDict(extra="ignore")
    radius: float
    floor: float
    ceiling: float


class DeployBody(_Body):
    program: str
    rev: int | None = None
    params: dict[str, Any] | None = None
    mode: Literal["pilot", "copilot"] = "pilot"
    sensors: Sensors | None = Field(default=None, description="simulator scenario sensors (06 §3.3)")
    arena: ArenaBox | None = None
    seed: int | None = Field(default=None, ge=0, description="ctx.rng and sensor noise; random when omitted")
    # WP-2a (06 §3.3, §4): simulator airframe for this run; never visible to the program
    airframe: AirframeBody | None = Field(
        default=None, description="simulators only: airframe defects fitted before the program starts"
    )
    # 08 §5: the aircraft's team in other aircraft's contact lists, and the program's team parameter
    team: str | None = Field(
        default=None, max_length=16, description="team name; filled into a declared team parameter"
    )


class AllowBody(_Body):
    allow: bool
    confirm: str | None = Field(default=None, description="PROGRAM to allow")


# ---------------------------------------------------------------------------- responses


class ProgramSummary(_Model):
    id: str
    name: str
    name_zh: str
    description: str
    description_zh: str
    builtin: bool
    rev: int
    author: str | None
    created: float
    updated: float
    test_count: int = Field(description="number of unit tests")


class Program(ProgramSummary):
    source: str
    params: dict[str, Any]
    tests: list[dict[str, Any]]


class ProgramRevision(_Model):
    rev: int
    source: str
    params: dict[str, Any]
    author: str
    note: str
    t: float


class ValidationProblem(_Model):
    line: int
    col: int
    end_line: int
    end_col: int
    code: str
    message_en: str
    message_zh: str


class Validation(_Model):
    ok: bool
    errors: list[ValidationProblem]
    params: dict[str, Any]
    param_ranges: dict[str, list[float]]
    has_init: bool
    has_step: bool


class TestResult(_Model):
    name: str
    passed: bool
    out: dict[str, Any] | None = Field(description="roll, pitch, yaw, throttle, fire")
    state: str | None
    error: str | None
    failures: list[dict[str, Any]] = Field(description="[{field, expected, actual}]")


class TestResults(_Model):
    program: str
    results: list[TestResult]
    passed: int
    total: int


class ProgramLogLine(_Model):
    seq: int
    t: float
    level: Literal["log", "error", "event"]
    text: str


class ProgramLog(_Model):
    id: str
    program: str
    lines: list[ProgramLogLine]
    last_seq: int


EXPORTED = {
    "ProgramSummary": ProgramSummary,
    "Program": Program,
    "ProgramRevision": ProgramRevision,
    "ProgramValidation": Validation,
    "ProgramTestResults": TestResults,
    "ProgramLog": ProgramLog,
}
