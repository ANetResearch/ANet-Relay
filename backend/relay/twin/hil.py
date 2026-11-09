"""MAVLink HIL messages without a transport (11 §6.2, §7): the PX4 twin's TCP link and, later, HITL over USB.

``HilEncoder`` turns the physics state into what PX4's ``simulator_mavlink`` (SITL) or its HITL mode reads:

* ``HIL_SENSOR`` every physics step: accelerometer (m/s^2) and gyro (rad/s) in the body frame, magnetometer
  (gauss), absolute pressure (hPa), pressure altitude (m), temperature (deg C), with ``fields_updated``
  naming those fields (bits 0-9, 11, 12: everything except the differential pressure);
* ``HIL_GPS`` at 10 Hz: fix, position (degE7, mm AMSL), accuracies (cm), NED velocity (cm/s), ground speed,
  course and satellites.

Readings come from ``sim.sensors.SensorModel``; ``imu`` lets the caller pass board-frame IMU values (the
airframe's level error and noise, ``twin.autopilot``). ``decode_actuators`` reads ``HIL_ACTUATOR_CONTROLS``:
the first four controls (PX4 motor order 1 front right, 2 rear left, 3 front left, 4 rear right, the same
as Relay's physics), clamped to 0..1, and the armed flag (``mode & MAV_MODE_FLAG_SAFETY_ARMED``).
"""

from __future__ import annotations

import math
from typing import Any

from ..mav.mavlink import Encoder, M, Parser
from ..sim.sensors import SensorModel

FIELDS_UPDATED = 0b1_1011_1111_1111  # accel, gyro, mag, abs pressure, pressure alt, temperature (no diff)
GPS_PERIOD_US = 100_000
SIM_COMPID = 200  # the simulator's component id on the HIL link (any non-autopilot id)


def gps_message(fix: dict[str, Any], t_us: int) -> Any:
    """``HIL_GPS`` from a ``SensorModel.gps`` reading."""
    vn, ve, vd = fix["vn"], fix["ve"], fix["vd"]
    ground = math.hypot(vn, ve)
    cog = (math.degrees(math.atan2(ve, vn)) % 360.0) if ground > 0.2 else None
    return M.MAVLink_hil_gps_message(
        int(t_us),
        int(fix["fix"]),
        round(fix["lat"] * 1e7),
        round(fix["lon"] * 1e7),
        round(fix["alt_m"] * 1000.0),
        min(65535, round(fix["eph_m"] * 100.0)),
        min(65535, round(fix["epv_m"] * 100.0)),
        min(65535, round(ground * 100.0)),
        round(vn * 100.0),
        round(ve * 100.0),
        round(vd * 100.0),
        65535 if cog is None else round(cog * 100.0) % 36000,
        int(fix["sats"]),
    )


class HilEncoder:
    """Encodes HIL_SENSOR / HIL_GPS frames and parses the autopilot's replies on one link."""

    def __init__(self, sensors: SensorModel, *, sysid: int = 1, compid: int = SIM_COMPID) -> None:
        self.sensors = sensors
        self.encoder = Encoder(sysid, compid)
        self.parser = Parser()
        self.next_gps_us = 0

    def sensor_message(
        self,
        quad: Any,
        t_us: int,
        dt: float,
        imu: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None,
        mag: tuple[float, float, float] | None = None,
    ) -> Any:
        accel, gyro = imu if imu is not None else self.sensors.imu(quad, dt)
        mx, my, mz = mag if mag is not None else self.sensors.mag(quad)
        pressure, alt, temperature = self.sensors.baro(quad, dt)
        return M.MAVLink_hil_sensor_message(
            int(t_us),
            accel[0],
            accel[1],
            accel[2],
            gyro[0],
            gyro[1],
            gyro[2],
            mx,
            my,
            mz,
            pressure,
            0.0,
            alt,
            temperature,
            FIELDS_UPDATED,
            0,
        )

    def frame(
        self,
        quad: Any,
        t_us: int,
        dt: float,
        imu: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None,
        mag: tuple[float, float, float] | None = None,
    ) -> bytes:
        """The bytes for one physics step at ``t_us``: HIL_SENSOR, and HIL_GPS when one is due."""
        data = self.encoder.encode(self.sensor_message(quad, t_us, dt, imu, mag))
        if t_us >= self.next_gps_us:
            self.next_gps_us = t_us + GPS_PERIOD_US
            data += self.encoder.encode(gps_message(self.sensors.gps(quad, t_us / 1e6), t_us))
        return data

    def parse(self, data: bytes) -> list[Any]:
        return self.parser.parse(data)


def decode_actuators(msg: Any) -> tuple[list[float], bool]:
    """(four motor outputs 0..1, armed) from HIL_ACTUATOR_CONTROLS."""
    controls = [min(1.0, max(0.0, float(c))) if math.isfinite(c) else 0.0 for c in list(msg.controls)[:4]]
    return controls, bool(int(msg.mode) & M.MAV_MODE_FLAG_SAFETY_ARMED)


__all__ = ["FIELDS_UPDATED", "HilEncoder", "decode_actuators", "gps_message"]
