"""Airframe defects of a simulated quadrotor (06 §4, WP-2a; physical since v0.5, 07 §1).

An ``Airframe`` is the assembly a simulator flies with: take-off weight, centre-of-gravity offset, level
calibration error, per-motor efficiency, one damaged propeller and IMU noise. ``generate(preset, seed)``
builds the ``ideal`` / ``typical`` / ``rough`` presets deterministically from a seed; ``custom`` is any
explicit set of values. ``AirframeModel`` is the runtime part ``physics.Quad`` consults while the quad's
physical effects are on (hover assist off, 05 §5.1), and also with hover assist on when ``with_assist`` is
set (10 §3: altitude hold keeps the height, the defects still tilt and push the airframe). Otherwise, or with
nominal values, the flight model is the nominal one.

Every defect acts through the six-degree-of-freedom model (body frame x forward, y right; quad-x motor
order 1 front-right CCW, 2 rear-left CCW, 3 front-left CW, 4 rear-right CW):

* Take-off weight: ``mass_kg + payload_kg`` is the mass the rotors lift (the payload hangs at the CG).
* Centre-of-gravity offset: the rotor thrusts act about the shifted centre of mass, so equal outputs make a
  real pitching / rolling moment that the simulated flight controller's rate integrators have to learn
  after every lift-off (they reset on the ground, as on PX4 and ArduPilot), and near full output the mixer
  runs out of room: the airframe tilts away (control loss).
* Motor efficiency: rotor ``i`` has ``k_f * (1 + motor_gain_i)``; unequal gains between the CCW and the CW
  pair leave a reaction-torque imbalance that the slow yaw integrator only gradually takes up.
* Propeller damage: the damaged rotor loses ``PROP_LOSS * prop_damage`` of its thrust and its thrust
  fluctuates (``JITTER_THRUST``, correlation time ``JITTER_TAU_S``), which shakes the attitude, plus frame
  vibration (VIBRATION, accelerometer noise).
* Level calibration error: the flight controller's attitude estimate is off by ``level_trim_deg``, so the
  airframe flies that much tilted with centred sticks, while ATTITUDE (the estimate) reads level.
  ``arm_bias_deg`` adds a fresh random error of up to that many degrees on roll and pitch at every arming
  (a gyro / accelerometer bias that differs from flight to flight; drawn from the seed and the arm count).
* ``imu_noise`` scales white noise on the reported attitude, rates and accelerations.

``weights`` / ``thrust_fraction`` / ``hover_throttle`` are the static trim: the per-rotor thrust shares that
put the thrust centroid under the CG and cancel the reaction torque, i.e. what the integrators converge to.

Values a program never sees (06 §4): the snapshot carries only ``{preset, seed}``; the numbers are served
to the operator and the controlling session through ``GET /sims/{vid}/airframe``.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, replace
from typing import Any

from .physics import ARM, MASS, ROTOR_DIR, THRUST_TO_WEIGHT, G, throttle_for_fraction

PRESETS = ("ideal", "typical", "rough", "custom")
GENERATED = ("ideal", "typical", "rough")
SEED_MAX = 2**31 - 1

# Field limits; also served to the renderer for the sliders.
LIMITS: dict[str, tuple[float, float]] = {
    "mass_kg": (0.08, 0.30),
    "payload_kg": (0.0, 0.20),
    "cg_offset_cm": (-3.0, 3.0),
    "level_trim_deg": (-5.0, 5.0),
    "motor_gain": (-0.08, 0.08),
    "prop_damage": (0.0, 1.0),
    "damaged_prop": (1, 4),
    "imu_noise": (0.0, 5.0),
    "arm_bias_deg": (0.0, 3.0),
}
FLAGS = ("with_assist",)  # boolean fields
VECTORS = {"cg_offset_cm": 2, "level_trim_deg": 2, "motor_gain": 4}

# Geometry (the physics rotor layout, in arm units for the static trim).
ARM_M = ARM  # lever arm of each rotor about the roll and pitch axes (25 cm wheelbase, quad-x)
MOTOR_X = (1.0, -1.0, 1.0, -1.0)  # forward, in arm units
MOTOR_Y = (1.0, -1.0, -1.0, 1.0)  # right
MOTOR_YAW = ROTOR_DIR  # reaction torque sign (CCW props turn the body clockwise, yaw +)
PROP_LOSS = 0.3  # thrust lost by a fully damaged propeller
JITTER_THRUST = 0.05  # thrust fluctuation (1 sigma) of a fully damaged propeller
JITTER_TAU_S = 0.08
VIB_DAMAGE = 50.0  # m/s^2 of extra VIBRATION at full damage and full throttle

# Sensor noise at imu_noise = 1 (1 sigma, white per report).
ATT_NOISE_RAD = math.radians(0.08)
YAW_NOISE_RAD = math.radians(0.15)
RATE_NOISE_RAD_S = math.radians(0.6)
ACC_NOISE_MS2 = 0.06
VIB_ACC_SHARE = 0.05  # accelerometer sigma per m/s^2 of damage vibration
VIB_RATE_SHARE = math.radians(0.3)  # gyro sigma per m/s^2 of damage vibration

_PHYS_SALT = 0x5A17
_SENSOR_SALT = 0x1C3D


@dataclass(frozen=True)
class Airframe:
    """Airframe values (06 §4). ``motor_gain`` is the efficiency deviation of each motor (0.05 = +5 %)."""

    preset: str = "ideal"
    seed: int = 0
    mass_kg: float = MASS
    payload_kg: float = 0.0
    cg_offset_cm: tuple[float, float] = (0.0, 0.0)  # x forward, y right
    level_trim_deg: tuple[float, float] = (0.0, 0.0)  # roll, pitch
    motor_gain: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    prop_damage: float = 0.0
    damaged_prop: int = 1  # 1..4, ArduPilot motor number
    imu_noise: float = 0.0
    arm_bias_deg: float = 0.0  # random level error drawn at every arming, per axis
    with_assist: bool = False  # act with hover assist on as well (10 §3)

    @property
    def nominal(self) -> bool:
        """True when the values change nothing: the flight model stays exactly the 05 one."""
        return (
            self.mass_kg == MASS
            and self.payload_kg == 0.0
            and not any(self.cg_offset_cm)
            and not any(self.level_trim_deg)
            and not any(self.motor_gain)
            and self.prop_damage == 0.0
            and self.imu_noise == 0.0
            and self.arm_bias_deg == 0.0
        )

    @property
    def total_mass(self) -> float:
        return self.mass_kg + self.payload_kg

    def params(self) -> dict[str, Any]:
        return {
            "mass_kg": self.mass_kg,
            "payload_kg": self.payload_kg,
            "cg_offset_cm": list(self.cg_offset_cm),
            "level_trim_deg": list(self.level_trim_deg),
            "motor_gain": list(self.motor_gain),
            "prop_damage": self.prop_damage,
            "damaged_prop": self.damaged_prop,
            "imu_noise": self.imu_noise,
            "arm_bias_deg": self.arm_bias_deg,
            "with_assist": self.with_assist,
        }

    def ref(self) -> dict[str, Any]:
        """What the snapshot shows (06 §4): the preset name and seed only."""
        return {"preset": self.preset, "seed": self.seed}

    def hover_throttle(self) -> float | None:
        """Level hover throttle out of ground effect at full battery; None when the airframe cannot lift."""
        model = AirframeModel(self)
        need = self.total_mass / (MASS * THRUST_TO_WEIGHT)
        if model.thrust_fraction(1.0) < need:
            return None
        lo, hi = 0.0, 1.0
        for _ in range(40):
            mid = (lo + hi) / 2.0
            if model.thrust_fraction(mid) < need:
                lo = mid
            else:
                hi = mid
        return round(throttle_for_fraction(hi), 3)

    def with_params(
        self, patch: dict[str, Any], *, preset: str = "custom", seed: int | None = None
    ) -> Airframe:
        values = self.params()
        values.update(validate_params(patch))
        return Airframe(
            preset=preset,
            seed=self.seed if seed is None else seed,
            mass_kg=values["mass_kg"],
            payload_kg=values["payload_kg"],
            cg_offset_cm=tuple(values["cg_offset_cm"]),
            level_trim_deg=tuple(values["level_trim_deg"]),
            motor_gain=tuple(values["motor_gain"]),
            prop_damage=values["prop_damage"],
            damaged_prop=int(values["damaged_prop"]),
            imu_noise=values["imu_noise"],
            arm_bias_deg=values["arm_bias_deg"],
            with_assist=bool(values["with_assist"]),
        )


def _number(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name)
    number = float(value)
    lo, hi = LIMITS[name]
    if not math.isfinite(number) or not lo <= number <= hi:
        raise ValueError(name)
    return number


def validate_params(patch: dict[str, Any]) -> dict[str, Any]:
    """Check a partial parameter set; raises ValueError naming the first bad field."""
    if not isinstance(patch, dict):
        raise ValueError("params")
    out: dict[str, Any] = {}
    for name, value in patch.items():
        if value is None:
            continue
        if name in FLAGS:
            if not isinstance(value, bool):
                raise ValueError(name)
            out[name] = value
            continue
        if name not in LIMITS:
            raise ValueError(name)
        if name in VECTORS:
            if not isinstance(value, (list, tuple)) or len(value) != VECTORS[name]:
                raise ValueError(name)
            out[name] = [_number(name, v) for v in value]
        elif name == "damaged_prop":
            number = _number(name, value)
            if not number.is_integer():
                raise ValueError(name)
            out[name] = int(number)
        else:
            out[name] = _number(name, value)
    return out


def _r(x: float, digits: int = 3) -> float:
    return round(x, digits) + 0.0  # + 0.0 folds -0.0


def _polar(rng: random.Random, lo: float, hi: float) -> tuple[float, float]:
    radius = rng.uniform(lo, hi)
    angle = rng.uniform(0.0, 2.0 * math.pi)
    return _r(radius * math.cos(angle)), _r(radius * math.sin(angle))


def generate(preset: str, seed: int = 0) -> Airframe:
    """A preset airframe; the same (preset, seed) always gives the same values."""
    if preset not in GENERATED:
        raise ValueError("preset")
    seed = int(seed)
    if not 0 <= seed <= SEED_MAX:
        raise ValueError("seed")
    if preset == "ideal":
        return Airframe(preset="ideal", seed=seed)
    rng = random.Random(f"{preset}:{seed}")
    if preset == "typical":
        return Airframe(
            preset=preset,
            seed=seed,
            cg_offset_cm=_polar(rng, 0.0, 0.25),
            level_trim_deg=(_r(rng.uniform(-0.4, 0.4), 2), _r(rng.uniform(-0.4, 0.4), 2)),
            motor_gain=tuple(_r(rng.uniform(-0.02, 0.02)) for _ in range(4)),  # type: ignore[arg-type]
            prop_damage=0.0,
            damaged_prop=rng.randint(1, 4),
            imu_noise=1.0,
        )
    return Airframe(
        preset=preset,
        seed=seed,
        payload_kg=_r(rng.uniform(0.005, 0.02)),
        cg_offset_cm=_polar(rng, 0.7, 1.4),
        level_trim_deg=_polar(rng, 1.0, 2.0),
        motor_gain=tuple(_r(rng.uniform(-0.08, 0.08)) for _ in range(4)),  # type: ignore[arg-type]
        prop_damage=_r(rng.uniform(0.2, 0.5), 2),
        damaged_prop=rng.randint(1, 4),
        imu_noise=2.0,
    )


def resolve(
    spec: dict[str, Any] | None,
    current: Airframe | None,
    default_preset: str,
    new_seed: Any,
) -> Airframe:
    """Turn a request body ``{preset?, params?, seed?}`` into an Airframe (06 §4).

    * nothing: ``default_preset`` with a fresh seed;
    * a generated preset alone: that preset from ``seed`` (a fresh one when omitted);
    * ``params``: overlaid on the named generated preset, or on ``current`` (ideal when there is none);
      the result is ``custom``.
    ``new_seed()`` returns a fresh seed. Raises ValueError naming the bad field.
    """
    spec = dict(spec or {})
    preset = spec.get("preset")
    params = spec.get("params")
    seed = spec.get("seed")
    if preset is not None and preset not in PRESETS:
        raise ValueError("preset")
    bad_seed = isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= SEED_MAX
    if seed is not None and bad_seed:
        raise ValueError("seed")
    if preset is None and params is None:
        preset = default_preset
    if preset in GENERATED:
        base = generate(preset, new_seed() if seed is None else seed)
    else:
        base = current or Airframe()
        if seed is not None:
            base = replace(base, seed=seed)
    if params is None:
        return base if preset != "custom" else replace(base, preset="custom")
    return base.with_params(params)


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting; None when singular."""
    n = len(rhs)
    a = [[*row, rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(n):
            if r != col:
                factor = a[r][col] / a[col][col]
                for c in range(col, n + 1):
                    a[r][c] -= factor * a[col][c]
    return [a[i][n] / a[i][i] for i in range(n)]


@dataclass
class AirframeModel:
    """Runtime state of an airframe inside ``physics.Quad`` (fleet thread only)."""

    spec: Airframe
    k: list[float] = field(init=False)
    weights: list[float] = field(init=False)

    def __post_init__(self) -> None:
        af = self.spec
        k = [1.0 + g for g in af.motor_gain]
        self.damaged = min(4, max(1, af.damaged_prop)) - 1
        k[self.damaged] *= 1.0 - PROP_LOSS * af.prop_damage
        self.k = k
        self.mass = af.total_mass
        self.cg_m = (af.cg_offset_cm[0] / 100.0, af.cg_offset_cm[1] / 100.0)  # forward, right
        self.cg = (self.cg_m[0] / ARM_M, self.cg_m[1] / ARM_M)  # arm units
        self.base_trim = (math.radians(af.level_trim_deg[0]), math.radians(af.level_trim_deg[1]))
        dx, dy = self.cg
        matrix = [
            [k[i] * (MOTOR_X[i] - dx) for i in range(4)],
            [k[i] * (MOTOR_Y[i] - dy) for i in range(4)],
            [k[i] * MOTOR_YAW[i] for i in range(4)],
            [1.0, 1.0, 1.0, 1.0],
        ]
        self.weights = _solve(matrix, [0.0, 0.0, 0.0, 4.0]) or [1.0, 1.0, 1.0, 1.0]
        total = sum(k)
        # Reaction-torque imbalance at equal outputs, as a share of one rotor's thrust (+: the body yaws
        # clockwise); what the yaw integrator has to take up.
        self.yaw_imbalance = sum(k[i] * MOTOR_YAW[i] for i in range(4)) / total
        self.reset()

    def reset(self) -> None:
        seed = self.spec.seed
        self.rng = random.Random(seed ^ _PHYS_SALT)
        self.sensor_rng = random.Random(seed ^ _SENSOR_SALT)
        self.jitter = [1.0, 1.0, 1.0, 1.0]  # per-rotor thrust multiplier from the damaged propeller
        self._wobble = 0.0
        self.arms = 0
        self.bias = (0.0, 0.0)
        self.trim = self.base_trim

    def rearm(self) -> tuple[float, float]:
        """A new arming: draw this flight's level error (``arm_bias_deg``); returns it in degrees."""
        self.arms += 1
        spread = self.spec.arm_bias_deg
        if spread > 0.0:
            rng = random.Random(f"bias:{self.spec.seed}:{self.arms}")
            self.bias = (
                math.radians(rng.uniform(-spread, spread)),
                math.radians(rng.uniform(-spread, spread)),
            )
        self.trim = (self.base_trim[0] + self.bias[0], self.base_trim[1] + self.bias[1])
        return math.degrees(self.trim[0]), math.degrees(self.trim[1])

    # ---------------------------------------------------------------- static trim

    def fractions(self, F: float) -> list[float]:
        """Per-rotor thrust fraction the trimmed mixer commands for collective ``F``, clamped to 0..1."""
        return [min(1.0, max(0.0, F * w)) for w in self.weights]

    def thrust_fraction(self, F: float) -> float:
        """Total thrust as a fraction of the nominal maximum for collective thrust fraction ``F``."""
        return sum(self.k[i] * f for i, f in enumerate(self.fractions(F))) / 4.0

    def efficiency(self) -> float:
        """Unsaturated thrust per unit collective (1.0 for a nominal airframe)."""
        return sum(self.k[i] * self.weights[i] for i in range(4)) / 4.0

    def motor_outputs(self, F: float) -> list[float]:
        """Per-motor output 0..1 of the trimmed mixer."""
        return [throttle_for_fraction(f) for f in self.fractions(F)]

    # ---------------------------------------------------------------- the damaged propeller

    def step(self, dt: float, spinning: float) -> None:
        """Advance the damaged propeller's thrust fluctuation by one physics step."""
        damage = self.spec.prop_damage
        if damage <= 0.0:
            return
        if spinning > 0.0:
            decay = math.exp(-dt / JITTER_TAU_S)
            spread = math.sqrt(1.0 - decay * decay)
            sigma = JITTER_THRUST * damage * min(1.0, spinning * 2.0)
            self._wobble = self._wobble * decay + sigma * spread * self.rng.gauss(0.0, 1.0)
        else:
            self._wobble = 0.0
        self.jitter[self.damaged] = 1.0 + self._wobble

    # ---------------------------------------------------------------- sensors

    def vibration_extra(self, motor: float) -> float:
        return VIB_DAMAGE * self.spec.prop_damage * max(0.0, motor)

    def gauss(self, sigma: float) -> float:
        return self.sensor_rng.gauss(0.0, sigma) if sigma > 0.0 else 0.0

    def noisy_attitude(
        self, roll: float, pitch: float, yaw: float, rates: list[float], motor: float
    ) -> tuple[float, float, float, list[float]]:
        """ATTITUDE as the board estimates it: level trim removed, noise added (radians, rad/s)."""
        n = self.spec.imu_noise
        vib_rate = VIB_RATE_SHARE * self.vibration_extra(motor)
        r = roll - self.trim[0] + self.gauss(ATT_NOISE_RAD * n)
        p = pitch - self.trim[1] + self.gauss(ATT_NOISE_RAD * n)
        y = yaw + self.gauss(YAW_NOISE_RAD * n)
        w = [rate + self.gauss(RATE_NOISE_RAD_S * n + vib_rate) for rate in rates]
        return r, p, y, w

    def accel_noise(self, motor: float) -> float:
        return ACC_NOISE_MS2 * self.spec.imu_noise + VIB_ACC_SHARE * self.vibration_extra(motor)

    def gyro_noise(self, motor: float) -> float:
        return RATE_NOISE_RAD_S * self.spec.imu_noise + VIB_RATE_SHARE * self.vibration_extra(motor)


def specific_force_body(
    accel_enu: list[float], roll: float, pitch: float, yaw: float
) -> tuple[float, float, float]:
    """Accelerometer reading (FRD body axes, m/s^2) for a world ENU acceleration and an attitude.

    ``yaw`` is the NED heading (0 north, clockwise +). At rest and level the result is (0, 0, -g).
    """
    e, n, u = accel_enu[0], accel_enu[1], accel_enu[2] + G
    d = -u
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    x = cp * cy * n + cp * sy * e - sp * d
    y = (sr * sp * cy - cr * sy) * n + (sr * sp * sy + cr * cy) * e + sr * cp * d
    z = (cr * sp * cy + sr * sy) * n + (cr * sp * sy - sr * cy) * e + cr * cp * d
    return x, y, z
