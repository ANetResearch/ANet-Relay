"""PX4 hardware in the loop over USB (11 §7): backup, write, reboot, run, restore, verify.

``HitlSession`` is the §7.2 flow as a state machine, ticked by the fleet thread (``tick``) and fed the
aircraft's MAVLink (``on_message``). It talks to the flight controller through the aircraft's own serial
driver (``drivers.serial.SerialDriver``), so Relay's heartbeat, sticks and modes keep going to the board.

States: ``idle`` -> ``backing_up`` -> ``writing`` -> ``rebooting`` -> ``running`` -> ``restoring`` ->
``verifying`` -> ``restored``; ``failed`` when a step fails and nothing more can be done. Any failure after
the backup goes through the restore (§7.2 step 5).

1. ``backing_up``: a fresh PARAM_REQUEST_LIST (gaps re-read by index) until every parameter answered; the
   table goes to ``<data dir>/hitl-backups/<vehicle>-<UTC time>.json`` with ``restore_pending: true``
   before anything is written. PX4 integer parameters are decoded bytewise (``mav.decode``).
2. ``writing``: ``SYS_HITL = 1`` and ``SYS_AUTOSTART = 1001`` (PX4's "HIL Quadcopter X" airframe), nothing
   else (``SYS_AUTOCONFIG`` stays: it would reset every parameter to the airframe's defaults). Each
   PARAM_SET is repeated until the PARAM_VALUE echo carries the value. Then ``SAVE_SETTLE_S`` for PX4's
   parameter autosave before the reboot.
3. ``rebooting``: MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN (repeated until the link drops), then the link must
   drop (the USB device goes away, or the heartbeat stops for ``DROP_GAP_S``) and come back with
   ``MAV_MODE_FLAG_HIL_ENABLED`` in the heartbeat; ``BOOT_SETTLE_S`` later the stream starts.
4. ``running``: real time, ``HIL_SENSOR`` every 4 ms of physics and ``HIL_GPS`` at 10 Hz (``twin.hil``), the
   physics (``sim.physics.Quad`` with the aircraft's frame, effects on) stepped with the last
   ``HIL_ACTUATOR_CONTROLS``. The firmware keeps the real motor outputs locked in HITL mode.
5. ``restoring``: wait for the link, disarm (forced only while the heartbeat shows HITL mode, where the
   motors are locked; otherwise wait for the operator), write every changed parameter back with its backed
   up value and type, reboot, wait for the board to come back without the HITL flag (when the backup had
   ``SYS_HITL 0``).
6. ``verifying``: re-read the whole table and compare it with the backup item by item (integers by value,
   floats by their IEEE bytes). Differences are ``mismatches`` (``hitl_restore_mismatch``); the backup file
   is kept either way and gets ``restore_pending: false``, ``restored_at`` and the mismatches.

A backup file with ``restore_pending: true`` means a restore is owed (Relay quit or the link went away
mid-session): the fleet starts it automatically the next time that board connects (``HitlSession.resume``).
Parameters the firmware changes by itself (``VOLATILE``: flight time and flight counters) are not
compared.
"""

from __future__ import annotations

import json
import math
import os
import re
import struct
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..mav.decode import px4_param_decode, text_field
from ..mav.mavlink import M
from ..sim.frame import FrameModel
from ..sim.physics import DT, Quad, seed_for, thrust_fraction
from ..sim.sensors import SensorModel
from . import identity, wmm
from .hil import SIM_COMPID, HilEncoder, decode_actuators

HITL_AIRFRAME = 1001  # PX4 ROMFS 1001_rc_quad_x.hil
TARGETS = (("SYS_HITL", 1.0), ("SYS_AUTOSTART", float(HITL_AIRFRAME)))
VOLATILE = ("LND_FLIGHT_T_HI", "LND_FLIGHT_T_LO", "COM_FLIGHT_UUID")
SENSOR_NOISE = 0.5  # a calibrated board, as the firmware twin (twin.autopilot.SENSOR_NOISE)
START_US = 1_000_000

BACKUP_TIMEOUT_S = 120.0
LIST_RETRY_S = 3.0
STALL_S = 1.0
READS_PER_ROUND = 25
MAX_ROUNDS = 200
ECHO_S = 1.0
ECHO_TRIES = 5
SAVE_SETTLE_S = 1.5
REBOOT_RETRY_S = 3.0
REBOOT_TRIES = 3
REBOOT_TIMEOUT_S = 45.0
DROP_GAP_S = 2.5  # longer than PX4's 1 Hz heartbeat period
LINK_STALE_S = 3.5
BOOT_SETTLE_S = 2.0
FLAG_WAIT_S = 6.0
LINK_WAIT_S = 60.0
DISARM_S = 1.0
DISARM_WAIT_S = 30.0
MAX_CATCHUP = 25  # physics steps per tick at most (0.1 s); a longer stall skips time
CRASH_RESET_S = 5.0
RATE_WINDOW_S = 1.0

STEP_KEYS = ("confirm", "backup", "write", "reboot", "run", "restore", "verify")
STEP_ZH = {
    "confirm": "确认",
    "backup": "备份",
    "write": "写入",
    "reboot": "重启",
    "run": "运行",
    "restore": "恢复",
    "verify": "核对",
}
ACTIVE = ("backing_up", "writing", "rebooting", "running", "restoring", "verifying")

INT_TYPES = (
    M.MAV_PARAM_TYPE_UINT8,
    M.MAV_PARAM_TYPE_INT8,
    M.MAV_PARAM_TYPE_UINT16,
    M.MAV_PARAM_TYPE_INT16,
    M.MAV_PARAM_TYPE_UINT32,
    M.MAV_PARAM_TYPE_INT32,
    M.MAV_PARAM_TYPE_UINT64,
    M.MAV_PARAM_TYPE_INT64,
)

# English reason -> Chinese (step details, failures).
ZH = {
    "waiting": "等待",
    "confirmed: propellers removed": "已确认拆下螺旋桨",
    "reading the parameter table": "正在读取参数表",
    "the parameter table did not arrive in time": "未能在限定时间内读完参数表",
    "the parameter table has no SYS_HITL or SYS_AUTOSTART": "参数表中没有 SYS_HITL 或 SYS_AUTOSTART",
    "the backup file could not be written": "无法写入备份文件",
    "writing the parameters": "正在写入参数",
    "the flight controller did not confirm the parameter": "飞控未确认参数写入",
    "the parameters already select hardware in the loop": "参数已是硬件在环配置",
    "saving the parameters": "等待飞控保存参数",
    "rebooting the flight controller": "正在重启飞控",
    "waiting for the flight controller to come back": "等待飞控重新连接",
    "the flight controller did not come back in time": "飞控未能在限定时间内重新连接",
    "the flight controller came back without hardware-in-the-loop mode": "飞控重启后未进入硬件在环模式",
    "the flight controller came back still in hardware-in-the-loop mode": "飞控重启后仍处于硬件在环模式",
    "the flight controller is starting": "飞控正在启动",
    "the physics feeds the flight controller": "物理模型正在向飞控提供传感器数据",
    "no actuator outputs from the flight controller yet": "尚未收到飞控的执行器输出",
    "stopped by the operator": "操作员已停止",
    "the link was lost": "链路已丢失",
    "waiting for the link": "等待链路恢复",
    "the link did not come back in time": "链路未能在限定时间内恢复",
    "disarming": "正在上锁",
    "waiting for the operator to disarm": "等待操作员上锁",
    "the aircraft did not disarm": "飞机未上锁",
    "writing the backed-up values": "正在写回备份值",
    "rereading the parameter table": "正在重新读取参数表",
    "every parameter matches the backup": "全部参数与备份一致",
    "parameters differ from the backup": "部分参数与备份不一致",
    "restore pending from an earlier session": "上次会话的参数恢复尚未完成",
    "the session ended before it started": "会话在开始前已结束",
    "not part of this restore": "本次恢复不涉及",
    "no parameter was changed": "无参数被修改",
}


def zh(text: str) -> str:
    return ZH.get(text, text)


# ---------------------------------------------------------------------------- parameter values


def _is_int(ptype: int) -> bool:
    return ptype in INT_TYPES


def same_param(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    """Item comparison of two ``{value, type}`` entries: integers by value, floats by their IEEE bytes."""
    if int(expected["type"]) != int(actual["type"]):
        return False
    a, b = float(expected["value"]), float(actual["value"])
    if _is_int(int(expected["type"])):
        return round(a) == round(b)
    fmt = "<d" if int(expected["type"]) == M.MAV_PARAM_TYPE_REAL64 else "<f"
    try:
        return struct.pack(fmt, a) == struct.pack(fmt, b)
    except (OverflowError, struct.error):
        return a == b


def compare(backup: dict[str, dict[str, Any]], current: dict[str, dict[str, Any]]) -> tuple[list, list]:
    """(mismatches, volatile names that changed). A parameter missing on either side is a mismatch."""
    mismatches: list[dict[str, Any]] = []
    volatile: list[str] = []
    for name in sorted(set(backup) | set(current)):
        want, got = backup.get(name), current.get(name)
        if want is not None and got is not None and same_param(want, got):
            continue
        if name in VOLATILE:
            volatile.append(name)
            continue
        mismatches.append(
            {
                "name": name,
                "expected": None if want is None else _number(want),
                "actual": None if got is None else _number(got),
            }
        )
    return mismatches, volatile


def _number(entry: dict[str, Any]) -> float | int:
    value = float(entry["value"])
    if _is_int(int(entry["type"])) and math.isfinite(value):
        return round(value)
    return value


def decode_param(msg: Any, autopilot: str | None) -> tuple[str, dict[str, Any], int, int] | None:
    """(name, {value, type}, index, count) from PARAM_VALUE, PX4 integers decoded bytewise."""
    name = text_field(msg.param_id)
    if not name:
        return None
    ptype = int(msg.param_type or M.MAV_PARAM_TYPE_REAL32)
    value = float(msg.param_value)
    if autopilot == "PX4":
        value = px4_param_decode(value, ptype)
    if _is_int(ptype) and math.isfinite(value):
        value = float(round(value))
    count = int(msg.param_count or 0)
    return name, {"value": value, "type": ptype}, int(msg.param_index), count


class ParamCollector:
    """One whole-table read: PARAM_REQUEST_LIST, then the missing indices again while it stalls."""

    def __init__(self, now: float) -> None:
        self.values: dict[str, dict[str, Any]] = {}
        self.indices: set[int] = set()
        self.count = 0
        self.started = now
        self.last_rx = now
        self.list_sent = 0
        self.rounds = 0

    def start(self, driver: Any, now: float) -> None:
        self.started = self.last_rx = now
        self.list_sent = 1
        driver.request_params()

    def add(self, name: str, entry: dict[str, Any], index: int, count: int, now: float) -> None:
        self.values[name] = entry
        if 0 <= index < 65535:
            self.indices.add(index)
        if count and count != 65535:
            self.count = count
        self.last_rx = now

    @property
    def complete(self) -> bool:
        return self.count > 0 and len(self.values) >= self.count

    def tick(self, driver: Any, now: float) -> None:
        if self.complete:
            return
        if not self.values:
            if now - self.started >= LIST_RETRY_S * self.list_sent and self.list_sent < 3:
                self.list_sent += 1
                driver.request_params()
            return
        if not self.count or now - self.last_rx < STALL_S or self.rounds >= MAX_ROUNDS:
            return
        missing = [i for i in range(self.count) if i not in self.indices][:READS_PER_ROUND]
        self.rounds += 1
        self.last_rx = now
        for index in missing:
            driver.request_param(index)


# ---------------------------------------------------------------------------- backup files

FILE_TIME = "%Y%m%dT%H%M%SZ"
SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def backup_name(vehicle: str, wall: float) -> str:
    stamp = time.strftime(FILE_TIME, time.gmtime(wall))
    return f"{SAFE.sub('-', vehicle)}-{stamp}.json"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), "utf-8")
    os.replace(tmp, path)


def read_backup(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("kind") != "relay-hitl-backup":
        return None
    params = data.get("params")
    if not isinstance(params, dict):
        return None
    return data


def pending_backups(directory: Path | None, match: dict[str, Any]) -> list[Path]:
    """Backups owing a restore for this board, newest first. A board matches by its USB serial number
    (and vendor) when both sides know it, otherwise by the Relay vehicle id."""
    if directory is None or not directory.is_dir():
        return []
    out: list[tuple[float, Path]] = []
    for path in directory.glob("*.json"):
        data = read_backup(path)
        if data is None or not data.get("restore_pending"):
            continue
        board = data.get("board") or {}
        number = board.get("serial_number")
        if number and match.get("serial_number"):
            if number != match["serial_number"] or board.get("vid") != match.get("vid"):
                continue
        elif data.get("vehicle") != match.get("vehicle"):
            continue
        out.append((float(data.get("captured_at") or 0.0), path))
    return [p for _, p in sorted(out, reverse=True)]


# ---------------------------------------------------------------------------- physics


class HitlBody:
    """The world the flight controller flies in: a ``Quad`` with the aircraft's frame and the sensors."""

    def __init__(self, frame: FrameModel, home: tuple[float, float, float], name: str) -> None:
        self.home = home
        self.quad = Quad(effects=True, turbulence=0.0, seed=seed_for(f"{name}:hitl"), frame=frame)
        lat, lon, _alt = home
        self.sensors = SensorModel(
            home, seed=seed_for(f"{name}:hitl:sensors"), noise=SENSOR_NOISE, field=wmm.field(lat, lon)
        )
        self.hil = HilEncoder(self.sensors, compid=SIM_COMPID)
        self.controls = [0.0, 0.0, 0.0, 0.0]
        self.armed = False
        self.t: float | None = None
        self.t_us = START_US
        self.steps = 0
        self.actuator_frames = 0
        self.actuator_at: float | None = None
        self.sent: deque[tuple[float, bool]] = deque()  # (clock time, with GPS) per HIL frame
        self.crash_reported = False
        self.disarmed_crashed_at: float | None = None

    def actuators(self, msg: Any, now: float) -> None:
        self.controls, self.armed = decode_actuators(msg)
        self.actuator_frames += 1
        self.actuator_at = now

    def advance(self, now: float) -> bytes:
        """The HIL frames for the physics steps due by ``now``, stepping the physics after each."""
        if self.t is None:
            self.t = now
        steps = int((now - self.t) / DT + 1e-9)
        if steps > MAX_CATCHUP:
            self.t = now - MAX_CATCHUP * DT
            steps = MAX_CATCHUP
        chunks = []
        for _ in range(steps):
            gps_due = self.t_us >= self.hil.next_gps_us
            chunks.append(self.hil.frame(self.quad, self.t_us, DT))
            self.sent.append((now, gps_due))
            self.quad.step([thrust_fraction(u) for u in self.controls], self.armed, DT)
            self.t += DT
            self.t_us += round(DT * 1e6)
            self.steps += 1
        while self.sent and now - self.sent[0][0] > RATE_WINDOW_S:
            self.sent.popleft()
        return b"".join(chunks)

    def rates(self) -> tuple[float, float]:
        sensor = len(self.sent) / RATE_WINDOW_S
        gps = sum(1 for _, g in self.sent if g) / RATE_WINDOW_S
        return round(sensor, 1), round(gps, 1)

    def reset(self) -> None:
        self.quad.reset()
        self.crash_reported = False
        self.disarmed_crashed_at = None

    def pose(self) -> dict[str, Any]:
        s = self.quad.s
        return {
            "x": round(s.p[0], 3),
            "y": round(s.p[1], 3),
            "z": round(s.p[2], 3),
            "vx": round(s.v[0], 3),
            "vy": round(s.v[1], 3),
            "vz": round(s.v[2], 3),
            "roll": round(s.roll, 4),
            "pitch": round(s.pitch, 4),
            "yaw": round(s.yaw, 4),
            "q": [round(c, 5) for c in s.q],
            "crashed": s.crashed,
            "on_ground": s.on_ground,
        }


# ---------------------------------------------------------------------------- session


class HitlSession:
    """One hardware-in-the-loop run (or an owed restore) on one aircraft. Fleet thread only.

    ``v`` is the fleet's ``Vehicle`` (``state``, ``driver``, ``link_state``, ``armed``); ``notify(kind,
    data)`` receives ``hitl_started``, ``hitl_restored``, ``hitl_failed`` and ``crash``; ``body_factory``
    builds the physics from the backed-up parameters when the run starts.
    """

    def __init__(
        self,
        v: Any,
        directory: Path,
        notify: Callable[[str, dict[str, Any]], None],
        body_factory: Callable[[dict[str, dict[str, Any]]], HitlBody],
        board: dict[str, Any] | None,
        now: float,
        wall: float,
    ) -> None:
        self.v = v
        self.directory = directory
        self.notify = notify
        self.body_factory = body_factory
        self.board = board or {}
        self.state = "idle"
        self.steps = {
            k: {"state": "pending", "detail": "waiting", "detail_zh": zh("waiting")} for k in STEP_KEYS
        }
        self.backup_path: Path | None = None
        self.backup: dict[str, dict[str, Any]] | None = None
        self.captured_at: float | None = None
        self.changed: dict[str, dict[str, Any]] = {}
        self.mismatches: list[dict[str, Any]] = []
        self.volatile: list[str] = []
        self.error: dict[str, Any] | None = None
        self.pending_restore = False
        self.body: HitlBody | None = None
        self.since: float | None = None  # wall time the run started
        self.phase = ""
        self.phase_at = now
        self.deadline = 0.0
        self.collector: ParamCollector | None = None
        self.sent_at: dict[str, float] = {}
        self.tries: dict[str, int] = {}
        self.confirmed: set[str] = set()
        self.writes: dict[str, tuple[float, int]] = {}  # name -> (value, type) being written
        self.reboot_sent = 0
        self.reboot_at = 0.0
        self.generation = 0
        self.dropped_at: float | None = None
        self.back_at: float | None = None
        self.expect_hil = True
        self.stop_requested = False
        self.failed_step: str | None = None
        self.created_wall = wall

    # ------------------------------------------------------------------ views

    @property
    def active(self) -> bool:
        return self.state in ACTIVE

    def view(self) -> dict[str, Any]:
        run = None
        body = self.body
        if body is not None and self.since is not None:
            sensor_hz, gps_hz = body.rates()
            run = {
                "sensor_hz": sensor_hz,
                "gps_hz": gps_hz,
                "actuator_frames": body.actuator_frames,
                "steps": body.steps,
                "since": self.since,
            }
        backup = None
        if self.backup_path is not None:
            backup = {
                "file": self.backup_path.name,
                "path": str(self.backup_path),
                "count": len(self.backup or {}),
                "captured_at": self.captured_at,
            }
        return {
            "state": self.state,
            "steps": [{"key": k, **self.steps[k]} for k in STEP_KEYS],
            "backup": backup,
            "changed": self.changed,
            "mismatches": self.mismatches,
            "volatile": self.volatile,
            "error": self.error,
            "run": run,
            "pending_restore": self.pending_restore,
        }

    def _step(self, key: str, state: str, detail: str) -> None:
        self.steps[key] = {"state": state, "detail": detail, "detail_zh": zh(detail)}

    def _phase(self, name: str, now: float, timeout: float | None = None) -> None:
        self.phase = name
        self.phase_at = now
        if timeout is not None:
            self.deadline = now + timeout

    # ------------------------------------------------------------------ helpers on the aircraft

    @property
    def driver(self) -> Any:
        return self.v.driver

    def _generation(self) -> int:
        link = getattr(self.driver, "link", None)
        return int(getattr(link, "generation", 0))

    def _hil_flag(self) -> bool:
        return bool(int(self.v.state.get("base_mode") or 0) & M.MAV_MODE_FLAG_HIL_ENABLED)

    def _link_up(self, now: float) -> bool:
        last = self.v.state.get("last_heartbeat")
        return self.v.link_state in ("ok", "delayed") and last is not None and now - last < DROP_GAP_S

    # ------------------------------------------------------------------ start / stop / resume

    def start(self, now: float) -> None:
        self._step("confirm", "done", "confirmed: propellers removed")
        self.state = "backing_up"
        self._step("backup", "active", "reading the parameter table")
        self.collector = ParamCollector(now)
        self.collector.start(self.driver, now)
        self._phase("collect", now, BACKUP_TIMEOUT_S)

    def resume(self, path: Path, data: dict[str, Any], now: float) -> None:
        """An owed restore from a backup file (Relay quit or the link went away mid-session)."""
        self.backup_path = path
        self.backup = {
            k: {"value": float(e["value"]), "type": int(e["type"])} for k, e in data["params"].items()
        }
        self.captured_at = data.get("captured_at")
        self.changed = dict(data.get("changed") or {})
        self.pending_restore = True
        for key in ("confirm", "backup", "write", "reboot", "run"):
            self._step(key, "skipped", "restore pending from an earlier session")
        self._begin_restore(now)

    def stop(self, now: float) -> None:
        """``action: stop``: end the run (or abandon the start) and restore."""
        self.stop_requested = True
        if self.state == "backing_up":
            # nothing written yet: no restore needed
            self.state = "idle"
            self._step("backup", "failed", "stopped by the operator")
            for key in ("write", "reboot", "run", "restore", "verify"):
                self._step(key, "skipped", "the session ended before it started")
            return
        if self.state in ("writing", "rebooting", "running"):
            if self.state == "running":
                self._step("run", "done", "stopped by the operator")
            else:
                self._step(self._current_step(), "failed", "stopped by the operator")
            self._begin_restore(now)

    def _current_step(self) -> str:
        return {
            "backing_up": "backup",
            "writing": "write",
            "rebooting": "reboot",
            "running": "run",
            "restoring": "restore",
            "verifying": "verify",
        }.get(self.state, "confirm")

    def _fail(self, step: str, reason: str, now: float, restore: bool) -> None:
        self._step(step, "failed", reason)
        self.failed_step = step
        self.error = {"code": "hitl_failed", "step": step, "message": reason, "message_zh": zh(reason)}
        self.notify("hitl_failed", {"step": step, "reason_en": reason, "reason_zh": zh(reason)})
        if restore:
            self._begin_restore(now)
        else:
            self.state = "failed"
            for key in STEP_KEYS:
                if self.steps[key]["state"] in ("pending", "active"):
                    self._step(key, "skipped", "the session ended before it started")

    # ------------------------------------------------------------------ messages

    def on_message(self, msg: Any, now: float) -> None:
        mtype = msg.get_type()
        st = self.v.state
        if mtype == "HIL_ACTUATOR_CONTROLS":
            if self.body is not None:
                self.body.actuators(msg, now)
            return
        if mtype != "PARAM_VALUE":
            return
        if st.get("sysid") is not None and (
            msg.get_srcSystem() != st["sysid"] or msg.get_srcComponent() != st["compid"]
        ):
            return
        decoded = decode_param(msg, st.get("autopilot"))
        if decoded is None:
            return
        name, entry, index, count = decoded
        if self.collector is not None:
            self.collector.add(name, entry, index, count, now)
        target = self.writes.get(name)
        if target is not None and same_param({"value": target[0], "type": target[1]}, entry):
            self.confirmed.add(name)

    # ------------------------------------------------------------------ tick

    def tick(self, now: float, wall: float) -> bytes:
        """Advance the flow; returns the HIL frames to send now (running and the start of a restore)."""
        state = self.state
        if state == "backing_up":
            self._tick_backup(now, wall)
        elif state == "writing":
            self._tick_write(now)
        elif state == "rebooting":
            self._tick_reboot(now, wall)
        elif state == "running":
            return self._tick_run(now, wall)
        elif state == "restoring":
            return self._tick_restore(now)
        elif state == "verifying":
            self._tick_verify(now, wall)
        return b""

    def _tick_backup(self, now: float, wall: float) -> None:
        col = self.collector
        assert col is not None
        col.tick(self.driver, now)
        if not col.complete:
            if now > self.deadline:
                self._fail("backup", "the parameter table did not arrive in time", now, restore=False)
            return
        params = dict(col.values)
        self.collector = None
        if any(name not in params for name, _ in TARGETS):
            self._fail("backup", "the parameter table has no SYS_HITL or SYS_AUTOSTART", now, restore=False)
            return
        self.backup = params
        self.captured_at = wall
        self.changed = {}
        for name, value in TARGETS:
            entry = params[name]
            if not same_param(entry, {"value": value, "type": entry["type"]}):
                self.changed[name] = {"from": _number(entry), "to": round(value)}
        path = self.directory / backup_name(self.v.id, wall)
        try:
            write_json(path, self._record(pending=True))
        except OSError:
            self._fail("backup", "the backup file could not be written", now, restore=False)
            return
        self.backup_path = path
        self._step("backup", "done", f"{len(params)} parameters saved to {path.name}")
        self.steps["backup"]["detail_zh"] = f"已保存 {len(params)} 项参数到 {path.name}"
        if not self.changed:
            self._step("write", "skipped", "the parameters already select hardware in the loop")
            if self._hil_flag():
                self._step("reboot", "skipped", "the parameters already select hardware in the loop")
                self._start_run(now, wall)
                return
            self._begin_reboot(now, expect_hil=True)
            return
        self.state = "writing"
        self._begin_writes(
            {name: (float(c["to"]), int(params[name]["type"])) for name, c in self.changed.items()}
        )
        self._step("write", "active", "writing the parameters")
        self._phase("set", now)

    def _record(self, pending: bool) -> dict[str, Any]:
        st = self.v.state
        found = identity.identify(st)
        info = {k: found.get(k) for k in ("family", "version", "type", "git", "board")}
        info["text"] = st.get("firmware")
        return {
            "format": 1,
            "kind": "relay-hitl-backup",
            "vehicle": self.v.id,
            "label": self.v.label,
            "board": self.board,
            "firmware": info,
            "sysid": st.get("sysid"),
            "captured_at": self.captured_at,
            "count": len(self.backup or {}),
            "changed": self.changed,
            "restore_pending": pending,
            "restored_at": None,
            "mismatches": [],
            "params": self.backup,
        }

    # writes ---------------------------------------------------------------

    def _begin_writes(self, writes: dict[str, tuple[float, int]]) -> None:
        self.writes = writes
        self.confirmed = set()
        self.sent_at = {}
        self.tries = {}

    def _send_writes(self, now: float) -> str | None:
        """Send due PARAM_SETs; the name that ran out of tries, if any."""
        for name, (value, ptype) in self.writes.items():
            if name in self.confirmed:
                continue
            if now - self.sent_at.get(name, -1e9) < ECHO_S:
                continue
            if self.tries.get(name, 0) >= ECHO_TRIES:
                return name
            self.tries[name] = self.tries.get(name, 0) + 1
            self.sent_at[name] = now
            self.driver.set_param(name, value, ptype)
        return None

    def _tick_write(self, now: float) -> None:
        if self.phase == "set":
            failed = self._send_writes(now)
            if failed is not None:
                self._fail("write", "the flight controller did not confirm the parameter", now, restore=True)
                return
            if self.confirmed >= set(self.writes):
                self._step("write", "active", "saving the parameters")
                self._phase("settle", now)
            return
        if self.phase == "settle" and now - self.phase_at >= SAVE_SETTLE_S:
            written = ", ".join(f"{n} = {c['to']}" for n, c in self.changed.items())
            self.steps["write"] = {"state": "done", "detail": written, "detail_zh": written}
            self._begin_reboot(now, expect_hil=True)

    # reboot ---------------------------------------------------------------

    def _begin_reboot(self, now: float, expect_hil: bool) -> None:
        self.state = "rebooting"
        self.expect_hil = expect_hil
        self._step("reboot", "active", "rebooting the flight controller")
        self.reboot_sent = 0
        self._send_reboot(now)
        self._phase("reboot", now, REBOOT_TIMEOUT_S)

    def _send_reboot(self, now: float) -> None:
        self.reboot_sent += 1
        self.reboot_at = now
        self.generation = self._generation()
        self.dropped_at = None
        self.back_at = None
        self.driver.command_long(M.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN, (1.0, 0.0))

    def _watch_reboot(self, now: float) -> str:
        """``waiting`` | ``back`` (came back with the expected HITL flag) | ``wrong`` | ``timeout``."""
        last = self.v.state.get("last_heartbeat")
        if self.dropped_at is None:
            gap = last is None or now - last >= DROP_GAP_S
            if self._generation() != self.generation or (gap and now - self.reboot_at >= DROP_GAP_S):
                self.dropped_at = now
            elif now - self.reboot_at >= REBOOT_RETRY_S and self.reboot_sent < REBOOT_TRIES:
                self.reboot_sent += 1
                self.reboot_at = now
                self.driver.command_long(M.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN, (1.0, 0.0))
        if self.dropped_at is not None and last is not None and last > self.dropped_at:
            if self.back_at is None:
                self.back_at = now
            if self._hil_flag() == self.expect_hil:
                if now - self.back_at >= BOOT_SETTLE_S:
                    return "back"
                return "waiting"
            if now - self.back_at >= FLAG_WAIT_S:
                return "wrong"
        if now > self.deadline:
            return "timeout"
        return "waiting"

    def _tick_reboot(self, now: float, wall: float) -> None:
        outcome = self._watch_reboot(now)
        if self.dropped_at is not None and self.back_at is None:
            self._step("reboot", "active", "waiting for the flight controller to come back")
        elif self.back_at is not None and outcome == "waiting":
            self._step("reboot", "active", "the flight controller is starting")
        if outcome == "back":
            self._step("reboot", "done", "rebooting the flight controller")
            self._start_run(now, wall)
        elif outcome == "wrong":
            self._fail(
                "reboot", "the flight controller came back without hardware-in-the-loop mode", now, True
            )
        elif outcome == "timeout":
            self._fail("reboot", "the flight controller did not come back in time", now, True)

    # run ------------------------------------------------------------------

    def _start_run(self, now: float, wall: float) -> None:
        self.state = "running"
        self.body = self.body_factory(self.backup or {})
        self.since = wall
        self._step("run", "active", "the physics feeds the flight controller")
        self._phase("run", now)
        self.notify("hitl_started", {"backup": None if self.backup_path is None else self.backup_path.name})

    def _tick_run(self, now: float, wall: float) -> bytes:
        body = self.body
        assert body is not None
        last = self.v.state.get("last_heartbeat")
        if now - self.phase_at > LINK_STALE_S and (last is None or now - last > LINK_STALE_S):
            self._fail("run", "the link was lost", now, restore=True)
            return b""
        data = body.advance(now)
        if body.actuator_frames == 0 and now - self.phase_at > 3.0:
            self.steps["run"]["detail"] = "no actuator outputs from the flight controller yet"
            self.steps["run"]["detail_zh"] = zh("no actuator outputs from the flight controller yet")
        elif body.actuator_frames:
            self.steps["run"]["detail"] = "the physics feeds the flight controller"
            self.steps["run"]["detail_zh"] = zh("the physics feeds the flight controller")
        s = body.quad.s
        if s.crashed and not body.crash_reported:
            body.crash_reported = True
            self.notify("crash", {"text": f"Crash: airframe down ({s.crash_reason or 'impact'})"[:50]})
        if s.crashed and not body.armed:
            if body.disarmed_crashed_at is None:
                body.disarmed_crashed_at = now
            elif now - body.disarmed_crashed_at >= CRASH_RESET_S:
                body.reset()
                self.notify("reset", {})
        return data

    # restore --------------------------------------------------------------

    def _begin_restore(self, now: float) -> None:
        self.state = "restoring"
        if self.steps["run"]["state"] == "active":
            self._step(
                "run", "done", "stopped by the operator" if self.stop_requested else "the link was lost"
            )
        for key in ("write", "reboot", "run"):
            if self.steps[key]["state"] in ("pending", "active"):
                self._step(key, "skipped", "not part of this restore")
        self._step("restore", "active", "waiting for the link")
        self._phase("link", now, LINK_WAIT_S)
        self.writes = {}

    def _tick_restore(self, now: float) -> bytes:
        data = b""
        if self.body is not None and self.phase in ("link", "disarm") and self._hil_flag():
            data = self.body.advance(now)  # keep the estimator fed until the board is disarmed
        if self.phase == "link":
            if self._link_up(now):
                self._phase("disarm", now, DISARM_WAIT_S)
            elif now > self.deadline:
                self._fail_restore("restore", "the link did not come back in time")
            return data
        if self.phase == "disarm":
            if not self.v.armed:
                backup = self.backup or {}
                writes = {
                    name: (float(backup[name]["value"]), int(backup[name]["type"]))
                    for name in self.changed
                    if name in backup
                }
                if not writes:
                    self._step("restore", "skipped", "no parameter was changed")
                    self.steps["restore"]["detail_zh"] = "无参数被修改"
                    self._begin_verify(now)
                    return data
                self._begin_writes(writes)
                self._step("restore", "active", "writing the backed-up values")
                self._phase("set", now)
                return data
            if self._hil_flag():
                self._step("restore", "active", "disarming")
                if now - self.sent_at.get("disarm", -1e9) >= DISARM_S:
                    self.sent_at["disarm"] = now
                    self.driver.arm(False, force=True)  # HITL: the firmware holds the motors
            else:
                self._step("restore", "active", "waiting for the operator to disarm")
            if now > self.deadline:
                self._fail_restore("restore", "the aircraft did not disarm")
            return data
        if self.phase in ("set", "settle"):
            if self.phase == "set":
                failed = self._send_writes(now)
                if failed is not None:
                    self._fail_restore("restore", "the flight controller did not confirm the parameter")
                elif self.confirmed >= set(self.writes):
                    self._phase("settle", now)
                return data
            if now - self.phase_at >= SAVE_SETTLE_S:
                hitl = (self.backup or {}).get("SYS_HITL")
                self.expect_hil = bool(hitl and round(float(hitl["value"])) != 0)
                self.reboot_sent = 0
                self._send_reboot(now)
                self._phase("reboot", now, REBOOT_TIMEOUT_S)
                self._step("restore", "active", "rebooting the flight controller")
            return data
        if self.phase == "reboot":
            self.body = None
            outcome = self._watch_reboot(now)
            if outcome == "back":
                written = ", ".join(f"{n} = {c['from']}" for n, c in self.changed.items())
                self.steps["restore"] = {"state": "done", "detail": written, "detail_zh": written}
                self._begin_verify(now)
            elif outcome == "wrong":
                self._fail_restore(
                    "restore", "the flight controller came back still in hardware-in-the-loop mode"
                )
            elif outcome == "timeout":
                self._fail_restore("restore", "the flight controller did not come back in time")
        return data

    def _begin_verify(self, now: float) -> None:
        self.state = "verifying"
        self._step("verify", "active", "rereading the parameter table")
        self.collector = ParamCollector(now)
        self.collector.start(self.driver, now)
        self._phase("collect", now, BACKUP_TIMEOUT_S)

    def _fail_restore(self, step: str, reason: str) -> None:
        """A restore that cannot finish now: the backup stays pending for the next connection."""
        self._step(step, "failed", reason)
        if step == "restore":
            self._step("verify", "skipped", "not part of this restore")
        self.state = "failed"
        self.body = None
        self.error = {"code": "hitl_failed", "step": step, "message": reason, "message_zh": zh(reason)}
        self.notify("hitl_failed", {"step": step, "reason_en": reason, "reason_zh": zh(reason)})

    def _tick_verify(self, now: float, wall: float) -> None:
        col = self.collector
        assert col is not None
        col.tick(self.driver, now)
        if not col.complete:
            if now > self.deadline:
                self.collector = None
                self._fail_restore("verify", "the parameter table did not arrive in time")
            return
        current = dict(col.values)
        self.collector = None
        self.mismatches, self.volatile = compare(self.backup or {}, current)
        self.state = "restored"
        self.pending_restore = False
        if self.mismatches:
            reason = "parameters differ from the backup"
            self._step("verify", "failed", f"{len(self.mismatches)} {reason}")
            self.steps["verify"]["detail_zh"] = f"{len(self.mismatches)} 项{zh(reason)}"
            self.error = {
                "code": "hitl_restore_mismatch",
                "step": "verify",
                "message": f"{len(self.mismatches)} parameters differ from the backup; the file is kept",
                "message_zh": f"{len(self.mismatches)} 项参数与备份不一致，备份文件已保留",
            }
        else:
            self._step("verify", "done", "every parameter matches the backup")
            self.error = None if self.failed_step is None else self.error
        if self.backup_path is not None:
            data = read_backup(self.backup_path) or self._record(pending=False)
            data.update(restore_pending=False, restored_at=wall, mismatches=self.mismatches)
            try:
                write_json(self.backup_path, data)
            except OSError:
                pass
        self.notify("hitl_restored", {"restored": sorted(self.changed), "mismatches": self.mismatches})


__all__ = [
    "HITL_AIRFRAME",
    "STEP_KEYS",
    "STEP_ZH",
    "TARGETS",
    "VOLATILE",
    "HitlBody",
    "HitlSession",
    "ParamCollector",
    "backup_name",
    "compare",
    "pending_backups",
    "read_backup",
    "same_param",
]
