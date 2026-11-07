"""Global topology (03 §10.4), a pure function of the fleet snapshot."""

from __future__ import annotations

import math
from typing import Any

EARTH_R = 6371000.0


def _frame_key(vehicle: dict[str, Any]) -> tuple[str, Any] | None:
    """Vehicles that share a key share a coordinate system."""
    if vehicle["kind"] == "sim" and vehicle.get("position"):
        return ("sim", None)
    gps = vehicle.get("gps") or {}
    if vehicle.get("global") and gps.get("fix", 0) >= 3:
        return ("global", None)
    return None


def _offset(a: dict[str, Any], b: dict[str, Any], key: tuple[str, Any]) -> tuple[float, float, float]:
    """(east, north, up) from a to b in metres."""
    if key[0] == "sim":
        pa, pb = a["position"], b["position"]
        return pb["x"] - pa["x"], pb["y"] - pa["y"], pb["z"] - pa["z"]
    ga, gb = a["global"], b["global"]
    lat0 = math.radians(ga["lat"])
    north = math.radians(gb["lat"] - ga["lat"]) * EARTH_R
    east = math.radians(gb["lon"] - ga["lon"]) * EARTH_R * math.cos(lat0)
    return east, north, gb["alt"] - ga["alt"]


def build(fleet: dict[str, Any], local_ips: list[str]) -> dict[str, Any]:
    vehicles = fleet.get("vehicles", [])
    listen = fleet.get("listen", {})
    nodes: list[dict[str, Any]] = [
        {"id": "station", "type": "station", "hosts": local_ips, "listen_port": listen.get("port")}
    ]
    edges: list[dict[str, Any]] = []
    bridges: set[str] = set()
    for v in vehicles:
        nodes.append(
            {
                "id": v["id"],
                "type": "vehicle",
                "label": v["label"],
                "kind": v["kind"],
                "autopilot": v["autopilot"],
                "link": v["link"]["state"],
                "controller": v["controller"],
                "armed": v["armed"],
                "mode": v["mode"],
                "capabilities": v["capabilities"],
            }
        )
        if v["kind"] == "real" and v.get("host"):
            bridge_id = f"bridge:{v['host']}"
            if bridge_id not in bridges:
                bridges.add(bridge_id)
                nodes.append(
                    {
                        "id": bridge_id,
                        "type": "bridge",
                        "host": v["host"],
                        "version": (v.get("bridge") or {}).get("version"),
                    }
                )
            edges.append({"from": "station", "to": bridge_id, "type": "link", "state": v["link"]["state"]})
            edges.append({"from": bridge_id, "to": v["id"], "type": "link", "state": v["link"]["state"]})
        else:
            edges.append(
                {"from": "station", "to": v["id"], "type": "link", "state": v["link"]["state"], "via": "sim"}
            )
        claim = v.get("claim")
        if claim:
            edges.append(
                {
                    "from": f"session:{claim['session_id']}",
                    "to": v["id"],
                    "type": "claim",
                    "suspended": claim["suspended"],
                }
            )
        possession = v.get("possession")
        if possession:
            edges.append(
                {
                    "from": "station",
                    "to": v["id"],
                    "type": "possession",
                    "input": possession["input"],
                    "pad": possession["pad"],
                }
            )
    for s in fleet.get("sessions", []):
        nodes.append(
            {
                "id": f"session:{s['session_id']}",
                "type": "session",
                "session_id": s["session_id"],
                "name": s["name"],
                "expires_in_s": s["expires_in_s"],
            }
        )
    relations = []
    placed = [(v, _frame_key(v)) for v in vehicles]
    placed = [(v, k) for v, k in placed if k is not None]
    for i, (a, ka) in enumerate(placed):
        for b, kb in placed[i + 1 :]:
            if ka != kb:
                continue
            east, north, up = _offset(a, b, ka)  # type: ignore[arg-type]
            relations.append(
                {
                    "a": a["id"],
                    "b": b["id"],
                    "horizontal_m": round(math.hypot(east, north), 3),
                    "vertical_m": round(up, 3),
                    "bearing_deg": round(math.degrees(math.atan2(east, north)) % 360.0, 1),
                }
            )
    return {
        "station": {"hosts": local_ips, "listen": listen},
        "nodes": nodes,
        "edges": edges,
        "relations": relations,
    }
