"""Write the shared arena test vectors (05 §7.2, §7.4) from the Python reference implementation.

    python backend/tools/arena_vectors.py [--check]

``docs/design/vectors/arena.json`` covers the seat frames, ``ballistics.json`` the bullet integrator, the
muzzle velocity and the swept hit test. backend/tests/test_peers.py and web/src/arena/*.test.ts both read
them, so the Python and TypeScript implementations cannot drift apart. ``--check`` exits 1 when a file
differs.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from relay.peers import arena, ballistics  # noqa: E402

OUT = ROOT / "docs" / "design" / "vectors"


def _r(values, digits: int = 12):
    if isinstance(values, (int, float)):
        return round(float(values), digits)
    return [round(float(x), digits) for x in values]


def arena_vectors() -> dict:
    seats = [
        {"seat": k, "phi": _r(arena.seat_angle(k)), "origin": _r(arena.seat_origin(k))}
        for k in range(arena.SEATS)
    ]
    points = [(0.0, 0.0, 0.0), (2.0, -2.0, 1.5), (-1.25, 0.75, 0.4), (4.0, 0.0, 3.0)]
    vectors = [(1.0, 0.0, 0.0), (0.3, -2.0, 0.5)]
    headings = [0.0, 1.0, -2.5]
    pairs = [(0, 0), (0, 6), (3, 9), (11, 1), (5, 2), (7, 7)]
    cases = []
    for i, (sp, sm) in enumerate(pairs):
        p = points[i % len(points)]
        v = vectors[i % len(vectors)]
        h = headings[i % len(headings)]
        cases.append(
            {
                "seat_peer": sp,
                "seat_me": sm,
                "p_peer": _r(p),
                "p_local": _r(arena.peer_to_local(p, sp, sm)),
                "p_arena": _r(arena.to_arena(sp, p)),
                "v_peer": _r(v),
                "v_local": _r(arena.peer_vec_to_local(v, sp, sm)),
                "heading_peer": _r(h),
                "heading_local": _r(arena.peer_heading_to_local(h, sp, sm)),
            }
        )
    free = [
        {"taken": t, "after": a, "seat": arena.free_seat(t, a)}
        for t, a in (([], -1), ([0, 1, 3], -1), ([0, 1, 2], 1), (list(range(12)), -1), ([11, 0], 10))
    ]
    return {"radius": arena.RADIUS, "seats": seats, "cases": cases, "free_seat": free}


def ballistics_vectors() -> dict:
    starts = [
        ((0.0, 0.0, 1.0), (0.0, 35.0, 0.0)),
        ((1.5, -2.0, 2.0), (20.0, 25.0, 8.0)),
        ((-3.0, 4.0, 0.5), (-10.0, -30.0, -2.0)),
    ]
    cases = []
    for p, v in starts:
        samples = []
        for n in (1, 2, 10, 60, 240, 600):
            pp, vv = ballistics.advance(p, v, n)
            samples.append({"steps": n, "p": _r(pp), "v": _r(vv)})
        cases.append({"p0": _r(p), "v0": _r(v), "samples": samples})
    muzzle = []
    for heading, pitch, av in (
        (0.0, 0.0, (0, 0, 0)),
        (math.pi / 2, -0.2, (1.0, 0.5, 0.0)),
        (-2.0, 0.35, (0, -2, 1)),
    ):
        muzzle.append(
            {
                "heading": _r(heading),
                "pitch": _r(pitch),
                "aircraft_v": _r(av),
                "v": _r(ballistics.muzzle(heading, pitch, av)),
            }
        )
    sweeps = []
    for p0, p1, c0, c1 in (
        ((0, -1, 1), (0, 1, 1), (0, 0, 1), (0, 0, 1)),
        ((0, -1, 1), (0, 1, 1), (0.5, 0, 1), (0.5, 0, 1)),
        ((0, -1, 1), (0, -0.5, 1), (0, 0, 1), (0, 0, 1)),
        ((0, 0, 1), (0, 0.3, 1), (0.2, 0.2, 1), (0.0, 0.2, 1)),
        ((0.05, 0.0, 1), (0.05, 0.1, 1), (0, 0, 1), (0, 0, 1)),
    ):
        t = ballistics.sweep(p0, p1, c0, c1, ballistics.HIT_RADIUS)
        sweeps.append(
            {"p0": _r(p0), "p1": _r(p1), "c0": _r(c0), "c1": _r(c1), "t": None if t is None else _r(t)}
        )
    return {
        "constants": {
            "dt": ballistics.DT,
            "g": ballistics.G,
            "k": ballistics.K,
            "muzzle": ballistics.MUZZLE,
            "ttl_steps": ballistics.TTL_STEPS,
            "hit_radius": ballistics.HIT_RADIUS,
        },
        "cases": cases,
        "muzzle": muzzle,
        "sweep": sweeps,
    }


def files() -> dict[Path, str]:
    return {
        OUT / "arena.json": json.dumps(arena_vectors(), indent=1) + "\n",
        OUT / "ballistics.json": json.dumps(ballistics_vectors(), indent=1) + "\n",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for path, text in files().items():
        if args.check:
            if not path.exists() or path.read_text("utf-8") != text:
                stale.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, "utf-8")
        print(f"wrote {path}")
    for path in stale:
        print(f"{path} is out of date; run backend/tools/arena_vectors.py", file=sys.stderr)
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
