"""Isolation of onboard programs (06 §2.4): one host process per deployment, nothing shared between programs,
the fleet thread never waiting for a host, memory limits on every platform, and the fixes around deployment,
stored parameters and the trigger."""

from __future__ import annotations

import sys
import textwrap
import time
from typing import ClassVar

import pytest
from test_programs_fleet import arm, boot, hang_command

from relay.authority import Actor
from relay.errors import RelayError
from relay.programs import host as proto
from relay.programs import hostclient, lib, validator
from relay.programs.host import Engine
from relay.programs.hostclient import HostClient, InProcessHost
from relay.programs.store import ProgramStore

pytestmark = pytest.mark.sticks_level

OP = Actor.operator()
HOVER = "def step(obs, ctx):\n    return Sticks(throttle=0.5)\n"


def run_engine(engine: Engine, key: str, source: str, frames: int = 1) -> dict:
    assert engine.load(key, source, {}, 0)["ok"], engine.load(key, source, {}, 0)
    res: dict = {}
    for i in range(frames):
        res = engine.step(key, {"t": float(i)})
    return res


# ============================================================================ nothing shared (finding 1)


def test_library_functions_carry_no_data_between_programs():
    writer = textwrap.dedent(
        """
        def step(obs, ctx):
            PID.step.mailbox = [1, 2, 3]
            return Sticks()
        """
    )
    assert validator.validate(writer).ok  # the language allows the attribute; the runtime refuses it
    engine = Engine()
    res = run_engine(engine, "a", writer)
    assert not res["ok"] and "cannot be changed" in res["error"]
    for target in ("StateMachine.add", "Timer.start", "Timer.done", "clamp", "relay.lerp", "math.prod"):
        source = f"import math\n\ndef step(obs, ctx):\n    {target}.box = 1\n    return Sticks()\n"
        res = run_engine(Engine(), "k", source)
        assert not res["ok"], target
    # the functions still work through the class
    pid = lib.PID(kp=2.0)
    assert lib.PID.step(pid, 0.5, 0.02) == pytest.approx(1.0)
    assert repr(lib.PID.step).startswith("<relay function")


def test_evaluator_engines_share_one_interrupt_guard():
    # several in-process engines (one per deployment in an evaluator) all keep the per-step interrupt
    first, second = Engine(), Engine()
    assert first.guard is second.guard
    loop = "def step(obs, ctx):\n    while True:\n        pass\n"
    for engine in (first, second):
        res = run_engine(engine, "loop", loop)
        assert not res["ok"] and "limit" in res["error"]


# ============================================================================ one host per deployment (2, 5)


DEEP = textwrap.dedent(
    """
    def init(ctx):
        ctx.mem["t"] = ()
        ctx.mem["n"] = 0

    def step(obs, ctx):
        t = ctx.mem["t"]
        for i in range(5000):
            t = (t,)
        ctx.mem["t"] = t
        ctx.mem["n"] += 1
        if ctx.mem["n"] >= 40:
            s = {t: 1}
        return Sticks(throttle=0.5)
    """
)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the crash is the point; Linux CI only")
def test_a_host_that_crashes_takes_down_only_its_own_program(svc, stepper):
    (a, b), session, agent = boot(svc, stepper, count=2)
    svc.programs.deploy_source(a, agent, {"id": "deep", "name": "deep"}, 1, DEEP, {})
    svc.programs.deploy(b, agent, {"program": "hover_trim"})
    hb = svc.programs.hosts[b]
    stepper.run(1.5, renew=[session])
    ra, rb = svc.programs.runs[a], svc.programs.runs[b]
    assert ra.status == "faulted" and ra.reason == "host", (ra.status, ra.last_error)
    assert rb.status == "running" and rb.faults == 0 and svc.programs.hosts[b] is hb
    lost = [e for e in svc.events.after(0) if e["kind"] == "program_host_restarted"]
    assert [e["vehicle"] for e in lost] == [a] and lost[0]["data"]["culprit"] == ra.key


BURN = textwrap.dedent(
    """
    def step(obs, ctx):
        x = 0
        for i in range(25000):
            x += i
        return Sticks(throttle=0.5)
    """
)
LIGHT = textwrap.dedent(
    """
    def step(obs, ctx):
        x = 0
        for i in range(2000):
            x += i
        return Sticks(throttle=0.5)
    """
)


def test_heavy_programs_do_not_starve_other_aircraft(svc, stepper):
    (a, b, c), session, agent = boot(svc, stepper, count=3)
    svc.programs.deploy_source(a, agent, {"id": "b1", "name": "burn"}, 1, BURN, {})
    svc.programs.deploy_source(b, agent, {"id": "b2", "name": "burn"}, 1, BURN, {})
    svc.programs.deploy_source(c, agent, {"id": "v", "name": "victim"}, 1, LIGHT, {})
    stepper.run(1.2, renew=[session])
    victim = svc.programs.runs[c]
    assert victim.status == "running" and victim.faults == 0 and victim.frames >= 55, victim.last_error


# ============================================================================ the fleet never waits (3, 4)


class RecordingHost(InProcessHost):
    """In-process host that notes where ``load`` ran and can be told to fail."""

    loads: ClassVar[list[bool]] = []
    fail = False

    def __init__(self, svc) -> None:
        super().__init__()
        self.svc = svc

    def load(self, key, source, params, seed):
        RecordingHost.loads.append(self.svc._in_fleet_call)
        if RecordingHost.fail:
            return {"ok": False, "error": "ZeroDivisionError: division by zero (line 2)"}
        return super().load(key, source, params, seed)


def test_deploy_loads_off_the_fleet_thread_and_a_failed_load_keeps_the_old_run(svc, stepper, monkeypatch):
    svc._in_fleet_call = False
    real_call = svc.call

    def call(fn, timeout=None):
        def wrapped():
            svc._in_fleet_call = True
            try:
                return fn()
            finally:
                svc._in_fleet_call = False

        return real_call(wrapped) if timeout is None else real_call(wrapped, timeout)

    monkeypatch.setattr(svc, "call", call)
    RecordingHost.loads = []
    RecordingHost.fail = False
    svc.programs.host_factory = lambda: RecordingHost(svc)
    (vid,), session, agent = boot(svc, stepper)
    arm(svc, stepper, vid, agent, session)
    svc.programs.deploy(vid, agent, {"program": "hover_trim"})
    stepper.run(0.5, renew=[session])
    old = svc.programs.runs[vid]
    assert old.status == "running" and RecordingHost.loads == [False]
    RecordingHost.fail = True
    broken = "def init(ctx):\n    ctx.mem['x'] = 1 / 0\n" + HOVER
    with pytest.raises(RelayError) as exc:
        svc.programs.deploy_source(vid, agent, {"id": "b", "name": "broken"}, 2, broken, {})
    assert exc.value.code == "program_load_failed"
    assert RecordingHost.loads == [False, False]  # neither load ran inside a fleet call
    assert svc.programs.runs[vid] is old and old.status == "running"  # still flying
    stepper.run(0.2, renew=[session])
    assert old.frames > 0 and svc.vehicles[vid].sticks.source == "program"
    assert not [e for e in svc.events.after(0) if e["kind"] == "program_stopped"]


def test_emergency_stop_does_not_wait_for_a_busy_host(svc, stepper, tmp_path):
    hosts = [HostClient(command=hang_command(tmp_path))]
    svc.programs.host_factory = lambda: hosts.pop(0) if hosts else HostClient()
    (vid,), _session, agent = boot(svc, stepper)
    svc.programs.deploy(vid, agent, {"program": "hover_trim"})
    host = svc.programs.hosts[vid]
    svc.step(svc.clock() + 0.005)  # the first frame times out: the host is busy with it for 30 s
    assert host.busy
    started = time.monotonic()
    svc.stop(vid, OP)  # emergency stop
    assert time.monotonic() - started < 0.25
    run = svc.programs.runs[vid]
    assert run.status == "stopped" and run.reason == "emergency_stop" and not host.alive


def test_live_params_travel_with_the_next_frame(svc, stepper):
    (vid,), session, agent = boot(svc, stepper)
    source = textwrap.dedent(
        """
        PARAMS = {"k": 1.0}

        def step(obs, ctx):
            ctx.state = "k" + str(ctx.params["k"])
            return Sticks()
        """
    )
    svc.programs.deploy_source(vid, agent, {"id": "p", "name": "p"}, 1, source, {})
    stepper.run(0.1, renew=[session])
    run = svc.programs.runs[vid]
    assert run.state == "k1.0"
    svc.programs.set_params(vid, agent, {"k": 2.5})
    assert run.pending_params == {"k": 2.5}
    stepper.run(0.05, renew=[session])
    assert run.state == "k2.5" and run.pending_params == {}


# ============================================================================ size and memory (6, 7)


def test_computed_format_specs_and_star_widths_are_refused():
    for source in ("w = 10\nx = f'{1:{w}}'\n", "n = 7\nx = f'{1.5:.{n}f}'\n"):
        problems = validator.validate(source + HOVER).errors
        assert [p.code for p in problems] == ["forbidden_format"], source
    assert validator.validate("x = f'{1.5:.3f} {2:>8}'\n" + HOVER).ok
    with pytest.raises(OverflowError):
        lib.relay_op("mod", "%*d", (50000000, 1))
    with pytest.raises(OverflowError):
        lib.relay_op("mod", "%(a)*d", {"a": 1})
    assert lib.relay_op("mod", "%d * %d = %5.2f %%", (2, 3, 6.0)) == "2 * 3 =  6.00 %"
    assert lib.relay_op("mod", "%(a)s*", {"a": "x"}) == "x*"


def test_state_held_by_closures_iterators_and_generators_is_counted(monkeypatch):
    monkeypatch.setattr(proto, "STATE_CHECK_S", 0.0)
    monkeypatch.setattr(proto, "STATE_LIMIT_BYTES", 2 * 1024 * 1024)
    sources = {
        "closure": """
            def make():
                buf = []
                def add():
                    buf.append("x" * 200000 + str(len(buf)))
                    return len(buf)
                return add

            add = make()

            def step(obs, ctx):
                add()
                return Sticks()
            """,
        "default": """
            def keep(x, store=[]):
                store.append(x)

            def step(obs, ctx):
                keep("y" * 200000 + str(obs.t))
                return Sticks()
            """,
        "iterator": """
            def init(ctx):
                ctx.mem["it"] = map(len, ["z" * 200000 + str(i) for i in range(30)])

            def step(obs, ctx):
                return Sticks()
            """,
        "generator": """
            def init(ctx):
                ctx.mem["g"] = (len(s) for s in ["w" * 200000 + str(i) for i in range(30)])

            def step(obs, ctx):
                return Sticks()
            """,
    }
    for name, source in sources.items():
        engine = Engine()
        assert engine.load(name, textwrap.dedent(source), {}, 0)["ok"], name
        res: dict = {}
        for i in range(30):
            res = engine.step(name, {"t": float(i)})
            if not res["ok"]:
                break
        assert res.get("fatal") and "memory" in res["error"], (name, res)


@pytest.mark.skipif(sys.platform == "win32", reason="reads the resident set through procmem")
def test_the_memory_watchdog_kills_a_host_from_outside(monkeypatch):
    monkeypatch.setattr(hostclient, "MEMORY_POLL_S", 0.005)
    client = HostClient()
    client.memory_limit = 120 * 1024 * 1024
    client.start()
    try:
        hog = textwrap.dedent(
            """
            def step(obs, ctx):
                ctx.mem["x"] = []
                while True:  # about 1 MB a millisecond: the watchdog sees it long before RLIMIT_AS
                    ctx.mem["x"].append("a" * 900000 + str(len(ctx.mem["x"])))
                    for k in range(3000):
                        pass
            """
        )
        assert client.load("hog", hog, {}, 0)["ok"]
        reply = client.step([("hog", {"t": 0})], 3.0, 2500.0)
        deadline = time.monotonic() + 3.0
        while client.alive and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not client.alive and client.killed_for == "memory", reply
    finally:
        client.kill()


def test_a_host_killed_for_memory_faults_its_program(svc, stepper, monkeypatch):
    class SmallHost(HostClient):
        def __init__(self) -> None:
            super().__init__()
            self.memory_limit = 120 * 1024 * 1024

    svc.programs.host_factory = SmallHost
    (a, b), session, agent = boot(svc, stepper, count=2)
    hog = textwrap.dedent(
        """
        def init(ctx):
            ctx.mem["x"] = []

        def step(obs, ctx):
            for i in range(3):
                ctx.mem["x"].append("a" * 900000 + str(obs.t) + str(i))
            return Sticks(throttle=0.5)
        """
    )
    svc.programs.deploy_source(a, agent, {"id": "hog", "name": "hog"}, 1, hog, {})
    svc.programs.deploy(b, agent, {"program": "hover_trim"})
    ra = svc.programs.runs[a]
    deadline = time.monotonic() + 10.0
    while ra.active and time.monotonic() < deadline:
        stepper.run(0.05, renew=[session])
    assert ra.status == "faulted" and ra.reason == "memory", (ra.status, ra.reason, ra.last_error)
    assert svc.programs.runs[b].status == "running"


# ============================================================================ stored parameters (11, 14)


SRC_AB = 'PARAMS = {"a": 1.0, "b": 2.0}\n' + HOVER
SRC_B = 'PARAMS = {"b": 2.0}\nPARAM_RANGES = {"b": [0.0, 5.0]}\n' + HOVER


def test_editing_params_drops_overrides_that_no_longer_fit(tmp_path):
    store = ProgramStore(tmp_path)
    pid = store.create({"name": "p", "source": SRC_AB, "params": {"a": 2.0, "b": 9.0}}, "operator")["id"]
    out = store.update(pid, {"source": SRC_B}, "operator")
    assert out["params"] == {}  # a is gone, b = 9 is out of the new range
    assert store.revision(pid, None)["params"] == {}
    store.update(pid, {"params": {"b": 4.0}}, "operator")
    out = store.update(pid, {"source": SRC_B + "\n# comment\n"}, "operator")
    assert out["params"] == {"b": 4.0}  # still fits: kept


def test_deploy_ignores_stored_overrides_outside_params(svc, stepper):
    (vid,), _session, agent = boot(svc, stepper)
    store = svc.programs.store
    pid = store.create({"name": "p", "source": SRC_AB, "params": {"a": 2.0}}, "operator")["id"]
    # a revision saved before the fix still carries the stale override
    program = store._find(pid)
    program["source"] = SRC_B
    program["revisions"][-1]["source"] = SRC_B
    out = svc.programs.deploy(vid, agent, {"program": pid})
    assert out["program"]["params"] == {"b": 2.0}
    with pytest.raises(RelayError) as exc:
        svc.programs.deploy(vid, agent, {"program": pid, "params": {"a": 1.0}})
    assert exc.value.code == "program_param_invalid"  # given in the request: still strict


def test_judge_entrant_ignores_stale_stored_overrides(svc):
    store = svc.programs.store
    pid = store.create({"name": "p", "source": SRC_AB, "params": {"a": 2.0}}, "operator")["id"]
    program = store._find(pid)
    program["revisions"][-1]["source"] = SRC_B
    entrant = svc.judge._entrant(pid)
    assert entrant["params"] == {"b": 2.0}


def test_a_refused_update_changes_nothing(tmp_path):
    store = ProgramStore(tmp_path)
    pid = store.create({"name": "orig", "source": SRC_AB}, "operator")["id"]
    with pytest.raises(RelayError):
        bad_tests = [{"expect": {"roll": "bad"}}]
        store.update(pid, {"name": "renamed", "description": "d", "tests": bad_tests}, "op")
    with pytest.raises(RelayError):
        store.update(pid, {"name": "renamed", "source": SRC_B, "note": 5}, "op")
    got = store.get(pid)
    assert got["name"] == "orig" and got["description"] == "" and got["source"] == SRC_AB
    assert got["rev"] == 1


# ============================================================================ the trigger (13)


def test_fire_false_releases_the_trigger_at_once(svc, stepper):
    svc.update_settings({"peer_enabled": True})
    (vid,), session, agent = boot(svc, stepper)
    arm(svc, stepper, vid, agent, session)
    source = textwrap.dedent(
        """
        PARAMS = {"shoot": True}

        def step(obs, ctx):
            thr = 0.7 if obs.alt < 1 else 0.45
            return Sticks(throttle=thr, fire=ctx.params["shoot"] and obs.alt > 0.6)
        """
    )
    svc.programs.deploy_source(vid, agent, {"id": "g", "name": "gunner"}, 1, source, {})
    gun = None
    for _ in range(80):
        stepper.run(0.05, renew=[session])
        gun = svc.arena.guns.get(vid)
        if gun is not None and gun.fire_until > svc.clock():
            break
    assert gun is not None and gun.fire_until > svc.clock()
    svc.programs.set_params(vid, agent, {"shoot": False})
    stepper.run(0.04, renew=[session])
    assert gun.fire_until <= svc.clock()


def test_a_persons_fire_is_not_released_by_the_program(svc, stepper):
    svc.update_settings({"peer_enabled": True})
    (vid,), session, agent = boot(svc, stepper)
    arm(svc, stepper, vid, agent, session)
    run_source = "def step(obs, ctx):\n    return Sticks(throttle=0.7 if obs.alt < 1 else 0.45)\n"
    svc.programs.deploy_source(vid, agent, {"id": "q", "name": "quiet"}, 1, run_source, {})
    stepper.run(2.0, renew=[session])
    v = svc.vehicles[vid]
    if not v.airborne():
        pytest.skip("did not lift off")
    svc.arena.fire(v, True, 2.0, svc.clock())  # someone else holds the trigger for 2 s
    stepper.run(0.1, renew=[session])
    assert svc.arena.guns[vid].fire_until > svc.clock()


# ============================================================================ sticks level (8)


def test_sticks_level_refuses_drift_and_hover_assist(svc, stepper):
    (vid,), _session, agent = boot(svc, stepper)
    for mode in ("DRIFT", "ALT_HOLD"):
        with pytest.raises(RelayError) as exc:
            svc.set_mode(vid, agent, mode)
        assert exc.value.code == "sticks_only"
    svc.set_mode(vid, agent, "ACRO")  # manual modes stay
    with pytest.raises(RelayError) as exc:
        svc.set_sim_assist(vid, True, agent, app_control=True)
    assert exc.value.code == "sticks_only"
    assert svc.set_sim_assist(vid, False, agent)["hover_assist"] is False  # switching it off is fine
    assert svc.set_sim_assist(vid, True, OP)["hover_assist"] is True  # the operator is never limited
    svc.settings.agent_flight_level = "full"
    svc.set_mode(vid, agent, "DRIFT")
    assert svc.set_sim_assist(vid, False, agent)["hover_assist"] is False
