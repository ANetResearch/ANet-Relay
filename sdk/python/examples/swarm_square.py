"""Four simulators: take off together, fly a synchronised square rotation, land together.

Each aircraft flies the corners of a square around its own spawn point. The four start on different
corners, so the formation turns as one. Every step waits until all four reached their corner.

    python sdk/python/examples/swarm_square.py [--laps 2]
"""

from __future__ import annotations

from _common import connect, ensure_sims, log, parser, vehicle_view

VEHICLES = ["sim-1", "sim-2", "sim-3", "sim-4"]
CORNERS = [(-1, -1), (1, -1), (1, 1), (-1, 1)]


def main() -> None:
    p = parser(__doc__)
    p.add_argument("--laps", type=int, default=2)
    p.add_argument("--half", type=float, default=0.5, help="half side of each square, m")
    p.add_argument("--alt", type=float, default=1.2)
    args = p.parse_args()

    relay = connect(args, "swarm-square")
    try:
        ensure_sims(relay, VEHICLES)
        centres = {}
        for vid in VEHICLES:
            relay.claim(vid)
            p_ = vehicle_view(relay, vid)["position"]
            centres[vid] = (p_["x"], p_["y"])
        log(f"claimed {', '.join(VEHICLES)}")

        started = {vid: relay.takeoff(vid, alt=args.alt, wait=False) for vid in VEHICLES}
        for vid, skill in started.items():
            relay.wait_skill(vid, skill["id"], timeout=40)
        log(f"all four airborne at {args.alt} m")

        for step in range(args.laps * len(CORNERS) + 1):
            pending = {}
            for i, vid in enumerate(VEHICLES):
                sx, sy = CORNERS[(i + step) % len(CORNERS)]
                cx, cy = centres[vid]
                pending[vid] = relay.goto(vid, cx + sx * args.half, cy + sy * args.half, args.alt, wait=False)
            for vid, skill in pending.items():
                relay.wait_skill(vid, skill["id"], timeout=30)
            log(f"formation step {step + 1}/{args.laps * len(CORNERS) + 1} complete")

        landing = {vid: relay.land(vid, wait=False) for vid in VEHICLES}
        for vid, skill in landing.items():
            relay.wait_skill(vid, skill["id"], timeout=90)
        for vid in VEHICLES:
            relay.unclaim(vid)
        log("all four landed; claims released")
    finally:
        relay.close()


if __name__ == "__main__":
    main()
