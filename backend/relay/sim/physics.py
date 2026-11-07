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
from dataclasses import dataclass, field

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
    """Rigid-body quadrotor with motor lag, idealised attitude loop, drag, ground contact and battery."""

    def __init__(self, wind: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> None:
        self.s = State()
        self.battery = Battery()
        self.wind = wind
        self.max_thrust = THRUST_TO_WEIGHT * MASS * G

    def reset(self, yaw: float = 0.0) -> None:
        self.s = State(yaw=yaw)
        self.battery = Battery()

    def thrust(self) -> float:
        """Thrust in newtons for the current motor output, attenuated below 3.3 V per cell."""
        sag = min(1.0, self.battery.cell_voltage / SAG_CELL_V) if self.battery.cell_voltage > 0 else 0.0
        return self.max_thrust * thrust_fraction(self.s.motor) * sag

    def step(self, cmd: Command, armed: bool, dt: float = DT) -> None:
        s = self.s
        if s.crashed:
            armed = False
        target = cmd.throttle if armed else 0.0
        s.motor += (target - s.motor) * min(1.0, dt / MOTOR_TAU)
        self.battery.step(s.motor, armed, dt)

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
        a_n = -thrust / MASS * (cr * sp * cy + sr * sy)
        a_e = -thrust / MASS * (cr * sp * sy - sr * cy)
        a_u = thrust / MASS * (cr * cp) - G
        rel = [s.v[i] - self.wind[i] for i in range(3)]
        speed = math.sqrt(sum(c * c for c in rel))
        # Drag is specified as acceleration (per unit mass), which gives ~7 m/s top speed at 30 deg tilt.
        drag = [-(DRAG_LINEAR * rel[i] + DRAG_QUADRATIC * speed * rel[i]) for i in range(3)]
        ax = a_e + drag[0]
        ay = a_n + drag[1]
        az = a_u + drag[2]
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

    # -- WP-D arena (05 §7.4): minimal hit hooks; the physics package (WP-C) owns the final versions -------

    def impulse(self, dv: tuple[float, float, float], kick: tuple[float, float] = (0.0, 0.0)) -> None:
        """A virtual bullet's hit: an instant velocity change (ENU m/s) and an attitude kick (roll, pitch).

        The attitude loop then pulls the airframe back level, which reads as a shake. An aircraft resting on
        the ground keeps still unless the push lifts it.
        """
        s = self.s
        if s.crashed and s.on_ground:
            return
        if s.on_ground and dv[2] <= 0.0:
            return
        s.v = [s.v[i] + float(dv[i]) for i in range(3)]
        s.roll += float(kick[0])
        s.pitch += float(kick[1])
        s.on_ground = False

    # -------------------------------------------------------------------------------------------------------

    def _crash(self, reason: str) -> None:
        if self.s.crashed:
            return  # keep the first reason (a shot-down aircraft hitting the ground is still "Shot down")
        self.s.crashed = True
        self.s.crash_reason = reason
        self.s.motor = 0.0

    @property
    def tilt(self) -> float:
        return math.acos(max(-1.0, min(1.0, math.cos(self.s.roll) * math.cos(self.s.pitch))))
