"""Simulator airframe defects through the fleet service (06 §4, WP-2a).

A mixin for ``FleetService``: the airframe a new simulator takes (``POST /sims`` body or the setting
``sim_airframe_default``), ``PUT`` / ``GET /sims/{vid}/airframe`` and the ``vehicle.sim.airframe`` snapshot
reference (preset and seed only). The model lives in ``sim/airframe.py``; it acts only while hover assist is
off. Everything here runs on the fleet thread through ``self.call``.

Who may set or read the values: the same rule as the hover-assist switch (``SimOps._check_sim_assist``):
the operator, the controlling session, or, while nobody controls the aircraft, an agent session as an
application operation when ``agents_app_control`` is on. Programs never see the values (06 §4).
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

from .authority import Actor
from .errors import err
from .sim.airframe import GENERATED, LIMITS, SEED_MAX, Airframe, resolve

PRESET_ZH = {"ideal": "理想", "typical": "典型", "rough": "粗糙", "custom": "自定义"}
PRESET_EN = {"ideal": "ideal", "typical": "typical", "rough": "rough", "custom": "custom"}
DEFAULT_PRESETS = GENERATED  # what sim_airframe_default may name


def _system_seed() -> int:
    return random.SystemRandom().randint(0, SEED_MAX)


class AirframeOps:
    # Tests and the headless evaluator may replace this with a deterministic source.
    airframe_seed_source: Callable[[], int] = staticmethod(_system_seed)

    def _resolve_airframe(self, spec: dict[str, Any] | None, current: Airframe | None = None) -> Airframe:
        spec = dict(spec or {})
        default = self.settings.sim_airframe_default
        if current is not None and not spec.get("preset") and spec.get("params") is None:
            # a seed alone re-rolls the current preset; a custom airframe keeps its values
            if current.preset in GENERATED:
                default = current.preset
            else:
                spec["preset"] = "custom"
        try:
            return resolve(spec, current, default, self.airframe_seed_source)
        except ValueError as exc:
            raise err("invalid_request", reason=f"airframe {exc.args[0] if exc.args else 'body'}") from None

    def _airframe_ref(self, v: Any) -> dict[str, Any]:
        return v.driver.sim.airframe.ref()

    def _airframe_view(self, v: Any) -> dict[str, Any]:
        sim = v.driver.sim
        af: Airframe = sim.airframe
        return {
            "id": v.id,
            "preset": af.preset,
            "seed": af.seed,
            "params": af.params(),
            "active": not sim.hover_assist,
            "hover_throttle": af.hover_throttle(),
            "limits": {name: list(bounds) for name, bounds in LIMITS.items()},
        }

    def _airframe_vehicle(self, vid: str, actor: Actor, app_control: bool) -> Any:
        v = self._vehicle(vid)
        if v.kind != "sim":
            raise err("sim_only")
        self._check_sim_assist(v, actor, app_control)
        return v

    def get_sim_airframe(self, vid: str, actor: Actor, app_control: bool = False) -> dict[str, Any]:
        """The full airframe values of one simulator (06 §4)."""

        def run() -> dict[str, Any]:
            return self._airframe_view(self._airframe_vehicle(vid, actor, app_control))

        return self.call(run)

    def set_sim_airframe(
        self, vid: str, spec: dict[str, Any], actor: Actor, app_control: bool = False
    ) -> dict[str, Any]:
        """Apply ``{preset | params, seed?}`` to one simulator; allowed armed and in flight."""

        def run() -> dict[str, Any]:
            v = self._airframe_vehicle(vid, actor, app_control)
            sim = v.driver.sim
            af = self._resolve_airframe(spec, sim.airframe)
            sim.set_airframe(af)
            self.events.emit(
                "sim_airframe_changed",
                v.id,
                data={"preset": af.preset, "seed": af.seed, "by": actor.kind},
                labels={
                    "vehicle": v.label,
                    "preset_en": PRESET_EN[af.preset],
                    "preset_zh": PRESET_ZH[af.preset],
                },
            )
            return self._airframe_view(v)

        return self.call(run)
