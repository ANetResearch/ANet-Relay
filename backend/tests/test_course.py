"""Ring courses and pass judging (03 §8)."""

import math

import pytest

from relay import course

RING = {"c": [0.0, 0.0, 1.0], "yaw_deg": 0.0, "r": 0.4}  # normal points north (+y)


def test_forward_crossing_passes():
    assert course.crossing((0, -0.2, 1.0), (0, 0.2, 1.0), RING)[0]


def test_reverse_crossing_does_not_pass():
    assert not course.crossing((0, 0.2, 1.0), (0, -0.2, 1.0), RING)[0]


def test_grazing_outside_radius_does_not_pass():
    assert not course.crossing((0.41, -0.1, 1.0), (0.41, 0.1, 1.0), RING)[0]
    assert course.crossing((0.39, -0.1, 1.0), (0.39, 0.1, 1.0), RING)[0]


def test_diagonal_crossing_uses_intersection_point():
    # Segment from (-0.6, -0.3) to (0.6, 0.3) crosses y=0 at x=0: inside.
    assert course.crossing((-0.6, -0.3, 1.0), (0.6, 0.3, 1.0), RING)[0]
    # Same slope offset so the intersection lands at x=0.5: outside.
    assert not course.crossing((-0.1, -0.3, 1.0), (1.1, 0.3, 1.0), RING)[0]
    # Vertical offset counts too.
    assert not course.crossing((0, -0.2, 1.5), (0, 0.2, 1.5), RING)[0]


def test_tilted_ring_normal():
    ring = {"c": [1.0, 0.0, 1.0], "yaw_deg": 180.0, "r": 0.45}  # normal points south
    assert course.crossing((1.0, 0.2, 1.0), (1.0, -0.2, 1.0), ring)[0]
    assert not course.crossing((1.0, -0.2, 1.0), (1.0, 0.2, 1.0), ring)[0]


def _fly(run, points, t0=0.0):
    events = []
    for i, p in enumerate(points):
        events += run.update(p, t0 + i * 0.1, 1e9 + i * 0.1, airborne=p[2] > 0.3)
    return events


def test_run_counts_only_the_next_ring_and_finishes():
    cage = course.PRESETS["cage-loop"]
    run = course.CourseRun.begin(cage, (0.0, 0.0, 0.0, 0.0), best_s=None)
    assert run.state == "waiting_takeoff"
    # Take off, then pass ring 2 first (skipped ring: must not count).
    events = _fly(run, [(0, 0, 0), (0, 0, 0.5), (1.0, 0.2, 1.0), (1.0, -0.2, 1.0)])
    assert run.state == "running" and run.next == 0 and not events
    path = [
        (-0.3, 1.0, 1.0),
        (0.3, 1.0, 1.0),
        (1.0, 0.3, 1.0),
        (1.0, -0.3, 1.0),
        (0.3, -1.0, 1.0),
        (-0.3, -1.0, 1.0),
        (-1.0, -0.3, 1.0),
        (-1.0, 0.3, 1.0),
    ]
    events = _fly(run, path, t0=1.0)
    kinds = [k for k, _ in events]
    assert kinds == ["ring_passed"] * 4 + ["course_finished"]
    assert run.state == "finished" and len(run.splits) == 4
    finished = events[-1][1]
    assert finished["new_best"] and finished["total_s"] == pytest.approx(1.6, abs=0.25)


def test_abort_and_no_events_after():
    run = course.CourseRun.begin(course.PRESETS["slalom"], (0, 0, 0, 0), None)
    _fly(run, [(0, 0, 0), (0, 0, 0.6)])
    assert run.abort("disarmed", 5.0) == ("course_aborted", {"reason": "disarmed"})
    assert run.abort("again", 6.0) is None
    assert run.update((0, 0, 1), 7.0, 1e9, True) == []


def test_presets_shape():
    cage = course.PRESETS["cage-loop"]["rings"]
    assert [r["c"] for r in cage] == [[0, 1, 1], [1, 0, 1], [0, -1, 1], [-1, 0, 1]]
    assert [r["yaw_deg"] for r in cage] == [90, 180, 270, 0]
    slalom = course.PRESETS["slalom"]["rings"]
    assert len(slalom) == 6 and slalom[0]["c"][1] == -1.25 and slalom[-1]["c"][1] == 1.25
    assert {abs(r["c"][0]) for r in slalom} == {0.6}
    climb = course.PRESETS["climb"]["rings"]
    assert [r["c"][2] for r in climb] == [0.6, 0.85, 1.1, 1.35, 1.6]
    assert all(math.isclose(math.hypot(r["c"][0], r["c"][1]), 1.0, abs_tol=1e-3) for r in climb)


def test_scaling_is_uniform_with_radius_floor():
    small = course.scaled(course.PRESETS["cage-loop"], (1.5, 3.0, 2.0))
    assert small["scale"] == 0.5
    assert small["rings"][0]["c"] == [0.0, 0.5, 0.5]
    assert all(r["r"] == 0.3 for r in small["rings"])  # 0.45 * 0.5 would be 0.225
    big = course.scaled(course.PRESETS["cage-loop"], (6.0, 6.0, 4.0))
    assert big["rings"][1]["c"] == [2.0, 0.0, 2.0] and big["rings"][1]["r"] == 0.9


def test_transform_rotates_to_heading():
    rings = course.transform(course.PRESETS["cage-loop"]["rings"], (10.0, 5.0, 0.0, 90.0))
    # Course north now points east: ring 1 (0, 1) lands at (11, 5) with normal heading 180.
    assert rings[0]["c"] == [11.0, 5.0, 1.0]
    assert rings[0]["yaw_deg"] == 180.0


def test_custom_course_validation():
    ok = course.validate_custom("my-course", {"rings": [{"c": [0, 0, 1], "yaw_deg": 0, "r": 0.4}]}, (3, 3, 2))
    assert ok["preset"] is False
    with pytest.raises(PermissionError):
        course.validate_custom("cage-loop", {"rings": []}, (3, 3, 2))
    with pytest.raises(ValueError):
        course.validate_custom("x", {"rings": [{"c": [2, 0, 1], "r": 0.4}]}, (3, 3, 2))
    with pytest.raises(ValueError):
        course.validate_custom("x", {"rings": [{"c": [0, 0, 1], "r": 0.1}]}, (3, 3, 2))


# ---------------------------------------------------------------------------- solid rings (07 §2, WP-A)


def test_clean_pass_needs_the_whole_sphere_to_clear_the_tube():
    clear = RING["r"] - course.CRAFT_RADIUS - course.RING_TUBE  # 0.22 m for a 0.4 m ring
    passed, _f, clean, point = course.pass_check((clear - 0.01, -0.1, 1.0), (clear - 0.01, 0.1, 1.0), RING)
    assert passed and clean and point == pytest.approx((clear - 0.01, 0.0, 1.0))
    passed, _f, clean, _p = course.pass_check((0.35, -0.1, 1.0), (0.35, 0.1, 1.0), RING)
    assert passed and not clean  # the centre is inside, the sphere scrapes the tube: passed, touched
    passed, _f, clean, _p = course.pass_check((0.45, -0.1, 1.0), (0.45, 0.1, 1.0), RING)
    assert not passed and not clean


def test_a_touched_pass_counts_and_reports_ring_touch():
    run = course.CourseRun.begin(
        {"id": "t", "rings": [dict(RING), {**RING, "c": [0.0, 2.0, 1.0]}]}, (0, 0, 0, 0), None
    )
    run.update((0.0, -1.0, 0.5), 0.0, 0.0, True)
    events = run.update((0.35, -0.5, 1.0), 0.5, 0.5, True) + run.update((0.35, 0.5, 1.0), 1.0, 1.0, True)
    kinds = [k for k, _ in events]
    assert kinds == ["ring_passed", "ring_touch"]
    assert events[0][1]["clean"] is False and events[1][1]["ring"] == 1
    assert events[1][1]["point"] == pytest.approx([0.35, 0.0, 1.0])
    events = run.update((0.0, 1.5, 1.0), 1.5, 1.5, True) + run.update((0.0, 2.5, 1.0), 2.0, 2.0, True)
    assert [k for k, _ in events] == ["ring_passed", "course_finished"] and events[0][1]["clean"] is True
    assert run.touches == [1]
