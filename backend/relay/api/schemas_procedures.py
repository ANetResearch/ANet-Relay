"""Request bodies and response shapes of the bench procedure API (WP-B, 05 §4.4)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow", json_schema_serialization_defaults_required=True)


StepKind = Literal["auto", "operator", "operator_device", "device", "flight"]
StepState = Literal["pending", "running", "waiting", "done", "skipped", "aborted"]
StepVerdict = Literal["pass", "warn", "fail", "skipped"]
RunState = Literal["running", "waiting", "passed", "warned", "failed", "aborted"]


# ---------------------------------------------------------------------------- catalogue


class CatalogueStep(_Model):
    id: str
    title_en: str
    title_zh: str
    kind: StepKind


class ProcedureEntry(_Model):
    id: str
    title_en: str
    title_zh: str
    steps: list[CatalogueStep]
    est_s: int = Field(description="rough duration in seconds")


class ProcedureCatalogue(_Model):
    procedures: list[ProcedureEntry]


# ---------------------------------------------------------------------------- run view


class ProcedureMetric(_Model):
    key: str
    label_en: str
    label_zh: str
    value: Any = None
    unit: str
    level: Literal["ok", "warn", "bad", "na", "info"]
    mean: float | None = None
    peak: float | None = None
    warn: float | None = None
    bad: float | None = None


class ProcedureFinding(_Model):
    code: str
    level: Literal["info", "warn", "fail"]
    text_en: str
    text_zh: str


class ProcedureSuggestion(_Model):
    kind: Literal["param", "relay"]
    name: str
    current: float | None
    suggested: float | None
    direction: Literal["increase", "decrease", "set"]
    change_pct: float | None
    reason_en: str
    reason_zh: str
    state: Literal["proposed", "applied", "kept", "failed", "unavailable"]


class PromptAnswer(_Model):
    id: str
    label_en: str
    label_zh: str
    tone: Literal["primary", "default", "danger"]


class ProcedurePrompt(_Model):
    id: str = Field(
        description="props_removed, motor_check, calibrate_gyro, arm_start, apply_param, paused, ..."
    )
    text_en: str
    text_zh: str
    detail_en: str | None
    detail_zh: str | None
    answers: list[PromptAnswer]
    confirm_word: str | None = Field(description="word the answer must carry on this aircraft, if any")
    operator_only: bool = Field(description="only the operator (the Relay window) may answer")
    data: dict[str, Any] = Field(description="motor layout, motor, sensor, item to apply, blocking checks")


class ProcedureStep(_Model):
    id: str
    title_en: str
    title_zh: str
    description_en: str
    description_zh: str
    kind: StepKind
    state: StepState
    verdict: StepVerdict | None
    metrics: list[ProcedureMetric]
    findings: list[ProcedureFinding]
    suggestions: list[ProcedureSuggestion]
    prompt: ProcedurePrompt | None = None
    activity_en: str | None = None
    activity_zh: str | None = None
    live: dict[str, list[float | None]] | None = Field(default=None, description="recent points, 5 Hz")
    started: float | None
    ended: float | None


class RunActor(_Model):
    kind: Literal["operator", "session"]
    name: str | None


class ProcedureRun(_Model):
    run_id: int
    procedure: str
    title_en: str
    title_zh: str
    vehicle: str
    label: str | None
    kind: Literal["real", "sim"] | None
    host: str | None
    autopilot: str | None
    firmware: str | None
    simulated: bool
    state: RunState
    step: str | None
    index: int
    total: int
    verdict: Literal["pass", "warn", "fail"] | None
    reason: str | None
    reason_en: str | None
    reason_zh: str | None
    paused: bool
    by: RunActor
    options: dict[str, Any]
    steps: list[ProcedureStep]
    started: float
    ended: float | None


class TestRunCounts(_Model):
    pass_: int = Field(alias="pass")
    warn: int
    fail: int
    skipped: int


class TestRunSummary(_Model):
    run_id: int
    vehicle: str
    host: str | None
    label: str | None = None
    kind: Literal["real", "sim"] | None = None
    procedure: str
    title_en: str | None = None
    title_zh: str | None = None
    state: RunState
    verdict: Literal["pass", "warn", "fail"] | None
    started: float
    ended: float | None
    counts: TestRunCounts
    total: int


class TestRuns(_Model):
    runs: list[TestRunSummary]


# ---------------------------------------------------------------------------- bodies


class ProcedureOptions(_Body):
    hover_s: float | None = Field(default=None, description="hover length of S9, 5 to 60 s")
    skip: list[str] | None = Field(default=None, description="step ids to skip (S12 cannot be skipped)")
    auto_answer: bool = Field(
        default=False, description="simulated aircraft: S6 answered from the servo outputs"
    )


class ProcedureStartBody(_Body):
    procedure: Literal["full_bench", "preflight_quick", "motor_check", "vibration_check", "hover_tune"]
    options: ProcedureOptions | None = None


class ProcedureAnswerBody(_Body):
    step: str = Field(description="id of the step that waits, e.g. S6")
    answer: str = Field(description="one of the prompt's answer ids")
    value: Any = None
    confirm: str | None = Field(default=None, description="the prompt's confirm word on a real aircraft")


EXPORTED = {
    "ProcedureCatalogue": ProcedureCatalogue,
    "ProcedureRun": ProcedureRun,
    "TestRuns": TestRuns,
}
