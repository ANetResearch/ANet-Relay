"""Stick timelines on the fleet (10 §2): fly an ``.ars`` sequence, edit and trim it in flight, record one.

* ``run_sequence``: the internal ``sequence`` skill flies the document in its portable mode (the
  autopilot's own name is looked up here), arming first on the ground (real aircraft: ``confirm: "ARM"``).
* ``edit_sequence``: new tracks (the blocks not yet flown take effect at once; the time line keeps its
  place) and / or a new trim, while the sequence flies.
* ``record_sequence``: the sticks actually sent to the aircraft, sampled at ``RECORD_HZ`` while it is
  armed, from ``start`` until ``stop``; ``stop`` returns them as a sequence document.
* ``record_to_sequence``: a history record's sticks as a sequence document.
"""

from __future__ import annotations

from typing import Any

from . import flight_level, refly
from . import sequence as seqmod
from .authority import Actor
from .errors import err
from .mav import modes
from .skills import LANDED_THROTTLE, sample_throttle

RECORD_HZ = 20.0
RECORD_MAX_S = seqmod.MAX_DURATION_S


def _invalid(exc: seqmod.SequenceError) -> Exception:
    return err("invalid_request", reason=f"sequence {exc.field}: {exc.reason}")


class SequenceOps:
    def run_sequence(
        self,
        vid: str,
        actor: Actor,
        document: Any,
        confirm: str | None = None,
        speed: float = 1.0,
        start_at: float = 0.0,
    ) -> dict[str, Any]:
        try:
            seq = seqmod.normalise(document)
        except seqmod.SequenceError as exc:
            raise _invalid(exc) from None
        if not 0.25 <= float(speed) <= 2.0:
            raise err("invalid_request", reason="speed must be within 0.25..2")
        if not 0.0 <= float(start_at) < seq["duration_s"]:
            raise err("invalid_request", reason="start_at must fall inside the sequence")

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            self._check_control(v, actor)
            self._require_link(v)
            mode = seqmod.autopilot_mode(seq["mode"], v.autopilot)
            if mode not in modes.available_modes(v.autopilot):
                raise err("unknown_mode", mode=mode, modes=modes.available_modes(v.autopilot))
            # WP-1 (06 §5): a session at the sticks level flies manual modes only
            flight_level.check_mode(self.settings, actor, v.autopilot, mode)
            sim = getattr(v.driver, "sim", None)
            if sim is not None and not sim.hover_assist and seq["mode"] not in seqmod.MANUAL_MODES:
                raise err(
                    "invalid_request",
                    reason="hover assist is off on this simulator: fly the sequence in stabilize or acro",
                )
            if seq["mode"] == "poshold" and not v.has_capability("position"):
                raise err("capability_missing", required="position")
            if not v.armed:
                self._arm_checks(v, confirm)
            elif (
                v.landed != "ground"
                and seq["mode"] in seqmod.MANUAL_MODES
                and sample_throttle(seq, float(start_at)) <= LANDED_THROTTLE
            ):
                raise err(
                    "invalid_request",
                    reason="the aircraft is in the air: start a stabilize timeline where its throttle keeps "
                    "it flying",
                )
            params = {
                "mode": mode,
                "speed": float(speed),
                "start_at": float(start_at),
                "_seq": seq,
                "_trim": dict(seq["trim"]),
                "_progress": float(start_at),
            }
            self._cancel_plan(v, "superseded by a sequence", "被时间轴序列取代")
            skill = self._start_skill(v, "sequence", params, None)
            self.events.emit(
                "sequence_started",
                v.id,
                data={"skill_id": skill.id, "name": seq["name"], "duration_s": seq["duration_s"]},
                labels={"vehicle": v.label, "name": seq["name"]},
            )
            return {"id": vid, "skill": skill.view(), "sequence": seq}

        return self.call(run)

    def edit_sequence(self, vid: str, actor: Actor, document: Any = None, trim: Any = None) -> dict[str, Any]:
        if document is None and not trim:
            raise err("invalid_request", reason="send a sequence, a trim or both")
        try:
            seq = seqmod.normalise(document) if document is not None else None
        except seqmod.SequenceError as exc:
            raise _invalid(exc) from None

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            self._check_control(v, actor)
            skill = v.skill
            if skill is None or skill.name != "sequence" or not skill.active:
                raise err("no_sequence")
            try:
                new_trim = seqmod.normalise_trim(trim, skill.params["_trim"])
            except seqmod.SequenceError as exc:
                raise _invalid(exc) from None
            if seq is not None:
                if seq["mode"] != skill.params["_seq"]["mode"]:
                    raise err("invalid_request", reason="the mode cannot change while the sequence flies")
                skill.params["_seq"] = seq
                rest = seq["duration_s"] - float(skill.params.get("start_at") or 0.0)
                skill.timeout = max(skill.timeout, 40.0 + rest / float(skill.params.get("speed") or 1.0))
            skill.params["_trim"].update(new_trim)  # the same dict the skill reads every tick
            self.events.emit(
                "sequence_edited",
                v.id,
                data={
                    "skill_id": skill.id,
                    "trim": dict(skill.params["_trim"]),
                    "tracks": seq is not None,
                },
                labels={"vehicle": v.label},
            )
            return {"id": vid, "skill": skill.view(), "trim": dict(skill.params["_trim"])}

        return self.call(run)

    def stop_sequence(self, vid: str, actor: Actor) -> dict[str, Any]:
        """Stop the timeline. In the air it does not cut anything: an altitude-hold sequence hands over to the
        hold skill, a stabilize sequence keeps sending its trim attitude and last throttle until someone
        flies."""

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            self._check_control(v, actor)
            skill = v.skill
            if skill is None or skill.name != "sequence" or not skill.active:
                raise err("no_sequence")
            airborne = v.armed and v.landed != "ground"  # "unknown" may be in the air
            hold = skill.expect_mode in modes.HOLD_STICK_MODES and v.capabilities.althold
            if airborne and hold and not flight_level.limited(self.settings, actor):
                self._stop_skill(v, "stopped by the controller", "被控制者停止")
                prepared, _ = self._prepare_skill(v, "hold", {})
                self._start_skill(v, "hold", prepared, None)
                after = "hold"
            elif airborne:
                skill.params["_rest_at"] = skill.params.get("_progress", 0.0)
                skill.finish("stopped by the controller; holding the sticks")
                self._drain_skill(v)
                after = "sticks"
            else:
                self._stop_skill(v, "stopped by the controller", "被控制者停止")
                after = "ground"
            return {"id": vid, "skill": skill.view(), "after": after}

        return self.call(run)

    # ------------------------------------------------------------------ recording

    def record_sequence(self, vid: str, actor: Actor, action: str, name: str = "") -> dict[str, Any]:
        if action not in ("start", "stop"):
            raise err("invalid_request", reason="action must be start or stop")

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            self._check_control(v, actor)
            now = self.clock()
            if action == "start":
                v.seq_record = {"t0": now, "next": now, "rows": [], "modes": [], "name": name}
                self.events.emit(
                    "sequence_recording", v.id, data={"recording": True}, labels={"vehicle": v.label}
                )
                return {"id": vid, "recording": True}
            rec = v.seq_record
            if rec is None:
                raise err("invalid_request", reason="no sequence recording on this aircraft")
            v.seq_record = None
            self.events.emit(
                "sequence_recording", v.id, data={"recording": False}, labels={"vehicle": v.label}
            )
            rows = rec["rows"]
            if len(rows) < 2:
                raise err(
                    "invalid_request", reason="nothing was flown while recording (the aircraft was not armed)"
                )
            mode = max(set(rec["modes"]), key=rec["modes"].count) if rec["modes"] else v.mode
            try:
                doc = seqmod.from_rows(
                    rows,
                    mode=mode,
                    autopilot=v.autopilot,
                    name=rec["name"] or name,
                    source={"recorded": v.kind, "autopilot": v.autopilot, "mode": mode},
                )
            except seqmod.SequenceError as exc:
                raise _invalid(exc) from None
            return {"id": vid, "recording": False, "sequence": doc}

        return self.call(run)

    def _sequence_record_tick(self, v: Any, now: float) -> None:
        rec = v.seq_record
        if rec is None or now < rec["next"]:
            return
        rec["next"] = now + 1.0 / RECORD_HZ
        if now - rec["t0"] > RECORD_MAX_S:
            return
        if not v.armed:
            return
        if v.skill is not None and v.skill.active:
            s = v.skill.out
        else:
            s = v.sticks if v.sticks is not None else None
        if s is None:
            return
        rec["rows"].append((round(now - rec["t0"], 3), s.roll, s.pitch, s.yaw, s.throttle))
        if v.mode:
            rec["modes"].append(v.mode)

    def record_to_sequence(self, sid: int) -> dict[str, Any]:
        detail = self.history.session_detail(sid)
        if detail is None:
            raise err("record_not_found", record=sid)
        rows = refly.stick_rows(detail)
        if rows is None:
            raise err("refly_unavailable", mode="sticks", reason="the record holds no stick flight")
        session = detail.get("session") or {}
        try:
            return seqmod.from_rows(
                rows["rows"],
                mode=rows["mode"],
                autopilot=rows["autopilot"],
                name=str(session.get("label") or f"record {sid}"),
                source={"record": sid, "recorded": session.get("kind"), "autopilot": rows["autopilot"]},
            )
        except seqmod.SequenceError as exc:
            raise _invalid(exc) from None
