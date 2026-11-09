"""FleetService: the vehicle table, the main loop and every operation the API exposes (03 §3.1).

One background thread owns all mutable state. It runs a 200 Hz tick: receive UDP, run queued commands,
step drivers (simulators included), send sticks and skill setpoints at 50 Hz, GCS heartbeats at 1 Hz,
housekeeping (link states, alerts, leases, records, course aborts) at 5 Hz and the fleet snapshot at 10 Hz.
API threads change state only through ``call`` (queued, executed on the loop, 2 s timeout) and read the
cached snapshot. Without a running thread, ``call`` executes inline and ``step`` is driven by the caller
(unit tests use this with a fake clock).
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import logging
import math
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, TypeVar

from . import authority as auth
from . import course as coursemod
from . import (
    device,
    flight_level,  # WP-1: agent flight level (06 §5)
    plans,
    preflight,
    refly,
    skills,
)
from .alerts import AlertBook, Condition
from .authority import Actor, Authority
from .config import (
    CALL_TIMEOUT_S,
    COURSE_RESULT_HOLD_S,
    HEARTBEAT_PERIOD_S,
    HOUSEKEEP_HZ,
    LINK_DELAYED_S,
    LINK_LOST_S,
    LINK_OFFLINE_S,
    LOSS_WINDOW_S,
    MODE_CONFIRM_S,
    PARAM_FETCH_DELAY_S,
    PILOT_OVERRIDE,
    POSITION_FRESH_S,
    POSITION_MIN_HZ,
    RATE_WINDOW_S,
    SIM_LIMIT,
    SNAPSHOT_HZ,
    STATUSTEXT_SNAPSHOT,
    STICK_HZ,
    STICK_TIMEOUT_S,
    STOP_THROTTLE_S,
    STOP_WINDOW_S,
    STREAM_RECHECK_S,
    TICK_HZ,
    TIMESYNC_PERIOD_S,
    TIMESYNC_STALE_S,
    VERSION,
    Settings,
    save_settings,
)
from .drivers.mavlink import MavlinkDriver
from .drivers.sim import SimDriver
from .errors import RelayError, err
from .events import EventBus
from .fleet_airframe import AirframeOps  # WP-2a airframe defects (06 §4)
from .fleet_device import DeviceOps
from .fleet_sequence import SequenceOps  # stick timelines, .ars (10 §2)
from .fleet_sim import SimOps
from .fleet_world import WorldOps  # WP-A collisions (07 §2)
from .flighthealth import FlightHealth
from .history import History
from .judge.service import JudgeService  # WP-2b: challenges, duels and the headless evaluator (06 §3)
from .mav import bridge as bridgemod
from .mav import decode, discovery, modes
from .mav.link import UdpLink
from .mav.mavlink import M, Parser
from .mav.params import ESSENTIAL, NamedReads, ParamFetch, normalise, same_value, type_name
from .model import INPUT_KINDS, Calibration, Capability, Confirmed, Signs, StickInput
from .netinfo import local_networks, on_bridge_ap, scan_hosts
from .peers.game import Arena
from .procedures import ProcedureOps
from .programs.service import ProgramService  # WP-1: onboard programs (06 §2)
from .scenarios import Dogfights  # WP-B: dogfight scenario (07 §3)
from .sessions import Session, SessionManager
from .sim.autopilot import ARDUPILOT, PX4, SimAutopilot
from .sim.world import World  # WP-A (07 §2)

log = logging.getLogger("relay.fleet")
T = TypeVar("T")

SIM_SPAWNS = [
    (0.0, 0.0),
    (2.0, 0.0),
    (-2.0, 0.0),
    (4.0, 0.0),
    (0.0, -2.0),
    (2.0, -2.0),
    (-2.0, -2.0),
    (4.0, -2.0),
]
KEY_MESSAGES = ("HEARTBEAT", "SYS_STATUS", "ATTITUDE", "VFR_HUD", "BATTERY_STATUS")
ARDUPILOT_STREAMS = (
    (M.MAV_DATA_STREAM_EXTRA1, 20),
    (M.MAV_DATA_STREAM_EXTRA2, 4),
    (M.MAV_DATA_STREAM_EXTRA3, 2),
    (M.MAV_DATA_STREAM_EXTENDED_STATUS, 2),
    (M.MAV_DATA_STREAM_POSITION, 10),
    (M.MAV_DATA_STREAM_RC_CHANNELS, 4),
    (M.MAV_DATA_STREAM_RAW_CONTROLLER, 0),
)
PX4_INTERVALS = (
    (M.MAVLINK_MSG_ID_ATTITUDE, 20),
    (M.MAVLINK_MSG_ID_LOCAL_POSITION_NED, 10),
    (M.MAVLINK_MSG_ID_GLOBAL_POSITION_INT, 4),
    (M.MAVLINK_MSG_ID_SYS_STATUS, 2),
    (M.MAVLINK_MSG_ID_BATTERY_STATUS, 2),
    (M.MAVLINK_MSG_ID_VFR_HUD, 4),
    (M.MAVLINK_MSG_ID_EXTENDED_SYS_STATE, 2),
    (M.MAVLINK_MSG_ID_RC_CHANNELS, 4),
    (M.MAVLINK_MSG_ID_SERVO_OUTPUT_RAW, 4),
    (M.MAVLINK_MSG_ID_VIBRATION, 2),
)
SIM_POSITION_HZ = 50.0
ARM_PRIME_S = 0.4  # zero-throttle sticks reach the autopilot before an arm command (10 §1)
BATTERY_SUSTAIN_S = 2.0
BATTERY_HYSTERESIS_V = 0.05
POSITION_ABORT_S = 1.0
MODE_ALERT_HOLD_S = 10.0


def _round(value: Any, digits: int = 3) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, digits)
    return value


class Vehicle:
    """Runtime record for one aircraft. Owned by the fleet thread."""

    def __init__(
        self, vid: str, kind: str, label: str, driver: Any, host: str | None = None, port: int = 0
    ) -> None:
        self.id = vid
        self.kind = kind
        self.label = label
        self.driver = driver
        self.host = host
        self.port = port
        self.parser = Parser()
        self.state = decode.fresh_state()
        self.auth = Authority()
        self.sticks: StickInput | None = None
        self.skill: skills.Skill | None = None
        self.stop_seq: dict[str, Any] | None = None
        self.stop_window_until = 0.0
        self.calibration = Calibration(hover=0.5 if kind == "sim" else 0.35)
        self.confirmed = Confirmed()
        self.course: coursemod.CourseRun | None = None
        self.link_state = "waiting"
        self.bridge: dict[str, Any] | None = None
        self.spawn: tuple[float, float] = (0.0, 0.0)
        self.frame_offset = (0.0, 0.0, 0.0)
        self.safety_origin: tuple[float, float, float] | None = None
        self.identity_at: float | None = None
        self.stream_check_at = 0.0
        self.param_due: float | None = None
        self.param_fetch: ParamFetch | None = None
        self.param_list_sent = 0
        self.param_named: NamedReads | None = None
        self.mode_wait: tuple[str, float, bool] | None = None
        self.mode_alert: tuple[str, float] | None = None
        self.record_session: int | None = None
        self.segment: tuple[str, str | None, float] | None = None
        self.flight_t0: float | None = None
        self.flight_time_s = 0.0
        self.armed_t0: float | None = None
        self.armed_time_s = 0.0
        self.last_armed = False
        self.last_mode: str | None = None
        self.battery_level = "unknown"
        self.battery_pending: tuple[str, float] | None = None
        self.rx_times: deque[float] = deque(maxlen=1024)
        self.seq_last: dict[tuple[int, int], int] = {}
        self.loss_buckets: deque[list[float]] = deque()
        self.timesync_sent: dict[int, float] = {}
        self.rtt_ms: float | None = None
        self.rtt_at: float | None = None
        self.airborne_alerted = False
        self.baro_ground: float | None = None  # VFR_HUD altitude on the ground (altitude fallback)
        self.ground_alt: float | None = None  # altitude() when the aircraft armed
        self.plan: plans.Plan | None = None
        self.rc_frame: dict[str, Any] | None = None  # channel override resent at the stick rate (04 §2)
        self.motor_seq: dict[str, Any] | None = None  # "all motors" test in progress
        self.motor_until = 0.0  # clock time until which a motor test may still be spinning
        self.deferred: list[tuple[float, Callable[[Any], None]]] = []
        self.refly: dict[str, Any] | None = None  # the re-fly in progress (04 §4)
        self.seq_record: dict[str, Any] | None = None  # sticks being recorded into a sequence (10 §2)
        self.procedure: Any = None  # bench procedure run, current or last (WP-B, 05 §4.3)
        self.health = FlightHealth()
        self.position_lost_since: float | None = None
        self.course_clear_at: float | None = None
        self.crash_event = False
        self.heartbeat_at = 0.0
        self.timesync_at = 0.0
        self.created = 0.0
        self.clock: Callable[[], float] = time.monotonic
        # WP-1 onboard programs (06 §2.6): the current or last program run, and the real-aircraft gate
        self.program: Any = None
        self.programs_allowed = False

    # Host protocol for skills ---------------------------------------------------

    @property
    def autopilot(self) -> str | None:
        return self.state["autopilot"]

    @property
    def armed(self) -> bool:
        return bool(self.state["armed"])

    @property
    def mode(self) -> str | None:
        return self.state["mode"]

    @property
    def landed(self) -> str:
        landed = self.state["landed"]
        if landed and landed != "unknown":
            return landed
        if self.state["last_heartbeat"] is None:
            return "unknown"
        if not self.armed:
            return "ground"
        alt = self.altitude()
        if alt is None:
            return "unknown"
        return "air" if alt > 0.3 else "ground"

    def airborne(self) -> bool:
        return self.armed and self.landed in ("air", "takeoff", "landing")

    def has_capability(self, name: str) -> bool:
        return self.capabilities.has(name)

    def local_enu(self) -> tuple[float, float, float] | None:
        local = self.state["local"]
        if local is None:
            return None
        return (local["y"], local["x"], -local["z"])

    def world_enu(self) -> tuple[float, float, float] | None:
        local = self.local_enu()
        if local is None:
            return None
        ox, oy, oz = self.frame_offset
        return (local[0] + ox, local[1] + oy, local[2] + oz)

    def altitude(self) -> float | None:
        local = self.state["local"]
        if local is not None:
            return -local["z"]
        glob = self.state["global"]
        if glob is not None:
            return glob["relative_alt"]
        # Barometer only: VFR_HUD altitude above the reading taken on the ground.
        hud = self.state["hud"] or {}
        if hud.get("alt") is not None and self.baro_ground is not None:
            return float(hud["alt"]) - self.baro_ground
        return None

    def heading(self) -> float | None:
        att = self.state["attitude"]
        return None if att is None else float(att["yaw"])

    def tilt(self) -> tuple[float, float] | None:
        att = self.state["attitude"]
        return None if att is None else (float(att["roll"]), float(att["pitch"]))

    def speed(self) -> float | None:
        local = self.state["local"]
        if local is None:
            return None
        return math.sqrt(local["vx"] ** 2 + local["vy"] ** 2 + local["vz"] ** 2)

    def param(self, name: str, default: float) -> float:
        entry = self.state["params"].get(name)
        return default if entry is None else float(entry["value"])

    def home_amsl(self) -> float | None:
        glob = self.state["global"]
        if glob is None or not glob.get("valid"):
            return None
        return glob["alt"] - glob["relative_alt"]

    def ack_after(self, command: int, t: float) -> dict | None:
        ack = self.state.get("acks", {}).get(command)
        if ack is not None and ack["t"] >= t:
            return ack
        return None

    # Derived facts ---------------------------------------------------------------

    @property
    def capabilities(self) -> Capability:
        cap = Capability()
        if self.state["sysid"] is None or self.link_state not in ("ok", "delayed"):
            return cap
        cap.manual = True
        present = self.state["sensor_bits"][0]
        cap.althold = modes.has_althold_mode(self.autopilot) and bool(
            present & M.MAV_SYS_STATUS_SENSOR_ABSOLUTE_PRESSURE
        )
        cap.position = self.position_ok()
        return cap

    def position_ok(self, now: float | None = None) -> bool:
        local = self.state["local"]
        if local is None:
            return False
        now = self.clock() if now is None else now
        if now - local["t"] > POSITION_FRESH_S:
            return False
        if decode.message_rate(self.state["local_times"], now) < POSITION_MIN_HZ:
            return False
        ekf = self.state["ekf_flags"]
        if ekf is not None and (not ekf & M.EKF_POS_HORIZ_REL or ekf & M.EKF_CONST_POS_MODE):
            return False
        est = self.state["estimator_flags"]
        return not (est is not None and not est & M.ESTIMATOR_POS_HORIZ_REL)

    def has_baro(self) -> bool:
        return bool(self.state["sensor_bits"][0] & M.MAV_SYS_STATUS_SENSOR_ABSOLUTE_PRESSURE)

    def cells(self) -> int | None:
        battery = self.state["battery"]
        if battery["cells"]:
            return battery["cells"]
        voltage = battery["voltage"]
        if voltage is None or voltage < 1.0:
            return None
        return max(1, math.ceil(voltage / 4.35))

    def controller(self, sessions: SessionManager) -> str:
        if self.program is not None and self.auth.possession is None and self.program.flying:
            return "program"  # WP-1: a pilot-mode program holds the sticks (06 §2.6)
        claim = self.auth.claim
        return auth.controller(self.auth, claim is not None and sessions.alive(claim.session_id))


class FleetService(DeviceOps, SimOps, AirframeOps, SequenceOps, ProcedureOps, WorldOps):
    def __init__(
        self,
        settings: Settings | None = None,
        data_dir: Path | None = None,
        *,
        operator_token: str = "",
        listen: bool = True,
        allow_loopback: bool = False,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        history: History | None = None,
        http_port: int | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.data_dir = data_dir
        self.operator_token = operator_token
        self.clock = clock
        self.wall = wall
        self.http_port = http_port
        self.allow_loopback = allow_loopback
        # Test seam (RELAY_SCAN_NETWORKS): networks and bridge HTTP port used when a scan names none.
        self.scan_default: tuple[list[str], int] | None = None
        self.started = clock()
        self.events = EventBus(wall=wall)
        self.sessions = SessionManager(clock=clock)
        self.alerts = AlertBook(wall)
        if history is None:
            path = data_dir / "history.db" if data_dir is not None else None
            history = History(path, self.settings.retention_days)
        self.history = history
        self.link: UdpLink | None = (
            UdpLink(self.settings.listen_port, allow_loopback=allow_loopback) if listen else None
        )
        self.vehicles: dict[str, Vehicle] = {}
        self.by_host: dict[str, str] = {}
        self._pending_parsers: dict[str, Parser] = {}
        self._labels: dict[str, str] = {}
        self._cmds: queue.Queue[tuple[Callable[[], Any], concurrent.futures.Future | None]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._stop = threading.Event()
        self._snapshot: dict[str, Any] = {}
        self._snapshot_dirty = True
        self._next = {"stick": 0.0, "heartbeat": 0.0, "house": 0.0, "snapshot": 0.0, "listen_retry": 0.0}
        self.scan: dict[str, Any] = {
            "running": False,
            "done": 0,
            "total": 0,
            "networks": [],
            "found": 0,
            "error": None,
        }
        self._scan_stop = threading.Event()
        self._scan_broadcast: list[str] = []
        self._scan_broadcast_at = 0.0
        self.custom_courses: dict[str, dict[str, Any]] = self._load_courses()
        self._best: dict[str, float | None] = {}
        self._skill_seq = 0
        self._plan_seq = 0
        self.events.add_listener(self._record_event)
        if self.link is not None:
            self._open_listen()
        self._labels = self.history.labels()
        # LAN arena (05 §7, WP-D): its own UDP socket and rules; peers never enter self.vehicles
        self.arena = Arena(self, network=listen)
        self.world = World()  # WP-A: the local simulators' collision world (07 §2)
        self.arena.apply_settings()
        # WP-1 onboard programs (06 §2): store, program host and the 50 Hz program tick
        self.programs = ProgramService(self)
        # WP-2b challenges and duels (06 §3.2-§3.4): job queue, worker processes, results, ladder, traces
        self.judge = JudgeService(self)
        # WP-B (07 §3): the dogfight scenario and its supervising worker
        self.dogfights = Dogfights(self)
        self._build_snapshot(self.clock())

    # ====================================================================== lifecycle

    def start(self) -> None:
        if self._thread is not None:
            return
        self.history.start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="relay-fleet", daemon=True)
        self._thread.start()
        self._running.wait(2.0)

    def shutdown(self) -> None:
        self._scan_stop.set()
        if self._thread is not None:
            try:
                self.call(self._shutdown_vehicles, timeout=2.0)
            except RelayError:
                pass
            self._stop.set()
            self._thread.join(timeout=3.0)
            self._thread = None
        else:
            self._shutdown_vehicles()
        if self.link is not None:
            self.link.close()
        self.arena.close()
        self.dogfights.close()  # WP-B
        self.judge.close()  # WP-2b: cancel evaluation jobs and wait for their workers
        self.programs.close()  # WP-1
        self.history.close()

    def _shutdown_vehicles(self) -> None:
        now = self.clock()
        wall = self.wall()
        for v in self.vehicles.values():
            self._stop_control(v, "shutdown", now)
            self._close_segment(v, wall)
            if v.record_session is not None:
                self.history.end_session(v.record_session, wall)
                v.record_session = None

    def _run(self) -> None:
        period = 1.0 / TICK_HZ
        self._running.set()
        while not self._stop.is_set():
            started = self.clock()
            try:
                self.step(started)
            except Exception:  # keep the loop alive; log and continue
                log.exception("fleet tick failed")
            remaining = period - (self.clock() - started)
            if remaining > 0:
                if self.link is not None and self.link.ok:
                    self.link.wait(remaining)
                else:
                    time.sleep(remaining)
        self._running.clear()

    @property
    def threaded(self) -> bool:
        return self._thread is not None and threading.current_thread() is not self._thread

    def call(self, fn: Callable[[], T], timeout: float = CALL_TIMEOUT_S) -> T:
        """Run ``fn`` on the fleet thread and return its result (or raise its RelayError)."""
        if not self.threaded:
            result = fn()
            self._snapshot_dirty = True
            return result
        future: concurrent.futures.Future = concurrent.futures.Future()
        self._cmds.put((fn, future))
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            raise err("backend_busy") from exc

    def post(self, fn: Callable[[], Any]) -> None:
        """Queue ``fn`` without waiting (used by worker threads)."""
        if self._thread is None:
            fn()
        else:
            self._cmds.put((fn, None))

    def _run_commands(self) -> None:
        while True:
            try:
                fn, future = self._cmds.get_nowait()
            except queue.Empty:
                return
            try:
                result = fn()
            except BaseException as exc:  # forwarded to the caller
                if future is not None:
                    future.set_exception(exc)
                elif not isinstance(exc, RelayError):
                    log.exception("posted command failed")
            else:
                if future is not None:
                    future.set_result(result)
            self._snapshot_dirty = True

    # ====================================================================== main tick

    def step(self, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        if self.link is not None and self.link.ok:
            for data, addr in self.link.recv_all():
                self._on_datagram(data, addr, now)
        self._run_commands()
        self._world_step(now)  # WP-A (07 §2): the simulators' physics in lockstep, with collisions
        for v in list(self.vehicles.values()):
            try:
                msgs = v.driver.tick(now)
            except Exception:
                log.exception("driver tick failed for %s", v.id)
                continue
            for msg in msgs:
                self._on_message(v, msg, now)
        # LAN arena (WP-D): fire, bullets and hits right after the simulators stepped; it keeps to its own
        # wall-time budget, and a failure in it never skips the stick, watchdog and stop path below
        try:
            self.arena.tick(now)
        except Exception:
            log.exception("arena tick failed")
        if now >= self._next["stick"]:
            self._next["stick"] = max(self._next["stick"] + 1.0 / STICK_HZ, now)
            try:
                self.programs.tick(now)  # WP-1: programs write their sticks before they are sent
            except Exception:
                log.exception("program tick failed")
            for v in list(self.vehicles.values()):
                self._stick_tick(v, now)
                self._sequence_record_tick(v, now)
                self._device_tick(v, now)
                self._plan_tick(v)
                self._refly_tick(v)
                self._health_tick(v, now)
        if now >= self._next["heartbeat"]:
            self._next["heartbeat"] = now + HEARTBEAT_PERIOD_S
            self._heartbeats(now)
        if now >= self._next["house"]:
            self._next["house"] = now + 1.0 / HOUSEKEEP_HZ
            self._housekeeping(now)
        if now >= self._next["snapshot"] or (self._snapshot_dirty and now >= self._next["snapshot"] - 0.08):
            self._next["snapshot"] = now + 1.0 / SNAPSHOT_HZ
            self._snapshot_dirty = False
            self._build_snapshot(now)

    # ====================================================================== receive

    def _open_listen(self) -> None:
        assert self.link is not None
        was_error = self.link.error
        if self.link.open():
            self.link.local_ips = {n["ip"] for n in local_networks()}
            return
        if self.link.error != was_error:
            self.events.emit("listen_error", data={"error": self.link.error, "port": self.link.port})

    def retry_listen(self) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            if self.link is None:
                raise err("listen_unavailable", port=self.settings.listen_port, reason="disabled")
            self._open_listen()
            return self._listen_view()

        return self.call(run)

    def _listen_view(self) -> dict[str, Any]:
        if self.link is None:
            return {"port": self.settings.listen_port, "ok": False, "error": "disabled"}
        return {"port": self.link.bound_port or self.link.port, "ok": self.link.ok, "error": self.link.error}

    def _on_datagram(self, data: bytes, addr: tuple[str, int], now: float) -> None:
        host, port = addr
        vid = self.by_host.get(host)
        if vid is None:
            parser = self._pending_parsers.setdefault(host, Parser())
            msgs = parser.parse(data)
            if not any(not decode.is_gcs_traffic(m) for m in msgs):
                return
            del self._pending_parsers[host]
            v = self._add_real(host, port, source="udp", now=now)
        else:
            v = self.vehicles[vid]
            msgs = v.parser.parse(data)
        if v.port != port:
            v.port = port
            v.driver.port = port
        v.driver.feed(msgs)

    def _on_message(self, v: Vehicle, msg: Any, now: float) -> None:
        wall = self.wall()
        v.rx_times.append(now)
        self._track_seq(v, msg, now)
        notes = decode.apply_message(v.state, msg, now, wall)
        for kind, payload in notes:
            if kind == "identity":
                self._on_identity(v, now)
            elif kind == "statustext":
                self.events.emit(
                    "statustext",
                    v.id,
                    data={"severity": payload["severity"], "text": payload["text"]},
                    labels={"vehicle": v.label},
                )
            elif kind == "command_ack":
                self.events.emit(
                    "command_ack",
                    v.id,
                    data={k: payload[k] for k in ("command", "command_name", "result", "result_name")},
                    labels={
                        "vehicle": v.label,
                        "command": payload["command_name"],
                        "result": payload["result_name"],
                    },
                )
            elif kind == "crash":
                self._on_crash(v, payload, now)
            elif kind == "timesync":
                sent = v.timesync_sent.pop(payload, None)
                if sent is not None:
                    v.rtt_ms = round((now - sent) * 1000.0, 1)
                    v.rtt_at = now
            elif kind == "position":
                self._on_position(v, now, wall)
            elif kind == "heartbeat":
                self._on_heartbeat(v, now, wall)
        if v.param_fetch is not None and msg.get_type() == "PARAM_VALUE":
            v.param_fetch.progress(len(v.state["params"]), v.state["param_count"], now)

    def _track_seq(self, v: Vehicle, msg: Any, now: float) -> None:
        key = (msg.get_srcSystem(), msg.get_srcComponent())
        seq = msg.get_seq()
        lost = 0
        last = v.seq_last.get(key)
        if last is not None:
            gap = (seq - last) % 256
            if 1 < gap < 128:
                lost = gap - 1
        v.seq_last[key] = seq
        second = math.floor(now)
        if not v.loss_buckets or v.loss_buckets[-1][0] != second:
            v.loss_buckets.append([second, 0.0, 0.0])
            while v.loss_buckets and v.loss_buckets[0][0] < second - LOSS_WINDOW_S:
                v.loss_buckets.popleft()
        v.loss_buckets[-1][1] += 1
        v.loss_buckets[-1][2] += lost

    def _on_identity(self, v: Vehicle, now: float) -> None:
        st = v.state
        v.driver.set_target(st["sysid"], st["compid"], st["autopilot"])
        v.identity_at = now
        self._request_streams(v)
        v.stream_check_at = now + STREAM_RECHECK_S
        # The parameters the preflight depends on are read by name at once, whatever the table setting.
        v.param_named = NamedReads(ESSENTIAL.get(st["autopilot"] or "", ()))
        if self.settings.auto_params:
            v.param_due = now + PARAM_FETCH_DELAY_S
        if v.record_session is not None:
            self.history.update_session(v.record_session, autopilot=st["autopilot"])

    def _request_streams(self, v: Vehicle) -> None:
        driver = v.driver
        if v.autopilot == modes.PX4:
            for msg_id, hz in PX4_INTERVALS:
                rate = (
                    SIM_POSITION_HZ
                    if v.kind == "sim" and msg_id == M.MAVLINK_MSG_ID_LOCAL_POSITION_NED
                    else hz
                )
                driver.set_message_interval(msg_id, rate)
        else:
            for stream, hz in ARDUPILOT_STREAMS:
                driver.request_data_stream(stream, hz)
            # Landed state is not part of a fixed ArduPilot stream group; ask for it explicitly.
            driver.set_message_interval(M.MAVLINK_MSG_ID_EXTENDED_SYS_STATE, 2)
            if v.kind == "sim":
                driver.set_message_interval(M.MAVLINK_MSG_ID_LOCAL_POSITION_NED, SIM_POSITION_HZ)
        driver.request_message(M.MAVLINK_MSG_ID_AUTOPILOT_VERSION)

    def _on_heartbeat(self, v: Vehicle, now: float, wall: float) -> None:
        if v.link_state in ("waiting", "offline") or v.link_state in ("delayed", "lost"):
            self._set_link(v, "ok", now)
        armed = v.armed
        hud = v.state["hud"] or {}
        if not armed and hud.get("alt") is not None:
            v.baro_ground = float(hud["alt"])
        if armed != v.last_armed:
            v.last_armed = armed
            if armed:
                v.health.reset()
                v.ground_alt = v.altitude()
                v.armed_t0 = now
                v.armed_time_s = 0.0
                v.flight_time_s = 0.0
                v.crash_event = False
                v.state["crashed"] = False if v.kind == "real" else v.state["crashed"]
                if v.safety_origin is None and v.kind == "real":
                    v.safety_origin = v.world_enu()
                self.events.emit("armed", v.id, labels={"vehicle": v.label})
            else:
                if v.armed_t0 is not None:
                    v.armed_time_s += now - v.armed_t0
                v.armed_t0 = None
                self.events.emit("disarmed", v.id, labels={"vehicle": v.label})
        if v.mode != v.last_mode:
            previous = v.last_mode
            v.last_mode = v.mode
            if v.mode is not None:
                self.events.emit(
                    "mode_changed",
                    v.id,
                    data={"mode": v.mode, "previous": previous},
                    labels={"vehicle": v.label},
                )
            if v.mode_wait is not None and v.mode == v.mode_wait[0]:
                v.mode_wait = None

    def _on_crash(self, v: Vehicle, text: str, now: float) -> None:
        if v.crash_event:
            return
        v.crash_event = True
        data = {"text": text, **self._crash_details(v)}  # WP-A: reason_code and point (07 §2)
        self.events.emit("crashed", v.id, data=data, labels={"vehicle": v.label})
        self.programs.on_vehicle_stop(v, "crashed")  # WP-1
        self._abort_course(v, "crashed", now)
        if v.skill is not None and v.skill.active:
            v.skill.stop("crashed", "坠毁")
            self._drain_skill(v)

    def _on_position(self, v: Vehicle, now: float, wall: float) -> None:
        run = v.course
        if run is None or not run.active:
            return
        p = v.world_enu()
        if p is None:
            return
        for kind, data in run.update(p, now, wall, v.airborne()):
            self._emit_course(v, kind, data, now)

    # ====================================================================== link and housekeeping

    def _set_link(self, v: Vehicle, state: str, now: float) -> None:
        previous = v.link_state
        if previous == state:
            return
        v.link_state = state
        wall = self.wall()
        labels = {"vehicle": v.label}
        if state == "ok":
            if previous in ("waiting", "offline"):
                self.events.emit("vehicle_online", v.id, labels=labels)
                self._start_record(v, wall)
                if previous == "offline":
                    self._request_streams(v)
            else:
                self.events.emit("link_restored", v.id, labels=labels)
                self._request_streams(v)
        elif state == "delayed":
            self.events.emit("link_delayed", v.id, labels=labels)
        elif state == "lost":
            self.events.emit("link_lost", v.id, labels=labels)
            self._stop_control(v, "link lost", now)
            self.programs.on_link(v, state)  # WP-1: the real-aircraft program gate resets
            self._abort_course(v, "link_lost", now)
        elif state == "offline":
            self.events.emit("vehicle_offline", v.id, labels=labels)
            self.programs.on_link(v, state)  # WP-1
            self._close_segment(v, wall)
            if v.record_session is not None:
                self.history.end_session(v.record_session, wall)
                v.record_session = None

    def _link_age(self, v: Vehicle, now: float) -> float | None:
        last = v.state["last_heartbeat"]
        return None if last is None else max(0.0, now - last)

    def _heartbeats(self, now: float) -> None:
        for v in self.vehicles.values():
            v.driver.heartbeat()
            if v.link_state == "ok" and now - v.timesync_at >= TIMESYNC_PERIOD_S:
                v.timesync_at = now
                ts1 = int(now * 1e9)
                v.timesync_sent = {k: t for k, t in v.timesync_sent.items() if now - t < TIMESYNC_STALE_S}
                v.timesync_sent[ts1] = now
                v.driver.timesync(ts1)
        if self.scan["running"] and self.link is not None:
            self._broadcast_wake()

    def _broadcast_wake(self) -> None:
        if self.link is None or not self.link.ok:
            return
        from .drivers.base import MavCommands

        class _Broadcast(MavCommands):
            def __init__(self, link: UdpLink, addr: tuple[str, int]) -> None:
                super().__init__()
                self._link, self._addr = link, addr

            def _write(self, data: bytes) -> None:
                self._link.sendto(data, self._addr)

        for address in self._scan_broadcast:
            _Broadcast(self.link, (address, self.settings.bridge_port)).heartbeat()

    def _housekeeping(self, now: float) -> None:
        wall = self.wall()
        if self.link is not None and not self.link.ok and now >= self._next["listen_retry"]:
            self._next["listen_retry"] = now + 5.0
            self._open_listen()
        for s in self.sessions.expire():
            self._expire_session(s, now)
        conditions: list[Condition] = []
        if self.link is not None and not self.link.ok:
            conditions.append(Condition("port_in_use", "critical", None, {"port": self.link.port}))
        for v in list(self.vehicles.values()):
            self._house_vehicle(v, now, wall, conditions)
        for alert in self.alerts.sync(conditions):
            log.info("alert %s %s", alert["level"], alert["id"])

    def _house_vehicle(self, v: Vehicle, now: float, wall: float, conditions: list[Condition]) -> None:
        age = self._link_age(v, now)
        if age is not None:
            if age > LINK_OFFLINE_S:
                if v.link_state != "offline":
                    if v.link_state not in ("lost",):
                        self._set_link(v, "lost", now)
                    self._set_link(v, "offline", now)
            elif age > LINK_LOST_S:
                if v.link_state in ("ok", "delayed"):
                    self._set_link(v, "lost", now)
            elif age > LINK_DELAYED_S and v.link_state == "ok":
                self._set_link(v, "delayed", now)
        labels = {"vehicle": v.label}
        if v.link_state == "delayed":
            conditions.append(Condition("link_delayed", "warn", v.id, {**labels, "age": f"{age:.1f}"}))
        elif v.link_state == "lost":
            level = "critical" if v.armed else "warn"
            conditions.append(Condition("link_lost", level, v.id, {**labels, "age": f"{age or 0:.0f}"}))

        # Streams, parameters, mode confirmation.
        if v.identity_at is not None and v.link_state == "ok" and now >= v.stream_check_at:
            v.stream_check_at = now + STREAM_RECHECK_S
            missing = [
                m
                for m in KEY_MESSAGES
                if now - v.state["messages"].get(m, {}).get("t", -1e9) > STREAM_RECHECK_S
            ]
            if missing:
                self._request_streams(v)
        self._param_housekeeping(v, now)
        if v.mode_wait is not None:
            target, deadline, retried = v.mode_wait
            if v.mode == target:
                v.mode_wait = None
            elif now > deadline:
                v.mode_wait = None
                v.mode_alert = (target, now + MODE_ALERT_HOLD_S)
            elif not retried and now > deadline - MODE_CONFIRM_S / 2:
                v.driver.set_mode(target)
                v.mode_wait = (target, deadline, True)
        if v.mode_alert is not None:
            if now > v.mode_alert[1] or v.mode == v.mode_alert[0]:
                v.mode_alert = None
            else:
                conditions.append(
                    Condition("mode_switch_failed", "warn", v.id, {**labels, "mode": v.mode_alert[0]})
                )

        # Battery.
        level = self._battery_level(v, now)
        voltage = v.state["battery"]["voltage"]
        if level == "warn":
            conditions.append(
                Condition("battery_warn", "warn", v.id, {**labels, "voltage": f"{voltage:.2f}"})
            )
        elif level == "critical":
            conditions.append(
                Condition("battery_critical", "critical", v.id, {**labels, "voltage": f"{voltage:.2f}"})
            )
        # a shoot-down in the arena is not a safety event (05 §7.4, WP-D)
        if v.state["crashed"] and not self.arena.is_down(v.id):
            conditions.append(Condition("crashed", "critical", v.id, labels))

        # Control: airborne with nobody flying.
        controller = v.controller(self.sessions)
        airborne = v.airborne()
        if (
            self.settings.airborne_alert
            and airborne
            and controller == "none"
            and v.link_state in ("ok", "delayed")
        ):
            conditions.append(Condition("airborne_no_controller", "warn", v.id, labels))
            if not v.airborne_alerted:
                v.airborne_alerted = True
                self.events.emit("airborne_no_controller", v.id, labels=labels)
        else:
            v.airborne_alerted = False
        self._update_segment(v, controller, wall)

        # Flight time.
        if airborne:
            if v.flight_t0 is None:
                v.flight_t0 = now
        elif v.flight_t0 is not None:
            v.flight_time_s += now - v.flight_t0
            v.flight_t0 = None

        # Course abort conditions.
        self._course_housekeeping(v, now, controller, airborne)

        # Records.
        if v.record_session is not None and v.link_state in ("ok", "delayed"):
            self._sample(v, wall, controller)

    def _param_housekeeping(self, v: Vehicle, now: float) -> None:
        if v.param_named is not None and v.link_state == "ok":
            for name in v.param_named.due(v.state["params"], now):
                v.driver.request_param_named(name)
        if v.param_due is not None and now >= v.param_due and v.link_state == "ok":
            v.param_due = None
            self._fetch_params(v, now)
        fetch = v.param_fetch
        if fetch is None or fetch.done:
            return
        total = v.state["param_count"]
        if not v.state["params"] and now - fetch.started > 3.0:
            if v.param_list_sent < 3:
                v.param_list_sent += 1
                fetch.started = now
                v.driver.request_params()
            return
        for index in fetch.due_reads(v.state["params"], total, now):
            v.driver.request_param(index)

    def _fetch_params(self, v: Vehicle, now: float) -> None:
        v.param_fetch = ParamFetch(started=now, last_progress=now)
        v.param_list_sent = 1
        v.driver.request_params()

    def _battery_level(self, v: Vehicle, now: float) -> str:
        voltage = v.state["battery"]["voltage"]
        cells = v.cells()
        if voltage is None or not cells:
            v.battery_level = "unknown"
            return "unknown"
        per_cell = voltage / cells
        current = v.battery_level
        warn, crit = self.settings.cell_warn_v, self.settings.cell_crit_v
        if per_cell < crit:
            target = "critical"
        elif per_cell < warn:
            target = (
                "warn"
                if not (current == "critical" and per_cell < crit + BATTERY_HYSTERESIS_V)
                else "critical"
            )
        else:
            target = (
                "ok"
                if not (current in ("warn", "critical") and per_cell < warn + BATTERY_HYSTERESIS_V)
                else current
            )
        if current == "unknown":
            v.battery_level = target if target == "ok" else current
            if target == "ok":
                return target
        if target == current:
            v.battery_pending = None
            return current
        if v.battery_pending is None or v.battery_pending[0] != target:
            v.battery_pending = (target, now)
        if now - v.battery_pending[1] >= BATTERY_SUSTAIN_S:
            v.battery_pending = None
            escalated = _level_rank(target) > _level_rank(current)
            v.battery_level = target
            if escalated:
                kind = "battery_critical" if target == "critical" else "battery_warn"
                self.events.emit(kind, v.id, data={"voltage": round(voltage, 2)}, labels={"vehicle": v.label})
        return v.battery_level

    def _course_housekeeping(self, v: Vehicle, now: float, controller: str, airborne: bool) -> None:
        run = v.course
        if run is None:
            return
        if not run.active:
            if v.course_clear_at is not None and now >= v.course_clear_at:
                v.course = None
                v.course_clear_at = None
            return
        if v.position_ok(now):
            v.position_lost_since = None
        elif v.position_lost_since is None:
            v.position_lost_since = now
        if run.state == "running":
            reason = None
            if v.state["crashed"]:
                reason = "crashed"
            elif not v.armed:
                reason = "disarmed"
            elif v.link_state in ("lost", "offline"):
                reason = "link_lost"
            elif v.position_lost_since is not None and now - v.position_lost_since > POSITION_ABORT_S:
                reason = "position_lost"
            elif controller == "none" and airborne:
                reason = "no_controller"
            if reason:
                self._abort_course(v, reason, now)

    def _sample(self, v: Vehicle, wall: float, controller: str) -> None:
        att = v.state["attitude"] or {}
        p = v.world_enu()
        battery = v.state["battery"]
        hud = v.state["hud"] or {}
        sent, source = self._sent_sticks(v)
        self.history.sample(
            v.record_session,  # type: ignore[arg-type]
            {
                "t": wall,
                "armed": int(v.armed),
                "mode": v.mode,
                "roll": _round(att.get("roll"), 4),
                "pitch": _round(att.get("pitch"), 4),
                "yaw": _round(att.get("yaw"), 4),
                "x": _round(p[0]) if p else None,
                "y": _round(p[1]) if p else None,
                "z": _round(p[2]) if p else None,
                "alt": _round(v.altitude()),
                "climb": _round(hud.get("climb")),
                "voltage": battery["voltage"],
                "current": battery["current"],
                "remaining": battery["remaining_pct"],
                "throttle": hud.get("throttle"),
                "controller": controller,
                "s_roll": None if sent is None else round(sent.roll, 3),
                "s_pitch": None if sent is None else round(sent.pitch, 3),
                "s_yaw": None if sent is None else round(sent.yaw, 3),
                "s_throttle": None if sent is None else round(sent.throttle, 3),
                "s_source": source,
            },
        )

    def _sent_sticks(self, v: Vehicle) -> tuple[StickInput | None, str | None]:
        """The sticks being sent now and where they come from: skill, human, agent or none (04 §4)."""
        if v.stop_seq is not None:
            return None, None
        if v.skill is not None and v.skill.active:
            return v.skill.out or skills.NEUTRAL, "skill"
        if v.sticks is not None and self.clock() - v.sticks.t <= STICK_TIMEOUT_S:
            return v.sticks, v.sticks.source
        return None, None

    def _update_segment(self, v: Vehicle, controller: str, wall: float) -> None:
        who = None
        if controller == "human" and v.auth.possession is not None:
            who = v.auth.possession.source
        elif controller == "agent" and v.auth.claim is not None:
            who = v.auth.claim.name
        elif controller == "program" and v.program is not None:
            who = v.program.name  # WP-1: the timeline shows PROGRAM · <name>
        current = v.segment
        if current is not None and (current[0], current[1]) == (controller, who):
            return
        self._close_segment(v, wall)
        if controller != "none":
            v.segment = (controller, who, wall)

    def _close_segment(self, v: Vehicle, wall: float) -> None:
        if v.segment is not None and v.record_session is not None:
            self.history.segment(v.record_session, v.segment[2], wall, v.segment[0], v.segment[1])
        v.segment = None

    def _start_record(self, v: Vehicle, wall: float) -> None:
        if v.record_session is None:
            v.record_session = self.history.start_session(v.id, v.kind, v.label, v.autopilot, wall)

    def _record_event(self, event: dict[str, Any]) -> None:
        vid = event.get("vehicle")
        if not vid:
            return
        v = self.vehicles.get(vid)
        if v is not None and v.record_session is not None:
            self.history.event(v.record_session, event)

    # ====================================================================== sticks

    def _stick_tick(self, v: Vehicle, now: float) -> None:
        signs = v.calibration.signs.as_dict()
        if v.stop_seq is not None:
            if now < v.stop_seq["until"]:
                v.driver.send_sticks(StickInput(0.0, 0.0, 0.0, 0.0), signs)
            else:
                v.driver.arm(False)
                v.driver.stop_sticks()
                v.stop_seq = None
                v.stop_window_until = now + STOP_WINDOW_S
            return
        if v.link_state not in ("ok", "delayed"):
            return
        if v.skill is not None and v.skill.active:
            sticks = v.skill.tick(v, now)
            self._drain_skill(v)
            if sticks is not None:
                v.driver.send_sticks(sticks, signs)
            elif not v.skill.active:
                v.driver.stop_sticks()
            return
        if v.rc_frame is not None:
            if now - v.rc_frame["t"] > STICK_TIMEOUT_S:
                v.rc_frame = None
                v.driver.stop_sticks()
            elif now >= v.stop_window_until:
                v.driver.send_rc(v.rc_frame["channels"], v.rc_frame["axes"], signs)
            return
        if v.sticks is None:
            return
        if now - v.sticks.t > STICK_TIMEOUT_S:
            v.sticks = None
            v.driver.stop_sticks()
            return
        if now < v.stop_window_until:
            return
        v.driver.send_sticks(v.sticks, signs)

    def _health_tick(self, v: Vehicle, now: float) -> None:
        if v.skill is not None and v.skill.active:
            sticks = v.skill.out
        else:
            sticks = v.sticks
        v.health.sample(v, now, sticks, v.mode in modes.HOLD_STICK_MODES)

    def _drain_skill(self, v: Vehicle) -> None:
        skill = v.skill
        if skill is None:
            return
        while skill.events:
            kind, data = skill.events.pop(0)
            self.events.emit(kind, v.id, data=data, labels={"vehicle": v.label, "skill": skill.name})

    def _stop_skill(self, v: Vehicle, en: str, zh: str) -> None:
        if v.skill is not None and v.skill.active:
            v.skill.stop(en, zh)
            self._drain_skill(v)

    def _stop_control(self, v: Vehicle, reason: str, now: float) -> None:
        """Stop every stick source on this vehicle (skill, explicit sticks)."""
        self.programs.on_vehicle_stop(v, reason)  # WP-1: link loss, removal, reset and shutdown stop programs
        self._stop_skill(v, reason, reason)
        v.sticks = None
        v.rc_frame = None
        v.driver.stop_sticks()

    def _apply_effects(self, v: Vehicle, outcome: auth.Outcome, now: float) -> None:
        v.auth = outcome.state
        wall = self.wall()
        for kind, data in outcome.events:
            self.events.emit(
                kind,
                v.id,
                session=data.get("session_id"),
                data=data,
                labels={"vehicle": v.label, "name": data.get("name") or "-"},
            )
        for effect in outcome.effects:
            if effect == auth.STOP_SKILL:
                self._stop_skill(v, "control changed", "控制权变化")
                if v.motor_seq is not None or now < v.motor_until:
                    self._stop_motors(v)  # a motor test belongs to the controller that started it
            elif effect == auth.STOP_STICKS:
                v.sticks = None
                v.rc_frame = None
                v.driver.stop_sticks()
            elif effect == auth.POSSESS_MODE:
                self._possess_mode(v, now)
            elif effect == auth.ABORT_COURSE:
                self._abort_course(v, "stopped", now)
            elif effect == auth.EMERGENCY_STOP:
                v.sticks = None
                v.rc_frame = None
                if v.motor_seq is not None or now < v.motor_until:
                    self._stop_motors(v)
                v.stop_seq = {"until": now + STOP_THROTTLE_S}
                self.dogfights.on_emergency_stop(v.id)  # WP-B (07 §3): an e-stop ends the aircraft's part
        self.programs.on_authority(v, outcome)  # WP-1: possession pauses, stop and claim end stop programs
        self._update_segment(v, v.controller(self.sessions), wall)

    def _possess_mode(self, v: Vehicle, now: float) -> None:
        if v.autopilot is None:
            return
        target = modes.possess_mode(v.autopilot, v.has_baro(), self.settings.possess_mode)
        if target is None or v.mode == target:
            return
        # A stick mode is kept, except that on the ground a manual-throttle mode (STABILIZE / STABILIZED /
        # MANUAL, as a keyboard script leaves it) becomes altitude hold when the setting asks for it: with a
        # manual throttle every stick wobble is a climb or a sink, which reads as "unsteady after take-off".
        if modes.accepts_sticks(v.autopilot, v.mode) and (
            v.mode in modes.HOLD_STICK_MODES or v.airborne() or target not in modes.HOLD_STICK_MODES
        ):
            return
        v.driver.set_mode(target)
        v.mode_wait = (target, now + MODE_CONFIRM_S, False)

    # ====================================================================== lookups

    def _vehicle(self, vid: str) -> Vehicle:
        v = self.vehicles.get(vid)
        if v is None:
            raise err("vehicle_not_found", vehicle=vid)
        return v

    def _claim_alive(self, v: Vehicle) -> bool:
        return v.auth.claim is not None and self.sessions.alive(v.auth.claim.session_id)

    def _check_control(self, v: Vehicle, actor: Actor) -> None:
        auth.check_control(v.auth, actor, self._claim_alive(v))

    def _require_link(self, v: Vehicle) -> None:
        if v.link_state not in ("ok", "delayed"):
            raise err("link_not_ready", state=v.link_state, vehicle=v.id)

    # ====================================================================== vehicles

    def _attach(self, v: Vehicle) -> None:
        v.clock = self.clock
        v.driver.clock = self.clock

    def _new_id(self, host: str) -> str:
        return "real-" + host.replace(".", "-")

    def _add_real(self, host: str, port: int, source: str, now: float, version: str | None = None) -> Vehicle:
        vid = self._new_id(host)
        label = self._labels.get(host) or f"Quad {host.rsplit('.', 1)[-1].zfill(2)}"
        assert self.link is not None
        driver = MavlinkDriver(self.link, host, port)
        v = Vehicle(vid, "real", label, driver, host=host, port=port)
        self._attach(v)
        v.created = now
        v.bridge = {"version": version, "source": source}
        stored = self.history.calibration(host)
        if stored is not None:
            signs, hover = stored
            v.calibration = Calibration(
                Signs(**{k: int(signs.get(k, 1)) for k in ("roll", "pitch", "yaw")}), hover
            )
        self.vehicles[vid] = v
        self.by_host[host] = vid
        self.events.emit(
            "vehicle_discovered",
            vid,
            data={"host": host, "via": source, "label": label},
            labels={"vehicle": label},
        )
        return v

    def connect(self, host: str, port: int | None = None) -> dict[str, Any]:
        try:
            address = ipaddress.IPv4Address(str(host).strip())
        except ValueError as exc:
            raise err("invalid_address", host=str(host)) from exc
        if (
            address.is_multicast
            or address.is_unspecified
            or (address.is_loopback and not self.allow_loopback)
        ):
            raise err("invalid_address", host=str(host))
        port = int(port or self.settings.bridge_port)
        if not 1 <= port <= 65535:
            raise err("invalid_request", reason="port must be 1-65535")

        def run() -> dict[str, Any]:
            if self.link is None or not self.link.ok:
                raise err(
                    "listen_unavailable", port=self.settings.listen_port, reason=self._listen_view()["error"]
                )
            ip = str(address)
            vid = self.by_host.get(ip)
            if vid is None:
                v = self._add_real(ip, port, "manual", self.clock())
            else:
                v = self.vehicles[vid]
                v.port = port
                v.driver.port = port
            v.driver.heartbeat()
            return {"id": v.id}

        return self.call(run)

    def add_sims(
        self,
        count: int = 1,
        flavour: str = ARDUPILOT,
        position: bool = True,
        hover_assist: bool | None = None,
        airframe: dict[str, Any] | None = None,  # WP-2a {preset | params, seed?}; None: sim_airframe_default
    ) -> list[str]:
        if flavour not in (ARDUPILOT, PX4):
            raise err("invalid_request", reason="autopilot must be ardupilot or px4")
        if not 1 <= int(count) <= SIM_LIMIT:
            raise err("invalid_request", reason=f"count must be 1-{SIM_LIMIT}")

        def run() -> list[str]:
            existing = {vid for vid, v in self.vehicles.items() if v.kind == "sim"}
            if len(existing) + count > SIM_LIMIT:
                raise err("sim_limit", limit=SIM_LIMIT)
            added = []
            now = self.clock()
            frame = self._resolve_airframe(airframe) if airframe else None  # WP-2a: one body, same airframe
            for _ in range(count):
                n = next(i for i in range(1, SIM_LIMIT + 1) if f"sim-{i}" not in existing)
                vid = f"sim-{n}"
                existing.add(vid)
                autopilot = SimAutopilot(
                    flavour,
                    position=position,
                    position_hz=SIM_POSITION_HZ,
                    name=vid,
                    **self._sim_options(hover_assist),  # WP-C: hover assist and turbulence (05 §5)
                    airframe=frame or self._resolve_airframe(None),  # WP-2a: setting default, fresh seed each
                )
                v = Vehicle(vid, "sim", f"SIM-{n}", SimDriver(autopilot))
                self._attach(v)
                v.created = now
                v.spawn = SIM_SPAWNS[n - 1]
                v.frame_offset = (v.spawn[0], v.spawn[1], 0.0)
                v.safety_origin = (v.spawn[0], v.spawn[1], 0.0)
                self.vehicles[vid] = v
                added.append(vid)
                self.events.emit(
                    "sim_added",
                    vid,
                    data={
                        "autopilot": flavour,
                        "hover_assist": autopilot.hover_assist,
                        "airframe": autopilot.airframe.ref(),  # WP-2a
                    },
                    labels={"vehicle": v.label},
                )
            return added

        return self.call(run)

    def remove_sims(self, ids: list[str] | None = None) -> list[str]:
        def run() -> list[str]:
            targets = [
                vid for vid, v in self.vehicles.items() if v.kind == "sim" and (ids is None or vid in ids)
            ]
            for vid in targets:
                self._remove(self.vehicles[vid])
            return targets

        return self.call(run)

    def remove_vehicle(self, vid: str) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            self._remove(v)
            return {"id": vid, "removed": True}

        return self.call(run)

    def _remove(self, v: Vehicle) -> None:
        now = self.clock()
        wall = self.wall()
        self._procedure_removed(v)  # bench procedure (WP-B)
        self._stop_control(v, "removed", now)
        if v.kind == "real":
            v.driver.pace(now + 1.0)  # flush pending release frames
        self._abort_course(v, "removed", now)
        self._close_segment(v, wall)
        if v.record_session is not None:
            self.history.end_session(v.record_session, wall)
        if v.kind == "sim":
            self.events.emit("sim_removed", v.id, labels={"vehicle": v.label})
        self.vehicles.pop(v.id, None)
        if v.host:
            self.by_host.pop(v.host, None)

    def reset_sim(self, vid: str) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            if v.kind != "sim":
                raise err("sim_only")
            if v.armed and not v.state["crashed"]:
                raise err("armed")
            self._stop_control(v, "reset", self.clock())
            self._abort_course(v, "reset", self.clock())
            v.driver.reset()
            v.state["crashed"] = False
            v.crash_event = False
            return {"id": vid, "reset": True}

        return self.call(run)

    def set_label(self, vid: str, label: str) -> dict[str, Any]:
        label = " ".join(str(label or "").split())[:32]
        if not label:
            raise err("invalid_request", reason="label is required")

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            v.label = label
            if v.kind == "real" and v.host:
                self._labels[v.host] = label
                self.history.set_label(v.host, label)
            if v.record_session is not None:
                self.history.update_session(v.record_session, label=label)
            return {"id": vid, "label": label}

        return self.call(run)

    def set_calibration(self, vid: str, signs: dict[str, Any] | None, hover: float | None) -> dict[str, Any]:
        if signs is not None:
            if not isinstance(signs, dict) or any(k not in ("roll", "pitch", "yaw") for k in signs):
                raise err("invalid_request", reason="signs takes roll, pitch and yaw")
            if any(v not in (1, -1) for v in signs.values()):
                raise err("invalid_request", reason="signs must be 1 or -1")
        if hover is not None and not (isinstance(hover, (int, float)) and 0.2 <= float(hover) <= 0.7):
            raise err("invalid_request", reason="hover must be 0.20-0.70")

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            cal = v.calibration
            if signs is not None:
                new_signs = Signs(**{**cal.signs.as_dict(), **{k: int(x) for k, x in signs.items()}})
                if new_signs != cal.signs:
                    v.confirmed.signs = False  # a changed sign invalidates the verification
                cal.signs = new_signs
            if hover is not None:
                cal.hover = float(hover)
            if v.kind == "real" and v.host:
                self.history.set_calibration(v.host, cal.signs.as_dict(), cal.hover)
            return {"id": vid, "calibration": cal.view(), "confirmed": v.confirmed.view()}

        return self.call(run)

    def confirm(self, vid: str, item: str) -> dict[str, Any]:
        if item not in ("signs", "props"):
            raise err("invalid_request", reason="item must be signs or props")

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            setattr(v.confirmed, item, True)
            return {"id": vid, "confirmed": v.confirmed.view()}

        return self.call(run)

    # ====================================================================== authority

    def possess(self, vid: str, input: str, pad: int | None = None) -> dict[str, Any]:
        if input not in INPUT_KINDS:
            raise err("invalid_request", reason="input must be keyboard, screen or gamepad")
        if input == "gamepad" and (pad is None or not 1 <= int(pad) <= 4):
            raise err("invalid_request", reason="gamepad needs pad 1-4")

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            now = self.clock()
            source = f"gamepad:{pad}" if input == "gamepad" else input
            for other in self.vehicles.values():
                if (
                    other is not v
                    and other.auth.possession is not None
                    and other.auth.possession.source == source
                ):
                    self._apply_effects(
                        other, auth.release(other.auth, self._claim_alive(other), self.wall()), now
                    )
            outcome = auth.possess(v.auth, input, int(pad) if pad is not None else None, self.wall())
            self._apply_effects(v, outcome, now)
            return {"id": vid, "possession": v.auth.possession.view() if v.auth.possession else None}

        return self.call(run)

    def release(self, vid: str) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            outcome = auth.release(v.auth, self._claim_alive(v), self.wall())
            self._apply_effects(v, outcome, self.clock())
            return {"id": vid, "controller": v.controller(self.sessions), "event": outcome.events[0][0]}

        return self.call(run)

    def claim(self, vid: str, actor: Actor) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            existing = v.auth.claim
            alive = existing is not None and self.sessions.alive(existing.session_id)
            outcome = auth.claim(v.auth, actor, alive, self.wall())
            self._apply_effects(v, outcome, self.clock())
            return {
                "id": vid,
                "claim": v.auth.claim.view(v.auth.possession is not None) if v.auth.claim else None,
            }

        return self.call(run)

    def unclaim(self, vid: str, actor: Actor) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            self._apply_effects(v, auth.unclaim(v.auth, actor), self.clock())
            return {"id": vid, "claim": None}

        return self.call(run)

    def _expire_session(self, s: Session, now: float) -> None:
        for v in self.vehicles.values():
            outcome = auth.session_expired(v.auth, s.session_id)
            if outcome.events:
                self._apply_effects(v, outcome, now)
        self.events.emit(
            "agent_session_expired",
            session=s.session_id,
            data={"session_id": s.session_id, "name": s.name},
            labels={"name": s.name},
        )

    def open_session(self, name: str) -> dict[str, Any]:
        session, token = self.sessions.open(name, self.wall())
        self.events.emit(
            "agent_session_started",
            session=session.session_id,
            data={"session_id": session.session_id, "name": session.name},
            labels={"name": session.name},
        )
        return {
            "session_id": session.session_id,
            "name": session.name,
            "token": token,
            "lease_s": session.lease_s,
        }

    def renew_session(self, session: Session) -> dict[str, Any]:
        lease = self.sessions.renew(session)
        return {"session_id": session.session_id, "lease_s": lease}

    def close_session(self, session: Session) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            released = []
            for v in self.vehicles.values():
                if v.auth.claim is not None and v.auth.claim.session_id == session.session_id:
                    self._apply_effects(
                        v, auth.unclaim(v.auth, Actor.session(session.session_id, session.name)), self.clock()
                    )
                    released.append(v.id)
            self.sessions.close(session)
            self.events.emit(
                "agent_session_closed",
                session=session.session_id,
                data={"session_id": session.session_id, "name": session.name},
                labels={"name": session.name},
            )
            return {"session_id": session.session_id, "closed": True, "released": released}

        return self.call(run)

    # ====================================================================== control

    def sticks(
        self, vid: str, actor: Actor, values: dict[str, Any], conn: str | None = None
    ) -> dict[str, Any]:
        problem = StickInput.problem(
            values.get("roll"), values.get("pitch"), values.get("yaw"), values.get("throttle")
        )
        if problem:
            raise err("invalid_request", reason=f"{problem} is missing or out of range")

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            self._check_control(v, actor)
            self._require_link(v)
            now = self.clock()
            new = StickInput(
                float(values["roll"]),
                float(values["pitch"]),
                float(values["yaw"]),
                float(values["throttle"]),
                t=now,
                source="human" if actor.kind == "operator" else "agent",
                conn=conn,
            )
            self.programs.on_session_sticks(v, actor)  # WP-1: a session's sticks supersede its program
            if v.skill is not None and v.skill.active:
                if actor.kind == "session":
                    self._stop_skill(v, "superseded by sticks", "被杆量指令取代")
                else:
                    baseline = v.skill.params.get("_baseline")
                    if baseline is None:
                        v.skill.params["_baseline"] = new
                        return {"accepted": False, "reason": "skill_running"}
                    if new.deflection_from(baseline) <= PILOT_OVERRIDE:
                        return {"accepted": False, "reason": "skill_running"}
                    self._stop_skill(v, "operator override", "操作员接管")
            v.sticks = new
            v.rc_frame = None
            if new.source == "human":
                v.health.note_stick_input(now)
            if now < v.stop_window_until:
                return {"accepted": False, "reason": "stop_window"}
            return {"accepted": True}

        return self.call(run)

    def ws_closed(self, conn: str) -> None:
        """A WebSocket closed: stop every stick stream it was feeding (03 §10.2, H2)."""

        def run() -> None:
            for v in self.vehicles.values():
                if v.sticks is not None and v.sticks.conn == conn:
                    v.sticks = None
                    v.driver.stop_sticks()

        try:
            self.call(run)
        except RelayError:
            pass

    def arm(
        self,
        vid: str,
        actor: Actor,
        on: bool,
        confirm: str | None = None,
        wait: bool = True,
        force: bool = False,
        authorised: bool = False,
    ) -> dict[str, Any]:
        """``authorised``: the caller already checked the actor (a program deployment that arms, 09 §2).

        A stick mode arms only with pilot input present (PX4 "No manual control input", ArduPilot "Throttle
        below failsafe", 10 §1): with no live sticks Relay first sends centred zero-throttle sticks, and
        gives the autopilot ``ARM_PRIME_S`` to see them before the arm command."""
        t0 = self.clock()
        primed = False

        def start() -> None:
            nonlocal primed
            v = self._vehicle(vid)
            if (on or actor.kind == "session") and not authorised:
                self._check_control(v, actor)
            self._require_link(v)
            if on and force:
                # Skips the autopilot's pre-arm checks and Relay's preflight list; the word is the guard.
                device.require_confirm("force_arm", v.kind, confirm)
                if v.state["crashed"] and v.kind == "sim":
                    raise err("crashed")
                v.stop_window_until = 0.0
            elif on:
                self._arm_checks(v, confirm)
                v.stop_window_until = 0.0
                primed = self._prime_sticks(v)
                if primed and self.threaded:
                    return  # the arm command follows once the autopilot has seen the sticks
            v.driver.arm(on, force=on and force)

        self.call(start)
        if primed and self.threaded:
            time.sleep(ARM_PRIME_S)

            def send() -> None:
                # the fleet lock was free during the wait: an e-stop, a control change or a dropped stream
                # cancels the arm rather than arming behind it
                v = self._vehicle(vid)
                if not authorised:
                    self._check_control(v, actor)
                self._require_link(v)
                if v.stop_seq is not None or v.sticks is None or v.sticks.source != "relay":
                    raise err("arm_rejected", reason="cancelled while the sticks were being primed")
                v.sticks = replace(v.sticks, t=self.clock())  # keep them alive across the arming
                v.driver.arm(True)

            self.call(send)
        if not wait or not self.threaded:
            return {"id": vid, "armed": None, "pending": True}
        deadline = t0 + 3.0
        while self.clock() < deadline:
            armed, ack, texts = self.call(lambda: self._arm_probe(vid, t0))
            if armed == on:
                return {"id": vid, "armed": on}
            if ack is not None and ack["result"] not in (M.MAV_RESULT_ACCEPTED, M.MAV_RESULT_IN_PROGRESS):
                reason = texts[-1] if texts else ack["result_name"]
                raise err("arm_rejected", reason=reason, ack=ack["result_name"], statustext=texts)
            time.sleep(0.05)
        raise err("arm_rejected", reason="no confirmation from the autopilot")

    def _prime_sticks(self, v: Vehicle) -> bool:
        """Centred zero-throttle sticks for arming when nothing else feeds the aircraft; True when sent."""
        if v.armed or not modes.accepts_sticks(v.autopilot, v.mode):
            return False  # armed already: zero throttle would be a descent, the repeated arm is harmless
        if (v.skill is not None and v.skill.active) or v.rc_frame is not None:
            return False
        now = self.clock()
        if v.sticks is not None and now - v.sticks.t <= STICK_TIMEOUT_S:
            # live sticks: send them once more now, so they are ahead of the arm command on the link
            v.driver.send_sticks(v.sticks, v.calibration.signs.as_dict())
            return False
        v.sticks = StickInput(0.0, 0.0, 0.0, 0.0, t=now, source="relay")
        v.driver.send_sticks(v.sticks, v.calibration.signs.as_dict())
        return True

    def _arm_probe(self, vid: str, t0: float) -> tuple[bool, dict | None, list[str]]:
        v = self._vehicle(vid)
        wall0 = self.wall() - (self.clock() - t0)
        texts = [s["text"] for s in v.state["statustext"] if s["t"] >= wall0 - 0.05]
        return v.armed, v.ack_after(M.MAV_CMD_COMPONENT_ARM_DISARM, t0), texts

    def _arm_checks(self, v: Vehicle, confirm: str | None) -> None:
        if v.kind == "real" and confirm != "ARM":
            raise err("confirm_required", expected="ARM")
        if v.state["crashed"] and v.kind == "sim":
            raise err("crashed")
        if v.kind == "real":
            items = self._preflight_items(v)
            summary = preflight.summary(items)
            if not summary["ok"]:
                raise err(
                    "preflight_blocked", items=", ".join(summary["blocking"]), blocking=summary["blocking"]
                )

    def set_mode(
        self, vid: str, actor: Actor, name: str, wait: bool = True, authorised: bool = False
    ) -> dict[str, Any]:
        t0 = self.clock()
        name = str(name or "").upper()

        def start() -> None:
            v = self._vehicle(vid)
            if not authorised:
                self._check_control(v, actor)
            self._require_link(v)
            if name not in modes.available_modes(v.autopilot):
                raise err("unknown_mode", mode=name, modes=modes.available_modes(v.autopilot))
            flight_level.check_mode(self.settings, actor, v.autopilot, name)  # WP-1 (06 §5)
            self._stop_skill(v, "mode changed by controller", "控制者切换了模式")
            v.driver.set_mode(name)

        self.call(start)
        if not wait or not self.threaded:
            return {"id": vid, "mode": None, "pending": True}
        retried = False
        while self.clock() < t0 + MODE_CONFIRM_S + 0.5:
            mode, ack = self.call(
                lambda: (self._vehicle(vid).mode, self._vehicle(vid).ack_after(M.MAV_CMD_DO_SET_MODE, t0))
            )
            if mode == name:
                return {"id": vid, "mode": name}
            if ack is not None and ack["result"] not in (M.MAV_RESULT_ACCEPTED, M.MAV_RESULT_IN_PROGRESS):
                raise err("mode_rejected", mode=name, ack=ack["result_name"])
            if not retried and self.clock() > t0 + MODE_CONFIRM_S / 2:
                retried = True
                self.call(lambda: self._vehicle(vid).driver.set_mode(name))
            time.sleep(0.05)
        raise err("mode_rejected", mode=name, ack=None)

    def run_skill(self, vid: str, actor: Actor, name: str, params: dict[str, Any]) -> dict[str, Any]:
        if name not in skills.SKILL_NAMES:
            raise err("unknown_skill", skill=name, skills=list(skills.SKILL_NAMES))
        params = dict(params or {})

        safety = flight_level.check_skill(self.settings, actor, name)  # WP-1 (06 §5): 403 sticks_only

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            self._check_control(v, actor)
            self._require_link(v)
            prepared, target_local = self._prepare_skill(v, name, params)
            self._cancel_plan(v, "superseded by a skill", "被单独的技能取代")
            skill = self._start_skill(v, name, prepared, target_local, safety=safety)
            return {"id": vid, "skill": skill.view()}

        return self.call(run)

    def _prepare_skill(
        self, v: Vehicle, name: str, params: dict[str, Any]
    ) -> tuple[dict[str, Any], tuple[float, float, float] | None]:
        """Check a skill against the vehicle and return its executor parameters (vehicle thread)."""
        params = dict(params)
        sv = self.settings.safety_volume
        required = skills.requirement(name)
        if not v.capabilities.has(required):
            raise err("capability_missing", required=required)
        position = v.capabilities.position
        target_local = None
        if name == "takeoff":
            alt = params.get("alt", 1.0)
            if not isinstance(alt, (int, float)) or isinstance(alt, bool) or not 0.2 <= float(alt) <= sv.z:
                raise err("outside_safety_volume", reason=f"alt must be 0.2-{sv.z:g} m", limit_z=sv.z)
            params["alt"] = float(alt)
            # Without a position estimate GUIDED / OFFBOARD takeoff is refused: climb on the stick.
            params["_stick"] = not position
            if not v.armed:
                self._arm_checks(v, params.get("confirm"))
        elif name == "goto":
            target = self._goto_target(v, params)
            target_local = (
                target[0] - v.frame_offset[0],
                target[1] - v.frame_offset[1],
                target[2] - v.frame_offset[2],
            )
            local = v.local_enu()
            params["distance"] = math.dist(local, target_local) if local else 3.0
            if not v.armed:
                raise err("not_armed")
        elif name == "move":
            forward, right, up = (_number_param(params, k) for k in ("forward", "right", "up"))
            params.update(forward=forward, right=right, up=up)
            if not v.armed or not v.airborne():
                raise err("not_airborne")
            altitude = v.altitude()
            if altitude is not None and up and not 0.2 <= altitude + up - (v.ground_alt or 0.0) <= sv.z:
                raise err("outside_safety_volume", reason=f"altitude must stay 0.2-{sv.z:g} m", limit_z=sv.z)
            if position:
                local = v.local_enu()
                heading = v.heading() or 0.0
                assert local is not None
                east = forward * math.sin(heading) + right * math.cos(heading)
                north = forward * math.cos(heading) - right * math.sin(heading)
                world = v.world_enu() or local
                self._goto_target(v, {"x": world[0] + east, "y": world[1] + north, "z": world[2] + up})
                target_local = (local[0] + east, local[1] + north, local[2] + up)
                params["distance"] = math.dist(local, target_local)
                params["yaw"] = None
            else:
                if math.hypot(forward, right) > skills.MOVE_OPEN_LOOP_MAX_M:
                    raise err(
                        "outside_safety_volume",
                        reason="without a position estimate a move is at most "
                        f"{skills.MOVE_OPEN_LOOP_MAX_M:g} m",
                    )
                tilt = skills.max_tilt_deg(v.autopilot, v.param)
                params["_profile"] = list(skills.open_loop_profile(forward, right, tilt))
                params["distance"] = math.hypot(forward, right)
        elif name == "yaw":
            deg = params.get("deg")
            if not isinstance(deg, (int, float)) or isinstance(deg, bool) or not -360 <= float(deg) <= 360:
                raise err("invalid_request", reason="deg must be -360 to 360")
            params["deg"] = float(deg)
            if not v.armed or not v.airborne():
                raise err("not_airborne")
        elif name == "hold":
            seconds = params.get("seconds", 0.0) or 0.0
            if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not 0 <= seconds <= 120:
                raise err("invalid_request", reason="seconds must be 0-120")
            params["seconds"] = float(seconds)
        elif name == "sticks":
            for axis in ("roll", "pitch", "yaw"):
                params[axis] = _skill_number(params, axis, -1.0, 1.0, 0.0)
            params["throttle"] = _skill_number(params, "throttle", 0.0, 1.0)
            params["seconds"] = _skill_number(params, "seconds", 0.05, 10.0)
            if not v.armed:
                raise err("not_armed")
        elif name == "attitude":
            params["roll_deg"] = _skill_number(params, "roll_deg", -30.0, 30.0, 0.0)
            params["pitch_deg"] = _skill_number(params, "pitch_deg", -30.0, 30.0, 0.0)
            params["yaw_rate_dps"] = _skill_number(params, "yaw_rate_dps", -90.0, 90.0, 0.0)
            params["seconds"] = _skill_number(params, "seconds", 0.05, 10.0)
            if params.get("thrust") is not None:
                params["thrust"] = _skill_number(params, "thrust", 0.0, 1.0)
            params["_hover"] = v.calibration.hover
            if not v.armed or not v.airborne():
                raise err("not_airborne")
        elif name == "velocity":
            for axis in ("vx", "vy", "vz"):
                params[axis] = _skill_number(params, axis, -2.0, 2.0, 0.0)
            params["seconds"] = _skill_number(params, "seconds", 0.05, 10.0)
            if params.get("yaw_rate_dps") is not None:
                params["yaw_rate_dps"] = _skill_number(params, "yaw_rate_dps", -90.0, 90.0)
            if not v.armed or not v.airborne():
                raise err("not_airborne")
            local = v.local_enu()
            if local is None:
                raise err("no_position_estimate")
            params["_box"] = self._local_box(v, local)
        elif name == "land":
            params["_stick"] = v.autopilot == modes.PX4 and not position
            params["_ground"] = v.ground_alt
        elif name == "rtl" and not v.armed:
            raise err("not_armed")
        elif name == "replay" and not v.armed:
            self._arm_checks(v, params.get("confirm"))
        return params, target_local

    def _start_skill(
        self,
        v: Vehicle,
        name: str,
        params: dict[str, Any],
        target_local: tuple[float, float, float] | None,
        safety: bool = False,
        tuning: dict[str, float] | None = None,
    ) -> skills.Skill:
        now = self.clock()
        self.programs.on_superseded(v)  # WP-1: a skill, plan or re-fly replaces the program
        self._stop_skill(v, "superseded", "被新技能取代")
        v.sticks = None
        v.rc_frame = None
        v.stop_window_until = 0.0
        skill = skills.make(name, {k: x for k, x in params.items() if k != "confirm"}, now, target_local)
        self._skill_seq += 1
        skill.id = self._skill_seq
        if tuning is not None and not v.has_capability("position"):
            # the plan's dict itself, so tune_plan reaches the flying step; with a position estimate the
            # autopilot flies take-off and holds, and a moved stick centre would only make POSCTL drift
            skill.tuning = tuning
        v.skill = skill
        data = {"skill": name, "skill_id": skill.id, "params": _public(skill.params)}
        if safety:
            data["safety"] = True  # WP-1 (06 §5): land kept as a safety action at the sticks level
        self.events.emit("skill_started", v.id, data=data, labels={"vehicle": v.label, "skill": name})
        return skill

    # ====================================================================== plans

    def run_plan(
        self,
        vid: str,
        actor: Actor,
        steps: Any,
        name: str | None = None,
        confirm: str | None = None,
        max_steps: int = plans.MAX_STEPS,
        tuning: Any = None,
    ) -> dict[str, Any]:
        normalised = plans.normalise_steps(steps, max_steps)
        tuned = plans.normalise_tuning(tuning)
        flight_level.check_plan(self.settings, actor, normalised)  # WP-1 (06 §5)

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            self._check_control(v, actor)
            self._require_link(v)
            first, params = plans.skill_for(normalised[0])
            if first == "takeoff":
                params["confirm"] = confirm
            elif first != "sticks" and not v.airborne():
                raise err("not_airborne")
            for step in normalised:
                required = skills.REQUIREMENTS[plans.skill_for(step)[0]]
                if not v.capabilities.has(required):
                    raise err("capability_missing", required=required, action=step["action"])
            prepared, target_local = self._prepare_skill(v, first, params)
            self._cancel_plan(v, "superseded by a new plan", "被新的动作序列取代")
            self._plan_seq += 1
            plan = plans.Plan(self._plan_seq, normalised, actor, self.clock(), name=name, tuning=tuned)
            v.plan = plan
            self.events.emit(
                "plan_started",
                v.id,
                data={"plan_id": plan.id, "steps": normalised, "name": name},
                labels={"vehicle": v.label, "count": str(len(normalised))},
            )
            skill = self._start_skill(v, first, prepared, target_local, tuning=plan.tuning)
            plan.skill_id = skill.id
            return {"id": vid, "plan": plan.view()}

        return self.call(run)

    def tune_plan(self, vid: str, actor: Actor, patch: Any) -> dict[str, Any]:
        """Change the throttle tuning of the aircraft's plan while it flies (and while its last step holds
        or the hold a cancel left behind flies)."""
        update = plans.normalise_tuning(patch)

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            self._check_control(v, actor)
            plan = v.plan
            if plan is None:
                raise err("no_plan")
            merged = {**plan.tuning, **update}
            plans.normalise_tuning(merged)  # climb stays above hover after the change
            plan.tuning.update(update)
            self.events.emit(
                "plan_tuned",
                v.id,
                data={"plan_id": plan.id, "tuning": dict(plan.tuning)},
                labels={"vehicle": v.label},
            )
            return {"id": vid, "plan": plan.view()}

        return self.call(run)

    def cancel_plan(self, vid: str, actor: Actor) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            self._check_control(v, actor)
            plan = v.plan
            if plan is None or not plan.running:
                raise err("no_plan")
            self._cancel_plan(v, "cancelled", "已取消")
            # Keep the aircraft where it is: hold in the stick mode with neutral sticks. At the sticks flight
            # level (WP-1, 06 §5) Relay does not fly a hold for the agent; the autopilot's failsafe applies.
            held = not flight_level.limited(self.settings, actor)
            if held and v.armed and v.airborne() and v.capabilities.althold:
                prepared, _ = self._prepare_skill(v, "hold", {})
                self._start_skill(v, "hold", prepared, None, tuning=plan.tuning)
            return {"id": vid, "plan": plan.view()}

        return self.call(run)

    def _cancel_plan(self, v: Vehicle, en: str, zh: str) -> None:
        plan = v.plan
        if plan is None or not plan.running:
            return
        plan.state = "cancelled"
        plan.detail, plan.detail_zh = en, zh
        plan.finished = self.clock()
        self.events.emit(
            "plan_cancelled",
            v.id,
            data={"plan_id": plan.id, "index": plan.index, "detail": en, "detail_en": en, "detail_zh": zh},
            labels={"vehicle": v.label, "detail": en},
        )

    def _plan_tick(self, v: Vehicle) -> None:
        plan = v.plan
        if plan is None or not plan.running:
            return
        skill = v.skill
        if skill is None or skill.id != plan.skill_id:
            self._fail_plan(v, "superseded", "被其他指令取代")
            return
        if skill.state == "failed":
            self._fail_plan(v, skill.detail or "step failed", skill.detail_zh or skill.detail or "步骤失败")
            return
        if skill.state != "done":
            return
        plan.results.append(skill.detail)
        self.events.emit(
            "plan_step",
            v.id,
            data={"plan_id": plan.id, "index": plan.index, "action": plan.steps[plan.index]["action"]},
            labels={"vehicle": v.label, "step": str(plan.index + 1), "count": str(len(plan.steps))},
        )
        plan.index += 1
        if plan.index >= len(plan.steps):
            plan.state = "done"
            plan.finished = self.clock()
            self.events.emit(
                "plan_finished",
                v.id,
                data={"plan_id": plan.id, "results": plan.results},
                labels={"vehicle": v.label},
            )
            return
        name, params = plans.skill_for(plan.steps[plan.index])
        try:
            prepared, target_local = self._prepare_skill(v, name, params)
        except RelayError as exc:
            self._fail_plan(v, exc.message_en, exc.message_zh)
            return
        plan.skill_id = self._start_skill(v, name, prepared, target_local, tuning=plan.tuning).id

    def _fail_plan(self, v: Vehicle, en: str, zh: str) -> None:
        plan = v.plan
        assert plan is not None
        plan.state = "failed"
        plan.detail, plan.detail_zh = en, zh
        plan.finished = self.clock()
        self.events.emit(
            "plan_failed",
            v.id,
            data={"plan_id": plan.id, "index": plan.index, "detail": en, "detail_en": en, "detail_zh": zh},
            labels={"vehicle": v.label, "detail": en, "step": str(plan.index + 1)},
        )

    def _local_box(self, v: Vehicle, local: tuple[float, float, float]) -> tuple[float, ...]:
        """The safety volume as (xmin, xmax, ymin, ymax, zmin, zmax) in the vehicle's local ENU frame."""
        origin = v.safety_origin or v.world_enu() or (0.0, 0.0, 0.0)
        sv = self.settings.safety_volume
        ox, oy, oz = (origin[i] - v.frame_offset[i] for i in range(3))
        box = (ox - sv.x / 2, ox + sv.x / 2, oy - sv.y / 2, oy + sv.y / 2, oz, oz + sv.z)
        if not skills._inside(box, local):
            raise err(
                "outside_safety_volume",
                origin=[round(c, 3) for c in origin],
                volume={"x": sv.x, "y": sv.y, "z": sv.z},
            )
        return box

    # ====================================================================== re-fly

    def refly_options(self, sid: int) -> dict[str, Any]:
        detail = self.history.session_detail(sid)
        if detail is None:
            raise err("record_not_found", record=sid)
        return refly.options(detail)

    def refly(
        self,
        vid: str,
        actor: Actor,
        sid: int,
        mode: str = "auto",
        speed: float = 1.0,
        confirm: str | None = None,
    ) -> dict[str, Any]:
        """Fly a record again as a plan, a path or recorded sticks (04 §4)."""
        detail = self.history.session_detail(sid)
        if detail is None:
            raise err("record_not_found", record=sid)
        found = refly.options(detail)
        if mode == "auto":
            mode = found["recommended"] or ""
        if not mode or found.get(mode) is None:
            raise err("refly_unavailable", mode=mode or "auto", reason="the record holds nothing to fly")
        if flight_level.limited(self.settings, actor):  # WP-1 (06 §5): only recorded sticks in manual modes
            rows = refly.stick_rows(detail) if mode == "sticks" else None
            recorded = [rows["mode"], *(m for _, m in rows["modes"])] if rows else []
            autopilot = rows["autopilot"] if rows else None
            flight_level.check_refly(self.settings, actor, mode, autopilot, recorded)

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            self._check_control(v, actor)
            self._require_link(v)
            label = f"refly-{sid}"
            if mode == "sticks":
                rows = refly.stick_rows(detail)
                assert rows is not None
                known = modes.available_modes(v.autopilot)
                for name in (rows["mode"], *(m for _, m in rows["modes"])):
                    if name not in known:
                        raise err("unknown_mode", mode=name, modes=known)
                params = {
                    "session_id": sid,
                    "speed": speed,
                    "mode": rows["mode"],
                    "duration_s": rows["duration_s"],
                    "confirm": confirm,
                    "_samples": rows["rows"],
                    "_modes": rows["modes"],
                }
                prepared, _ = self._prepare_skill(v, "replay", params)
                self._cancel_plan(v, "superseded by a re-fly", "被再飞取代")
                skill = self._start_skill(v, "replay", prepared, None)
                v.refly = {"session_id": sid, "mode": mode, "kind": "skill", "id": skill.id}
                result = {"id": vid, "skill": skill.view()}
            else:
                steps = self._refly_steps(v, detail, mode)
                result = self.run_plan(vid, actor, steps, label, confirm, refly.MAX_STEPS)
                v.refly = {"session_id": sid, "mode": mode, "kind": "plan", "id": result["plan"]["id"]}
            self.events.emit(
                "refly_started",
                v.id,
                data={"session_id": sid, "mode": mode},
                labels={"vehicle": v.label, "session_id": str(sid), "mode": mode},
            )
            return result

        return self.call(run)

    def _refly_steps(self, v: Vehicle, detail: dict[str, Any], mode: str) -> list[dict[str, Any]]:
        here = v.world_enu()
        if mode == "plan":
            cols = detail["samples"]
            armed = [i for i, a in enumerate(cols["armed"]) if a and cols["x"][i] is not None]
            offset = (0.0, 0.0, 0.0)
            if armed and here is not None:
                i = armed[0]
                offset = (here[0] - cols["x"][i], here[1] - cols["y"][i], here[2] - cols["z"][i])
            steps = refly.plan_steps(detail, offset)
            assert steps is not None
            return steps
        if here is None or not v.capabilities.position:
            raise err("capability_missing", required="position")
        path = refly.path_waypoints(detail)
        assert path is not None
        sv = self.settings.safety_volume
        origin = v.safety_origin or here
        steps = refly.path_steps(
            path, here, v.airborne(), (origin[2] + 0.3, origin[2] + max(0.4, sv.z - 0.05))
        )
        for step in steps:
            if step["action"] == "goto":
                self._goto_target(v, step)
        return steps

    def _refly_tick(self, v: Vehicle) -> None:
        info = v.refly
        if info is None:
            return
        if info["kind"] == "plan":
            item = v.plan
            state = item.state if item is not None and item.id == info["id"] else "superseded"
            detail = item.detail if item is not None else None
            detail_zh = item.detail_zh if item is not None else None
        else:
            item = v.skill
            state = item.state if item is not None and item.id == info["id"] else "superseded"
            detail = item.detail if item is not None else None
            detail_zh = item.detail_zh if item is not None else None
        if state in ("running",):
            return
        v.refly = None
        data = {"session_id": info["session_id"], "mode": info["mode"]}
        labels = {"vehicle": v.label, "session_id": str(info["session_id"]), "mode": info["mode"]}
        if state == "done":
            self.events.emit("refly_finished", v.id, data=data, labels=labels)
            return
        en = detail or ("superseded" if state == "superseded" else state)
        zh = detail_zh or detail or ("被其他指令取代" if state == "superseded" else state)
        self.events.emit(
            "refly_failed",
            v.id,
            data={**data, "detail": en, "detail_en": en, "detail_zh": zh},
            labels={**labels, "detail": en},
        )

    def _goto_target(self, v: Vehicle, params: dict[str, Any]) -> tuple[float, float, float]:
        try:
            x, y, z = (float(params[k]) for k in ("x", "y", "z"))
        except (KeyError, TypeError, ValueError) as exc:
            raise err("invalid_request", reason="goto needs numeric x, y and z") from exc
        if params.get("yaw") is not None and not isinstance(params["yaw"], (int, float)):
            raise err("invalid_request", reason="yaw must be a number of degrees")
        origin = v.safety_origin
        if origin is None:
            origin = v.world_enu() or (0.0, 0.0, 0.0)
        sv = self.settings.safety_volume
        dx, dy, dz = x - origin[0], y - origin[1], z - origin[2]
        if abs(dx) > sv.x / 2 or abs(dy) > sv.y / 2 or not 0.0 <= dz <= sv.z:
            raise err(
                "outside_safety_volume",
                origin=[round(c, 3) for c in origin],
                volume={"x": sv.x, "y": sv.y, "z": sv.z},
            )
        return (x, y, z)

    def stop(self, vid: str, actor: Actor) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            outcome = auth.stop(v.auth, actor, self._claim_alive(v))
            self._apply_effects(v, outcome, self.clock())
            return {"id": vid, "stopped": True}

        return self.call(run)

    def stop_all(self) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            now = self.clock()
            self.events.emit("stop_all", data={"count": len(self.vehicles)})
            for v in self.vehicles.values():
                outcome = auth.stop(v.auth, Actor.operator(), self._claim_alive(v))
                outcome.events = [("stop", {**outcome.events[0][1], "all": True})]
                self._apply_effects(v, outcome, now)
            return {"stopped": list(self.vehicles)}

        return self.call(run)

    def motor_test(
        self, vid: str, motor: int, throttle_pct: float, seconds: float, confirm: str | None
    ) -> dict[str, Any]:
        """Legacy operator route ``/motor-test``: the same test without needing possession (04 §2)."""
        return self.motors_test(vid, Actor.operator(), motor, throttle_pct, seconds, confirm, strict=False)

    # ====================================================================== parameters

    def params_refresh(self, vid: str, actor: Actor) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            if actor.kind == "session":
                self._check_control(v, actor)
            self._require_link(v)
            v.state["params"].clear()
            v.state["param_count"] = 0
            if v.param_named is not None:
                v.param_named = NamedReads(v.param_named.names)
            self._fetch_params(v, self.clock())
            return {"id": vid, "requested": True}

        return self.call(run)

    def set_param(
        self,
        vid: str,
        name: str,
        value: Any,
        confirm: str | None,
        wait: bool = True,
        actor: Actor | None = None,
    ) -> dict[str, Any]:
        name = str(name or "").strip()
        if confirm != name or not name:
            raise err("confirm_required", expected=name or "NAME")
        t0 = self.clock()

        def start() -> tuple[int, float]:
            v = self._vehicle(vid)
            if actor is not None and actor.kind == "session":
                self._check_control(v, actor)
            self._require_link(v)
            entry = v.state["params"].get(name)
            if entry is None:
                raise err("param_unknown", name=name)
            ptype = entry["type"]
            try:
                number = normalise(value, ptype)
            except ValueError as exc:
                raise err("param_type", name=name, type=type_name(ptype)) from exc
            v.state.setdefault("param_set_at", {})[name] = t0
            v.driver.set_param(name, number, ptype)
            return ptype, number

        ptype, number = self.call(start)
        if not wait or not self.threaded:
            return {"id": vid, "name": name, "value": number, "pending": True}
        resent = False
        while self.clock() < t0 + 3.0:
            entry, rx_at = self.call(
                lambda: (
                    dict(self._vehicle(vid).state["params"].get(name) or {}),
                    self._vehicle(vid).state["param_rx_at"],
                )
            )
            if entry and rx_at is not None and rx_at >= t0 and same_value(entry["value"], number, ptype):
                self.call(lambda: self._param_event(vid, name, number))
                return {"id": vid, "name": name, "value": entry["value"], "type": type_name(ptype)}
            if not resent and self.clock() > t0 + 1.5:
                resent = True
                self.call(lambda: self._vehicle(vid).driver.set_param(name, number, ptype))
            time.sleep(0.05)
        entry = self.call(lambda: dict(self._vehicle(vid).state["params"].get(name) or {}))
        if entry and not same_value(entry["value"], number, ptype):
            raise err("param_readback_mismatch", name=name, value=number, readback=entry["value"])
        raise err("param_timeout", name=name)

    def _param_event(self, vid: str, name: str, value: float) -> None:
        v = self._vehicle(vid)
        self.events.emit("param_set", v.id, data={"name": name, "value": value}, labels={"vehicle": v.label})

    # ====================================================================== courses

    def _courses_path(self) -> Path | None:
        return None if self.data_dir is None else self.data_dir / "courses.json"

    def _load_courses(self) -> dict[str, dict[str, Any]]:
        path = self._courses_path()
        if path is None or not path.exists():
            return {}
        try:
            data = json.loads(path.read_text("utf-8"))
            return {c["id"]: c for c in data if isinstance(c, dict) and "id" in c}
        except (OSError, ValueError, KeyError):
            return {}

    def _volume(self) -> tuple[float, float, float]:
        sv = self.settings.safety_volume
        return (sv.x, sv.y, sv.z)

    def courses(self) -> dict[str, Any]:
        volume = self._volume()
        items = [coursemod.scaled(c, volume) for c in coursemod.PRESETS.values()]
        items += [dict(c) for c in self.custom_courses.values()]
        sv = self.settings.safety_volume
        return {
            "safety_volume": {"x": sv.x, "y": sv.y, "z": sv.z},
            "scale": round(coursemod.scale_factor(volume), 4),
            "courses": items,
        }

    def course(self, course_id: str) -> dict[str, Any]:
        for c in self.courses()["courses"]:
            if c["id"] == course_id:
                return c
        raise err("course_not_found", course=course_id)

    def put_course(self, course_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            course = coursemod.validate_custom(course_id, body, self._volume())
        except PermissionError as exc:
            raise err("course_preset_readonly", course=course_id) from exc
        except ValueError as exc:
            raise err("invalid_course", reason=str(exc)) from exc

        def run() -> dict[str, Any]:
            self.custom_courses[course_id] = course
            path = self._courses_path()
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(list(self.custom_courses.values()), ensure_ascii=False, indent=2), "utf-8"
                )
            return course

        return self.call(run)

    def _best_for(self, course_id: str) -> float | None:
        if course_id not in self._best:
            self._best[course_id] = self.history.best_course(course_id)
        return self._best[course_id]

    def course_start(self, vid: str, actor: Actor, course_id: str) -> dict[str, Any]:
        course = self.course(course_id)

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            auth.check_operator_or_controller(v.auth, actor, self._claim_alive(v))
            self._require_link(v)
            if not v.capabilities.position:
                raise err("no_position_estimate")
            now = self.clock()
            if v.course is not None and v.course.active:
                self._abort_course(v, "restarted", now)
            if v.kind == "sim":
                origin = (v.spawn[0], v.spawn[1], 0.0, 0.0)
            else:
                p = v.world_enu() or (0.0, 0.0, 0.0)
                z = p[2] if not v.airborne() else p[2] - (v.altitude() or 0.0)
                heading = math.degrees((v.state["attitude"] or {}).get("yaw", 0.0)) % 360.0
                origin = (p[0], p[1], z, heading)
            run_ = coursemod.CourseRun.begin(course, origin, self._best_for(course_id))
            run_.controller = v.controller(self.sessions)
            if v.airborne():
                run_.update(v.world_enu() or (0.0, 0.0, 0.0), now, self.wall(), True)
            v.course = run_
            v.course_clear_at = None
            self.events.emit(
                "course_started",
                v.id,
                data={"course_id": course_id, "total": len(run_.rings), "origin": list(origin)},
                labels={"vehicle": v.label, "course": course.get("name_en", course_id)},
            )
            return {"id": vid, "course": run_.view(now)}

        return self.call(run)

    def course_stop(self, vid: str, actor: Actor) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            auth.check_operator_or_controller(v.auth, actor, self._claim_alive(v))
            if v.course is None or not v.course.active:
                raise err("course_not_running")
            self._abort_course(v, "stopped", self.clock())
            return {"id": vid, "course": v.course.view(self.clock()) if v.course else None}

        return self.call(run)

    def _abort_course(self, v: Vehicle, reason: str, now: float) -> None:
        run = v.course
        if run is None:
            return
        result = run.abort(reason, now)
        if result is not None:
            self._emit_course(v, result[0], result[1], now)

    def _emit_course(self, v: Vehicle, kind: str, data: dict[str, Any], now: float) -> None:
        run = v.course
        assert run is not None
        course_name = run.course.get("name_en", run.course["id"])
        payload = {"course_id": run.course["id"], **data}
        if kind == "ring_touch":  # WP-A (07 §2)
            payload["vehicle"] = v.id
        self.events.emit(kind, v.id, data=payload, labels={"vehicle": v.label, "course": course_name})
        if kind in ("course_finished", "course_aborted"):
            v.course_clear_at = now + COURSE_RESULT_HOLD_S
            if kind == "course_finished":
                total = data["total_s"]
                best = self._best.get(run.course["id"])
                if best is None or total < best:
                    self._best[run.course["id"]] = total
            if run.started is not None or kind == "course_finished":
                self.history.course_run(
                    {
                        "session_id": v.record_session,
                        "vehicle_id": v.id,
                        "course_id": run.course["id"],
                        "started_at": run.started_wall,
                        "finished_at": self.wall(),
                        "state": run.state,
                        "total_s": run.total_s(),
                        "splits": run.splits,
                        "controller": v.controller(self.sessions),
                    }
                )

    # ====================================================================== discovery and bridges

    def network(self) -> dict[str, Any]:
        nets = local_networks()
        return {
            "interfaces": nets,
            "on_bridge_ap": on_bridge_ap(nets),
            "listen": self._listen_view(),
            "bridge_port": self.settings.bridge_port,
            "api_port": self.http_port,
            "allow_loopback": self.allow_loopback,
            "arena": self.arena.network_view(),
        }

    def discover(self, networks: list[str] | None = None, http_port: int = 80) -> dict[str, Any]:
        if self.scan["running"]:
            raise err("scan_running")
        if not networks and self.scan_default is not None:
            networks, http_port = self.scan_default
        if networks:
            chosen = []
            for cidr in networks:
                try:
                    net = ipaddress.IPv4Network(str(cidr), strict=False)
                except ValueError as exc:
                    raise err("invalid_request", reason=f"{cidr} is not a network") from exc
                if net.prefixlen < 22:
                    raise err("invalid_request", reason="networks larger than /22 are not scanned")
                if net.is_loopback and not self.allow_loopback:
                    raise err("invalid_request", reason="loopback networks are not scanned")
                chosen.append(
                    {
                        "network": str(net),
                        "scannable": True,
                        "ip": None,
                        "broadcast": str(net.broadcast_address),
                    }
                )
        else:
            chosen = [n for n in local_networks(max_age=0.0) if n["scannable"]]
        hosts = scan_hosts(chosen)
        self.scan.update(
            running=True,
            done=0,
            total=len(hosts),
            networks=[n["network"] for n in chosen],
            found=0,
            error=None,
        )
        self._scan_broadcast = [
            n["broadcast"] for n in chosen if not ipaddress.IPv4Address(n["broadcast"]).is_loopback
        ]
        self._scan_stop.clear()
        self.post(self._broadcast_wake)
        found: list[dict[str, Any]] = []

        def on_result(host: str, record: dict[str, Any] | None) -> None:
            def apply() -> None:
                self.scan["done"] += 1
                if record is not None:
                    self.scan["found"] += 1
                    found.append(record)
                    vid = self.by_host.get(host)
                    if vid is None and self.link is not None and self.link.ok:
                        self._add_real(
                            host, self.settings.bridge_port, "http", self.clock(), version=record["version"]
                        )
                    elif vid is not None:
                        v = self.vehicles[vid]
                        v.bridge = {
                            "version": record["version"],
                            "source": (v.bridge or {}).get("source", "http"),
                        }
                    vid = self.by_host.get(host)
                    if vid is not None:
                        self.vehicles[vid].driver.heartbeat()

            self.post(apply)

        def worker() -> None:
            try:
                discovery.probe_many(hosts, on_result, port=http_port, stop=self._scan_stop.is_set)
            except Exception as exc:  # report, never crash the service
                message = str(exc)
                self.post(lambda: self.scan.update(error=message))
            finally:
                self.post(lambda: self.scan.update(running=False))

        threading.Thread(target=worker, name="relay-scan", daemon=True).start()
        return {"networks": self.scan["networks"], "total": len(hosts), "found": found}

    def bridge_read(self, host: str, http_port: int = 80) -> dict[str, Any]:
        self._validate_bridge_host(host)
        record = bridgemod.read_bridge(host, http_port)
        record["on_bridge_ap"] = on_bridge_ap(local_networks())
        record["last_network_ssid"] = self.settings.last_network_ssid or None
        return record

    def bridge_configure(self, host: str, http_port: int, desired: dict[str, Any]) -> dict[str, Any]:
        self._validate_bridge_host(host)
        result = bridgemod.configure_bridge(host, http_port, desired)
        ssid = str(desired.get("ssid") or "").strip()
        if ssid and str(desired.get("mode", "station")).lower() == "station":
            self.update_settings({"last_network_ssid": ssid})
        self.call(
            lambda: self.events.emit(
                "bridge_configured",
                data={
                    "host": host,
                    "mode": desired.get("mode", "station"),
                    "ssid": ssid or None,
                    "fields": result["submitted"],
                },
                labels={"host": host},
            )
        )
        return result

    def _validate_bridge_host(self, host: str) -> None:
        try:
            address = ipaddress.IPv4Address(host)
        except ValueError as exc:
            raise err("invalid_address", host=host) from exc
        if address.is_loopback and not self.allow_loopback:
            raise err("invalid_address", host=host)

    # ====================================================================== settings and alerts

    def get_settings(self) -> dict[str, Any]:
        data = self.settings.to_dict()
        data["flight_level"] = flight_level.view(self.settings)  # WP-1 (06 §5), read only
        data["restart_fields"] = list(Settings.RESTART_FIELDS)
        # read only: where records, settings and logs live (shown in Settings > Data)
        data["data_dir"] = str(self.data_dir) if self.data_dir is not None else None
        return data

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        try:
            new = self.settings.merged(patch)
        except (ValueError, TypeError) as exc:
            raise err("invalid_request", reason=f"setting {exc} is not valid") from exc
        self.arena.validate(new)

        def run() -> dict[str, Any]:
            self.settings = new
            save_settings(self.data_dir, new)
            self._apply_sim_settings()  # WP-C: turbulence follows the setting live
            self.arena.apply_settings()  # WP-D: peer port, callsign, auto respawn
            return self.get_settings()

        return self.call(run)

    def ack_alert(self, alert_id: str) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            if not self.alerts.ack(alert_id):
                raise err("alert_not_found", alert=alert_id)
            return {"id": alert_id, "acked": True}

        return self.call(run)

    # ====================================================================== views

    def fleet(self) -> dict[str, Any]:
        return self._snapshot

    def session_views(self) -> list[dict[str, Any]]:
        now = self.clock()
        out = []
        for s in self.sessions.live():
            out.append(
                {
                    "session_id": s.session_id,
                    "name": s.name,
                    "created_at": s.created_wall,
                    "expires_in_s": round(max(0.0, s.expires - now), 1),
                    "lease_s": s.lease_s,
                    "last_call": s.last_call,
                    "vehicles": [
                        v.id
                        for v in self.vehicles.values()
                        if v.auth.claim and v.auth.claim.session_id == s.session_id
                    ],
                    "calls": list(s.calls),
                }
            )
        return out

    def _preflight_items(self, v: Vehicle) -> list[dict[str, Any]]:
        params = {k: e["value"] for k, e in v.state["params"].items()}
        total = v.state["param_count"]
        facts = preflight.Facts(
            kind=v.kind,
            link_state=v.link_state,
            autopilot=v.autopilot,
            heartbeats=v.state["heartbeats"],
            voltage=v.state["battery"]["voltage"],
            cells=v.cells(),
            cell_warn_v=self.settings.cell_warn_v,
            cell_crit_v=self.settings.cell_crit_v,
            prearm=v.state["prearm"],
            params=params,
            params_complete=bool(total) and len(params) >= total,
            params_unreadable=v.param_named.unreadable(params) if v.param_named else frozenset(),
            confirmed_signs=v.confirmed.signs,
            confirmed_props=v.confirmed.props,
            mode=v.mode,
            mode_accepts_sticks=modes.accepts_sticks(v.autopilot, v.mode),
            position=v.position_ok(),
        )
        return preflight.evaluate(facts)

    def preflight(self, vid: str) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            items = self._preflight_items(v)
            return {"id": vid, **preflight.summary(items), "items": items}

        return self.call(run)

    def messages(self, vid: str) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            now = self.clock()
            wall = self.wall()
            out = {}
            for name, entry in sorted(v.state["messages"].items()):
                out[name] = {
                    "fields": entry["fields"],
                    "t": round(wall - (now - entry["t"]), 3),
                    "age_s": round(now - entry["t"], 3),
                    "hz": decode.message_rate(entry["times"], now),
                    "count": entry["count"],
                    "src": entry["src"],
                }
            return {"id": vid, "messages": out}

        return self.call(run)

    def params(self, vid: str) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            params = v.state["params"]
            total = v.state["param_count"]
            fetch = v.param_fetch
            return {
                "id": vid,
                "received": len(params),
                "total": total,
                "complete": bool(total) and len(params) >= total,
                "fetching": fetch is not None and not fetch.done,
                "params": [
                    {"name": n, "value": e["value"], "type": type_name(e["type"]), "index": e["index"]}
                    for n, e in sorted(params.items())
                ],
            }

        return self.call(run)

    def vehicle_detail(self, vid: str) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            now = self.clock()
            view = self._vehicle_view(v, now, self.wall())
            st = v.state
            items = self._preflight_items(v)
            view["detail"] = {
                "statustext": st["statustext"],
                "home": st["home"],
                "version": st["version"],
                "vibration": st["vibration"],
                "radio": st["radio"],
                "last_ack": {k: x for k, x in (st["last_ack"] or {}).items() if k not in ("t",)} or None,
                "cell_voltages": st["battery"]["cell_voltages"],
                "consumed_mah": st["battery"]["consumed_mah"],
                "params": {"received": len(st["params"]), "total": st["param_count"]},
                "preflight_items": items,
                "safety_origin": list(v.safety_origin) if v.safety_origin else None,
                "spawn": list(v.spawn) if v.kind == "sim" else None,
                "port": v.port or None,
            }
            return view

        return self.call(run)

    def topology(self) -> dict[str, Any]:
        from . import topology

        return topology.build(self._snapshot, sorted(self.link.local_ips) if self.link else [])

    def _vehicle_view(self, v: Vehicle, now: float, wall: float) -> dict[str, Any]:
        st = v.state
        att = st["attitude"]
        local = st["local"]
        hud = st["hud"] or {}
        glob = st["global"]
        battery = st["battery"]
        age = self._link_age(v, now)
        rx = sum(1 for t in v.rx_times if now - t <= RATE_WINDOW_S) / RATE_WINDOW_S
        tx = sum(1 for t in v.driver.tx_times[-400:] if now - t <= RATE_WINDOW_S) / RATE_WINDOW_S
        received = sum(b[1] for b in v.loss_buckets)
        lost = sum(b[2] for b in v.loss_buckets)
        loss = None if received == 0 else round(100.0 * lost / (received + lost), 1)
        rtt = v.rtt_ms if v.rtt_at is not None and now - v.rtt_at < TIMESYNC_STALE_S else None
        position = None
        if local is not None:
            ox, oy, oz = v.frame_offset
            position = {
                "frame": "sim" if v.kind == "sim" else "local",
                "x": _round(local["y"] + ox),
                "y": _round(local["x"] + oy),
                "z": _round(-local["z"] + oz),
                "vx": _round(local["vy"]),
                "vy": _round(local["vx"]),
                "vz": _round(-local["vz"]),
                "age_s": _round(now - local["t"], 2),
            }
        climb = hud.get("climb")
        if climb is None and local is not None:
            climb = -local["vz"]
        ground = hud.get("groundspeed")
        if ground is None and local is not None:
            ground = math.hypot(local["vx"], local["vy"])
        heading = hud.get("heading")
        if heading is None and att is not None:
            heading = math.degrees(att["yaw"]) % 360.0
        claim = v.auth.claim
        claim_view = None
        if claim is not None and self.sessions.alive(claim.session_id):
            claim_view = claim.view(v.auth.possession is not None)
        sticks = None
        if v.skill is not None and v.skill.active:
            sticks = {"roll": 0.0, "pitch": 0.0, "yaw": 0.0, "throttle": 0.5, "age_s": 0.0, "source": "agent"}
        elif v.sticks is not None:
            s = v.sticks
            sticks = {
                "roll": _round(s.roll),
                "pitch": _round(s.pitch),
                "yaw": _round(s.yaw),
                "throttle": _round(s.throttle),
                "age_s": _round(now - s.t, 2),
                "source": s.source,
            }
        cells = v.cells()
        items = self._preflight_items(v)
        link = {
            "state": v.link_state,
            "heartbeat_age_s": _round(age, 2),
            "rx_hz": round(rx, 1),
            "tx_hz": round(tx, 1),
            "loss_pct": loss,
            "rtt_ms": rtt,
        }
        human = v.sticks is not None and v.sticks.source == "human" and now - v.sticks.t < STICK_TIMEOUT_S
        gps = st["gps"]
        return {
            "id": v.id,
            "kind": v.kind,
            "label": v.label,
            "host": v.host,
            "autopilot": v.autopilot,
            "firmware": st["firmware"],
            "sysid": st["sysid"],
            "bridge": v.bridge,
            "link": link,
            "armed": v.armed,
            "mode": v.mode,
            "mode_class": modes.mode_class(v.autopilot, v.mode),
            "modes": modes.available_modes(v.autopilot),
            "landed": v.landed,
            "crashed": bool(st["crashed"]),
            "attitude": None if att is None else {k: _round(att[k], 4) for k in ("roll", "pitch", "yaw")},
            "rates": None
            if att is None
            else {
                "roll": _round(att["rollspeed"], 4),
                "pitch": _round(att["pitchspeed"], 4),
                "yaw": _round(att["yawspeed"], 4),
            },
            "position": position,
            "global": None
            if glob is None or not glob["valid"]
            else {"lat": glob["lat"], "lon": glob["lon"], "alt": _round(glob["alt"], 2)},
            "altitude_m": _round(v.altitude()),
            "climb_ms": _round(climb),
            "groundspeed_ms": _round(ground),
            "heading_deg": _round(heading, 1),
            "throttle_pct": hud.get("throttle"),
            "battery": {
                "voltage": battery["voltage"],
                "current": battery["current"],
                "remaining_pct": battery["remaining_pct"],
                "cells": cells,
                "level": v.battery_level if battery["voltage"] is not None else "unknown",
            },
            "gps": None if gps is None else {"fix": gps["fix"], "sats": gps["sats"], "hdop": gps["hdop"]},
            "sensors": st["sensors"],
            "statustext": st["statustext"][-STATUSTEXT_SNAPSHOT:],
            "rc": st["rc"],
            "servo": st["servo"],
            "flight_time_s": round(
                v.flight_time_s + (now - v.flight_t0 if v.flight_t0 is not None else 0.0), 1
            ),
            "armed_time_s": round(v.armed_time_s + (now - v.armed_t0 if v.armed_t0 is not None else 0.0), 1),
            "capabilities": v.capabilities.view(),
            "claim": claim_view,
            "possession": v.auth.possession.view() if v.auth.possession else None,
            "controller": v.controller(self.sessions),
            "sticks": sticks,
            "skill": None if v.skill is None else v.skill.view(),
            "plan": None if v.plan is None else v.plan.view(),
            "health": v.health.view(v, now, link, human),
            "calibration": v.calibration.view(),
            "confirmed": v.confirmed.view(),
            "preflight": preflight.summary(items),
            "sim": self._sim_view(v),  # WP-C (05 §5.2)
            "course": None if v.course is None else v.course.view(now),
            "record_session": v.record_session,
            "procedure": self._procedure_brief(v),  # bench procedure (WP-B, 05 §4.4)
            # WP-1 onboard programs (06 §2.6)
            "program": self.programs.view(v),
            "programs_allowed": v.kind == "sim" or v.programs_allowed,
        }

    def _build_snapshot(self, now: float) -> None:
        wall = self.wall()
        vehicles = []
        for v in sorted(self.vehicles.values(), key=lambda x: (x.kind != "real", x.created, x.id)):
            try:
                vehicles.append(self._vehicle_view(v, now, wall))
            except Exception:
                log.exception("snapshot failed for %s", v.id)
        sv = self.settings.safety_volume
        sessions = [
            {
                "session_id": s["session_id"],
                "name": s["name"],
                "expires_in_s": s["expires_in_s"],
                "vehicles": s["vehicles"],
            }
            for s in self.session_views()
        ]
        self._snapshot = {
            "t": wall,
            "version": VERSION,
            "listen": self._listen_view(),
            "scan": dict(self.scan),
            "sessions": sessions,
            "safety_volume": {"x": sv.x, "y": sv.y, "z": sv.z},
            "vehicles": vehicles,
            "alerts": self.alerts.view(),
        }

    def health(self) -> dict[str, Any]:
        return {"ok": True, "version": VERSION, "uptime_s": round(self.clock() - self.started, 1)}

    # ====================================================================== LAN arena (05 §7, WP-D)

    def fire(self, vid: str, actor: Actor, on: bool = True, seconds: float | None = None) -> dict[str, Any]:
        """Hold-type fire input: WebSocket ``on`` lapses after 300 ms; REST holds up to 3 s (05 §7.4)."""

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)
            if v.kind != "sim":
                raise err("sim_only")
            self._check_control(v, actor)
            return self.arena.fire(v, on, seconds, self.clock())

        return self.call(run)

    def arena_snapshot(self) -> dict[str, Any]:
        """The ``arena`` stream payload (05 §7.5), rebuilt at 20 Hz on the fleet thread."""
        return self.arena.snapshot


def _level_rank(level: str) -> int:
    return {"unknown": 0, "ok": 0, "warn": 1, "critical": 2}.get(level, 0)


def _number_param(params: dict[str, Any], key: str) -> float:
    value = params.get(key) or 0.0
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise err("invalid_request", reason=f"{key} must be a number of metres")
    return float(value)


def _skill_number(
    params: dict[str, Any], key: str, low: float, high: float, default: float | None = None
) -> float:
    value = params.get(key, default)
    if value is None:
        raise err("invalid_request", reason=f"{key} is required")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise err("invalid_request", reason=f"{key} must be a number")
    if not low <= float(value) <= high:
        raise err("invalid_request", reason=f"{key} must be {low:g} to {high:g}")
    return float(value)


def _public(params: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in params.items() if not k.startswith("_")}
