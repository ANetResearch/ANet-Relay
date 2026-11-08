"""The program-host process (06 §2.4): framing, budgets, process limits, crashes and restarts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from relay.programs import host as proto
from relay.programs import hostclient
from relay.programs.host import Engine
from relay.programs.hostclient import HostClient, run_tests_isolated

LOOP = "def step(obs, ctx):\n    while True:\n        pass\n"
HOVER = "def step(obs, ctx):\n    ctx.log('alt', obs.alt)\n    return Sticks(throttle=0.5)\n"


@pytest.fixture
def client():
    c = HostClient()
    c.start()
    yield c
    c.kill()


def test_frames_round_trip():
    data = proto.encode(proto.STEP, {"id": 3, "items": []})
    assert data[:2] == b"RP" and len(data) == proto.HEADER.size + len(data[proto.HEADER.size :])
    import io

    assert proto.read_frame(io.BytesIO(data)) == (proto.STEP, {"id": 3, "items": []})
    assert proto.read_frame(io.BytesIO(b"")) is None
    with pytest.raises(ValueError):
        proto.read_frame(io.BytesIO(b"XX" + data[2:]))


def test_host_starts_with_limits_and_answers(client):
    hello = client.hello
    assert hello["version"] == proto.VERSION and hello["pid"] == client.pid
    if sys.platform.startswith("linux"):
        assert hello["interrupt"] == "signal"
        assert hello["limits"]["as"] == proto.MEMORY_LIMIT and hello["limits"]["fsize"] == 0
        limits = Path(f"/proc/{client.pid}/limits").read_text()
        assert "Max address space" in limits and str(proto.MEMORY_LIMIT) in limits
    assert client.load("a", HOVER, {}, 1)["ok"]
    reply = client.step([("a", {"t": 0.0, "alt": 1.0})], 0.015, 8.0)
    assert not reply.timed_out and reply.results["a"]["out"] == [0.0, 0.0, 0.0, 0.5, False]
    assert reply.results["a"]["logs"] == ["alt 1.0"]
    assert client.set_params("a", {"k": 1}) and not client.set_params("missing", {})


def test_host_inherits_no_handles_and_no_secrets(client, tmp_path):
    if not sys.platform.startswith("linux"):
        pytest.skip("reads /proc")
    fds = sorted(int(n) for n in os.listdir(f"/proc/{client.pid}/fd"))
    assert fds[:3] == [0, 1, 2]
    targets = [os.readlink(f"/proc/{client.pid}/fd/{n}") for n in fds]
    assert not any("history.db" in t or t.startswith("socket:") for t in targets), targets
    environ = Path(f"/proc/{client.pid}/environ").read_bytes().split(b"\0")
    names = {e.split(b"=", 1)[0].decode() for e in environ if e}
    assert "RELAY_OPERATOR_TOKEN" not in names and "RELAY_LIFELINE" not in names
    assert names <= set(hostclient.host_env())


def test_a_hung_program_does_not_take_the_batch(client):
    assert client.load("loop", LOOP, {}, 0)["ok"]
    assert client.load("ok", HOVER, {}, 0)["ok"]
    reply = client.step([("loop", {"t": 0}), ("ok", {"t": 0, "alt": 1})], 0.015, 4.0)
    assert not reply.timed_out
    assert not reply.results["loop"]["ok"] and "limit" in reply.results["loop"]["error"]
    assert reply.results["ok"]["ok"]


def test_memory_limit_turns_into_a_frame_failure(client):
    if not sys.platform.startswith("linux"):
        pytest.skip("RLIMIT_AS")
    hog = "def step(obs, ctx):\n    x = ['a' * 900000 + str(i) for i in range(900)]\n    return Sticks()\n"
    assert client.load("hog", hog, {}, 0)["ok"]
    reply = client.step([("hog", {"t": 0})], 2.0, 1500.0)
    assert not reply.results["hog"]["ok"] and "MemoryError" in reply.results["hog"]["error"]
    assert client.alive and client.load("ok", HOVER, {}, 0)["ok"]


def test_growing_state_is_dropped(monkeypatch):
    monkeypatch.setattr(proto, "STATE_CHECK_S", 0.0)
    monkeypatch.setattr(proto, "STATE_LIMIT_BYTES", 2 * 1024 * 1024)
    source = textwrap.dedent(
        """
        def init(ctx):
            ctx.mem["junk"] = []

        def step(obs, ctx):
            ctx.mem["junk"].append("x" * 200000 + str(obs.t))
            return Sticks()
        """
    )
    engine = Engine()
    assert engine.load("grow", source, {}, 0)["ok"]
    for i in range(30):
        res = engine.step("grow", {"t": i})
        if not res["ok"]:
            break
    assert res.get("fatal") and "memory" in res["error"]
    assert "grow" not in engine.programs


def test_load_errors_are_reported(client):
    bad = client.load("bad", "import os\n" + HOVER, {}, 0)
    assert not bad["ok"] and "line 1" in bad["error"] and bad["problems"][0]["code"] == "forbidden_import"
    slow = client.load("slow", "x = 0\nwhile True:\n    x += 1\n" + HOVER, {}, 0)
    assert not slow["ok"] and "100 ms" in slow["error"]
    boom = client.load("boom", "def init(ctx):\n    1 / 0\n" + HOVER, {}, 0)
    assert not boom["ok"] and "ZeroDivisionError" in boom["error"] and "line 2" in boom["error"]


def test_dead_host_is_reported(client):
    assert client.load("a", HOVER, {}, 0)["ok"]
    os.kill(client.pid, 9)
    client.proc.wait()
    reply = client.step([("a", {"t": 0})], 0.015, 8.0)
    assert reply.dead and reply.running is None  # died between batches: no program to blame


def test_unit_tests_run_in_a_one_shot_host():
    tests = [
        {"name": "level", "obs": {"alt": 1.0}, "expect": {"throttle": [0.4, 0.6], "roll": [0, 0]}},
        {"name": "wrong", "obs": {}, "expect": {"throttle": [0.9, 1.0], "state": "x"}},
        {"name": "fire", "obs": {}, "expect": {"fire": True}},
    ]
    out = run_tests_isolated(HOVER, {}, tests)
    assert out["passed"] == 1 and out["total"] == 3
    wrong = out["results"][1]
    assert not wrong["passed"] and {f["field"] for f in wrong["failures"]} == {"throttle", "state"}
    assert out["results"][0]["out"]["throttle"] == 0.5


def test_unit_test_defaults_hover_at_one_metre():
    source = "def step(obs, ctx):\n    ctx.state = f'{obs.alt} {obs.armed} {obs.az}'\n    return Sticks()\n"
    out = Engine().run_tests(source, {}, [{"name": "d", "obs": {}, "expect": {"state": "1.0 True 9.81"}}])
    assert out["passed"] == 1, out


def test_frozen_and_source_commands(monkeypatch):
    assert hostclient.host_command()[1:] == ["-m", "relay", "--program-host"]
    assert hostclient.host_env()["PYTHONPATH"].endswith("backend")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert hostclient.host_command() == [sys.executable, "--program-host"]
    assert "PYTHONPATH" not in hostclient.host_env()


def test_main_dispatches_before_the_server_imports():
    backend = Path(__file__).resolve().parents[1]
    code = (
        "import sys, relay.__main__; "
        "print(json.dumps(['uvicorn' in sys.modules, 'relay.fleet' in sys.modules]))"
    )
    out = subprocess.run(
        [sys.executable, "-c", "import json; " + code],
        cwd=backend,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONPATH": str(backend)},
    )
    assert json.loads(out.stdout) == [False, False]


def hanging_host(tmp_path: Path) -> list[str]:
    """A fake host that answers HELLO and LOAD but never finishes a STEP."""
    script = tmp_path / "hang_host.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import sys, time
            sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
            from relay.programs import host as p
            out = sys.stdout.buffer
            out.write(p.encode(p.HELLO, {{"pid": 0, "version": 1, "interrupt": "none", "limits": {{}}}}))
            out.flush()
            while True:
                frame = p.read_frame(sys.stdin.buffer)
                if frame is None:
                    break
                kind, msg = frame
                if kind == p.STEP:
                    time.sleep(30)
                out.write(p.encode(p.REPLY, {{"id": msg.get("id"), "ok": True, "state": "", "logs": []}}))
                out.flush()
            """
        )
    )
    return [sys.executable, str(script)]


def test_hung_host_times_out_the_batch(tmp_path):
    c = HostClient(command=hanging_host(tmp_path))
    c.start()
    try:
        assert c.load("a", HOVER, {}, 0)["ok"]
        t0 = time.monotonic()
        reply = c.step([("a", {"t": 0})], 0.015, 8.0)
        assert reply.timed_out and reply.running == "a" and time.monotonic() - t0 < 0.2
        again = c.step([("a", {"t": 0})], 0.015, 8.0)  # still busy: times out at once
        assert again.timed_out and again.elapsed_ms < 50 and again.running == "a"
    finally:
        c.kill()
