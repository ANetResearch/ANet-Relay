"""Sensor readings from the physics state (11 §8.2), for the firmware twin and hardware in the loop.

``SensorModel`` turns a ``physics.Quad`` state into what a flight controller's sensors would read:

* ``imu``: accelerometer specific force and gyro rates in the body frame (FRD), with white noise and a slow
  bias (first-order Gauss-Markov). At rest and level the accelerometer reads (0, 0, -g).
* ``baro``: absolute pressure of the International Standard Atmosphere at the aircraft's altitude above
  mean sea level, the pressure altitude and the temperature, with noise and a slow drift.
* ``mag``: the earth field in the body frame, in gauss, from a tilted-dipole model at the home position
  (declination positive east, inclination positive down), or from an explicit ``field``; with noise.
* ``gps``: latitude, longitude, altitude AMSL and NED velocity with a slowly wandering position error and
  white velocity noise, the reported accuracies, satellites and fix type.

Physics conventions: ``s.p`` / ``s.v`` are ENU (x east, y north, z up), ``s.q`` is the FRD-to-NED attitude
(w, x, y, z), ``s.rates`` are body FRD rates and ``s.accel`` is the ENU kinematic acceleration. Positions map
to latitude / longitude on a sphere of radius 6 371 000 m around ``home`` (PX4 ``CONSTANTS_RADIUS_OF_EARTH``,
also what the fast simulator reports), so a firmware's local frame matches the physics frame.

No reading is ever exactly constant: PX4 flags a sensor whose value does not change as stuck, so the noise
scale has a floor (``NOISE_FLOOR``). Every random draw comes from ``random.Random(seed)``: the same seed and
the same sequence of calls give the same readings.
"""

from __future__ import annotations

import math
import random
from typing import Any

from .physics import DT, G

EARTH_RADIUS = 6371000.0
# Geomagnetic north pole (the dipole axis, IGRF 2025 epoch) and the equatorial dipole field strength.
GEOMAG_POLE = (80.8, -72.8)  # lat, lon degrees
DIPOLE_GAUSS = 0.30

# ISA (troposphere).
P0_PA = 101325.0
T0_K = 288.15
LAPSE = 0.0065  # K/m
ISA_EXP = 5.25588
ISA_K = 2.25577e-5  # LAPSE / T0_K

# Noise at scale 1 (1 sigma): white per reading, bias / drift as Gauss-Markov with its time constant.
ACCEL_NOISE = 0.04  # m/s^2
ACCEL_BIAS = 0.03  # m/s^2
GYRO_NOISE = 0.003  # rad/s
GYRO_BIAS = 0.002  # rad/s
BIAS_TAU_S = 300.0
BARO_NOISE_M = 0.08  # m of pressure altitude
BARO_DRIFT_M = 0.3
BARO_DRIFT_TAU_S = 120.0
MAG_NOISE = 0.003  # gauss
GPS_POS_WALK_M = 0.4  # horizontal position error, Gauss-Markov
GPS_ALT_WALK_M = 0.8
GPS_WALK_TAU_S = 30.0
GPS_POS_NOISE_M = 0.05
GPS_VEL_NOISE = 0.05  # m/s
GPS_EPH_M = 0.8
GPS_EPV_M = 1.2
GPS_SATS = 12
GPS_FIX_3D = 3
NOISE_FLOOR = 0.05  # readings keep some noise even at scale 0


def _unit_ecef(lat_deg: float, lon_deg: float) -> tuple[float, float, float]:
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    return (math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat))


def dipole_field(lat_deg: float, lon_deg: float) -> tuple[float, float, float]:
    """Earth field (north, east, down) in gauss at a site from the tilted geomagnetic dipole.

    ``B = B0 * (3 (m . r) r - m)`` with ``r`` the site's unit position and ``m`` the unit dipole moment, which
    points to the geomagnetic south pole: at the geomagnetic north pole the field points down with ``2 B0``,
    on the geomagnetic equator it points north with ``B0``.
    """
    r = _unit_ecef(lat_deg, lon_deg)
    pole = _unit_ecef(*GEOMAG_POLE)
    m = (-pole[0], -pole[1], -pole[2])
    dot = m[0] * r[0] + m[1] * r[1] + m[2] * r[2]
    b = tuple(DIPOLE_GAUSS * (3.0 * dot * r[i] - m[i]) for i in range(3))
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    north = (-math.sin(lat) * math.cos(lon), -math.sin(lat) * math.sin(lon), math.cos(lat))
    east = (-math.sin(lon), math.cos(lon), 0.0)
    down = (-r[0], -r[1], -r[2])
    return (
        sum(b[i] * north[i] for i in range(3)),
        sum(b[i] * east[i] for i in range(3)),
        sum(b[i] * down[i] for i in range(3)),
    )


def field_angles(ned: tuple[float, float, float]) -> tuple[float, float, float]:
    """(strength gauss, declination deg east +, inclination deg down +) of an NED field."""
    n, e, d = ned
    return (
        math.sqrt(n * n + e * e + d * d),
        math.degrees(math.atan2(e, n)),
        math.degrees(math.atan2(d, math.hypot(n, e))),
    )


def field_from_angles(
    strength: float, declination_deg: float, inclination_deg: float
) -> tuple[float, float, float]:
    dec, inc = math.radians(declination_deg), math.radians(inclination_deg)
    h = strength * math.cos(inc)
    return (h * math.cos(dec), h * math.sin(dec), strength * math.sin(inc))


def isa_pressure_pa(alt_m: float) -> float:
    """ISA static pressure at an altitude above mean sea level (troposphere), Pa."""
    return P0_PA * (1.0 - ISA_K * alt_m) ** ISA_EXP


def isa_altitude_m(pressure_pa: float) -> float:
    """Pressure altitude of an ISA static pressure, m."""
    return (1.0 - (pressure_pa / P0_PA) ** (1.0 / ISA_EXP)) / ISA_K


def _to_body(q: list[float], n: float, e: float, d: float) -> tuple[float, float, float]:
    """An NED vector in the body frame (FRD) for the FRD-to-NED attitude ``q``."""
    w, x, y, z = q
    r00 = 1.0 - 2.0 * (y * y + z * z)
    r01 = 2.0 * (x * y - w * z)
    r02 = 2.0 * (x * z + w * y)
    r10 = 2.0 * (x * y + w * z)
    r11 = 1.0 - 2.0 * (x * x + z * z)
    r12 = 2.0 * (y * z - w * x)
    r20 = 2.0 * (x * z - w * y)
    r21 = 2.0 * (y * z + w * x)
    r22 = 1.0 - 2.0 * (x * x + y * y)
    return (r00 * n + r10 * e + r20 * d, r01 * n + r11 * e + r21 * d, r02 * n + r12 * e + r22 * d)


class _Markov:
    """First-order Gauss-Markov process with standard deviation ``sigma`` and time constant ``tau``."""

    def __init__(self, rng: random.Random, sigma: float, tau: float) -> None:
        self.rng = rng
        self.sigma = sigma
        self.tau = tau
        self.x = rng.gauss(0.0, sigma) if sigma > 0.0 else 0.0

    def step(self, dt: float) -> float:
        if dt > 0.0 and self.sigma > 0.0:
            decay = math.exp(-dt / self.tau)
            self.x = self.x * decay + self.sigma * math.sqrt(1.0 - decay * decay) * self.rng.gauss(0.0, 1.0)
        return self.x


class SensorModel:
    """Sensor readings of one aircraft.

    ``home`` is (lat deg, lon deg, alt m AMSL) of the physics origin; ``noise`` scales every noise term
    (1 = typical MEMS sensors and a consumer GPS); ``field`` optionally gives the earth field as (strength
    gauss, declination deg, inclination deg) instead of the dipole model, so a bridge can match the
    firmware's own magnetic model (the dipole is within about 15 deg of the true declination).
    """

    def __init__(
        self,
        home: tuple[float, float, float] = (47.397742, 8.545594, 488.0),
        *,
        seed: int = 0,
        noise: float = 1.0,
        field: tuple[float, float, float] | None = None,
    ) -> None:
        lat, lon, alt = (float(c) for c in home)
        if not (-89.0 <= lat <= 89.0 and -180.0 <= lon <= 180.0 and -500.0 <= alt <= 9000.0):
            raise ValueError("home")
        if not math.isfinite(noise) or noise < 0.0:
            raise ValueError("noise")
        self.home = (lat, lon, alt)
        self.scale = max(NOISE_FLOOR, float(noise))
        self.rng = random.Random(seed)
        self.field_ned = field_from_angles(*field) if field is not None else dipole_field(lat, lon)
        k = self.scale
        rng = self.rng
        self._accel_bias = [_Markov(rng, ACCEL_BIAS * k, BIAS_TAU_S) for _ in range(3)]
        self._gyro_bias = [_Markov(rng, GYRO_BIAS * k, BIAS_TAU_S) for _ in range(3)]
        self._baro_drift = _Markov(rng, BARO_DRIFT_M * k, BARO_DRIFT_TAU_S)
        self._gps_walk = [
            _Markov(rng, GPS_POS_WALK_M * k, GPS_WALK_TAU_S),
            _Markov(rng, GPS_POS_WALK_M * k, GPS_WALK_TAU_S),
            _Markov(rng, GPS_ALT_WALK_M * k, GPS_WALK_TAU_S),
        ]
        self._gps_t: float | None = None
        self._cos_lat = math.cos(math.radians(lat))

    @property
    def declination_deg(self) -> float:
        return field_angles(self.field_ned)[1]

    @property
    def inclination_deg(self) -> float:
        return field_angles(self.field_ned)[2]

    def _gauss(self, sigma: float) -> float:
        return self.rng.gauss(0.0, sigma * self.scale)

    # ---------------------------------------------------------------- readings

    def imu(self, quad: Any, dt: float = DT) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """(accelerometer specific force m/s^2, gyro rad/s), both body FRD; ``dt`` since the last reading."""
        s = quad.s
        a = s.accel
        # specific force = kinematic acceleration - gravity, NED components (gravity is +g down)
        fx, fy, fz = _to_body(s.q, a[1], a[0], -a[2] - G)
        ab, gb = self._accel_bias, self._gyro_bias
        accel = (
            fx + ab[0].step(dt) + self._gauss(ACCEL_NOISE),
            fy + ab[1].step(dt) + self._gauss(ACCEL_NOISE),
            fz + ab[2].step(dt) + self._gauss(ACCEL_NOISE),
        )
        w = s.rates
        gyro = (
            w[0] + gb[0].step(dt) + self._gauss(GYRO_NOISE),
            w[1] + gb[1].step(dt) + self._gauss(GYRO_NOISE),
            w[2] + gb[2].step(dt) + self._gauss(GYRO_NOISE),
        )
        return accel, gyro

    def baro(self, quad: Any, dt: float = DT) -> tuple[float, float, float]:
        """(absolute pressure hPa, pressure altitude m AMSL, temperature deg C)."""
        alt = self.home[2] + quad.s.p[2]
        measured = alt + self._baro_drift.step(dt) + self._gauss(BARO_NOISE_M)
        pressure = isa_pressure_pa(measured)
        temperature = T0_K - LAPSE * alt - 273.15 + self._gauss(0.05)
        return pressure / 100.0, isa_altitude_m(pressure), temperature

    def mag(self, quad: Any) -> tuple[float, float, float]:
        """Earth field in the body frame (FRD), gauss."""
        n, e, d = self.field_ned
        x, y, z = _to_body(quad.s.q, n, e, d)
        return (x + self._gauss(MAG_NOISE), y + self._gauss(MAG_NOISE), z + self._gauss(MAG_NOISE))

    def gps(self, quad: Any, t: float) -> dict[str, Any]:
        """A GPS fix at time ``t`` (s, any monotonic clock): position, NED velocity, accuracy, satellites."""
        s = quad.s
        dt = 0.0 if self._gps_t is None else max(0.0, t - self._gps_t)
        self._gps_t = t
        wn, we, wu = (m.step(dt) for m in self._gps_walk)
        north = s.p[1] + wn + self._gauss(GPS_POS_NOISE_M)
        east = s.p[0] + we + self._gauss(GPS_POS_NOISE_M)
        up = s.p[2] + wu + self._gauss(GPS_POS_NOISE_M)
        lat0, lon0, alt0 = self.home
        return {
            "lat": lat0 + math.degrees(north / EARTH_RADIUS),
            "lon": lon0 + math.degrees(east / (EARTH_RADIUS * self._cos_lat)),
            "alt_m": alt0 + up,
            "vn": s.v[1] + self._gauss(GPS_VEL_NOISE),
            "ve": s.v[0] + self._gauss(GPS_VEL_NOISE),
            "vd": -s.v[2] + self._gauss(GPS_VEL_NOISE),
            "eph_m": GPS_EPH_M * self.scale if self.scale > 1.0 else GPS_EPH_M,
            "epv_m": GPS_EPV_M * self.scale if self.scale > 1.0 else GPS_EPV_M,
            "sats": GPS_SATS,
            "fix": GPS_FIX_3D,
        }


__all__ = ["SensorModel", "dipole_field", "field_angles", "isa_altitude_m", "isa_pressure_pa"]
