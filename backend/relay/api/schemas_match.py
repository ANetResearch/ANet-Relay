"""Request bodies and response shapes of the dogfight scenario, match and leaderboard (WP-B, 07 §3-§4)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .schemas import ArenaMatch


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow", json_schema_serialization_defaults_required=True)


AirframePresetName = Literal["ideal", "typical", "rough"]


# ---------------------------------------------------------------------------- bodies


class DogfightRoles(_Body):
    gunner: int = Field(ge=0, le=12, description="aircraft that fire")
    rammer: int = Field(ge=0, le=12, description="aircraft that ram (never fire)")


class DogfightPrograms(_Body):
    gunner: str | None = Field(default=None, description="program id for gunners (default hunter_medium)")
    rammer: str | None = Field(default=None, description="program id for rammers (default rammer)")


class DogfightArena(_Body):
    radius: float = Field(default=8.0, ge=3.0, le=30.0, description="m, vertical cylinder around the centre")
    ceiling: float = Field(default=4.0, ge=2.0, le=10.0, description="m above the ground")


class DogfightBody(_Body):
    """``POST /scenarios/dogfight`` (07 §3). Simulators only; real aircraft never take part."""

    aircraft: int = Field(
        default=6, ge=2, le=12, description="simulated aircraft (at most the simulator limit)"
    )
    teams: Literal["ffa", 2, 3] = Field(default="ffa", description="free-for-all, or 2 or 3 teams")
    roles: DogfightRoles | None = Field(
        default=None, description="sum = aircraft; default 2 gunners : 1 rammer"
    )
    programs: DogfightPrograms | None = None
    arena: DogfightArena | None = None
    airframe: AirframePresetName = "typical"
    hover_assist: bool = False
    duration_s: float = Field(default=180.0, ge=30.0, le=1800.0)
    respawn: bool = Field(default=True, description="back at the spawn point 5 s after a shoot-down or crash")
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    vehicles: list[str] | None = Field(
        default=None, description="simulators to use first (sim-N); a real aircraft id is refused with 400"
    )


class MatchBody(_Body):
    duration_s: float = Field(default=180.0, ge=30.0, le=1800.0)


# ---------------------------------------------------------------------------- responses


class DogfightAircraft(_Model):
    vid: str
    role: Literal["gunner", "rammer"]
    program: str
    team: str
    state: str = Field(description="booting | arming | deploying | flying | human | down | stopped | failed")


class DogfightConfig(_Model):
    aircraft: int
    teams: Literal["ffa", 2, 3]
    roles: dict[str, int]
    programs: dict[str, str]
    arena: dict[str, float]
    airframe: str
    hover_assist: bool
    duration_s: float
    respawn: bool
    seed: int
    lan: bool = Field(description="on the LAN (the match is shared) or local practice")


class DogfightView(_Model):
    running: bool
    phase: str = Field(description="idle | booting | arming | deploying | running | ending | done")
    started_at: float | None = Field(description="backend wall clock")
    ends_at: float | None
    config: DogfightConfig | None
    aircraft: list[DogfightAircraft]
    match: ArenaMatch | None
    by: str | None = Field(description="operator, or the session that started it")
    reason: str | None = Field(description="why it ended: time | stopped | error")
    error: str | None


class LeaderboardController(_Model):
    kind: Literal["program", "agent", "human", "none"]
    name: str


class LeaderboardRow(_Model):
    """One aircraft. The statistics are null for a 0.4 peer (no statistics block)."""

    key: str = Field(description="sim-N here, <peer id>/sim-N for a peer's aircraft")
    aircraft: str
    callsign: str
    local: bool
    controller: LeaderboardController
    downs: int | None
    hits: int | None
    shots: int | None
    accuracy: float | None = Field(description="hits / shots, 0..1; null without shots")
    rams: int | None
    deaths: int | None
    crashes: int | None
    alive_s: float | None = Field(description="current survival time (at the time of the reply)")
    best_alive_s: float | None
    damage: int | None
    alive: bool | None = Field(description="flying now: alive_s keeps counting")


class Leaderboard(_Model):
    by: Literal["downs", "survival", "accuracy", "rams"]
    match: ArenaMatch | None
    rows: list[LeaderboardRow]


class MatchView(_Model):
    match: ArenaMatch | None
    ended: bool = Field(description="DELETE: a running match was ended (false: the statistics were reset)")


EXPORTED = {
    "DogfightView": DogfightView,
    "Leaderboard": Leaderboard,
}

__all__ = [
    "EXPORTED",
    "DogfightBody",
    "DogfightView",
    "Leaderboard",
    "LeaderboardRow",
    "MatchBody",
    "MatchView",
]
