"""Write the Lab's judge wire fixture from the real evaluator (WP-5, 06 §3.2-§3.4, §7).

    python backend/tools/judge_fixture.py

Runs a liftoff submission on its visible seeds and a one-round duel of ``hunter_easy`` against ``hunter_hard``
in process, then stores the bodies of the challenge, job, ladder and trace routes in
``web/src/lab/fixtures/judge-bodies.json``. Traces are cut to short windows (the duel's around its first hit)
to keep the file small. web/src/lab/judgeWire.test.ts reads it, so the Lab's normalisers and trace parser are
tested against what the backend actually sends. Rerun after a judge response shape changes.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from relay.api.app import create_app  # noqa: E402
from relay.fleet import FleetService  # noqa: E402
from relay.history import History  # noqa: E402
from relay.judge import service as servicemod  # noqa: E402
from relay.judge.worker import run_inprocess  # noqa: E402

OUT = ROOT / "web" / "src" / "lab" / "fixtures" / "judge-bodies.json"
API = "/api/v1"
OP = {"X-Relay-Operator": "op"}


def _finish(client: TestClient, jid: str) -> dict[str, Any]:
    while True:
        body = client.get(f"{API}/jobs/{jid}").json()
        if body["job"]["state"] not in ("queued", "running"):
            return body
        time.sleep(0.1)


def _round(x: Any) -> Any:
    if isinstance(x, float):
        return round(x, 3)
    if isinstance(x, list):
        return [_round(i) for i in x]
    if isinstance(x, dict):
        return {k: _round(v) for k, v in x.items()}
    return x


def _window(trace: dict[str, Any], lo: int, n: int) -> dict[str, Any]:
    hi = min(lo + n, len(trace["t"]))
    t0, t1 = trace["t"][lo], trace["t"][hi - 1]
    trace["t"] = trace["t"][lo:hi]
    for a in trace["aircraft"]:
        a["frames"] = a["frames"][lo:hi]
        a["states"] = [s for s in a["states"] if s[0] <= t1]
    trace["shots"] = [s for s in trace["shots"] if t0 - 2.5 <= s["t0"] <= t1]
    trace["events"] = [e for e in trace["events"] if t0 <= e["t"] <= t1]
    return trace


def main() -> int:
    servicemod._subprocess_runner = run_inprocess
    svc = FleetService(
        None, Path(tempfile.mkdtemp()), listen=False, history=History(None), operator_token="op"
    )
    out: dict[str, Any] = {}
    with TestClient(create_app(svc), base_url="http://127.0.0.1") as c:
        body = {"program": "liftoff", "visible_only": True}
        out["submit"] = c.post(f"{API}/challenges/liftoff/submit", headers=OP, json=body).json()
        out["challenge_job"] = _finish(c, out["submit"]["job"]["id"])
        tid = out["challenge_job"]["job"]["result"]["trace_id"]
        out["challenge_trace"] = _window(c.get(f"{API}/traces/{tid}").json(), 0, 30)
        duel = {
            "programs": ["hunter_easy", "hunter_hard"],
            "rounds": 1,
            "seed": 3,
            "scenario": {"duration_s": 20},
        }
        started = c.post(f"{API}/duels", headers=OP, json=duel).json()
        out["duel_job"] = _finish(c, started["job"]["id"])
        rtid = out["duel_job"]["job"]["result"]["rounds"][0]["trace_id"]
        trace = c.get(f"{API}/traces/{rtid}").json()
        hits = [e["t"] for e in trace["events"] if e["kind"] == "hit"]
        lo = max(0, int(((hits[0] if hits else 5.0) - 2.0) * trace["hz"]))
        out["duel_trace"] = _window(trace, lo, 40)
        out["challenges"] = c.get(f"{API}/challenges").json()
        out["ladder"] = c.get(f"{API}/ladder").json()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(_round(out), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
