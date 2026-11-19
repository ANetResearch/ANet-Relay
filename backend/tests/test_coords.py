"""Control frame conventions (12 §3): pure conversions and the skill request normaliser."""

from __future__ import annotations

import math

import pytest

from relay import coords


def test_body_axes():
    assert coords.body_to_fru("flu", 1.0, 2.0, 3.0) == (1.0, -2.0, 3.0)  # left 2 -> right -2
    assert coords.body_to_fru("frd", 1.0, 2.0, 3.0) == (1.0, 2.0, -3.0)  # down 3 -> up -3
    assert coords.body_to_fru("relay", 1.0, 2.0, 3.0) == (1.0, 2.0, 3.0)
    for frame in ("flu", "frd"):
        assert coords.body_from_fru(frame, *coords.body_to_fru(frame, 0.4, -0.2, 0.7)) == pytest.approx(
            (0.4, -0.2, 0.7)
        )


def test_local_axes():
    assert coords.local_to_enu("flu", 1.0, 2.0, 3.0) == (1.0, 2.0, 3.0)
    assert coords.local_to_enu("frd", 1.0, 2.0, 3.0) == (2.0, 1.0, -3.0)  # north, east, down
    assert coords.local_from_enu("frd", 2.0, 1.0, -3.0) == (1.0, 2.0, 3.0)


def test_headings_and_rates():
    assert coords.heading_to_compass("flu", 0.0) == 90.0  # +x is east
    assert coords.heading_to_compass("flu", 90.0) == 0.0  # +y is north
    assert coords.heading_to_compass("flu", -90.0) == 180.0
    assert coords.heading_to_compass("frd", 370.0) == 10.0
    assert coords.heading_from_compass("flu", 0.0) == 90.0
    assert (
        coords.heading_from_compass("flu", 270.0) == -180.0
        or coords.heading_from_compass("flu", 270.0) == 180.0
    )
    assert coords.rate_to_clockwise("flu", 10.0) == -10.0
    assert coords.rate_to_clockwise("frd", 10.0) == 10.0


def test_rotation_follows_the_nose():
    # heading 0 (north): forward is north, right is east
    assert coords.fru_to_enu(0.0, 1.0, 0.0, 0.0) == pytest.approx((0.0, 1.0, 0.0))
    assert coords.fru_to_enu(0.0, 0.0, 1.0, 0.0) == pytest.approx((1.0, 0.0, 0.0))
    # heading 90 deg (east): forward is east, right is south
    assert coords.fru_to_enu(math.pi / 2, 1.0, 0.0, 0.0) == pytest.approx((1.0, 0.0, 0.0))
    assert coords.fru_to_enu(math.pi / 2, 0.0, 1.0, 0.0) == pytest.approx((0.0, -1.0, 0.0))
    for heading in (0.3, -2.0, 3.0):
        enu = coords.fru_to_enu(heading, 0.5, -0.25, 0.1)
        assert coords.enu_to_fru(heading, *enu) == pytest.approx((0.5, -0.25, 0.1))


def test_ros_body_velocity_matches_the_onboard_test_node():
    """u7_mavros_simple.body_velocity: ENU velocity and ENU yaw to body FLU."""
    for yaw_enu, east, north in ((0.4, 0.3, -0.2), (-2.1, -0.5, 0.1), (math.pi / 2, 0.0, 0.5)):
        c, s = math.cos(yaw_enu), math.sin(yaw_enu)
        ros_forward, ros_left = c * east + s * north, -s * east + c * north
        heading = math.pi / 2 - yaw_enu  # ENU yaw to NED heading
        forward, right, _ = coords.enu_to_fru(heading, east, north, 0.0)
        assert (forward, -right) == pytest.approx((ros_forward, ros_left))


def test_move_generic_and_named():
    n = coords.normalise_skill
    assert n("move", {"x": 1.0, "y": 0.5, "z": 0.2}) == {
        "forward": 1.0,
        "right": -0.5,
        "up": 0.2,
        "frame": "relay",
    }
    assert n("move", {"x": 1.0, "y": 0.5, "z": 0.2, "frame": "frd"})["right"] == 0.5
    assert n("move", {"x": 1.0, "y": 0.5, "z": 0.2, "frame": "frd"})["up"] == -0.2
    assert n("move", {"left": 1.0, "down": 0.3}, "frd") == {
        "forward": 0.0,
        "right": -1.0,
        "up": -0.3,
        "frame": "relay",
    }
    assert n("move", {"forward": 1.0, "right": 2.0}) == {
        "forward": 1.0,
        "right": 2.0,
        "up": 0.0,
        "frame": "relay",
    }
    for bad in ({"x": 1.0, "forward": 1.0}, {"left": 1.0, "right": 1.0}, {"up": 1.0, "down": 1.0}):
        with pytest.raises(coords.FrameError):
            n("move", bad)
    with pytest.raises(coords.FrameError):
        n("move", {"forward": 1.0, "frame": "ned"})
    with pytest.raises(coords.FrameError):
        n("move", {"forward": 1.0, "reference": "body"})


def test_velocity_references():
    n = coords.normalise_skill
    body = n("velocity", {"vx": 0.5, "vy": 0.2, "vz": 0.1, "yaw_rate_dps": 10.0, "seconds": 1})
    assert body["_body"] == [0.5, -0.2, 0.1] and body["reference"] == "body" and body["yaw_rate_dps"] == -10.0
    assert (body["vx"], body["vy"], body["vz"]) == (0.5, -0.2, 0.1) and body["frame"] == "relay"
    frd = n("velocity", {"vx": 0.5, "vy": 0.2, "vz": 0.1, "yaw_rate_dps": 10.0, "frame": "frd"})
    assert frd["_body"] == [0.5, 0.2, -0.1] and frd["yaw_rate_dps"] == 10.0
    local = n("velocity", {"vx": 0.5, "vy": 0.2, "vz": 0.1, "reference": "local", "frame": "frd"})
    assert "_body" not in local and (local["vx"], local["vy"], local["vz"]) == (0.2, 0.5, -0.1)
    # Relay's own axes: local by default, as the API before v0.6
    legacy = n("velocity", {"vx": 0.5, "vy": 0.2, "vz": 0.1, "yaw_rate_dps": 10.0, "frame": "relay"})
    assert "_body" not in legacy and legacy["yaw_rate_dps"] == 10.0 and legacy["reference"] == "local"


def test_goto_and_yaw():
    n = coords.normalise_skill
    assert n("goto", {"x": 1.0, "y": 2.0, "z": 3.0, "yaw": 0.0}) == {
        "x": 1.0,
        "y": 2.0,
        "z": 3.0,
        "yaw": 90.0,
        "frame": "relay",
    }
    assert n("goto", {"x": 1.0, "y": 2.0, "z": -3.0, "yaw": 45.0, "frame": "frd"}) == {
        "x": 2.0,
        "y": 1.0,
        "z": 3.0,
        "yaw": 45.0,
        "frame": "relay",
    }
    assert n("yaw", {"deg": 90.0})["deg"] == -90.0  # counter-clockwise in flu
    assert n("yaw", {"deg": 90.0}, "frd")["deg"] == 90.0
    with pytest.raises(coords.FrameError):
        n("goto", {"x": 1.0, "y": 2.0})


def test_normalised_parameters_round_trip():
    """What skill.params echo flies the same again under any setting (re-fly of a record)."""
    n = coords.normalise_skill
    for name, params in (
        ("move", {"x": 1.0, "y": 0.5, "z": 0.2}),
        ("goto", {"x": 1.0, "y": 2.0, "z": 3.0, "yaw": 30.0}),
        ("yaw", {"deg": 45.0}),
        ("velocity", {"vx": 0.5, "vy": 0.2, "vz": 0.1, "yaw_rate_dps": 10.0}),
        ("velocity", {"vx": 0.5, "vy": 0.2, "vz": 0.1, "reference": "local"}),
    ):
        first = n(name, params, "flu")
        public = {k: v for k, v in first.items() if not k.startswith("_")}
        for setting in ("flu", "frd"):
            assert n(name, public, setting) == first


def test_nav_view():
    heading = math.radians(90.0)  # nose east
    flu = coords.nav_view("flu", (1.0, 2.0, 3.0), (0.5, 0.0, 0.1), heading, math.radians(10.0))
    assert flu == {
        "frame": "flu",
        "position": [1.0, 2.0, 3.0],
        "velocity": [0.5, 0.0, 0.1],
        "body_velocity": [0.5, 0.0, 0.1],
        "yaw_deg": 0.0,
        "yaw_rate_dps": -10.0,
    }
    frd = coords.nav_view("frd", (1.0, 2.0, 3.0), (0.5, 0.0, 0.1), heading, math.radians(10.0))
    assert frd["position"] == [2.0, 1.0, -3.0] and frd["body_velocity"] == [0.5, 0.0, -0.1]
    assert frd["yaw_deg"] == 90.0 and frd["yaw_rate_dps"] == 10.0
    assert coords.nav_view("flu", None, None, None, None) is None
