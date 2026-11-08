"""Simulator flight-model operations of the fleet service (05 §5, WP-C).

A mixin for ``FleetService``: the per-aircraft hover-assist switch, the defaults new simulators take from
the settings (``sim_hover_assist``, ``sim_turbulence``) and the ``vehicle.sim`` snapshot block. The flight
model itself lives in ``sim/autopilot.py`` and ``sim/physics.py``; everything here runs on the fleet thread
through ``self.call``.

Who may switch hover assist: the operator, or the session that controls the aircraft. While nobody
controls it (no possession and no live claim), an agent session may also switch it as an application
operation when the operator allows agents to drive the app (``agents_app_control``).
"""

from __future__ import annotations

from typing import Any

from . import authority as auth
from .authority import Actor
from .errors import err
from .mav import modes
from .mav.mavlink import M


class SimOps:
    def _sim_options(self, hover_assist: bool | None = None) -> dict[str, Any]:
        """Constructor keywords for a new SimAutopilot from the settings (and an explicit override)."""
        settings = self.settings
        assist = settings.sim_hover_assist if hover_assist is None else bool(hover_assist)
        return {"hover_assist": assist, "turbulence": settings.sim_turbulence}

    def _sim_view(self, v: Any) -> dict[str, Any] | None:
        if v.kind != "sim":
            return None
        sim = v.driver.sim
        return {
            "hover_assist": sim.hover_assist,
            "turbulence": round(sim.turbulence, 2),
            "airframe": sim.airframe.ref(),  # WP-2a (06 §4): preset and seed only
        }

    def _apply_sim_settings(self) -> None:
        """Push a changed turbulence strength to every simulator (hover assist stays per aircraft)."""
        for v in self.vehicles.values():
            if v.kind == "sim":
                v.driver.sim.turbulence = self.settings.sim_turbulence

    def _check_sim_assist(self, v: Any, actor: Actor, app_control: bool) -> None:
        if actor.kind == "operator":
            return
        controlled = v.auth.possession is not None or self._claim_alive(v)
        if not controlled and app_control:
            if not self.settings.agents_app_control:
                raise err("app_control_disabled")
            return
        auth.check_operator_or_controller(v.auth, actor, self._claim_alive(v))

    def set_sim_assist(self, vid: str, on: bool, actor: Actor, app_control: bool = False) -> dict[str, Any]:
        """Switch hover assist on one simulator; allowed armed and in flight (05 §5.1)."""

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            if v.kind != "sim":
                raise err("sim_only")
            self._check_sim_assist(v, actor, app_control)
            sim = v.driver.sim
            before = sim.hover_assist
            enter_hold = self.settings.possess_mode != "stabilize"
            sim.set_hover_assist(bool(on), self.clock(), enter_hold=enter_hold)
            if sim.hover_assist != before:
                # Ask for the sensor and estimator reports now, so capabilities and the possession mode
                # follow the switch within one round trip rather than at the next 2 Hz report.
                v.driver.request_message(M.MAVLINK_MSG_ID_SYS_STATUS)
                if v.autopilot != modes.PX4:
                    v.driver.request_message(M.MAVLINK_MSG_ID_EKF_STATUS_REPORT)
                self.events.emit(
                    "sim_assist_changed",
                    v.id,
                    data={"hover_assist": sim.hover_assist, "by": actor.kind},
                    labels={
                        "vehicle": v.label,
                        "detail_en": "on" if sim.hover_assist else "off",
                        "detail_zh": "已开启" if sim.hover_assist else "已关闭",
                    },
                )
            return {"id": vid, "hover_assist": sim.hover_assist}

        return self.call(run)
