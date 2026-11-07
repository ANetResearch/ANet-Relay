"""WebSocket ``/api/v1/stream`` (03 §10.2).

Server -> client: ``{"type": "hello", ...}`` once, then ``{"type": "fleet", "data": snapshot}`` at 10 Hz
and ``{"type": "event", "data": event}`` as events happen. Client -> server: ``{"type": "sticks",
"vehicle", "roll", "pitch", "yaw", "throttle"}`` (needs operator or session auth) and ``{"type": "ping"}``.
Closing the socket stops every stick stream it was feeding. Operator sockets also carry the application
channel (04 §3.1): the window sends ``ui_state`` and ``ui_result``, the server pushes ``ui_command``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from ..app_bridge import Connection
from ..authority import Actor
from ..config import SNAPSHOT_HZ, VERSION
from ..errors import RelayError, err
from .deps import operator_valid
from .origins import origin_allowed
from .schemas_app import UiState

router = APIRouter()
QUEUE_LIMIT = 2000
ERROR_INTERVAL_S = 1.0


@router.websocket("/stream")
async def stream(ws: WebSocket) -> None:
    svc = ws.app.state.service
    origin = ws.headers.get("origin")
    if origin is not None and not origin_allowed(origin):
        await ws.close(code=4403)
        return
    op_token = ws.query_params.get("operator")
    session_token = ws.query_params.get("session")
    operator = operator_valid(svc, op_token) if op_token else False
    session = svc.sessions.lookup(session_token) if session_token else None
    if (op_token and not operator) or (session_token and session is None):
        await ws.close(code=4401)
        return
    await ws.accept()
    conn = "ws-" + secrets.token_hex(6)
    loop = asyncio.get_running_loop()
    events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_LIMIT)

    def on_event(event: dict[str, Any]) -> None:
        def put() -> None:
            if not events.full():
                events.put_nowait(event)

        loop.call_soon_threadsafe(put)

    svc.events.add_listener(on_event)
    await ws.send_text(
        json.dumps(
            {
                "type": "hello",
                "version": VERSION,
                "operator": operator,
                "session": session.session_id if session else None,
            }
        )
    )
    sender = asyncio.create_task(_send_loop(ws, svc, events))
    bridge = ws.app.state.app_bridge

    async def push(message: dict[str, Any]) -> None:
        await ws.send_text(json.dumps(message, ensure_ascii=False))

    link = Connection(conn, loop, push)
    last_error: dict[str, float] = {}
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                await _send_error(ws, err("invalid_request", reason="message is not JSON"), last_error)
                continue
            kind = msg.get("type") if isinstance(msg, dict) else None
            if kind == "ping":
                await ws.send_text(json.dumps({"type": "pong", "t": time.time()}))
            elif kind == "sticks":
                await _sticks(ws, svc, msg, operator, session, conn, last_error)
            elif kind in ("ui_state", "ui_result"):
                await _ui_message(ws, bridge, link, msg, kind, operator, last_error)
            else:
                await _send_error(ws, err("invalid_request", reason="unknown message type"), last_error)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        svc.events.remove_listener(on_event)
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await sender
        bridge.closed(conn)
        await run_in_threadpool(svc.ws_closed, conn)


async def _sticks(ws, svc, msg, operator, session, conn, last_error) -> None:
    vehicle = msg.get("vehicle")
    try:
        if operator:
            who = Actor.operator(msg.get("input"), msg.get("pad"))
        elif session is not None:
            if not svc.sessions.alive(session.session_id):
                raise err("session_expired")
            if not svc.sessions.allow(session, "control"):
                raise err("rate_limited", klass="control")
            svc.sessions.renew(session)
            who = Actor.session(session.session_id, session.name)
        else:
            raise err("auth_required")
        if not isinstance(vehicle, str):
            raise err("invalid_request", reason="vehicle is required")
        await run_in_threadpool(svc.sticks, vehicle, who, msg, conn)
    except RelayError as exc:
        await _send_error(ws, exc, last_error, ref=vehicle)


async def _ui_message(ws, bridge, link, msg, kind, operator, last_error) -> None:
    """``ui_state`` and ``ui_result`` from the Relay window (04 §3.1); operator sockets only."""
    if not operator:
        await _send_error(ws, err("operator_required"), last_error)
        return
    if kind == "ui_state":
        try:
            state = UiState.model_validate(msg.get("data"))
        except ValidationError as exc:
            reason = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:3])
            await _send_error(ws, err("invalid_request", reason=f"ui_state {reason}"), last_error)
            return
        bridge.update(link, state.model_dump(mode="json"))
        return
    command_id = msg.get("id")
    if not isinstance(command_id, str) or not isinstance(msg.get("ok"), bool):
        await _send_error(ws, err("invalid_request", reason="ui_result needs id and ok"), last_error)
        return
    error = msg.get("error")
    data = msg.get("data")
    bridge.resolve(command_id, msg["ok"], error if isinstance(error, str) else None, data)


async def _send_error(ws: WebSocket, exc: RelayError, last_error: dict[str, float], ref: Any = None) -> None:
    now = time.monotonic()
    if now - last_error.get(exc.code, -1e9) < ERROR_INTERVAL_S:
        return
    last_error[exc.code] = now
    payload = exc.body()
    payload["type"] = "error"
    payload["ref"] = ref
    await ws.send_text(json.dumps(payload))


async def _send_loop(ws: WebSocket, svc, events: asyncio.Queue) -> None:
    period = 1.0 / SNAPSHOT_HZ
    next_fleet = 0.0
    last_t = None
    while True:
        now = time.monotonic()
        timeout = max(0.0, next_fleet - now)
        try:
            event = await asyncio.wait_for(events.get(), timeout=timeout)
            await ws.send_text(json.dumps({"type": "event", "data": event}, ensure_ascii=False))
            continue
        except TimeoutError:
            pass
        next_fleet = time.monotonic() + period
        snapshot = svc.fleet()
        if snapshot.get("t") != last_t:
            last_t = snapshot.get("t")
            await ws.send_text(json.dumps({"type": "fleet", "data": snapshot}, ensure_ascii=False))
