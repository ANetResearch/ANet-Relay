"""Fly a training course ring by ring with goto and print the result.

For every ring the script flies to a point 0.35 m before the ring plane and then 0.35 m beyond it,
both on the ring normal, so the aircraft crosses the plane through the ring centre.

    python sdk/python/examples/course_sim.py [--vehicle sim-1] [--course cage-loop]
"""

from __future__ import annotations

import math

from _common import connect, ensure_sims, log, parser, vehicle_view

OFFSET_M = 0.35
MARGIN_M = 0.1


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def main() -> None:
    p = parser(__doc__)
    p.add_argument("--vehicle", default="sim-1")
    p.add_argument("--course", default="cage-loop")
    args = p.parse_args()

    relay = connect(args, "course-sim")
    vid = args.vehicle
    try:
        ensure_sims(relay, [vid])
        relay.claim(vid)
        started = relay.course_start(vid, args.course)["course"]
        rings = started["rings"]
        ox, oy, oz = started["origin"][:3]
        volume = relay.fleet()["safety_volume"]
        log(f"course {started['name_en']} ({len(rings)} rings) placed at origin ({ox:.2f}, {oy:.2f})")

        def inside(x: float, y: float, z: float) -> tuple[float, float, float]:
            hx, hy = volume["x"] / 2 - MARGIN_M, volume["y"] / 2 - MARGIN_M
            return (
                clamp(x, ox - hx, ox + hx),
                clamp(y, oy - hy, oy + hy),
                clamp(z, oz + 0.3, oz + volume["z"] - MARGIN_M),
            )

        relay.takeoff(vid, alt=rings[0]["c"][2] - oz)
        log("airborne; course clock running")
        for index, ring in enumerate(rings, start=1):
            cx, cy, cz = ring["c"]
            nx, ny = math.sin(math.radians(ring["yaw_deg"])), math.cos(math.radians(ring["yaw_deg"]))
            entry = inside(cx - OFFSET_M * nx, cy - OFFSET_M * ny, cz)
            exit_ = inside(cx + OFFSET_M * nx, cy + OFFSET_M * ny, cz)
            relay.goto(vid, *entry, yaw=ring["yaw_deg"])
            relay.goto(vid, *exit_, yaw=ring["yaw_deg"])
            course = vehicle_view(relay, vid)["course"]
            log(f"ring {index}/{len(rings)}: course shows {course['next']}/{course['total']} passed")

        course = vehicle_view(relay, vid)["course"]
        if course["state"] == "finished":
            splits = ", ".join(f"{s:.2f}" for s in course["splits"])
            best = f"{course['best_s']:.2f} s" if course["best_s"] is not None else "n/a"
            log(f"course finished in {course['elapsed_s']:.2f} s (splits {splits}; best {best})")
        else:
            log(f"course not finished: state {course['state']}, {course['next']}/{course['total']} rings")
        relay.land(vid)
        relay.unclaim(vid)
        log(f"landed and released {vid}")
        if course["state"] != "finished":
            raise SystemExit(1)
    finally:
        relay.close()


if __name__ == "__main__":
    main()
