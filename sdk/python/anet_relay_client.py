"""ANet Relay Python SDK (03 §10.6). Standard library only.

    from anet_relay_client import RelayClient

    with RelayClient() as relay:                 # http://127.0.0.1:8765 by default
        relay.open_session("my-agent")           # lease renewed in the background
        relay.claim("sim-1")
        relay.takeoff("sim-1", alt=1.2)          # waits until the skill finishes
        relay.goto("sim-1", 0.5, 0.0, 1.2)
        relay.land("sim-1")

Errors from the API raise ``RelayError`` with the stable ``code`` from the error body.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any

__all__ = ["DEFAULT_BASE", "RelayClient", "RelayError"]
__version__ = "0.2.1"

DEFAULT_BASE = os.environ.get("RELAY_API", "http://127.0.0.1:8765")
API = "/api/v1"


class RelayError(Exception):
    """API error: stable ``code``, HTTP ``status``, ``message`` (English), ``message_zh``, ``detail``."""

    def __init__(
        self, code: str, status: int, message: str, message_zh: str = "", detail: dict | None = None
    ):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.status = status
        self.message = message
        self.message_zh = message_zh
        self.detail = detail or {}


class RelayClient:
    def __init__(
        self,
        base: str = DEFAULT_BASE,
        operator_token: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.base = base.rstrip("/")
        self.operator_token = operator_token
        self.timeout = timeout
        self.session_id: str | None = None
        self.session_name: str | None = None
        self.lease_s: float | None = None
        self._token: str | None = None
        self._renew_stop = threading.Event()
        self._renew_thread: threading.Thread | None = None

    # ------------------------------------------------------------------ transport

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        auth: str | None = "session",
        query: dict | None = None,
    ) -> Any:
        url = f"{self.base}{API}{path}"
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None}, doseq=True)
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if auth == "operator":
            if not self.operator_token:
                raise RelayError("operator_required", 403, "This call needs the operator token.")
            headers["X-Relay-Operator"] = self.operator_token
        elif auth == "session" and self._token:
            headers["X-Relay-Session"] = self._token
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    ctype = resp.headers.get("Content-Type", "")
                    return json.loads(raw) if "json" in ctype else raw.decode("utf-8")
            except urllib.error.HTTPError as exc:
                payload = exc.read()
                try:
                    error = json.loads(payload)["error"]
                except (ValueError, KeyError, TypeError):
                    raise RelayError(
                        "http_error", exc.code, payload.decode("utf-8", "replace")[:200]
                    ) from exc
                if exc.code == 429 and attempt < 2:
                    time.sleep(float(exc.headers.get("Retry-After", "1")) * 0.5)
                    continue
                raise RelayError(
                    error.get("code", "error"),
                    exc.code,
                    error.get("message_en", ""),
                    error.get("message_zh", ""),
                    error.get("detail") or {},
                ) from exc
            except urllib.error.URLError as exc:
                raise RelayError(
                    "unreachable", 0, f"Relay is not reachable at {self.base}: {exc.reason}"
                ) from exc
        raise RelayError("rate_limited", 429, "Too many requests.")

    def _get(self, path: str, **query: Any) -> Any:
        return self.request("GET", path, auth="session", query=query or None)

    def _post(self, path: str, body: Any = None) -> Any:
        return self.request("POST", path, body if body is not None else {}, auth="session")

    def _op(self, method: str, path: str, body: Any = None, query: dict | None = None) -> Any:
        return self.request(method, path, body, auth="operator", query=query)

    # ------------------------------------------------------------------ reads

    def health(self) -> dict:
        return self._get("/health")

    def capabilities(self) -> dict:
        return self._get("/capabilities")

    def fleet(self) -> dict:
        return self._get("/fleet")

    def vehicles(self) -> list[dict]:
        return self.fleet()["vehicles"]

    def vehicle(self, vehicle_id: str) -> dict:
        return self._get(f"/vehicles/{vehicle_id}")

    def topology(self) -> dict:
        return self._get("/topology")

    def events(
        self, after: int = 0, limit: int = 200, vehicle: str | None = None, tail: bool = False
    ) -> dict:
        """Events with seq > ``after`` (oldest first); ``tail=True`` returns the newest ``limit`` instead."""
        return self._get("/events", after=after, limit=limit, vehicle=vehicle, tail="true" if tail else None)

    def courses(self) -> list[dict]:
        return self._get("/courses")["courses"]

    def last_seq(self) -> int:
        return int(self.events(after=0, limit=1)["last_seq"])

    def wait_event(
        self,
        kind: str | tuple[str, ...],
        vehicle: str | None = None,
        after: int | None = None,
        timeout: float = 30.0,
        predicate: Callable[[dict], bool] | None = None,
    ) -> dict:
        """Poll /events until an event of ``kind`` arrives (after sequence ``after``)."""
        kinds = (kind,) if isinstance(kind, str) else kind
        seq = self.last_seq() if after is None else after
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            batch = self.events(after=seq, limit=500, vehicle=vehicle)
            for event in batch["events"]:
                seq = max(seq, event["seq"])
                if event["kind"] in kinds and (predicate is None or predicate(event)):
                    return event
            time.sleep(0.1)
        raise RelayError("timeout", 0, f"No {'/'.join(kinds)} event within {timeout:g} s.")

    def stream(self, timeout: float | None = None) -> Iterator[dict]:
        """Yield WebSocket messages ({"type": "hello" | "fleet" | "event", ...}) until the socket closes."""
        ws = _WebSocket.connect(self._stream_url(), timeout)
        try:
            while True:
                text = ws.recv()
                if text is None:
                    return
                yield json.loads(text)
        finally:
            ws.close()

    def _stream_url(self) -> str:
        parsed = urllib.parse.urlsplit(self.base)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        query = {}
        if self._token:
            query["session"] = self._token
        suffix = ("?" + urllib.parse.urlencode(query)) if query else ""
        return f"{scheme}://{parsed.netloc}{API}/stream{suffix}"

    # ------------------------------------------------------------------ session

    def open_session(self, name: str, renew: bool = True) -> str:
        opened = self.request("POST", "/sessions", {"name": name}, auth=None)
        self._token = opened["token"]
        self.session_id = opened["session_id"]
        self.session_name = opened["name"]
        self.lease_s = float(opened["lease_s"])
        if renew:
            self._renew_stop.set()  # retire a previous renew thread
            self._renew_stop = threading.Event()
            self._renew_thread = threading.Thread(
                target=self._renew, args=(self._renew_stop,), name="relay-lease", daemon=True
            )
            self._renew_thread.start()
        return opened["session_id"]

    def _renew(self, stop: threading.Event) -> None:
        period = max(1.0, (self.lease_s or 15.0) / 3.0)
        while not stop.wait(period):
            try:
                self._post("/sessions/heartbeat")
            except RelayError as exc:
                if exc.code == "session_expired":
                    return

    def heartbeat(self) -> dict:
        return self._post("/sessions/heartbeat")

    def close(self) -> None:
        """Close the session (releases every claim) and stop renewing."""
        self._renew_stop.set()
        if self._token:
            try:
                self.request("DELETE", "/sessions", auth="session")
            except RelayError:
                pass
        self._token = None
        self.session_id = None

    def __enter__(self) -> RelayClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ authority

    def claim(self, vehicle_id: str) -> dict:
        return self._post(f"/vehicles/{vehicle_id}/claim")

    def unclaim(self, vehicle_id: str) -> dict:
        return self._post(f"/vehicles/{vehicle_id}/unclaim")

    # ------------------------------------------------------------------ control

    def sticks(self, vehicle_id: str, roll: float, pitch: float, yaw: float, throttle: float) -> dict:
        """One stick frame. Repeat at 10-50 Hz; frames older than 0.8 s stop being sent."""
        return self._post(
            f"/vehicles/{vehicle_id}/sticks", {"roll": roll, "pitch": pitch, "yaw": yaw, "throttle": throttle}
        )

    def arm(self, vehicle_id: str, confirm: str | None = None, force: bool = False) -> dict:
        """Arm. Real aircraft need ``confirm="ARM"`` and passing preflight checks.

        ``force=True`` skips the autopilot's own pre-arm checks and needs ``confirm="FORCE"`` (04 §2).
        """
        body: dict[str, Any] = {"arm": True, "confirm": confirm}
        if force:
            body["force"] = True
        return self._post(f"/vehicles/{vehicle_id}/arm", body)

    def disarm(self, vehicle_id: str) -> dict:
        return self._post(f"/vehicles/{vehicle_id}/arm", {"arm": False})

    def mode(self, vehicle_id: str, name: str) -> dict:
        return self._post(f"/vehicles/{vehicle_id}/mode", {"name": name})

    def skill(
        self, vehicle_id: str, name: str, params: dict | None = None, wait: bool = True, timeout: float = 60.0
    ) -> dict:
        started = self._post(f"/vehicles/{vehicle_id}/skills/{name}", params or {})["skill"]
        if not wait:
            return started
        return self.wait_skill(vehicle_id, started["id"], timeout)

    def wait_skill(self, vehicle_id: str, skill_id: int, timeout: float = 60.0) -> dict:
        """Wait until skill ``skill_id`` finishes; raises RelayError('skill_failed') if it fails."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            vehicle = next((v for v in self.vehicles() if v["id"] == vehicle_id), None)
            if vehicle is None:
                raise RelayError("vehicle_not_found", 404, f"{vehicle_id} disappeared.")
            skill = vehicle.get("skill") or {}
            if skill.get("id") == skill_id and skill.get("state") != "running":
                if skill["state"] == "failed":
                    raise RelayError(
                        "skill_failed", 409, f"{skill['name']} failed: {skill.get('detail')}", detail=skill
                    )
                return skill
            if skill.get("id", 0) > skill_id:
                raise RelayError("skill_superseded", 409, "Another skill replaced this one.", detail=skill)
            time.sleep(0.1)
        raise RelayError("timeout", 0, f"Skill {skill_id} did not finish within {timeout:g} s.")

    def takeoff(
        self,
        vehicle_id: str,
        alt: float = 1.0,
        wait: bool = True,
        confirm: str | None = None,
        timeout: float = 40.0,
    ) -> dict:
        params: dict[str, Any] = {"alt": alt}
        if confirm:
            params["confirm"] = confirm
        return self.skill(vehicle_id, "takeoff", params, wait, timeout)

    def land(self, vehicle_id: str, wait: bool = True, timeout: float = 90.0) -> dict:
        return self.skill(vehicle_id, "land", {}, wait, timeout)

    def hold(
        self, vehicle_id: str, seconds: float = 0.0, wait: bool = True, timeout: float | None = None
    ) -> dict:
        """Hold altitude (and position when estimated); with ``seconds`` it reports done after hovering."""
        params = {"seconds": seconds} if seconds else {}
        return self.skill(vehicle_id, "hold", params, wait, timeout or 10.0 + seconds)

    def move(
        self,
        vehicle_id: str,
        forward: float = 0.0,
        right: float = 0.0,
        up: float = 0.0,
        wait: bool = True,
        timeout: float = 40.0,
    ) -> dict:
        """Move in the body frame. Without a position estimate the distance is an open-loop estimate."""
        params = {"forward": forward, "right": right, "up": up}
        return self.skill(vehicle_id, "move", params, wait, timeout)

    def yaw(self, vehicle_id: str, deg: float, wait: bool = True, timeout: float = 30.0) -> dict:
        """Turn by ``deg`` degrees relative to the current heading, positive clockwise."""
        return self.skill(vehicle_id, "yaw", {"deg": deg}, wait, timeout)

    def plan(
        self,
        vehicle_id: str,
        steps: list[dict],
        name: str | None = None,
        confirm: str | None = None,
        wait: bool = True,
        timeout: float = 180.0,
    ) -> dict:
        """Fly a sequence, e.g. [{"action": "takeoff", "alt": 1}, {"action": "move", "forward": 1}, ...].

        With ``wait`` it returns the finished plan and raises ``plan_failed`` when a step fails.
        """
        body: dict[str, Any] = {"steps": steps}
        if name:
            body["name"] = name
        if confirm:
            body["confirm"] = confirm
        plan = self._post(f"/vehicles/{vehicle_id}/plan", body)["plan"]
        if not wait:
            return plan
        return self.wait_plan(vehicle_id, plan["id"], timeout)

    def wait_plan(self, vehicle_id: str, plan_id: int, timeout: float = 180.0) -> dict:
        """Wait until plan ``plan_id`` finishes; raises RelayError('plan_failed') unless it ends done."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            current = self.vehicle(vehicle_id).get("plan") or {}
            if current.get("id") == plan_id and current.get("state") != "running":
                if current["state"] != "done":
                    raise RelayError(
                        "plan_failed",
                        409,
                        f"plan {current['state']} at step {current['index'] + 1}: {current.get('detail')}",
                        detail=current,
                    )
                return current
            time.sleep(0.25)
        raise RelayError("timeout", 0, f"plan {plan_id} did not finish in {timeout:g} s")

    def cancel_plan(self, vehicle_id: str) -> dict:
        """Cancel the running plan; an airborne aircraft holds where it is."""
        return self._post(f"/vehicles/{vehicle_id}/plan/cancel")

    def rtl(self, vehicle_id: str, wait: bool = True, timeout: float = 180.0) -> dict:
        return self.skill(vehicle_id, "rtl", {}, wait, timeout)

    def goto(
        self,
        vehicle_id: str,
        x: float,
        y: float,
        z: float,
        yaw: float | None = None,
        wait: bool = True,
        timeout: float = 30.0,
    ) -> dict:
        """Fly to (x east, y north, z up) metres in the vehicle's position frame; ``yaw`` in degrees."""
        params: dict[str, Any] = {"x": x, "y": y, "z": z}
        if yaw is not None:
            params["yaw"] = yaw
        return self.skill(vehicle_id, "goto", params, wait, timeout)

    def course_start(self, vehicle_id: str, course_id: str = "cage-loop") -> dict:
        return self._post(f"/vehicles/{vehicle_id}/course/start", {"course_id": course_id})

    def course_stop(self, vehicle_id: str) -> dict:
        return self._post(f"/vehicles/{vehicle_id}/course/stop")

    def stop(self, vehicle_id: str) -> dict:
        """Emergency stop: throttle to zero and a disarm request."""
        return self._post(f"/vehicles/{vehicle_id}/stop")

    # ------------------------------------------------------------------ device level (04 §2)

    def device(self, vehicle_id: str) -> dict:
        """Device overview: motors, servos, RC, frame, sensors, estimator, vibration, home, parameters."""
        return self._get(f"/vehicles/{vehicle_id}/device")

    def message(self, vehicle_id: str, name: str) -> dict:
        """Latest frame of one MAVLink message ``{name, fields, t, hz}``; 404 ``message_not_seen``."""
        return self._get(f"/vehicles/{vehicle_id}/messages/{urllib.parse.quote(name, safe='')}")

    def param(self, vehicle_id: str, name: str) -> dict:
        """One parameter ``{name, value, type}``; waits up to 2 s when the table is not loaded."""
        return self._get(f"/vehicles/{vehicle_id}/params/{urllib.parse.quote(name, safe='')}")

    def params_read(self, vehicle_id: str, names: list[str]) -> dict:
        """Read up to 32 parameters by name: ``{values: {NAME: value}, missing: [...]}``."""
        return self._post(f"/vehicles/{vehicle_id}/params/read", {"names": names})

    def commands(self) -> list[dict]:
        """Catalogue of raw MAVLink commands the command endpoint accepts."""
        result = self._get("/commands")
        return result["commands"] if isinstance(result, dict) and "commands" in result else result

    def command(
        self,
        vehicle_id: str,
        command: str | int,
        params: list[float] | None = None,
        frame: int | None = None,
        use_int: bool | None = None,
        confirm: str | None = None,
        timeout_s: float | None = None,
    ) -> dict:
        """Send one catalogued MAVLink command; waits for ``COMMAND_ACK`` (``ack`` None on timeout)."""
        body: dict[str, Any] = {"command": command, "params": params or []}
        for key, value in (
            ("frame", frame),
            ("use_int", use_int),
            ("confirm", confirm),
            ("timeout_s", timeout_s),
        ):
            if value is not None:
                body[key] = value
        return self._post(f"/vehicles/{vehicle_id}/command", body)

    def motor_test(
        self,
        vehicle_id: str,
        motor: int | str,
        throttle_pct: float,
        seconds: float = 2.0,
        confirm: str | None = None,
    ) -> dict:
        """Spin one motor (1..8) or ``"all"``. Disarmed only; real aircraft need ``confirm="MOTOR"``."""
        body = {"motor": motor, "throttle_pct": throttle_pct, "seconds": seconds, "confirm": confirm}
        return self._post(f"/vehicles/{vehicle_id}/motors/test", body)

    def motors_stop(self, vehicle_id: str) -> dict:
        """Stop every running motor test immediately."""
        return self._post(f"/vehicles/{vehicle_id}/motors/stop")

    def actuator(
        self,
        vehicle_id: str,
        index: int,
        value: float | None = None,
        pwm: int | None = None,
        seconds: float | None = None,
        confirm: str | None = None,
    ) -> dict:
        """Drive output ``index`` (1..16) with ``value`` (-1..1) or ``pwm`` (800..2200); 0 s holds."""
        body: dict[str, Any] = {"index": index}
        for key, v in (("value", value), ("pwm", pwm), ("seconds", seconds), ("confirm", confirm)):
            if v is not None:
                body[key] = v
        return self._post(f"/vehicles/{vehicle_id}/actuators/set", body)

    def rc(
        self,
        vehicle_id: str,
        channels: dict[int | str, int] | None = None,
        axes: dict[str, float] | None = None,
    ) -> dict:
        """One RC override frame (``channels`` 1..18 in us, or named ``axes``); expires after 0.8 s."""
        body: dict[str, Any] = {}
        if channels is not None:
            body["channels"] = {str(k): v for k, v in channels.items()}
        if axes is not None:
            body["axes"] = axes
        return self._post(f"/vehicles/{vehicle_id}/rc", body)

    def message_interval(self, vehicle_id: str, message: str, hz: float) -> dict:
        """Ask the autopilot to stream ``message`` at ``hz`` (0 stops it)."""
        return self._post(f"/vehicles/{vehicle_id}/message-interval", {"message": message, "hz": hz})

    def request_message(self, vehicle_id: str, message: str) -> dict:
        return self._post(f"/vehicles/{vehicle_id}/request-message", {"message": message})

    def reboot(self, vehicle_id: str, confirm: str | None = None) -> dict:
        """Reboot the flight controller. Disarmed only; needs ``confirm="REBOOT"``."""
        return self._post(f"/vehicles/{vehicle_id}/reboot", {"confirm": confirm})

    def calibrate(self, vehicle_id: str, sensor: str, confirm: str | None = None) -> dict:
        """Calibrate gyro | level | baro | accel_simple. Disarmed only; ``confirm="CALIBRATE"``."""
        return self._post(f"/vehicles/{vehicle_id}/calibrate", {"sensor": sensor, "confirm": confirm})

    def set_home(
        self,
        vehicle_id: str,
        here: bool = False,
        lat: float | None = None,
        lon: float | None = None,
        alt: float | None = None,
    ) -> dict:
        """Set home to the current position (``here=True``) or to ``lat``, ``lon``, ``alt``."""
        body: dict[str, Any] = {"here": True} if here else {"lat": lat, "lon": lon, "alt": alt}
        return self._post(f"/vehicles/{vehicle_id}/home", body)

    def terminate(self, vehicle_id: str, confirm: str | None = None) -> dict:
        """Flight termination, motors stop at once. ``confirm="TERMINATE"`` on real and simulated aircraft."""
        return self._post(f"/vehicles/{vehicle_id}/terminate", {"confirm": confirm})

    def sticks_for(
        self,
        vehicle_id: str,
        roll: float = 0.0,
        pitch: float = 0.0,
        yaw: float = 0.0,
        throttle: float = 0.5,
        seconds: float = 1.0,
        wait: bool = True,
    ) -> dict:
        """Hold the sticks for ``seconds`` (<= 10), then centre them. Armed, manual mode."""
        params = {"roll": roll, "pitch": pitch, "yaw": yaw, "throttle": throttle, "seconds": seconds}
        return self.skill(vehicle_id, "sticks", params, wait, 15.0 + seconds)

    def attitude(
        self,
        vehicle_id: str,
        roll_deg: float = 0.0,
        pitch_deg: float = 0.0,
        yaw_rate_dps: float = 0.0,
        thrust: float = 0.5,
        seconds: float = 1.0,
        wait: bool = True,
    ) -> dict:
        """Command an attitude and thrust for ``seconds`` (<= 10) while airborne, then hover again."""
        params = {
            "roll_deg": roll_deg,
            "pitch_deg": pitch_deg,
            "yaw_rate_dps": yaw_rate_dps,
            "thrust": thrust,
            "seconds": seconds,
        }
        return self.skill(vehicle_id, "attitude", params, wait, 15.0 + seconds)

    def velocity(
        self,
        vehicle_id: str,
        vx: float = 0.0,
        vy: float = 0.0,
        vz: float = 0.0,
        yaw_rate_dps: float | None = None,
        seconds: float = 1.0,
        wait: bool = True,
    ) -> dict:
        """Command an ENU velocity (m/s, each <= 2) for ``seconds`` (<= 10) inside the safety volume."""
        params: dict[str, Any] = {"vx": vx, "vy": vy, "vz": vz, "seconds": seconds}
        if yaw_rate_dps is not None:
            params["yaw_rate_dps"] = yaw_rate_dps
        return self.skill(vehicle_id, "velocity", params, wait, 15.0 + seconds)

    # ------------------------------------------------------------------ application level (04 §3)

    def app(self) -> dict:
        """Application status: version, uptime, counts, whether a window is connected."""
        return self._get("/app")

    def ui_state(self) -> dict:
        """The window's current ``UiState`` with ``age_s``; ``state`` is None without a window."""
        return self._get("/app/ui")

    def gamepads(self) -> list[dict]:
        result = self._get("/app/gamepads")
        return result["gamepads"] if isinstance(result, dict) and "gamepads" in result else result

    def app_actions(self) -> Any:
        """Catalogue of the interface actions ``ui`` accepts."""
        return self._get("/app/actions")

    def ui(self, action: str, **args: Any) -> dict:
        """Run one window action, e.g. ``ui("set_language", lang="zh")`` -> ``{ok, data}``."""
        body = {"action": action, "args": {k: v for k, v in args.items() if v is not None}}
        if self._token:
            return self._post("/app/ui", body)
        return self._op("POST", "/app/ui", body)

    # ------------------------------------------------------------------ refly (04 §4)

    def refly_options(self, session_id: int) -> dict:
        """What a record can be flown again from: ``{plan, path, sticks, recommended}``."""
        return self._get(f"/history/sessions/{session_id}/refly")

    def refly(
        self,
        vehicle_id: str,
        session_id: int,
        mode: str = "auto",
        speed: float | None = None,
        confirm: str | None = None,
        wait: bool = True,
        timeout: float = 300.0,
    ) -> dict:
        """Fly a record again; returns ``{plan}`` or ``{skill}`` (finished when ``wait``)."""
        body: dict[str, Any] = {"session_id": session_id, "mode": mode}
        if speed is not None:
            body["speed"] = speed
        if confirm:
            body["confirm"] = confirm
        started = self._post(f"/vehicles/{vehicle_id}/refly", body)
        if not wait:
            return started
        if started.get("skill"):
            return {"skill": self.wait_skill(vehicle_id, started["skill"]["id"], timeout)}
        if started.get("plan"):
            return {"plan": self.wait_plan(vehicle_id, started["plan"]["id"], timeout)}
        return started

    # ------------------------------------------------------------------ operator helpers (operator token)

    def add_sims(self, count: int = 1, autopilot: str = "ardupilot") -> list[str]:
        return self._op("POST", "/sims", {"count": count, "autopilot": autopilot})["added"]

    def remove_sims(self, ids: list[str] | None = None) -> list[str]:
        return self._op("DELETE", "/sims", query={"id": ids} if ids else None)["removed"]

    def possess(self, vehicle_id: str, input: str = "keyboard", pad: int | None = None) -> dict:
        return self._op("POST", f"/vehicles/{vehicle_id}/possess", {"input": input, "pad": pad})

    def release(self, vehicle_id: str) -> dict:
        return self._op("POST", f"/vehicles/{vehicle_id}/release")


class _WebSocket:
    """Minimal RFC 6455 client: text frames, ping/pong, close. No extensions."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self._buffer = b""
        self.lock = threading.Lock()

    @classmethod
    def connect(cls, url: str, timeout: float | None = None) -> _WebSocket:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        sock = socket.create_connection((parsed.hostname, port), timeout=10.0)
        if parsed.scheme == "wss":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parsed.hostname)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        handshake = (
            f"GET {path} HTTP/1.1\r\nHost: {parsed.netloc}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(handshake.encode("ascii"))
        ws = cls(sock)
        header = ws._read_until(b"\r\n\r\n")
        status = header.split(b"\r\n", 1)[0]
        if b" 101 " not in status + b" ":
            sock.close()
            raise RelayError("stream_rejected", 0, f"WebSocket handshake failed: {status.decode('latin-1')}")
        sock.settimeout(timeout)
        return ws

    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self._buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RelayError("stream_closed", 0, "The stream closed during the handshake.")
            self._buffer += chunk
        head, _, self._buffer = self._buffer.partition(marker)
        return head

    def _read(self, n: int) -> bytes:
        while len(self._buffer) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("closed")
            self._buffer += chunk
        data, self._buffer = self._buffer[:n], self._buffer[n:]
        return data

    def send(self, text: str, opcode: int = 0x1) -> None:
        payload = text.encode("utf-8") if isinstance(text, str) else text
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            header = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self.lock:
            self.sock.sendall(header + mask + masked)

    def recv(self) -> str | None:
        """Next text message, or None when the server closes."""
        message = b""
        while True:
            try:
                b0, b1 = self._read(2)
            except (ConnectionError, OSError):
                return None
            opcode = b0 & 0x0F
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else None
            payload = self._read(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                return None
            if opcode == 0x9:
                self.send(payload, opcode=0xA)  # type: ignore[arg-type]
                continue
            if opcode == 0xA:
                continue
            message += payload
            if b0 & 0x80:
                return message.decode("utf-8")

    def close(self) -> None:
        try:
            self.send(b"", opcode=0x8)  # type: ignore[arg-type]
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
