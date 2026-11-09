"""Twin profiles (11 §5): a real aircraft's firmware identity, parameters, frame and home, kept as JSON.

``capture`` builds a profile from a connected aircraft whose parameter table Relay has read completely;
``ProfileStore`` keeps profiles as ``<data dir>/twins/<id>.json``. A profile names the recommended runtime
pack (``twin.identity.match``) and may be edited afterwards: name, frame, pack (same firmware only) and
home. Recapturing from the aircraft replaces the identity and parameters and keeps the name and frame.

``frame_model`` turns a profile frame (or none) and the effective parameters into the ``FrameModel`` the
twin's physics flies: the preset's values, mass and wheelbase from the profile, the inertia estimated from
them (``sim.frame.inertia_from``), and the rotor maximum derived from the learned hover thrust
(``sim.frame.derive_from_hover``) so the twin hovers at the aircraft's own output.
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..sim.frame import PRESETS, FrameModel, derive_from_hover, inertia_from
from . import params as tparams

ID_RE = re.compile(r"^tp-[0-9a-f]{8}$")
FRAME_KEYS = ("preset", "mass_kg", "wheelbase_m", "prop_in", "cells", "capacity_mah")
NAME_MAX = 64
FORMAT = 1

# Firmware defaults of the hover-thrust parameters, for a table that does not list them.
HOVER_DEFAULTS = {
    "ardupilot": {"MOT_THST_HOVER": 0.35, "MOT_THST_EXPO": 0.65, "MOT_SPIN_MIN": 0.15, "MOT_SPIN_MAX": 0.95},
    "px4": {"MPC_THR_HOVER": 0.5, "THR_MDL_FAC": 0.0},
}


class ProfileError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def new_id() -> str:
    return f"tp-{secrets.token_hex(4)}"


# ---------------------------------------------------------------------------------------------- frames


def guess_preset(cells: int | None, capacity_mah: float | None) -> str:
    """A frame preset for a captured aircraft: 4S and up or a large pack is the 450 class, else the 250."""
    if (cells or 0) >= 4 or (capacity_mah or 0.0) >= 2000.0:
        return "large-450"
    return "small-250"


def normalise_frame(spec: Any, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """A validated profile frame ``{preset, mass_kg, wheelbase_m, prop_in, cells, capacity_mah}`` from
    ``spec`` over ``base`` (a changed preset starts from that preset's values). Raises ProfileError."""
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise ProfileError("frame must be an object")
    unknown = [k for k in spec if k not in FRAME_KEYS]
    if unknown:
        raise ProfileError(f"frame field {unknown[0]} is not known")
    current = dict(base or {})
    preset = spec.get("preset") or current.get("preset") or "micro-120"
    if preset not in PRESETS:
        raise ProfileError(f"frame preset {preset} is not known")
    if spec.get("preset") and spec.get("preset") != current.get("preset"):
        current = {}  # a new preset: its own values unless the request gives them
    model = PRESETS[preset]
    out = {
        "preset": preset,
        "mass_kg": model.mass_kg,
        "wheelbase_m": model.wheelbase_m,
        "prop_in": round(model.prop_in, 2),
        "cells": model.cells,
        "capacity_mah": model.capacity_mah,
    }
    for key in FRAME_KEYS[1:]:
        if current.get(key) is not None:
            out[key] = current[key]
        if spec.get(key) is not None:
            out[key] = spec[key]
    try:
        frame_model(out, {}, "ardupilot")
    except ValueError as exc:
        raise ProfileError(f"frame {exc.args[0] if exc.args else 'values'} is out of range") from None
    return out


def frame_model(frame: dict[str, Any] | None, effective: dict[str, float], family: str) -> FrameModel:
    """The physics frame for a twin. ``effective``: the parameters the firmware starts with (pack defaults,
    profile, Relay's forced values); the hover thrust in them sets the rotor maximum. Raises ValueError."""
    spec = dict(frame or {"preset": "micro-120"})
    base = PRESETS.get(spec.get("preset") or "micro-120")
    if base is None:
        raise ValueError("preset")
    values: dict[str, Any] = {}
    for key in ("mass_kg", "wheelbase_m", "prop_in", "capacity_mah"):
        if spec.get(key) is not None:
            raw = spec[key]
            if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
                raise ValueError(key)
            values[key] = float(raw)
    if spec.get("cells") is not None:
        cells = spec["cells"]
        if isinstance(cells, bool) or not isinstance(cells, (int, float)) or not float(cells).is_integer():
            raise ValueError("cells")
        values["cells"] = int(cells)
    mass = values.get("mass_kg", base.mass_kg)
    wheelbase = values.get("wheelbase_m", base.wheelbase_m)
    if mass != base.mass_kg or wheelbase != base.wheelbase_m:
        values["ixx"], values["iyy"], values["izz"] = inertia_from(mass, wheelbase)
        scale = mass / base.mass_kg
        values["body_drag_lin"] = tuple(c * scale for c in base.body_drag_lin)
        values["body_drag_quad"] = tuple(c * scale for c in base.body_drag_quad)
    model = FrameModel.from_dict({**{k: v for k, v in values.items()}, "preset": base.preset}, base=base)
    defaults = HOVER_DEFAULTS.get(family, {})
    get = lambda name: float(effective.get(name, defaults.get(name, 0.0)))  # noqa: E731
    if family == "ardupilot":
        model = derive_from_hover(
            model,
            min(0.95, max(0.05, get("MOT_THST_HOVER"))),
            expo=min(1.0, max(-1.0, get("MOT_THST_EXPO"))),
            spin_min=min(0.5, max(0.0, get("MOT_SPIN_MIN"))),
            spin_max=min(1.0, max(0.5, get("MOT_SPIN_MAX"))),
        )
    elif family == "px4":
        model = derive_from_hover(
            model,
            min(0.95, max(0.05, get("MPC_THR_HOVER"))),
            expo=min(1.0, max(-1.0, get("THR_MDL_FAC"))),
        )
    return replace(model, preset=spec.get("preset") or base.preset)


# ---------------------------------------------------------------------------------------------- capture


def capture(
    *,
    vehicle_id: str,
    label: str,
    identity: dict[str, Any],
    params: dict[str, float],
    types: dict[str, str],
    pack: str | None,
    match_level: str,
    home: dict[str, float] | None,
    cells: int | None,
    captured_at: float,
    name: str | None = None,
    frame: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A new profile from an aircraft's identity and complete parameter table."""
    capacity = None
    for key in ("BATT_CAPACITY", "BAT1_CAPACITY", "BAT_CAPACITY"):
        if params.get(key):
            capacity = float(params[key])
            break
    if frame is None:
        preset = guess_preset(cells, capacity)
        frame = {"preset": preset}
        if cells:
            frame["cells"] = int(cells)
        if capacity and capacity > 0:
            frame["capacity_mah"] = capacity
    title = name or " ".join(
        part
        for part in (
            identity.get("board") or label,
            {"ardupilot": "ArduCopter", "px4": "PX4"}.get(identity.get("family") or "", ""),
            identity.get("version") or "",
        )
        if part
    )
    return {
        "format": FORMAT,
        "id": new_id(),
        "name": _name(title),
        "captured_at": captured_at,
        "updated_at": captured_at,
        "source": {"vehicle": vehicle_id, "label": label},
        "firmware": {
            k: identity.get(k) for k in ("family", "vehicle", "version", "type", "git", "board", "board_id")
        },
        "pack": pack,
        "match": match_level,
        "params": {k: float(v) for k, v in sorted(params.items())},
        "param_types": {k: types[k] for k in sorted(types) if k in params},
        "frame": normalise_frame(frame),
        "home": home,
    }


def recapture(profile: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
    """``profile`` with the identity, parameters, pack and source of ``fresh``; name, frame, home kept."""
    out = dict(profile)
    for key in ("firmware", "pack", "match", "params", "param_types", "source", "captured_at"):
        out[key] = fresh[key]
    out["updated_at"] = fresh["captured_at"]
    if out.get("home") is None:
        out["home"] = fresh.get("home")
    return out


def _name(text: Any) -> str:
    name = " ".join(str(text or "").split())[:NAME_MAX]
    if not name:
        raise ProfileError("name is required")
    return name


def normalise_home(home: Any) -> dict[str, float] | None:
    if home is None:
        return None
    if not isinstance(home, dict):
        raise ProfileError("home must be an object")
    try:
        lat, lon = float(home["lat"]), float(home["lon"])
        alt = float(home.get("alt_m", 0.0))
    except (KeyError, TypeError, ValueError):
        raise ProfileError("home needs lat, lon and alt_m") from None
    if not (-89.0 <= lat <= 89.0 and -180.0 <= lon <= 180.0 and -500.0 <= alt <= 9000.0):
        raise ProfileError("home is out of range")
    return {"lat": lat, "lon": lon, "alt_m": alt}


def edit(profile: dict[str, Any], patch: dict[str, Any], now: float) -> dict[str, Any]:
    """Apply ``{name?, frame?, pack?, home?}`` (the caller checked that ``pack`` is a usable pack of the same
    firmware). Raises ProfileError."""
    out = dict(profile)
    unknown = [k for k in patch if k not in ("name", "frame", "pack", "home")]
    if unknown:
        raise ProfileError(f"field {unknown[0]} cannot be changed")
    if "name" in patch:
        out["name"] = _name(patch["name"])
    if "frame" in patch:
        out["frame"] = normalise_frame(patch["frame"], out.get("frame"))
    if "pack" in patch:
        out["pack"] = patch["pack"]
    if "home" in patch:
        out["home"] = normalise_home(patch["home"])
    out["updated_at"] = now
    return out


def summary(profile: dict[str, Any]) -> dict[str, Any]:
    """The list view of a profile (no parameter values)."""
    family = (profile.get("firmware") or {}).get("family") or ""
    applied, excluded = tparams.split(family, profile.get("params") or {})
    return {
        "id": profile["id"],
        "name": profile["name"],
        "captured_at": profile.get("captured_at"),
        "updated_at": profile.get("updated_at"),
        "source": profile.get("source"),
        "firmware": profile.get("firmware"),
        "pack": profile.get("pack"),
        "match": profile.get("match"),
        "frame": profile.get("frame"),
        "home": profile.get("home"),
        "params_total": len(profile.get("params") or {}),
        "params_applied": len(applied),
        "params_excluded": len(excluded),
    }


def detail(profile: dict[str, Any]) -> dict[str, Any]:
    """The full profile with the parameter filter's result (11 §5: applied N, not applied M and why)."""
    family = (profile.get("firmware") or {}).get("family") or ""
    _applied, excluded = tparams.split(family, profile.get("params") or {})
    return {**summary(profile), "params": profile.get("params") or {}, "excluded": excluded}


# ---------------------------------------------------------------------------------------------- store


class ProfileStore:
    """``<root>/<id>.json`` per profile; ``root`` None keeps profiles in memory (tests, no data dir)."""

    def __init__(self, root: Path | None) -> None:
        self.root = root
        self._memory: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            if self.root is None:
                items = list(self._memory.values())
            else:
                items = []
                if self.root.is_dir():
                    for path in sorted(self.root.glob("tp-*.json")):
                        data = _load(path)
                        if data is not None:
                            items.append(data)
        return sorted(items, key=lambda p: (-(p.get("updated_at") or 0.0), p["id"]))

    def get(self, pid: str) -> dict[str, Any] | None:
        if not ID_RE.match(str(pid)):
            return None
        with self._lock:
            if self.root is None:
                found = self._memory.get(pid)
                return json.loads(json.dumps(found)) if found is not None else None
            return _load(self.root / f"{pid}.json")

    def save(self, profile: dict[str, Any]) -> None:
        with self._lock:
            if self.root is None:
                self._memory[profile["id"]] = json.loads(json.dumps(profile))
                return
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / f"{profile['id']}.json"
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", "utf-8")
            os.replace(tmp, path)

    def delete(self, pid: str) -> bool:
        if not ID_RE.match(str(pid)):
            return False
        with self._lock:
            if self.root is None:
                return self._memory.pop(pid, None) is not None
            path = self.root / f"{pid}.json"
            if not path.exists():
                return False
            path.unlink()
            return True


def _load(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not ID_RE.match(str(data.get("id"))) or path.stem != data["id"]:
        return None
    return data


__all__ = [
    "ProfileError",
    "ProfileStore",
    "capture",
    "detail",
    "edit",
    "frame_model",
    "normalise_frame",
    "recapture",
    "summary",
]
