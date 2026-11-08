"""Challenge, duel, job, ladder and trace API (06 §3.2-§3.4) with the evaluator in process."""

from __future__ import annotations

import gzip
import json
import time

import pytest
from fastapi.testclient import TestClient

from relay.api import schemas_judge as sj
from relay.api.app import create_app
from relay.fleet import FleetService
from relay.history import History
from relay.judge import service as servicemod
from relay.judge.worker import run_inprocess

pytestmark = pytest.mark.sticks_level

OP = {"X-Relay-Operator": "op-token"}
API = "/api/v1"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(servicemod, "_subprocess_runner", run_inprocess)
    svc = FleetService(None, tmp_path, listen=False, history=History(None), operator_token="op-token")
    with TestClient(create_app(svc), base_url="http://127.0.0.1") as c:
        c.svc = svc
        c.data = tmp_path
        yield c


def session(client, name="coder"):
    body = client.post(f"{API}/sessions", json={"name": name}).json()
    return {"X-Relay-Session": body["token"]}


def code(resp):
    return resp.json()["error"]["code"]


def finish(client, job_id, timeout=120.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = client.get(f"{API}/jobs/{job_id}").json()["job"]
        if job["state"] not in ("queued", "running"):
            return job
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_challenges_are_listed_and_described(client):
    listed = client.get(f"{API}/challenges").json()["challenges"]
    assert [c["id"] for c in listed][:3] == ["liftoff", "hover", "heading"] and len(listed) == 9
    for c in listed:
        sj.ChallengeSummary.model_validate(c)
        assert c["best"] is None and c["attempts"] == 0
    hover = client.get(f"{API}/challenges/hover").json()["challenge"]
    sj.Challenge.model_validate(hover)
    assert hover["sensors"] == {"baro": True, "position": "none", "contacts": False}
    assert hover["hover_assist"] is False and hover["hidden_seeds"] == 3 and hover["history"] == []
    assert code(client.get(f"{API}/challenges/nope")) == "challenge_not_found"


def test_submit_poll_record_and_replay(client):
    agent = session(client)
    assert client.post(f"{API}/challenges/liftoff/submit", json={"program": "liftoff"}).status_code == 401
    resp = client.post(f"{API}/challenges/liftoff/submit", headers=agent, json={"program": "liftoff"})
    job = resp.json()["job"]
    assert resp.status_code == 200 and job["kind"] == "challenge" and job["challenge"] == "liftoff"
    assert job["programs"] == [{"program": "liftoff", "rev": 1, "name": "Lift-off"}]
    done = finish(client, job["id"])
    sj.Job.model_validate(done)
    result = done["result"]
    assert done["state"] == "done" and result["passed"] and len(result["seeds"]) == 5
    assert result["best"]["job"] == job["id"] and result["speed"]["factor"] > 1.0
    hidden = [s for s in result["seeds"] if s["hidden"]]
    assert len(hidden) == 3 and all(s["seed"] is None and s["trace_id"] is None for s in hidden)
    trace = client.get(f"{API}/traces/{result['trace_id']}").json()
    sj.Trace.model_validate(trace)
    assert trace["challenge"] == "liftoff" and trace["job"] == job["id"] and trace["seed"] == 1
    # stored compactly next to the programs
    path = client.data / "programs" / "traces" / f"{result['trace_id']}.json.gz"
    assert json.loads(gzip.decompress(path.read_bytes()))["id"] == result["trace_id"]
    results = json.loads((client.data / "programs" / "results.json").read_text("utf-8"))
    assert results["challenges"]["liftoff"]["best"]["score"] == result["score"]
    listed = {c["id"]: c for c in client.get(f"{API}/challenges").json()["challenges"]}
    assert listed["liftoff"]["attempts"] == 1 and listed["liftoff"]["best"]["passed"]
    detail = client.get(f"{API}/challenges/liftoff").json()["challenge"]
    assert detail["history"][0]["job"] == job["id"]
    events = [e for e in client.get(f"{API}/events").json()["events"] if e["kind"] == "judge_finished"]
    assert events and "passed" in events[-1]["text_en"] and "通过" in events[-1]["text_zh"]
    assert code(client.get(f"{API}/traces/tr-ffffffffffffffff")) == "trace_not_found"
    assert code(client.get(f"{API}/traces/..%2fresults")) in ("trace_not_found", "not_found")
    assert code(client.get(f"{API}/traces/results")) == "trace_not_found"
    # the program store skips the evaluation records
    assert all(p["id"] not in ("results", "ladder") for p in client.get(f"{API}/programs").json()["programs"])


def test_visible_only_is_not_the_best_and_bad_submissions_fail_early(client):
    resp = client.post(
        f"{API}/challenges/hover/submit", headers=OP, json={"program": "hover_trim", "visible_only": True}
    )
    done = finish(client, resp.json()["job"]["id"])
    assert done["state"] == "done" and len(done["result"]["seeds"]) == 2 and done["result"]["best"] is None
    assert client.get(f"{API}/challenges/hover").json()["challenge"]["history"][0]["visible_only"]
    for body, expected in (
        ({"program": "nope"}, "program_not_found"),
        ({"program": "hover_trim", "rev": 9}, "revision_not_found"),
        ({"program": "hover_trim", "params": {"zzz": 1}}, "program_param_invalid"),
    ):
        assert code(client.post(f"{API}/challenges/hover/submit", headers=OP, json=body)) == expected
    assert code(client.post(f"{API}/challenges/nope/submit", headers=OP, json={"program": "x"})) in (
        "challenge_not_found",
        "program_not_found",
    )


def test_duels_move_the_ladder(client):
    body = {
        "programs": ["hunter_easy", {"program": "hunter_hard"}],
        "rounds": 2,
        "seed": 4,
        "scenario": {"duration_s": 20},
    }
    job = client.post(f"{API}/duels", headers=OP, json=body).json()["job"]
    assert job["kind"] == "duel" and [p["program"] for p in job["programs"]] == ["hunter_easy", "hunter_hard"]
    done = finish(client, job["id"])
    sj.Job.model_validate(done)
    result = done["result"]
    assert done["state"] == "done" and len(result["rounds"]) == 2
    assert all(r["trace_id"] for r in result["rounds"])
    trace = client.get(f"{API}/traces/{result['rounds'][0]['trace_id']}").json()
    assert trace["kind"] == "duel" and trace["round"] == 1 and len(trace["aircraft"]) == 2
    ladder = client.get(f"{API}/ladder").json()
    sj.Ladder.model_validate(ladder)
    rows = {r["program"]: r for r in ladder["ladder"]}
    assert (
        set(rows) == {"hunter_easy", "hunter_hard"}
        and rows["hunter_hard"]["rating"] >= rows["hunter_easy"]["rating"]
    )
    assert rows["hunter_easy"]["games"] == 2 and ladder["duels"][-1]["job"] == job["id"]
    assert sum(r["rating"] for r in rows.values()) == pytest.approx(2400.0, abs=0.2)
    stored = json.loads((client.data / "programs" / "ladder.json").read_text("utf-8"))
    assert set(stored["ratings"]) == {"hunter_easy", "hunter_hard"}


def test_duel_bodies_are_checked(client):
    for body, expected in (
        ({"programs": ["hunter_easy"]}, "invalid_request"),
        ({"programs": ["hunter_easy", "hunter_easy"]}, "invalid_request"),
        ({"programs": ["hunter_easy", "nope"]}, "program_not_found"),
        ({"programs": ["hunter_easy", "hunter_hard"], "rounds": 0}, "invalid_request"),
        ({"programs": ["hunter_easy", "hunter_hard"], "scenario": {"duration_s": 2}}, "invalid_request"),
        ({"programs": ["hunter_easy", "hunter_hard"], "scenario": {"airframe": "broken"}}, "invalid_request"),
    ):
        assert code(client.post(f"{API}/duels", headers=OP, json=body)) == expected, body
    assert client.post(f"{API}/duels", json={"programs": ["hunter_easy", "hunter_hard"]}).status_code == 401


def test_jobs_can_be_cancelled(client):
    body = {"programs": ["hunter_easy", "hunter_medium", "hunter_hard"], "rounds": 5}
    first = client.post(f"{API}/duels", headers=OP, json=body).json()["job"]
    second = client.post(f"{API}/duels", headers=OP, json=body).json()["job"]
    third = client.post(f"{API}/duels", headers=OP, json=body).json()["job"]
    assert client.get(f"{API}/jobs/{third['id']}").json()["job"]["state"] == "queued"
    assert client.delete(f"{API}/jobs/{third['id']}", headers=OP).json()["job"]["state"] == "cancelled"
    assert client.delete(f"{API}/jobs/{first['id']}").status_code == 401
    client.delete(f"{API}/jobs/{first['id']}", headers=OP)
    client.delete(f"{API}/jobs/{second['id']}", headers=OP)
    assert finish(client, first["id"])["state"] == "cancelled"
    assert finish(client, second["id"])["state"] == "cancelled"
    listed = client.get(f"{API}/jobs").json()["jobs"]
    assert {j["id"] for j in listed} >= {first["id"], second["id"], third["id"]}
    assert code(client.get(f"{API}/jobs/job-nope")) == "job_not_found"
