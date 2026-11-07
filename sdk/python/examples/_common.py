"""Shared helpers for the SDK examples: argument parsing, client setup and log lines."""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anet_relay_client import DEFAULT_BASE, RelayClient, RelayError

__all__ = ["RelayClient", "RelayError", "connect", "ensure_sims", "log", "parser", "position", "vehicle_view"]

_T0 = time.monotonic()


def parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--base", default=DEFAULT_BASE, help="Relay API base URL (default %(default)s)")
    p.add_argument("--name", default=None, help="agent session name")
    p.add_argument(
        "--operator-token",
        default=os.environ.get("RELAY_OPERATOR_TOKEN"),
        help="operator token, only needed to add missing simulators (default: $RELAY_OPERATOR_TOKEN)",
    )
    return p


def log(message: str) -> None:
    print(f"[{time.monotonic() - _T0:6.1f}s] {message}", flush=True)


def connect(args: argparse.Namespace, default_name: str) -> RelayClient:
    client = RelayClient(args.base, operator_token=args.operator_token)
    health = client.health()
    client.open_session(args.name or default_name)
    log(f"connected to Relay {health['version']} as session {client.session_name} ({client.session_id})")
    return client


def ensure_sims(client: RelayClient, ids: list[str]) -> None:
    """Make sure the simulators exist; add the missing ones when an operator token is available."""
    present = {v["id"] for v in client.vehicles()}
    missing = [vid for vid in ids if vid not in present]
    if not missing:
        return
    if not client.operator_token:
        raise SystemExit(
            f"Missing simulators: {', '.join(missing)}. Add them in the Relay app (Add sim) "
            "or pass --operator-token."
        )
    added = client.add_sims(len(missing))
    log(f"added simulators {', '.join(added)}")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        ready = {
            v["id"] for v in client.vehicles() if v["link"]["state"] == "ok" and v["capabilities"]["position"]
        }
        if all(vid in ready for vid in ids):
            return
        time.sleep(0.2)
    raise SystemExit("Simulators did not come up within 10 s.")


def vehicle_view(client: RelayClient, vid: str) -> dict:
    return next(v for v in client.vehicles() if v["id"] == vid)


def position(view: dict) -> tuple[float, float, float]:
    p = view["position"]
    return (p["x"], p["y"], p["z"])


def heading_to(origin: tuple[float, float], target: tuple[float, float]) -> float:
    """Compass heading (deg, 0 north, clockwise) from origin to target."""
    return math.degrees(math.atan2(target[0] - origin[0], target[1] - origin[1])) % 360.0
