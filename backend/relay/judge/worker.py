"""The evaluation worker process (06 §3.4): one per job, started by ``jobs.JobManager``.

The backend starts it as ``<python> -m relay --judge-worker`` (frozen: ``<backend executable>
--judge-worker``; ``relay/__main__.py`` dispatches before the server modules are imported) with the program
host's minimal environment (no tokens, ``PYTHONHASHSEED=0``) and no inherited handles. On POSIX it limits
its address space and refuses file writes (``RLIMIT_FSIZE`` 0); it opens no socket (``listen=False``) and
reads no file but the code. Programs run in its own ``InProcessHost`` engine, with the host's language rules
and per-step interrupt.

Protocol: the backend writes one line of JSON (the job spec) to stdin and keeps the pipe open; the worker
exits when stdin closes. The worker writes one JSON object per line to stdout:
``{"type": "progress", "value": 0..1}``, then ``{"type": "result", "result": {...}}`` or
``{"type": "error", "code", "message_en", "message_zh", "detail"}``. Logging goes to stderr.

Job spec::

    {"kind": "challenge", "challenge": id, "runs": [{"seed": n, "hidden": bool}, ...],
     "entrant": entrant, "opponent": entrant | null}
    {"kind": "duel", "entrants": [entrant, ...], "rounds": n, "seed": n, "scenario": {...} | null}
    entrant = {"program", "rev", "name", "source", "params"}

``execute(spec, progress, cancelled)`` is the same work in the calling process (tests, and the in-process
runner of ``JobManager``).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..errors import RelayError, err
from ..programs.hostclient import host_env
from .headless import Cancelled
from .scenario import DuelSettings, Slot

log = logging.getLogger("relay.judge")

MEMORY_LIMIT = 1024 * 1024 * 1024
TIMEOUT_FACTOR = 1.0  # wall seconds allowed per simulated second, on top of TIMEOUT_BASE_S
TIMEOUT_BASE_S = 60.0


def _slot(raw: dict[str, Any]) -> Slot:
    return Slot(
        program=str(raw["program"]),
        rev=int(raw.get("rev") or 1),
        name=str(raw.get("name") or raw["program"]),
        source=str(raw["source"]),
        params=dict(raw.get("params") or {}),
    )


def _merge_metrics(seeds: list[dict[str, Any]]) -> dict[str, Any]:
    """Numbers averaged over the seeds, flags true when any seed had them; other values from the first."""
    if not seeds:
        return {}
    out: dict[str, Any] = {}
    for key, first in seeds[0]["metrics"].items():
        values = [s["metrics"].get(key) for s in seeds]
        if isinstance(first, bool):
            out[key] = any(bool(v) for v in values)
        elif isinstance(first, (int, float)) and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in values
        ):
            out[key] = round(sum(values) / len(values), 3)
        elif first is None and all(v is None for v in values):
            out[key] = None
        else:
            out[key] = first
    return out


def _speed(sim_s: float, wall_s: float) -> dict[str, float]:
    return {
        "sim_s": round(sim_s, 3),
        "wall_s": round(wall_s, 3),
        "factor": round(sim_s / max(wall_s, 1e-6), 1),
    }


def execute(
    spec: dict[str, Any],
    progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Run a job spec; raises ``Cancelled`` or ``RelayError``."""
    from . import challenges, duels

    report = progress or (lambda _f: None)
    kind = spec.get("kind")
    if kind == "challenge":
        challenge = challenges.get(str(spec.get("challenge")))
        if challenge is None:
            raise err("challenge_not_found", challenge=str(spec.get("challenge")))
        runs = list(spec.get("runs") or [])
        seeds, traces = [], []
        sim_s = wall_s = 0.0
        for i, item in enumerate(runs):
            player = _slot(spec["entrant"])
            opponent = _slot(spec["opponent"]) if spec.get("opponent") else None

            def part(f: float, i: int = i) -> None:
                report((i + f) / len(runs))

            out = challenges.run_seed(
                challenge, int(item["seed"]), player, opponent, cancelled=cancelled, progress=part
            )
            hidden = bool(item.get("hidden"))
            res = out["result"]
            entry: dict[str, Any] = {
                "seed": None if hidden else int(item["seed"]),
                "hidden": hidden,
                "score": res["score"],
                "passed": res["passed"],
                "metrics": res["metrics"],
                "trace": None,
                "sim_s": out["sim_s"],
            }
            if not hidden:
                trace = out["trace"]
                trace.update(kind="challenge", challenge=challenge.id)
                entry["trace"] = len(traces)
                traces.append(trace)
            seeds.append(entry)
            sim_s += out["sim_s"]
            wall_s += out["wall_s"]
        scores = [s["score"] for s in seeds]
        return {
            "kind": "challenge",
            "challenge": challenge.id,
            "score": round(sum(scores) / len(scores), 1) if scores else 0.0,
            "passed": bool(seeds) and all(s["passed"] for s in seeds),
            "metrics": _merge_metrics(seeds),
            "seeds": seeds,
            "traces": traces,
            "speed": _speed(sim_s, wall_s),
        }
    if kind == "duel":
        settings = DuelSettings.parse(spec.get("scenario"))
        entrants = [_slot(e) for e in spec.get("entrants") or []]
        out = duels.run_duel(
            entrants,
            int(spec.get("rounds") or 1),
            int(spec.get("seed") or 0),
            settings,
            cancelled=cancelled,
            progress=report,
        )
        for trace in out["traces"]:
            trace["kind"] = "duel"
        return {
            "kind": "duel",
            "rounds": out["rounds"],
            "standings": out["standings"],
            "scenario": settings.view(),
            "traces": out["traces"],
            "speed": _speed(out["sim_s"], out["wall_s"]),
        }
    raise err("invalid_request", reason="unknown job kind")


# ============================================================================ the process


def worker_command() -> list[str]:
    """The frozen backend re-executes itself; a source checkout runs ``-m relay``."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--judge-worker"]
    return [sys.executable, "-m", "relay", "--judge-worker"]


def _harden() -> None:
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):
        pass
    try:
        import resource  # POSIX only
    except ImportError:
        return
    if hasattr(signal, "SIGXFSZ"):
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)  # a refused write raises instead of killing
    for name, limit in (("RLIMIT_AS", MEMORY_LIMIT), ("RLIMIT_FSIZE", 0)):
        which = getattr(resource, name, None)
        if which is None:
            continue
        try:
            resource.setrlimit(which, (limit, limit))
        except (ValueError, OSError):
            pass


def _lifeline(stream: Any) -> None:
    try:
        while stream.read(4096):
            pass
    except (OSError, ValueError):
        pass
    os._exit(3)  # the backend went away


def main() -> int:
    """Entry for ``--judge-worker``."""
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    _harden()
    stdin = sys.stdin.buffer
    out = sys.stdout
    line = stdin.readline()
    threading.Thread(target=_lifeline, args=(stdin,), name="judge-lifeline", daemon=True).start()
    lock = threading.Lock()

    def send(payload: dict[str, Any]) -> None:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with lock:
            out.write(text + "\n")
            out.flush()

    last = [0.0]

    def progress(value: float) -> None:
        now = time.monotonic()
        if now - last[0] >= 0.2:
            last[0] = now
            send({"type": "progress", "value": round(min(1.0, max(0.0, value)), 4)})

    try:
        spec = json.loads(line.decode("utf-8"))
        result = execute(spec, progress)
    except RelayError as exc:
        send({"type": "error", **exc.body()["error"]})
        return 1
    except Exception as exc:  # report and exit; the backend marks the job failed
        log.exception("evaluation failed")
        send(
            {
                "type": "error",
                "code": "judge_failed",
                "message_en": f"The evaluation failed: {type(exc).__name__}.",
                "message_zh": f"评测失败：{type(exc).__name__}。",
                "detail": {"reason": str(exc)[:200]},
            }
        )
        return 1
    send({"type": "result", "result": result})
    return 0


class WorkerError(Exception):
    def __init__(self, body: dict[str, Any]) -> None:
        super().__init__(body.get("message_en") or body.get("code") or "worker error")
        self.body = body


def run_subprocess(
    spec: dict[str, Any],
    progress: Callable[[float], None],
    cancelled: threading.Event,
    timeout_s: float | None = None,
    command: list[str] | None = None,
) -> dict[str, Any]:
    """Run ``spec`` in a worker process; raises ``Cancelled`` or ``WorkerError``."""
    env = host_env()
    env.pop("RELAY_PROGRAM_HOST", None)
    env["RELAY_JUDGE_WORKER"] = "1"
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    cwd = str(Path(env.get("PYTHONPATH") or Path(sys.executable).parent))
    try:
        proc = subprocess.Popen(
            command or worker_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=cwd,
            close_fds=True,
            **kwargs,
        )
    except OSError as exc:
        raise WorkerError({"code": "judge_unavailable", "detail": {"reason": str(exc)}}) from exc
    lines: queue.Queue[bytes | None] = queue.Queue()
    errors: list[str] = []

    def read() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            lines.put(raw)
        lines.put(None)

    def drain() -> None:
        assert proc.stderr is not None
        for raw in proc.stderr:
            text = raw.decode("utf-8", "replace").rstrip()
            if text:
                errors.append(text)
                del errors[:-20]
                log.warning("judge worker: %s", text[:300])

    threading.Thread(target=read, name="judge-worker-out", daemon=True).start()
    threading.Thread(target=drain, name="judge-worker-err", daemon=True).start()
    deadline = time.monotonic() + (timeout_s or 3600.0)
    try:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(spec, ensure_ascii=False).encode("utf-8") + b"\n")
        proc.stdin.flush()
        while True:
            if cancelled.is_set():
                raise Cancelled()
            if time.monotonic() > deadline:
                raise WorkerError({"code": "judge_timeout", "detail": {}})
            try:
                raw = lines.get(timeout=0.1)
            except queue.Empty:
                continue
            if raw is None:
                tail = " | ".join(errors[-3:]) or f"exit code {proc.poll()}"
                raise WorkerError({"code": "judge_failed", "detail": {"reason": tail[:300]}})
            try:
                msg = json.loads(raw.decode("utf-8"))
            except ValueError:
                continue
            if msg.get("type") == "progress":
                progress(float(msg.get("value") or 0.0))
            elif msg.get("type") == "result":
                return msg["result"]
            elif msg.get("type") == "error":
                raise WorkerError(msg)
    finally:
        with contextlib.suppress(OSError, ValueError):
            proc.stdin.close()  # type: ignore[union-attr]
        if proc.poll() is None:
            proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=2.0)


def timeout_for(spec: dict[str, Any]) -> float:
    """Wall-time limit of a job: generous (the worker is expected at many times real time)."""
    from . import challenges

    if spec.get("kind") == "challenge":
        challenge = challenges.get(str(spec.get("challenge")))
        sim = (challenge.duration_s if challenge else 60.0) * len(spec.get("runs") or [1])
    else:
        sim = float((spec.get("scenario") or {}).get("duration_s") or 60.0) * int(spec.get("rounds") or 1)
        sim *= max(1, len(spec.get("entrants") or [])) / 2.0
    return TIMEOUT_BASE_S + TIMEOUT_FACTOR * sim * 2.0


def run_inprocess(
    spec: dict[str, Any], progress: Callable[[float], None], cancelled: threading.Event
) -> dict[str, Any]:
    """``JobManager`` runner that evaluates in the calling process (tests; no isolation)."""
    return execute(spec, progress, cancelled.is_set)


__all__ = [
    "WorkerError",
    "execute",
    "main",
    "run_inprocess",
    "run_subprocess",
    "timeout_for",
    "worker_command",
]
