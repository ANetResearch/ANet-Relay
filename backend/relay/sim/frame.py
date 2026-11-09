"""Airframe types of the simulated quadrotor (11 §8.1): mass, geometry, rotors, drag and battery.

A ``FrameModel`` is what ``physics.Quad`` flies: the take-off mass, the wheelbase (motor-to-motor diagonal)
and propeller size, the principal moments of inertia, the rotor maximum (as a thrust-to-weight ratio, the
per-rotor thrust follows), the rotor speed time constants up and down, the reaction-torque coefficient, the
body drag and the battery. Three presets ship:

* ``micro-120``: the v0.5.3 airframe, exactly the module constants of ``sim.physics`` (0.12 kg, 25 cm,
  3S 850 mAh); the default, so a simulator without a frame flies as before;
* ``small-250``: a 250 mm class quadrotor on 5 inch propellers and a 3S pack (the operator's small aircraft
  class, 11 §0);
* ``large-450``: a 450 mm class quadrotor on 10 inch propellers and a 4S pack.

``derive_from_hover`` sets the rotor maximum so the aircraft hovers at a flight controller's learned hover
throttle (ArduPilot ``MOT_THST_HOVER`` with ``MOT_THST_EXPO`` and ``MOT_SPIN_MIN`` / ``MOT_SPIN_MAX``;
PX4 ``MPC_THR_HOVER`` with ``THR_MDL_FAC``), so a firmware twin flown with the real aircraft's parameters
hovers at the same output (11 §5). ``inertia_from`` is the empirical inertia of a quadrotor of a given mass
and wheelbase.

The contact geometry (legs, propeller tips, top of the body) follows from the wheelbase; see
``leg_m`` / ``top_m`` / ``prop_z_m``.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields, replace
from typing import Any

G = 9.80665
INCH = 0.0254
INERTIA_RATIO = 0.10  # Ixx = Iyy = ratio * mass * (wheelbase / 2)^2 (F450 class: about 0.0075 kg m^2)
INERTIA_YAW = 1.8  # Izz / Ixx
LEG_RATIO = 0.12  # leg length below the centre of mass per metre of wheelbase (3 cm at 25 cm)
TOP_RATIO = 0.12  # top of the body above the centre of mass
PROP_Z_RATIO = 0.06  # propeller plane above the centre of mass
DEFAULT = "micro-120"

# Limits for validation (and the editor's ranges). Vectors are checked per component.
LIMITS: dict[str, tuple[float, float]] = {
    "mass_kg": (0.05, 30.0),
    "wheelbase_m": (0.08, 1.5),
    "prop_in": (1.5, 32.0),
    "ixx": (1e-6, 10.0),
    "iyy": (1e-6, 10.0),
    "izz": (1e-6, 20.0),
    "thrust_to_weight": (1.1, 15.0),
    "omega_max": (100.0, 10000.0),
    "tau_up_s": (0.002, 0.5),
    "tau_down_s": (0.002, 0.5),
    "yaw_moment_m": (0.0005, 0.2),
    "rotor_drag_per_s": (0.0, 2.0),
    "body_drag_lin": (0.0, 50.0),
    "body_drag_quad": (0.0, 50.0),
    "cells": (1, 14),
    "capacity_mah": (50.0, 100000.0),
    "r_internal_ohm": (0.0005, 2.0),
    "load_current_a": (0.5, 1000.0),
}
VECTORS = ("body_drag_lin", "body_drag_quad")


@dataclass(frozen=True)
class FrameModel:
    """One airframe type. Body drag is absolute (N per m/s and per (m/s)^2 along the body axes x, y, z);
    ``rotor_drag_per_s`` is the rotor-plane drag in hover per unit mass (Gazebo ``rotorDragCoefficient``
    follows from it and the hover rotor speed)."""

    preset: str = DEFAULT
    mass_kg: float = 0.12
    wheelbase_m: float = 0.25
    prop_in: float = 0.13 / INCH
    ixx: float = 1.2e-4
    iyy: float = 1.2e-4
    izz: float = 2.2e-4
    thrust_to_weight: float = 2.4
    omega_max: float = 3100.0  # rad/s at full output and the reference voltage
    tau_up_s: float = 0.0125
    tau_down_s: float = 0.025
    yaw_moment_m: float = 0.006  # reaction torque per newton of thrust (Gazebo momentConstant)
    rotor_drag_per_s: float = 0.15
    body_drag_lin: tuple[float, float, float] = (0.20 * 0.12, 0.20 * 0.12, 0.35 * 0.12)
    body_drag_quad: tuple[float, float, float] = (0.065 * 0.12, 0.065 * 0.12, 0.08 * 0.12)
    cells: int = 3
    capacity_mah: float = 850.0
    r_internal_ohm: float = 0.075
    load_current_a: float = 11.0  # pack current at full output

    # ---------------------------------------------------------------- derived values

    @property
    def arm_m(self) -> float:
        """Lever arm of each rotor about the roll and pitch axes (quad-x), m."""
        return self.wheelbase_m / 2.0 / math.sqrt(2.0)

    @property
    def prop_diameter_m(self) -> float:
        return self.prop_in * INCH

    @property
    def rotor_thrust_n(self) -> float:
        """Maximum thrust of one rotor at the reference voltage, N."""
        return self.thrust_to_weight * self.mass_kg * G / 4.0

    @property
    def hover_fraction(self) -> float:
        """Thrust fraction (of the maximum) that balances the weight."""
        return 1.0 / self.thrust_to_weight

    @property
    def leg_m(self) -> float:
        """Feet below the centre of mass (under the motors)."""
        return LEG_RATIO * self.wheelbase_m

    @property
    def top_m(self) -> float:
        """Top of the body above the centre of mass."""
        return TOP_RATIO * self.wheelbase_m

    @property
    def prop_z_m(self) -> float:
        """Propeller plane above the centre of mass."""
        return PROP_Z_RATIO * self.wheelbase_m

    @property
    def capacity_ah(self) -> float:
        return self.capacity_mah / 1000.0

    @property
    def nominal(self) -> bool:
        """True for exactly the default airframe (the v0.5.3 flight model)."""
        return self == DEFAULT_FRAME or replace(self, preset=DEFAULT) == DEFAULT_FRAME

    # ---------------------------------------------------------------- serialisation

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        for name in VECTORS:
            out[name] = list(out[name])
        out["rotor_thrust_n"] = self.rotor_thrust_n
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any], base: FrameModel | None = None) -> FrameModel:
        """Build a frame from ``data`` over ``base`` (or the preset ``data["preset"]``, else the default).

        A changed mass or wheelbase without explicit inertia scales the base inertia (``I ~ m * b^2``); a
        changed mass without a thrust value keeps the base rotors (the thrust-to-weight ratio follows) and
        the body drag (absolute). ``rotor_thrust_n`` may stand in for ``thrust_to_weight``. Unknown keys and
        out-of-range values raise ValueError naming the field.
        """
        if not isinstance(data, dict):
            raise ValueError("frame")
        known = {f.name for f in fields(cls)} | {"rotor_thrust_n"}
        for key in data:
            if key not in known:
                raise ValueError(key)
        name = data.get("preset")
        if base is None:
            if name is None:
                base = DEFAULT_FRAME
            elif not isinstance(name, str) or name not in PRESETS:
                raise ValueError("preset")
            else:
                base = PRESETS[name]
        values: dict[str, Any] = {}
        if name is not None:
            if not isinstance(name, str) or not name or len(name) > 40:
                raise ValueError("preset")
            values["preset"] = name
        for f in fields(cls):
            if f.name == "preset" or f.name not in data or data[f.name] is None:
                continue
            raw = data[f.name]
            if f.name in VECTORS:
                if not isinstance(raw, (list, tuple)) or len(raw) != 3:
                    raise ValueError(f.name)
                values[f.name] = tuple(_number(f.name, c) for c in raw)
            elif f.name == "cells":
                number = _number(f.name, raw)
                if not number.is_integer():
                    raise ValueError(f.name)
                values[f.name] = int(number)
            else:
                values[f.name] = _number(f.name, raw)
        mass = values.get("mass_kg", base.mass_kg)
        wheelbase = values.get("wheelbase_m", base.wheelbase_m)
        if (mass != base.mass_kg or wheelbase != base.wheelbase_m) and not any(
            k in values for k in ("ixx", "iyy", "izz")
        ):
            scale = (mass / base.mass_kg) * (wheelbase / base.wheelbase_m) ** 2
            values["ixx"], values["iyy"], values["izz"] = base.ixx * scale, base.iyy * scale, base.izz * scale
        if data.get("rotor_thrust_n") is not None and "thrust_to_weight" not in values:
            rotor = _positive("rotor_thrust_n", data["rotor_thrust_n"])
            values["thrust_to_weight"] = 4.0 * rotor / (mass * G)
        elif mass != base.mass_kg and "thrust_to_weight" not in values:
            values["thrust_to_weight"] = 4.0 * base.rotor_thrust_n / (mass * G)
        frame = replace(base, **values)
        frame.validate()
        return frame

    def validate(self) -> None:
        """Raise ValueError naming the first field out of its range or inconsistent."""
        for f in fields(self):
            if f.name == "preset":
                if not isinstance(self.preset, str) or not self.preset:
                    raise ValueError("preset")
                continue
            lo, hi = LIMITS[f.name]
            value = getattr(self, f.name)
            values = value if f.name in VECTORS else (value,)
            if f.name in VECTORS and len(values) != 3:
                raise ValueError(f.name)
            for c in values:
                if isinstance(c, bool) or not isinstance(c, (int, float)):
                    raise ValueError(f.name)
                if not math.isfinite(c) or not lo <= c <= hi:
                    raise ValueError(f.name)
        if self.tau_down_s < self.tau_up_s:
            raise ValueError("tau_down_s")  # rotors spin down no faster than they spin up
        if self.prop_diameter_m >= self.wheelbase_m / math.sqrt(2.0):
            raise ValueError("prop_in")  # adjacent propellers would overlap
        if self.izz < max(self.ixx, self.iyy) or self.izz > self.ixx + self.iyy + 1e-12:
            raise ValueError("izz")  # a flat body: max(Ixx, Iyy) <= Izz <= Ixx + Iyy


def _number(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name)
    number = float(value)
    lo, hi = LIMITS[name]
    if not math.isfinite(number) or not lo <= number <= hi:
        raise ValueError(name)
    return number


def _positive(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name)
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(name)
    return number


def inertia_from(mass_kg: float, wheelbase_m: float) -> tuple[float, float, float]:
    """Empirical principal inertia (Ixx, Iyy, Izz) of a quad-x of this mass and wheelbase, kg m^2.

    Motors, arms and pack spread over the frame give ``Ixx = Iyy = 0.10 * m * (b / 2)^2`` and
    ``Izz = 1.8 * Ixx`` (a 1.5 kg 450 mm frame: 0.0076 / 0.0137 kg m^2, in line with measured F450 values).
    """
    ixx = INERTIA_RATIO * mass_kg * (wheelbase_m / 2.0) ** 2
    return (ixx, ixx, INERTIA_YAW * ixx)


def _thrust_from_actuator(a: float, expo: float) -> float:
    return (1.0 - expo) * a + expo * a * a


def _actuator_from_thrust(thrust: float, expo: float) -> float:
    """Inverse of ``(1 - expo) * a + expo * a^2`` on [0, 1] (ArduPilot ``thr_lin``, PX4 ``THR_MDL_FAC``)."""
    thrust = min(1.0, max(0.0, thrust))
    if abs(expo) < 1e-9:
        return thrust
    return ((expo - 1.0) + math.sqrt((1.0 - expo) ** 2 + 4.0 * expo * thrust)) / (2.0 * expo)


def derive_from_hover(
    frame: FrameModel,
    hover_throttle: float,
    *,
    expo: float = 0.0,
    spin_min: float = 0.0,
    spin_max: float = 1.0,
) -> FrameModel:
    """The frame with the rotor maximum that hovers it at ``hover_throttle``.

    ``hover_throttle`` is the flight controller's collective thrust demand at hover, 0..1 (ArduPilot
    ``MOT_THST_HOVER``, PX4 ``MPC_THR_HOVER``). The controller linearises it into an actuator value with
    ``expo`` (ArduPilot ``MOT_THST_EXPO``, PX4 ``THR_MDL_FAC``) and scales that into the spin range
    ``spin_min``..``spin_max`` (ArduPilot ``MOT_SPIN_MIN`` / ``MOT_SPIN_MAX``; 0..1 on PX4): the motor output
    the ESC receives. The simulated ESC (``physics.thrust_fraction``) turns that output into a thrust
    fraction; the thrust-to-weight ratio is set so that fraction exactly balances the weight.
    """
    from . import physics  # physics imports this module

    for name, value, lo, hi in (
        ("hover_throttle", hover_throttle, 0.05, 0.95),
        ("expo", expo, -1.0, 1.0),
        ("spin_min", spin_min, 0.0, 0.5),
        ("spin_max", spin_max, 0.5, 1.0),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(name)
        if not lo <= value <= hi:
            raise ValueError(name)
    if spin_max <= spin_min:
        raise ValueError("spin_max")
    actuator = _actuator_from_thrust(hover_throttle, expo)
    output = spin_min + (spin_max - spin_min) * actuator
    fraction = physics.thrust_fraction(output)
    if fraction <= 0.0:
        raise ValueError("hover_throttle")
    twr = 1.0 / fraction
    lo, hi = LIMITS["thrust_to_weight"]
    if not lo <= twr <= hi:
        raise ValueError("hover_throttle")
    return replace(frame, thrust_to_weight=twr)


def _preset(name: str, mass: float, wheelbase: float, prop_in: float, **kw: Any) -> FrameModel:
    ixx, iyy, izz = inertia_from(mass, wheelbase)
    return FrameModel(
        preset=name,
        mass_kg=mass,
        wheelbase_m=wheelbase,
        prop_in=prop_in,
        ixx=ixx,
        iyy=iyy,
        izz=izz,
        body_drag_lin=(0.20 * mass, 0.20 * mass, 0.35 * mass),
        body_drag_quad=(0.065 * mass, 0.065 * mass, 0.08 * mass),
        **kw,
    )


DEFAULT_FRAME = FrameModel()
PRESETS: dict[str, FrameModel] = {
    DEFAULT: DEFAULT_FRAME,
    # 250 mm class on 5 inch propellers, 3S 850 mAh (the operator's small aircraft; 11 §5 example).
    "small-250": _preset(
        "small-250",
        0.42,
        0.25,
        5.0,
        thrust_to_weight=3.5,
        omega_max=2700.0,
        tau_up_s=0.018,
        tau_down_s=0.03,
        yaw_moment_m=0.007,
        cells=3,
        capacity_mah=850.0,
        r_internal_ohm=0.045,
        load_current_a=32.0,
    ),
    # 450 mm class on 10 inch propellers, 4S 5000 mAh (an F450-type aircraft).
    "large-450": _preset(
        "large-450",
        1.5,
        0.45,
        10.0,
        thrust_to_weight=2.2,
        omega_max=1050.0,
        tau_up_s=0.035,
        tau_down_s=0.055,
        yaw_moment_m=0.016,
        cells=4,
        capacity_mah=5000.0,
        r_internal_ohm=0.02,
        load_current_a=45.0,
    ),
}


def preset(name: str) -> FrameModel:
    """The preset ``name``; raises ValueError for an unknown one."""
    frame = PRESETS.get(name)
    if frame is None:
        raise ValueError("preset")
    return frame


__all__ = [
    "DEFAULT",
    "DEFAULT_FRAME",
    "PRESETS",
    "FrameModel",
    "derive_from_hover",
    "inertia_from",
    "preset",
]
