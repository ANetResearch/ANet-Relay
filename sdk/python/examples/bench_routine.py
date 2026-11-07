"""Bench routine as one plan: take off, hover, forward 1 m, back 1 m, turn 45 degrees, land.

python sdk/python/examples/bench_routine.py [--vehicle sim-1]
python sdk/python/examples/bench_routine.py --vehicle real-10-7-7-36 --arm-real

Works with or without a position estimate. Without one, Relay flies the moves as timed tilts in altitude
hold, so the 1 m is an estimate; height and heading are measured. A real aircraft needs --arm-real (it
sends confirm="ARM"), passing preflight checks, and an operator at the Relay window who can take over.
"""

from __future__ import annotations

from _common import RelayError, connect, ensure_sims, log, parser

STEPS = [
    {"action": "takeoff", "alt": 1.0},
    {"action": "hover", "seconds": 3},
    {"action": "move", "forward": 1},
    {"action": "move", "forward": -1},
    {"action": "yaw", "deg": 45},
    {"action": "land"},
]


def main() -> None:
    p = parser(__doc__)
    p.add_argument("--vehicle", default="sim-1")
    p.add_argument("--alt", type=float, default=1.0)
    p.add_argument("--arm-real", action="store_true", help="confirm arming a real aircraft")
    args = p.parse_args()

    relay = connect(args, "bench-routine")
    vid = args.vehicle
    steps = [dict(s) for s in STEPS]
    steps[0]["alt"] = args.alt
    try:
        if vid.startswith("sim-"):
            ensure_sims(relay, [vid])
        relay.claim(vid)
        log(f"claimed {vid}; flying {len(steps)} steps")
        try:
            plan = relay.plan(vid, steps, name="bench routine", confirm="ARM" if args.arm_real else None)
        except RelayError as exc:
            log(f"plan stopped: {exc.message}")
            raise SystemExit(1) from exc
        for step, result in zip(plan["steps"], plan["results"], strict=False):
            log(f"{step['action']:8} {result or 'ok'}")
        relay.unclaim(vid)
        log(f"released {vid}")
    finally:
        relay.close()


if __name__ == "__main__":
    main()
