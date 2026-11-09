"""Sensor readings from the physics state (11 §8.2): IMU, barometer, magnetometer and GPS."""

from __future__ import annotations

import math
import statistics

import pytest

from relay.sim.physics import DT, G, Quad
from relay.sim.sensors import (
    EARTH_RADIUS,
    SensorModel,
    dipole_field,
    field_angles,
    isa_altitude_m,
    isa_pressure_pa,
)

SEATTLE = (47.6, -122.3, 50.0)  # east declination, northern hemisphere
SYDNEY = (-33.9, 151.2, 20.0)  # east declination, field points up


def test_imu_at_rest_reads_minus_g_and_noise_is_never_constant():
    quad = Quad()
    quad.step(None, armed=False)
    sensors = SensorModel(seed=1)
    accel, gyro = zip(*(sensors.imu(quad) for _ in range(500)), strict=True)
    for axis, expect in enumerate((0.0, 0.0, -G)):
        values = [a[axis] for a in accel]
        assert statistics.mean(values) == pytest.approx(expect, abs=0.1)
        assert 0.02 < statistics.stdev(values) < 0.08
    assert all(abs(statistics.mean(g[i] for g in gyro)) < 0.01 for i in range(3))
    # PX4 flags a reading that never changes as stuck: even at noise scale 0 the values move
    quiet = SensorModel(seed=2, noise=0.0)
    readings = {quiet.imu(quad)[0][2] for _ in range(20)} | {quiet.baro(quad)[0] for _ in range(20)}
    assert len(readings) == 40


def test_imu_turns_with_the_attitude_and_sees_the_rates():
    quad = Quad()
    quad.s.on_ground = quad.s.resting = False
    quad.s.p[2] = 10.0
    quad.s.set_attitude(math.radians(90.0), 0.0, 0.0)  # right wing down: body y points down
    quad.s.rates = [0.5, -0.2, 0.1]
    sensors = SensorModel(seed=3, noise=0.05)
    (ax, ay, az), (p, q, r) = sensors.imu(quad)
    assert (ax, ay, az) == pytest.approx((0.0, -G, 0.0), abs=0.05)  # the specific force points up
    assert (p, q, r) == pytest.approx((0.5, -0.2, 0.1), abs=0.01)
    quad.s.accel = [0.0, 0.0, 2.0]  # climbing at 2 m/s^2, level
    quad.s.set_attitude(0.0, 0.0, 1.0)
    (ax, ay, az), _ = sensors.imu(quad)
    assert az == pytest.approx(-(G + 2.0), abs=0.05)


def test_baro_follows_the_standard_atmosphere():
    assert isa_pressure_pa(0.0) == pytest.approx(101325.0)
    assert isa_pressure_pa(1000.0) == pytest.approx(89874.6, rel=1e-4)
    assert isa_altitude_m(isa_pressure_pa(488.0)) == pytest.approx(488.0, abs=1e-6)
    quad = Quad()
    sensors = SensorModel((47.0, 8.0, 500.0), seed=4)
    samples = []
    for height in (0.0, 10.0, 50.0):
        quad.s.p[2] = height
        hpa, alt, temp = zip(*(sensors.baro(quad) for _ in range(200)), strict=True)
        samples.append((statistics.mean(hpa), statistics.mean(alt), statistics.mean(temp)))
    assert samples[0][0] > samples[1][0] > samples[2][0]  # pressure falls with height
    assert samples[0][0] == pytest.approx(isa_pressure_pa(500.0) / 100.0, abs=0.06)
    assert samples[2][1] - samples[0][1] == pytest.approx(50.0, abs=0.5)
    assert samples[0][2] == pytest.approx(15.0 - 0.0065 * 500.0, abs=0.05)


def test_mag_declination_and_inclination_signs():
    strength, dec, inc = field_angles(dipole_field(*SEATTLE[:2]))
    assert 0.45 < strength < 0.6 and dec > 5.0 and inc > 60.0
    _s, dec_s, inc_s = field_angles(dipole_field(*SYDNEY[:2]))
    assert dec_s > 0.0 and inc_s < -40.0  # southern hemisphere: the field points up
    _s, _d, inc_eq = field_angles(dipole_field(5.0, -75.0))  # near the geomagnetic equator in America
    assert abs(inc_eq) < 45.0
    quad = Quad()
    sensors = SensorModel(SEATTLE, seed=5, noise=0.1)
    assert sensors.declination_deg == pytest.approx(dec)
    # heading north: the horizontal field points forward and to the right (east declination), down into z
    mx, my, mz = sensors.mag(quad)
    assert mx > 0.15 and my > 0.0 and mz > 0.4
    assert math.degrees(math.atan2(my, mx)) == pytest.approx(dec, abs=2.0)
    # heading east (yaw 90 deg): the field now comes from the left
    quad.s.set_attitude(0.0, 0.0, math.radians(90.0))
    mx, my, _mz = sensors.mag(quad)
    assert my < -0.15
    # an explicit field (a firmware's own magnetic model) overrides the dipole
    explicit = SensorModel(SEATTLE, field=(0.5, -6.0, 46.0))
    assert explicit.declination_deg == pytest.approx(-6.0) and explicit.inclination_deg == pytest.approx(46.0)


def test_gps_converts_the_physics_frame_to_geodetic():
    home = (31.2304, 121.4737, 10.0)
    quad = Quad()
    quad.s.p[:] = [100.0, 200.0, 30.0]  # 100 m east, 200 m north, 30 m up
    quad.s.v[:] = [1.0, 2.0, -0.5]
    sensors = SensorModel(home, seed=6)
    fixes = [sensors.gps(quad, 1000.0 + i * 0.1) for i in range(50)]
    lat = statistics.mean(f["lat"] for f in fixes)
    lon = statistics.mean(f["lon"] for f in fixes)
    north = math.radians(lat - home[0]) * EARTH_RADIUS
    east = math.radians(lon - home[1]) * EARTH_RADIUS * math.cos(math.radians(home[0]))
    assert north == pytest.approx(200.0, abs=2.0) and east == pytest.approx(100.0, abs=2.0)
    assert statistics.mean(f["alt_m"] for f in fixes) == pytest.approx(40.0, abs=3.0)
    assert statistics.mean(f["vn"] for f in fixes) == pytest.approx(2.0, abs=0.05)
    assert statistics.mean(f["ve"] for f in fixes) == pytest.approx(1.0, abs=0.05)
    assert statistics.mean(f["vd"] for f in fixes) == pytest.approx(0.5, abs=0.05)
    first = fixes[0]
    assert first["fix"] == 3 and first["sats"] >= 6 and 0.0 < first["eph_m"] < first["epv_m"]
    assert len({f["lat"] for f in fixes}) == len(fixes)  # never exactly constant


def test_same_seed_same_readings():
    quad = Quad()
    quad.step(None, armed=False)

    def read(seed):
        sensors = SensorModel(SEATTLE, seed=seed)
        out = []
        for i in range(20):
            out.append(
                (sensors.imu(quad, DT), sensors.baro(quad), sensors.mag(quad), sensors.gps(quad, i * 0.1))
            )
        return out

    assert read(7) == read(7) and read(7) != read(8)
    with pytest.raises(ValueError):
        SensorModel((95.0, 0.0, 0.0))
    with pytest.raises(ValueError):
        SensorModel(noise=-1.0)
