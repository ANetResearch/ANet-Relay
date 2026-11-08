"""Evaluation jobs (06 §3.4): concurrency, queue, progress, cancel, cleanup and the worker process."""

from __future__ import annotations

import sys
import threading
import time

import pytest

from relay.errors import RelayError
from relay.judge import worker
from relay.judge.headless import Cancelled
from relay.judge.jobs import JobManager
from relay.programs.store import ProgramStore

pytestmark = pytest.mark.sticks_level

STORE = ProgramStore(None)


def entrant(pid: str) -> dict:
    p = STORE.get(pid)
    return {"program": pid, "rev": 1, "name": p["name"], "source": p["source"], "params": dict(p["params"])}


def wait_for(fn, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(0.01)
    return False


class Gate:
    """A runner that blocks until released (or cancelled) and records the most jobs running at once."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.now = 0
        self.peak = 0
        self.seen: list[str] = []

    def __call__(self, spec, progress, cancel):
        with self.lock:
            self.now += 1
            self.peak = max(self.peak, self.now)
            self.seen.append(spec["name"])
        try:
            progress(0.5)
            while not self.release.wait(0.01):
                if cancel.is_set():
                    raise Cancelled()
            if spec.get("fail"):
                raise RelayError("judge_failed", detail={"reason": "test"})
            return {"value": spec["name"]}
        finally:
            with self.lock:
                self.now -= 1


def manager(gate, **kwargs):
    finished = []
    jm = JobManager(gate, lambda job, raw: {"echo": raw["value"]}, on_finished=finished.append, **kwargs)
    return jm, finished


def test_at_most_two_jobs_run_and_the_rest_queue_in_order():
    gate = Gate()
    jm, finished = manager(gate)
    views = [jm.submit("challenge", {"name": f"j{i}"}, {"challenge": "hover"}) for i in range(5)]
    assert [v["state"] for v in views[:2]] == ["running", "running"]
    assert wait_for(lambda: gate.now == 2)
    queued = [jm.view(v["id"]) for v in views[2:]]
    assert [q["state"] for q in queued] == ["queued"] * 3 and [q["queue_position"] for q in queued] == [
        1,
        2,
        3,
    ]
    assert jm.view(views[0]["id"])["progress"] == pytest.approx(0.5)
    gate.release.set()
    assert wait_for(lambda: all(jm.view(v["id"])["state"] == "done" for v in views))
    assert gate.peak == 2 and gate.seen == [f"j{i}" for i in range(5)]
    done = jm.view(views[4]["id"])
    assert done["result"] == {"echo": "j4"} and done["progress"] == 1.0 and done["challenge"] == "hover"
    assert done["finished"] >= done["started"] >= done["created"]
    assert len(finished) == 5


def test_cancel_a_queued_and_a_running_job():
    gate = Gate()
    jm, _finished = manager(gate)
    a = jm.submit("duel", {"name": "a"}, {})
    b = jm.submit("duel", {"name": "b"}, {})
    c = jm.submit("duel", {"name": "c"}, {})
    assert jm.cancel(c["id"])["state"] == "cancelled"  # off the queue, never ran
    assert wait_for(lambda: gate.now == 2)
    jm.cancel(a["id"])
    assert wait_for(lambda: jm.view(a["id"])["state"] == "cancelled")
    gate.release.set()
    assert wait_for(lambda: jm.view(b["id"])["state"] == "done")
    assert "c" not in gate.seen and jm.view(a["id"])["result"] is None
    assert jm.cancel(b["id"])["state"] == "done"  # a finished job stays as it is
    with pytest.raises(RelayError) as exc:
        jm.view("job-nope")
    assert exc.value.code == "job_not_found" and exc.value.status == 404


def test_failures_queue_limit_and_close():
    gate = Gate()
    jm, _ = manager(gate, max_queued=2)
    bad = jm.submit("challenge", {"name": "bad", "fail": True}, {})
    rest = [jm.submit("challenge", {"name": n}, {})["id"] for n in ("x", "y", "z")]
    with pytest.raises(RelayError) as exc:
        jm.submit("challenge", {"name": "w"}, {})
    assert exc.value.code == "judge_queue_full" and exc.value.status == 429
    gate.release.set()
    assert wait_for(lambda: all(jm.view(j)["state"] == "done" for j in rest))
    assert jm.view(bad["id"])["state"] == "failed"
    error = jm.view(bad["id"])["error"]
    assert error["code"] == "judge_failed" and error["message_zh"]
    gate.release.clear()
    running = jm.submit("challenge", {"name": "long"}, {})
    queued = jm.submit("challenge", {"name": "later"}, {})
    jm.submit("challenge", {"name": "later2"}, {})
    assert wait_for(lambda: jm.view(running["id"])["state"] == "running")
    jm.close()
    assert jm.view(running["id"])["state"] == "cancelled" and jm.view(queued["id"])["state"] == "cancelled"
    assert gate.now == 0
    with pytest.raises(RelayError) as exc:
        jm.submit("challenge", {"name": "after"}, {})
    assert exc.value.code == "judge_unavailable"


# ============================================================================ the worker process


def liftoff_spec(seeds=(1,)) -> dict:
    return {
        "kind": "challenge",
        "challenge": "liftoff",
        "runs": [{"seed": s, "hidden": False} for s in seeds],
        "entrant": entrant("liftoff"),
        "opponent": None,
    }


def test_the_worker_process_evaluates_like_the_backend():
    progress: list[float] = []
    out = worker.run_subprocess(liftoff_spec(), progress.append, threading.Event(), timeout_s=120.0)
    assert out["passed"] and out["seeds"][0]["metrics"]["hold_s"] >= 3.0
    assert progress and all(0.0 <= p <= 1.0 for p in progress)
    local = worker.execute(liftoff_spec())
    assert out["seeds"][0]["metrics"] == local["seeds"][0]["metrics"]  # the same flight in both processes


def test_cancel_kills_the_worker_and_errors_are_reported():
    cancel = threading.Event()
    started = time.monotonic()

    def progress(value):
        cancel.set()

    with pytest.raises(Cancelled):
        worker.run_subprocess(liftoff_spec(seeds=(1, 2, 7703, 19211)), progress, cancel, timeout_s=120.0)
    assert time.monotonic() - started < 60.0
    bad = {**liftoff_spec(), "challenge": "nope"}
    with pytest.raises(worker.WorkerError) as exc:
        worker.run_subprocess(bad, lambda v: None, threading.Event(), timeout_s=60.0)
    assert exc.value.body["code"] == "challenge_not_found" and exc.value.body["message_zh"]


def test_frozen_backend_re_executes_itself(monkeypatch):
    assert worker.worker_command()[1:] == ["-m", "relay", "--judge-worker"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert worker.worker_command() == [sys.executable, "--judge-worker"]


def test_main_dispatches_the_worker_before_the_server(monkeypatch):
    from relay import __main__ as entry
    from relay.judge import worker as workermod

    called = []
    monkeypatch.setattr(workermod, "main", lambda: called.append(True) or 0)
    assert entry.main(["--judge-worker"]) == 0 and called == [True]
