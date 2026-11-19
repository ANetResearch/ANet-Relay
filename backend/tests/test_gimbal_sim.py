"""The simulated gimbal pod (12 §5.4): dynamics, modes, zoom and lenses, the laser, tracking, captures."""

from __future__ import annotations

import math

import pytest

from relay.errors import RelayError
from relay.gimbal import Carrier, SimPod
from relay.gimbal import carrier as geo
from relay.gimbal.lens import LENS_TABLE, fov_deg
from relay.gimbal.sim import SimGimbal

DT = 0.01
VIEW_KEYS = {
    "kind", "link", "address", "yaw_deg", "pitch_deg", "roll_deg", "yaw_dps", "pitch_dps", "mode", "lens",
    "pip", "zoom", "zoom_range", "digital_zoom", "fov_deg", "palette", "tracking", "laser", "recording",
    "photos", "motors", "enhance", "osd", "low_light", "selftest", "target", "focus", "debug", "age_s",
}  # fmt: skip


class Rig:
    """A pod, a carrier and a clock."""

    def __init__(self, carrier: Carrier | None = None, **options) -> None:
        self.carrier = carrier or Carrier(pos=(0.0, 0.0, 10.0), lat=47.0, lon=8.0, alt=500.0, wall=1.8e9)
        self.pod = SimPod(**options)
        self.sim = self.pod.sim
        self.t = 0.0
        self.pod.step(self.t, self.carrier)

    def cmd(self, action: str, **params) -> None:
        self.pod.command(action, params, self.t)

    def run(self, seconds: float, yaw_rate_dps: float = 0.0) -> None:
        for _ in range(round(seconds / DT)):
            self.t += DT
            self.carrier.yaw += math.radians(yaw_rate_dps) * DT
            self.pod.step(self.t, self.carrier)

    def view(self) -> dict:
        return self.pod.view(self.t)


def test_view_has_exactly_the_documented_fields():
    rig = Rig()
    v = rig.view()
    assert set(v) == VIEW_KEYS
    assert v["kind"] == "sim" and v["link"] == "ok" and v["address"] is None and v["debug"] is None
    assert (v["yaw_deg"], v["pitch_deg"], v["mode"], v["lens"], v["pip"], v["zoom"]) == (
        0.0, -30.0, "follow", "visible1", None, 1.0,
    )  # fmt: skip
    assert v["zoom_range"] == [1.0, 30.0] and v["fov_deg"] == [60.0, 35.98]
    assert v["palette"] == "white_hot" and v["tracking"] == {
        "state": "off", "algorithm": "auto", "x_deg": 0.0, "y_deg": 0.0, "box_px": [0, 0],
    }  # fmt: skip
    assert v["laser"] == {"on": False, "range_m": None} and v["selftest"]["done"] is True
    assert (v["recording"], v["photos"], v["motors"], v["osd"], v["target"]) == (False, 0, True, True, None)


def test_rate_response_is_first_order_with_a_100_dps_limit():
    rig = Rig()
    rig.cmd("rate", yaw_dps=50.0, pitch_dps=0.0)
    rig.run(0.08)  # one time constant: 63% of the commanded rate
    assert rig.view()["yaw_dps"] == pytest.approx(50 * (1 - math.exp(-1)), abs=1.5)
    rig.run(0.92)
    assert rig.view()["yaw_dps"] == pytest.approx(50.0, abs=0.1)
    assert rig.view()["yaw_deg"] == pytest.approx(50 * (1.0 - 0.08 * (1 - math.exp(-1 / 0.08))), abs=1.0)
    assert rig.view()["mode"] == "rate"
    rig.cmd("rate", yaw_dps=100.0, pitch_dps=-100.0)
    rig.run(1.0)
    assert rig.view()["yaw_dps"] == pytest.approx(100.0, abs=0.1)
    rig.cmd("stop")
    rig.run(2.0)
    assert abs(rig.view()["yaw_dps"]) < 0.1 and rig.view()["mode"] == "follow"
    stopped = rig.view()["yaw_deg"]
    rig.run(1.0)
    assert rig.view()["yaw_deg"] == pytest.approx(stopped, abs=0.05)


def test_angle_command_settles_and_reports_angle_mode():
    rig = Rig()
    rig.cmd("angle", yaw_deg=90.0, pitch_deg=-45.0)
    rig.run(0.3)
    assert 5 < rig.view()["yaw_deg"] < 85 and rig.view()["mode"] == "angle"
    rig.run(2.2)
    v = rig.view()
    assert v["yaw_deg"] == pytest.approx(90.0, abs=0.2) and v["pitch_deg"] == pytest.approx(-45.0, abs=0.2)
    assert abs(v["yaw_dps"]) < 0.5 and v["mode"] == "angle"


def test_pitch_limits_and_continuous_azimuth():
    rig = Rig()
    rig.cmd("rate", yaw_dps=0.0, pitch_dps=100.0)
    rig.run(4.0)
    assert rig.view()["pitch_deg"] == 90.0 and rig.view()["pitch_dps"] <= 0.0
    rig.cmd("rate", yaw_dps=0.0, pitch_dps=-100.0)
    rig.run(4.0)
    assert rig.view()["pitch_deg"] == -120.0
    rig.cmd("angle", yaw_deg=170.0, pitch_deg=0.0)
    rig.run(3.0)
    rig.cmd("angle", yaw_deg=-170.0, pitch_deg=0.0)
    seen = []
    for _ in range(300):
        rig.run(0.01)
        seen.append(abs(rig.sim.yaw))
    assert min(seen) > 165.0  # 20 degrees through +-180, not 340 degrees through north
    assert rig.view()["yaw_deg"] == pytest.approx(-170.0, abs=0.3)
    rig.cmd("rate", yaw_dps=100.0, pitch_dps=0.0)
    rig.run(5.0)
    assert -180.0 <= rig.sim.yaw <= 180.0  # the azimuth wraps, it never stops


def test_follow_turns_with_the_aircraft_lock_holds_the_line_of_sight():
    def run(mode: str):
        rig = Rig()
        rig.cmd("angle", yaw_deg=20.0, pitch_deg=-10.0)
        rig.run(2.0)
        rig.cmd(mode, on=True)
        assert rig.view()["mode"] == mode
        before = geo.ned_to_angles(geo.enu_to_ned(rig.sim.los()))
        rig.run(3.0, yaw_rate_dps=30.0)  # the aircraft turns 90 degrees
        after = geo.ned_to_angles(geo.enu_to_ned(rig.sim.los()))
        return rig.view(), before, after

    v, before, after = run("follow")
    assert v["yaw_deg"] == pytest.approx(20.0, abs=0.3)  # the frame angle stays: the pod turns with the head
    assert after[0] - before[0] == pytest.approx(90.0, abs=1.0)
    v, before, after = run("lock")
    assert after[0] == pytest.approx(before[0], abs=1.0) and after[1] == pytest.approx(before[1], abs=1.0)
    assert v["yaw_deg"] == pytest.approx(20.0 - 90.0, abs=1.5)  # the pod turned against the aircraft


def test_lock_off_and_follow_off_swap_the_hold_mode():
    rig = Rig()
    rig.cmd("lock", on=True)
    rig.cmd("lock", on=False)
    assert rig.view()["mode"] == "follow"
    rig.cmd("follow", on=False)
    assert rig.view()["mode"] == "lock"


def test_center_goes_forward_and_level_then_holds():
    rig = Rig()
    rig.cmd("angle", yaw_deg=60.0, pitch_deg=-60.0)
    rig.run(3.0)
    rig.cmd("center")
    rig.run(3.0)
    v = rig.view()
    assert (v["yaw_deg"], v["pitch_deg"]) == (pytest.approx(0.0, abs=0.4), pytest.approx(0.0, abs=0.4))
    assert v["mode"] == "follow"


def test_earth_angle_points_at_a_compass_direction_whatever_the_heading():
    rig = Rig()
    rig.carrier.yaw = math.radians(90.0)  # facing east
    rig.cmd("angle", yaw_deg=0.0, pitch_deg=-20.0, space="earth")  # north, 20 degrees down
    rig.run(3.0)
    az, el = geo.ned_to_angles(geo.enu_to_ned(rig.sim.los()))
    assert az == pytest.approx(0.0, abs=0.3) and el == pytest.approx(-20.0, abs=0.3)
    assert rig.view()["yaw_deg"] == pytest.approx(-90.0, abs=0.3)  # frame: left of the nose


def test_a_rolled_and_pitched_aircraft_is_compensated_in_lock():
    rig = Rig()
    rig.cmd("angle", yaw_deg=0.0, pitch_deg=-45.0, space="earth")
    rig.run(2.0)
    rig.carrier.roll = math.radians(15.0)
    rig.carrier.pitch = math.radians(10.0)
    rig.run(2.0)
    az, el = geo.ned_to_angles(geo.enu_to_ned(rig.sim.los()))
    assert az == pytest.approx(0.0, abs=0.5) and el == pytest.approx(-45.0, abs=0.5)


def test_scan_sweeps_both_sides_at_the_given_speed():
    rig = Rig()
    rig.cmd("scan", yaw_deg=40.0, pitch_deg=-20.0, speed_dps=20.0)
    lows, highs = [], []
    for _ in range(100):
        rig.run(0.1)
        (lows if rig.sim.yaw < 0 else highs).append(rig.sim.yaw)
    assert rig.view()["mode"] == "scan"
    assert max(highs) == pytest.approx(40.0, abs=3.0) and min(lows) == pytest.approx(-40.0, abs=3.0)
    assert rig.view()["pitch_deg"] == pytest.approx(-20.0, abs=0.5)
    rig.cmd("scan_stop")
    assert rig.view()["mode"] == "follow"


def test_motors_off_freezes_the_pod_and_refuses_motion():
    rig = Rig()
    rig.cmd("rate", yaw_dps=60.0, pitch_dps=0.0)
    rig.run(0.5)
    rig.cmd("motors", on=False)
    rig.run(0.5)
    v = rig.view()
    assert v["mode"] == "off" and v["motors"] is False and abs(v["yaw_dps"]) < 0.1
    frozen = v["yaw_deg"]
    for action, params in (
        ("rate", {"yaw_dps": 5}),
        ("angle", {"yaw_deg": 1, "pitch_deg": 1}),
        ("center", {}),
    ):
        with pytest.raises(RelayError) as exc:
            rig.cmd(action, **params)
        assert exc.value.code == "gimbal_busy" and exc.value.status == 409
    rig.cmd("photo")  # not a motion: still allowed
    rig.run(0.5)
    assert rig.view()["yaw_deg"] == frozen
    rig.cmd("motors", on=True)
    assert rig.view()["mode"] == "follow"


def test_self_test_blocks_motion_until_it_ends():
    rig = Rig(selftest_s=1.0)
    assert rig.view()["selftest"]["done"] is False
    with pytest.raises(RelayError) as exc:
        rig.cmd("rate", yaw_dps=5.0)
    assert exc.value.code == "gimbal_busy"
    rig.run(1.1)
    assert rig.view()["selftest"]["done"] is True
    rig.cmd("rate", yaw_dps=5.0)


def test_zoom_slews_two_times_per_second_and_per_lens():
    rig = Rig()
    rig.cmd("zoom", level=5.0)
    rig.run(1.0)
    assert rig.view()["zoom"] == pytest.approx(3.0, abs=0.05)
    rig.run(1.2)
    assert rig.view()["zoom"] == 5.0
    assert rig.view()["fov_deg"] == list(fov_deg("visible1", 5.0))
    rig.cmd("zoom_rate", rate=-50)  # half rate out: 1x per second
    rig.run(2.0)
    assert rig.view()["zoom"] == pytest.approx(3.0, abs=0.05)
    rig.cmd("zoom_rate", rate=100)
    rig.run(20.0)  # runs into the end of the range and stops there
    assert rig.view()["zoom"] == 30.0 and rig.sim.zoom_rate == 0.0
    rig.cmd("zoom_rate", rate=-100)
    rig.run(0.5)
    rig.cmd("zoom_rate", rate=0)
    held = rig.view()["zoom"]
    rig.run(1.0)
    assert rig.view()["zoom"] == held


def test_lens_switch_changes_the_field_of_view_by_the_table():
    rig = Rig()
    rig.cmd("zoom", level=5.0)
    rig.run(2.5)
    h5 = 2 * math.degrees(math.atan(math.tan(math.radians(30)) / 5))
    assert rig.view()["fov_deg"][0] == pytest.approx(h5, abs=0.01)
    expected = {"visible2": (90.0, [1.0, 1.0]), "ir1": (40.0, [1.0, 8.0]), "ir2": (24.0, [1.0, 1.0])}
    for lens, (hfov, zrange) in expected.items():
        rig.cmd("lens", lens=lens)
        v = rig.view()
        assert (
            v["lens"] == lens and v["fov_deg"][0] == hfov and v["zoom_range"] == zrange and v["zoom"] == 1.0
        )
        assert v["fov_deg"][1] == pytest.approx(
            2 * math.degrees(math.atan(math.tan(math.radians(hfov / 2)) * 9 / 16)), abs=0.01
        )
    rig.cmd("lens", lens="visible1", pip="ir1")
    v = rig.view()
    assert v["zoom"] == 5.0 and v["pip"] == "ir1"  # each lens keeps its own zoom
    rig.cmd("lens", lens="ir1", pip="ir1")
    assert rig.view()["pip"] is None  # a small picture equal to the big one is no picture in picture
    rig.cmd("zoom", level=8.0)
    rig.run(4.0)
    assert rig.view()["fov_deg"][0] == pytest.approx(
        2 * math.degrees(math.atan(math.tan(math.radians(20)) / 8)), abs=0.01
    )
    assert LENS_TABLE["ir1"].infrared and not LENS_TABLE["visible1"].infrared


def test_zoom_limits_per_lens():
    rig = Rig()
    with pytest.raises(RelayError) as exc:
        rig.cmd("zoom", level=31.0)
    assert exc.value.code == "invalid_request"
    rig.cmd("lens", lens="visible2")
    for action, params in (("zoom", {"level": 2.0}), ("zoom_rate", {"rate": 10})):
        with pytest.raises(RelayError) as exc:
            rig.cmd(action, **params)
        assert exc.value.code == "gimbal_unsupported"
    rig.cmd("zoom_rate", rate=0)
    rig.cmd("lens", lens="ir1")
    with pytest.raises(RelayError):
        rig.cmd("zoom", level=9.0)


def test_laser_range_is_the_line_of_sight_to_the_ground():
    rig = Rig()  # 10 m up, level
    rig.cmd("angle", yaw_deg=0.0, pitch_deg=-45.0)
    rig.cmd("laser", on=True)
    rig.run(2.0)
    v = rig.view()
    assert v["laser"] == {"on": True, "range_m": pytest.approx(10 * math.sqrt(2), abs=0.15)}
    # the target is on the ground 10 m ahead (north), with the aircraft's latitude plus 10 m
    t = v["target"]
    assert t["lat"] == pytest.approx(47.0 + math.degrees(10.0 / 6371000.0), abs=2e-6)
    assert t["lon"] == pytest.approx(8.0, abs=2e-6) and t["alt"] == pytest.approx(490.0, abs=0.05)
    rig.cmd("angle", yaw_deg=0.0, pitch_deg=-90.0)
    rig.run(2.5)
    assert rig.view()["laser"]["range_m"] == pytest.approx(10.0, abs=0.05)
    rig.cmd("angle", yaw_deg=0.0, pitch_deg=10.0)  # above the horizon: nothing to hit
    rig.run(2.5)
    assert rig.view()["laser"] == {"on": True, "range_m": None} and rig.view()["target"] is None
    rig.cmd("angle", yaw_deg=0.0, pitch_deg=-0.05, space="earth")  # nearly level: far, capped at 1200 m
    rig.carrier.pos = (0.0, 0.0, 100.0)
    rig.run(2.5)
    assert rig.view()["laser"]["range_m"] is None or rig.view()["laser"]["range_m"] <= 1200.0
    rig.cmd("laser", on=False)
    assert rig.view()["laser"] == {"on": False, "range_m": None}


def test_laser_hits_a_ring_before_the_ground():
    ring = {"c": (0.0, 20.0, 10.0), "yaw_deg": 0.0, "r": 1.0}  # faces north: its normal is (0, 1, 0)
    rig = Rig(Carrier(pos=(0.0, 0.0, 10.0), lat=47.0, lon=8.0, alt=500.0, rings=[ring]))
    rig.cmd("laser", on=True)
    rig.cmd("angle", yaw_deg=0.0, pitch_deg=0.0, space="earth")  # through the middle of the hole: ground only
    rig.run(1.5)
    assert rig.view()["laser"]["range_m"] is None
    rig.cmd(
        "angle", yaw_deg=0.0, pitch_deg=math.degrees(math.atan2(1.0, 20.0)), space="earth"
    )  # the top of it
    rig.run(2.0)
    assert rig.view()["laser"]["range_m"] == pytest.approx(math.hypot(20.0, 1.0), abs=0.1)


def test_laser_target_geometry_for_a_turned_and_climbing_aircraft():
    rig = Rig(Carrier(pos=(100.0, 50.0, 30.0), yaw=math.radians(90.0), lat=47.0, lon=8.0, alt=500.0))
    rig.cmd("angle", yaw_deg=0.0, pitch_deg=-45.0)  # facing east, 45 degrees down
    rig.cmd("laser", on=True)
    rig.run(2.0)
    t = rig.view()["target"]
    east = math.radians(t["lon"] - 8.0) * 6371000.0 * math.cos(math.radians(47.0))
    assert east == pytest.approx(30.0, abs=0.2) and t["lat"] == pytest.approx(47.0, abs=1e-6)
    assert t["alt"] == pytest.approx(470.0, abs=0.1)


def test_track_locks_a_ground_point_and_keeps_it_while_the_aircraft_moves():
    rig = Rig()
    rig.cmd("angle", yaw_deg=0.0, pitch_deg=-45.0)
    rig.run(2.0)
    rig.cmd("track", x=0, y=0, algorithm="person")
    v = rig.view()
    assert (
        v["mode"] == "track" and v["tracking"]["state"] == "locked" and v["tracking"]["algorithm"] == "person"
    )
    assert v["tracking"]["box_px"] == [96, 96]
    assert rig.sim.tgt[1] == pytest.approx(
        (0.0, 10.0, 0.0), abs=1e-3
    )  # the ground point at the picture centre
    for step in range(1, 11):  # the aircraft flies 3 m east and climbs, 0.5 s at a time
        rig.carrier.pos = (3.0 * step / 10, 0.0, 10.0 + step * 0.2)
        rig.run(0.5)
    rig.cmd("laser", on=True)
    rig.run(0.2)
    hit = rig.sim.laser_hit
    assert (
        hit[0] == pytest.approx(0.0, abs=0.15)
        and hit[1] == pytest.approx(10.0, abs=0.15)
        and hit[2] == pytest.approx(0.0, abs=0.01)
    )
    assert abs(rig.view()["tracking"]["x_deg"]) < 0.5 and abs(rig.view()["tracking"]["y_deg"]) < 0.5
    rig.cmd("track_stop")
    v = rig.view()
    assert v["mode"] == "follow" and v["tracking"]["state"] == "off" and v["tracking"]["box_px"] == [0, 0]


def test_track_pixel_to_the_right_turns_the_pod_right_and_to_the_top_turns_it_up():
    rig = Rig()
    rig.cmd("angle", yaw_deg=0.0, pitch_deg=-45.0)
    rig.run(2.0)
    rig.cmd("track", x=320, y=-180)  # right and above the centre
    rig.run(3.0)
    v = rig.view()
    assert v["yaw_deg"] > 5.0 and v["pitch_deg"] > -45.0 and v["tracking"]["state"] == "locked"


def test_track_above_the_horizon_only_searches():
    rig = Rig()
    rig.cmd("angle", yaw_deg=0.0, pitch_deg=20.0)
    rig.run(2.0)
    rig.cmd("track", x=0, y=0)
    assert rig.view()["tracking"]["state"] == "searching" and rig.view()["mode"] == "track"


def test_geo_track_a_given_point_and_the_view_centre():
    rig = Rig()
    lat = 47.0 + math.degrees(
        20.0 / 6371000.0
    )  # 20 m north of the aircraft, on the ground (alt 490 = 500 - 10)
    rig.cmd("geo_track", lat=lat, lon=8.0, alt=490.0)
    rig.run(3.0)
    v = rig.view()
    assert v["mode"] == "track" and v["tracking"]["state"] == "off"
    assert v["pitch_deg"] == pytest.approx(
        -math.degrees(math.atan2(10.0, 20.0)), abs=0.3
    )  # 10 m up, 20 m out
    rig.cmd("angle", yaw_deg=40.0, pitch_deg=-30.0)
    rig.run(2.0)
    rig.cmd("geo_track", mode="center")
    rig.carrier.pos = (5.0, 0.0, 10.0)
    rig.run(3.0)
    hit = geo.unit(rig.sim.los())
    t = -rig.carrier.pos[2] / hit[2]
    ground = (rig.carrier.pos[0] + hit[0] * t, rig.carrier.pos[1] + hit[1] * t)
    centre = rig.sim.tgt[1]
    assert ground == pytest.approx((centre[0], centre[1]), abs=0.15)
    rig.cmd("geo_track", mode="exit")
    assert rig.view()["mode"] == "follow"


def test_switches_and_remembered_settings():
    rig = Rig()
    for action, key in (
        ("enhance", "enhance"),
        ("osd", "osd"),
        ("low_light", "low_light"),
        ("digital_zoom", "digital_zoom"),
    ):
        rig.cmd(action, on=not rig.view()[key])
    v = rig.view()
    assert (v["enhance"], v["osd"], v["low_light"], v["digital_zoom"]) == (True, False, True, True)
    rig.cmd("palette", palette="pseudo")
    rig.cmd("focus", mode="manual", position=20000)
    v = rig.view()
    assert v["palette"] == "pseudo" and v["focus"] == {"mode": "manual", "position": 20000}
    rig.cmd("focus", mode="auto")
    assert rig.view()["focus"] == {"mode": "auto", "position": None}


def test_photos_and_recording_are_captured_with_the_aircraft_pose():
    rig = Rig(Carrier(pos=(1.0, 2.0, 3.0), yaw=math.radians(90.0), lat=47.0, lon=8.0, alt=500.0, wall=1.8e9))
    rig.cmd("angle", yaw_deg=15.0, pitch_deg=-25.0)
    rig.run(2.0)
    rig.cmd("laser", on=True)
    rig.run(0.5)
    rig.cmd("photo")
    rig.cmd("record", on=True)
    rig.cmd("record", on=True)  # no change, no entry
    rig.run(1.0)
    rig.cmd("record", on=False)
    rig.cmd("photo")
    assert rig.view()["photos"] == 2 and rig.view()["recording"] is False
    caps = list(rig.pod.captures)
    assert [c["kind"] for c in caps] == ["photo", "record_start", "record_stop", "photo"]
    assert [c["seq"] for c in caps] == [1, 2, 3, 4]
    first = caps[0]
    assert first["nav"]["position"] == [1.0, 2.0, 3.0] and first["nav"]["heading_deg"] == 90.0
    assert first["nav"]["yaw_deg"] == 0.0 and first["nav"]["lat"] == 47.0  # flu: east is 0 degrees
    assert first["gimbal"]["yaw_deg"] == pytest.approx(15.0, abs=0.1)
    assert first["gimbal"]["pitch_deg"] == pytest.approx(-25.0, abs=0.1)
    assert first["lens"] == "visible1" and first["zoom"] == 1.0 and first["t"] == 1.8e9
    assert (
        first["laser_range_m"] == pytest.approx(3 / math.sin(math.radians(25)), abs=0.2)
        and first["target"]["lon"] > 8.0
    )
    kinds = [k for k, _ in rig.pod.take_events()]
    assert kinds == ["capture", "record", "record", "capture"] and rig.pod.take_events() == []


def test_lens_event_and_nav_in_the_other_frame():
    rig = Rig(Carrier(pos=(1.0, 2.0, 3.0), yaw=math.radians(30.0), frame="frd"))
    rig.cmd("lens", lens="ir1")
    rig.cmd("photo")
    assert [k for k, _ in rig.pod.take_events()] == ["lens", "capture"]
    nav = rig.pod.captures[0]["nav"]
    assert nav["frame"] == "frd" and nav["position"] == [2.0, 1.0, -3.0] and nav["yaw_deg"] == 30.0


def test_validation_of_parameters():
    rig = Rig()
    bad = [
        ("rate", {"yaw_dps": 101.0}),
        ("rate", {"yaw_dps": "fast"}),
        ("angle", {"yaw_deg": 200.0, "pitch_deg": 0.0}),
        ("angle", {"yaw_deg": 0.0, "pitch_deg": 91.0}),
        ("angle", {"yaw_deg": 0.0}),
        ("angle", {"yaw_deg": 0.0, "pitch_deg": -100.0, "space": "earth"}),
        ("angle", {"yaw_deg": 0.0, "pitch_deg": 0.0, "space": "sky"}),
        ("follow", {}),
        ("lens", {"lens": "thermal"}),
        ("lens", {"lens": "ir1", "pip": "thermal"}),
        ("palette", {"palette": "rainbow"}),
        ("track", {"x": 0}),
        ("track", {"x": 900, "y": 0}),
        ("track", {"x": 0, "y": 0, "algorithm": "cat"}),
        ("focus", {"mode": "manual", "position": 100}),
        ("scan", {"yaw_deg": 30, "pitch_deg": 0, "speed_dps": 80}),
        ("geo_track", {"lat": 95.0, "lon": 0.0, "alt": 0.0}),
        ("geo_track", {}),
        ("look_at", {"x": 1, "y": 2, "z": 3}),
        ("dance", {}),
        ("rate", {"yaw_dps": float("nan")}),
    ]
    for action, params in bad:
        with pytest.raises(RelayError) as exc:
            rig.cmd(action, **params)
        assert exc.value.code == "invalid_request" and exc.value.status == 400, (action, params)


def test_the_pod_is_deterministic():
    def flight() -> tuple:
        rig = Rig()
        rig.cmd("lock", on=True)
        rig.cmd("angle", yaw_deg=30.0, pitch_deg=-20.0, space="earth")
        rig.cmd("zoom", level=4.0)
        rig.cmd("laser", on=True)
        rig.run(3.0, yaw_rate_dps=20.0)
        rig.cmd("track", x=100, y=50)
        rig.run(2.0, yaw_rate_dps=-10.0)
        return tuple(sorted((k, str(v)) for k, v in rig.view().items()))

    assert flight() == flight()


def test_long_steps_are_cut_and_stay_stable():
    sim = SimGimbal()
    c = Carrier(pos=(0.0, 0.0, 10.0))
    sim.step(0.0, c)
    sim.execute("angle", {"yaw_deg": 170.0, "pitch_deg": 80.0, "space": "frame"}, 0.0)
    t = 0.0
    for _ in range(100):
        t += 5.0  # a stalled tick: the pod never integrates more than 0.1 s of it
        sim.step(t, c)
        assert -180.0 <= sim.yaw <= 180.0 and -120.0 <= sim.pitch <= 90.0
    assert sim.yaw == pytest.approx(170.0, abs=1.0) and sim.pitch == pytest.approx(80.0, abs=1.0)
