"""The collision world (07 §2, WP-A): lockstep physics, swept contacts, events and observations."""

import math

import pytest

from relay.programs.obs import ObsBuilder
from relay.sim import collide, physics
from relay.sim.autopilot import SimAutopilot
from relay.sim.world import Body, ExternalBody, World

RING = {"c": [0.0, 0.0, 2.0], "yaw_deg": 0.0, "r": 0.45}


def flying(name: str, p, v) -> SimAutopilot:
    """A simulator in level flight at ``p`` with velocity ``v``: armed, rotors at hover, mid stick."""
    sim = SimAutopilot("ardupilot", name=name)
    sim.step(0.0)
    s = sim.quad.s
    s.p, s.v, s.on_ground = list(p), list(v), False
    s.omega = [physics.OMEGA_HOVER] * 4
    sim.armed = True
    sim.pilot = (0.0, 0.0, 0.0, 0.5)
    return sim


def run(bodies, rings=(), seconds=0.5, world=None, before=None):
    world = world or World()
    t, reports = 0.0, []
    while t < seconds - 1e-9:
        t += 0.005
        for b in bodies:
            b.sim.pilot_t = t  # the pilot keeps sending
        if before is not None:
            before(world, t)
        reports += world.step(bodies, list(rings), t)
        for b in bodies:
            b.sim.step(t)  # telemetry and supervision; the world already stepped the physics
    return world, reports


def pair(closing: float):
    a = flying("a", (-0.5, 0.0, 2.0), (closing / 2, 0.0, 0.0))
    b = flying("b", (0.5, 0.0, 2.0), (-closing / 2, 0.0, 0.0))
    return a, b, [Body("sim-1", "SIM-1", a), Body("sim-2", "SIM-2", b)]


def test_lockstep_matches_the_simulators_own_stepping():
    alone = flying("a", (0.0, 0.0, 2.0), (1.0, 0.0, 0.0))
    together = flying("a", (0.0, 0.0, 2.0), (1.0, 0.0, 0.0))
    t = 0.0
    for _ in range(100):
        t += 0.005
        alone.pilot_t = t
        alone.step(t)
    run([Body("sim-1", "SIM-1", together)], seconds=0.5)
    assert together.quad.s.p == alone.quad.s.p and together.t == alone.t  # bit-identical


def test_head_on_at_15_ms_never_tunnels():
    a, b, bodies = pair(15.0)
    _world, reports = run(bodies, seconds=0.3)
    (report,) = reports
    assert report["kind"] == "craft" and report["a"] == "sim-1" and report["b"] == "sim-2"
    assert report["v_rel"] > 14.0 and sorted(report["crashed"]) == ["sim-1", "sim-2"]
    assert a.crashed and b.crashed and a.quad.s.crash_code == "collision"
    assert a.quad.s.crash_reason == "collision with SIM-2" and b.quad.s.crash_reason == "collision with SIM-1"
    assert a.quad.s.p[0] < 0.0 < b.quad.s.p[0]  # they bounced back, never passed through each other
    assert abs(report["point"][0]) < 0.05  # the contact is in the middle


@pytest.mark.parametrize(("closing", "crash"), [(2.6, True), (1.6, False)])
def test_threshold_on_both_sides_of_ram_crash_v(closing, crash):
    a, b, bodies = pair(closing)
    _world, reports = run(bodies, seconds=0.8)
    (report,) = reports
    v_rel = report["v_rel"]
    assert (v_rel >= collide.RAM_CRASH_V) is crash
    assert a.crashed is crash and b.crashed is crash
    if not crash:
        # e = 0.4: they part at 40 % of the closing speed, level, and keep flying
        assert a.quad.s.v[0] < 0.0 < b.quad.s.v[0]
        assert a.quad.s.p[2] == pytest.approx(2.0, abs=0.05) and not a.quad.s.on_ground


def test_ring_clean_pass_graze_and_crash():
    def through(x: float, vy: float, seconds: float = 1.2):
        sim = flying("a", (x, -1.0, 2.0), (0.0, vy, 0.0))
        _w, reports = run([Body("sim-1", "SIM-1", sim)], [("ring:1", RING)], seconds)
        return sim, reports

    sim, reports = through(0.0, 3.0)
    assert reports == [] and sim.quad.s.p[1] > 0.5 and not sim.crashed  # through the middle
    sim, reports = through(0.33, 1.8)  # the sphere overlaps the tube slowly: a graze
    (report,) = reports
    assert report["kind"] == "ring" and report["b"] == "ring:1" and report["b_label"] == "ring 1"
    assert report["v_rel"] < collide.RAM_CRASH_V and report["crashed"] == [] and not sim.crashed
    sim, reports = through(0.45, 3.0)  # straight into the tube
    (report,) = reports
    assert report["v_rel"] >= collide.RAM_CRASH_V and report["crashed"] == ["sim-1"]
    assert sim.crashed and sim.quad.s.crash_reason == "collision with ring 1"
    assert sim.quad.s.p[1] < 0.0  # it never got through


def test_a_shot_down_aircraft_still_collides():
    below = flying("b", (0.0, 0.0, 1.0), (0.0, 0.0, 0.0))
    wreck = flying("a", (0.0, 0.0, 2.5), (0.0, 0.0, 0.0))
    wreck.shoot_down()
    _w, reports = run([Body("sim-1", "SIM-1", wreck), Body("sim-2", "SIM-2", below)], seconds=0.8)
    assert reports and reports[0]["kind"] == "craft"
    assert reports[0]["v_rel"] > collide.RAM_CRASH_V  # it fell over a metre before it hit
    assert below.crashed and below.quad.s.crash_reason == "collision with SIM-1"
    assert wreck.quad.s.crash_code == "shot_down"  # the first cause stands


def test_external_bodies_move_only_the_local_aircraft():
    sim = flying("a", (0.0, 0.0, 2.0), (1.5, 0.0, 0.0))
    seen = []
    world = World()
    world.on_external_contact = lambda key, vid, _p, v_rel, crashed: seen.append((key, vid, v_rel, crashed))

    def remote(w, t):  # a remote aircraft hovering 0.6 m east, refreshed every tick like the arena does
        at = (0.6, 0.0, 2.0)
        w.external["peer-a/sim-1"] = ExternalBody("peer-a/sim-1", at, at, (0.0, 0.0, 0.0), "Alpha / SIM-1")

    _w, reports = run([Body("sim-1", "SIM-1", sim)], world=world, before=remote, seconds=0.5)
    report = reports[0]
    assert report["b"] == "peer-a/sim-1" and report["b_label"] == "Alpha / SIM-1" and report["crashed"] == []
    assert seen and seen[0][0] == "peer-a/sim-1" and seen[0][1] == "sim-1" and seen[0][3] is False
    # an equal-mass half of the impulse: the remote terminal applies the other half to its own aircraft, so
    # here (where the remote one does not move) the local aircraft bumps again, ever more softly
    assert all(later["v_rel"] < report["v_rel"] for later in reports[1:])
    assert sim.quad.s.v[0] < 0.5 and sim.quad.s.p[0] < 0.6 - 0.29


def test_contacts_are_deterministic():
    def once():
        a, b, bodies = pair(1.8)
        b.quad.s.p[2] = 2.1  # off-centre: a tilt from the rim
        _w, reports = run(bodies, seconds=0.6)
        return reports, a.quad.s.p, a.quad.s.q, b.quad.s.rates

    assert once() == once()


# ---------------------------------------------------------------------------- on the fleet thread


def test_fleet_collision_events_crash_details_and_obs(svc, stepper):
    ids = svc.add_sims(2)
    stepper.run(0.5)
    v1, v2 = svc.vehicles[ids[0]], svc.vehicles[ids[1]]
    s1, s2 = v1.driver.sim, v2.driver.sim
    # sim-2 (spawned 2 m east) is shot down 1.5 m above sim-1, which rests on the ground
    ox = v2.frame_offset[0] - v1.frame_offset[0]
    s2.quad.s.p = [-ox, 0.0, 1.5]
    s2.quad.s.on_ground = False
    s2.shoot_down()
    events = []
    svc.events.add_listener(events.append)
    stepper.run(1.0)
    hits = [e for e in events if e["kind"] == "collision"]
    assert hits, [e["kind"] for e in events]
    data = hits[0]["data"]
    assert {data["a"], data["b"]} == {v1.id, v2.id} and data["kind"] == "craft"
    assert data["v_rel"] >= collide.RAM_CRASH_V and v1.id in data["crashed"]
    assert "collided with" in hits[0]["text_en"] and "相撞" in hits[0]["text_zh"]
    crashed = [e for e in events if e["kind"] == "crashed" and e["vehicle"] == v1.id]
    assert crashed and crashed[0]["data"]["reason_code"] == "collision"
    point = crashed[0]["data"]["point"]
    assert math.hypot(point[0] - v1.frame_offset[0], point[1]) < 0.2 and point[2] < 0.5
    assert "collided with SIM-2" in crashed[0]["text_en"] and "与SIM-2相撞" in crashed[0]["text_zh"]
    obs = ObsBuilder(1).build(svc, v1, svc.clock())
    assert obs["collision"] is not None and obs["collision"]["with"] == v2.id
    assert obs["collision"]["normal"][2] < -0.5  # pushed down, from above
    assert obs["collision"]["t_ago"] < 1.0
    stepper.run(1.2)
    assert ObsBuilder(1).build(svc, v1, svc.clock())["collision"] is None  # one second of memory
    assert s1.crashed and s2.crashed


def test_contacts_carry_the_closing_speed(svc, stepper):
    ids = svc.add_sims(2)
    stepper.run(0.5)
    v1, v2 = svc.vehicles[ids[0]], svc.vehicles[ids[1]]
    for v, vx in ((v1, 1.0), (v2, -1.0)):  # 2 m apart, closing at 2 m/s
        s = v.driver.sim.quad.s
        s.p, s.v, s.on_ground = [0.0, 0.0, 5.0], [vx, 0.0, 0.0], False
    stepper.run(0.15)
    (contact,) = ObsBuilder(1, arena=None).build(svc, v1, svc.clock())["contacts"]
    assert contact["closing"] == pytest.approx(2.0, abs=0.4)  # noisy, like rel and vrel
