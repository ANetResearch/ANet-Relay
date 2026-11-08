"""Programs on disk (06 §2.5): ``<data_dir>/programs/<id>.json`` with the last 50 revisions, plus the
read-only built-in examples shipped in ``examples/`` (source ``<id>.py``, metadata ``<id>.json``).

A program record::

    {id, name, description, source, params, tests, created, updated, builtin,
     revisions: [{rev, source, params, author, note, t}]}

``source`` / ``params`` are those of the newest revision. Authors are ``operator`` or the agent session's
name. Every change to the source or the parameters adds a revision; name, description and tests change
in place. The store is thread-safe (API threads); without a data directory it keeps programs in memory.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from ..errors import err
from . import validator

log = logging.getLogger("relay.programs")

MAX_REVISIONS = 50
MAX_TESTS = 64
NAME_MAX = 48
DESCRIPTION_MAX = 2000
NOTE_MAX = 200
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"
BUILTIN_IDS = ("hover_trim", "liftoff", "patrol", "hunter_easy", "hunter_medium", "hunter_hard")


def _clean_text(value: Any, limit: int, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise err("invalid_request", reason=f"{field} must be text")
    return value.strip()[:limit]


def check_params(params: Any, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    """A ``params`` object: names known to PARAMS (when given) and plain values."""
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise err("invalid_request", reason="params must be an object")
    out = {}
    for key, value in params.items():
        if not isinstance(key, str) or not key.isidentifier() or key.startswith("_"):
            raise err("program_param_invalid", name=str(key), reason="not a parameter name")
        if defaults is not None and key not in defaults:
            raise err("program_param_invalid", name=key, reason="not in PARAMS")
        if defaults is not None:
            value = coerce_param(key, value, defaults[key], None)
        elif not isinstance(value, (bool, int, float, str)) or (isinstance(value, str) and len(value) > 32):
            raise err(
                "program_param_invalid", name=key, reason="must be a number, a boolean or a short string"
            )
        out[key] = value
    return out


def check_stored_params(params: Any, source: str) -> dict[str, Any]:
    """Stored parameter overrides: checked against the source's PARAMS and PARAM_RANGES when it validates
    (a source that does not validate yet keeps plain values; deployment checks them again)."""
    result = validator.validate(source)
    if not result.ok or params is None:
        return check_params(params)
    out = check_params(params)
    for key in out:
        if key not in result.params:
            raise err("program_param_invalid", name=key, reason="not in PARAMS")
    return {k: coerce_param(k, v, result.params[k], result.param_ranges.get(k)) for k, v in out.items()}


def coerce_param(name: str, value: Any, default: Any, bounds: list[float] | None) -> Any:
    """Type and range of a live parameter follow its PARAMS default (06 §2.6).

    Booleans stay booleans, strings stay strings of at most 32 characters, integers stay integral and
    numbers stay finite. The range is ``PARAM_RANGES[name]`` when the program gives one, otherwise
    ``|value| <= max(10 * |default|, 1)`` (10 for integers).
    """
    import math

    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise err("program_param_invalid", name=name, reason="must be true or false")
        return value
    if isinstance(default, str):
        if not isinstance(value, str) or len(value) > 32:
            raise err("program_param_invalid", name=name, reason="must be text of at most 32 characters")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise err("program_param_invalid", name=name, reason="must be a finite number")
    if isinstance(default, int) and float(value) != int(value):
        raise err("program_param_invalid", name=name, reason="must be a whole number")
    if bounds is not None:
        low, high = bounds
    else:
        span = max(10.0 * abs(float(default)), 10.0 if isinstance(default, int) else 1.0)
        low, high = -span, span
    if not low <= float(value) <= high:
        raise err("program_param_invalid", name=name, reason=f"must be within {low:g} to {high:g}")
    return int(value) if isinstance(default, int) else float(value)


def check_tests(tests: Any) -> list[dict[str, Any]]:
    """Unit tests (06 §3.1): ``[{name, obs, mem?, state?, params?, expect}]``."""
    if tests is None:
        return []
    if not isinstance(tests, list) or len(tests) > MAX_TESTS:
        raise err("invalid_request", reason=f"tests must be a list of at most {MAX_TESTS}")
    out = []
    for i, test in enumerate(tests, 1):
        if not isinstance(test, dict):
            raise err("invalid_request", reason=f"test {i} must be an object")
        expect = test.get("expect") or {}
        if not isinstance(expect, dict):
            raise err("invalid_request", reason=f"test {i}: expect must be an object")
        for key, want in expect.items():
            if key in ("roll", "pitch", "yaw", "throttle"):
                if (
                    not isinstance(want, list)
                    or len(want) != 2
                    or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in want)
                ):
                    raise err("invalid_request", reason=f"test {i}: expect.{key} must be [low, high]")
            elif key == "fire":
                if not isinstance(want, bool):
                    raise err("invalid_request", reason=f"test {i}: expect.fire must be true or false")
            elif key == "state":
                if not isinstance(want, str):
                    raise err("invalid_request", reason=f"test {i}: expect.state must be text")
            else:
                raise err("invalid_request", reason=f"test {i}: unknown expectation {key}")
        for key in ("obs", "mem", "params"):
            if test.get(key) is not None and not isinstance(test[key], dict):
                raise err("invalid_request", reason=f"test {i}: {key} must be an object")
        if test.get("state") is not None and not isinstance(test["state"], str):
            raise err("invalid_request", reason=f"test {i}: state must be text")
        item = {
            "name": _clean_text(test.get("name") or f"test {i}", 64, "name"),
            "obs": dict(test.get("obs") or {}),
            "expect": dict(expect),
        }
        for key in ("mem", "params", "state"):
            if test.get(key) is not None:
                item[key] = test[key]
        out.append(item)
    if len(json.dumps(out)) > 256 * 1024:
        raise err("invalid_request", reason="tests are larger than 256 KB")
    return out


def _check_source(source: Any) -> str:
    if not isinstance(source, str):
        raise err("invalid_request", reason="source must be text")
    if len(source.encode("utf-8", "surrogatepass")) > validator.MAX_SOURCE_BYTES:
        raise err("program_invalid", reason="source is larger than 64 KB")
    return source


def apply_patch(source: str, patch: Any) -> str:
    """Line-based edits ``[{start, end, text}]`` (1-based, inclusive; ``end = start - 1`` inserts)."""
    if not isinstance(patch, list) or not patch:
        raise err("invalid_request", reason="patch must be a non-empty list")
    lines = source.split("\n")
    edits = []
    for i, edit in enumerate(patch, 1):
        if not isinstance(edit, dict):
            raise err("invalid_request", reason=f"patch {i} must be an object")
        start, end, text = edit.get("start"), edit.get("end"), edit.get("text", "")
        if (
            isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
            or not isinstance(text, str)
        ):
            raise err("invalid_request", reason=f"patch {i} needs integer start, end and text")
        if not (1 <= start <= len(lines) + 1 and start - 1 <= end <= len(lines)):
            raise err("invalid_request", reason=f"patch {i}: lines {start}-{end} are outside the source")
        edits.append((start, end, text))
    edits.sort(key=lambda e: e[0], reverse=True)
    last_start = None
    for start, end, text in edits:
        if last_start is not None and end >= last_start:
            raise err("invalid_request", reason="patch ranges overlap")
        last_start = start
        lines[start - 1 : end] = text.split("\n") if text != "" else []
    return "\n".join(lines)


class ProgramStore:
    def __init__(self, data_dir: Path | None) -> None:
        self.dir = data_dir / "programs" if data_dir is not None else None
        self._lock = threading.RLock()
        self._programs: dict[str, dict[str, Any]] = {}
        self._builtins = self._load_builtins()
        self._load()

    # ------------------------------------------------------------------ loading

    def _load_builtins(self) -> dict[str, dict[str, Any]]:
        out = {}
        for pid in BUILTIN_IDS:
            try:
                source = (EXAMPLES_DIR / f"{pid}.py").read_text("utf-8")
                meta = json.loads((EXAMPLES_DIR / f"{pid}.json").read_text("utf-8"))
            except (OSError, ValueError) as exc:
                log.warning("built-in program %s unavailable: %s", pid, exc)
                continue
            result = validator.validate(source)
            out[pid] = {
                "id": pid,
                "name": meta.get("name", pid),
                "name_zh": meta.get("name_zh", meta.get("name", pid)),
                "description": meta.get("description", ""),
                "description_zh": meta.get("description_zh", ""),
                "source": source,
                "params": dict(result.params),
                "tests": meta.get("tests", []),
                "created": 0.0,
                "updated": 0.0,
                "builtin": True,
                "revisions": [
                    {
                        "rev": 1,
                        "source": source,
                        "params": dict(result.params),
                        "author": "relay",
                        "note": "built-in",
                        "t": 0.0,
                    }
                ],
            }
        return out

    def _load(self) -> None:
        if self.dir is None or not self.dir.is_dir():
            return
        for path in sorted(self.dir.glob("*.json")):
            if path.name in ("results.json", "ladder.json"):
                continue
            try:
                data = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError) as exc:
                log.warning("program file %s unreadable: %s", path.name, exc)
                continue
            if isinstance(data, dict) and ID_RE.match(str(data.get("id", ""))) and data["id"] == path.stem:
                data.setdefault("revisions", [])
                data["builtin"] = False
                self._programs[data["id"]] = data

    def _save(self, program: dict[str, Any]) -> None:
        if self.dir is None:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{program['id']}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(program, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(path)

    # ------------------------------------------------------------------ queries

    def _find(self, pid: str) -> dict[str, Any]:
        program = self._builtins.get(pid) or self._programs.get(pid)
        if program is None:
            raise err("program_not_found", program=pid)
        return program

    @staticmethod
    def summary(program: dict[str, Any]) -> dict[str, Any]:
        revisions = program.get("revisions") or []
        return {
            "id": program["id"],
            "name": program["name"],
            "name_zh": program.get("name_zh") or program["name"],
            "description": program.get("description", ""),
            "description_zh": program.get("description_zh") or program.get("description", ""),
            "builtin": bool(program.get("builtin")),
            "rev": revisions[-1]["rev"] if revisions else 0,
            "author": revisions[-1]["author"] if revisions else None,
            "created": program.get("created", 0.0),
            "updated": program.get("updated", 0.0),
            "test_count": len(program.get("tests") or []),
        }

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            builtins = [self.summary(p) for p in self._builtins.values()]
            mine = sorted((self.summary(p) for p in self._programs.values()), key=lambda s: -s["updated"])
            return builtins + mine

    def get(self, pid: str) -> dict[str, Any]:
        with self._lock:
            program = copy.deepcopy(self._find(pid))
        out = {k: v for k, v in program.items() if k != "revisions"}
        out.update(self.summary(program))
        return out

    def revisions(self, pid: str) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(list(reversed(self._find(pid).get("revisions") or [])))

    def revision(self, pid: str, rev: int | None) -> dict[str, Any]:
        """``{rev, source, params, ...}`` of one revision (the newest when ``rev`` is None)."""
        with self._lock:
            revisions = self._find(pid).get("revisions") or []
            if not revisions:
                raise err("revision_not_found", program=pid, rev=rev or 0)
            if rev is None:
                return copy.deepcopy(revisions[-1])
            for entry in revisions:
                if entry["rev"] == rev:
                    return copy.deepcopy(entry)
        raise err("revision_not_found", program=pid, rev=rev)

    # ------------------------------------------------------------------ changes

    def _new_id(self, name: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "program"
        while True:
            pid = f"{base}-{secrets.token_hex(3)}"
            if pid not in self._programs and pid not in self._builtins:
                return pid

    def create(self, body: dict[str, Any], author: str, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        copy_of = body.get("copy_of")
        with self._lock:
            if copy_of is not None:
                origin = self._find(str(copy_of))
                source = body.get("source", origin["source"])
                params = body.get("params", origin.get("params") or {})
                tests = body.get("tests", origin.get("tests") or [])
                name = body.get("name") or f"{origin['name']} copy"
                description = body.get("description", origin.get("description", ""))
            else:
                source = body.get("source", "")
                params = body.get("params") or {}
                tests = body.get("tests") or []
                name = body.get("name")
                description = body.get("description", "")
            name = _clean_text(name, NAME_MAX, "name")
            if not name:
                raise err("invalid_request", reason="name is required")
            source = _check_source(source)
            program = {
                "id": self._new_id(name),
                "name": name,
                "description": _clean_text(description, DESCRIPTION_MAX, "description"),
                "source": source,
                "params": check_stored_params(params, source),
                "tests": check_tests(tests),
                "created": now,
                "updated": now,
                "builtin": False,
                "revisions": [],
            }
            note = _clean_text(body.get("note"), NOTE_MAX, "note") or ("copied" if copy_of else "created")
            self._add_revision(program, author, note, now)
            self._programs[program["id"]] = program
            self._save(program)
            return self.get(program["id"])

    def _add_revision(self, program: dict[str, Any], author: str, note: str, now: float) -> None:
        revisions = program["revisions"]
        rev = (revisions[-1]["rev"] + 1) if revisions else 1
        revisions.append(
            {
                "rev": rev,
                "source": program["source"],
                "params": dict(program["params"]),
                "author": author,
                "note": note,
                "t": now,
            }
        )
        del revisions[:-MAX_REVISIONS]

    def update(self, pid: str, body: dict[str, Any], author: str, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        with self._lock:
            program = self._find(pid)
            if program.get("builtin"):
                raise err("program_readonly", program=pid)
            source = program["source"]
            if body.get("patch") is not None:
                if body.get("source") is not None:
                    raise err("invalid_request", reason="give source or patch, not both")
                source = apply_patch(source, body["patch"])
            elif body.get("source") is not None:
                source = body["source"]
            source = _check_source(source)
            params = (
                check_stored_params(body["params"], source)
                if body.get("params") is not None
                else program["params"]
            )
            changed = source != program["source"] or params != program["params"]
            if body.get("name") is not None:
                name = _clean_text(body["name"], NAME_MAX, "name")
                if not name:
                    raise err("invalid_request", reason="name is required")
                program["name"] = name
            if body.get("description") is not None:
                program["description"] = _clean_text(body["description"], DESCRIPTION_MAX, "description")
            if body.get("tests") is not None:
                program["tests"] = check_tests(body["tests"])
            program["source"] = source
            program["params"] = params
            if changed:
                self._add_revision(
                    program, author, _clean_text(body.get("note"), NOTE_MAX, "note") or "edited", now
                )
            program["updated"] = now
            self._save(program)
            return self.get(pid)

    def delete(self, pid: str) -> dict[str, Any]:
        with self._lock:
            program = self._find(pid)
            if program.get("builtin"):
                raise err("program_readonly", program=pid)
            self._programs.pop(pid, None)
            if self.dir is not None:
                path = self.dir / f"{pid}.json"
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            return {"id": pid, "deleted": True}


__all__ = [
    "BUILTIN_IDS",
    "MAX_REVISIONS",
    "ProgramStore",
    "apply_patch",
    "check_params",
    "check_tests",
    "coerce_param",
]
