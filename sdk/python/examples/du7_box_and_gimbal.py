"""ANetDU7 box test, then the gimbal pod: point it, zoom, switch the lens, take a photo and read the record.

python sdk/python/examples/du7_box_and_gimbal.py [--vehicle sim-1]

Flies the built-in plan ``du7_box`` (take off to 2 m, 1 m forward and back, 1 m left and back, land, all at
0.5 m/s on speed-limited body velocity), then drives the gimbal pod of the aircraft. A new simulator is an
ANetDU7 with a simulated pod, so this runs without hardware; on a real ANetDU7 attach the pod first
(``gimbal_attach``, operator token) and pass --arm-real. Gimbal angles are the pod's own (yaw right +,
pitch up +, looking down is negative); they do not follow the control frame.
"""

from __future__ import annotations

import time

from _common import RelayError, connect, ensure_sims, log, parser


def main() -> None:
    p = parser(__doc__)
    p.add_argument("--vehicle", default="sim-1")
    p.add_argument("--arm-real", action="store_true", help="confirm arming a real aircraft")
    args = p.parse_args()

    relay = connect(args, "du7-box-and-gimbal")
    vid = args.vehicle
    try:
        if vid.startswith("sim-"):
            ensure_sims(relay, [vid])
        view = relay.vehicle(vid)
        frame = relay.capabilities()["control_frame"]
        log(f"{vid} is a {view['model']} ({view['autopilot']}); control frame {frame}")
        relay.claim(vid)

        box = next(b for b in relay.plans_builtin() if b["id"] == "du7_box")
        log(f"flying {box['id']}: {box['description']}")
        try:
            relay.plan(vid, builtin="du7_box", confirm="ARM" if args.arm_real else None, timeout=240.0)
        except RelayError as exc:
            log(f"box stopped: {exc.message}")
            raise SystemExit(1) from exc
        log("box done")

        pod = relay.gimbal(vid)
        log(f"pod {pod['kind']}, link {pod['link']}, {pod['lens']} {pod['zoom']}x, fov {pod['fov_deg']}")

        relay.gimbal_angle(vid, 0.0, -90.0)  # straight down
        time.sleep(1.5)
        relay.gimbal_lens(vid, "ir1", pip="visible2")  # infrared zoom lens, wide lens in the corner
        relay.gimbal_zoom(vid, 4.0)  # ir1 zooms 1-8x; visible1 1-30x; the other two lenses are fixed
        time.sleep(2.0)
        pod = relay.gimbal(vid)
        log(f"pod yaw {pod['yaw_deg']:.1f}, pitch {pod['pitch_deg']:.1f}, {pod['lens']} {pod['zoom']:.1f}x")

        relay.gimbal_look_at(vid, 1.0, 0.0, 0.0)  # a local point, in the control frame
        relay.gimbal_photo(vid)
        captures = relay.gimbal_captures(vid, limit=1)["captures"]
        log(f"capture record: {captures[-1] if captures else 'none'}")

        relay.unclaim(vid)
        log(f"released {vid}")
    finally:
        relay.close()


if __name__ == "__main__":
    main()
