"""Timeline routes over HTTP (10 §2): fly, edit, record, stop; the threaded fleet arms with primed sticks."""

from test_api import OP, client, error, wait_for  # noqa: F401  (client is a fixture)
from test_sequence import hover_doc


def test_fly_edit_record_and_stop_over_http(client):  # noqa: F811
    added = client.post("/api/v1/sims", headers=OP, json={"count": 1, "position": False})
    assert added.status_code == 200, added.text
    assert wait_for(
        lambda: [v["link"]["state"] for v in client.get("/api/v1/fleet").json()["vehicles"]] == ["ok"]
    )
    client.post("/api/v1/vehicles/sim-1/possess", headers=OP, json={"input": "keyboard"})
    bad = client.post("/api/v1/vehicles/sim-1/sequence", headers=OP, json={"sequence": {"format": "x"}})
    assert error(bad) == "invalid_request" and "format" in bad.json()["error"]["message_en"]
    assert (
        client.post(
            "/api/v1/vehicles/sim-1/sequence/record", headers=OP, json={"action": "start"}
        ).status_code
        == 200
    )
    out = client.post("/api/v1/vehicles/sim-1/sequence", headers=OP, json={"sequence": hover_doc(hold_s=6.0)})
    assert out.status_code == 200, out.text
    assert out.json()["skill"]["name"] == "sequence"
    vehicle = lambda: client.get("/api/v1/vehicles/sim-1").json()  # noqa: E731
    assert wait_for(lambda: (vehicle()["skill"] or {}).get("progress_s", 0) >= 4.0, 20)
    assert vehicle()["armed"]
    edit = client.patch("/api/v1/vehicles/sim-1/sequence", headers=OP, json={"trim": {"pitch": -0.03}})
    assert edit.status_code == 200 and edit.json()["trim"]["pitch"] == -0.03
    stop = client.post("/api/v1/vehicles/sim-1/sequence/stop", headers=OP)
    assert stop.json()["after"] == "hold"
    rec = client.post("/api/v1/vehicles/sim-1/sequence/record", headers=OP, json={"action": "stop"}).json()
    assert rec["sequence"]["format"] == "ars" and rec["sequence"]["duration_s"] > 2.0
    assert (
        error(client.patch("/api/v1/vehicles/sim-1/sequence", headers=OP, json={"trim": {"pitch": 0}}))
        == "no_sequence"
    )
