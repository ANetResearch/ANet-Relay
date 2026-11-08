"""Request bodies and response shapes of the challenge, duel, job, ladder and trace API (WP-2b, 06 §3.2-§3.4).

The trace shape (``Trace``) is the replay data contract documented in ``relay.judge``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .schemas_programs import ArenaBox, Sensors


class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow", json_schema_serialization_defaults_required=True)


# ---------------------------------------------------------------------------- bodies


class SubmitBody(_Body):
    program: str = Field(description="program id (built-in or stored)")
    rev: int | None = Field(default=None, description="revision; the newest when omitted")
    params: dict[str, Any] | None = Field(
        default=None, description="parameter overrides (types follow PARAMS)"
    )
    visible_only: bool = Field(default=False, description="run the visible seeds only (not recorded as best)")


class DuelEntrant(_Body):
    program: str
    rev: int | None = None
    params: dict[str, Any] | None = None


class DuelScenario(_Body):
    duration_s: float | None = Field(default=None, description="10-180 s, default 60")
    arena: ArenaBox | None = Field(default=None, description="default radius 8, floor 0, ceiling 4")
    airframe: Literal["ideal", "typical", "rough"] | None = Field(default=None, description="default typical")
    sensors: Sensors | None = None
    turbulence: float | None = Field(default=None, ge=0.0, le=1.0)


class DuelBody(_Body):
    programs: list[str | DuelEntrant] = Field(description="2-8 programs, each once")
    rounds: int = Field(default=3, ge=1, le=20)
    seed: int = Field(default=1, ge=0)
    scenario: DuelScenario | None = None


# ---------------------------------------------------------------------------- responses


class ChallengeEntry(_Model):
    job: str
    program: str
    rev: int
    name: str
    score: float
    passed: bool
    visible_only: bool
    seeds: list[dict[str, Any]] = Field(description="[{seed (null when hidden), hidden, score, passed}]")
    trace_id: str | None
    author: str | None
    t: float


class ChallengeSummary(_Model):
    id: str
    kind: Literal["flight", "duel"]
    name_en: str
    name_zh: str
    goal_en: str
    goal_zh: str
    duration_s: float
    visible_seeds: list[int]
    hidden_seeds: int = Field(description="number of hidden seeds")
    opponent: str | None
    best: ChallengeEntry | None
    attempts: int


class Challenge(ChallengeSummary):
    scene_en: str
    scene_zh: str
    scoring_en: str
    scoring_zh: str
    sensors: dict[str, Any]
    hover_assist: bool
    info: dict[str, Any]
    history: list[ChallengeEntry]


class SeedResult(_Model):
    seed: int | None = Field(description="null for a hidden seed")
    hidden: bool
    score: float
    passed: bool
    metrics: dict[str, Any]
    trace_id: str | None
    sim_s: float


class Speed(_Model):
    sim_s: float
    wall_s: float
    factor: float = Field(description="simulated seconds per wall second")


class ChallengeResult(_Model):
    challenge: str
    score: float
    passed: bool
    metrics: dict[str, Any]
    seeds: list[SeedResult]
    trace_id: str | None
    best: ChallengeEntry | None
    speed: Speed | None


class RoundAircraft(_Model):
    id: str
    program: str
    rev: int
    name: str
    role: str
    team: str | None
    alive: bool
    hp: int | None
    hits: int
    hits_taken: int
    survived_s: float
    airborne_s: float
    reason: Literal["shot_down", "crashed", "out"] | None
    place: int


class DuelRound(_Model):
    round: int
    seed: int
    winner: str | None
    winner_id: str | None
    draw: bool
    duration_s: float
    aircraft: list[RoundAircraft]
    trace_id: str | None


class Standing(_Model):
    program: str
    rev: int
    name: str
    rounds: int
    wins: int
    draws: int
    losses: int
    hits: int
    hits_taken: int
    survived_s: float
    mean_place: float


class LadderChange(_Model):
    program: str
    name: str
    rating: float
    delta: float


class DuelResult(_Model):
    rounds: list[DuelRound]
    standings: list[Standing]
    ladder: list[LadderChange]
    scenario: dict[str, Any] | None
    speed: Speed | None


class JobProgram(_Model):
    program: str
    rev: int
    name: str


class Job(_Model):
    id: str
    kind: Literal["challenge", "duel"]
    state: Literal["queued", "running", "done", "failed", "cancelled"]
    progress: float
    queue_position: int | None
    created: float
    started: float | None
    finished: float | None
    challenge: str | None
    programs: list[JobProgram]
    result: ChallengeResult | DuelResult | None
    error: dict[str, Any] | None = Field(description="{code, message_en, message_zh, detail} when failed")


class LadderRow(_Model):
    program: str
    name: str
    rating: float
    games: int
    wins: int
    draws: int
    losses: int
    updated: float
    rank: int


class Ladder(_Model):
    ladder: list[LadderRow]
    duels: list[dict[str, Any]]


class TraceAircraft(_Model):
    id: str
    label: str
    program: str
    rev: int
    name: str
    role: str
    team: str
    spawn: dict[str, float]
    frames: list[list[float | int | None]] = Field(description="one row per t, in the order of fields")
    states: list[list[float | str]] = Field(description="[t, state] whenever ctx.state changed")


class TraceShot(_Model):
    id: int
    shooter: str
    t0: float
    p: list[float]
    v: list[float]


class TraceEvent(_Model):
    t: float
    kind: str
    vehicle: str | None


class Trace(_Model):
    id: str
    created: float
    job: str
    version: int
    kind: Literal["challenge", "duel"]
    scenario: str
    seed: int
    hz: float
    duration_s: float
    fields: list[str]
    arena: dict[str, float] | None
    course: dict[str, Any] | None
    markers: list[dict[str, Any]]
    t: list[float]
    aircraft: list[TraceAircraft]
    shots: list[TraceShot]
    events: list[TraceEvent]
    result: dict[str, Any]


EXPORTED = {
    "ChallengeSummary": ChallengeSummary,
    "Challenge": Challenge,
    "Job": Job,
    "Ladder": Ladder,
    "Trace": Trace,
}
