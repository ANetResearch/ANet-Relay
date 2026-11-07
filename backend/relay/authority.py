"""Control authority: who may fly a vehicle (03 §4), as pure functions.

Each vehicle has two independent slots: ``claim`` (an agent session) and
``possession`` (an operator input). Possession always wins and suspends the
claim; releasing possession hands the vehicle back to a live claim. The
transitions only return the new state, events and side-effect requests; the
fleet service executes the effects. Nothing here ever sends sticks, modes or
commands on an agent's behalf.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

from .errors import RelayError, err
from .model import Claim, Controller, Possession

# Side effects the fleet service applies after a transition.
STOP_SKILL = "stop_skill"
STOP_STICKS = "stop_sticks"
POSSESS_MODE = "possess_mode"
CHECK_AIRBORNE = "check_airborne"
EMERGENCY_STOP = "emergency_stop"
ABORT_COURSE = "abort_course"


@dataclass(frozen=True)
class Authority:
    claim: Claim | None = None
    possession: Possession | None = None


@dataclass(frozen=True)
class Actor:
    kind: Literal["operator", "session"]
    session_id: str | None = None
    name: str | None = None
    input: str | None = None  # operator only: input the request claims to come from
    pad: int | None = None

    @staticmethod
    def operator(input: str | None = None, pad: int | None = None) -> Actor:
        return Actor("operator", input=input, pad=pad)

    @staticmethod
    def session(session_id: str, name: str) -> Actor:
        return Actor("session", session_id=session_id, name=name)


@dataclass
class Outcome:
    state: Authority
    events: list[tuple[str, dict]] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)


def controller(a: Authority, claim_alive: bool) -> Controller:
    if a.possession is not None:
        return "human"
    if a.claim is not None and claim_alive:
        return "agent"
    return "none"


def _claim_view(claim: Claim | None) -> dict | None:
    return None if claim is None else {"session_id": claim.session_id, "name": claim.name}


def possess(a: Authority, input: str, pad: int | None, wall: float) -> Outcome:
    """Operator possession always succeeds; another input's possession is replaced."""
    new = Possession(input=input, pad=pad if input == "gamepad" else None, since=wall)  # type: ignore[arg-type]
    replaced = a.possession
    if replaced is not None and replaced.source == new.source:
        return Outcome(a)
    data = {
        "input": new.input,
        "pad": new.pad,
        "suspended_claim": _claim_view(a.claim),
        "replaced": None if replaced is None else replaced.view(),
    }
    return Outcome(
        replace(a, possession=new),
        events=[("possession_started", data)],
        effects=[STOP_SKILL, STOP_STICKS, POSSESS_MODE],
    )


def release(a: Authority, claim_alive: bool, wall: float) -> Outcome:
    if a.possession is None:
        raise err("not_possessed")
    held_s = round(max(0.0, wall - a.possession.since), 1)
    base = {"input": a.possession.input, "pad": a.possession.pad, "possession_s": held_s}
    new = replace(a, possession=None)
    if a.claim is not None and claim_alive:
        data = {**base, "session_id": a.claim.session_id, "name": a.claim.name}
        return Outcome(new, events=[("relay_handback", data)], effects=[STOP_STICKS])
    return Outcome(new, events=[("possession_released", base)], effects=[STOP_STICKS, CHECK_AIRBORNE])


def claim(a: Authority, actor: Actor, existing_alive: bool, wall: float) -> Outcome:
    """Claim for a live session. Claiming under possession is allowed; the claim starts suspended."""
    assert actor.kind == "session" and actor.session_id and actor.name is not None
    current = a.claim
    if current is not None and current.session_id == actor.session_id:
        return Outcome(a)
    if current is not None and existing_alive:
        raise err("claimed_by_other", name=current.name, claim=_claim_view(current))
    new = Claim(session_id=actor.session_id, name=actor.name, since=wall)
    data = {"session_id": new.session_id, "name": new.name, "suspended": a.possession is not None}
    return Outcome(replace(a, claim=new), events=[("claim_started", data)])


def unclaim(a: Authority, actor: Actor) -> Outcome:
    if a.claim is None or a.claim.session_id != actor.session_id:
        raise err("not_claimed")
    return _drop_claim(a, reason="released")


def _drop_claim(a: Authority, reason: str) -> Outcome:
    assert a.claim is not None
    was_agent = a.possession is None
    data = {"session_id": a.claim.session_id, "name": a.claim.name, "reason": reason}
    effects = [STOP_STICKS, STOP_SKILL, CHECK_AIRBORNE] if was_agent else []
    return Outcome(replace(a, claim=None), events=[("claim_released", data)], effects=effects)


def session_expired(a: Authority, session_id: str) -> Outcome:
    """Drop this session's claim, if it holds one. Same effects as unclaim."""
    if a.claim is None or a.claim.session_id != session_id:
        return Outcome(a)
    return _drop_claim(a, reason="expired")


def check_control(a: Authority, actor: Actor, claim_alive: bool) -> None:
    """Raise unless ``actor`` is the vehicle's current controller."""
    if actor.kind == "operator":
        if a.possession is None:
            raise err("not_possessed")
        if actor.input is not None:
            pad = actor.pad if actor.input == "gamepad" else None
            if Possession(actor.input, pad, 0.0).source != a.possession.source:  # type: ignore[arg-type]
                raise err("not_possessed", possession=a.possession.view())
        return
    if a.possession is not None:
        if a.claim is not None and a.claim.session_id == actor.session_id:
            raise err("claim_suspended", possession=a.possession.view())
        raise err("possessed_by_human", possession=a.possession.view())
    if a.claim is None or not claim_alive:
        raise err("not_claimed")
    if a.claim.session_id != actor.session_id:
        raise err("claimed_by_other", name=a.claim.name, claim=_claim_view(a.claim))


def check_operator_or_controller(a: Authority, actor: Actor, claim_alive: bool) -> None:
    """Operator may always act (stop, course stop); an agent must be the controller."""
    if actor.kind == "operator":
        return
    check_control(a, actor, claim_alive)


def stop(a: Authority, actor: Actor, claim_alive: bool) -> Outcome:
    """Emergency stop: never changes claim or possession."""
    check_operator_or_controller(a, actor, claim_alive)
    data = {"by": actor.kind, "session_id": actor.session_id}
    return Outcome(a, events=[("stop", data)], effects=[STOP_SKILL, ABORT_COURSE, EMERGENCY_STOP])


def is_authority_error(exc: BaseException) -> bool:
    return isinstance(exc, RelayError) and exc.code in {
        "not_possessed",
        "possessed_by_human",
        "claimed_by_other",
        "not_claimed",
        "claim_suspended",
        "session_expired",
        "operator_required",
    }
