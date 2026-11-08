"""The backend's side of the program host (06 §2.4): start the process, speak the framed protocol, enforce the
batch deadline. ``InProcessHost`` offers the same interface around an in-process ``Engine`` (tests,
headless evaluators that are already isolated processes).

Interface used by ``service.ProgramService`` (and later packages):

* ``start()`` / ``close()`` / ``alive``
* ``load(key, source, params, seed) -> {ok, error?, state, logs}`` (blocking, ``LOAD_TIMEOUT_S``)
* ``unload(key)``, ``set_params(key, params)``
* ``step(items, deadline_s) -> StepReply``: ``items`` = ``[(key, obs_dict), ...]``; ``StepReply.results``
  maps each answered key to its result, ``timed_out`` is set when the batch missed the deadline and
  ``running`` names the program that was executing then (the first without a result)
* ``run_tests(source, params, tests, seed, timeout_s)`` (one-shot process for ``HostClient``)
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import host as proto

log = logging.getLogger("relay.programs")

START_TIMEOUT_S = 10.0
LOAD_TIMEOUT_S = 1.0
REQUEST_TIMEOUT_S = 1.0
TEST_TIMEOUT_S = 10.0
STDERR_KEEP = 40


def host_command() -> list[str]:
    """How to start the host: the frozen backend re-executes itself, a source checkout runs ``-m relay``."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--program-host"]
    return [sys.executable, "-m", "relay", "--program-host"]


def host_env() -> dict[str, str]:
    """A minimal environment: no tokens, no lifeline, deterministic hashing."""
    env = {
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "RELAY_PROGRAM_HOST": "1",
    }
    if not getattr(sys, "frozen", False):
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    for name in ("SYSTEMROOT", "SystemRoot", "WINDIR", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL"):
        if name in os.environ:
            env[name] = os.environ[name]
    return env


class HostError(Exception):
    """The host is not available (did not start, died, or did not answer)."""


@dataclass
class StepReply:
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    timed_out: bool = False
    running: str | None = None
    dead: bool = False
    elapsed_ms: float = 0.0


class HostClient:
    """One host process. Every call is made from one thread (the fleet thread)."""

    def __init__(self, command: list[str] | None = None, env: dict[str, str] | None = None) -> None:
        self.command = command or host_command()
        self.env = env or host_env()
        self.proc: subprocess.Popen | None = None
        self.hello: dict[str, Any] | None = None
        self._frames: queue.Queue[tuple[int, dict[str, Any]] | None] = queue.Queue()
        self._seq = 0
        self._busy: int | None = None  # id of a STEP whose DONE has not arrived
        self._stuck: str | None = None  # the program that was running when that STEP timed out
        self.stderr: list[str] = []
        self.pid: int | None = None

    # ------------------------------------------------------------------ process

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def busy(self) -> bool:
        return self._busy is not None

    def start(self) -> None:
        self.close()
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        cwd = str(Path(self.env.get("PYTHONPATH") or Path(sys.executable).parent))
        try:
            self.proc = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.env,
                cwd=cwd,
                close_fds=True,
                **kwargs,
            )
        except OSError as exc:
            raise HostError(f"cannot start: {exc}") from exc
        self.pid = self.proc.pid
        self._frames = queue.Queue()
        self._busy = None
        threading.Thread(
            target=self._read, args=(self.proc, self._frames), name="program-host-out", daemon=True
        ).start()
        threading.Thread(target=self._drain, args=(self.proc,), name="program-host-err", daemon=True).start()
        deadline = time.monotonic() + START_TIMEOUT_S
        frame = self._next(deadline)
        if frame is None or frame[0] != proto.HELLO:
            self.close()
            raise HostError("did not start: " + (" | ".join(self.stderr[-3:]) or "no answer"))
        self.hello = frame[1]
        log.info("program host %s started (%s interrupts)", self.pid, self.hello.get("interrupt"))

    def close(self) -> None:
        proc = self.proc
        self.proc = None
        self._busy = None
        if proc is None:
            return
        with contextlib.suppress(OSError, ValueError):
            proc.stdin.close()  # type: ignore[union-attr]
        try:
            proc.wait(timeout=0.3)
        except subprocess.TimeoutExpired:
            proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=1.0)

    def kill(self) -> None:
        proc = self.proc
        self.proc = None
        self._busy = None
        if proc is not None and proc.poll() is None:
            proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=1.0)

    @staticmethod
    def _read(proc: subprocess.Popen, out: queue.Queue) -> None:
        stream = proc.stdout
        try:
            while True:
                frame = proto.read_frame(stream)
                if frame is None:
                    break
                out.put(frame)
        except (OSError, ValueError):
            pass
        out.put(None)

    def _drain(self, proc: subprocess.Popen) -> None:
        try:
            for raw in proc.stderr:  # type: ignore[union-attr]
                line = raw.decode("utf-8", "replace").rstrip()
                if line:
                    self.stderr = [*self.stderr[-STDERR_KEEP:], line]
                    log.warning("program host: %s", line[:300])
        except (OSError, ValueError):
            pass

    def _next(self, deadline: float) -> tuple[int, dict[str, Any]] | bool | None:
        """The next frame; ``False`` on timeout, ``None`` when the host is gone."""
        remaining = deadline - time.monotonic()
        try:
            return (
                self._frames.get(timeout=max(0.0, remaining)) if remaining > 0 else self._frames.get_nowait()
            )
        except queue.Empty:
            return False

    def _send(self, kind: int, payload: dict[str, Any]) -> int:
        if not self.alive:
            raise HostError("not running")
        self._seq += 1
        payload = {**payload, "id": self._seq}
        try:
            self.proc.stdin.write(proto.encode(kind, payload))  # type: ignore[union-attr]
            self.proc.stdin.flush()  # type: ignore[union-attr]
        except (OSError, ValueError) as exc:
            raise HostError(f"write failed: {exc}") from exc
        return self._seq

    def _drop_stale(self, deadline: float) -> None:
        """Wait for the DONE of a late batch before a new request (the host answers in order)."""
        while self._busy is not None:
            frame = self._next(deadline)
            if frame is False:
                raise HostError("busy")
            if frame is None:
                self._busy = None
                raise HostError("exited")
            if frame[0] == proto.DONE and frame[1].get("id") == self._busy:
                self._busy = None

    def request(
        self, kind: int, payload: dict[str, Any], timeout: float = REQUEST_TIMEOUT_S
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        self._drop_stale(deadline)
        rid = self._send(kind, payload)
        while True:
            frame = self._next(deadline)
            if frame is False:
                raise HostError("no answer")
            if frame is None:
                raise HostError("exited")
            if frame[0] == proto.REPLY and frame[1].get("id") == rid:
                return frame[1]

    # ------------------------------------------------------------------ operations

    def load(self, key: str, source: str, params: dict[str, Any], seed: int) -> dict[str, Any]:
        return self.request(
            proto.LOAD, {"key": key, "source": source, "params": params, "seed": seed}, LOAD_TIMEOUT_S
        )

    def unload(self, key: str) -> None:
        with contextlib.suppress(HostError):
            self.request(proto.UNLOAD, {"key": key})

    def set_params(self, key: str, params: dict[str, Any]) -> bool:
        return bool(self.request(proto.PARAMS, {"key": key, "params": params}).get("ok"))

    def step(self, items: list[tuple[str, dict[str, Any]]], deadline_s: float, hard_ms: float) -> StepReply:
        reply = StepReply()
        started = time.monotonic()
        deadline = started + deadline_s
        try:
            self._drop_stale(time.monotonic())  # a late batch still running: this batch times out too
            rid = self._send(
                proto.STEP,
                {
                    "items": [{"key": k, "obs": o} for k, o in items],
                    "hard_ms": hard_ms,
                    "budget_ms": max(1.0, deadline_s * 1000.0 - 1.0),
                },
            )
        except HostError as exc:
            reply.timed_out = str(exc) == "busy"
            reply.dead = not reply.timed_out
            # still busy: the program a late batch was stuck in; died between batches: nobody to blame
            reply.running = self._stuck if reply.timed_out else None
            return reply
        self._busy = rid
        while True:
            frame = self._next(deadline)
            if frame is False:
                reply.timed_out = True
                break
            if frame is None:
                reply.dead = True
                self._busy = None
                break
            kind, msg = frame
            if msg.get("id") != rid:
                continue
            if kind == proto.RESULT:
                reply.results[str(msg.get("key"))] = msg
            elif kind == proto.DONE:
                self._busy = None
                break
        if reply.timed_out or reply.dead:
            reply.running = next((k for k, _ in items if k not in reply.results), None)
            self._stuck = reply.running if reply.timed_out else None
        reply.elapsed_ms = (time.monotonic() - started) * 1000.0
        return reply

    def run_tests(
        self, source: str, params: dict[str, Any], tests: list[dict[str, Any]], seed: int, timeout_s: float
    ) -> dict[str, Any]:
        return self.request(
            proto.TEST, {"source": source, "params": params, "tests": tests, "seed": seed}, timeout_s
        )


def run_tests_isolated(
    source: str,
    params: dict[str, Any],
    tests: list[dict[str, Any]],
    seed: int = 0,
    timeout_s: float = TEST_TIMEOUT_S,
) -> dict[str, Any]:
    """Unit tests (06 §3.1) in a one-shot host process, apart from the live host."""
    client = HostClient()
    client.start()
    try:
        return client.run_tests(source, params, tests, seed, timeout_s)
    finally:
        client.kill()


class InProcessHost:
    """``HostClient``'s interface around an in-process ``Engine`` (no deadline enforcement beyond the
    engine's per-step interrupt)."""

    def __init__(self) -> None:
        self.engine: proto.Engine | None = None
        self.hello = {"interrupt": "in-process"}
        self.pid = None

    @property
    def alive(self) -> bool:
        return self.engine is not None

    busy = False

    def start(self) -> None:
        self.engine = proto.Engine()

    def close(self) -> None:
        self.engine = None

    kill = close

    def _engine(self) -> proto.Engine:
        if self.engine is None:
            raise HostError("not running")
        return self.engine

    def load(self, key: str, source: str, params: dict[str, Any], seed: int) -> dict[str, Any]:
        return self._engine().load(key, source, params, seed)

    def unload(self, key: str) -> None:
        if self.engine is not None:
            self.engine.unload(key)

    def set_params(self, key: str, params: dict[str, Any]) -> bool:
        return self._engine().set_params(key, params)

    def step(self, items: list[tuple[str, dict[str, Any]]], deadline_s: float, hard_ms: float) -> StepReply:
        engine = self._engine()
        reply = StepReply()
        started = time.monotonic()
        for key, obs in items:
            reply.results[key] = engine.step(key, obs, hard_ms)
        reply.elapsed_ms = (time.monotonic() - started) * 1000.0
        return reply

    def run_tests(
        self, source: str, params: dict[str, Any], tests: list[dict[str, Any]], seed: int, timeout_s: float
    ) -> dict[str, Any]:
        return {"ok": True, **self._engine().run_tests(source, params, tests, seed)}


__all__ = [
    "HostClient",
    "HostError",
    "InProcessHost",
    "StepReply",
    "host_command",
    "host_env",
    "run_tests_isolated",
]
