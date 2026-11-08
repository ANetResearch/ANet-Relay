"""Evaluation jobs (06 §3.4): a queue with at most ``MAX_RUNNING`` jobs running, each in its own worker.

A job is ``queued`` until a slot is free, then ``running`` (with ``progress`` 0..1 from the worker), then
``done``, ``failed`` or ``cancelled``. Cancelling a queued job takes it off the queue; cancelling a running
one kills its worker process. Finished jobs stay readable until ``KEEP`` newer ones have finished. Closing
the manager (backend shutdown) cancels everything and waits for the workers to exit.

The runner is ``runner(spec, progress, cancel_event) -> result``; the default starts a worker process
(``worker.run_subprocess``). ``on_done(job, result)`` turns the worker's result into the job's result (it
stores traces and records) on the job's thread; an exception there fails the job.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..errors import RelayError, err
from .headless import Cancelled
from .worker import WorkerError

log = logging.getLogger("relay.judge")

MAX_RUNNING = 2
MAX_QUEUED = 16
KEEP = 100
ACTIVE = ("queued", "running")


@dataclass
class Job:
    id: str
    kind: str
    spec: dict[str, Any]
    meta: dict[str, Any]
    created: float
    state: str = "queued"
    progress: float = 0.0
    started: float | None = None
    finished: float | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None

    def view(self, position: int | None = None) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "progress": round(self.progress, 4),
            "queue_position": position,
            "created": self.created,
            "started": self.started,
            "finished": self.finished,
            **self.meta,
            "result": self.result,
            "error": self.error,
        }


class JobManager:
    def __init__(
        self,
        runner: Callable[[dict[str, Any], Callable[[float], None], threading.Event], dict[str, Any]],
        on_done: Callable[[Job, dict[str, Any]], dict[str, Any]],
        *,
        max_running: int = MAX_RUNNING,
        max_queued: int = MAX_QUEUED,
        wall: Callable[[], float] = time.time,
        on_finished: Callable[[Job], None] | None = None,
    ) -> None:
        self.runner = runner
        self.on_done = on_done
        self.on_finished = on_finished or (lambda _job: None)
        self.max_running = max_running
        self.max_queued = max_queued
        self.wall = wall
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._queue: list[str] = []
        self._closed = False

    # ------------------------------------------------------------------ queries

    def _position(self, job: Job) -> int | None:
        return self._queue.index(job.id) + 1 if job.id in self._queue else None

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise err("job_not_found", job=job_id)
            return job

    def view(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self.get(job_id)
            return job.view(self._position(job))

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: -j.created)
            return [j.view(self._position(j)) for j in jobs]

    @property
    def running(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.state == "running")

    # ------------------------------------------------------------------ changes

    def submit(self, kind: str, spec: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise err("judge_unavailable", reason="shutting down")
            if len(self._queue) >= self.max_queued:
                raise err("judge_queue_full", limit=self.max_queued)
            job = Job("job-" + secrets.token_hex(6), kind, spec, meta, self.wall())
            self._jobs[job.id] = job
            self._queue.append(job.id)
            self._pump()
            return job.view(self._position(job))

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self.get(job_id)
            if job.state == "queued":
                self._queue.remove(job.id)
                self._end(job, "cancelled")
            elif job.state == "running":
                job.cancel.set()
            return job.view(self._position(job))

    def close(self, timeout: float = 5.0) -> None:
        with self._lock:
            self._closed = True
            for jid in list(self._queue):
                self._end(self._jobs[jid], "cancelled")
            self._queue.clear()
            threads = [j.thread for j in self._jobs.values() if j.state == "running" and j.thread]
            for j in self._jobs.values():
                j.cancel.set()
        for t in threads:
            t.join(timeout)

    def _pump(self) -> None:
        while self._queue and self.running < self.max_running and not self._closed:
            job = self._jobs[self._queue.pop(0)]
            job.state = "running"
            job.started = self.wall()
            job.thread = threading.Thread(target=self._run, args=(job,), name=f"judge-{job.id}", daemon=True)
            job.thread.start()

    def _end(self, job: Job, state: str) -> None:
        job.state = state
        job.finished = self.wall()
        if state == "done":
            job.progress = 1.0
        self._forget()
        try:
            self.on_finished(job)
        except Exception:  # a listener never breaks the queue
            log.exception("job listener failed")

    def _forget(self) -> None:
        done = sorted(
            (j for j in self._jobs.values() if j.state not in ACTIVE), key=lambda j: j.finished or 0
        )
        for j in done[: max(0, len(done) - KEEP)]:
            self._jobs.pop(j.id, None)

    def _run(self, job: Job) -> None:
        def progress(value: float) -> None:
            job.progress = max(job.progress, min(0.999, float(value)))

        state = "done"
        try:
            raw = self.runner(job.spec, progress, job.cancel)
            if job.cancel.is_set():
                raise Cancelled()
            job.result = self.on_done(job, raw)
        except Cancelled:
            state = "cancelled"
        except RelayError as exc:
            state = "failed"
            job.error = exc.body()["error"]
        except WorkerError as exc:
            state = "failed"
            body = exc.body
            code = str(body.get("code") or "judge_failed")
            detail = dict(body.get("detail") or {})
            if body.get("message_en"):
                job.error = {
                    "code": code,
                    "message_en": body["message_en"],
                    "message_zh": body.get("message_zh") or body["message_en"],
                    "detail": detail,
                }
            else:
                job.error = err(code, **detail).body()["error"]
        except Exception as exc:  # pragma: no cover - a bug in the evaluator
            log.exception("job %s failed", job.id)
            state = "failed"
            job.error = err("judge_failed", reason=f"{type(exc).__name__}: {exc}"[:200]).body()["error"]
        with self._lock:
            self._end(job, state)
            self._pump()


__all__ = ["KEEP", "MAX_QUEUED", "MAX_RUNNING", "Job", "JobManager"]
