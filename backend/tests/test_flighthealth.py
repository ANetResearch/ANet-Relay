"""Flight health diagnostics (03 §5.9)."""

import math

import pytest
from conftest import open_agent

from relay import flighthealth as fh
from relay.mav import decode
from relay.model import StickInput


def items(view):
    return {i["key"]: i for i in view["items"]}


@pytest.mark.parametrize("flavour", ["px4", "ardupilot"])
def test_steady_sim_hover_is_healthy(svc, stepper, flavour):
    (vid,) = svc.add_sims(1, flavour, False)
    stepper.run(2.5)
    v = svc.vehicles[vid]
    assert svc.fleet()["vehicles"][0]["health"]["state"] == "idle"
    session, agent = open_agent(svc)
    svc.claim(vid, agent)
    svc.run_skill(vid, agent, "takeoff", {"alt": 1.0})
    assert stepper.until(lambda: v.skill.state == "done", 20, [session])
    svc.run_skill(vid, agent, "hold", {"seconds": 8})
    stepper.run(6.0, [session])
    svc._build_snapshot(stepper.clock.t)
    view = svc.fleet()["vehicles"][0]["health"]
    got = items(view)
    assert view["airborne_samples"] >= 40
    for key in ("oscillation", "lean", "yaw_drift", "alt_hold", "hover_throttle", "motors", "vibration"):
        assert got[key]["level"] == "ok", (key, got[key])
    assert got["battery_sag"]["level"] in ("ok", "na")
    assert got["stick_stream"]["level"] == "na"  # nobody is flying by hand
    assert 30 <= got["hover_throttle"]["value"] <= 65
    assert view["state"] == "ok"


class FakeVehicle:
    def __init__(self):
        self.state = decode.fresh_state()
        self.state["mav_type"] = 2
        self.state["battery"] = {"voltage": 12.4, "current": None, "remaining_pct": None}
        self.armed = False
        self.landed = "ground"

    def cells(self):
        return 3


def fly(
    h,
    v,
    seconds,
    t0=0.0,
    roll=0.0,
    pitch=0.0,
    rate=0.0,
    motors=(1500, 1500, 1500, 1500),
    throttle=45.0,
    climb=0.0,
    sticks=None,
    volts=11.9,
    hold=True,
):
    t = t0
    v.armed, v.landed = True, "air"
    while t < t0 + seconds:
        osc = rate * math.sin(2 * math.pi * 6.0 * t)  # 6 Hz oscillation in deg/s
        v.state["attitude"] = {
            "roll": math.radians(roll),
            "pitch": math.radians(pitch),
            "yaw": 0.0,
            "rollspeed": math.radians(osc),
            "pitchspeed": math.radians(osc),
            "yawspeed": 0.0,
        }
        v.state["hud"] = {"throttle": throttle, "climb": climb * math.sin(2 * math.pi * 1.3 * t), "alt": 1.0}
        v.state["servo"] = [*motors, 0, 0, 0, 0]
        v.state["battery"]["voltage"] = volts
        h.sample(v, t, sticks, hold)
        t += 0.02
    return t


def judge(**kw):
    h = fh.FlightHealth()
    v = FakeVehicle()
    h.sample(v, 0.0, None, True)  # resting voltage while disarmed
    h.reset()
    now = fly(h, v, 5.0, t0=0.2, **kw)
    return items(h.view(v, now, {"loss_pct": 0.0, "rtt_ms": 20.0}, False))


def test_each_problem_is_named():
    calm = judge()
    assert {
        k: calm[k]["level"] for k in ("oscillation", "lean", "motors", "alt_hold", "hover_throttle")
    } == dict.fromkeys(("oscillation", "lean", "motors", "alt_hold", "hover_throttle"), "ok")
    assert judge(rate=80.0)["oscillation"]["level"] == "bad"
    leaned = judge(roll=4.0, pitch=-2.0)["lean"]
    assert leaned["level"] == "warn" and leaned["detail"] == {"roll": 4.0, "pitch": -2.0}
    # the operator holding a stick is not a lean
    assert judge(roll=8.0, sticks=StickInput(0.4, 0.0, 0.0, 0.5))["lean"]["level"] == "na"
    motors = judge(motors=(1700, 1450, 1460, 1450))["motors"]
    assert motors["level"] == "bad" and motors["detail"]["high"] == 1
    assert judge(motors=(1960, 1960, 1960, 1960))["motors"]["level"] == "bad"  # saturated
    assert judge(climb=0.8)["alt_hold"]["level"] == "bad"
    assert judge(throttle=78.0)["hover_throttle"]["level"] == "warn"
    sag = judge(volts=10.0)["battery_sag"]
    assert sag["level"] == "bad" and sag["value"] == pytest.approx(0.8, abs=0.01)


def test_link_and_stick_stream():
    h = fh.FlightHealth()
    v = FakeVehicle()
    fly(h, v, 2.0)
    for i in range(10):  # 5 Hz with 0.2 s gaps from the renderer
        h.note_stick_input(i * 0.2)
    view = items(h.view(v, 2.0, {"loss_pct": 20.0, "rtt_ms": 50.0}, True))
    assert view["link"]["level"] == "bad"
    assert view["stick_stream"]["level"] == "bad" and view["stick_stream"]["value"] == 5.0


def test_vibration_clipping_is_bad():
    h = fh.FlightHealth()
    v = FakeVehicle()
    v.state["vibration"] = {"vibration_x": 8.0, "vibration_y": 8.0, "vibration_z": 9.0, "clipping_0": 2}
    fly(h, v, 1.0)
    v.state["vibration"] = {"vibration_x": 8.0, "vibration_y": 8.0, "vibration_z": 9.0, "clipping_0": 7}
    now = fly(h, v, 1.0, t0=1.0)
    vib = items(h.view(v, now, {}, False))["vibration"]
    assert vib["level"] == "bad" and vib["detail"] == {"clipping": 5}
