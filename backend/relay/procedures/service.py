"""Bench procedures on the fleet service (05 §4.3, §4.4): a mixin for ``FleetService``.

Starting, answering and aborting run on the fleet thread through ``self.call``; the run itself is advanced by
``_procedure_tick`` from ``_device_tick`` (50 Hz). Reports are saved to ``history.db`` (``test_runs``) when a
run starts, after every step and when it ends.
"""

from __future__ import annotations

import math
from typing import Any

from .. import authority as auth
from ..authority import Actor
from ..errors import err
from . import report as reportmod
from .catalogue import HOVER_S, PROCEDURES, catalogue_view
from .runner import Run, clean


class ProcedureOps:
    """Mixed into ``FleetService``; relies on its ``call``, ``_vehicle``, ``history`` and ``wall``."""

    # ------------------------------------------------------------------ catalogue and runs

    def procedure_catalogue(self) -> dict[str, Any]:
        return {"procedures": catalogue_view()}

    def _procedure_options(self, procedure: str, options: dict[str, Any] | None) -> dict[str, Any]:
        if procedure not in PROCEDURES:
            raise err("procedure_unknown", procedure=procedure, procedures=list(PROCEDURES))
        options = dict(options or {})
        out: dict[str, Any] = {}
        hover = options.get("hover_s")
        if hover is not None:
            if (
                isinstance(hover, bool)
                or not isinstance(hover, (int, float))
                or not math.isfinite(hover)
                or not HOVER_S[0] <= hover <= HOVER_S[1]
            ):
                raise err("invalid_request", reason=f"hover_s must be {HOVER_S[0]:g} to {HOVER_S[1]:g}")
            out["hover_s"] = float(hover)
        steps = PROCEDURES[procedure][2]
        skip = options.get("skip") or []
        if not isinstance(skip, list) or any(s not in steps for s in skip):
            raise err("invalid_request", reason=f"skip lists steps of {procedure}: {', '.join(steps)}")
        if "S12" in skip:
            raise err("invalid_request", reason="the report step S12 cannot be skipped")
        out["skip"] = sorted(set(skip), key=steps.index)
        if options.get("auto_answer"):
            out["auto_answer"] = True
        return out

    def procedure_start(
        self, vid: str, actor: Actor, procedure: str, options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        opts = self._procedure_options(procedure, options)

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)  # type: ignore[attr-defined]
            self._check_control(v, actor)  # type: ignore[attr-defined]
            self._require_link(v)  # type: ignore[attr-defined]
            if v.procedure is not None and v.procedure.active:
                raise err("procedure_running", vehicle=vid, procedure=v.procedure.procedure)
            if opts.get("auto_answer") and v.kind != "sim":
                raise err("invalid_request", reason="auto_answer is only available on simulated aircraft")
            if v.state["crashed"]:
                raise err("crashed")
            if v.armed:
                raise err("armed")
            started = self.wall()  # type: ignore[attr-defined]
            run_id = self.history.test_run_start(  # type: ignore[attr-defined]
                {
                    "vehicle_id": v.id,
                    "host": v.host,
                    "procedure": procedure,
                    "state": "running",
                    "started": started,
                    "session_id": v.record_session,
                }
            )
            run_ = Run(self, v, run_id, procedure, actor, opts)  # type: ignore[arg-type]
            v.procedure = run_
            run_.begin(v)
            return clean(run_.view())

        return self.call(run)  # type: ignore[attr-defined]

    def procedure_view(self, vid: str) -> dict[str, Any]:
        """The current or most recent run of an aircraft (from the records when Relay restarted since)."""

        def run() -> dict[str, Any] | None:
            v = self._vehicle(vid)  # type: ignore[attr-defined]
            return None if v.procedure is None else clean(v.procedure.view())

        view = self.call(run)  # type: ignore[attr-defined]
        if view is not None:
            return view
        recent = self.history.test_runs(vid, 1)  # type: ignore[attr-defined]
        if recent:
            full = self.history.test_run(recent[0]["run_id"])  # type: ignore[attr-defined]
            if full is not None:
                return full
        raise err("procedure_not_found", vehicle=vid)

    def procedure_answer(
        self, vid: str, actor: Actor, step: str, answer: str, value: Any = None, confirm: str | None = None
    ) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            v = self._vehicle(vid)  # type: ignore[attr-defined]
            if v.procedure is None or not v.procedure.active:
                raise err("no_procedure", vehicle=vid)
            v.procedure.answer(actor, step, answer, value, confirm)
            return clean(v.procedure.view())

        return self.call(run)  # type: ignore[attr-defined]

    def procedure_abort(self, vid: str, actor: Actor) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            v = self._vehicle(vid)  # type: ignore[attr-defined]
            auth.check_operator_or_controller(v.auth, actor, self._claim_alive(v))  # type: ignore[attr-defined]
            if v.procedure is None or not v.procedure.active:
                raise err("no_procedure", vehicle=vid)
            v.procedure.abort("operator" if actor.kind == "operator" else "agent")
            return clean(v.procedure.view())

        return self.call(run)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ reports

    def test_runs(self, vehicle: str | None = None, limit: int = 50) -> dict[str, Any]:
        return {"runs": self.history.test_runs(vehicle, limit)}  # type: ignore[attr-defined]

    def test_run(self, run_id: int) -> dict[str, Any]:
        found = self.history.test_run(run_id)  # type: ignore[attr-defined]
        if found is None:
            raise err("test_run_not_found", run=run_id)
        return found

    def test_run_export(self, run_id: int, fmt: str) -> tuple[str, str]:
        found = self.test_run(run_id)
        if fmt == "md":
            return "text/markdown; charset=utf-8", reportmod.to_markdown(found)
        return "application/json", reportmod.to_json(found)

    # ------------------------------------------------------------------ fleet hooks

    def _procedure_tick(self, v: Any, now: float) -> None:
        run = v.procedure
        if run is not None and run.active:
            run.tick(now)

    def _procedure_brief(self, v: Any) -> dict[str, Any] | None:
        return None if v.procedure is None else v.procedure.brief()

    def _procedure_removed(self, v: Any) -> None:
        if v.procedure is not None and v.procedure.active:
            v.procedure.abort("removed")

    def _save_test_run(self, run: Run) -> None:
        view = clean(run.view())
        steps = view.pop("steps")
        for s in steps:
            s.pop("live", None)  # the live mini-chart series are not part of the report
            s.pop("prompt", None)
        meta = {k: view[k] for k in view if k not in ("run_id", "vehicle", "host", "procedure", "state")}
        self.history.test_run_update(  # type: ignore[attr-defined]
            run.run_id,
            {
                "state": run.state,
                "verdict": run.verdict,
                "ended": run.ended,
                "steps": steps,
                "params": clean(run.params_record()),
                "session_id": run.session_id,
                "meta": meta,
            },
        )
