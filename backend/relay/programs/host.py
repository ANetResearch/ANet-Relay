"""The program host (06 §2.4): a separate process that runs one deployment's program.

The backend starts one per deployment, so programs share nothing; the protocol still carries several keys.

The backend starts it as ``<python> -m relay --program-host`` (frozen: ``<backend executable>
--program-host``; ``relay/__main__.py`` dispatches before importing the server) with a minimal
environment, no inherited handles and pipes on stdin / stdout. On POSIX it then limits its own address
space (``RLIMIT_AS`` 512 MB), CPU time (``RLIMIT_CPU``, a soft limit that moves forward only while the
host keeps answering) and file size (``RLIMIT_FSIZE`` 0), lowers its priority (``os.nice``), ignores SIGINT
and caps the recursion depth. The backend also watches its resident memory from outside on every platform
(``hostclient``, ``procmem``).

Protocol (fixed framing): every message is an 8-byte header ``b"RP"``, version (u8), type (u8), payload
length (u32 little-endian), followed by that many bytes of UTF-8 JSON. Requests carry an ``id``; the host
answers each with ``REPLY {id, ...}``, except ``STEP``, which yields one ``RESULT {id, key, ...}`` frame per
program as soon as it ran (so a host that hangs in the middle of a batch still tells which program is
running) and then ``DONE {id}``. ``HELLO`` is sent unasked once at start.

Budgets: each ``step`` has a 2 ms soft budget (``over`` in the result) and a hard per-step interrupt
(``hard_ms``, from the batch); module load and ``init`` 100 ms; a unit test step 50 ms. The interrupt is a
``BaseException`` that ``except Exception`` cannot catch, raised from SIGALRM on the main thread on POSIX
and through ``PyThreadState_SetAsyncExc`` from a watchdog thread otherwise. Persistent state per program
(module globals and ``ctx.mem``, including what its functions close over, iterators and suspended generators)
is measured once a second and the program is dropped above ``STATE_LIMIT_BYTES``. Live parameter changes
arrive with a ``STEP`` item (``params``) as well as with ``PARAMS``.

``Engine`` is the execution core; ``main()`` wraps it in the pipe protocol. The backend may also use an
``Engine`` in its own process (``service.InProcessHost``, tests and headless evaluators).
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import signal
import struct
import sys
import threading
import time
import traceback
from typing import Any

from .lib import Ctx, make_obs, program_globals, sticks_values
from .validator import FILENAME, ProgramError, compile_program

MAGIC = b"RP"
VERSION = 1
HEADER = struct.Struct("<2sBBI")
MAX_PAYLOAD = 8 * 1024 * 1024

HELLO, LOAD, UNLOAD, PARAMS, STEP, TEST, PING, EXIT = 1, 2, 3, 4, 5, 6, 7, 8
REPLY, RESULT, DONE = 64, 65, 66

SOFT_BUDGET_MS = 2.0
HARD_STEP_MS = 8.0
LOAD_MS = 100.0
TEST_STEP_MS = 50.0
STATE_CHECK_S = 1.0
STATE_LIMIT_BYTES = 32 * 1024 * 1024
STATE_LIMIT_OBJECTS = 100_000
MEMORY_LIMIT = 512 * 1024 * 1024
CPU_SLICE_S = 10
RECURSION_LIMIT = 400
HOST_NICE = 5

TEST_DEFAULTS: dict[str, Any] = {
    "t": 1.0,
    "dt": 0.02,
    "armed": True,
    "airborne": True,
    "mode": "STABILIZE",
    "roll": 0.0,
    "pitch": 0.0,
    "yaw": 0.0,
    "p": 0.0,
    "q": 0.0,
    "r": 0.0,
    "ax": 0.0,
    "ay": 0.0,
    "az": 9.81,
    "alt": 1.0,
    "vz": 0.0,
    "pos": None,
    "vel": None,
    "battery": {"v_cell": 3.9, "pct": 80},
    "throttle_out": 0.5,
    "hp": None,
    "ammo": None,
    "reloading": None,
    "contacts": [],
    "arena": None,
    "sticks_in": None,
}


class Interrupt(BaseException):
    """Raised into a program that exceeds its hard time limit (not catchable by ``except Exception``)."""


# ============================================================================ framing


def encode(kind: int, payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(data) > MAX_PAYLOAD:
        raise ValueError("payload too large")
    return HEADER.pack(MAGIC, VERSION, kind, len(data)) + data


def read_exact(stream: Any, n: int) -> bytes | None:
    chunks = []
    while n:
        chunk = stream.read(n)
        if not chunk:
            return None
        chunks.append(chunk)
        n -= len(chunk)
    return b"".join(chunks)


def read_frame(stream: Any) -> tuple[int, dict[str, Any]] | None:
    """One message, or None at end of stream. Raises ValueError on a malformed frame."""
    head = read_exact(stream, HEADER.size)
    if head is None:
        return None
    magic, version, kind, length = HEADER.unpack(head)
    if magic != MAGIC or version != VERSION or length > MAX_PAYLOAD:
        raise ValueError("bad frame header")
    body = read_exact(stream, length) if length else b"{}"
    if body is None:
        return None
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("payload is not an object")
    return kind, payload


# ============================================================================ hard interrupt


class _Guard:
    """Arms a one-shot timer that raises ``Interrupt`` in the calling thread."""

    def __init__(self) -> None:
        self._armed = False
        self._fired = False
        self._signal = hasattr(signal, "setitimer") and threading.current_thread() is threading.main_thread()
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._deadline: float | None = None
        self._token = 0
        self._tid = 0
        self._watchdog: threading.Thread | None = None
        if self._signal:
            signal.signal(signal.SIGALRM, self._on_alarm)

    @property
    def mode(self) -> str:
        return "signal" if self._signal else "thread"

    def _on_alarm(self, signum: int, frame: Any) -> None:
        if self._armed:
            self._armed = False
            self._fired = True
            raise Interrupt()

    def arm(self, ms: float) -> None:
        self._fired = False
        if self._signal:
            self._armed = True
            signal.setitimer(signal.ITIMER_REAL, max(0.0005, ms / 1000.0))
            return
        with self._cond:
            self._token += 1
            self._tid = threading.get_ident()
            self._deadline = time.perf_counter() + ms / 1000.0
            if self._watchdog is None:
                self._watchdog = threading.Thread(target=self._watch, name="program-guard", daemon=True)
                self._watchdog.start()
            self._cond.notify()

    def disarm(self) -> bool:
        """Stop the timer; True when it fired."""
        if self._signal:
            self._armed = False
            signal.setitimer(signal.ITIMER_REAL, 0)
            return self._fired
        with self._cond:
            self._deadline = None
            fired = self._fired
            if fired:
                # an interrupt set just before the user code returned must not surface in host code
                ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(self._tid), None)
        return fired

    def _watch(self) -> None:
        with self._cond:
            while True:
                if self._deadline is None:
                    self._cond.wait()
                    continue
                remaining = self._deadline - time.perf_counter()
                if remaining > 0:
                    self._cond.wait(remaining)
                    continue
                self._deadline = None
                self._fired = True
                ctypes.pythonapi.PyThreadState_SetAsyncExc(
                    ctypes.c_ulong(self._tid), ctypes.py_object(Interrupt)
                )


_GUARDS: dict[int, _Guard] = {}
_GUARDS_LOCK = threading.Lock()


def _shared_guard() -> _Guard:
    """One guard per thread: SIGALRM has one handler per process, so every ``Engine`` of the main thread
    (several in one evaluator process) must arm the same guard."""
    tid = threading.get_ident()
    with _GUARDS_LOCK:
        guard = _GUARDS.get(tid)
        if guard is None:
            guard = _GUARDS[tid] = _Guard()
        return guard


# ============================================================================ engine


def _error_text(exc: BaseException) -> str:
    """``ZeroDivisionError: division by zero (line 12)``, using the innermost program frame."""
    line = None
    for frame in traceback.extract_tb(exc.__traceback__):
        if frame.filename == FILENAME:
            line = frame.lineno
    text = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    text = text[:200]
    return f"{text} (line {line})" if line else text


def _state_size(values: list[Any], limit_bytes: int, limit_objects: int) -> tuple[int, int]:
    """Approximate bytes and object count reachable from ``values`` through plain containers."""
    seen: set[int] = set()
    stack = list(values)
    total = 0
    count = 0
    while stack:
        obj = stack.pop()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        count += 1
        try:
            total += sys.getsizeof(obj)
        except TypeError:
            total += 64
        if total > limit_bytes or count > limit_objects:
            break
        if isinstance(obj, dict):
            stack.extend(obj.keys())
            stack.extend(obj.values())
        elif isinstance(obj, (list, tuple, set, frozenset)):
            stack.extend(obj)
        else:
            stack.extend(_held_by(obj))
    return total, count


# iterator types whose __reduce__ names what they hold (map, zip, filter, reversed, enumerate, iter(...))
_ITERATOR_TYPES = tuple(
    {
        map,
        filter,
        zip,
        reversed,
        enumerate,
        type(iter([])),
        type(iter(())),
        type(iter("")),
        type(iter(range(1))),
        type(iter(set())),
        type(iter({})),
        type(iter({}.values())),
        type(iter({}.items())),
        type(reversed([])),
    }
)
_GENERATOR = type(x for x in ())


def _held_by(obj: Any) -> list[Any]:
    """What a non-container keeps alive: the values a program's function closes over or defaults to, the
    sequences an iterator walks, a suspended generator's locals and the slots of library objects."""
    kind = type(obj)
    if kind is _FUNCTION:
        if obj.__code__.co_filename != FILENAME:
            return []
        cells = [c.cell_contents for c in obj.__closure__ or () if _cell_full(c)]
        return [*cells, *(obj.__defaults__ or ()), *(obj.__kwdefaults__ or {}).values()]
    if kind is _GENERATOR:
        frame = obj.gi_frame
        return list(frame.f_locals.values()) if frame is not None else []
    if isinstance(obj, _ITERATOR_TYPES):
        try:
            reduced = obj.__reduce__()
        except Exception:
            return []
        return list(reduced[1:]) if isinstance(reduced, tuple) else []
    slots = getattr(kind, "__slots__", None)
    if slots and kind.__module__ == __package__ + ".lib":
        return [getattr(obj, name, None) for name in slots]
    return []


def _cell_full(cell: Any) -> bool:
    try:
        cell.cell_contents  # noqa: B018 - an empty cell raises
    except ValueError:
        return False
    return True


_FUNCTION = type(_cell_full)


class Instance:
    def __init__(self, key: str, ctx: Ctx, g: dict[str, Any], step: Any, params: dict[str, Any]) -> None:
        self.key = key
        self.ctx = ctx
        self.globals = g
        self.step = step
        self.params = params  # the dict behind ctx.params, updated in place
        self.checked_at = 0.0


class Engine:
    """Loads programs and runs their steps. Not thread-safe; one caller at a time."""

    def __init__(self) -> None:
        self.programs: dict[str, Instance] = {}
        self.guard = _shared_guard()

    # ------------------------------------------------------------------ lifecycle

    def _call(self, ms: float, fn: Any, *args: Any) -> Any:
        self.guard.arm(ms)
        try:
            return fn(*args)
        finally:
            self.guard.disarm()

    def _instantiate(
        self, key: str, source: str, params: dict[str, Any], seed: int, run_init: bool = True
    ) -> Instance:
        code = compile_program(source)
        values = dict(params)
        ctx = Ctx(values, seed)
        g = program_globals(ctx)
        self._call(LOAD_MS, exec, code, g)
        step = g.get("step")
        if not callable(step):
            raise ValueError("the program defines no step(obs, ctx)")
        inst = Instance(key, ctx, g, step, values)
        init = g.get("init")
        if run_init and callable(init):
            self._call(LOAD_MS, init, ctx)
        return inst

    def load(self, key: str, source: str, params: dict[str, Any], seed: int = 0) -> dict[str, Any]:
        self.programs.pop(key, None)
        try:
            inst = self._instantiate(key, source, params, seed)
        except ProgramError as exc:
            first = exc.problems[0]
            return {
                "ok": False,
                "error": f"line {first.line}: {first.message_en}",
                "problems": [p.view() for p in exc.problems],
            }
        except Interrupt:
            return {"ok": False, "error": f"loading took longer than {LOAD_MS:g} ms"}
        except Exception as exc:
            return {"ok": False, "error": _error_text(exc)}
        self.programs[key] = inst
        lines, _ = inst.ctx.take_lines()
        return {"ok": True, "state": inst.ctx.state_name(), "logs": lines}

    def unload(self, key: str) -> None:
        self.programs.pop(key, None)

    def set_params(self, key: str, params: dict[str, Any]) -> bool:
        inst = self.programs.get(key)
        if inst is None:
            return False
        inst.params.update(params)
        return True

    # ------------------------------------------------------------------ frames

    def step(
        self,
        key: str,
        obs: dict[str, Any],
        hard_ms: float = HARD_STEP_MS,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One frame; ``params`` (live parameter changes, already checked by the backend) apply first."""
        inst = self.programs.get(key)
        if inst is None:
            return {"ok": False, "error": "program not loaded", "fatal": True}
        if params:
            inst.params.update(params)
        ctx = inst.ctx
        t = obs.get("t")
        if isinstance(t, (int, float)) and math.isfinite(t):
            ctx.t = float(t)
        result: dict[str, Any] = {"ok": False}
        started = time.perf_counter()
        try:
            o = make_obs(obs)
            out = self._call(hard_ms, inst.step, o, ctx)
            result["out"] = list(sticks_values(out))
            result["ok"] = True
        except Interrupt:
            result["error"] = f"step exceeded the {hard_ms:g} ms limit"
        except Exception as exc:
            result["error"] = _error_text(exc)
        ms = (time.perf_counter() - started) * 1000.0
        lines, dropped = ctx.take_lines()
        result.update(
            ms=round(ms, 3),
            over=ms > SOFT_BUDGET_MS,
            state=ctx.state_name(),
            logs=lines,
            dropped=dropped,
            sm=ctx.machine_view(),
        )
        now = time.monotonic()
        if now - inst.checked_at >= STATE_CHECK_S:
            inst.checked_at = now
            size, count = _state_size(
                [ctx.mem, *(v for k, v in inst.globals.items() if not k.startswith("__"))],
                STATE_LIMIT_BYTES,
                STATE_LIMIT_OBJECTS,
            )
            if size > STATE_LIMIT_BYTES or count > STATE_LIMIT_OBJECTS:
                self.programs.pop(key, None)
                result.update(ok=False, fatal=True, error="program state exceeds the memory limit")
                result.pop("out", None)
        return result

    # ------------------------------------------------------------------ unit tests (06 §3.1)

    def run_tests(
        self, source: str, params: dict[str, Any], tests: list[dict[str, Any]], seed: int = 0
    ) -> dict[str, Any]:
        results = []
        for i, test in enumerate(tests):
            name = str(test.get("name") or f"test {i + 1}")[:64]
            results.append(self._one_test(name, source, params, test, seed))
        passed = sum(1 for r in results if r["passed"])
        return {"results": results, "passed": passed, "total": len(results)}

    def _one_test(
        self, name: str, source: str, params: dict[str, Any], test: dict[str, Any], seed: int
    ) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": name,
            "passed": False,
            "out": None,
            "state": None,
            "error": None,
            "failures": [],
        }
        try:
            merged = {**params, **(test.get("params") or {})}
            inst = self._instantiate("test", source, merged, seed)
            if isinstance(test.get("mem"), dict):
                inst.ctx.mem.update(test["mem"])
            if isinstance(test.get("state"), str):
                inst.ctx.state = test["state"][:32]
            obs = {**TEST_DEFAULTS, **(test.get("obs") or {})}
            inst.ctx.t = float(obs.get("t") or 0.0)
            sticks = sticks_values(self._call(TEST_STEP_MS, inst.step, make_obs(obs), inst.ctx))
        except ProgramError as exc:
            first = exc.problems[0]
            out["error"] = f"line {first.line}: {first.message_en}"
            return out
        except Interrupt:
            out["error"] = f"step exceeded the {TEST_STEP_MS:g} ms limit"
            return out
        except Exception as exc:
            out["error"] = _error_text(exc)
            return out
        roll, pitch, yaw, throttle, fire = sticks
        values = {"roll": roll, "pitch": pitch, "yaw": yaw, "throttle": throttle, "fire": fire}
        state = inst.ctx.state_name()
        out["out"] = {k: (round(v, 6) if isinstance(v, float) else v) for k, v in values.items()}
        out["state"] = state
        expect = test.get("expect") or {}
        for key, want in expect.items():
            if key in ("roll", "pitch", "yaw", "throttle"):
                ok = (
                    isinstance(want, (list, tuple))
                    and len(want) == 2
                    and float(want[0]) - 1e-9 <= values[key] <= float(want[1]) + 1e-9
                )
                actual: Any = values[key]
            elif key == "fire":
                ok = bool(want) == fire
                actual = fire
            elif key == "state":
                ok = str(want) == state
                actual = state
            else:
                continue
            if not ok:
                out["failures"].append({"field": key, "expected": want, "actual": actual})
        out["passed"] = not out["failures"]
        return out


# ============================================================================ process


def _harden() -> dict[str, Any]:
    """Process limits on POSIX; returns what was applied (reported in HELLO)."""
    applied: dict[str, Any] = {"as": None, "cpu": None, "fsize": None, "nice": None}
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):
        pass
    if hasattr(os, "nice"):  # the fleet thread wins the CPU over every program host (Windows: creationflags)
        try:
            os.nice(HOST_NICE)
            applied["nice"] = HOST_NICE
        except OSError:
            pass
    try:
        import resource  # POSIX only
    except ImportError:
        return applied
    for name, limit in (("RLIMIT_AS", MEMORY_LIMIT), ("RLIMIT_FSIZE", 0)):
        which = getattr(resource, name, None)
        if which is None:
            continue
        try:
            resource.setrlimit(which, (limit, limit))
            applied["as" if name == "RLIMIT_AS" else "fsize"] = limit
        except (ValueError, OSError):
            pass
    _cpu_slice()
    applied["cpu"] = CPU_SLICE_S
    return applied


def _cpu_slice() -> None:
    """Move the CPU-time soft limit to ``used + CPU_SLICE_S``: a host stuck in C code dies of SIGXCPU."""
    try:
        import resource
    except ImportError:
        return
    try:
        used = resource.getrusage(resource.RUSAGE_SELF)
        soft = int(used.ru_utime + used.ru_stime) + CPU_SLICE_S
        _, hard = resource.getrlimit(resource.RLIMIT_CPU)
        if hard != resource.RLIM_INFINITY:
            soft = min(soft, hard)
        resource.setrlimit(resource.RLIMIT_CPU, (soft, hard))
    except (ValueError, OSError):
        pass


def serve(stdin: Any, stdout: Any) -> int:
    engine = Engine()

    def send(kind: int, payload: dict[str, Any]) -> None:
        stdout.write(encode(kind, payload))
        stdout.flush()

    send(HELLO, {"pid": os.getpid(), "version": VERSION, "interrupt": engine.guard.mode, "limits": LIMITS})
    last_slice = time.monotonic()
    while True:
        try:
            frame = read_frame(stdin)
        except (ValueError, UnicodeDecodeError):
            return 2
        if frame is None:
            return 0
        kind, msg = frame
        rid = msg.get("id")
        try:
            if kind == STEP:
                hard = float(msg.get("hard_ms") or HARD_STEP_MS)
                deadline = time.perf_counter() + float(msg.get("budget_ms") or 14.0) / 1000.0
                for item in msg.get("items") or []:
                    remaining = (deadline - time.perf_counter()) * 1000.0
                    result = engine.step(
                        str(item.get("key")),
                        item.get("obs") or {},
                        max(0.5, min(hard, remaining)),
                        item.get("params") if isinstance(item.get("params"), dict) else None,
                    )
                    result["id"] = rid
                    result["key"] = item.get("key")
                    send(RESULT, result)
                send(DONE, {"id": rid})
                now = time.monotonic()
                if now - last_slice > 1.0:
                    last_slice = now
                    _cpu_slice()
            elif kind == LOAD:
                reply = engine.load(
                    str(msg.get("key")),
                    str(msg.get("source") or ""),
                    dict(msg.get("params") or {}),
                    int(msg.get("seed") or 0),
                )
                send(REPLY, {"id": rid, **reply})
            elif kind == UNLOAD:
                engine.unload(str(msg.get("key")))
                send(REPLY, {"id": rid, "ok": True})
            elif kind == PARAMS:
                ok = engine.set_params(str(msg.get("key")), dict(msg.get("params") or {}))
                send(REPLY, {"id": rid, "ok": ok})
            elif kind == TEST:
                reply = engine.run_tests(
                    str(msg.get("source") or ""),
                    dict(msg.get("params") or {}),
                    list(msg.get("tests") or []),
                    int(msg.get("seed") or 0),
                )
                send(REPLY, {"id": rid, "ok": True, **reply})
            elif kind == PING:
                send(REPLY, {"id": rid, "ok": True, "programs": len(engine.programs)})
            elif kind == EXIT:
                send(REPLY, {"id": rid, "ok": True})
                return 0
            else:
                send(REPLY, {"id": rid, "ok": False, "error": "unknown message"})
        except Interrupt:  # pragma: no cover - a stray interrupt outside user code
            send(REPLY, {"id": rid, "ok": False, "error": "interrupted"})
        except Exception as exc:  # keep serving; the backend decides what a failed request means
            send(REPLY, {"id": rid, "ok": False, "error": f"host error: {type(exc).__name__}: {exc}"[:200]})


LIMITS: dict[str, Any] = {}


def main() -> int:
    """Entry for ``--program-host``: harden, then serve the pipe protocol on stdin / stdout."""
    LIMITS.update(_harden())
    sys.setrecursionlimit(RECURSION_LIMIT)
    return serve(sys.stdin.buffer, sys.stdout.buffer)


__all__ = ["Engine", "Interrupt", "encode", "main", "read_frame", "serve"]
