"""Authority transitions and invariants as pure functions (03 §4.2, §4.3)."""

import pytest

from relay import authority as auth
from relay.authority import Actor, Authority
from relay.errors import RelayError

A = Actor.session("s-a", "harness-a")
B = Actor.session("s-b", "harness-b")
OP = Actor.operator()


def claimed() -> Authority:
    return auth.claim(Authority(), A, False, 1.0).state


def test_claim_and_controller():
    out = auth.claim(Authority(), A, False, 1.0)
    assert out.events[0][0] == "claim_started"
    assert auth.controller(out.state, True) == "agent"
    assert auth.controller(out.state, False) == "none"


def test_claim_conflict_and_dead_claim_takeover():
    a = claimed()
    with pytest.raises(RelayError) as exc:
        auth.claim(a, B, True, 2.0)
    assert exc.value.code == "claimed_by_other"
    out = auth.claim(a, B, False, 2.0)  # the old session is dead
    assert out.state.claim.session_id == "s-b"


def test_possession_always_succeeds_and_suspends_claim():
    a = claimed()
    out = auth.possess(a, "gamepad", 1, 3.0)
    assert out.state.possession.source == "gamepad:1"
    assert out.state.claim is not None
    assert auth.controller(out.state, True) == "human"
    assert auth.STOP_SKILL in out.effects and auth.POSSESS_MODE in out.effects
    assert out.events[0][1]["suspended_claim"]["name"] == "harness-a"
    # Another input takes over from the first one.
    out2 = auth.possess(out.state, "keyboard", None, 4.0)
    assert out2.state.possession.source == "keyboard"
    assert out2.events[0][1]["replaced"]["input"] == "gamepad"


def test_release_hands_back_to_live_claim_without_side_effect_commands():
    a = auth.possess(claimed(), "keyboard", None, 3.0).state
    out = auth.release(a, True, 5.0)
    assert out.events[0][0] == "relay_handback"
    assert out.state.possession is None and out.state.claim is not None
    # Invariant 3: the backend sends nothing on the agent's behalf at hand-back.
    assert out.effects == [auth.STOP_STICKS]


def test_release_without_claim_checks_airborne():
    a = auth.possess(Authority(), "keyboard", None, 1.0).state
    out = auth.release(a, False, 2.0)
    assert out.events[0][0] == "possession_released"
    assert auth.CHECK_AIRBORNE in out.effects


def test_release_requires_possession():
    with pytest.raises(RelayError) as exc:
        auth.release(Authority(), False, 1.0)
    assert exc.value.code == "not_possessed"


def test_unclaim_effects_only_when_agent_was_flying():
    out = auth.unclaim(claimed(), A)
    assert set(out.effects) >= {auth.STOP_STICKS, auth.STOP_SKILL}
    suspended = auth.possess(claimed(), "keyboard", None, 2.0).state
    assert auth.unclaim(suspended, A).effects == []
    with pytest.raises(RelayError):
        auth.unclaim(claimed(), B)


def test_session_expiry_drops_only_that_claim():
    out = auth.session_expired(claimed(), "s-a")
    assert out.state.claim is None and out.events[0][1]["reason"] == "expired"
    assert auth.session_expired(claimed(), "s-b").state.claim is not None


@pytest.mark.parametrize(
    ("state", "actor", "code"),
    [
        (Authority(), OP, "not_possessed"),
        (Authority(), A, "not_claimed"),
        ("claimed", B, "claimed_by_other"),
        ("possessed_claimed", A, "claim_suspended"),
        ("possessed_claimed", B, "possessed_by_human"),
    ],
)
def test_control_rejections(state, actor, code):
    if state == "claimed":
        state = claimed()
    elif state == "possessed_claimed":
        state = auth.possess(claimed(), "keyboard", None, 1.0).state
    with pytest.raises(RelayError) as exc:
        auth.check_control(state, actor, True)
    assert exc.value.code == code


def test_operator_input_must_match_possession():
    a = auth.possess(Authority(), "gamepad", 2, 1.0).state
    auth.check_control(a, Actor.operator("gamepad", 2), False)
    auth.check_control(a, Actor.operator(), False)
    with pytest.raises(RelayError):
        auth.check_control(a, Actor.operator("gamepad", 1), False)


def test_stop_never_changes_claim_or_possession():
    a = auth.possess(claimed(), "keyboard", None, 1.0).state
    out = auth.stop(a, OP, True)
    assert out.state == a  # invariant 4
    assert auth.EMERGENCY_STOP in out.effects
    with pytest.raises(RelayError):
        auth.stop(a, A, True)  # an agent may only stop what it controls
    b = claimed()
    assert auth.stop(b, A, True).state == b


def test_possession_is_idempotent_for_same_input():
    a = auth.possess(Authority(), "keyboard", None, 1.0).state
    out = auth.possess(a, "keyboard", None, 2.0)
    assert out.events == [] and out.state == a
