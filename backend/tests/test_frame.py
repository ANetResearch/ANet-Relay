"""Stick mapping (03 §5.2)."""

from relay import frame


def test_ardupilot_centre_and_extremes():
    assert frame.ardupilot_channels(0, 0, 0, 0) == (1500, 1500, 1000, 1500, 65535, 65535, 65535, 65535)
    assert frame.ardupilot_channels(1, 1, 1, 1)[:4] == (2000, 1000, 2000, 2000)
    assert frame.ardupilot_channels(-1, -1, -1, 0.5)[:4] == (1000, 2000, 1500, 1000)


def test_ardupilot_pitch_forward_is_low_pwm():
    assert frame.ardupilot_channels(0, 0.5, 0, 0)[1] == 1250


def test_px4_mapping_has_no_inverted_pitch():
    # Pitch forward is +x; the old demo negated x, which must not come back.
    assert frame.px4_manual(0, 0.5, 0, 0) == (500, 0, 0, 0)
    assert frame.px4_manual(0.25, 0, -0.5, 0.75) == (0, 250, 750, -500)


def test_signs_apply_after_frame():
    signs = {"roll": -1, "pitch": -1, "yaw": 1}
    assert frame.ardupilot_channels(0.5, 0.5, 0.5, 0, signs)[:4] == (1250, 1750, 1000, 1750)
    assert frame.px4_manual(0.5, 0.5, 0.5, 0, signs)[:4] == (-500, -500, 0, 500)


def test_clamping_and_rounding():
    assert frame.ardupilot_channels(2, -3, 5, 4)[:4] == (2000, 2000, 2000, 2000)
    assert frame.px4_manual(0.0004, 0.0006, 0, 2) == (1, 0, 1000, 0)


def test_release_frame_is_all_zero():
    assert frame.ARDUPILOT_RELEASE == (0,) * 8


def test_inverse_round_trip():
    ch = frame.ardupilot_channels(0.3, -0.4, 0.2, 0.7)
    roll, pitch, yaw, thr = frame.sticks_from_ardupilot(ch)
    assert (round(roll, 3), round(pitch, 3), round(yaw, 3), round(thr, 3)) == (0.3, -0.4, 0.2, 0.7)
    x, y, z, r = frame.px4_manual(0.3, -0.4, 0.2, 0.7)
    assert frame.sticks_from_px4(x, y, z, r) == (0.3, -0.4, 0.2, 0.7)
