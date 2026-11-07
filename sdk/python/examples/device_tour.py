"""Tour of the device-level interface on a simulator: overview, command catalogue, motor test, attitude.

python sdk/python/examples/device_tour.py [--vehicle sim-1]
"""

from __future__ import annotations

from _common import connect, ensure_sims, log, parser


def main() -> None:
    p = parser(__doc__)
    p.add_argument("--vehicle", default="sim-1")
    args = p.parse_args()

    relay = connect(args, "device-tour")
    vid = args.vehicle
    try:
        ensure_sims(relay, [vid])
        relay.claim(vid)

        device = relay.device(vid)
        motors = device["motors"]
        log(f"{device['frame']['name']}: {motors['count']} motors ({motors['layout']})")
        log(f"parameters {device['params']['loaded']}/{device['params']['total']}")

        catalogue = relay.commands()
        log(f"{len(catalogue)} raw commands, e.g. " + ", ".join(c["name"] for c in catalogue[:4]))

        # Motor test needs a disarmed aircraft; simulators need no confirm word.
        for motor in (1, 2):
            relay.motor_test(vid, motor, throttle_pct=15, seconds=0.6)
            outputs = relay.device(vid)["motors"]["outputs_us"]
            log(f"motor {motor} test: outputs {outputs} us")
        relay.motors_stop(vid)

        relay.arm(vid)
        relay.takeoff(vid, alt=1.2)
        before = relay.vehicle(vid)
        relay.attitude(vid, roll_deg=10, pitch_deg=0, thrust=0.55, seconds=1.5)
        after = relay.vehicle(vid)
        log(f"attitude skill finished, mode {before['mode']} -> {after['mode']}")
        relay.land(vid)
        relay.unclaim(vid)
    finally:
        relay.close()


if __name__ == "__main__":
    main()
