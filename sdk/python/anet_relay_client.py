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
__version__ = "0.3.1"

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

    def _app(self, method: str, path: str, body: Any = None, query: dict | None = None) -> Any:
        """An app operation (04 §3): as the open session (needs agents_app_control), else as the operator."""
        return self.request(method, path, body, auth="session" if self._token else "operator", query=query)

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
        return self._app("POST", "/app/ui", body)

    def scan_network(self, networks: list[str] | None = None) -> dict:
        """Start a network scan for aircraft and bridges."""
        return self._app("POST", "/discover", {"networks": networks} if networks else {})

    def connect_vehicle(self, host: str, port: int | None = None) -> dict:
        return self._app("POST", "/vehicles", {"host": host, "port": port} if port else {"host": host})

    def remove_vehicle(self, vehicle_id: str) -> dict:
        return self._app("DELETE", f"/vehicles/{vehicle_id}")

    def settings(self) -> dict:
        return self._get("/settings")

    def update_settings(self, **patch: Any) -> dict:
        """Change settings; agents_app_control itself belongs to the operator."""
        return self._app("PUT", "/settings", patch)

    def ack_alert(self, alert_id: str) -> dict:
        return self._app("POST", f"/alerts/{alert_id}/ack")

    def stop_all(self) -> dict:
        """EMERGENCY: throttle to zero and disarm every aircraft."""
        return self._app("POST", "/stop-all")

    def set_param(self, vehicle_id: str, name: str, value: float) -> dict:
        """Write one parameter: needs control of the aircraft (``claim``) or the operator token."""
        body = {"name": name, "value": value, "confirm": name}
        return self.request(
            "POST", f"/vehicles/{vehicle_id}/params", body, auth="session" if self._token else "operator"
        )

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

    # ------------------------------------------------------------------ bench procedures (05 §4)

    def procedures(self) -> list[dict]:
        """The catalogue: ``[{id, title_en, title_zh, steps, est_s}]``."""
        return self._get("/procedures")["procedures"]

    def procedure_start(
        self,
        vehicle_id: str,
        procedure: str,
        hover_s: float | None = None,
        skip: list[str] | None = None,
        auto_answer: bool = False,
    ) -> dict:
        """Start a procedure: full_bench, preflight_quick, motor_check, vibration_check or hover_tune.

        Needs control of the aircraft. ``auto_answer`` (simulators only) answers S6 from the servo outputs.
        """
        options: dict[str, Any] = {}
        if hover_s is not None:
            options["hover_s"] = hover_s
        if skip:
            options["skip"] = skip
        if auto_answer:
            options["auto_answer"] = True
        return self._post(f"/vehicles/{vehicle_id}/procedure", {"procedure": procedure, "options": options})

    def procedure(self, vehicle_id: str) -> dict:
        """The current or most recent run: steps, the waiting prompt, metrics, findings, suggestions."""
        return self._get(f"/vehicles/{vehicle_id}/procedure")

    def procedure_answer(
        self, vehicle_id: str, step: str, answer: str, confirm: str | None = None, value: Any = None
    ) -> dict:
        """Answer the waiting prompt of ``step``. Prompts marked ``operator_only`` refuse agents."""
        body: dict[str, Any] = {"step": step, "answer": answer}
        if confirm is not None:
            body["confirm"] = confirm
        if value is not None:
            body["value"] = value
        return self._post(f"/vehicles/{vehicle_id}/procedure/answer", body)

    def procedure_abort(self, vehicle_id: str) -> dict:
        """Abort the run: motors stop, an airborne aircraft lands."""
        return self._post(f"/vehicles/{vehicle_id}/procedure/abort")

    def wait_procedure(self, vehicle_id: str, timeout: float = 600.0, waiting: bool = True) -> dict:
        """Poll until the run ends (or, with ``waiting``, until a prompt waits for an answer)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            view = self.procedure(vehicle_id)
            if view["state"] not in ("running", "waiting") or (waiting and view["state"] == "waiting"):
                return view
            time.sleep(0.5)
        raise RelayError("timeout", 0, f"the procedure did not finish in {timeout:g} s")

    def test_runs(self, vehicle_id: str | None = None, limit: int = 50) -> list[dict]:
        """Saved bench reports, newest first (``vehicle_id`` matches the id or the address)."""
        return self._get("/test-runs", vehicle=vehicle_id, limit=limit)["runs"]

    def test_run(self, run_id: int) -> dict:
        return self._get(f"/test-runs/{run_id}")

    def test_run_export(self, run_id: int, fmt: str = "md") -> Any:
        """The report as Markdown text (``fmt="md"``) or as a JSON object."""
        return self._get(f"/test-runs/{run_id}/export", fmt=fmt)

    # names of 05 §4.4 (the same calls as procedures() and procedure())
    procedure_list = procedures
    procedure_status = procedure

    # ------------------------------------------------------------------ LAN arena (05 §7)

    def arena(self) -> dict:
        """The arena view: this terminal's seat and callsign, peers and their simulators, rounds in flight, and
        the local ammunition, hit points and score. Real aircraft never take part."""
        return self._get("/arena")

    def fire(self, vehicle_id: str, seconds: float = 1.0) -> dict:
        """Hold the trigger of a claimed, armed, airborne simulator for ``seconds`` (at most 3; 0 releases it):
        8 rounds per second, 24 per magazine. Needs this terminal in the arena (``arena_off``); ``sim_only`` for
        a real aircraft. Returns ``{id, firing, ammo, max, reloading, hp}``."""
        return self._post(f"/vehicles/{vehicle_id}/fire", {"seconds": float(seconds)})

    # ------------------------------------------------------------------ simulators (app operations)

    def add_sims(
        self, count: int = 1, autopilot: str = "ardupilot", hover_assist: bool | None = None
    ) -> list[str]:
        """Add simulators; ``hover_assist=False`` gives physical flight (omitted: the setting)."""
        body: dict[str, Any] = {"count": count, "autopilot": autopilot}
        if hover_assist is not None:
            body["hover_assist"] = hover_assist
        return self._app("POST", "/sims", body)["added"]

    def sim_assist(self, vehicle_id: str, on: bool) -> dict:
        """Switch a simulator's hover assist (05 §5), armed and in flight too; returns ``{id, hover_assist}``.

        Off: physical flight (throttle is thrust, hold modes refused, airborne aircraft move to STABILIZE).
        Needs the claim on the aircraft, or one nobody controls; ``sim_only`` for a real aircraft.
        """
        return self._app("POST", f"/sims/{vehicle_id}/assist", {"on": bool(on)})

    def remove_sims(self, ids: list[str] | None = None) -> list[str]:
        return self._app("DELETE", "/sims", query={"id": ids} if ids else None)["removed"]

    # ------------------------------------------------------------------ onboard programs (06 §2, §3, §6)
    # WP-4b: the same operations as the Copilot program tools and the app MCP server. Store writes, validation
    # and tests need the open session (or the operator token); deploying, stopping, resuming and tuning need
    # control of the aircraft (the claim) or the operator.

    def _either(self, method: str, path: str, body: Any = None, query: dict | None = None) -> Any:
        """As the open session, otherwise with the operator token."""
        return self.request(method, path, body, auth="session" if self._token else "operator", query=query)

    def program_list(self) -> list[dict]:
        """Built-in examples (read-only) and stored programs: ``[{id, name, builtin, rev, test_count}]``."""
        return self._get("/programs")["programs"]

    def program_get(self, program_id: str) -> dict:
        """One program with ``source``, ``params``, ``tests`` and its current ``rev``."""
        return self._get(f"/programs/{_q(program_id)}")["program"]

    def program_revisions(self, program_id: str) -> list[dict]:
        """Up to the last 50 revisions, newest first: ``[{rev, source, params, author, note, t}]``."""
        return self._get(f"/programs/{_q(program_id)}/revisions")["revisions"]

    def program_create(
        self,
        name: str,
        source: str | None = None,
        description: str | None = None,
        params: dict | None = None,
        tests: list[dict] | None = None,
        note: str | None = None,
        copy_of: str | None = None,
    ) -> dict:
        """Create a program (or a copy of ``copy_of``); returns ``{program, validation}``."""
        body = _drop_none(
            name=name,
            source=source,
            description=description,
            params=params,
            tests=tests,
            note=note,
            copy_of=copy_of,
        )
        return self._either("POST", "/programs", body)

    def program_update(
        self,
        program_id: str,
        source: str | None = None,
        patch: list[dict] | None = None,
        note: str | None = None,
        name: str | None = None,
        description: str | None = None,
        params: dict | None = None,
        tests: list[dict] | None = None,
    ) -> dict:
        """A new revision from the whole ``source`` or a line ``patch`` ``[{start, end, text}]`` (1-based,
        inclusive, numbered against the current revision; ``end = start - 1`` inserts). Returns
        ``{program, validation}``."""
        body = _drop_none(
            source=source,
            patch=patch,
            note=note,
            name=name,
            description=description,
            params=params,
            tests=tests,
        )
        return self._either("PUT", f"/programs/{_q(program_id)}", body)

    def program_delete(self, program_id: str) -> dict:
        return self._either("DELETE", f"/programs/{_q(program_id)}")

    def program_validate(self, program_id: str, source: str | None = None) -> dict:
        """Language check only: ``{ok, errors: [{line, col, code, message_en, message_zh}], params, ...}``."""
        return self._either("POST", f"/programs/{_q(program_id)}/validate", _drop_none(source=source))

    def program_test(
        self,
        program_id: str,
        rev: int | None = None,
        source: str | None = None,
        tests: list[dict] | None = None,
        seed: int | None = None,
    ) -> dict:
        """Run the unit tests (06 §3.1): ``{program, results, passed, total}``."""
        body = _drop_none(rev=rev, source=source, tests=tests, seed=seed)
        return self._either("POST", f"/programs/{_q(program_id)}/test", body)

    def program_deploy(
        self,
        vehicle_id: str,
        program_id: str,
        rev: int | None = None,
        params: dict | None = None,
        mode: str = "pilot",
        sensors: dict | None = None,
        arena: dict | None = None,
        seed: int | None = None,
        airframe: dict | None = None,
    ) -> dict:
        """Run a revision on an aircraft at 50 Hz (06 §2.6); returns ``{id, program}`` (the run view).

        ``mode`` is ``pilot`` or ``copilot``. Simulators also take ``sensors`` ``{baro, position, contacts}``,
        ``arena`` ``{radius, floor, ceiling}``, ``seed`` and ``airframe`` ``{preset | params, seed?}``. A real
        aircraft needs the operator's ``Allow programs`` switch (``programs_not_allowed``) and preflight.
        """
        body = _drop_none(
            program=program_id,
            rev=rev,
            params=params,
            mode=mode,
            sensors=sensors,
            arena=arena,
            seed=seed,
            airframe=airframe,
        )
        return self._either("POST", f"/vehicles/{vehicle_id}/program", body)

    def program_stop(self, vehicle_id: str) -> dict:
        return self._either("POST", f"/vehicles/{vehicle_id}/program/stop", {})

    def program_resume(self, vehicle_id: str) -> dict:
        """Resume a paused program (possession pauses it; releasing never resumes it by itself)."""
        return self._either("POST", f"/vehicles/{vehicle_id}/program/resume", {})

    def program_params(self, vehicle_id: str, params: dict | None = None, **values: Any) -> dict:
        """Live parameters of the running program: ``program_params("sim-1", kp=0.3)``."""
        patch = {**(params or {}), **values}
        return self._either("PATCH", f"/vehicles/{vehicle_id}/program/params", patch)

    def program_status(self, vehicle_id: str) -> dict | None:
        """The run view ``{id, name, rev, state, status, fps, faults, last_error, mode, ...}`` or None."""
        return self.vehicle(vehicle_id).get("program")

    def program_log(self, vehicle_id: str, after: int = 0, limit: int = 500) -> dict:
        """Log lines with ``seq > after``: ``{id, program, lines: [{seq, t, level, text}], last_seq}``."""
        return self._get(f"/vehicles/{vehicle_id}/program/log", after=after, limit=limit)

    def program_allow(self, vehicle_id: str, allow: bool = True, confirm: str | None = "PROGRAM") -> dict:
        """Operator only: ``Allow programs on this aircraft`` (real aircraft; reset when the link drops)."""
        return self._op("POST", f"/vehicles/{vehicle_id}/program/allow", {"allow": allow, "confirm": confirm})

    # judge (06 §3.2-§3.4): headless evaluation of challenges and duels, run as jobs

    def _judge(self, method: str, path: str, body: Any = None, query: dict | None = None) -> Any:
        """A judge call; a Relay without the judge API raises ``judge_unavailable``, not ``not_found``."""
        try:
            return self._either(method, path, body, query)
        except RelayError as exc:
            if exc.code != "not_found":
                raise
            present = False
            if path != "/challenges":
                try:
                    self._get("/challenges")
                    present = True
                except RelayError as probe:
                    present = probe.code != "not_found"
            if present:
                raise
            raise RelayError(
                "judge_unavailable",
                404,
                "This Relay has no judge API (challenges, jobs, duels, ladder); it comes with Relay 0.4.0.",
                "此 Relay 没有评测接口（题目、评测任务、对抗、排名），该接口随 Relay 0.4.0 提供。",
            ) from exc

    def challenge_list(self) -> Any:
        """The challenges with scenario, pass condition, best score and status."""
        return self._judge("GET", "/challenges")

    def challenge_submit(
        self,
        challenge_id: str,
        program_id: str,
        rev: int | None = None,
        wait: bool = True,
        timeout: float = 300.0,
    ) -> dict:
        """Judge a revision; with ``wait`` returns the finished job ``{state, progress, result}``."""
        body = _drop_none(program=program_id, rev=rev)
        job = self._judge("POST", f"/challenges/{_q(challenge_id)}/submit", body)
        return self.wait_job(_job_id(job), timeout) if wait and _job_id(job) is not None else job

    def job_status(self, job_id: str) -> dict:
        job = self._judge("GET", f"/jobs/{_q(job_id)}")
        return job.get("job", job) if isinstance(job, dict) else job

    def wait_job(self, job_id: str, timeout: float = 300.0, interval: float = 0.5) -> dict:
        """Poll an evaluation job until it is no longer queued or running."""
        end = time.monotonic() + timeout
        while True:
            job = self.job_status(job_id)
            if job.get("state") not in ("queued", "running", "pending"):
                return job
            if time.monotonic() >= end:
                raise RelayError("timeout", 0, f"job {job_id} did not finish in {timeout:g} s", detail=job)
            time.sleep(interval)

    def duel_run(
        self,
        programs: list[str],
        rounds: int | None = None,
        seed: int | None = None,
        scenario: dict | None = None,
        wait: bool = True,
        timeout: float = 600.0,
    ) -> dict:
        """2 to 8 programs against each other; the result has the rounds and the Elo table."""
        body = _drop_none(programs=list(programs), rounds=rounds, seed=seed, scenario=scenario)
        job = self._judge("POST", "/duels", body)
        return self.wait_job(_job_id(job), timeout) if wait and _job_id(job) is not None else job

    def ladder(self) -> Any:
        """The Elo ladder of all duels so far."""
        return self._judge("GET", "/ladder")

    def trace(self, trace_id: str) -> Any:
        """The downsampled trace (10 Hz, every aircraft) of one judged round, for replay."""
        return self._judge("GET", f"/traces/{_q(trace_id)}")

    # simulator airframe defects (06 §4)

    def airframe_get(self, vehicle_id: str) -> dict:
        """``{id, preset, seed, params, active, hover_throttle, limits}`` of a simulator."""
        return self._either("GET", f"/sims/{vehicle_id}/airframe")

    def airframe_set(
        self, vehicle_id: str, preset: str | None = None, params: dict | None = None, seed: int | None = None
    ) -> dict:
        """Fit defects to a simulator: ``preset`` ideal | typical | rough (with ``seed``), or ``params`` for a
        custom airframe. They act with hover assist off. ``sim_only`` for a real aircraft."""
        return self._either(
            "PUT", f"/sims/{vehicle_id}/airframe", _drop_none(preset=preset, params=params, seed=seed)
        )

    # ------------------------------------------------------------------ operator helpers (operator token)

    def possess(self, vehicle_id: str, input: str = "keyboard", pad: int | None = None) -> dict:
        return self._op("POST", f"/vehicles/{vehicle_id}/possess", {"input": input, "pad": pad})

    def release(self, vehicle_id: str) -> dict:
        return self._op("POST", f"/vehicles/{vehicle_id}/release")


def _q(value: str) -> str:
    return urllib.parse.quote(str(value), safe="")


def _drop_none(**values: Any) -> dict:
    return {k: v for k, v in values.items() if v is not None}


def _job_id(job: Any) -> str | None:
    if not isinstance(job, dict):
        return None
    inner = job.get("job")
    if isinstance(inner, dict) and inner.get("id") is not None:
        return str(inner["id"])
    for key in ("job_id", "id"):
        if job.get(key) is not None:
            return str(job[key])
    return None


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
