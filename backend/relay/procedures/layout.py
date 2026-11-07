"""Motor layouts for the bench diagram (05 §4.2 S6): position and spin direction of every motor.

``device.motor_layout`` names the frame from MAV_TYPE (quad, hex, octo, tri, coaxial); this module adds the
geometry. Angles are degrees clockwise from the nose; ``dir`` is the propeller's rotation seen from above.
Numbering follows the X layouts of ArduPilot's motor matrix, which PX4's standard quad, hexa and octo X
airframes share. Pure data, no vehicle access.
"""

from __future__ import annotations

from typing import Any

from .. import device

# layout -> [(motor number, angle_deg, dir)]
_X_LAYOUTS: dict[str, list[tuple[int, float, str]]] = {
    "quad": [(1, 45.0, "ccw"), (2, -135.0, "ccw"), (3, -45.0, "cw"), (4, 135.0, "cw")],
    "hex": [
        (1, 90.0, "cw"),
        (2, -90.0, "ccw"),
        (3, -30.0, "cw"),
        (4, 150.0, "ccw"),
        (5, 30.0, "ccw"),
        (6, -150.0, "cw"),
    ],
    "octo": [
        (1, 22.5, "cw"),
        (2, -157.5, "cw"),
        (3, 67.5, "ccw"),
        (4, 157.5, "ccw"),
        (5, -22.5, "ccw"),
        (6, -112.5, "ccw"),
        (7, -67.5, "cw"),
        (8, 112.5, "cw"),
    ],
    # tricopter: two front arms and the tail; the tail servo sets yaw, so no direction is implied
    "tri": [(1, 60.0, "ccw"), (2, -60.0, "cw"), (3, 180.0, "ccw")],
    # coaxial: two rotors on one axis, counter-rotating
    "coaxial": [(1, 0.0, "cw"), (2, 0.0, "ccw")],
}


def layout(mav_type: int | None) -> dict[str, Any]:
    """{name, count, motors: [{n, angle_deg, dir}]} for the aircraft's frame (quad X when unknown)."""
    count, name = device.motor_layout(mav_type)
    rows = _X_LAYOUTS.get(name) or _X_LAYOUTS["quad"]
    motors = [{"n": n, "angle_deg": a, "dir": d} for n, a, d in rows][:count]
    return {"name": name, "count": count, "motors": motors}
