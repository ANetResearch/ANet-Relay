"""Flight records in SQLite (03 §11, R-50..R-52).

One record session per vehicle per time online: 5 Hz samples, every event,
control segments and ring-course results. WAL mode; samples and events are
buffered and written once per second by a writer thread. Reads open their own
connection so they never wait for the writer.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS labels (host TEXT PRIMARY KEY, label TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS record_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id TEXT NOT NULL, kind TEXT NOT NULL, label TEXT,
    autopilot TEXT, started_at REAL NOT NULL, ended_at REAL);
CREATE TABLE IF NOT EXISTS samples (
    session_id INTEGER NOT NULL, t REAL NOT NULL, armed INTEGER, mode TEXT, roll REAL, pitch REAL, yaw REAL,
    x REAL, y REAL, z REAL, alt REAL, climb REAL, voltage REAL, current REAL, remaining REAL, throttle REAL,
    controller TEXT, s_roll REAL, s_pitch REAL, s_yaw REAL, s_throttle REAL, s_source TEXT);
CREATE INDEX IF NOT EXISTS samples_session ON samples (session_id, t);
CREATE TABLE IF NOT EXISTS events (
    session_id INTEGER NOT NULL, seq INTEGER NOT NULL, t REAL NOT NULL, kind TEXT NOT NULL, data_json TEXT);
CREATE INDEX IF NOT EXISTS events_session ON events (session_id, seq);
CREATE TABLE IF NOT EXISTS control_segments (
    session_id INTEGER NOT NULL, start REAL NOT NULL, "end" REAL, controller TEXT NOT NULL, who TEXT);
CREATE TABLE IF NOT EXISTS course_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, vehicle_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    started_at REAL, finished_at REAL, state TEXT NOT NULL, total_s REAL, splits_json TEXT, controller TEXT);
CREATE TABLE IF NOT EXISTS calibration (host TEXT PRIMARY KEY, signs_json TEXT NOT NULL, hover REAL NOT NULL);
"""
# Bench procedure reports (WP-B, 05 §4.4): one row per run, linked to the aircraft's host across restarts.
TEST_RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS test_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id TEXT NOT NULL, host TEXT, procedure TEXT NOT NULL,
    state TEXT NOT NULL, verdict TEXT, started REAL NOT NULL, ended REAL, steps_json TEXT, params_json TEXT,
    session_id INTEGER, meta_json TEXT);
CREATE INDEX IF NOT EXISTS test_runs_vehicle ON test_runs (vehicle_id, started);
CREATE INDEX IF NOT EXISTS test_runs_host ON test_runs (host, started);
"""
TEST_RUN_FIELDS = ("state", "verdict", "ended", "steps_json", "params_json", "session_id", "meta_json")

SAMPLE_COLUMNS = (
    "t",
    "armed",
    "mode",
    "roll",
    "pitch",
    "yaw",
    "x",
    "y",
    "z",
    "alt",
    "climb",
    "voltage",
    "current",
    "remaining",
    "throttle",
    "controller",
    "s_roll",
    "s_pitch",
    "s_yaw",
    "s_throttle",
    "s_source",
)
SAMPLE_ADDED = (
    ("s_roll", "REAL"),
    ("s_pitch", "REAL"),
    ("s_yaw", "REAL"),
    ("s_throttle", "REAL"),
    ("s_source", "TEXT"),
)
ALERT_KINDS = ("crashed", "link_lost", "battery_critical", "battery_warn", "airborne_no_controller")


class History:
    def __init__(self, path: Path | None, retention_days: int = 90) -> None:
        """``path=None`` keeps everything in a private in-memory database (tests)."""
        self.path = path
        self._uri = f"file:relay-{id(self)}?mode=memory&cache=shared" if path is None else str(path)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = self._connect()
        self._conn.executescript(SCHEMA)
        self._conn.executescript(TEST_RUNS_SCHEMA)
        self._migrate()
        self._close_stale_test_runs()
        self._conn.commit()
        self._lock = threading.Lock()
        self._samples: list[tuple] = []
        self._events: list[tuple] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.prune(retention_days)

    def _connect(self) -> sqlite3.Connection:
        if self.path is None:
            conn = sqlite3.connect(self._uri, uri=True, check_same_thread=False)
        else:
            conn = sqlite3.connect(self._uri, check_same_thread=False, timeout=5.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _migrate(self) -> None:
        """Add the stick columns (04 §4) to a database written by v0.2.0; idempotent."""
        have = {r[1] for r in self._conn.execute("PRAGMA table_info(samples)")}
        for name, kind in SAMPLE_ADDED:
            if name not in have:
                self._conn.execute(f"ALTER TABLE samples ADD COLUMN {name} {kind}")

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="relay-history", daemon=True)
            self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        self.flush()
        with self._lock:
            self._conn.close()

    def _run(self) -> None:
        while not self._stop.wait(1.0):
            try:
                self.flush()
            except sqlite3.Error:
                pass

    def flush(self) -> None:
        with self._lock:
            samples, self._samples = self._samples, []
            events, self._events = self._events, []
            if not samples and not events:
                return
            placeholders = ",".join("?" * (len(SAMPLE_COLUMNS) + 1))
            self._conn.executemany(
                f"INSERT INTO samples (session_id,{','.join(SAMPLE_COLUMNS)}) VALUES ({placeholders})",
                samples,
            )
            self._conn.executemany("INSERT INTO events VALUES (?,?,?,?,?)", events)
            self._conn.commit()

    def _write(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return int(cursor.lastrowid or 0)

    def prune(self, retention_days: int) -> int:
        cutoff = time.time() - retention_days * 86400.0
        with self._lock:
            ids = [
                r[0]
                for r in self._conn.execute("SELECT id FROM record_sessions WHERE started_at < ?", (cutoff,))
            ]
            for table in ("samples", "events", "control_segments", "course_runs"):
                self._conn.executemany(f"DELETE FROM {table} WHERE session_id = ?", [(i,) for i in ids])
            self._conn.executemany("DELETE FROM record_sessions WHERE id = ?", [(i,) for i in ids])
            self._conn.execute("DELETE FROM test_runs WHERE started < ?", (cutoff,))
            self._conn.commit()
        return len(ids)

    # ------------------------------------------------------------------ writes

    def set_label(self, host: str, label: str) -> None:
        self._write(
            "INSERT INTO labels VALUES (?,?) ON CONFLICT(host) DO UPDATE SET label=excluded.label",
            (host, label),
        )

    def labels(self) -> dict[str, str]:
        with self._lock:
            return {r["host"]: r["label"] for r in self._conn.execute("SELECT host, label FROM labels")}

    def set_calibration(self, host: str, signs: dict[str, int], hover: float) -> None:
        self._write(
            "INSERT INTO calibration VALUES (?,?,?) ON CONFLICT(host) DO UPDATE SET "
            "signs_json=excluded.signs_json, hover=excluded.hover",
            (host, json.dumps(signs), hover),
        )

    def calibration(self, host: str) -> tuple[dict[str, int], float] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT signs_json, hover FROM calibration WHERE host = ?", (host,)
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["signs_json"]), float(row["hover"])

    def start_session(self, vehicle_id: str, kind: str, label: str, autopilot: str | None, t: float) -> int:
        return self._write(
            "INSERT INTO record_sessions (vehicle_id, kind, label, autopilot, started_at) VALUES (?,?,?,?,?)",
            (vehicle_id, kind, label, autopilot, t),
        )

    def update_session(self, session_id: int, label: str | None = None, autopilot: str | None = None) -> None:
        if label is not None:
            self._write("UPDATE record_sessions SET label = ? WHERE id = ?", (label, session_id))
        if autopilot is not None:
            self._write("UPDATE record_sessions SET autopilot = ? WHERE id = ?", (autopilot, session_id))

    def end_session(self, session_id: int, t: float) -> None:
        self.flush()
        self._write("UPDATE record_sessions SET ended_at = ? WHERE id = ?", (t, session_id))

    def sample(self, session_id: int, row: dict[str, Any]) -> None:
        values = tuple(row.get(c) for c in SAMPLE_COLUMNS)
        with self._lock:
            self._samples.append((session_id, *values))

    def event(self, session_id: int, event: dict[str, Any]) -> None:
        payload = json.dumps(
            {"data": event["data"], "text_en": event["text_en"], "text_zh": event["text_zh"]}
        )
        with self._lock:
            self._events.append((session_id, event["seq"], event["t"], event["kind"], payload))

    def segment(self, session_id: int, start: float, end: float, controller: str, who: str | None) -> None:
        self._write(
            "INSERT INTO control_segments VALUES (?,?,?,?,?)", (session_id, start, end, controller, who)
        )

    def course_run(self, record: dict[str, Any]) -> int:
        return self._write(
            "INSERT INTO course_runs (session_id, vehicle_id, course_id, started_at, finished_at, state, "
            "total_s, "
            "splits_json, controller) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                record.get("session_id"),
                record["vehicle_id"],
                record["course_id"],
                record.get("started_at"),
                record.get("finished_at"),
                record["state"],
                record.get("total_s"),
                json.dumps(record.get("splits") or []),
                record.get("controller"),
            ),
        )

    # ------------------------------------------------------------------ reads

    def _read(self) -> sqlite3.Connection:
        return self._connect()

    def best_course(self, course_id: str) -> float | None:
        conn = self._read()
        try:
            row = conn.execute(
                "SELECT MIN(total_s) FROM course_runs WHERE course_id = ? AND state = 'finished'",
                (course_id,),
            ).fetchone()
        finally:
            conn.close()
        return None if row is None or row[0] is None else float(row[0])

    def list_sessions(
        self, limit: int = 200, offset: int = 0, vehicle: str | None = None
    ) -> list[dict[str, Any]]:
        self.flush()
        conn = self._read()
        try:
            where, params = ("WHERE vehicle_id = ?", [vehicle]) if vehicle else ("", [])
            rows = conn.execute(
                f"SELECT * FROM record_sessions {where} ORDER BY started_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            out = []
            for r in rows:
                stats = conn.execute(
                    "SELECT MAX(alt), COUNT(*), SUM(armed) FROM samples WHERE session_id = ?", (r["id"],)
                ).fetchone()
                best = conn.execute(
                    "SELECT MIN(total_s), COUNT(*) FROM course_runs "
                    "WHERE session_id = ? AND state='finished'",
                    (r["id"],),
                ).fetchone()
                alerts = conn.execute(
                    "SELECT COUNT(*) FROM events WHERE session_id = ? "
                    f"AND kind IN ({','.join('?' * len(ALERT_KINDS))})",
                    (r["id"], *ALERT_KINDS),
                ).fetchone()
                end = r["ended_at"]
                out.append(
                    {
                        "id": r["id"],
                        "vehicle_id": r["vehicle_id"],
                        "kind": r["kind"],
                        "label": r["label"],
                        "autopilot": r["autopilot"],
                        "started_at": r["started_at"],
                        "ended_at": end,
                        "live": end is None,
                        "duration_s": round((end or time.time()) - r["started_at"], 1),
                        "max_alt_m": None if stats[0] is None else round(stats[0], 2),
                        "samples": stats[1],
                        "armed_s": round((stats[2] or 0) / 5.0, 1),
                        "course_best_s": best[0],
                        "course_finished": best[1],
                        "alerts": alerts[0],
                    }
                )
            return out
        finally:
            conn.close()

    def session_detail(self, session_id: int) -> dict[str, Any] | None:
        self.flush()
        conn = self._read()
        try:
            row = conn.execute("SELECT * FROM record_sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                return None
            samples = conn.execute(
                f"SELECT {','.join(SAMPLE_COLUMNS)} FROM samples WHERE session_id = ? ORDER BY t",
                (session_id,),
            ).fetchall()
            columns: dict[str, list[Any]] = {c: [] for c in SAMPLE_COLUMNS}
            for s in samples:
                for c in SAMPLE_COLUMNS:
                    columns[c].append(s[c])
            events = []
            for e in conn.execute(
                "SELECT seq, t, kind, data_json FROM events WHERE session_id = ? ORDER BY seq", (session_id,)
            ):
                payload = json.loads(e["data_json"] or "{}")
                events.append(
                    {
                        "seq": e["seq"],
                        "t": e["t"],
                        "kind": e["kind"],
                        "data": payload.get("data", {}),
                        "text_en": payload.get("text_en", ""),
                        "text_zh": payload.get("text_zh", ""),
                    }
                )
            segments = [
                {"start": s["start"], "end": s["end"], "controller": s["controller"], "who": s["who"]}
                for s in conn.execute(
                    'SELECT start, "end", controller, who FROM control_segments '
                    "WHERE session_id = ? ORDER BY start",
                    (session_id,),
                )
            ]
            runs = [
                {
                    "id": c["id"],
                    "course_id": c["course_id"],
                    "started_at": c["started_at"],
                    "finished_at": c["finished_at"],
                    "state": c["state"],
                    "total_s": c["total_s"],
                    "splits": json.loads(c["splits_json"] or "[]"),
                    "controller": c["controller"],
                }
                for c in conn.execute(
                    "SELECT * FROM course_runs WHERE session_id = ? ORDER BY id", (session_id,)
                )
            ]
        finally:
            conn.close()
        alts = [a for a in columns["alt"] if a is not None]
        volts = [v for v in columns["voltage"] if v is not None]
        finished = [r["total_s"] for r in runs if r["state"] == "finished" and r["total_s"] is not None]
        summary = {
            "duration_s": round((row["ended_at"] or time.time()) - row["started_at"], 1),
            "armed_s": round(sum(1 for a in columns["armed"] if a) / 5.0, 1),
            "max_alt_m": round(max(alts), 2) if alts else None,
            "voltage_start": volts[0] if volts else None,
            "voltage_end": volts[-1] if volts else None,
            "possessions": sum(1 for e in events if e["kind"] == "possession_started"),
            "handbacks": sum(1 for e in events if e["kind"] == "relay_handback"),
            "course_best_s": min(finished) if finished else None,
        }
        return {
            "session": dict(row),
            "summary": summary,
            "samples": columns,
            "events": events,
            "control_segments": segments,
            "course_runs": runs,
        }

    def export(self, session_id: int, fmt: str) -> tuple[str, str] | None:
        """(content type, body) for CSV (samples) or JSON (everything)."""
        detail = self.session_detail(session_id)
        if detail is None:
            return None
        if fmt == "json":
            return "application/json", json.dumps(detail, ensure_ascii=False, indent=2)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(SAMPLE_COLUMNS)
        cols = detail["samples"]
        for i in range(len(cols["t"])):
            writer.writerow([cols[c][i] for c in SAMPLE_COLUMNS])
        return "text/csv", buffer.getvalue()

    # ------------------------------------------------------------------ bench test runs (WP-B, 05 §4.4)

    def _close_stale_test_runs(self) -> None:
        """A run that was active when Relay stopped can never finish: mark it aborted."""
        self._conn.execute(
            "UPDATE test_runs SET state = 'aborted', ended = COALESCE(ended, started) "
            "WHERE state IN ('running', 'waiting')"
        )

    def test_run_start(self, record: dict[str, Any]) -> int:
        return self._write(
            "INSERT INTO test_runs (vehicle_id, host, procedure, state, verdict, started, ended, steps_json, "
            "params_json, session_id, meta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                record["vehicle_id"],
                record.get("host"),
                record["procedure"],
                record.get("state", "running"),
                record.get("verdict"),
                record["started"],
                record.get("ended"),
                json.dumps(record.get("steps") or [], ensure_ascii=False),
                json.dumps(record.get("params") or {}, ensure_ascii=False),
                record.get("session_id"),
                json.dumps(record.get("meta") or {}, ensure_ascii=False),
            ),
        )

    def test_run_update(self, run_id: int, record: dict[str, Any]) -> None:
        values = {
            "state": record.get("state"),
            "verdict": record.get("verdict"),
            "ended": record.get("ended"),
            "steps_json": json.dumps(record.get("steps") or [], ensure_ascii=False),
            "params_json": json.dumps(record.get("params") or {}, ensure_ascii=False),
            "session_id": record.get("session_id"),
            "meta_json": json.dumps(record.get("meta") or {}, ensure_ascii=False),
        }
        sets = ", ".join(f"{k} = ?" for k in TEST_RUN_FIELDS)
        params = (*(values[k] for k in TEST_RUN_FIELDS), run_id)
        self._write(f"UPDATE test_runs SET {sets} WHERE id = ?", params)

    @staticmethod
    def _test_run_row(r: sqlite3.Row, full: bool) -> dict[str, Any]:
        meta = json.loads(r["meta_json"] or "{}")
        steps = json.loads(r["steps_json"] or "[]")
        out = {
            **meta,
            "run_id": r["id"],
            "vehicle": r["vehicle_id"],
            "host": r["host"],
            "procedure": r["procedure"],
            "state": r["state"],
            "verdict": r["verdict"],
            "started": r["started"],
            "ended": r["ended"],
            "session_id": r["session_id"],
        }
        if full:
            out["steps"] = steps
            out["params"] = json.loads(r["params_json"] or "{}")
        else:
            out["counts"] = {
                v: sum(1 for s in steps if s.get("verdict") == v) for v in ("pass", "warn", "fail", "skipped")
            }
            out["total"] = len(steps)
        return out

    def test_runs(self, vehicle: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Newest first. ``vehicle`` matches the aircraft id or its host (reports survive a restart)."""
        conn = self._read()
        try:
            if vehicle:
                rows = conn.execute(
                    "SELECT * FROM test_runs WHERE vehicle_id = ? OR host = ? ORDER BY started DESC, id DESC "
                    "LIMIT ?",
                    (vehicle, vehicle, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM test_runs ORDER BY started DESC, id DESC LIMIT ?", (limit,)
                ).fetchall()
            return [self._test_run_row(r, full=False) for r in rows]
        finally:
            conn.close()

    def test_run(self, run_id: int) -> dict[str, Any] | None:
        conn = self._read()
        try:
            row = conn.execute("SELECT * FROM test_runs WHERE id = ?", (run_id,)).fetchone()
        finally:
            conn.close()
        return None if row is None else self._test_run_row(row, full=True)
