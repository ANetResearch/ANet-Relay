"""Small-quadrotor dynamics v2 (07 §1): a six-degree-of-freedom rigid body with four rotors.

World frame ENU (x east, y north, z up), metres and seconds. The attitude is a unit quaternion from the
body frame FRD (x forward, y right, z down) to NED; ``State.roll`` / ``pitch`` / ``yaw`` are the aerospace
Euler angles of it (roll right-wing-down +, pitch nose-up +, yaw the NED heading, 0 = north, clockwise +),
matching MAVLink ATTITUDE, and ``State.rates`` are the body rates (p, q, r) in rad/s.

Rotors follow Gazebo's ``MulticopterMotorModel`` (gazebo_motor_model.cpp): the rotor speed follows its
reference through a first-order filter with separate spin-up and spin-down time constants, thrust is
``k_f * w^2``, the reaction torque is ``k_m * thrust`` against the spin direction, a rotor drag force
``-|w| * rotor_drag * v_perp`` and a rolling moment ``-|w| * turning_direction * rolling_moment * v_perp``
act with the airspeed in the rotor plane. Gazebo's linear thrust roll-off with axial airspeed (``1 - v / 25``)
is left out: it takes thrust away in a descent as well as in a climb; the propeller wake below covers the
descent.

Rotor layout is PX4 / ArduPilot quad-x (the motor test numbering): 1 front-right CCW, 2 rear-left CCW,
3 front-left CW, 4 rear-right CW. The ESC maps a motor output ``u`` (0..1) to a thrust fraction
``(1 - THR_MDL_FAC) * u + THR_MDL_FAC * u^2`` (PX4 ``THR_MDL_FAC``), so the rotor speed reference is
``w_top * sqrt(fraction)``, with ``w_top`` proportional to the battery voltage. ``k_f`` and ``w_max`` follow
from a thrust-to-weight ratio of 2.4 and a hover output of 0.50.

``Quad.step`` takes the four rotor thrust fractions the flight controller's mixer asks for
(``sim.autopilot.FlightController``); integration is semi-implicit Euler at 250 Hz with the quaternion
normalised every step. Same seed, same inputs and same step give bit-identical trajectories.

Airframe types (11 §8.1): the constants below are the default ``micro-120`` airframe; ``Quad(frame=...)``
flies any ``sim.frame.FrameModel`` with its own mass, geometry, inertia, rotors, drag and battery. Ground
contact (11 §8.3) is a rigid-body contact model with nine points, see ``Quad``.
"""

from __future__ import annotations

import itertools
import math
import random
import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .frame import DEFAULT_FRAME, FrameModel

Vec3 = tuple[float, float, float]

G = 9.80665
DT = 1.0 / 250.0
MASS = 0.12
THRUST_TO_WEIGHT = 2.4
HOVER_THROTTLE = 0.50
MAX_YAW_RATE = math.radians(120.0)
CRASH_VZ = 2.5
CRASH_VH = 3.0
CRASH_TILT = math.radians(60.0)
GROUND_FRICTION = 6.0  # yaw damping of an aircraft turning on its legs, 1/s

# Ground contact (11 §8.3): spring-damper normal force and Coulomb friction at nine contact points.
CONTACT_PEN = 0.0005  # m the legs give under the weight at rest (sets the stiffness, scaled with the mass)
CONTACT_ZETA_FOOT = 0.7  # damping ratio of the legs: a landing does not bounce
CONTACT_ZETA_BODY = 0.25  # propeller tips and the top of the body: a wreck bounces
CONTACT_MU = 0.6  # Coulomb friction coefficient
CONTACT_V_SLIP = 0.02  # m/s: friction rises from 0 to mu * N over this slip speed (no stick-slip chatter)
CONTACT_OMEGA_DT = 0.5  # stiffest contact mode's omega * substep
CONTACT_SUBSTEPS_MIN = 2
CONTACT_SUBSTEPS_MAX = 16
CONTACT_K_LIMIT = 0.3  # per-point stiffness cap, effective mass / substep^2
CONTACT_C_LIMIT = 0.8  # per-point damping and friction cap, effective mass / substep
CONTACT_MARGIN = 0.01  # m above the contact reach a step is checked for contact
REST_V = 0.03  # m/s: still enough to come to rest
REST_RATE = 0.2  # rad/s
REST_S = 0.2  # s still in contact before the body rests
REST_UPRIGHT = math.radians(20.0)  # resting closer than this to level: stand level on the legs

# Rigid body. Inertia from a 25 cm wheelbase and 0.12 kg: four 4 g motor-and-propeller sets at 0.088 m
# lever (0.125 m from the centre) give Ixx = Iyy = 1.25e-4 and Izz = 2.5e-4 kg m^2 with the core's own
# small part; rounded to the contract values (07 §1).
IXX = 1.2e-4
IYY = 1.2e-4
IZZ = 2.2e-4
WHEELBASE = 0.25
ARM = WHEELBASE / 2.0 / math.sqrt(2.0)  # lever arm of each rotor about the roll and pitch axes, m
ROTOR_X = (ARM, -ARM, ARM, -ARM)  # forward, m
ROTOR_Y = (ARM, -ARM, -ARM, ARM)  # right, m
ROTOR_DIR = (1.0, 1.0, -1.0, -1.0)  # reaction torque sign about body z (CCW rotors turn the body clockwise)

# Rotors (Gazebo MulticopterMotorModel parameters, scaled to a 13 cm propeller).
TAU_UP = 0.0125  # s, timeConstantUp
TAU_DOWN = 0.025  # s, timeConstantDown
OMEGA_MAX = 3100.0  # rad/s at full output and the reference voltage (about 29 600 rpm)
ROTOR_THRUST_MAX = THRUST_TO_WEIGHT * MASS * G / 4.0  # N per rotor
K_F = ROTOR_THRUST_MAX / OMEGA_MAX**2  # motorConstant, N s^2
K_M = 0.006  # momentConstant, m: reaction torque per newton of thrust
OMEGA_HOVER = OMEGA_MAX * math.sqrt(1.0 / THRUST_TO_WEIGHT)
ROTOR_DRAG = 0.15 * MASS / (4.0 * OMEGA_HOVER)  # rotorDragCoefficient, N s^2 / m: 0.15 /s of drag in hover
ROLLING_MOMENT = 1.0e-8  # rollingMomentCoefficient, N m s^2 / m
THR_MDL_FAC = 1.0 / 3.0  # ESC thrust curve: f(0.5) * 2.4 = 1

# Body drag (forces, N per m/s and per (m/s)^2 along the body axes): the side areas are equal, the top view
# is larger. With the rotor drag the horizontal total in hover is 0.35 /s, as in v0.4, which gives about
# 7 m/s level at 30 degrees of tilt.
BODY_DRAG_LIN = (0.20 * MASS, 0.20 * MASS, 0.35 * MASS)
BODY_DRAG_QUAD = (0.065 * MASS, 0.065 * MASS, 0.08 * MASS)
DRAG_LINEAR = 0.35  # vertical drag per unit mass (the open-loop landing's sink model)
DRAG_QUADRATIC = 0.08

# Physical effects (05 §5.1), active only while ``Quad.effects`` is set (hover assist off).
PROP_DIAMETER = 0.13
GROUND_EFFECT_GAIN = 0.12
GROUND_EFFECT_K = 3.0 / PROP_DIAMETER
V_REF_CELL = 4.09  # loaded cell voltage at full charge and hover: rotor speed scales with voltage around it
V_FACTOR_MAX = 1.03
TURBULENCE_TAU = 0.6  # s, correlation time of the gust process
TURBULENCE_XY = 1.2  # m/s^2 gust acceleration standard deviation at strength 1
TURBULENCE_Z = 0.5
PROP_WASH_VZ = 1.2  # m/s descent rate where the aircraft enters its own wake
PROP_WASH_GAIN = 0.12  # thrust fluctuation per m/s beyond PROP_WASH_VZ
PROP_WASH_MAX = 0.18
IMPULSE_MAX = 5.0  # m/s per component, a sanity bound on a bullet's velocity change
KICK_MAX = math.radians(30.0)
COLLIDE_DV_MAX = 20.0  # m/s per component, the bound on a collision's velocity change
COLLIDE_RATE_MAX = 30.0  # rad/s per component, the bound on a collision's body-rate kick

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

# ESC thrust curve f(u) = a*u + b*u^2 with f(1) = 1 and f(HOVER_THROTTLE) * TWR = 1 (b = THR_MDL_FAC).
_B = (1.0 / THRUST_TO_WEIGHT - HOVER_THROTTLE) / (HOVER_THROTTLE**2 - HOVER_THROTTLE)
_A = 1.0 - _B
_A2 = _A * _A
_INV_2B = 1.0 / (2.0 * _B)
_FOUR_B = 4.0 * _B
_ALPHA_UP = math.exp(-DT / TAU_UP)
_ALPHA_DOWN = math.exp(-DT / TAU_DOWN)
_GE_FLOOR = math.exp(-GROUND_EFFECT_K * PROP_DIAMETER)
_DLX, _DLY, _DLZ = BODY_DRAG_LIN
_IIX, _IIY, _IIZ = 1.0 / IXX, 1.0 / IYY, 1.0 / IZZ
_JZY, _JXZ, _JYX = IZZ - IYY, IXX - IZZ, IYY - IXX
_DQX, _DQY, _DQZ = BODY_DRAG_QUAD


def thrust_fraction(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return _A * u + _B * u * u


def throttle_for_fraction(f: float) -> float:
    """Inverse of thrust_fraction on [0, 1]."""
    f = min(1.0, max(0.0, f))
    if abs(_B) < 1e-9:
        return f / _A
    return (-_A + math.sqrt(_A2 + _FOUR_B * f)) * _INV_2B


def ocv_per_cell(soc: float) -> float:
    soc = min(1.0, max(0.0, soc))
    for (s0, v0), (s1, v1) in itertools.pairwise(OCV_TABLE):
        if soc <= s1:
            return v0 + (v1 - v0) * (soc - s0) / (s1 - s0)
    return OCV_TABLE[-1][1]


def _ocv_segment(soc: float) -> tuple[float, float, float, float]:
    soc = min(1.0, max(0.0, soc))
    for (s0, v0), (s1, v1) in itertools.pairwise(OCV_TABLE):
        if soc <= s1:
            return (s0, s1, v0, v1)
    (s0, v0), (s1, v1) = OCV_TABLE[-2], OCV_TABLE[-1]
    return (s0, s1, v0, v1)


def wrap_pi(angle: float) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def seed_for(name: str) -> int:
    """Stable turbulence seed for an aircraft id (``hash()`` is salted per process, crc32 is not)."""
    return zlib.crc32(name.encode("utf-8"))


def euler_quaternion(roll: float, pitch: float, yaw: float) -> tuple[float, float, float, float]:
    """(w, x, y, z) of the FRD-to-NED rotation for aerospace Euler angles (ZYX)."""
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return (
        cr * cp * cy + sr * sp * sy,
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
    )


@dataclass
class Command:
    """Outer-loop targets for the flight controller (``sim.autopilot.FlightController``).

    With ``rates`` set (ACRO, 11 §8.4) the angle loop is skipped: ``rates`` are the body-rate targets
    (p, q, r) in rad/s and ``roll`` / ``pitch`` / ``yaw_rate`` are ignored.
    """

    roll: float = 0.0  # rad
    pitch: float = 0.0  # rad
    yaw_rate: float = 0.0  # rad/s, heading rate
    throttle: float = 0.0  # 0..1 collective motor output
    rates: tuple[float, float, float] | None = None  # body rate targets, rad/s (rate mode)


@dataclass
class Battery:
    """A LiPo pack: ``cells`` in series, ``capacity_ah``; the defaults are the 3S 850 mAh of ``micro-120``."""

    soc: float = 1.0
    current: float = IDLE_CURRENT
    voltage: float = math.nan  # full charge (4.2 V per cell) when not given
    consumed_mah: float = 0.0
    cells: int = CELLS
    capacity_ah: float = CAPACITY_AH
    r_internal: float = R_INTERNAL
    load_current: float = LOAD_CURRENT

    def __post_init__(self) -> None:
        if math.isnan(self.voltage):
            self.voltage = self.cells * 4.2

    def step(self, throttle: float, motors_on: bool, dt: float) -> None:
        load = self.load_current * throttle**1.5 if motors_on and throttle > 0.0 else 0.0
        current = IDLE_CURRENT + load
        self.current = current
        used_ah = current * dt / 3600.0
        soc = self.soc - used_ah / self.capacity_ah
        if soc < 0.0:
            soc = 0.0
        self.soc = soc
        self.consumed_mah += used_ah * 1000.0
        # the open-circuit curve segment the charge is in (it only moves down), cached between steps
        seg = getattr(self, "_seg", None)
        if seg is None or not seg[0] <= soc <= seg[1]:
            seg = self._seg = _ocv_segment(soc)
        s0, s1, v0, v1 = seg[0], seg[1], seg[2], seg[3]
        ocv = v0 + (v1 - v0) * (soc - s0) / (s1 - s0)
        volts = self.cells * ocv - current * self.r_internal
        self.voltage = volts if volts > 0.0 else 0.0

    @property
    def cell_voltage(self) -> float:
        return self.voltage / self.cells

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
    rates: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])  # body p, q, r (FRD), rad/s
    motor: float = 0.0  # mean motor output the rotors actually run at (after their lag), 0..1
    on_ground: bool = True  # resting on the ground or some contact point touching it
    crashed: bool = False
    crash_reason: str | None = None
    crash_code: str | None = None  # 07 §2: 'ground' | 'collision' | 'shot_down'
    crash_point: tuple[float, float, float] | None = None  # simulator ENU where the crash happened
    crash_speed: float | None = None  # m/s at the crash (07 §5: a hard ground crash explodes)
    accel: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    q: list[float] = field(default_factory=lambda: [1.0, 0.0, 0.0, 0.0])  # FRD -> NED (w, x, y, z)
    omega: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])  # rotor speeds, rad/s
    resting: bool = True  # 11 §8.3: settled on the ground, held still without the contact model
    touching: bool = False  # some contact point was in the ground at the end of the last step
    rest_t: float = 0.0  # s the body has been still in contact (toward ``resting``)

    def __post_init__(self) -> None:
        if self.roll or self.pitch or self.yaw:
            self.q = list(euler_quaternion(self.roll, self.pitch, self.yaw))

    def quaternion(self) -> tuple[float, float, float, float]:
        q = self.q
        return (q[0], q[1], q[2], q[3])

    def set_attitude(self, roll: float, pitch: float, yaw: float) -> None:
        """Set the attitude from Euler angles (quaternion and the Euler fields together)."""
        self.roll, self.pitch, self.yaw = roll, pitch, wrap_pi(yaw)
        self.q = list(euler_quaternion(roll, pitch, self.yaw))

    def sync_euler(self) -> None:
        """Recompute roll / pitch / yaw from the quaternion (after it was written directly)."""
        w, x, y, z = self.q
        self.roll = math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
        self.pitch = math.asin(max(-1.0, min(1.0, 2.0 * (w * y - z * x))))
        self.yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


class Quad:
    """Six-degree-of-freedom quadrotor: rigid body, four rotors, drag, ground contact and battery.

    ``frame`` (``sim.frame.FrameModel``, ``micro-120`` by default) gives the mass, geometry, inertia, rotors,
    drag and battery; they become instance attributes (``mass``, ``ixx``, ``k_f``, ...), so the module
    constants above stay the default airframe's values.

    With ``effects`` off (the default, and hover assist on) the battery acts only below 3.3 V per cell and
    there is no ground effect, wake, turbulence or airframe defect. With ``effects`` on (hover assist off,
    05 §5.1) the rotor speed scales with battery voltage, ground effect adds up to 12 % thrust within one
    propeller diameter, seeded turbulence (strength ``turbulence`` 0..1) pushes the airframe, descending
    into the propeller wake makes thrust fluctuate, and a non-nominal airframe (06 §4) acts: take-off weight,
    CG offset (thrust applied off the centre of mass), per-rotor thrust gains and a damaged propeller. The
    noise streams are ``random.Random`` seeded by ``seed``, so the same seed and inputs repeat exactly.

    Ground contact (11 §8.3): ``s.p`` is the centre of mass less the leg height, so a level aircraft on its
    legs is at height 0. Nine contact points (four feet under the motors, four propeller tips at the arm
    ends, the top of the body) each take a spring-damper normal force and Coulomb friction where they are
    in the ground, applied at the point, so contact also turns the body. A landing at or above the crash
    thresholds (vertical 2.5 m/s, horizontal 3 m/s, tilt 60 deg) is a crash: the motors stop and the
    airframe keeps moving under contact and gravity until it lies still, on its legs, side or back. A body
    still in contact for ``REST_S`` goes to rest (``s.resting``): it is held without the contact model
    (upright it is put level at height 0) until thrust lifts it or something pushes it.
    """

    def __init__(
        self,
        wind: tuple[float, float, float] = (0.0, 0.0, 0.0),
        *,
        effects: bool = False,
        turbulence: float = 0.0,
        seed: int = 0,
        frame: FrameModel | None = None,
    ) -> None:
        self._apply_frame(frame)
        self.s = State()
        self.battery = self._new_battery()
        self.wind = wind
        self.effects = effects
        self.turbulence = min(1.0, max(0.0, turbulence))
        self.seed = seed
        # WP-2a airframe defects (06 §4): an AirframeModel for a non-nominal airframe, consulted while
        # ``effects`` is on or the airframe asks for it with hover assist on (``active_airframe``).
        self.airframe: Any = None
        self.airframe_spec: Any = None
        self.thrusts = [0.0, 0.0, 0.0, 0.0]  # N per rotor in the last step
        self._reset_noise()

    # ------------------------------------------------------------------ airframe type (11 §8.1)

    def _apply_frame(self, frame: FrameModel | None) -> None:
        f = DEFAULT_FRAME if frame is None else frame
        f.validate()
        self.frame = f
        self.mass = f.mass_kg
        self.twr = f.thrust_to_weight
        self.ixx, self.iyy, self.izz = f.ixx, f.iyy, f.izz
        self.arm = f.arm_m
        self.rotor_x = (self.arm, -self.arm, self.arm, -self.arm)
        self.rotor_y = (self.arm, -self.arm, -self.arm, self.arm)
        self.tau_up, self.tau_down = f.tau_up_s, f.tau_down_s
        self.omega_max = f.omega_max
        self.rotor_thrust_max = f.rotor_thrust_n
        self.k_f = self.rotor_thrust_max / self.omega_max**2
        self.k_m = f.yaw_moment_m
        self.omega_hover = self.omega_max * math.sqrt(1.0 / self.twr)
        self.rotor_drag = f.rotor_drag_per_s * self.mass / (4.0 * self.omega_hover)
        self.body_drag_lin = f.body_drag_lin
        self.body_drag_quad = f.body_drag_quad
        self.prop_diameter = f.prop_diameter_m
        self.max_thrust = self.twr * self.mass * G
        self._ge_k = 3.0 / self.prop_diameter
        self._ge_floor = math.exp(-self._ge_k * self.prop_diameter)
        self._alpha_up = math.exp(-DT / self.tau_up)
        self._alpha_down = math.exp(-DT / self.tau_down)
        self._iix, self._iiy, self._iiz = 1.0 / self.ixx, 1.0 / self.iyy, 1.0 / self.izz
        self._jzy, self._jxz, self._jyx = self.izz - self.iyy, self.ixx - self.izz, self.iyy - self.ixx
        self._mass_delta = self.mass - MASS  # an airframe's take-off weight is relative to micro-120
        self._contact_setup(f)

    def set_frame(self, frame: FrameModel | None) -> None:
        """Fly ``frame`` from the next step (on the ground, disarmed); the battery is a full pack of it."""
        self._apply_frame(frame)
        self.battery = self._new_battery()

    def _new_battery(self) -> Battery:
        f = self.frame
        return Battery(
            cells=f.cells,
            capacity_ah=f.capacity_ah,
            r_internal=f.r_internal_ohm,
            load_current=f.load_current_a,
        )

    @property
    def inertia(self) -> tuple[float, float, float]:
        return (self.ixx, self.iyy, self.izz)

    def body_mass(self) -> float:
        """The mass in flight: the frame's, or the active airframe's take-off weight."""
        af = self.active_airframe()
        return af.mass + self._mass_delta if af is not None else self.mass

    def _contact_setup(self, f: FrameModel) -> None:
        """Contact points (body FRD from the centre of mass), stiffness, damping and the substep count."""
        leg, top, pz = f.leg_m, f.top_m, f.prop_z_m
        tip = (f.wheelbase_m / 2.0 + f.prop_diameter_m / 2.0) / (f.wheelbase_m / 2.0)
        k = self.mass * G / (4.0 * CONTACT_PEN)
        foot_c = 2.0 * CONTACT_ZETA_FOOT * math.sqrt(k * self.mass / 4.0)
        body_c = 2.0 * CONTACT_ZETA_BODY * math.sqrt(k * self.mass / 4.0)
        pts: list[tuple[float, float, float, float]] = []
        for i in range(4):
            pts.append((self.rotor_x[i], self.rotor_y[i], leg, foot_c))
        for i in range(4):
            pts.append((self.rotor_x[i] * tip, self.rotor_y[i] * tip, -pz, body_c))
        pts.append((0.0, 0.0, -top, body_c))
        self._points = tuple(pts)
        self._k_contact = k
        self._leg = leg
        radius = max(math.sqrt(x * x + y * y + z * z) for x, y, z, _c in pts)
        self._reach = radius - leg  # below this height of s.p some point may touch
        i_min = min(self.ixx, self.iyy, self.izz)
        m_eff = 1.0 / (1.0 / self.mass + radius * radius / i_min)
        steps = math.ceil(DT * math.sqrt(k / m_eff) / CONTACT_OMEGA_DT)
        self._substeps = min(CONTACT_SUBSTEPS_MAX, max(CONTACT_SUBSTEPS_MIN, steps))

    def reset(self, yaw: float = 0.0) -> None:
        self.s = State(yaw=yaw)
        self.battery = self._new_battery()
        self.thrusts = [0.0, 0.0, 0.0, 0.0]
        self._reset_noise()
        if self.airframe is not None:
            self.airframe.reset()

    def set_airframe(self, spec: Any) -> None:
        """Fly with ``spec`` (a ``sim.airframe.Airframe``, None for nominal). WP-2a."""
        from .airframe import AirframeModel  # airframe imports this module

        old = self.airframe
        self.airframe_spec = spec
        self.airframe = None if spec is None or spec.nominal else AirframeModel(spec)
        if old is not None and old.arms and self.airframe is not None:
            # same flight: redraw its level error (seeded by the arm count), so a change in flight keeps it
            self.airframe.arms = old.arms - 1
            self.airframe.rearm()

    def active_airframe(self) -> Any:
        """The airframe model in effect: with the physical effects on (hover assist off), or with hover
        assist on when the airframe asks for it (``with_assist``, 10 §3)."""
        af = self.airframe
        if af is None:
            return None
        return af if self.effects or af.spec.with_assist else None

    def _reset_noise(self) -> None:
        self.rng = random.Random(self.seed)
        self.gust = [0.0, 0.0, 0.0]  # m/s^2, Ornstein-Uhlenbeck state
        self.wash = 0.0

    def voltage_factor(self) -> float:
        """Rotor speed factor from battery voltage: below 3.3 V per cell only, proportional with effects."""
        cell = self.battery.voltage / self.battery.cells
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
        if h >= self.prop_diameter:
            return 1.0
        decay = (math.exp(-self._ge_k * max(0.0, h)) - self._ge_floor) / (1.0 - self._ge_floor)
        return 1.0 + GROUND_EFFECT_GAIN * decay

    def hover_throttle(self, height: float | None = None) -> float:
        """Collective output that balances gravity level at ``height`` with the present battery voltage."""
        vf = self.voltage_factor()
        gain = vf * vf * self.ground_effect(height)
        if gain <= 0:
            return 1.0
        af = self.active_airframe()
        if af is not None:  # WP-2a: take-off weight and rotor efficiency
            mass = af.mass + self._mass_delta
            return throttle_for_fraction(mass / (self.mass * self.twr * gain * af.efficiency()))
        return throttle_for_fraction(1.0 / (self.twr * gain))

    def thrust(self) -> float:
        """Total rotor thrust in newtons in the last step."""
        t = self.thrusts
        return t[0] + t[1] + t[2] + t[3]

    def stop_motors(self) -> None:
        """Rotors stop at once (flight termination)."""
        s = self.s
        s.omega = [0.0, 0.0, 0.0, 0.0]
        s.motor = 0.0

    def impulse(self, dv: tuple[float, float, float], kick: tuple[float, float] = (0.0, 0.0)) -> None:
        """Apply an instant external disturbance (05 §7.4: a virtual bullet hit).

        ``dv`` is a velocity change in ENU m/s, ``kick`` a (roll, pitch) attitude offset in radians that
        the flight controller then levels out again. Each component is bounded (5 m/s, 30 deg). An aircraft
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
        s.resting = False
        for i in range(3):
            s.v[i] += bounded[i]
        roll, pitch = (max(-KICK_MAX, min(KICK_MAX, float(c))) for c in kick)
        if math.isfinite(roll) and math.isfinite(pitch):
            s.set_attitude(s.roll + roll, s.pitch + pitch, s.yaw)

    def collide(
        self,
        dv: tuple[float, float, float],
        rate_kick: tuple[float, float, float] = (0.0, 0.0, 0.0),
        crash_reason: str | None = None,
        *,
        point: tuple[float, float, float] | None = None,
    ) -> None:
        """Apply a collision response (07 §2, ``sim.world``).

        ``dv`` is the velocity change in ENU m/s (bounded to 20 m/s per component), ``rate_kick`` a change
        of the body rates (p, q, r) in rad/s from the contact's angular impulse (bounded to 30 rad/s), and
        ``crash_reason`` (when set) brings the aircraft down: motors off, it falls (``crash_code``
        ``collision``). A crashed aircraft still takes the velocity change, so a falling wreck bounces off
        whatever it hits. ``point`` is the contact in simulator ENU (recorded with the crash). An aircraft
        resting on the ground takes only an upward push. Call it on the thread that steps the simulation.
        """
        s = self.s
        bounded = [max(-COLLIDE_DV_MAX, min(COLLIDE_DV_MAX, float(c))) for c in dv]
        kick = [max(-COLLIDE_RATE_MAX, min(COLLIDE_RATE_MAX, float(c))) for c in rate_kick]
        if not all(math.isfinite(c) for c in (*bounded, *kick)):
            return
        if crash_reason is not None:
            self._crash(crash_reason, "collision", point)
        if s.on_ground and bounded[2] <= 0.0:
            return
        s.on_ground = False
        s.resting = False
        for i in range(3):
            s.v[i] += bounded[i]
            s.rates[i] += kick[i]

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

    def step(self, rotors: Sequence[float] | None, armed: bool, dt: float = DT) -> None:
        """One physics step with the mixer's four rotor thrust fractions (0..1, None for all stopped)."""
        s = self.s
        if s.crashed or rotors is None:
            armed = False
        effects = self.effects
        cell = self.battery.voltage / self.battery.cells
        if cell <= 0.0:
            vf = 0.0
        elif effects:
            vf = cell / V_REF_CELL
            vf = V_FACTOR_MAX if vf > V_FACTOR_MAX else vf
        else:
            vf = cell / SAG_CELL_V
            vf = 1.0 if vf > 1.0 else vf
        top = self.omega_max * vf
        # Rotor speeds: Gazebo's first-order filter with separate up / down time constants.
        if dt == DT:
            a_up, a_dn = self._alpha_up, self._alpha_down
        else:
            a_up, a_dn = math.exp(-dt / self.tau_up), math.exp(-dt / self.tau_down)
        om = s.omega
        o1, o2, o3, o4 = om
        sqrt = math.sqrt
        if armed:
            f1, f2, f3, f4 = rotors  # type: ignore[misc]
            r1 = top * sqrt(f1) if f1 > 0.0 else 0.0
            r2 = top * sqrt(f2) if f2 > 0.0 else 0.0
            r3 = top * sqrt(f3) if f3 > 0.0 else 0.0
            r4 = top * sqrt(f4) if f4 > 0.0 else 0.0
        else:
            r1 = r2 = r3 = r4 = 0.0
        a = a_up if r1 > o1 else a_dn
        o1 = a * o1 + (1.0 - a) * r1
        a = a_up if r2 > o2 else a_dn
        o2 = a * o2 + (1.0 - a) * r2
        a = a_up if r3 > o3 else a_dn
        o3 = a * o3 + (1.0 - a) * r3
        a = a_up if r4 > o4 else a_dn
        o4 = a * o4 + (1.0 - a) * r4
        om[0], om[1], om[2], om[3] = o1, o2, o3, o4
        if top > 0.0 and not s.crashed:
            f = (o1 * o1 + o2 * o2 + o3 * o3 + o4 * o4) / (4.0 * top * top)
            f = 1.0 if f > 1.0 else f
            s.motor = (-_A + sqrt(_A2 + _FOUR_B * f)) * _INV_2B
        else:
            s.motor = 0.0
        self.battery.step(s.motor, armed, dt)
        if effects:
            self._noise(dt)
        af = self.active_airframe()
        mass = self.mass
        cx = cy = 0.0
        if af is not None:  # WP-2a: weight, CG and the damaged propeller's vibration
            mass = af.mass + self._mass_delta
            cx, cy = af.cg_m
            af.step(dt, s.motor if armed else 0.0)

        # Rotor thrusts.
        lift = self.k_f
        if effects:
            lift *= self.ground_effect() * (1.0 + self.wash)
        if af is not None:
            k, jit = af.k, af.jitter
            t1 = lift * k[0] * jit[0] * o1 * o1
            t2 = lift * k[1] * jit[1] * o2 * o2
            t3 = lift * k[2] * jit[2] * o3 * o3
            t4 = lift * k[3] * jit[3] * o4 * o4
        else:
            t1, t2, t3, t4 = lift * o1 * o1, lift * o2 * o2, lift * o3 * o3, lift * o4 * o4
        thr = self.thrusts
        thr[0], thr[1], thr[2], thr[3] = t1, t2, t3, t4
        total = t1 + t2 + t3 + t4

        resting = s.resting and s.on_ground
        if resting and not armed:
            s.rates = [0.0, 0.0, 0.0]
            s.v = [0.0, 0.0, 0.0]
            s.accel = [0.0, 0.0, 0.0]
            return

        # Rotation body FRD -> NED.
        qw, qx, qy, qz = s.q
        xx, yy, zz = qx * qx, qy * qy, qz * qz
        xy, xz, yz = qx * qy, qx * qz, qy * qz
        wx, wy, wz = qw * qx, qw * qy, qw * qz
        r00 = 1.0 - 2.0 * (yy + zz)
        r01 = 2.0 * (xy - wz)
        r02 = 2.0 * (xz + wy)
        r10 = 2.0 * (xy + wz)
        r11 = 1.0 - 2.0 * (xx + zz)
        r12 = 2.0 * (yz - wx)
        r20 = 2.0 * (xz - wy)
        r21 = 2.0 * (yz + wx)
        r22 = 1.0 - 2.0 * (xx + yy)

        # Air-relative velocity in the body frame (NED components first).
        v = s.v
        wind = self.wind
        vn, ve, vd = v[1] - wind[1], v[0] - wind[0], wind[2] - v[2]
        bx = r00 * vn + r10 * ve + r20 * vd
        by = r01 * vn + r11 * ve + r21 * vd
        bz = r02 * vn + r12 * ve + r22 * vd
        speed = math.sqrt(bx * bx + by * by + bz * bz)

        # Forces in the body frame: thrust, rotor drag (in the rotor plane), body drag.
        dlx, dly, dlz = self.body_drag_lin
        dqx, dqy, dqz = self.body_drag_quad
        rotor_drag = self.rotor_drag
        wsum = o1 + o2 + o3 + o4
        rd = rotor_drag * wsum
        fx = -(rd + dlx + dqx * speed) * bx
        fy = -(rd + dly + dqy * speed) * by
        fz = -total - (dlz + dqz * speed) * bz

        # Torques about the centre of mass: thrust off the CG, reaction torques, rolling moment and the yaw
        # moment of the rotor drag forces at the hubs.
        arm = self.arm
        x1, x2, x3, x4 = arm - cx, -arm - cx, arm - cx, -arm - cx
        y1, y2, y3, y4 = arm - cy, -arm - cy, -arm - cy, arm - cy
        # Gazebo's rolling moment -|w| * turning_direction * mu * v_perp: CW and CCW rotors cancel in a
        # symmetric quad, only a yawing imbalance leaves a net moment
        wdir = o1 + o2 - o3 - o4  # ROTOR_DIR
        tx = -(y1 * t1 + y2 * t2 + y3 * t3 + y4 * t4) - ROLLING_MOMENT * wdir * bx
        ty = x1 * t1 + x2 * t2 + x3 * t3 + x4 * t4 - ROLLING_MOMENT * wdir * by
        sx = o1 * x1 + o2 * x2 + o3 * x3 + o4 * x4
        sy = o1 * y1 + o2 * y2 + o3 * y3 + o4 * y4
        tz = self.k_m * (t1 + t2 - t3 - t4) - rotor_drag * (sx * by - sy * bx)

        # Translation (ENU).
        fn = r00 * fx + r01 * fy + r02 * fz
        fe = r10 * fx + r11 * fy + r12 * fz
        fdn = r20 * fx + r21 * fy + r22 * fz
        inv_m = 1.0 / mass
        ax = fe * inv_m
        ay = fn * inv_m
        az = -fdn * inv_m - G
        gusty = effects and not s.on_ground
        if gusty:
            gust = self.gust
            ax += gust[0]
            ay += gust[1]
            az += gust[2]

        rates = s.rates
        if resting:
            if az <= 0.0:
                s.v = [0.0, 0.0, 0.0]
                s.accel = [0.0, 0.0, 0.0]
                # resting on the legs, turning only with enough thrust to unload them
                if total > 0.3 * mass * G:
                    rates[0] = rates[1] = 0.0
                    rates[2] += (tz / self.izz - GROUND_FRICTION * rates[2]) * dt
                    s.yaw = wrap_pi(s.yaw + rates[2] * dt)
                    s.set_attitude(s.roll, s.pitch, s.yaw)
                else:
                    s.rates = [0.0, 0.0, 0.0]
                return
            s.resting = False  # the thrust lifts it: free, or the contact model while a point touches
            s.on_ground = False
            s.rest_t = 0.0

        pos = s.p
        vz = v[2]
        near = pos[2] < self._reach + (vz if vz > 0.0 else -vz) * dt + CONTACT_MARGIN
        if near:  # the step may end in contact: keep its start to redo it with the contact model
            start = (pos[0], pos[1], pos[2], v[0], v[1], v[2], rates[0], rates[1], rates[2])

        # Rotation: Euler's equations, then the quaternion with the new rates.
        p, q, r = rates
        p += (tx - self._jzy * q * r) * self._iix * dt
        q += (ty - self._jxz * r * p) * self._iiy * dt
        r += (tz - self._jyx * p * q) * self._iiz * dt
        rates[0], rates[1], rates[2] = p, q, r
        h = 0.5 * dt
        nw = qw - h * (qx * p + qy * q + qz * r)
        nx = qx + h * (qw * p + qy * r - qz * q)
        ny = qy + h * (qw * q + qz * p - qx * r)
        nz = qz + h * (qw * r + qx * q - qy * p)
        norm = 1.0 / math.sqrt(nw * nw + nx * nx + ny * ny + nz * nz)
        nw, nx, ny, nz = nw * norm, nx * norm, ny * norm, nz * norm
        quat = s.q
        quat[0], quat[1], quat[2], quat[3] = nw, nx, ny, nz

        v[0] += ax * dt
        v[1] += ay * dt
        v[2] += az * dt
        pos[0] += v[0] * dt
        pos[1] += v[1] * dt
        pos[2] += v[2] * dt
        acc = s.accel
        acc[0], acc[1], acc[2] = ax, ay, az
        if near:
            if s.touching or self._lowest() < 0.0:
                # in contact during this step: redo it from its start with the contact model
                quat[0], quat[1], quat[2], quat[3] = qw, qx, qy, qz
                pos[0], pos[1], pos[2], v[0], v[1], v[2], rates[0], rates[1], rates[2] = start
                gust3 = (self.gust[0], self.gust[1], self.gust[2]) if gusty else None
                self._contact(dt, mass, (fx, fy, fz), (tx, ty, tz), gust3, az > 0.0)
                return
            s.on_ground = False
            s.rest_t = 0.0
        s.roll = math.atan2(2.0 * (nw * nx + ny * nz), 1.0 - 2.0 * (nx * nx + ny * ny))
        sp = 2.0 * (nw * ny - nz * nx)
        s.pitch = math.asin(1.0 if sp > 1.0 else -1.0 if sp < -1.0 else sp)
        s.yaw = math.atan2(2.0 * (nw * nz + nx * ny), 1.0 - 2.0 * (ny * ny + nz * nz))

    # ------------------------------------------------------------------ ground contact (11 §8.3)

    def _lowest(self) -> float:
        """Height of the lowest contact point above the ground, m (negative: in the ground)."""
        s = self.s
        qw, qx, qy, qz = s.q
        r20 = 2.0 * (qx * qz - qw * qy)
        r21 = 2.0 * (qy * qz + qw * qx)
        r22 = 1.0 - 2.0 * (qx * qx + qy * qy)
        up = s.p[2] + self._leg
        return min(up - (r20 * cx + r21 * cy + r22 * cz) for cx, cy, cz, _c in self._points)

    def _contact(
        self,
        dt: float,
        mass: float,
        force: Vec3,
        torque: Vec3,
        gust: Vec3 | None,
        lifting: bool,
    ) -> None:
        """One step in contact with the ground, in substeps: the body-frame thrust and drag ``force`` and
        ``torque`` of the step, gravity, ``gust`` and a spring-damper normal force with Coulomb friction at
        every contact point in the ground. Judges a touchdown (crash thresholds) and puts a body that has
        been still for ``REST_S`` to rest."""
        s = self.s
        n = self._substeps
        hs = dt / n
        half = 0.5 * hs
        fx, fy, fz = force
        tx, ty, tz = torque
        gx, gy, gz = gust if gust is not None else (0.0, 0.0, 0.0)
        pts = self._points
        k = self._k_contact
        leg = self._leg
        iix, iiy, iiz = self._iix, self._iiy, self._iiz
        jzy, jxz, jyx = self._jzy, self._jxz, self._jyx
        inv_m = 1.0 / mass
        k_lim = CONTACT_K_LIMIT / (hs * hs)
        c_lim = CONTACT_C_LIMIT / hs
        mu, v_slip = CONTACT_MU, CONTACT_V_SLIP
        sqrt = math.sqrt
        v, pos, rates, quat = s.v, s.p, s.rates, s.q
        v0 = (v[0], v[1], v[2])
        judged = s.touching or s.on_ground  # a touchdown is judged once, when contact begins
        touching = False
        for _ in range(n):
            qw, qx, qy, qz = quat
            xx, yy, zz = qx * qx, qy * qy, qz * qz
            xy, xz, yz = qx * qy, qx * qz, qy * qz
            wx, wy, wz = qw * qx, qw * qy, qw * qz
            r00 = 1.0 - 2.0 * (yy + zz)
            r01 = 2.0 * (xy - wz)
            r02 = 2.0 * (xz + wy)
            r10 = 2.0 * (xy + wz)
            r11 = 1.0 - 2.0 * (xx + zz)
            r12 = 2.0 * (yz - wx)
            r20 = 2.0 * (xz - wy)
            r21 = 2.0 * (yz + wx)
            r22 = 1.0 - 2.0 * (xx + yy)
            # step forces in NED; body rates as an NED angular velocity
            fn = r00 * fx + r01 * fy + r02 * fz
            fe = r10 * fx + r11 * fy + r12 * fz
            fd = r20 * fx + r21 * fy + r22 * fz
            p, q, r = rates
            wn = r00 * p + r01 * q + r02 * r
            we = r10 * p + r11 * q + r12 * r
            wd = r20 * p + r21 * q + r22 * r
            vn, ve, vd = v[1], v[0], -v[2]
            up = pos[2] + leg
            ux, uy, uz = -r20, -r21, -r22  # world up in body axes
            tn = te = td = 0.0
            hits = [
                (pt, depth) for pt in pts if (depth := r20 * pt[0] + r21 * pt[1] + r22 * pt[2] - up) > 0.0
            ]
            touching = bool(hits)
            if touching and not judged:
                judged = True
                self._touchdown()
            share = c_lim / len(hits) if hits else 0.0  # the friction cap is shared by the points in contact
            for (cx, cy, cz, c), depth in hits:
                rdn = depth + up
                rn = r00 * cx + r01 * cy + r02 * cz
                re = r10 * cx + r11 * cy + r12 * cz
                pvn = vn + we * rdn - wd * re
                pve = ve + wd * rn - wn * rdn
                pvd = vd + wn * re - we * rn
                # effective mass of the point along the normal; caps keep the substep stable
                lx, ly, lz = cy * uz - cz * uy, cz * ux - cx * uz, cx * uy - cy * ux
                m_n = 1.0 / (inv_m + lx * lx * iix + ly * ly * iiy + lz * lz * iiz)
                kk = k if k < k_lim * m_n else k_lim * m_n
                cc = c if c < c_lim * m_n else c_lim * m_n
                normal = kk * depth + cc * pvd
                if normal <= 0.0:
                    continue
                cn = ce = 0.0
                slip = sqrt(pvn * pvn + pve * pve)
                if slip > 1e-9:
                    dn, de = -pvn / slip, -pve / slip  # friction direction (NED, horizontal)
                    bx = r00 * dn + r10 * de
                    by = r01 * dn + r11 * de
                    bz = r02 * dn + r12 * de
                    lx, ly, lz = cy * bz - cz * by, cz * bx - cx * bz, cx * by - cy * bx
                    m_t = 1.0 / (inv_m + lx * lx * iix + ly * ly * iiy + lz * lz * iiz)
                    ft = mu * normal * (slip / v_slip if slip < v_slip else 1.0)
                    cap = share * m_t * slip
                    ft = cap if ft > cap else ft
                    cn, ce = ft * dn, ft * de
                fn += cn
                fe += ce
                fd -= normal
                tn += re * -normal - rdn * ce
                te += rdn * cn + rn * normal
                td += rn * ce - re * cn
            bxt = tx + r00 * tn + r10 * te + r20 * td
            byt = ty + r01 * tn + r11 * te + r21 * td
            bzt = tz + r02 * tn + r12 * te + r22 * td
            p += (bxt - jzy * q * r) * iix * hs
            q += (byt - jxz * r * p) * iiy * hs
            r += (bzt - jyx * p * q) * iiz * hs
            rates[0], rates[1], rates[2] = p, q, r
            nw = qw - half * (qx * p + qy * q + qz * r)
            nx = qx + half * (qw * p + qy * r - qz * q)
            ny = qy + half * (qw * q + qz * p - qx * r)
            nz = qz + half * (qw * r + qx * q - qy * p)
            norm = 1.0 / sqrt(nw * nw + nx * nx + ny * ny + nz * nz)
            quat[0], quat[1], quat[2], quat[3] = nw * norm, nx * norm, ny * norm, nz * norm
            v[0] += (fe * inv_m + gx) * hs
            v[1] += (fn * inv_m + gy) * hs
            v[2] += (-fd * inv_m - G + gz) * hs
            pos[0] += v[0] * hs
            pos[1] += v[1] * hs
            pos[2] += v[2] * hs
        acc = s.accel
        acc[0], acc[1], acc[2] = (v[0] - v0[0]) / dt, (v[1] - v0[1]) / dt, (v[2] - v0[2]) / dt
        s.sync_euler()
        s.touching = s.on_ground = touching
        if touching and not lifting:
            still = v[0] * v[0] + v[1] * v[1] + v[2] * v[2] < REST_V * REST_V
            still = still and rates[0] * rates[0] + rates[1] * rates[1] + rates[2] * rates[2] < REST_RATE**2
            s.rest_t = s.rest_t + dt if still else 0.0
            if s.rest_t >= REST_S:
                self._sleep()
        else:
            s.rest_t = 0.0

    def _sleep(self) -> None:
        """Come to rest: held still on the ground; upright it stands level on its legs at height 0."""
        s = self.s
        s.resting = True
        s.on_ground = True
        s.rest_t = 0.0
        s.v = [0.0, 0.0, 0.0]
        s.rates = [0.0, 0.0, 0.0]
        s.accel = [0.0, 0.0, 0.0]
        if self.tilt < REST_UPRIGHT:
            s.p[2] = 0.0
            s.set_attitude(0.0, 0.0, s.yaw)
            s.touching = False
        else:
            s.touching = True

    def _touchdown(self) -> None:
        """First contact with the ground: a crash at or above the thresholds (motors stop, the body keeps
        its dynamics)."""
        s = self.s
        vh = math.hypot(s.v[0], s.v[1])
        tilt = self.tilt
        point = (s.p[0], s.p[1], 0.0)
        if -s.v[2] > CRASH_VZ:
            self._crash(f"vertical speed {-s.v[2]:.1f} m/s", "ground", point)
        elif vh > CRASH_VH:
            self._crash(f"horizontal speed {vh:.1f} m/s", "ground", point)
        elif tilt > CRASH_TILT:
            self._crash(f"tilt {math.degrees(tilt):.0f} deg", "ground", point)

    def _crash(self, reason: str, code: str = "ground", point: Vec3 | None = None) -> None:
        s = self.s
        if s.crashed:
            s.motor = 0.0
            return  # the first cause stands (a shot-down aircraft also hits the ground)
        s.crashed = True
        s.crash_reason = reason
        s.crash_code = code
        s.crash_point = point if point is not None else (s.p[0], s.p[1], s.p[2])
        s.crash_speed = math.sqrt(s.v[0] * s.v[0] + s.v[1] * s.v[1] + s.v[2] * s.v[2])
        s.motor = 0.0

    @property
    def tilt(self) -> float:
        q = self.s.q
        r22 = 1.0 - 2.0 * (q[1] * q[1] + q[2] * q[2])
        return math.acos(max(-1.0, min(1.0, r22)))
