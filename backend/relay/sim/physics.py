"""Small-quadrotor dynamics (03 §7.1).

World frame ENU (x east, y north, z up), metres and seconds. Attitude uses
aerospace Euler angles: roll right-wing-down +, pitch nose-up +, yaw is the
NED heading (0 = north, clockwise +), matching MAVLink ATTITUDE. The attitude
loop is idealised: each angle follows its target with a first-order lag, and
yaw follows a rate target. Integration is semi-implicit Euler at 250 Hz.
"""

from __future__ import annotations

import itertools
import math
import random
import zlib
from dataclasses import dataclass, field
from typing import Any

G = 9.80665
DT = 1.0 / 250.0
MASS = 0.12
THRUST_TO_WEIGHT = 2.4
HOVER_THROTTLE = 0.50
MOTOR_TAU = 0.03
ATTITUDE_TAU = 0.08
MAX_YAW_RATE = math.radians(120.0)
DRAG_LINEAR = 0.35
DRAG_QUADRATIC = 0.08
CRASH_VZ = 2.5
CRASH_VH = 3.0
CRASH_TILT = math.radians(60.0)
GROUND_FRICTION = 6.0

# Physical effects (05 §5.1), active only while ``Quad.effects`` is set (hover assist off).
PROP_DIAMETER = 0.13
GROUND_EFFECT_GAIN = 0.12
GROUND_EFFECT_K = 3.0 / PROP_DIAMETER
MOTOR_TAU_UP = 0.03
MOTOR_TAU_DOWN = 0.05
V_REF_CELL = 4.09  # loaded cell voltage at full charge and hover: thrust scales with voltage around it
V_FACTOR_MAX = 1.03
TURBULENCE_TAU = 0.6  # s, correlation time of the gust process
TURBULENCE_XY = 1.2  # m/s^2 gust acceleration standard deviation at strength 1
TURBULENCE_Z = 0.5
PROP_WASH_VZ = 1.2  # m/s descent rate where the aircraft enters its own wake
PROP_WASH_GAIN = 0.12  # thrust fluctuation per m/s beyond PROP_WASH_VZ
PROP_WASH_MAX = 0.18
IMPULSE_MAX = 5.0  # m/s per component, a sanity bound on external velocity changes
KICK_MAX = math.radians(30.0)

# Battery: 3S 850 mAh LiPo.
CELLS = 3
CAPACITY_AH = 0.85
R_INTERNAL = 0.075
IDLE_CURRENT = 0.4
LOAD_CURRENT = 11.0
SAG_CELL_V = 3.3
OCV_TABLE = (  # (state of charge, open-circuit volts per cell)
    (0.00, 3.30),
    (0.05, 3.50),
    (0.15, 3.68),
    (0.30, 3.75),
    (0.50, 3.82),
    (0.70, 3.92),
    (0.85, 4.05),
    (1.00, 4.20),
)

# Thrust curve f(u) = a*u + b*u^2 with f(1) = 1 and f(HOVER_THROTTLE) * TWR = 1.
_B = (1.0 / THRUST_TO_WEIGHT - HOVER_THROTTLE) / (HOVER_THROTTLE**2 - HOVER_THROTTLE)
_A = 1.0 - _B


def thrust_fraction(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return _A * u + _B * u * u


def throttle_for_fraction(f: float) -> float:
    """Inverse of thrust_fraction on [0, 1]."""
    f = min(1.0, max(0.0, f))
    if abs(_B) < 1e-9:
        return f / _A
    return (-_A + math.sqrt(_A * _A + 4.0 * _B * f)) / (2.0 * _B)


def ocv_per_cell(soc: float) -> float:
    soc = min(1.0, max(0.0, soc))
    for (s0, v0), (s1, v1) in itertools.pairwise(OCV_TABLE):
        if soc <= s1:
            return v0 + (v1 - v0) * (soc - s0) / (s1 - s0)
    return OCV_TABLE[-1][1]


def wrap_pi(angle: float) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def seed_for(name: str) -> int:
    """Stable turbulence seed for an aircraft id (``hash()`` is salted per process, crc32 is not)."""
    return zlib.crc32(name.encode("utf-8"))


@dataclass
class Command:
    """Inner-loop targets from the autopilot."""

    roll: float = 0.0  # rad
    pitch: float = 0.0  # rad
    yaw_rate: float = 0.0  # rad/s
    throttle: float = 0.0  # 0..1 motor command


@dataclass
class Battery:
    soc: float = 1.0
    current: float = IDLE_CURRENT
    voltage: float = CELLS * 4.2
    consumed_mah: float = 0.0

    def step(self, throttle: float, motors_on: bool, dt: float) -> None:
        load = LOAD_CURRENT * max(0.0, throttle) ** 1.5 if motors_on else 0.0
        self.current = IDLE_CURRENT + load
        used_ah = self.current * dt / 3600.0
        self.soc = max(0.0, self.soc - used_ah / CAPACITY_AH)
        self.consumed_mah += used_ah * 1000.0
        self.voltage = max(0.0, CELLS * ocv_per_cell(self.soc) - self.current * R_INTERNAL)

    @property
    def cell_voltage(self) -> float:
        return self.voltage / CELLS

    @property
    def remaining_pct(self) -> int:
        return round(self.soc * 100)


@dataclass
class State:
    p: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    v: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    rates: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])  # body-ish roll, pitch, yaw rates
    motor: float = 0.0  # actual throttle after motor lag
    on_ground: bool = True
    crashed: bool = False
    crash_reason: str | None = None
    accel: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])

    def quaternion(self) -> tuple[float, float, float, float]:
        cr, sr = math.cos(self.roll / 2), math.sin(self.roll / 2)
        cp, sp = math.cos(self.pitch / 2), math.sin(self.pitch / 2)
        cy, sy = math.cos(self.yaw / 2), math.sin(self.yaw / 2)
        return (
            cr * cp * cy + sr * sp * sy,
            sr * cp * cy - cr * sp * sy,
            cr * sp * cy + sr * cp * sy,
            cr * cp * sy - sr * sp * cy,
        )


class Quad:
    """Rigid-body quadrotor with motor lag, idealised attitude loop, drag, ground contact and battery.

    With ``effects`` off (the default, and hover assist on) the model is exactly the v0.2 one. With
    ``effects`` on (hover assist off, 05 §5.1) thrust scales with battery voltage, the motor spins up
    faster than it spins down, ground effect adds up to 12 % thrust within one propeller diameter,
    seeded turbulence (strength ``turbulence`` 0..1) pushes the airframe, and descending into the
    propeller wake makes thrust fluctuate. The noise stream is a ``random.Random`` seeded by ``seed``,
    so the same seed and the same inputs give the same trajectory.
    """

    def __init__(
        self,
        wind: tuple[float, float, float] = (0.0, 0.0, 0.0),
        *,
        effects: bool = False,
        turbulence: float = 0.0,
        seed: int = 0,
    ) -> None:
        self.s = State()
        self.battery = Battery()
        self.wind = wind
        self.max_thrust = THRUST_TO_WEIGHT * MASS * G
        self.effects = effects
        self.turbulence = min(1.0, max(0.0, turbulence))
        self.seed = seed
        # WP-2a airframe defects (06 §4): an AirframeModel for a non-nominal airframe, consulted only while
        # ``effects`` is on, so the assisted model and a nominal airframe stay exactly the 05 model.
        self.airframe: Any = None
        self.airframe_spec: Any = None
        self._reset_noise()

    def reset(self, yaw: float = 0.0) -> None:
        self.s = State(yaw=yaw)
        self.battery = Battery()
        self._reset_noise()
        if self.airframe is not None:
            self.airframe.reset()

    def set_airframe(self, spec: Any) -> None:
        """Fly with ``spec`` (a ``sim.airframe.Airframe``, None for nominal). WP-2a."""
        from .airframe import AirframeModel  # airframe imports this module

        self.airframe_spec = spec
        self.airframe = None if spec is None or spec.nominal else AirframeModel(spec)

    def active_airframe(self) -> Any:
        """The airframe model in effect: only with the physical effects on (hover assist off)."""
        return self.airframe if self.effects else None

    def _reset_noise(self) -> None:
        self.rng = random.Random(self.seed)
        self.gust = [0.0, 0.0, 0.0]  # m/s^2, Ornstein-Uhlenbeck state
        self.wash = 0.0

    def voltage_factor(self) -> float:
        """Thrust multiplier from battery voltage: below 3.3 V per cell only, or proportional with effects."""
        cell = self.battery.cell_voltage
        if cell <= 0:
            return 0.0
        if self.effects:
            return min(V_FACTOR_MAX, cell / V_REF_CELL)
        return min(1.0, cell / SAG_CELL_V)

    def ground_effect(self, height: float | None = None) -> float:
        """Thrust multiplier near the ground: 1 + 12 % at contact, decaying to 1 at one prop diameter."""
        if not self.effects:
            return 1.0
        h = self.s.p[2] if height is None else height
        if h >= PROP_DIAMETER:
            return 1.0
        floor = math.exp(-GROUND_EFFECT_K * PROP_DIAMETER)
        decay = (math.exp(-GROUND_EFFECT_K * max(0.0, h)) - floor) / (1.0 - floor)
        return 1.0 + GROUND_EFFECT_GAIN * decay

    def hover_throttle(self, height: float | None = None) -> float:
        """Motor output that balances gravity level at ``height`` with the present battery voltage."""
        gain = self.voltage_factor() * self.ground_effect(height)
        if gain <= 0:
            return 1.0
        af = self.active_airframe()
        if af is not None:  # WP-2a: take-off weight and motor efficiency
            return throttle_for_fraction(af.mass / (MASS * THRUST_TO_WEIGHT * gain * af.efficiency()))
        return throttle_for_fraction(1.0 / (THRUST_TO_WEIGHT * gain))

    def thrust(self) -> float:
        """Thrust in newtons for the current motor output (voltage, ground effect and wake applied)."""
        af = self.active_airframe()
        if af is not None:  # WP-2a: per-motor efficiency and saturation
            fraction = af.thrust_fraction(thrust_fraction(self.s.motor))
            thrust = self.max_thrust * fraction * self.voltage_factor()
        else:
            thrust = self.max_thrust * thrust_fraction(self.s.motor) * self.voltage_factor()
        if self.effects:
            thrust *= self.ground_effect() * (1.0 + self.wash)
        return thrust

    def impulse(self, dv: tuple[float, float, float], kick: tuple[float, float] = (0.0, 0.0)) -> None:
        """Apply an instant external disturbance (05 §7.4: a virtual bullet hit).

        ``dv`` is a velocity change in ENU m/s, ``kick`` a (roll, pitch) attitude offset in radians that
        the attitude loop then levels out again. Each component is bounded (5 m/s, 30 deg). An aircraft
        resting on the ground keeps still unless the push has an upward part, as ground contact absorbs it.
        Call it on the thread that steps the simulation.
        """
        s = self.s
        if s.crashed:
            return
        bounded = [max(-IMPULSE_MAX, min(IMPULSE_MAX, float(c))) for c in dv]
        if not all(math.isfinite(c) for c in bounded):
            return
        if s.on_ground and bounded[2] <= 0.0:
            return
        if s.on_ground:
            s.on_ground = False
        for i in range(3):
            s.v[i] += bounded[i]
        roll, pitch = (max(-KICK_MAX, min(KICK_MAX, float(c))) for c in kick)
        if math.isfinite(roll) and math.isfinite(pitch):
            s.roll += roll
            s.pitch += pitch

    def _noise(self, dt: float) -> None:
        """Advance the gust and propeller-wake processes by one step (effects only)."""
        s = self.s
        decay = math.exp(-dt / TURBULENCE_TAU)
        spread = math.sqrt(1.0 - decay * decay)
        sigma = (TURBULENCE_XY, TURBULENCE_XY, TURBULENCE_Z)
        for i in range(3):
            shock = sigma[i] * self.turbulence * spread * self.rng.gauss(0.0, 1.0)
            self.gust[i] = self.gust[i] * decay + shock
        sink = -s.v[2] - PROP_WASH_VZ
        depth = min(PROP_WASH_MAX, PROP_WASH_GAIN * sink) if sink > 0.0 else 0.0
        self.wash = depth * self.rng.uniform(-1.0, 1.0)

    def step(self, cmd: Command, armed: bool, dt: float = DT) -> None:
        s = self.s
        if s.crashed:
            armed = False
        target = cmd.throttle if armed else 0.0
        tau = MOTOR_TAU
        if self.effects:
            tau = MOTOR_TAU_UP if target > s.motor else MOTOR_TAU_DOWN
        s.motor += (target - s.motor) * min(1.0, dt / tau)
        self.battery.step(s.motor, armed, dt)
        if self.effects:
            self._noise(dt)
        af = self.active_airframe()
        mass = MASS
        if af is not None:  # WP-2a
            mass = af.mass
            af.step(dt, thrust_fraction(s.motor), armed and not s.on_ground, s.motor if armed else 0.0)

        if s.on_ground and not armed:
            s.rates = [0.0, 0.0, 0.0]
            s.v = [0.0, 0.0, 0.0]
            s.accel = [0.0, 0.0, 0.0]
            s.roll += (0.0 - s.roll) * min(1.0, dt / ATTITUDE_TAU)
            s.pitch += (0.0 - s.pitch) * min(1.0, dt / ATTITUDE_TAU)
            return

        k = min(1.0, dt / ATTITUDE_TAU)
        prev = (s.roll, s.pitch, s.yaw)
        roll_t, pitch_t = cmd.roll, cmd.pitch
        yaw_rate_t = max(-MAX_YAW_RATE, min(MAX_YAW_RATE, cmd.yaw_rate))
        if af is not None and not s.on_ground:  # WP-2a: what the loops do not correct, and the level error
            roll_t += af.offset[0]
            pitch_t += af.offset[1]
            yaw_rate_t += af.offset[2]
        if s.on_ground:
            roll_t = pitch_t = 0.0
            yaw_rate_t = 0.0 if s.motor < 0.2 else yaw_rate_t
        s.roll += (roll_t - s.roll) * k
        s.pitch += (pitch_t - s.pitch) * k
        yaw_rate = s.rates[2] + (yaw_rate_t - s.rates[2]) * k
        s.yaw = wrap_pi(s.yaw + yaw_rate * dt)
        s.rates = [(s.roll - prev[0]) / dt, (s.pitch - prev[1]) / dt, yaw_rate]

        thrust = self.thrust()
        cr, sr = math.cos(s.roll), math.sin(s.roll)
        cp, sp = math.cos(s.pitch), math.sin(s.pitch)
        cy, sy = math.cos(s.yaw), math.sin(s.yaw)
        # Body z-up axis in NED is -(third column of R_nb); thrust acts along body up.
        a_n = -thrust / mass * (cr * sp * cy + sr * sy)
        a_e = -thrust / mass * (cr * sp * sy - sr * cy)
        a_u = thrust / mass * (cr * cp) - G
        rel = [s.v[i] - self.wind[i] for i in range(3)]
        speed = math.sqrt(sum(c * c for c in rel))
        # Drag is specified as acceleration (per unit mass), which gives ~7 m/s top speed at 30 deg tilt.
        drag = [-(DRAG_LINEAR * rel[i] + DRAG_QUADRATIC * speed * rel[i]) for i in range(3)]
        ax = a_e + drag[0]
        ay = a_n + drag[1]
        az = a_u + drag[2]
        if self.effects and not s.on_ground:
            ax += self.gust[0]
            ay += self.gust[1]
            az += self.gust[2]
        if s.on_ground:
            if az <= 0.0:
                s.v = [0.0, 0.0, 0.0]
                s.accel = [0.0, 0.0, 0.0]
                return
            s.on_ground = False
        s.v[0] += ax * dt
        s.v[1] += ay * dt
        s.v[2] += az * dt
        s.p[0] += s.v[0] * dt
        s.p[1] += s.v[1] * dt
        s.p[2] += s.v[2] * dt
        s.accel = [ax, ay, az]
        if s.p[2] <= 0.0:
            self._touchdown()

    def _touchdown(self) -> None:
        s = self.s
        vh = math.hypot(s.v[0], s.v[1])
        tilt = math.acos(max(-1.0, min(1.0, math.cos(s.roll) * math.cos(s.pitch))))
        s.p[2] = 0.0
        if -s.v[2] > CRASH_VZ:
            self._crash(f"vertical speed {-s.v[2]:.1f} m/s")
        elif vh > CRASH_VH:
            self._crash(f"horizontal speed {vh:.1f} m/s")
        elif tilt > CRASH_TILT:
            self._crash(f"tilt {math.degrees(tilt):.0f} deg")
        s.v = [0.0, 0.0, 0.0]
        s.on_ground = True

    def _crash(self, reason: str) -> None:
        if self.s.crashed:
            self.s.motor = 0.0
            return  # the first cause stands (a shot-down aircraft also hits the ground)
        self.s.crashed = True
        self.s.crash_reason = reason
        self.s.motor = 0.0

    @property
    def tilt(self) -> float:
        return math.acos(max(-1.0, min(1.0, math.cos(self.s.roll) * math.cos(self.s.pitch))))
