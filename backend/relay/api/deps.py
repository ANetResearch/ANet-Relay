"""Authentication, actors and rate limits for the API (03 §6.2, H8, H9).

* Operator: ``X-Relay-Operator`` header (``operator`` query parameter on the WebSocket), compared with
  ``hmac.compare_digest``. Only the Relay window holds it.
* Agent: ``X-Relay-Session`` header (``session`` query parameter on the WebSocket).
* Read-only endpoints are anonymous.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Request

from ..authority import Actor
from ..errors import err
from ..fleet import FleetService
from ..sessions import Session

OPERATOR_HEADER = "X-Relay-Operator"
SESSION_HEADER = "X-Relay-Session"


def service(request: Request) -> FleetService:
    return request.app.state.service


def operator_valid(svc: FleetService, token: str | None) -> bool:
    expected = svc.operator_token
    if not token or not expected:
        return False
    return hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))


@dataclass
class Caller:
    operator: bool
    session: Session | None


def caller(request: Request, klass: str = "other") -> Caller:
    """Resolve the caller from headers; raises for invalid tokens and rate-limited sessions."""
    svc = service(request)
    op_token = request.headers.get(OPERATOR_HEADER)
    session_token = request.headers.get(SESSION_HEADER)
    if op_token:
        if not operator_valid(svc, op_token):
            raise err("invalid_operator_token")
        return Caller(operator=True, session=None)
    if session_token:
        session = svc.sessions.lookup(session_token)
        if session is None:
            raise err("session_expired")
        request.state.session = session
        if not svc.sessions.allow(session, klass):
            raise err("rate_limited", klass=klass)
        svc.sessions.renew(session)
        return Caller(operator=False, session=session)
    raise err("auth_required")


def require_operator(request: Request) -> None:
    who = caller(request)
    if not who.operator:
        raise err("operator_required")


def require_app_control(request: Request) -> None:
    """Application operations (04 §3): the operator, or an agent session while agents_app_control is on."""
    who = caller(request)
    if who.operator:
        return
    if who.session is None:
        raise err("operator_required")
    if not service(request).settings.agents_app_control:
        raise err("app_control_disabled")


def require_session(request: Request, klass: str = "other") -> Session:
    who = caller(request, klass)
    if who.session is None:
        raise err("session_required")
    return who.session


def actor(
    request: Request, klass: str = "control", input: str | None = None, pad: int | None = None
) -> Actor:
    """The operator or the agent session behind this request."""
    who = caller(request, klass)
    if who.operator:
        return Actor.operator(input, pad)
    assert who.session is not None
    return Actor.session(who.session.session_id, who.session.name)
