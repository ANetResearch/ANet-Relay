"""The ``relay`` library of onboard programs (06 §2.1-§2.3) and the restricted runtime they execute in.

Everything a program can name is defined here: ``Sticks``, the helpers (``clamp``, ``wrap180``, ``lerp``,
``deadband``, ``lowpass``), ``PID``, ``StateMachine``, ``Timer``, the read-only observation records, the
per-program context ``Ctx``, the ``math`` subset and the whitelisted builtins. The program host
(``host.py``) builds one globals dictionary per program with :func:`program_globals`.

Safety notes. The language rules (``validator.py``) forbid every name and attribute that starts with ``_``,
so the objects below may keep their internals in underscored slots. Operators that can make a value grow
without bound in a single C call (``*``, ``**``, ``<<``, ``+`` and ``%`` on strings) are rewritten by the
validator into :func:`relay_op`, which refuses results above ``MAX_INT_BITS`` bits or ``MAX_SEQ`` items;
``range`` is capped at ``MAX_RANGE`` items and the expensive ``math`` functions are bounded. Wall-time
limits, memory accounting and process limits live in the host.
"""

from __future__ import annotations

import math as _math
import random as _random
import reprlib as _reprlib
from collections.abc import Callable, Mapping
from typing import Any

MAX_INT_BITS = 8192
MAX_SEQ = 100_000
MAX_STR = 1_000_000
MAX_RANGE = 100_000
STATE_MAX = 32
LOG_LINE_MAX = 160
LOG_LINES_PER_FRAME = 4
_WIDTH_DIGITS = 4  # "%10000d" is fine, "%100000d" is refused


class _Locked(type):
    """Metaclass of every library class: programs share them in one host, so class attributes are fixed."""

    def __setattr__(cls, name: str, value: Any) -> None:
        raise TypeError("library classes cannot be changed")

    def __delattr__(cls, name: str) -> None:
        raise TypeError("library classes cannot be changed")


class _Fn(metaclass=_Locked):
    """A shared library function without a writable ``__dict__`` (no data channel between programs)."""

    __slots__ = ("_f",)

    def __init__(self, fn: Callable[..., Any]) -> None:
        object.__setattr__(self, "_f", fn)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._f(*args, **kwargs)

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("library functions cannot be changed")

    def __repr__(self) -> str:
        return f"<relay function {self._f.__name__}>"


class _Namespace(metaclass=_Locked):
    """Read-only namespace (``math``, ``relay``)."""

    __slots__ = ("_d",)

    def __init__(self, **values: Any) -> None:
        object.__setattr__(self, "_d", dict(values))

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self._d[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("this module is read-only")

    def __dir__(self) -> list[str]:
        return sorted(self._d)


# ============================================================================ Sticks


class Sticks(metaclass=_Locked):
    """Stick output, in the operator's frame (03 §5.2): roll right +, pitch forward +, yaw clockwise +.

    roll / pitch / yaw in [-1, 1], throttle in [0, 1]; the host clamps out-of-range values and rejects
    non-finite ones. ``fire`` is the trigger (ignored on real aircraft).
    """

    __slots__ = ("fire", "pitch", "roll", "throttle", "yaw")

    def __init__(
        self, roll: Any = 0.0, pitch: Any = 0.0, yaw: Any = 0.0, throttle: Any = 0.0, fire: Any = False
    ) -> None:
        self.roll = roll
        self.pitch = pitch
        self.yaw = yaw
        self.throttle = throttle
        self.fire = fire

    def __repr__(self) -> str:
        return (
            f"Sticks(roll={self.roll!r}, pitch={self.pitch!r}, yaw={self.yaw!r}, "
            f"throttle={self.throttle!r}, fire={self.fire!r})"
        )


def sticks_values(out: Any) -> tuple[float, float, float, float, bool]:
    """Validate a program's return value: (roll, pitch, yaw, throttle, fire), clamped. Raises ValueError."""
    if out is None:
        raise ValueError("step() returned None")
    if not isinstance(out, Sticks):
        raise ValueError("step() must return Sticks(...)")
    values = []
    for name in ("roll", "pitch", "yaw", "throttle"):
        raw = getattr(out, name)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"Sticks.{name} must be a number")
        number = float(raw)
        if not _math.isfinite(number):
            raise ValueError(f"Sticks.{name} is not finite")
        low = 0.0 if name == "throttle" else -1.0
        values.append(min(1.0, max(low, number)))
    return values[0], values[1], values[2], values[3], bool(out.fire)


# ============================================================================ helpers


def clamp(x: float, lo: float, hi: float) -> float:
    """``x`` limited to [lo, hi]."""
    return lo if x < lo else hi if x > hi else x


def wrap180(deg: float) -> float:
    """An angle in degrees folded into [-180, 180)."""
    return (deg + 180.0) % 360.0 - 180.0


def lerp(a: float, b: float, t: float) -> float:
    """Linear blend: ``a`` at t = 0, ``b`` at t = 1."""
    return a + (b - a) * t


def deadband(x: float, d: float) -> float:
    """Zero inside +-d, continuous outside it (the band is subtracted)."""
    if abs(x) <= d:
        return 0.0
    return x - d if x > 0 else x + d


def lowpass(prev: float | None, x: float, alpha: float) -> float:
    """First-order filter: ``prev + alpha * (x - prev)``; the first sample passes through."""
    if prev is None:
        return x
    return prev + alpha * (x - prev)


class PID(metaclass=_Locked):
    """PID controller: ``out = kp*e + ki*integral(e) + kd*de/dt``.

    ``i_limit`` bounds the integral term (anti-windup), ``out_limit`` the output, both symmetric.
    The gains are plain attributes, so a program may retune them from ``ctx.params``.
    """

    __slots__ = ("_last", "i_limit", "integral", "kd", "ki", "kp", "out_limit")

    def __init__(
        self,
        kp: float = 0.0,
        ki: float = 0.0,
        kd: float = 0.0,
        i_limit: float | None = None,
        out_limit: float | None = None,
    ) -> None:
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.i_limit = i_limit
        self.out_limit = out_limit
        self.integral = 0.0
        self._last: float | None = None

    def step(self, error: float, dt: float) -> float:
        dt = dt if dt and dt > 0 else 0.02
        self.integral += error * dt
        if self.i_limit is not None and self.ki:
            bound = abs(self.i_limit / self.ki)
            self.integral = clamp(self.integral, -bound, bound)
        deriv = 0.0 if self._last is None else (error - self._last) / dt
        self._last = error
        out = self.kp * error + self.ki * self.integral + self.kd * deriv
        if self.out_limit is not None:
            out = clamp(out, -abs(self.out_limit), abs(self.out_limit))
        return out

    def reset(self) -> None:
        self.integral = 0.0
        self._last = None


class StateMachine(metaclass=_Locked):
    """Named states run one at a time.

    ``sm.add(name, fn)`` registers ``fn(obs, ctx) -> (Sticks, next_state | None)`` (a bare ``Sticks``
    means "stay"). ``sm.step(obs, ctx)`` runs the current state, applies the transition for the next
    frame, keeps ``ctx.state`` equal to the current state name and counts transitions and dwell time.
    Setting ``ctx.state`` to a registered name (in ``init``, a unit test or a state) switches to it.
    """

    __slots__ = ("_fns", "dwell", "entered", "state", "transitions")

    def __init__(self, initial: str) -> None:
        self.state = str(initial)
        self._fns: dict[str, Callable[..., Any]] = {}
        self.transitions = 0
        self.entered: float | None = None
        self.dwell: dict[str, float] = {}

    def add(self, name: str, fn: Callable[..., Any]) -> StateMachine:
        self._fns[str(name)] = fn
        return self

    def time_in_state(self, ctx: Ctx) -> float:
        return 0.0 if self.entered is None else max(0.0, ctx.t - self.entered)

    def step(self, obs: Any, ctx: Ctx) -> Sticks:
        if self.entered is None:
            self.entered = ctx.t
        current = ctx.state
        if isinstance(current, str) and current != self.state and current in self._fns:
            self.state = current  # set by init, a unit test or the program itself
            self.entered = ctx.t
        fn = self._fns.get(self.state)
        if fn is None:
            raise KeyError(f"state {self.state!r} has no function")
        ctx.state = self.state
        ctx._machine = self
        result = fn(obs, ctx)
        nxt = None
        if isinstance(result, tuple):
            if len(result) != 2:
                raise ValueError("a state returns (Sticks, next_state)")
            result, nxt = result
        if nxt is not None and str(nxt) != self.state:
            nxt = str(nxt)
            if nxt not in self._fns:
                raise KeyError(f"unknown state {nxt!r}")
            self.dwell[self.state] = self.dwell.get(self.state, 0.0) + self.time_in_state(ctx)
            self.state = nxt
            self.entered = ctx.t
            self.transitions += 1
            ctx.state = nxt
        return result


class Timer(metaclass=_Locked):
    """Named timers kept in ``ctx.mem["timers"]`` (program time, seconds)."""

    @staticmethod
    def _table(ctx: Ctx) -> dict[str, float]:
        table = ctx.mem.get("timers")
        if not isinstance(table, dict):
            table = {}
            ctx.mem["timers"] = table
        return table

    @staticmethod
    def start(ctx: Ctx, name: str = "default") -> None:
        Timer._table(ctx)[str(name)] = ctx.t

    @staticmethod
    def stop(ctx: Ctx, name: str = "default") -> None:
        Timer._table(ctx).pop(str(name), None)

    @staticmethod
    def running(ctx: Ctx, name: str = "default") -> bool:
        return str(name) in Timer._table(ctx)

    @staticmethod
    def elapsed(ctx: Ctx, name: str = "default") -> float | None:
        started = Timer._table(ctx).get(str(name))
        return None if started is None else max(0.0, ctx.t - started)

    @staticmethod
    def done(ctx: Ctx, name: str, seconds: float) -> bool:
        """True once ``seconds`` have passed since ``start``; starts the timer when it is not running."""
        table = Timer._table(ctx)
        if str(name) not in table:
            table[str(name)] = ctx.t
        return ctx.t - table[str(name)] >= seconds


# ============================================================================ observation records


class Record(metaclass=_Locked):
    """Read-only record with attribute and item access (observation, battery, contact, arena)."""

    __slots__ = ("_d",)

    def __init__(self, data: Mapping[str, Any]) -> None:
        object.__setattr__(self, "_d", dict(data))

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self._d[name]
        except KeyError:
            raise AttributeError(f"no field {name!r}") from None

    def __getitem__(self, name: str) -> Any:
        return self._d[name]

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("observations are read-only")

    def get(self, name: str, default: Any = None) -> Any:
        return self._d.get(name, default)

    def keys(self) -> list[str]:
        return list(self._d)

    def __contains__(self, name: object) -> bool:
        return name in self._d

    def __repr__(self) -> str:
        return "Record(" + ", ".join(f"{k}={_short(v)}" for k, v in self._d.items()) + ")"


def _vec(value: Any) -> tuple[float, float, float] | None:
    if value is None:
        return None
    return tuple(float(x) for x in value)  # type: ignore[return-value]


def make_obs(data: Mapping[str, Any]) -> Record:
    """The ``obs`` object from the backend's observation dictionary (``obs.py``)."""
    d = dict(data)
    for key in ("pos", "vel"):
        d[key] = _vec(d.get(key))
    if isinstance(d.get("battery"), Mapping):
        d["battery"] = Record(d["battery"])
    if isinstance(d.get("arena"), Mapping):
        d["arena"] = Record(d["arena"])
    contacts = d.get("contacts")
    if contacts is not None:
        out = []
        for c in contacts:
            c = dict(c)
            c["rel"] = _vec(c.get("rel"))
            c["vrel"] = _vec(c.get("vrel"))
            out.append(Record(c))
        d["contacts"] = tuple(out)
    sticks = d.get("sticks_in")
    if isinstance(sticks, Mapping):
        d["sticks_in"] = Sticks(
            float(sticks.get("roll", 0.0)),
            float(sticks.get("pitch", 0.0)),
            float(sticks.get("yaw", 0.0)),
            float(sticks.get("throttle", 0.0)),
            bool(sticks.get("fire", False)),
        )
    return Record(d)


# ============================================================================ context


class Params(metaclass=_Locked):
    """``ctx.params``: a read-only view of the live parameters (attribute or item access)."""

    __slots__ = ("_d",)

    def __init__(self, data: dict[str, Any]) -> None:
        object.__setattr__(self, "_d", data)

    def __getitem__(self, name: str) -> Any:
        return self._d[name]

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self._d[name]
        except KeyError:
            raise AttributeError(f"no parameter {name!r}") from None

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("ctx.params is read-only; change parameters with the params API")

    def __iter__(self):
        return iter(self._d)

    def __len__(self) -> int:
        return len(self._d)

    def __contains__(self, name: object) -> bool:
        return name in self._d

    def get(self, name: str, default: Any = None) -> Any:
        return self._d.get(name, default)

    def keys(self) -> list[str]:
        return list(self._d)

    def items(self) -> list[tuple[str, Any]]:
        return list(self._d.items())

    def values(self) -> list[Any]:
        return list(self._d.values())

    def __repr__(self) -> str:
        return f"Params({self._d!r})"


class Rng(metaclass=_Locked):
    """Deterministic random numbers seeded at deployment."""

    __slots__ = ("_r",)

    def __init__(self, seed: int) -> None:
        self._r = _random.Random(int(seed))

    def random(self) -> float:
        return self._r.random()

    def uniform(self, a: float, b: float) -> float:
        return self._r.uniform(a, b)

    def gauss(self, mu: float = 0.0, sigma: float = 1.0) -> float:
        return self._r.gauss(mu, sigma)

    def randint(self, a: int, b: int) -> int:
        return self._r.randint(int(a), int(b))

    def choice(self, seq: Any) -> Any:
        return self._r.choice(seq)


class Ctx(metaclass=_Locked):
    """Per-program context: ``mem`` (kept across frames), ``params`` (read-only, live), ``state`` (name
    shown on the stage, at most 32 characters), ``log(*args)`` (at most 4 lines per frame), ``t`` (seconds
    since deployment) and ``rng`` (seeded at deployment)."""

    __slots__ = ("_dropped", "_lines", "_machine", "mem", "params", "rng", "state", "t")

    def __init__(self, params: dict[str, Any], seed: int) -> None:
        self.mem: dict[str, Any] = {}
        self.params = Params(params)
        self.state = ""
        self.t = 0.0
        self.rng = Rng(seed)
        self._lines: list[str] = []
        self._dropped = 0
        self._machine: StateMachine | None = None

    def log(self, *args: Any) -> None:
        if len(self._lines) >= LOG_LINES_PER_FRAME:
            self._dropped += 1
            return
        self._lines.append(" ".join(_short(a) for a in args)[:LOG_LINE_MAX])

    def take_lines(self) -> tuple[list[str], int]:
        lines, dropped = self._lines, self._dropped
        self._lines, self._dropped = [], 0
        return lines, dropped

    def state_name(self) -> str:
        state = self.state
        if not isinstance(state, str):
            state = _short(state)
        return state[:STATE_MAX]

    def machine_view(self) -> dict[str, Any] | None:
        sm = self._machine
        if sm is None:
            return None
        return {
            "state": str(sm.state)[:STATE_MAX],
            "transitions": sm.transitions,
            "state_s": sm.time_in_state(self),
        }


_REPR = _reprlib.Repr()
_REPR.maxstring = LOG_LINE_MAX
_REPR.maxother = LOG_LINE_MAX
_REPR.maxlong = 40


def _short(value: Any) -> str:
    if isinstance(value, str):
        return value[:LOG_LINE_MAX]
    if isinstance(value, float):
        return repr(round(value, 6)) if _math.isfinite(value) else repr(value)
    try:
        return _REPR.repr(value)
    except Exception:  # a hostile __repr__ is impossible (no classes), but stay defensive
        return "<unprintable>"


# ============================================================================ bounded operators


def _int_bits(x: Any) -> int:
    return x.bit_length() if isinstance(x, int) else 0


def _seq_len(x: Any) -> int | None:
    if isinstance(x, (str, list, tuple, bytes)):
        return len(x)
    return None


def relay_op(op: str, a: Any, b: Any) -> Any:
    """``a <op> b`` with size limits: the validator rewrites ``*``, ``**``, ``<<``, ``+`` and ``%``."""
    if op == "mul":
        if isinstance(a, int) and isinstance(b, int):
            if _int_bits(a) + _int_bits(b) > MAX_INT_BITS:
                raise OverflowError("integer result too large")
        else:
            seq, n = (a, b) if _seq_len(a) is not None else (b, a)
            length = _seq_len(seq)
            if length is not None and isinstance(n, int):
                limit = MAX_STR if isinstance(seq, str) else MAX_SEQ
                if n > 0 and length * n > limit:
                    raise OverflowError("sequence too long")
        return a * b
    if op == "pow":
        return safe_pow(a, b)
    if op == "lshift":
        if isinstance(a, int) and isinstance(b, int) and b > 0 and _int_bits(a) + b > MAX_INT_BITS:
            raise OverflowError("integer result too large")
        return a << b
    if op == "add":
        la, lb = _seq_len(a), _seq_len(b)
        if la is not None and lb is not None:
            limit = MAX_STR if isinstance(a, str) else MAX_SEQ
            if la + lb > limit:
                raise OverflowError("sequence too long")
        return a + b
    if op == "mod":
        if isinstance(a, str) and _wide_format(a):
            raise OverflowError("format width too large")
        return a % b
    raise ValueError(op)


def _wide_format(fmt: str) -> bool:
    run = 0
    for ch in fmt:
        if ch.isdigit():
            run += 1
            if run > _WIDTH_DIGITS:
                return True
        else:
            run = 0
    return False


def safe_pow(a: Any, b: Any, m: Any = None) -> Any:
    if m is not None:
        if isinstance(m, int) and _int_bits(m) > MAX_INT_BITS:
            raise OverflowError("modulus too large")
        return pow(a, b, m)
    if isinstance(a, int) and isinstance(b, int) and b > 0 and abs(a) > 1 and _int_bits(a) * b > MAX_INT_BITS:
        raise OverflowError("integer result too large")
    return a**b


def safe_range(*args: Any) -> range:
    r = range(*args)
    if len(r) > MAX_RANGE:
        raise OverflowError(f"range is limited to {MAX_RANGE} items")
    return r


def _bounded(fn: Callable[..., Any], limit: int) -> Callable[..., Any]:
    def wrapper(*args: Any) -> Any:
        for value in args:
            if isinstance(value, int) and abs(value) > limit:
                raise OverflowError(f"argument too large (limit {limit})")
        return fn(*args)

    wrapper.__name__ = fn.__name__
    return wrapper


def _prod(iterable: Any, start: Any = 1) -> Any:
    out = start
    for x in iterable:
        out = relay_op("mul", out, x)
    return out


_MATH_NAMES = [
    "acos",
    "acosh",
    "asin",
    "asinh",
    "atan",
    "atan2",
    "atanh",
    "cbrt",
    "ceil",
    "copysign",
    "cos",
    "cosh",
    "degrees",
    "dist",
    "e",
    "erf",
    "erfc",
    "exp",
    "exp2",
    "expm1",
    "fabs",
    "floor",
    "fmod",
    "frexp",
    "fsum",
    "gamma",
    "gcd",
    "hypot",
    "inf",
    "isclose",
    "isfinite",
    "isinf",
    "isnan",
    "isqrt",
    "lcm",
    "ldexp",
    "lgamma",
    "log",
    "log10",
    "log1p",
    "log2",
    "modf",
    "nan",
    "nextafter",
    "pi",
    "pow",
    "radians",
    "remainder",
    "sin",
    "sinh",
    "sqrt",
    "tan",
    "tanh",
    "tau",
    "trunc",
    "ulp",
]


def _math_module() -> _Namespace:
    names = {name: getattr(_math, name) for name in _MATH_NAMES if hasattr(_math, name)}
    names["factorial"] = _Fn(_bounded(_math.factorial, 1000))
    names["comb"] = _Fn(_bounded(_math.comb, 1000))
    names["perm"] = _Fn(_bounded(_math.perm, 1000))
    names["prod"] = _Fn(_prod)
    return _Namespace(**names)


SAFE_MATH = _math_module()


def _restricted_import(
    name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0
):
    if name == "math" and level == 0:
        return SAFE_MATH
    raise ImportError("only 'import math' is allowed")


BUILTIN_NAMES = [
    "abs",
    "min",
    "max",
    "round",
    "sum",
    "len",
    "range",
    "enumerate",
    "zip",
    "sorted",
    "reversed",
    "int",
    "float",
    "bool",
    "str",
    "list",
    "dict",
    "tuple",
    "set",
    "isinstance",
    "any",
    "all",
    "map",
    "filter",
    "divmod",
    "pow",
    "print",
]


def safe_builtins(print_fn: Callable[..., None]) -> dict[str, Any]:
    import builtins

    out: dict[str, Any] = {name: getattr(builtins, name) for name in BUILTIN_NAMES}
    out["range"] = _SAFE_RANGE
    out["pow"] = _SAFE_POW
    out["print"] = print_fn
    out["Exception"] = Exception
    out["True"] = True
    out["False"] = False
    out["None"] = None
    out["__import__"] = _IMPORT
    return out


LIBRARY_NAMES = ("Sticks", "clamp", "wrap180", "lerp", "deadband", "lowpass", "PID", "StateMachine", "Timer")
_SAFE_RANGE = _Fn(safe_range)
_SAFE_POW = _Fn(safe_pow)
_IMPORT = _Fn(_restricted_import)
_RELAY_OP = _Fn(relay_op)
RELAY = _Namespace(
    Sticks=Sticks,
    clamp=_Fn(clamp),
    wrap180=_Fn(wrap180),
    lerp=_Fn(lerp),
    deadband=_Fn(deadband),
    lowpass=_Fn(lowpass),
    PID=PID,
    StateMachine=StateMachine,
    Timer=Timer,
)
OP_NAME = "__relay_op__"


def program_globals(ctx: Ctx) -> dict[str, Any]:
    """A fresh module namespace for one program instance (``print`` writes to ``ctx.log``)."""
    g: dict[str, Any] = {
        "__builtins__": safe_builtins(ctx.log),
        "__name__": "program",
        OP_NAME: _RELAY_OP,
        "relay": RELAY,
    }
    for name in LIBRARY_NAMES:
        g[name] = getattr(RELAY, name)
    return g


def known_names() -> frozenset[str]:
    """Names a program may use without defining them (validator: unknown-name check)."""
    return frozenset((*BUILTIN_NAMES, "Exception", "True", "False", "None", "relay", *LIBRARY_NAMES))


__all__ = [
    "LIBRARY_NAMES",
    "PID",
    "Ctx",
    "Params",
    "Record",
    "Rng",
    "StateMachine",
    "Sticks",
    "Timer",
    "clamp",
    "deadband",
    "lerp",
    "lowpass",
    "make_obs",
    "program_globals",
    "relay_op",
    "safe_pow",
    "safe_range",
    "sticks_values",
    "wrap180",
]
