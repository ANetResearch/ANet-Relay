"""Program API (06 §2.6, §3.1): store, revisions, validation, unit tests, deployment and the snapshot."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from relay.api import schemas
from relay.api import schemas_programs as sp
from relay.api.app import create_app
from relay.fleet import FleetService
from relay.history import History
from relay.programs import store as storemod
from relay.programs.store import BUILTIN_IDS, ProgramStore

pytestmark = pytest.mark.sticks_level

OP = {"X-Relay-Operator": "op-token"}
SOURCE = (
    "PARAMS = {'gain': 0.5}\n\ndef step(obs, ctx):\n    return Sticks(throttle=obs.alt * ctx.params.gain)\n"
)


@pytest.fixture
def client():
    svc = FleetService(listen=False, history=History(None), operator_token="op-token")
    with TestClient(create_app(svc), base_url="http://127.0.0.1") as c:
        c.svc = svc
        yield c


def session(client, name="coder"):
    body = client.post("/api/v1/sessions", json={"name": name}).json()
    return {"X-Relay-Session": body["token"]}


def code(resp):
    return resp.json()["error"]["code"]


def wait_for(fn, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


def test_builtins_are_listed_read_only_and_copyable(client):
    listed = client.get("/api/v1/programs").json()["programs"]
    assert [p["id"] for p in listed][: len(BUILTIN_IDS)] == list(BUILTIN_IDS)
    for p in listed:
        sp.ProgramSummary.model_validate(p)
        assert p["builtin"] and p["name_zh"] and p["test_count"] >= 2
    hover = client.get("/api/v1/programs/hover_trim").json()["program"]
    sp.Program.model_validate(hover)
    assert hover["params"]["target_alt"] == 1.0 and "def step" in hover["source"]
    agent = session(client)
    resp = client.put("/api/v1/programs/hover_trim", headers=agent, json={"source": SOURCE})
    assert resp.status_code == 409 and code(resp) == "program_readonly"
    assert code(client.delete("/api/v1/programs/hover_trim", headers=OP)) == "program_readonly"
    copy = client.post("/api/v1/programs", headers=agent, json={"copy_of": "hover_trim"}).json()
    assert copy["program"]["source"] == hover["source"] and not copy["program"]["builtin"]
    assert copy["validation"]["ok"] and copy["program"]["author"] == "coder"
    # WP-5: the copy keeps no stored overrides, so an edit of PARAMS in its source takes effect
    assert copy["program"]["params"] == {}
    line = hover["source"].splitlines().index('    "target_alt": 1.0,') + 1
    patch = [{"start": line, "end": line, "text": '    "target_alt": 1.2,'}]
    cid = copy["program"]["id"]
    edited = client.put(f"/api/v1/programs/{cid}", headers=agent, json={"patch": patch}).json()
    assert edited["validation"]["params"]["target_alt"] == 1.2 and edited["program"]["params"] == {}
    assert code(client.get("/api/v1/programs/nope")) == "program_not_found"


def test_examples_validate_and_pass_their_tests(client):
    for pid in BUILTIN_IDS:
        out = client.post(f"/api/v1/programs/{pid}/validate", headers=OP).json()
        assert out["ok"] and out["has_step"], (pid, out["errors"])
        results = client.post(f"/api/v1/programs/{pid}/test", headers=OP).json()
        sp.TestResults.model_validate(results)
        assert results["passed"] == results["total"] >= 2, (pid, results)


def test_create_update_patch_revisions_and_delete(client):
    assert client.post("/api/v1/programs", json={"source": SOURCE}).status_code == 401
    agent = session(client)
    out = client.post("/api/v1/programs", headers=agent, json={"name": "Gain", "source": SOURCE}).json()
    pid = out["program"]["id"]
    assert out["program"]["rev"] == 1 and out["validation"]["params"] == {"gain": 0.5}
    # a line patch replaces line 4 (the return)
    patched = client.put(
        f"/api/v1/programs/{pid}",
        headers=OP,
        json={
            "patch": [{"start": 4, "end": 4, "text": "    return Sticks(throttle=ctx.params.gain)"}],
            "note": "flat",
        },
    ).json()
    assert patched["program"]["rev"] == 2 and patched["program"]["source"].splitlines()[3].endswith("gain)")
    revs = client.get(f"/api/v1/programs/{pid}/revisions").json()["revisions"]
    assert (
        [r["rev"] for r in revs] == [2, 1] and revs[0]["author"] == "operator" and revs[0]["note"] == "flat"
    )
    for r in revs:
        sp.ProgramRevision.model_validate(r)
    renamed = client.put(f"/api/v1/programs/{pid}", headers=OP, json={"name": "Flat gain"}).json()
    assert renamed["program"]["rev"] == 2  # metadata is no new revision
    resp = client.put(f"/api/v1/programs/{pid}", headers=OP, json={"params": {"gain": "x"}})
    assert code(resp) == "program_param_invalid"
    resp = client.put(f"/api/v1/programs/{pid}", headers=OP, json={"params": {"gain": 3}})
    assert resp.json()["program"]["params"] == {"gain": 3.0} and resp.json()["program"]["rev"] == 3
    bad = client.put(f"/api/v1/programs/{pid}", headers=OP, json={"source": "import os\n"}).json()
    assert not bad["validation"]["ok"] and bad["validation"]["errors"][0]["code"] == "forbidden_import"
    resp = client.put(
        f"/api/v1/programs/{pid}", headers=OP, json={"patch": [{"start": 90, "end": 99, "text": ""}]}
    )
    assert resp.status_code == 400
    assert client.delete(f"/api/v1/programs/{pid}", headers=agent).json() == {"id": pid, "deleted": True}
    assert client.get(f"/api/v1/programs/{pid}").status_code == 404


def test_revisions_keep_the_last_fifty():
    store = ProgramStore(None)
    pid = store.create({"name": "many", "source": SOURCE}, "operator")["id"]
    for i in range(storemod.MAX_REVISIONS + 10):
        store.update(pid, {"source": SOURCE + f"# {i}\n"}, "operator")
    revs = store.revisions(pid)
    assert len(revs) == storemod.MAX_REVISIONS and revs[0]["rev"] == storemod.MAX_REVISIONS + 11
    with pytest.raises(Exception) as exc:
        store.revision(pid, 1)
    assert exc.value.code == "revision_not_found"


def test_store_persists(tmp_path):
    store = ProgramStore(tmp_path)
    pid = store.create({"name": "kept", "source": SOURCE, "tests": [{"name": "a", "expect": {}}]}, "agent")[
        "id"
    ]
    store.update(pid, {"source": SOURCE + "\n"}, "agent")
    data = json.loads((tmp_path / "programs" / f"{pid}.json").read_text())
    assert [r["rev"] for r in data["revisions"]] == [1, 2]
    again = ProgramStore(tmp_path)
    assert (
        again.get(pid)["rev"] == 2
        and len(again.revisions(pid)) == 2
        and again.get(pid)["tests"][0]["name"] == "a"
    )


def test_validate_reports_line_and_column(client):
    out = client.post(
        "/api/v1/programs/hover_trim/validate",
        headers=OP,
        json={"source": "def step(obs, ctx):\n    x = obs.__class__\n"},
    ).json()
    assert not out["ok"]
    (problem,) = out["errors"]
    sp.Validation.model_validate(out)
    assert problem["line"] == 2 and problem["col"] == 9 and problem["code"] == "forbidden_attribute"
    assert problem["message_zh"] and problem["message_en"] != problem["message_zh"]


def test_unit_test_endpoint_with_inline_tests(client):
    tests = [{"name": "half", "obs": {"alt": 1.0}, "expect": {"throttle": [0.45, 0.55]}}]
    agent = session(client)
    pid = client.post(
        "/api/v1/programs", headers=agent, json={"name": "half", "source": SOURCE, "tests": tests}
    ).json()["program"]["id"]
    out = client.post(f"/api/v1/programs/{pid}/test", headers=agent).json()
    assert out["passed"] == 1 and out["program"] == pid
    out = client.post(
        f"/api/v1/programs/{pid}/test",
        headers=agent,
        json={"tests": [{"name": "low", "obs": {"alt": 0.2}, "expect": {"throttle": [0.45, 0.55]}}]},
    ).json()
    assert out["passed"] == 0 and out["results"][0]["failures"][0]["field"] == "throttle"
    resp = client.post(f"/api/v1/programs/{pid}/test", headers=agent, json={"source": "def step(:\n"})
    assert code(resp) == "program_invalid"


def test_deploy_through_the_api_and_snapshot(client):
    client.post("/api/v1/sims", headers=OP, json={"count": 1})
    assert wait_for(lambda: client.svc.vehicles["sim-1"].link_state == "ok", 10)
    agent = session(client)
    resp = client.post("/api/v1/vehicles/sim-1/program", headers=agent, json={"program": "hover_trim"})
    assert resp.status_code == 409 and code(resp) == "not_claimed"
    client.post("/api/v1/vehicles/sim-1/claim", headers=agent)
    resp = client.post(
        "/api/v1/vehicles/sim-1/program", headers=agent, json={"program": "hover_trim", "seed": 2}
    )
    assert resp.status_code == 200, resp.json()
    view = resp.json()["program"]
    schemas.ProgramRunView.model_validate(view)
    assert view["id"] == "hover_trim" and view["rev"] == 1 and view["owner"]["kind"] == "session"
    assert wait_for(lambda: client.svc.programs.runs["sim-1"].frames > 20, 5)
    fleet = client.get("/api/v1/fleet").json()
    (vehicle,) = fleet["vehicles"]
    assert vehicle["program"]["status"] == "running" and vehicle["programs_allowed"] is True
    schemas.Fleet.model_validate(fleet)
    resp = client.patch("/api/v1/vehicles/sim-1/program/params", headers=agent, json={"target_alt": 1.2})
    assert resp.json()["params"]["target_alt"] == 1.2
    log = client.get("/api/v1/vehicles/sim-1/program/log?after=0").json()
    sp.ProgramLog.model_validate(log)
    assert log["program"] == "hover_trim" and log["lines"]
    resumed = client.post("/api/v1/vehicles/sim-1/program/resume", headers=agent).json()
    assert resumed["program"]["status"] == "running"  # resuming a running program changes nothing
    assert (
        client.post("/api/v1/vehicles/sim-1/program/stop", headers=agent).json()["program"]["status"]
        == "stopped"
    )
    resp = client.post("/api/v1/vehicles/sim-1/program/resume", headers=agent)
    assert resp.status_code == 409 and code(resp) == "program_not_paused"
    resp = client.post(
        "/api/v1/vehicles/sim-1/program/allow", headers=agent, json={"allow": True, "confirm": "PROGRAM"}
    )
    assert resp.status_code == 403 and code(resp) == "operator_required"
    resp = client.post(
        "/api/v1/vehicles/sim-1/program/allow", headers=OP, json={"allow": True, "confirm": "PROGRAM"}
    )
    assert resp.json() == {"id": "sim-1", "programs_allowed": True}
    assert code(client.post("/api/v1/vehicles/sim-2/program/stop", headers=OP)) == "vehicle_not_found"
    spec = client.get("/openapi.json").json()
    assert (
        "/api/v1/vehicles/{vid}/program" in spec["paths"] and "/api/v1/programs/{pid}/test" in spec["paths"]
    )
