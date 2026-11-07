"""Fly circles on sim-1 with goto; pause when a person possesses it, resume after the hand-back.

While the operator possesses the aircraft the claim is suspended and every control call is refused
(``possessed_by_human`` / ``claim_suspended``). The script waits for the ``relay_handback`` event and
continues the circle from wherever the aircraft is at that moment (taking off again if it landed).

    python sdk/python/examples/watch_handback.py [--laps 2]

``--demo-possess A,B`` (needs the operator token) possesses the aircraft A seconds after the first lap
starts and releases it B seconds later, to exercise the hand-back without the desktop app.
"""

from __future__ import annotations

import math
import threading
import time

from _common import RelayClient, RelayError, connect, ensure_sims, log, parser, position, vehicle_view

PAUSED = {"possessed_by_human", "claim_suspended", "skill_failed", "not_possessed", "claimed_by_other"}


def demo_possess(base: str, token: str, vid: str, after_s: float, hold_s: float) -> None:
    operator = RelayClient(base, operator_token=token)
    time.sleep(after_s)
    operator.possess(vid, "screen")
    log("demo operator: possessed the aircraft")
    time.sleep(hold_s)
    operator.release(vid)
    log("demo operator: released the aircraft")


def wait_handback(relay: RelayClient, vid: str, after: int) -> None:
    log("paused: a person is flying the aircraft; waiting for the hand-back")
    event = relay.wait_event("relay_handback", vehicle=vid, after=after, timeout=600)
    log(f"hand-back received: {event['text_en']}")


def main() -> None:
    p = parser(__doc__)
    p.add_argument("--vehicle", default="sim-1")
    p.add_argument("--laps", type=int, default=2)
    p.add_argument("--radius", type=float, default=0.8)
    p.add_argument("--alt", type=float, default=1.2)
    p.add_argument("--points", type=int, default=8)
    p.add_argument("--demo-possess", default=None, help="A,B seconds: possess after A, release after B more")
    args = p.parse_args()

    relay = connect(args, "watch-handback")
    vid = args.vehicle
    try:
        ensure_sims(relay, [vid])
        relay.claim(vid)
        log(f"claimed {vid}")
        view = vehicle_view(relay, vid)
        origin = relay.vehicle(vid)["detail"].get("safety_origin") or position(view)
        cx, cy = origin[0], origin[1]  # circle around the centre of the safety volume
        volume = relay.fleet()["safety_volume"]
        radius = min(args.radius, volume["x"] / 2 - 0.2, volume["y"] / 2 - 0.2)
        if view["landed"] != "air":
            relay.takeoff(vid, alt=args.alt)
            log(f"took off to {args.alt} m")
        if args.demo_possess:
            if not args.operator_token:
                raise SystemExit("--demo-possess needs --operator-token")
            a, b = (float(s) for s in args.demo_possess.split(","))
            threading.Thread(
                target=demo_possess, args=(args.base, args.operator_token, vid, a, b), daemon=True
            ).start()

        total = args.laps * args.points
        step = 2 * math.pi / args.points
        angle = 0.0
        done = 0
        pauses = 0
        while done < total:
            seq = relay.last_seq()
            target = (cx + radius * math.sin(angle), cy + radius * math.cos(angle), args.alt)
            try:
                relay.goto(vid, *target)
            except RelayError as exc:
                if exc.code not in PAUSED:
                    raise
                pauses += 1
                wait_handback(relay, vid, seq)
                view = vehicle_view(relay, vid)
                x, y, _ = position(view)
                if view["landed"] != "air":
                    relay.takeoff(vid, alt=args.alt)
                    log("resumed: took off again")
                angle = math.atan2(x - cx, y - cy)  # continue the circle from the current bearing
                log(f"resumed from ({x:.2f}, {y:.2f})")
                continue
            done += 1
            angle += step
            log(f"waypoint {done}/{total} reached at ({target[0]:.2f}, {target[1]:.2f}, {target[2]:.2f})")
        relay.land(vid)
        relay.unclaim(vid)
        log(f"finished {args.laps} laps with {pauses} hand-back pause(s); landed and released {vid}")
    finally:
        relay.close()


if __name__ == "__main__":
    main()
