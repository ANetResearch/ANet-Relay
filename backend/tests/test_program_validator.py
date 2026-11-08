"""Onboard-program language rules and the escape test suite (06 §2.4, §10).

Two layers are checked: the AST whitelist refuses the construct statically, and where a construct is legal
syntax but could still be abused at run time (huge allocations, long C calls, deep recursion, endless loops,
shared library objects) the runtime bounds it. Runtime cases run in an in-process ``Engine``; the
process-level limits are in test_program_host.py.
"""

from __future__ import annotations

import pytest

from relay.programs import lib, validator
from relay.programs.host import Engine

STEP = "def step(obs, ctx):\n    return Sticks(throttle=0.5)\n"


def problems(source: str) -> list[str]:
    return [p.code for p in validator.validate(source).errors]


def refused(source: str, code: str | None = None) -> None:
    result = validator.validate(source)
    assert not result.ok, source
    if code is not None:
        assert code in [p.code for p in result.errors], (source, [p.view() for p in result.errors])
    with pytest.raises(validator.ProgramError):
        validator.compile_program(source)


# ---------------------------------------------------------------------------- accepted programs


def test_minimal_program_and_params():
    source = 'PARAMS = {"k": 0.5, "n": 3, "on": True, "tag": "a"}\nPARAM_RANGES = {"k": [0, 1]}\n' + STEP
    result = validator.validate(source)
    assert result.ok and result.has_step and not result.has_init
    assert result.params == {"k": 0.5, "n": 3, "on": True, "tag": "a"}
    assert result.param_ranges == {"k": [0.0, 1.0]}


def test_allowed_constructs_compile_and_run():
    source = """
import math
from math import sin as s, pi
PARAMS = {"gain": 1.5}

def helper(x, *rest, scale=1.0, **kw):
    return x * scale

def init(ctx):
    ctx.mem["n"] = 0
    ctx.state = "go"

def step(obs, ctx):
    total = 0
    for i in range(3):
        if i == 1:
            continue
        total += i
    while total > 10:
        break
    try:
        bad = 1 / 0
    except Exception as e:
        ctx.log("caught", e)
    squares = [i * i for i in range(4)]
    evens = {i for i in squares if i % 2 == 0}
    table = {k: v for k, v in zip("ab", (1, 2))}
    first = next_value = (lambda q: q + 1)(1)
    assert first == 2, "lambda"
    if (n := ctx.mem["n"] + 1) > 0:
        ctx.mem["n"] = n
    f = f"{obs.alt:.2f} {pi:.1f}"
    ctx.log(f, sorted(evens), table, sum(x for x in squares))
    out = Sticks(roll=clamp(helper(0.1, scale=ctx.params["gain"]), -1, 1), throttle=0.5 + 0.1 * s(0))
    out.yaw = relay.wrap180(370) / 180
    return out
"""
    assert validator.validate(source).ok, [p.view() for p in validator.validate(source).errors]
    engine = Engine()
    assert engine.load("a", source, {"gain": 1.5}, 1)["ok"]
    res = engine.step("a", {"t": 0.1, "alt": 1.0})
    assert res["ok"], res
    assert res["out"][0] == pytest.approx(0.15) and res["out"][2] == pytest.approx(10 / 180)
    assert res["state"] == "go" and any("caught" in line for line in res["logs"])


def test_library_helpers():
    assert lib.clamp(5, 0, 1) == 1 and lib.wrap180(190) == -170 and lib.wrap180(-180) == -180
    assert lib.lerp(0, 10, 0.25) == 2.5 and lib.deadband(0.05, 0.1) == 0.0
    assert lib.deadband(0.3, 0.1) == pytest.approx(0.2) and lib.deadband(-0.3, 0.1) == pytest.approx(-0.2)
    assert lib.lowpass(None, 3.0, 0.5) == 3.0 and lib.lowpass(1.0, 3.0, 0.5) == 2.0
    pid = lib.PID(kp=1.0, ki=1.0, kd=0.0, i_limit=0.5, out_limit=2.0)
    for _ in range(100):
        out = pid.step(1.0, 0.1)
    assert pid.integral == pytest.approx(0.5) and out == pytest.approx(1.5)
    pid.reset()
    assert pid.integral == 0.0
    ctx = lib.Ctx({}, 1)
    sm = lib.StateMachine("a")
    sm.add("a", lambda obs, c: (lib.Sticks(), "b")).add("b", lambda obs, c: lib.Sticks(throttle=1))
    sm.step(None, ctx)
    assert ctx.state == "b" and sm.transitions == 1
    ctx.t = 1.5
    assert sm.step(None, ctx).throttle == 1 and ctx.machine_view()["state"] == "b"
    ctx.state = "a"  # a unit test or init may set the state by name
    sm.step(None, ctx)
    assert sm.transitions == 2 and ctx.state == "b"
    lib.Timer.start(ctx, "x")
    ctx.t = 3.0
    assert lib.Timer.elapsed(ctx, "x") == pytest.approx(1.5) and lib.Timer.done(ctx, "x", 1.0)
    assert not lib.Timer.done(ctx, "y", 1.0) and lib.Timer.running(ctx, "y")


def test_sticks_are_clamped_and_checked():
    out = lib.sticks_values(lib.Sticks(roll=3, pitch=-3, yaw=0.5, throttle=2, fire=1))
    assert out == (1.0, -1.0, 0.5, 1.0, True)
    for bad in (None, 5, lib.Sticks(throttle=float("nan")), lib.Sticks(roll="x"), lib.Sticks(yaw=True)):
        with pytest.raises(ValueError):
            lib.sticks_values(bad)


# ---------------------------------------------------------------------------- static refusals


@pytest.mark.parametrize(
    ("source", "code"),
    [
        # imports
        ("import os\n" + STEP, "forbidden_import"),
        ("import math, sys\n" + STEP, "forbidden_import"),
        ("from os import path\n" + STEP, "forbidden_import"),
        ("from math import *\n" + STEP, "forbidden_import"),
        ("from . import x\n" + STEP, "forbidden_import"),
        ("import math as _m\n" + STEP, "forbidden_name"),
        # dunder and underscore names and attributes
        ("x = ().__class__\n" + STEP, "forbidden_attribute"),
        ("x = Sticks.__init__.__globals__\n" + STEP, "forbidden_attribute"),
        ("x = [].__class__.__base__.__subclasses__()\n" + STEP, "forbidden_attribute"),
        ("x = __builtins__\n" + STEP, "forbidden_name"),
        ("x = __import__('os')\n" + STEP, "forbidden_name"),
        ("_x = 1\n" + STEP, "forbidden_name"),
        ("def _f():\n    pass\n" + STEP, "forbidden_name"),
        ("def f(_a):\n    pass\n" + STEP, "forbidden_name"),
        ("x = obs._d\n" + STEP, "forbidden_attribute"),
        ("x = Sticks(_secret=1)\n" + STEP, "forbidden_name"),
        ("x = f'{obs.__class__}'\n" + STEP, "forbidden_attribute"),
        # introspection builtins
        ("x = eval('1')\n" + STEP, "forbidden_name"),
        ("exec('x = 1')\n" + STEP, "forbidden_name"),
        ("x = compile('1', 'a', 'eval')\n" + STEP, "forbidden_name"),
        ("x = open('/etc/passwd')\n" + STEP, "forbidden_name"),
        ("x = getattr(obs, 'alt')\n" + STEP, "forbidden_name"),
        ("setattr(obs, 'alt', 1)\n" + STEP, "forbidden_name"),
        ("x = globals()\n" + STEP, "forbidden_name"),
        ("x = locals()\n" + STEP, "forbidden_name"),
        ("x = vars()\n" + STEP, "forbidden_name"),
        ("x = dir()\n" + STEP, "forbidden_name"),
        ("x = type(obs)\n" + STEP, "forbidden_name"),
        ("x = input()\n" + STEP, "forbidden_name"),
        ("breakpoint()\n" + STEP, "forbidden_name"),
        ("x = object\n" + STEP, "forbidden_name"),
        ("type = 3\n" + STEP, "forbidden_name"),
        # format-string tricks reach attributes through the format spec
        ("x = '{0.__class__}'.format(1)\n" + STEP, "forbidden_attribute"),
        ("x = '{a}'.format_map({'a': 1})\n" + STEP, "forbidden_attribute"),
        ("x = str.format('{0}', 1)\n" + STEP, "forbidden_attribute"),
        ("x = f'{1:>99999999}'\n" + STEP, "forbidden_format"),
        # generator, coroutine, frame and code objects
        ("g = (i for i in [1])\nx = g.gi_frame\n" + STEP, "forbidden_attribute"),
        ("g = (i for i in [1])\nx = g.gi_code\n" + STEP, "forbidden_attribute"),
        ("x = step.f_globals\n" + STEP, "forbidden_attribute"),
        ("x = step.co_consts\n" + STEP, "forbidden_attribute"),
        ("x = step.tb_frame\n" + STEP, "forbidden_attribute"),
        ("x = int.mro()\n" + STEP, "forbidden_attribute"),
        ("def f():\n    yield 1\n" + STEP, "forbidden_syntax"),
        ("def f():\n    yield from [1]\n" + STEP, "forbidden_syntax"),
        ("async def f():\n    pass\n" + STEP, "forbidden_syntax"),
        # statements outside the whitelist
        ("class A:\n    pass\n" + STEP, "forbidden_syntax"),
        ("x = 1\ndef f():\n    global x\n" + STEP, "forbidden_syntax"),
        ("def f():\n    x = 1\n    def g():\n        nonlocal x\n" + STEP, "forbidden_syntax"),
        ("with x:\n    pass\n" + STEP, "forbidden_syntax"),
        ("raise Exception('x')\n" + STEP, "forbidden_syntax"),
        ("x = 1\ndel x\n" + STEP, "forbidden_syntax"),
        ("match 1:\n    case 1:\n        pass\n" + STEP, "forbidden_syntax"),
        ("try:\n    pass\nfinally:\n    pass\n" + STEP, "forbidden_syntax"),
        ("try:\n    pass\nexcept:\n    pass\n" + STEP, "forbidden_except"),
        ("try:\n    pass\nexcept BaseException:\n    pass\n" + STEP, "forbidden_except"),
        ("try:\n    pass\nexcept (Exception, KeyboardInterrupt):\n    pass\n" + STEP, "forbidden_except"),
        ("@clamp\ndef f():\n    pass\n" + STEP, "forbidden_syntax"),
        ("x = b'abc'\n" + STEP, "forbidden_syntax"),
        # unknown names (would only fail at run time)
        ("x = os\n" + STEP, "unknown_name"),
        ("x = hasattr\n" + STEP, "forbidden_name"),
        ("x = BaseException\n" + STEP, "unknown_name"),
        # program shape
        ("def init(ctx):\n    pass\n", "no_step"),
        ("def step():\n    pass\n", "step_signature"),
        ("def step(obs, ctx)\n    pass\n", "syntax"),
        ('PARAMS = {"k": [1, 2]}\n' + STEP, "params_value"),
        ('PARAMS = {"_k": 1}\n' + STEP, "params_value"),
        ('PARAMS = {"k": "' + "x" * 40 + '"}\n' + STEP, "params_value"),
        ("PARAMS = dict(k=1)\n" + STEP, "params_literal"),
        ('PARAMS = {"k": 1}\nPARAMS = {"k": 2}\n' + STEP, "params_literal"),
        ('PARAMS = {"k": 1}\nPARAM_RANGES = {"k": [2, 1]}\n' + STEP, "params_value"),
        ('PARAMS = {"k": 1}\nPARAM_RANGES = {"j": [0, 1]}\n' + STEP, "params_value"),
    ],
)
def test_refused(source, code):
    refused(source, code)


def test_problems_carry_line_and_column():
    result = validator.validate("x = 1\ny = obs.__dict__\n" + STEP)
    (problem,) = result.errors
    assert (problem.line, problem.col) == (2, 5) and problem.message_zh and problem.message_en


def test_size_and_depth_limits():
    refused("x = 1\n" * 20000 + STEP, "too_large")
    nested = "x = " + "[" * 80 + "]" * 80 + "\n" + STEP
    refused(nested, "too_deep")
    assert problems("x = (" * 1000) == ["syntax"]  # the parser's own limit, reported, not raised


def test_non_text_source():
    assert problems(None) == ["invalid_source"]  # type: ignore[arg-type]


# ---------------------------------------------------------------------------- runtime bounds


def run_step(body: str, hard_ms: float = 50.0, params: dict | None = None) -> dict:
    source = (
        "def step(obs, ctx):\n"
        + "".join(f"    {line}\n" for line in body.splitlines())
        + ("    return Sticks(throttle=0.5)\n")
    )
    engine = Engine()
    loaded = engine.load("p", source, params or {}, 0)
    assert loaded["ok"], loaded
    return engine.step("p", {"t": 0.0}, hard_ms)


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ("x = 10 ** 10 ** 9", "OverflowError"),
        ("x = pow(10, 10 ** 6)", "OverflowError"),
        ("x = 2 ** 100000", "OverflowError"),
        ("x = 1 << 100000", "OverflowError"),
        ("x = [0] * 10 ** 8", "OverflowError"),
        ("x = 'a' * 10 ** 9", "OverflowError"),
        ("x = (1,) * 10 ** 7", "OverflowError"),
        ("x = list(range(10 ** 9))", "OverflowError"),
        ("x = sum(range(10 ** 12))", "OverflowError"),
        ("x = 'ab'\nfor i in range(40):\n    x = x + x", "OverflowError"),
        ("x = 7\nfor i in range(40):\n    x = x * x", "OverflowError"),
        ("x = 3\nx **= 100000", "OverflowError"),
        ("x = '%099999999d' % 1", "OverflowError"),
        ("import math\nx = math.factorial(10 ** 6)", "OverflowError"),
        ("import math\nx = math.comb(10 ** 6, 5)", "OverflowError"),
        ("import math\nx = math.prod(range(1, 100000))", "OverflowError"),
        ("x = int('9' * 5000)", "ValueError"),
        ("x = sorted(range(10 ** 6))", "OverflowError"),
    ],
)
def test_huge_values_are_refused_at_run_time(body, fragment):
    res = run_step(body)
    assert not res["ok"] and fragment in res["error"], res


def test_endless_loops_are_interrupted():
    for body in (
        "while True:\n    pass",
        "x = 0\nwhile True:\n    x += 1",
        "x = [i for i in range(99999) for j in range(99999)]",
    ):
        res = run_step(body, hard_ms=5.0)
        assert not res["ok"] and "limit" in res["error"], res


def test_except_exception_cannot_swallow_the_interrupt():
    body = "while True:\n    try:\n        while True:\n            pass\n    except Exception:\n        pass"
    res = run_step(body, hard_ms=5.0)
    assert not res["ok"] and "limit" in res["error"]


def test_recursion_bomb_fails_the_frame():
    source = "def f(n):\n    return f(n + 1)\n\ndef step(obs, ctx):\n    f(0)\n    return Sticks()\n"
    engine = Engine()
    assert engine.load("r", source, {}, 0)["ok"]
    res = engine.step("r", {"t": 0.0}, 200.0)
    assert not res["ok"] and "RecursionError" in res["error"]
    # the engine keeps working afterwards
    assert engine.load("ok", STEP, {}, 0)["ok"] and engine.step("ok", {"t": 0.0})["ok"]


def test_import_is_limited_to_math_at_run_time_too():
    with pytest.raises(ImportError):
        lib._restricted_import("os")
    assert lib._restricted_import("math") is lib.SAFE_MATH
    assert not hasattr(lib.SAFE_MATH, "factorial") or lib.SAFE_MATH.factorial(5) == 120


def test_library_objects_cannot_be_changed_by_programs():
    for body in (
        "Sticks.roll = 1",
        "PID.step = 1",
        "clamp.leak = 1",
        "relay.clamp = 1",
        "import math\nmath.sin = 1",
        "obs.alt = 5",
        "ctx.params.k = 1",
        "ctx.extra = 1",
    ):
        res = run_step(body, params={"k": 1})
        assert not res["ok"], body
    # nothing leaked into the next program
    engine = Engine()
    assert engine.load("x", "def step(obs, ctx):\n    return Sticks(throttle=clamp(2, 0, 1))\n", {}, 0)["ok"]
    assert engine.step("x", {"t": 0})["out"][3] == 1.0


def test_builtins_are_the_whitelist():
    source = "def step(obs, ctx):\n    x = memoryview\n    return Sticks()\n"
    assert "forbidden_name" in problems(source)
    globals_ = lib.program_globals(lib.Ctx({}, 0))
    names = set(globals_["__builtins__"])
    assert names == set(lib.BUILTIN_NAMES) | {"Exception", "True", "False", "None", "__import__"}


def test_validator_survives_hostile_sources():
    for source in (
        "\x00",
        "def step(obs, ctx):\n    return '\udcff'\n",
        "x = 1e400\n" + STEP,
        "lambda: (yield)\n",
    ):
        validator.validate(source)  # never raises
