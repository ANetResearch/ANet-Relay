"""Claim a simulator, arm, take off to 1.2 m, hover 8 s, land and release the claim.

python sdk/python/examples/hover_sim.py [--vehicle sim-1]
"""

from __future__ import annotations

import time

from _common import connect, ensure_sims, log, parser, vehicle_view


def main() -> None:
    p = parser(__doc__)
    p.add_argument("--vehicle", default="sim-1")
    p.add_argument("--alt", type=float, default=1.2)
    p.add_argument("--hover-s", type=float, default=8.0)
    args = p.parse_args()

    relay = connect(args, "hover-sim")
    vid = args.vehicle
    try:
        ensure_sims(relay, [vid])
        relay.claim(vid)
        log(f"claimed {vid}")
        relay.arm(vid)
        log("armed")
        done = relay.takeoff(vid, alt=args.alt)
        log(f"takeoff finished: {done['detail'] or 'ok'}")
        relay.hold(vid)
        end = time.monotonic() + args.hover_s
        while time.monotonic() < end:
            v = vehicle_view(relay, vid)
            volts = v["battery"]["voltage"]
            log(f"hovering at {v['altitude_m']:.2f} m, mode {v['mode']}, battery {volts:.2f} V")
            time.sleep(2.0)
        done = relay.land(vid)
        log(f"landed: {done['detail'] or 'ok'}")
        relay.unclaim(vid)
        log(f"released {vid}")
    finally:
        relay.close()


if __name__ == "__main__":
    main()
