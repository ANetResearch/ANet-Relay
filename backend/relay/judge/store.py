"""Evaluation records on disk (06 §3.2, §3.4), next to the programs in ``<data_dir>/programs/``:

* ``results.json``: per challenge the best full submission and the last ``HISTORY_KEEP`` submissions::

      {"version": 1, "challenges": {"hover": {"best": entry | null, "history": [entry, ...]}}}
      entry = {job, program, rev, score, passed, visible_only, seeds, t}

* ``ladder.json``: the Elo ladder and the last ``DUELS_KEEP`` duels::

      {"version": 1, "ratings": {program: {program, name, rating, games, wins, draws, losses, updated}},
       "duels": [{job, programs, rounds, seed, t, deltas: {program: change}}]}

* ``traces/<id>.json.gz``: replay traces (gzip JSON, shape in ``relay.judge``), the newest ``TRACE_KEEP``.

Without a data directory everything stays in memory. Writes go through a temporary file and a rename.
"""

from __future__ import annotations

import copy
import gzip
import json
import logging
import re
import secrets
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

log = logging.getLogger("relay.judge")

HISTORY_KEEP = 50
DUELS_KEEP = 50
TRACE_KEEP = 300
TRACE_ID_RE = re.compile(r"^tr-[0-9a-f]{16}$")
VERSION = 1


def _read(path: Path | None, default: dict[str, Any]) -> dict[str, Any]:
    if path is None or not path.exists():
        return copy.deepcopy(default)
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("%s unreadable, starting over: %s", path.name, exc)
        return copy.deepcopy(default)
    return data if isinstance(data, dict) else copy.deepcopy(default)


def _write(path: Path | None, data: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(path)


class JudgeStore:
    def __init__(self, data_dir: Path | None, wall: Any = time.time) -> None:
        self.dir = data_dir / "programs" if data_dir is not None else None
        self.wall = wall
        self._lock = threading.RLock()
        self.results = _read(self._path("results.json"), {"version": VERSION, "challenges": {}})
        self.results.setdefault("challenges", {})
        self.ladder = _read(self._path("ladder.json"), {"version": VERSION, "ratings": {}, "duels": []})
        self.ladder.setdefault("ratings", {})
        self.ladder.setdefault("duels", [])
        self._traces: OrderedDict[str, dict[str, Any]] = OrderedDict()  # in memory without a data dir

    def _path(self, name: str) -> Path | None:
        return None if self.dir is None else self.dir / name

    # ------------------------------------------------------------------ results

    def record(self, challenge: str, entry: dict[str, Any]) -> dict[str, Any]:
        """Add a submission; a full one (not ``visible_only``) may become the best. Returns the record."""
        with self._lock:
            rec = self.results["challenges"].setdefault(challenge, {"best": None, "history": []})
            rec["history"] = [*rec.get("history", []), entry][-HISTORY_KEEP:]
            best = rec.get("best")
            if not entry.get("visible_only") and (
                best is None
                or (entry["passed"], entry["score"])
                > (bool(best.get("passed")), float(best.get("score") or 0))
            ):
                rec["best"] = entry
            _write(self._path("results.json"), self.results)
            return copy.deepcopy(rec)

    def challenge_record(self, challenge: str) -> dict[str, Any]:
        with self._lock:
            rec = self.results["challenges"].get(challenge) or {"best": None, "history": []}
            return copy.deepcopy(rec)

    # ------------------------------------------------------------------ ladder

    def ratings(self) -> dict[str, float]:
        with self._lock:
            return {pid: float(row.get("rating", 0.0)) for pid, row in self.ladder["ratings"].items()}

    def apply_duel(
        self,
        duel: dict[str, Any],
        deltas: dict[str, float],
        records: dict[str, dict[str, int]],
        names: dict[str, str],
        start: float,
    ) -> list[dict[str, Any]]:
        """Fold one duel into the ladder: rating changes, win / draw / loss counts; returns the rows."""
        with self._lock:
            now = self.wall()
            rows = self.ladder["ratings"]
            for pid, change in deltas.items():
                row = rows.get(pid)
                if row is None:
                    row = rows[pid] = {
                        "program": pid,
                        "name": names.get(pid, pid),
                        "rating": start,
                        "games": 0,
                        "wins": 0,
                        "draws": 0,
                        "losses": 0,
                        "updated": now,
                    }
                row["rating"] = round(float(row["rating"]) + change, 1)
                rec = records.get(pid, {})
                row["games"] += rec.get("rounds", 0)
                row["wins"] += rec.get("wins", 0)
                row["draws"] += rec.get("draws", 0)
                row["losses"] += rec.get("losses", 0)
                row["name"] = names.get(pid, row.get("name", pid))
                row["updated"] = now
            self.ladder["duels"] = [*self.ladder["duels"], {**duel, "t": now}][-DUELS_KEEP:]
            _write(self._path("ladder.json"), self.ladder)
            return [copy.deepcopy(rows[pid]) for pid in deltas]

    def ladder_view(self) -> dict[str, Any]:
        with self._lock:
            rows = sorted(self.ladder["ratings"].values(), key=lambda r: (-float(r["rating"]), r["program"]))
            ranked = [{**copy.deepcopy(r), "rank": i + 1} for i, r in enumerate(rows)]
            return {"ladder": ranked, "duels": copy.deepcopy(self.ladder["duels"][-20:])}

    # ------------------------------------------------------------------ traces

    def _trace_dir(self) -> Path | None:
        return None if self.dir is None else self.dir / "traces"

    def save_trace(self, trace: dict[str, Any]) -> str:
        tid = "tr-" + secrets.token_hex(8)
        trace = {"id": tid, "created": self.wall(), **trace}
        folder = self._trace_dir()
        with self._lock:
            if folder is None:
                self._traces[tid] = trace
                while len(self._traces) > TRACE_KEEP:
                    self._traces.popitem(last=False)
                return tid
            folder.mkdir(parents=True, exist_ok=True)
            data = json.dumps(trace, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            tmp = folder / f"{tid}.json.gz.tmp"
            tmp.write_bytes(gzip.compress(data, compresslevel=6, mtime=0))
            tmp.replace(folder / f"{tid}.json.gz")
            self._prune(folder)
        return tid

    def _prune(self, folder: Path) -> None:
        files = sorted(folder.glob("tr-*.json.gz"), key=lambda p: p.stat().st_mtime)
        for path in files[:-TRACE_KEEP] if len(files) > TRACE_KEEP else []:
            try:
                path.unlink()
            except OSError:
                pass

    def trace(self, tid: str) -> dict[str, Any] | None:
        if not TRACE_ID_RE.match(tid or ""):
            return None
        folder = self._trace_dir()
        with self._lock:
            if folder is None:
                found = self._traces.get(tid)
                return copy.deepcopy(found) if found is not None else None
            path = folder / f"{tid}.json.gz"
            try:
                return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
            except (OSError, ValueError, EOFError):
                return None


__all__ = ["DUELS_KEEP", "HISTORY_KEEP", "TRACE_KEEP", "JudgeStore"]
