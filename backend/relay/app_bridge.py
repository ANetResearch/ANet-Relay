"""Application-level channel between the API and the Relay window (04 §3).

The renderer reports its ``UiState`` over the operator WebSocket and executes ``ui_command`` messages
the API pushes back. This object is the rendezvous: the latest state and its age, the connection that
should receive commands, and the futures of commands waiting for their ``ui_result``. It is touched by
FastAPI handler threads, the fleet thread and the WebSocket event loop, so every access takes the lock;
nothing blocks while holding it.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from .errors import err

UI_WAIT_S = 5.0
STALE_S = 15.0  # no ui_state for this long: the window is treated as gone (heartbeat is every 5 s)


class Connection:
    """One operator WebSocket able to receive ``ui_command`` messages."""

    def __init__(
        self, conn: str, loop: asyncio.AbstractEventLoop, send: Callable[[dict[str, Any]], Any]
    ) -> None:
        self.conn = conn
        self.loop = loop
        self._send = send  # coroutine function taking the message dict

    def push(self, message: dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(self._send(message), self.loop)


class AppBridge:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._state: dict[str, Any] | None = None
        self._state_at = 0.0
        self._active: Connection | None = None
        self._pending: dict[str, tuple[Future, str]] = {}  # command id -> (future, connection)

    # ------------------------------------------------------------------ renderer side

    def update(self, conn: Connection, state: dict[str, Any]) -> None:
        """The newest ``ui_state`` wins: its connection receives commands from now on."""
        with self._lock:
            self._state = state
            self._state_at = self._clock()
            self._active = conn

    def resolve(self, command_id: str, ok: bool, error: str | None, data: Any) -> bool:
        with self._lock:
            entry = self._pending.pop(command_id, None)
        if entry is None:
            return False
        if not entry[0].done():
            entry[0].set_result({"ok": ok, "error": error, "data": data})
        return True

    def closed(self, conn_id: str) -> None:
        """A WebSocket closed: forget its state and fail the commands it never answered."""
        with self._lock:
            if self._active is not None and self._active.conn == conn_id:
                self._active = None
                self._state = None
            orphans = [(k, f) for k, (f, c) in self._pending.items() if c == conn_id]
            for key, _ in orphans:
                self._pending.pop(key, None)
        for _, future in orphans:
            if not future.done():
                future.set_result({"ok": False, "error": "window_closed", "data": None})

    # ------------------------------------------------------------------ API side

    def view(self) -> dict[str, Any]:
        """``{connected, age_s, state}`` for GET /app and GET /app/ui."""
        with self._lock:
            state, at, active = self._state, self._state_at, self._active
        if state is None or active is None:
            return {"connected": False, "age_s": None, "state": None}
        age = max(0.0, self._clock() - at)
        return {"connected": age <= STALE_S, "age_s": round(age, 2), "state": state}

    def command(self, action: str, args: dict[str, Any], by: str, timeout: float | None = None) -> Any:
        """Send a ``ui_command`` and wait for its result. Raises ``ui_unavailable`` / ``ui_timeout`` /
        ``ui_action_failed``; returns the ``data`` of a successful result."""
        with self._lock:
            conn = self._active
            fresh = self._state is not None and self._clock() - self._state_at <= STALE_S
            if conn is None or not fresh:
                raise err("ui_unavailable")
            command_id = str(uuid.uuid4())
            future: Future = Future()
            self._pending[command_id] = (future, conn.conn)
        try:
            conn.push({"type": "ui_command", "id": command_id, "action": action, "args": args, "by": by})
            result = future.result(timeout=UI_WAIT_S if timeout is None else timeout)
        except FutureTimeout as exc:
            raise err("ui_timeout", action=action) from exc
        finally:
            with self._lock:
                self._pending.pop(command_id, None)
        if not result["ok"]:
            raise err("ui_action_failed", error=result.get("error") or "unknown", action=action)
        return result.get("data") or {}
