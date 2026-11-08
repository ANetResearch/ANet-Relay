"""The backend's side of the program host (06 §2.4): start the process, speak the framed protocol, enforce the
batch deadline. ``InProcessHost`` offers the same interface around an in-process ``Engine`` (tests,
headless evaluators that are already isolated processes).

Interface used by ``service.ProgramService`` (and later packages):

* ``start()`` / ``close()`` / ``kill()`` / ``discard()`` (kill without waiting) / ``alive``
* ``load(key, source, params, seed) -> {ok, error?, state, logs}`` (blocking, ``LOAD_TIMEOUT_S``; the service
  calls it off the fleet thread)
* ``unload(key)``, ``set_params(key, params)``
* ``step(items, deadline_s, hard_ms) -> StepReply``: ``items`` = ``[(key, obs_dict[, params]), ...]``;
  ``StepReply.results`` maps each answered key to its result, ``timed_out`` is set when the batch missed the
  deadline and ``running`` names the program that was executing then (the first without a result).
  ``begin_step`` / ``finish_step`` split it so that the fleet thread drives every aircraft's host at once.
* ``run_tests(source, params, tests, seed, timeout_s)`` (one-shot process for ``HostClient``)
* ``killed_for``: ``"memory"`` once the watchdog killed the host for its resident memory
  (``MEMORY_LIMIT_BYTES``, read from outside the host by ``procmem.rss`` on every platform)
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
from . import procmem

log = logging.getLogger("relay.programs")

START_TIMEOUT_S = 10.0
LOAD_TIMEOUT_S = 5.0
REQUEST_TIMEOUT_S = 1.0
TEST_TIMEOUT_S = 10.0
STDERR_KEEP = 40
MEMORY_LIMIT_BYTES = proto.MEMORY_LIMIT
MEMORY_POLL_S = 0.05


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


@dataclass
class PendingStep:
    """A batch sent by ``begin_step`` and collected by ``finish_step`` (``reply``: nothing was sent)."""

    keys: list[str]
    started: float
    deadline: float
    rid: int | None = None
    reply: StepReply | None = None


def _item(item: tuple[Any, ...]) -> dict[str, Any]:
    out = {"key": item[0], "obs": item[1]}
    if len(item) > 2 and item[2]:
        out["params"] = item[2]
    return out


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
        self.killed_for: str | None = None
        self.memory_limit = MEMORY_LIMIT_BYTES

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
        if sys.platform == "win32":  # below the backend's priority, as os.nice does on POSIX (host._harden)
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0
            )
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
        self.killed_for = None
        if self.memory_limit:
            threading.Thread(
                target=self._watch_memory, args=(self.proc,), name="program-host-mem", daemon=True
            ).start()
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

    def discard(self) -> None:
        """Kill the host and reap it on a worker thread: the fleet thread never waits for a host."""
        proc = self.proc
        self.proc = None
        self._busy = None
        if proc is None:
            return
        with contextlib.suppress(OSError):
            if proc.poll() is None:
                proc.kill()

        def reap() -> None:
            with contextlib.suppress(subprocess.TimeoutExpired, OSError):
                proc.wait(timeout=5.0)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                with contextlib.suppress(OSError, ValueError, AttributeError):
                    stream.close()  # type: ignore[union-attr]

        threading.Thread(target=reap, name="program-host-reap", daemon=True).start()

    def _watch_memory(self, proc: subprocess.Popen) -> None:
        """Kill ``proc`` when its resident memory passes ``memory_limit`` (every platform, from outside)."""
        while proc.poll() is None:
            used = procmem.rss(proc)
            if used is None:
                return  # no reading on this platform
            if used > self.memory_limit:
                self.killed_for = "memory"
                log.warning("program host %s uses %d MB; killed", proc.pid, used // (1024 * 1024))
                with contextlib.suppress(OSError):
                    proc.kill()
                return
            time.sleep(MEMORY_POLL_S)

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

    def step(self, items: list[tuple[Any, ...]], deadline_s: float, hard_ms: float) -> StepReply:
        return self.finish_step(self.begin_step(items, deadline_s, hard_ms))

    def begin_step(self, items: list[tuple[Any, ...]], deadline_s: float, hard_ms: float) -> PendingStep:
        """Send one batch without waiting; ``items`` = ``[(key, obs[, params]), ...]``."""
        started = time.monotonic()
        pending = PendingStep([str(i[0]) for i in items], started, started + deadline_s)
        try:
            self._drop_stale(time.monotonic())  # a late batch still running: this batch times out too
            pending.rid = self._send(
                proto.STEP,
                {
                    "items": [_item(i) for i in items],
                    "hard_ms": hard_ms,
                    "budget_ms": max(1.0, deadline_s * 1000.0 - 1.0),
                },
            )
        except HostError as exc:
            reply = StepReply()
            reply.timed_out = str(exc) == "busy"
            reply.dead = not reply.timed_out
            # still busy: the program a late batch was stuck in; died between batches: nobody to blame
            reply.running = self._stuck if reply.timed_out else None
            pending.reply = reply
            return pending
        self._busy = pending.rid
        return pending

    def finish_step(self, pending: PendingStep) -> StepReply:
        """Collect a batch until its deadline."""
        if pending.reply is not None:
            return pending.reply
        reply = StepReply()
        rid, deadline, started = pending.rid, pending.deadline, pending.started
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
            reply.running = next((k for k in pending.keys if k not in reply.results), None)
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

    killed_for = None

    def start(self) -> None:
        self.engine = proto.Engine()

    def close(self) -> None:
        self.engine = None

    kill = close
    discard = close

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

    def step(self, items: list[tuple[Any, ...]], deadline_s: float, hard_ms: float) -> StepReply:
        engine = self._engine()
        reply = StepReply()
        started = time.monotonic()
        for item in items:
            key, obs = item[0], item[1]
            reply.results[key] = engine.step(key, obs, hard_ms, item[2] if len(item) > 2 else None)
        reply.elapsed_ms = (time.monotonic() - started) * 1000.0
        return reply

    def begin_step(self, items: list[tuple[Any, ...]], deadline_s: float, hard_ms: float) -> PendingStep:
        """Runs the batch at once (``step``, which subclasses may override); ``finish_step`` returns it."""
        now = time.monotonic()
        pending = PendingStep([str(i[0]) for i in items], now, now + deadline_s)
        pending.reply = self.step(items, deadline_s, hard_ms)
        return pending

    def finish_step(self, pending: PendingStep) -> StepReply:
        return pending.reply or StepReply()

    def run_tests(
        self, source: str, params: dict[str, Any], tests: list[dict[str, Any]], seed: int, timeout_s: float
    ) -> dict[str, Any]:
        return {"ok": True, **self._engine().run_tests(source, params, tests, seed)}


__all__ = [
    "HostClient",
    "HostError",
    "InProcessHost",
    "PendingStep",
    "StepReply",
    "host_command",
    "host_env",
    "run_tests_isolated",
]
