"""Stick frame to MAVLink encodings (03 §5.2, §5.3).

API frame: roll right +, pitch forward +, yaw clockwise +, all -1..1; throttle
0..1. Per-vehicle signs (±1) multiply roll, pitch and yaw after the frame is
fixed, so calibration never changes the API.
"""

from __future__ import annotations

from collections.abc import Mapping

PWM_MIN = 1000
PWM_CENTER = 1500
PWM_MAX = 2000
RC_IGNORE = 65535  # channels 1-8: leave this channel's override unchanged
RC_RELEASE = 0  # channels 1-8: hand the channel back to the RC receiver
MANUAL_MAX = 1000

ARDUPILOT_RELEASE: tuple[int, ...] = (RC_RELEASE,) * 8


def _sign(signs: Mapping[str, int] | None, axis: str) -> int:
    if not signs:
        return 1
    return -1 if int(signs.get(axis, 1)) < 0 else 1


def _clamp(value: float, low: int, high: int) -> int:
    return max(low, min(high, round(value)))


def ardupilot_channels(
    roll: float, pitch: float, yaw: float, throttle: float, signs: Mapping[str, int] | None = None
) -> tuple[int, ...]:
    """RC_CHANNELS_OVERRIDE channels 1-8. Low pitch PWM is pitch forward (ArduPilot convention)."""
    ch1 = _clamp(PWM_CENTER + 500 * roll * _sign(signs, "roll"), PWM_MIN, PWM_MAX)
    ch2 = _clamp(PWM_CENTER - 500 * pitch * _sign(signs, "pitch"), PWM_MIN, PWM_MAX)
    ch3 = _clamp(PWM_MIN + 1000 * throttle, PWM_MIN, PWM_MAX)
    ch4 = _clamp(PWM_CENTER + 500 * yaw * _sign(signs, "yaw"), PWM_MIN, PWM_MAX)
    return (ch1, ch2, ch3, ch4, RC_IGNORE, RC_IGNORE, RC_IGNORE, RC_IGNORE)


def px4_manual(
    roll: float, pitch: float, yaw: float, throttle: float, signs: Mapping[str, int] | None = None
) -> tuple[int, int, int, int]:
    """MANUAL_CONTROL (x, y, z, r): x forward +, y right +, z throttle 0..1000, r clockwise +."""
    x = _clamp(MANUAL_MAX * pitch * _sign(signs, "pitch"), -MANUAL_MAX, MANUAL_MAX)
    y = _clamp(MANUAL_MAX * roll * _sign(signs, "roll"), -MANUAL_MAX, MANUAL_MAX)
    z = _clamp(MANUAL_MAX * throttle, 0, MANUAL_MAX)
    r = _clamp(MANUAL_MAX * yaw * _sign(signs, "yaw"), -MANUAL_MAX, MANUAL_MAX)
    return (x, y, z, r)


def sticks_from_ardupilot(channels: tuple[int, ...]) -> tuple[float, float, float, float]:
    """Inverse of ardupilot_channels with unit signs: (roll, pitch, yaw, throttle)."""
    ch1, ch2, ch3, ch4 = channels[:4]
    return (
        (ch1 - PWM_CENTER) / 500.0,
        -(ch2 - PWM_CENTER) / 500.0,
        (ch4 - PWM_CENTER) / 500.0,
        (ch3 - PWM_MIN) / 1000.0,
    )


def sticks_from_px4(x: int, y: int, z: int, r: int) -> tuple[float, float, float, float]:
    """Inverse of px4_manual with unit signs: (roll, pitch, yaw, throttle)."""
    return (y / 1000.0, x / 1000.0, r / 1000.0, z / 1000.0)
