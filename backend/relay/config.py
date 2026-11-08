"""Constants, thresholds and persisted settings (03 §3, §6, §11).

Timing constants are fixed by the design and are not user settings. ``Settings``
holds what the operator may change; it is stored as ``settings.json`` in the
data directory.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, ClassVar

VERSION = "0.3.1"
APP_NAME = "ANet Relay"
API_PREFIX = "/api/v1"

# MAVLink identity of Relay. ArduPilot only accepts RC overrides from MAV_GCS_SYSID (default 255).
GCS_SYSID = 255
GCS_COMPID = 190  # MAV_COMP_ID_MISSIONPLANNER

# Main loop cadence (03 §3.1).
TICK_HZ = 200.0
STICK_HZ = 50.0
SNAPSHOT_HZ = 10.0
HOUSEKEEP_HZ = 5.0
HEARTBEAT_PERIOD_S = 1.0
CALL_TIMEOUT_S = 2.0

# Control safety (03 §4.3, §5.3).
STICK_TIMEOUT_S = 0.8
RELEASE_FRAMES = 4
STOP_THROTTLE_S = 0.05
STOP_WINDOW_S = 1.0
MODE_CONFIRM_S = 1.5
ARM_CONFIRM = "ARM"
MOTOR_CONFIRM = "MOTOR"
PILOT_OVERRIDE = 0.15  # stick deflection that cancels a skill started by the operator

# Link (03 §5.1).
LINK_DELAYED_S = 3.0
LINK_LOST_S = 5.0
LINK_OFFLINE_S = 30.0
LOSS_WINDOW_S = 5.0
RATE_WINDOW_S = 2.0
TIMESYNC_PERIOD_S = 2.0
TIMESYNC_STALE_S = 10.0
STREAM_RECHECK_S = 10.0
PARAM_FETCH_DELAY_S = 2.0
POSITION_FRESH_S = 0.5
POSITION_MIN_HZ = 5.0

# Agents (03 §6.1 H9, §4.2).
LEASE_S = 15.0
RATE_CONTROL_PER_S = 60
RATE_OTHER_PER_S = 20
SESSION_CALL_LOG = 50

# Fleet.
SIM_LIMIT = 8
EVENT_RING = 5000
STATUSTEXT_KEEP = 50
STATUSTEXT_SNAPSHOT = 20
HISTORY_SAMPLE_HZ = 5.0
COURSE_RESULT_HOLD_S = 10.0
BRIDGE_AP_NETWORK = "192.168.4.0/24"


@dataclass
class SafetyVolume:
    x: float = 3.0
    y: float = 3.0
    z: float = 2.0


@dataclass
class Settings:
    """Operator settings persisted in settings.json."""

    safety_volume: SafetyVolume = field(default_factory=SafetyVolume)
    cell_warn_v: float = 3.60
    cell_crit_v: float = 3.45
    possess_mode: str = "auto"  # auto | stabilize | althold
    airborne_alert: bool = True
    auto_params: bool = True
    agents_app_control: bool = True  # agent sessions may drive the app UI (04 §3.2)
    retention_days: int = 90
    listen_port: int = 14550
    bridge_port: int = 14555
    last_network_ssid: str = ""
    # LAN arena (05 §7, WP-D): off until the operator joins; the callsign defaults to the host name
    peer_enabled: bool = False
    peer_port: int = 47114
    peer_callsign: str = ""
    arena_auto_respawn: bool = True
    ui: dict[str, Any] = field(default_factory=dict)
    # WP-C simulator flight model (05 §5): the hover-assist default for new simulators and turbulence 0..1.
    sim_hover_assist: bool = True
    sim_turbulence: float = 0.3
    # WP-1 (06 §5): what agent sessions may fly. "sticks": sticks, manual modes, programs and safety
    # actions only; "full": the v0.3 high-level skills as well. Only the operator changes it.
    agent_flight_level: str = "sticks"
    # WP-2a airframe defects (06 §4): the preset new simulators take (ideal | typical | rough), new seed each.
    sim_airframe_default: str = "typical"

    RESTART_FIELDS: ClassVar[tuple[str, ...]] = ("listen_port",)
    READ_ONLY: ClassVar[dict[str, float]] = {"stick_timeout_s": STICK_TIMEOUT_S}

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["stick_timeout_s"] = STICK_TIMEOUT_S
        return data

    def merged(self, patch: dict[str, Any]) -> Settings:
        """Return a validated copy with ``patch`` applied. Raises ValueError naming the bad field."""
        out = copy.deepcopy(self)
        for key, value in patch.items():
            # read-only fields echoed by GET /settings are ignored, not rejected
            echoed = ("restart_fields", "data_dir", "flight_level")  # flight_level: WP-1, read only
            if key in self.READ_ONLY or key in echoed or value is None:
                continue
            if key == "safety_volume":
                if not isinstance(value, dict):
                    raise ValueError("safety_volume")
                sv = out.safety_volume
                for axis in ("x", "y", "z"):
                    if axis in value:
                        number = float(value[axis])
                        if not 1.0 <= number <= (50.0 if axis != "z" else 20.0):
                            raise ValueError(f"safety_volume.{axis}")
                        setattr(sv, axis, number)
                continue
            if key == "ui":
                if not isinstance(value, dict) or len(json.dumps(value)) > 16384:
                    raise ValueError("ui")
                out.ui = dict(value)
                continue
            if not hasattr(out, key) or key.startswith("_"):
                raise ValueError(key)
            current = getattr(out, key)
            if isinstance(current, bool):
                if not isinstance(value, bool):
                    raise ValueError(key)
                setattr(out, key, value)
            elif isinstance(current, int):
                if isinstance(value, bool) or not float(value).is_integer():
                    raise ValueError(key)
                setattr(out, key, int(value))
            elif isinstance(current, float):
                if isinstance(value, bool):
                    raise ValueError(key)
                setattr(out, key, float(value))
            else:
                setattr(out, key, str(value)[:64])
        problem = out.problem()
        if problem:
            raise ValueError(problem)
        return out

    def problem(self) -> str | None:
        if not 3.0 <= self.cell_crit_v < self.cell_warn_v <= 4.2:
            return "cell_warn_v"
        if self.possess_mode not in ("auto", "stabilize", "althold"):
            return "possess_mode"
        if not 0.0 <= self.sim_turbulence <= 1.0:  # WP-C
            return "sim_turbulence"
        if self.agent_flight_level not in ("sticks", "full"):  # WP-1
            return "agent_flight_level"
        if self.sim_airframe_default not in ("ideal", "typical", "rough"):  # WP-2a
            return "sim_airframe_default"
        if not 1 <= self.retention_days <= 3650:
            return "retention_days"
        for name in ("listen_port", "bridge_port"):
            if not 1 <= getattr(self, name) <= 65535:
                return name
        # LAN arena (05 §7.3, WP-D): its own port, never one of the MAVLink ports
        if not 1024 <= self.peer_port <= 65535 or self.peer_port in (self.listen_port, self.bridge_port):
            return "peer_port"
        if len(self.peer_callsign) > 16:
            return "peer_callsign"
        return None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        try:
            return cls().merged({k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (ValueError, TypeError):
            return cls()


log = logging.getLogger("relay")


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def env_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        log.warning("ignoring non-integer %s", name)
        return None


def default_data_dir() -> Path:
    """RELAY_DATA_DIR, else a per-user folder when frozen, else backend/data in development."""
    override = os.environ.get("RELAY_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if getattr(sys, "frozen", False):
        home = Path.home()
        if sys.platform == "darwin":
            return home / "Library" / "Application Support" / APP_NAME
        if sys.platform.startswith("win"):
            return Path(os.environ.get("APPDATA", str(home))) / APP_NAME
        return Path(os.environ.get("XDG_DATA_HOME", str(home / ".local" / "share"))) / APP_NAME
    return Path(__file__).resolve().parent.parent / "data"


def load_settings(data_dir: Path | None) -> Settings:
    settings = Settings()
    if data_dir is not None:
        path = data_dir / "settings.json"
        if path.exists():
            try:
                stored = json.loads(path.read_text("utf-8"))
                if isinstance(stored, dict):
                    settings = Settings.from_dict(stored)
            except (OSError, ValueError) as exc:
                log.warning("settings.json unreadable: %s", exc)
    port = env_int("RELAY_LISTEN_PORT")
    if port is not None and 0 <= port <= 65535:
        settings.listen_port = port
    return settings


def save_settings(data_dir: Path | None, settings: Settings) -> None:
    if data_dir is None:
        return
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "settings.json"
    tmp = path.with_suffix(".json.tmp")
    data = settings.to_dict()
    data.pop("stick_timeout_s", None)
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    tmp.replace(path)
