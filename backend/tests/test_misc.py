"""Alerts, events, topology, sessions and history (03 §10.4, §10.5, §11)."""

import time

from relay.alerts import AlertBook, Condition
from relay.events import TEMPLATES, EventBus
from relay.history import History
from relay.sessions import SessionManager
from relay.topology import build


def test_alert_lifecycle():
    book = AlertBook(lambda: 100.0)
    new = book.sync([Condition("link_lost", "warn", "v1", {"vehicle": "Q1", "age": "6"})])
    assert new[0]["id"] == "link_lost:v1" and "Q1" in new[0]["text_en"]
    assert book.ack("link_lost:v1")
    assert book.sync([Condition("link_lost", "warn", "v1", {"vehicle": "Q1", "age": "7"})]) == []
    assert book.view()[0]["acked"]
    escalated = book.sync([Condition("link_lost", "critical", "v1", {"vehicle": "Q1"})])
    assert escalated and not book.view()[0]["acked"]
    assert book.sync([]) == [] and book.view() == []
    assert not book.ack("missing")


def test_event_bus_texts_and_ring():
    bus = EventBus(size=3, wall=lambda: 5.0)
    e = bus.emit("relay_handback", "sim-1", data={"name": "harness-a"}, labels={"vehicle": "SIM-1"})
    assert e["text_en"] == "SIM-1 handed back to harness-a." and e["text_zh"] == "SIM-1 已交还给 harness-a。"
    for _ in range(4):
        bus.emit("stop_all")
    assert [x["seq"] for x in bus.after(0)] == [3, 4, 5]
    assert len(TEMPLATES) >= 37


def test_every_event_kind_is_documented_and_has_text():
    from relay.api.schemas import EventKind

    # a kind missing from the schema fails validation of every /events read that includes it
    assert set(EventKind.__args__) == set(TEMPLATES)


def test_sessions_lease_rate_limit_and_tokens():
    now = [0.0]
    mgr = SessionManager(lease_s=10.0, clock=lambda: now[0])
    session, token = mgr.open("harness", 0.0)
    assert mgr.lookup(token) is session and mgr.lookup("wrong") is None
    assert session.digest != token  # never stored in clear
    granted = sum(mgr.allow(session, "other") for _ in range(30))
    assert granted == 20
    assert sum(mgr.allow(session, "control") for _ in range(70)) == 60
    now[0] = 10.1
    assert mgr.lookup(token) is None
    assert [s.session_id for s in mgr.expire()] == [session.session_id]


def test_topology_relations_only_in_shared_frames():
    def vehicle(vid, kind, x=0.0, y=0.0, z=0.0, host=None):
        return {
            "id": vid,
            "kind": kind,
            "label": vid,
            "autopilot": "ArduPilot",
            "host": host,
            "link": {"state": "ok"},
            "controller": "none",
            "armed": False,
            "mode": None,
            "capabilities": {},
            "bridge": None,
            "claim": None,
            "possession": None,
            "gps": None,
            "global": None,
            "position": {"x": x, "y": y, "z": z}
            if kind == "sim" or host is None
            else {"x": 0, "y": 0, "z": 0},
        }

    fleet = {
        "vehicles": [
            vehicle("sim-1", "sim"),
            vehicle("sim-2", "sim", 3.0, 4.0, 1.0),
            vehicle("real-1", "real", host="10.7.7.36"),
        ],
        "sessions": [{"session_id": "s-1", "name": "h", "expires_in_s": 9.0, "vehicles": []}],
        "listen": {"port": 14550, "ok": True},
    }
    topo = build(fleet, ["10.7.7.5"])
    assert topo["relations"] == [
        {"a": "sim-1", "b": "sim-2", "horizontal_m": 5.0, "vertical_m": 1.0, "bearing_deg": 36.9}
    ]
    assert any(n["type"] == "bridge" for n in topo["nodes"])
    assert any(n["type"] == "session" for n in topo["nodes"])


def test_history_tables_and_export():
    h = History(None)
    sid = h.start_session("sim-1", "sim", "SIM-1", "ArduPilot", time.time())
    h.sample(
        sid, {"t": 1.0, "armed": 1, "mode": "LOITER", "alt": 1.2, "voltage": 12.1, "controller": "agent"}
    )
    h.sample(
        sid, {"t": 1.2, "armed": 1, "mode": "LOITER", "alt": 1.4, "voltage": 12.0, "controller": "human"}
    )
    h.event(
        sid, {"seq": 1, "t": 1.1, "kind": "possession_started", "data": {}, "text_en": "a", "text_zh": "b"}
    )
    h.event(sid, {"seq": 2, "t": 1.3, "kind": "relay_handback", "data": {}, "text_en": "a", "text_zh": "b"})
    h.segment(sid, 1.0, 1.1, "agent", "harness")
    h.course_run(
        {
            "session_id": sid,
            "vehicle_id": "sim-1",
            "course_id": "cage-loop",
            "state": "finished",
            "total_s": 12.5,
            "splits": [3, 3, 3, 3.5],
        }
    )
    h.set_label("10.7.7.36", "Quad 36")
    h.set_calibration("10.7.7.36", {"roll": -1, "pitch": 1, "yaw": 1}, 0.4)
    h.end_session(sid, time.time() + 5)
    detail = h.session_detail(sid)
    assert detail["samples"]["alt"] == [1.2, 1.4]
    assert detail["summary"]["possessions"] == 1 and detail["summary"]["handbacks"] == 1
    assert detail["summary"]["course_best_s"] == 12.5
    assert detail["control_segments"][0]["controller"] == "agent"
    assert h.best_course("cage-loop") == 12.5
    assert h.labels() == {"10.7.7.36": "Quad 36"}
    assert h.calibration("10.7.7.36") == ({"roll": -1, "pitch": 1, "yaw": 1}, 0.4)
    listing = h.list_sessions()
    assert listing[0]["max_alt_m"] == 1.4 and listing[0]["course_best_s"] == 12.5
    ctype, body = h.export(sid, "csv")
    assert ctype == "text/csv" and body.splitlines()[0].startswith("t,armed,mode")
    assert h.export(sid, "json")[0] == "application/json"
    assert h.prune(0) == 1 and h.list_sessions() == []
    h.close()


def test_exported_schema_is_current():
    import subprocess
    import sys

    from conftest import REPO

    result = subprocess.run(
        [sys.executable, str(REPO / "backend" / "tools" / "export_schema.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
