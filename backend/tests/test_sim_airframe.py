"""Airframe defects of the simulator (06 §4, WP-2a): presets, physics and sensor outputs."""

import math
import statistics

import pytest
from conftest import SimPilot

from relay.mav.mavlink import M
from relay.model import StickInput
from relay.sim import airframe as afm
from relay.sim import physics
from relay.sim.airframe import Airframe, AirframeModel, generate, resolve


def custom(**values) -> Airframe:
    return Airframe().with_params(values)


def pilot(af: Airframe | None = None, *, assist: bool = False, turbulence: float = 0.0, name="sim-1"):
    p = SimPilot("ardupilot", name=name, hover_assist=assist, turbulence=turbulence, airframe=af)
    p.run(0.2)
    p.arm(True)
    p.run(0.2)
    assert p.sim.armed
    return p


def hover(p: SimPilot, seconds: float, alt: float = 1.0, sample=None) -> None:
    """A naive pilot: constant hover throttle plus a height loop on the true altitude, sticks centred.

    It never corrects attitude, so whatever the airframe does horizontally shows up as drift.
    """
    q = p.sim.quad
    t = 0.0
    while t < seconds - 1e-9:
        thr = q.hover_throttle(alt) + 0.25 * (alt - q.s.p[2]) - 0.15 * q.s.v[2]
        p.sticks = StickInput(0, 0, 0, min(1.0, max(0.0, thr)))
        p.run(0.02)
        t += 0.02
        if sample is not None:
            sample(t)


def lift(p: SimPilot, alt: float = 0.8) -> None:
    for _ in range(300):
        hover(p, 0.02)
        if p.z >= alt:
            return
    raise AssertionError("did not lift")


def drift_over(p: SimPilot, seconds: float) -> float:
    lift(p)
    start = list(p.sim.quad.s.p)
    worst = [0.0]

    def track(_t):
        s = p.sim.quad.s.p
        worst[0] = max(worst[0], math.hypot(s[0] - start[0], s[1] - start[1]))

    hover(p, seconds, sample=track)
    return worst[0]


# ---------------------------------------------------------------------------- presets


def test_presets_are_deterministic_per_seed():
    for preset in ("typical", "rough"):
        assert generate(preset, 42) == generate(preset, 42)
        assert generate(preset, 42).params() != generate(preset, 43).params()
        assert generate(preset, 42).ref() == {"preset": preset, "seed": 42}
    ideal = generate("ideal", 5)
    assert ideal.nominal and ideal.ref() == {"preset": "ideal", "seed": 5}
    assert generate("typical", 1).imu_noise == 1.0 and not generate("typical", 1).nominal
    rough = generate("rough", 1)
    assert rough.prop_damage >= 0.2 and math.hypot(*rough.cg_offset_cm) >= 0.7 - 1e-9
    assert math.hypot(*rough.level_trim_deg) >= 1.0 - 1e-9
    with pytest.raises(ValueError):
        generate("custom", 1)
    with pytest.raises(ValueError):
        generate("rough", -1)


def test_presets_stay_inside_the_limits():
    for preset in ("typical", "rough"):
        for seed in range(40):
            values = generate(preset, seed).params()
            assert afm.validate_params(values) == values


def test_params_are_validated():
    assert custom(payload_kg=0.05).payload_kg == 0.05
    for bad in (
        {"payload_kg": 0.5},
        {"cg_offset_cm": [1.0]},
        {"motor_gain": [0.1, 0, 0, 0]},
        {"damaged_prop": 5},
        {"damaged_prop": 1.5},
        {"imu_noise": True},
        {"mass_kg": float("nan")},
        {"wind": 1},
    ):
        with pytest.raises(ValueError):
            Airframe().with_params(bad)


def test_resolve_body_semantics():
    seeds = iter([11, 12, 13])  # the ideal base in the overlay case draws one too

    def fresh():
        return next(seeds)

    assert resolve(None, None, "typical", fresh) == generate("typical", 11)
    assert resolve({"preset": "rough", "seed": 3}, None, "typical", fresh) == generate("rough", 3)
    current = generate("rough", 3)
    tuned = resolve({"params": {"payload_kg": 0.04}}, current, "typical", fresh)
    assert tuned.preset == "custom" and tuned.payload_kg == 0.04
    assert tuned.cg_offset_cm == current.cg_offset_cm
    over = resolve({"preset": "ideal", "params": {"imu_noise": 2.0}}, current, "typical", fresh)
    assert over.preset == "custom" and over.imu_noise == 2.0 and not any(over.cg_offset_cm)
    assert resolve({"preset": "typical"}, None, "ideal", fresh) == generate("typical", 13)
    for bad in ({"preset": "broken"}, {"preset": "rough", "seed": -4}, {"params": {"payload_kg": 9}}):
        with pytest.raises(ValueError):
            resolve(bad, None, "typical", fresh)


def test_mixer_balances_cg_and_motors():
    model = AirframeModel(custom(cg_offset_cm=[1.0, 0.0], motor_gain=[0.05, -0.03, 0.0, 0.02]))
    assert sum(model.weights) == pytest.approx(4.0)
    k, w = model.k, model.weights
    total = sum(k[i] * w[i] for i in range(4))
    centroid = sum(k[i] * w[i] * afm.MOTOR_X[i] for i in range(4)) / total * afm.ARM_M
    assert centroid == pytest.approx(0.01)  # thrust centroid under the CG
    assert sum(k[i] * w[i] * afm.MOTOR_YAW[i] for i in range(4)) == pytest.approx(0.0, abs=1e-12)
    assert w[0] > 1.0 and w[2] > 1.0  # the front motors carry more


# ---------------------------------------------------------------------------- the 05 model is untouched


def wire(af, *, assist, turbulence=0.3, seconds=6.0):
    p = SimPilot("ardupilot", name="sim-1", hover_assist=assist, turbulence=turbulence, airframe=af)
    p.request_data_stream(M.MAV_DATA_STREAM_ALL, 20)
    p.run(0.2)
    if assist:
        p.set_mode("ALT_HOLD")
    p.arm(True)
    out = []
    for i in range(int(seconds / 0.02)):
        p.sticks = StickInput(0.2 * math.sin(i / 20), 0.1, 0.0, 0.62 if i < 60 else 0.5)
        p.run(0.02)
        out.append((*p.sim.quad.s.p, p.sim.quad.s.roll, p.sim.quad.s.pitch))
    return out, [m.to_dict() for m in p.msgs]


def test_ideal_preset_reproduces_the_05_model():
    base = wire(None, assist=False)
    assert wire(generate("ideal", 99), assist=False) == base
    assert wire(Airframe(), assist=False) == base


def test_defects_never_act_with_hover_assist_on():
    base = wire(None, assist=True)
    assert wire(generate("rough", 4), assist=True) == base


# ---------------------------------------------------------------------------- physics


def test_rough_airframe_drifts_a_naive_hover():
    for seed in range(1, 6):
        p = pilot(generate("rough", seed))
        assert drift_over(p, 20.0) > 0.8, seed
        assert not p.sim.crashed


def test_ideal_airframe_holds_a_naive_hover():
    p = pilot(generate("ideal", 1))
    assert drift_over(p, 20.0) < 0.05


def test_payload_raises_hover_throttle_until_it_cannot_lift():
    p = pilot(custom(payload_kg=0.05))
    p.sticks = StickInput(0, 0, 0, 0.5)
    p.run(4.0)
    assert p.z == 0.0 and p.sim.quad.s.on_ground  # 0.5 no longer lifts
    assert p.sim.quad.hover_throttle(1.0) > 0.6
    p.sticks = StickInput(0, 0, 0, 0.8)
    p.run(2.0)
    assert p.z > 0.5
    heavy = pilot(custom(payload_kg=0.2))
    heavy.sticks = StickInput(0, 0, 0, 1.0)
    heavy.run(3.0)
    assert heavy.z == 0.0
    assert custom(payload_kg=0.2).hover_throttle() is None
    assert custom(payload_kg=0.05).hover_throttle() == pytest.approx(0.665, abs=0.01)
    assert Airframe().hover_throttle() == pytest.approx(0.5, abs=0.002)


def test_cg_offset_is_a_real_moment_the_rate_integrator_learns():
    # v0.5 (07 §1): the CG offset is thrust applied off the centre of mass. Right after lift-off the
    # moment pitches the nose down until the pitch-rate integrator has learned it; then the airframe flies
    # level with the front rotors working harder. (v0.4 modelled a 70 % / 2 s correction with a 30 % lean.)
    p = pilot(custom(cg_offset_cm=[1.0, 0.0]))
    fc = p.sim.fc
    worst = [0.0]

    def track(_t):
        if not p.sim.quad.s.on_ground:
            worst[0] = min(worst[0], p.sim.quad.s.pitch)

    hover(p, 2.0, sample=track)
    assert worst[0] < -math.radians(0.5)  # nose down at first
    hover(p, 10.0)
    s = p.sim.quad.s
    assert abs(s.pitch) < math.radians(0.1)  # learned: level again
    assert fc.i[1] == pytest.approx(physics.MASS * physics.G * 0.01 / physics.IYY, rel=0.05)
    servo = p.sim._build("SERVO_OUTPUT_RAW", p.t)
    front, rear = (servo.servo1_raw, servo.servo3_raw), (servo.servo2_raw, servo.servo4_raw)
    assert min(front) > max(rear) + 50  # the mixer's real outputs: the front pair carries the CG
    reported = p.sim._build("ATTITUDE", p.t)
    assert reported.pitch == pytest.approx(s.pitch)  # the board sees the true attitude (no noise set)
    p.sticks = StickInput(0, 0, 0, 0.0)
    p.run(6.0)
    assert p.sim.quad.s.on_ground and fc.i == [0.0, 0.0, 0.0]  # reset once landed


def test_level_trim_reads_level_but_drifts():
    p = pilot(custom(level_trim_deg=[1.5, 0.0]))
    lift(p)
    hover(p, 6.0)
    s = p.sim.quad.s
    assert math.degrees(s.roll) == pytest.approx(1.5, abs=0.05)
    att = p.sim._build("ATTITUDE", p.t)
    assert abs(math.degrees(att.roll)) < 0.05  # the flight controller believes it is level
    assert s.v[0] > 0.1  # right wing down at heading 0: drifts east
    p.sticks = StickInput(0, 0, 0, 0.0)
    p.run(8.0)
    assert math.degrees(p.sim._build("ATTITUDE", p.t).roll) == pytest.approx(-1.5, abs=0.05)  # on the ground


def test_motor_efficiency_turns_the_aircraft():
    # v0.5: the CCW pair makes more thrust and so more reaction torque; the slow yaw integrator takes it up
    # over several seconds (v0.4: a fixed 30 % residual yaw rate).
    p = pilot(custom(motor_gain=[0.06, 0.06, -0.06, -0.06]))
    assert p.sim.quad.airframe.yaw_imbalance > 0  # the CCW pair is stronger: the body yaws clockwise
    lift(p)
    yaw0 = p.sim.quad.s.yaw
    hover(p, 1.0)
    early = p.sim._build("ATTITUDE", p.t).yawspeed
    hover(p, 9.0)
    turned = math.degrees(physics_wrap(p.sim.quad.s.yaw - yaw0))
    assert turned > 20.0
    late = p.sim._build("ATTITUDE", p.t).yawspeed
    assert math.degrees(early) > 5.0 and 0.0 < late < 0.6 * early  # turning, more slowly as it learns


def physics_wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def test_prop_damage_shakes_and_loads_one_motor():
    clean = pilot(None)
    damaged = pilot(custom(prop_damage=0.8, damaged_prop=3))
    for p in (clean, damaged):
        lift(p)
        hover(p, 3.0)
    vib_clean = clean.sim._build("VIBRATION", clean.t).vibration_x
    vib = damaged.sim._build("VIBRATION", damaged.t).vibration_x
    assert vib > vib_clean + 15.0
    servo = damaged.sim._build("SERVO_OUTPUT_RAW", damaged.t)
    outputs = [servo.servo1_raw, servo.servo2_raw, servo.servo3_raw, servo.servo4_raw]
    assert max(outputs) == outputs[2] and outputs[2] - min(outputs) > 40
    assert damaged.sim.quad.hover_throttle(1.0) > clean.sim.quad.hover_throttle(1.0)
    rates = []
    hover(damaged, 2.0, sample=lambda _t: rates.append(damaged.sim.quad.s.rates[0]))
    assert statistics.pstdev(rates) > math.radians(1.0)  # jitter in the attitude itself


def test_saturation_loses_control_authority():
    af = custom(cg_offset_cm=[3.0, 0.0], payload_kg=0.11)
    model = AirframeModel(af)
    F = 0.95
    assert any(F * w > 1.0 for w in model.weights)  # the static trim already needs more than one rotor has
    assert model.thrust_fraction(F) < F * model.efficiency()
    p = pilot(af)
    p.sticks = StickInput(0, 0, 0, F)
    worst, saturated = 0.0, False
    for _ in range(150):
        p.run(0.02)
        if not p.sim.quad.s.on_ground:
            worst = max(worst, p.sim.quad.tilt)
            saturated = saturated or p.sim.fc.sat_rp
    assert saturated and worst > math.radians(10.0)  # the mixer runs out of room: the nose goes down


def test_imu_noise_and_scaled_imu():
    quiet = pilot(custom(cg_offset_cm=[0.1, 0.0]))  # active but noise-free
    noisy = pilot(custom(imu_noise=3.0))
    for p in (quiet, noisy):
        lift(p)
        hover(p, 2.0)
    rolls = [noisy.sim._build("ATTITUDE", noisy.t).roll for _ in range(400)]
    sigma = statistics.pstdev(rolls)
    assert sigma == pytest.approx(3.0 * afm.ATT_NOISE_RAD, rel=0.2)
    assert statistics.pstdev([quiet.sim._build("ATTITUDE", quiet.t).roll for _ in range(50)]) == 0.0
    imu = quiet.sim._build("SCALED_IMU", quiet.t)
    assert imu.zacc == pytest.approx(-1000, abs=60) and abs(imu.xacc) < 30
    accs = [noisy.sim._build("SCALED_IMU", noisy.t).xacc for _ in range(400)]
    assert statistics.pstdev(accs) == pytest.approx(3.0 * afm.ACC_NOISE_MS2 / 9.80665 * 1000, rel=0.25)


def test_scaled_imu_streams_on_request_only():
    p = SimPilot("ardupilot", name="sim-1")
    p.request_data_stream(M.MAV_DATA_STREAM_ALL, 10)
    p.run(1.0)
    assert not any(m.get_type() == "SCALED_IMU" for m in p.msgs)
    p.set_message_interval(M.MAVLINK_MSG_ID_SCALED_IMU, 50)
    p.run(1.0)
    got = [m for m in p.msgs if m.get_type() == "SCALED_IMU"]
    assert 45 <= len(got) <= 55
    assert got[-1].zacc == pytest.approx(-1000, abs=2)  # at rest, level, assisted model: exact gravity


def test_level_trim_shows_in_the_accelerometer_while_drifting():
    p = pilot(custom(level_trim_deg=[0.0, -2.0]))
    lift(p)
    hover(p, 0.6)
    imu = p.sim._build("SCALED_IMU", p.t)
    # nose down by 2 deg, board level: the board feels the forward push before drag builds up
    assert imu.xacc / 1000 * 9.80665 > 0.15


def test_same_seed_same_flight():
    def flight(seed):
        p = pilot(generate("rough", seed), turbulence=0.3)
        lift(p)
        out = []

        def sample(_t):
            out.append((tuple(p.sim.quad.s.p), p.sim._build("ATTITUDE", p.t).roll))

        hover(p, 3.0, sample=sample)
        return out

    assert flight(8) == flight(8)
    assert flight(8) != flight(9)


def test_airframe_survives_reset_and_can_change_in_flight():
    rough = generate("rough", 2)
    p = pilot(rough)
    p.sim.reset()
    assert p.sim.airframe == rough
    p.arm(True)
    p.run(0.2)
    lift(p)
    p.sim.set_airframe(generate("ideal", 0))
    assert p.sim.quad.airframe is None and p.sim.airframe.preset == "ideal"
    hover(p, 1.0)
    assert not p.sim.crashed
