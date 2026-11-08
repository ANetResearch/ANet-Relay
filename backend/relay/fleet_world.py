"""Collisions on the fleet thread (07 §2, WP-A): the fleet's side of ``sim.world``.

Every fleet tick, before the simulator drivers report, ``_world_step`` advances all local simulators to the
tick together through ``World.step`` (one 250 Hz physics step at a time, contacts swept after each), with
the solid rings of every active ring course, and publishes the contacts as ``collision`` events. Real
aircraft never take part. ``_crash_details`` adds the cause and place of a simulator crash to the
``crashed`` event (``reason_code`` ``ground`` / ``collision`` / ``shot_down``, ``point`` in world ENU).

The LAN arena (WP-B) reaches the world through ``self.world.external`` (remote aircraft, local frame) and
``self.world.on_external_contact``; the evaluator through ``self.world.fixed_rings``.
"""

from __future__ import annotations

import logging
from typing import Any

from .sim.world import Body

log = logging.getLogger(__name__)

COLLISION_PREFIX = "collision with "  # sim.world's crash reason


class WorldOps:
    def _world_bodies(self) -> list[Body]:
        bodies = []
        for v in self.vehicles.values():  # type: ignore[attr-defined]
            sim = getattr(v.driver, "sim", None)
            if v.kind != "sim" or sim is None:
                continue
            bodies.append(Body(v.id, v.label, sim, tuple(v.frame_offset)))
        return bodies

    def _world_rings(self) -> list[tuple[str, dict[str, Any]]]:
        rings: list[tuple[str, dict[str, Any]]] = []
        seen: set[tuple[float, float, float, float, float]] = set()
        for v in self.vehicles.values():  # type: ignore[attr-defined]
            run = v.course
            if run is None or not run.active:
                continue
            for i, ring in enumerate(run.rings):
                key = (*ring["c"], ring["yaw_deg"], ring["r"])
                if key in seen:  # two aircraft on the same course share its rings
                    continue
                seen.add(key)
                rings.append((f"ring:{i + 1}", ring))
        return rings

    def _world_step(self, now: float) -> None:
        bodies = self._world_bodies()
        if not bodies:
            return
        for b in bodies:
            self.vehicles[b.id].driver.prepare(now)  # type: ignore[attr-defined]
        try:
            reports = self.world.step(bodies, self._world_rings(), now)  # type: ignore[attr-defined]
        except Exception:
            log.exception("collision world step failed")  # the drivers still step what is left
            return
        for report in reports:
            self._emit_collision(report)

    def _emit_collision(self, report: dict[str, Any]) -> None:
        data = {k: report[k] for k in ("a", "b", "b_label", "point", "v_rel", "crashed", "kind")}
        if report.get("remote"):
            data["remote"] = True  # a peer's aircraft (WP-B); its ram is credited through COLLIDE
        crashed_en = " Both aircraft crashed." if len(report["crashed"]) > 1 else ""
        crashed_zh = "两机均坠毁。" if len(report["crashed"]) > 1 else ""
        if len(report["crashed"]) == 1:
            crashed_en, crashed_zh = " The aircraft crashed.", "飞机坠毁。"
        other_zh = f" {report['b_label']} "  # a Latin label is set off by spaces in Chinese text
        if report["kind"] == "ring":
            other_zh = f"第 {report['b'].split(':', 1)[-1]} 个环"
        self.events.emit(  # type: ignore[attr-defined]
            "collision",
            report["a"],
            data={**data, "detail_en": crashed_en, "detail_zh": crashed_zh, "other_zh": other_zh},
            labels={"vehicle": report["a_label"], "other": report["b_label"]},
        )

    def _crash_details(self, v: Any) -> dict[str, Any]:
        """``crashed`` event data: the cause code, the place, and for a collision what it hit (the text)."""
        sim = getattr(v.driver, "sim", None)
        if v.kind != "sim" or sim is None:
            return {"reason_code": None, "point": None, "detail_en": "", "detail_zh": ""}
        s = sim.quad.s
        point = s.crash_point
        if point is not None:
            ox, oy, oz = v.frame_offset
            point = [round(point[0] + ox, 3), round(point[1] + oy, 3), round(point[2] + oz, 3)]
        detail_en = detail_zh = ""
        reason = s.crash_reason or ""
        if s.crash_code == "collision" and reason.startswith(COLLISION_PREFIX):
            other = reason[len(COLLISION_PREFIX) :]
            other_zh = f"第 {other[5:]} 个环" if other.startswith("ring ") else f" {other} "
            detail_en, detail_zh = f": collided with {other}", f"：与{other_zh}相撞"
        return {"reason_code": s.crash_code, "point": point, "detail_en": detail_en, "detail_zh": detail_zh}
