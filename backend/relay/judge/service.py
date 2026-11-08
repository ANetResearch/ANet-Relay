"""The evaluation service (06 §3.2-§3.4) behind the ``/challenges``, ``/jobs``, ``/duels``, ``/ladder`` and
``/traces`` routes; ``FleetService.judge``.

Submissions and duels resolve their programs from the program store on the caller's thread (so a bad id,
revision or parameter is a 4xx at once), then queue a job. The worker gets the program source, never a path.
When a job finishes its traces are stored, the challenge result is recorded (``results.json``) or the duel
moves the Elo ladder (``ladder.json``), and a ``judge_finished`` event is emitted.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..errors import RelayError, err
from ..programs import validator
from ..programs.store import fitting_params
from . import challenges as challengemod
from .duels import ELO_START, MAX_PROGRAMS, MAX_ROUNDS, MIN_PROGRAMS, elo_round
from .jobs import Job, JobManager
from .scenario import DuelSettings
from .store import JudgeStore
from .worker import run_subprocess, timeout_for

if TYPE_CHECKING:
    from ..fleet import FleetService

log = logging.getLogger("relay.judge")

DEFAULT_ROUNDS = 3
SEED_MAX = 2**31 - 1


def _subprocess_runner(
    spec: dict[str, Any], progress: Callable[[float], None], cancel: Any
) -> dict[str, Any]:
    return run_subprocess(spec, progress, cancel, timeout_for(spec))


class JudgeService:
    def __init__(self, fleet: FleetService, runner: Callable[..., dict[str, Any]] | None = None) -> None:
        self.fleet = fleet
        self.store = JudgeStore(fleet.data_dir, wall=fleet.wall)
        self.jobs = JobManager(
            runner or _subprocess_runner, self._done, wall=fleet.wall, on_finished=self._announce
        )

    def close(self) -> None:
        self.jobs.close()

    # ------------------------------------------------------------------ challenges

    def challenges(self) -> list[dict[str, Any]]:
        out = []
        for c in challengemod.CHALLENGES.values():
            rec = self.store.challenge_record(c.id)
            out.append({**c.summary(), "best": rec["best"], "attempts": len(rec["history"])})
        return out

    def _challenge(self, cid: str) -> challengemod.Challenge:
        c = challengemod.get(cid)
        if c is None:
            raise err("challenge_not_found", challenge=cid)
        return c

    def challenge(self, cid: str) -> dict[str, Any]:
        c = self._challenge(cid)
        rec = self.store.challenge_record(c.id)
        return {
            **c.detail(),
            "best": rec["best"],
            "attempts": len(rec["history"]),
            "history": list(reversed(rec["history"]))[:20],
        }

    # ------------------------------------------------------------------ programs

    def _entrant(self, item: Any) -> dict[str, Any]:
        """``"id"`` or ``{program, rev?, params?}`` -> the revision's source and checked parameters."""
        if isinstance(item, str):
            pid, rev, params = item, None, None
        elif isinstance(item, dict):
            pid, rev, params = item.get("program"), item.get("rev"), item.get("params")
        else:
            raise err("invalid_request", reason="a program is an id or {program, rev?, params?}")
        if not isinstance(pid, str) or not pid:
            raise err("invalid_request", reason="program is required")
        if rev is not None and (isinstance(rev, bool) or not isinstance(rev, int)):
            raise err("invalid_request", reason="rev must be an integer")
        if params is not None and not isinstance(params, dict):
            raise err("invalid_request", reason="params must be an object")
        store = self.fleet.programs.store
        program = store.get(pid)
        revision = store.revision(pid, rev)
        source = revision["source"]
        result = validator.validate(source)
        if not result.ok:
            first = result.errors[0]
            raise err(
                "program_invalid",
                reason=f"line {first.line}: {first.message_en}",
                problems=[p.view() for p in result.errors],
            )
        stored = fitting_params(revision.get("params") or {}, result.params, result.param_ranges)
        merged = self.fleet.programs._params({**stored, **(params or {})}, result.params, result.param_ranges)
        return {
            "program": pid,
            "rev": revision["rev"],
            "name": program["name"],
            "source": source,
            "params": merged,
        }

    @staticmethod
    def _public(entrant: dict[str, Any]) -> dict[str, Any]:
        return {"program": entrant["program"], "rev": entrant["rev"], "name": entrant["name"]}

    # ------------------------------------------------------------------ submissions and duels

    def submit(self, cid: str, body: dict[str, Any], author: str) -> dict[str, Any]:
        """``POST /challenges/{id}/submit``: visible seeds, and the hidden ones unless ``visible_only``."""
        c = self._challenge(cid)
        entrant = self._entrant(body)
        opponent = self._entrant(c.opponent) if c.opponent else None
        visible_only = bool(body.get("visible_only"))
        runs = [{"seed": s, "hidden": False} for s in c.visible]
        if not visible_only:
            runs += [{"seed": s, "hidden": True} for s in c.hidden]
        spec = {
            "kind": "challenge",
            "challenge": c.id,
            "runs": runs,
            "entrant": entrant,
            "opponent": opponent,
        }
        meta = {
            "challenge": c.id,
            "programs": [self._public(entrant)],
            "visible_only": visible_only,
            "author": author,
        }
        return self.jobs.submit("challenge", spec, meta)

    def duel(self, body: dict[str, Any], author: str) -> dict[str, Any]:
        """``POST /duels {programs: [2..8], rounds?, seed?, scenario?}``."""
        items = body.get("programs")
        if not isinstance(items, list) or not MIN_PROGRAMS <= len(items) <= MAX_PROGRAMS:
            raise err("invalid_request", reason=f"programs must list {MIN_PROGRAMS}-{MAX_PROGRAMS} programs")
        entrants = [self._entrant(item) for item in items]
        ids = [e["program"] for e in entrants]
        if len(set(ids)) != len(ids):
            raise err("invalid_request", reason="each program may enter once")
        rounds = body.get("rounds", DEFAULT_ROUNDS)
        if isinstance(rounds, bool) or not isinstance(rounds, int) or not 1 <= rounds <= MAX_ROUNDS:
            raise err("invalid_request", reason=f"rounds must be 1-{MAX_ROUNDS}")
        seed = body.get("seed", 1)
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= SEED_MAX:
            raise err("invalid_request", reason="seed must be a non-negative integer")
        try:
            settings = DuelSettings.parse(body.get("scenario"))
        except ValueError as exc:
            raise err("invalid_request", reason=str(exc)) from exc
        spec = {
            "kind": "duel",
            "entrants": entrants,
            "rounds": rounds,
            "seed": seed,
            "scenario": settings.view(),
        }
        meta = {
            "challenge": None,
            "programs": [self._public(e) for e in entrants],
            "rounds": rounds,
            "seed": seed,
            "scenario": settings.view(),
            "author": author,
        }
        return self.jobs.submit("duel", spec, meta)

    def job(self, job_id: str) -> dict[str, Any]:
        return self.jobs.view(job_id)

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self.jobs.cancel(job_id)

    def ladder(self) -> dict[str, Any]:
        return self.store.ladder_view()

    def trace(self, trace_id: str) -> dict[str, Any]:
        found = self.store.trace(trace_id)
        if found is None:
            raise err("trace_not_found", trace=trace_id)
        return found

    # ------------------------------------------------------------------ finishing

    def _save_traces(self, job: Job, traces: list[dict[str, Any]]) -> list[str]:
        return [self.store.save_trace({**t, "job": job.id}) for t in traces]

    def _done(self, job: Job, raw: dict[str, Any]) -> dict[str, Any]:
        speed = raw.get("speed") or {}
        log.info(
            "judge %s (%s): %.1f simulated s in %.1f s, %.1fx real time",
            job.id,
            job.kind,
            speed.get("sim_s", 0.0),
            speed.get("wall_s", 0.0),
            speed.get("factor", 0.0),
        )
        ids = self._save_traces(job, raw.get("traces") or [])
        if job.kind == "challenge":
            return self._done_challenge(job, raw, ids)
        return self._done_duel(job, raw, ids)

    def _done_challenge(self, job: Job, raw: dict[str, Any], ids: list[str]) -> dict[str, Any]:
        seeds = []
        for s in raw["seeds"]:
            index = s.pop("trace", None)
            seeds.append({**s, "trace_id": ids[index] if index is not None else None})
        trace_id = next((s["trace_id"] for s in seeds if s["trace_id"]), None)
        entrant = job.meta["programs"][0]
        entry = {
            "job": job.id,
            "program": entrant["program"],
            "rev": entrant["rev"],
            "name": entrant["name"],
            "score": raw["score"],
            "passed": raw["passed"],
            "visible_only": bool(job.meta.get("visible_only")),
            "seeds": [{k: s[k] for k in ("seed", "hidden", "score", "passed")} for s in seeds],
            "trace_id": trace_id,
            "author": job.meta.get("author"),
            "t": self.fleet.wall(),
        }
        record = self.store.record(raw["challenge"], entry)
        return {
            "challenge": raw["challenge"],
            "score": raw["score"],
            "passed": raw["passed"],
            "metrics": raw["metrics"],
            "seeds": seeds,
            "trace_id": trace_id,
            "best": record["best"],
            "speed": raw.get("speed"),
        }

    def _done_duel(self, job: Job, raw: dict[str, Any], ids: list[str]) -> dict[str, Any]:
        ratings = self.store.ratings()
        current = {p["program"]: ratings.get(p["program"], ELO_START) for p in job.meta["programs"]}
        totals = {pid: 0.0 for pid in current}
        rounds = []
        for rnd in raw["rounds"]:
            index = rnd.pop("trace", None)
            rnd["trace_id"] = ids[index] if index is not None else None
            places = {a["program"]: a["place"] for a in rnd["aircraft"]}
            delta = elo_round(current, places)
            for pid, change in delta.items():
                current[pid] += change
                totals[pid] += change
            rounds.append(rnd)
        records = {
            row["program"]: {k: row[k] for k in ("rounds", "wins", "draws", "losses")}
            for row in raw["standings"]
        }
        names = {p["program"]: p["name"] for p in job.meta["programs"]}
        summary = {
            "job": job.id,
            "programs": list(current),
            "rounds": len(rounds),
            "seed": job.meta.get("seed"),
            "deltas": {pid: round(v, 1) for pid, v in totals.items()},
        }
        rows = self.store.apply_duel(summary, totals, records, names, ELO_START)
        ladder = [
            {
                "program": r["program"],
                "name": r["name"],
                "rating": r["rating"],
                "delta": round(totals[r["program"]], 1),
            }
            for r in rows
        ]
        ladder.sort(key=lambda r: -r["rating"])
        return {
            "rounds": rounds,
            "standings": raw["standings"],
            "ladder": ladder,
            "scenario": raw.get("scenario"),
            "speed": raw.get("speed"),
        }

    def _announce(self, job: Job) -> None:
        en, zh = self._detail(job)
        data = {
            "job": job.id,
            "kind": job.kind,
            "state": job.state,
            "challenge": job.meta.get("challenge"),
            "score": (job.result or {}).get("score"),
            "passed": (job.result or {}).get("passed"),
            "detail_en": en,
            "detail_zh": zh,
        }

        def emit() -> None:
            self.fleet.events.emit("judge_finished", data=data)

        try:
            self.fleet.post(emit)
        except RelayError:
            pass

    def _detail(self, job: Job) -> tuple[str, str]:
        names = ", ".join(p["name"] for p in job.meta.get("programs") or [])
        if job.kind == "challenge":
            c = challengemod.get(job.meta.get("challenge") or "")
            what_en = f"Evaluation of {names} on {c.name_en if c else job.meta.get('challenge')}"
            what_zh = f"{names} 的题目「{c.name_zh if c else job.meta.get('challenge')}」评测"
        else:
            n = len(job.meta.get("programs") or [])
            what_en = f"Duel of {n} programs over {job.meta.get('rounds')} rounds"
            what_zh = f"{n} 个程序 {job.meta.get('rounds')} 局对抗"
        if job.state == "done" and job.kind == "challenge" and job.result is not None:
            verdict_en = "passed" if job.result["passed"] else "not passed"
            verdict_zh = "通过" if job.result["passed"] else "未通过"
            return (
                f"{what_en} finished: score {job.result['score']}, {verdict_en}",
                f"{what_zh}完成：得分 {job.result['score']}，{verdict_zh}",
            )
        if job.state == "done":
            return f"{what_en} finished", f"{what_zh}完成"
        if job.state == "cancelled":
            return f"{what_en} was cancelled", f"{what_zh}已取消"
        error = job.error or {}
        return (
            f"{what_en} failed: {str(error.get('message_en') or 'unknown error').rstrip('.')}",
            f"{what_zh}失败：{str(error.get('message_zh') or '未知错误').rstrip('。')}",
        )


__all__ = ["JudgeService"]
