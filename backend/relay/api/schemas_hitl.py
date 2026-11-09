"""Serial ports and PX4 hardware in the loop (11 §7, §10): request bodies and response shapes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow", json_schema_serialization_defaults_required=True)


# ---------------------------------------------------------------------------- serial ports (11 §7.1)


class SerialPort(_Model):
    device: str = Field(description="path or name to connect with: /dev/ttyACM0, COM5, /dev/cu.usbmodem01")
    name: str
    description: str | None
    manufacturer: str | None
    vid: int | None = Field(description="USB vendor id")
    pid: int | None = Field(description="USB product id")
    serial_number: str | None = Field(description="USB serial number (PX4 boards report their unique id)")
    autopilot: bool = Field(description="a known flight controller USB interface")
    kind: Literal["autopilot", "usb-serial", "other"] = Field(
        description="usb-serial: a plain adapter (CP210x, FTDI, CH340), e.g. a telemetry radio"
    )
    adapter: str | None = Field(default=None, description="CP210x, FTDI or CH340 for usb-serial")
    vehicle: str | None = Field(default=None, description="the aircraft connected on this port")


class SerialPorts(_Model):
    available: bool = Field(description="false: pyserial is missing, serial links cannot be used")
    reason: str | None
    reason_zh: str | None
    ports: list[SerialPort]


class SerialUsb(_Model):
    vid: int | None
    pid: int | None
    serial_number: str | None


class SerialLinkView(_Model):
    """The snapshot's ``vehicle.serial``: the port of a serial aircraft (null on UDP and simulators)."""

    device: str = Field(description="current device name (a USB board that reappears renamed is followed)")
    baud: int
    open: bool = Field(description="false while the device is away; Relay reopens it once a second")
    error: str | None
    reopened: int = Field(description="times the port was reopened after an error")
    usb: SerialUsb | None


class SerialConnect(_Body):
    device: str = Field(min_length=1, max_length=256)
    baud: int | Literal["auto"] = Field(
        default="auto", description="auto tries 115200, 57600, 921600; USB CDC boards ignore the rate"
    )


# ---------------------------------------------------------------------------- hardware in the loop (11 §7)

HitlState = Literal[
    "idle", "backing_up", "writing", "rebooting", "running", "restoring", "verifying", "restored", "failed"
]


class HitlStep(_Model):
    key: Literal["confirm", "backup", "write", "reboot", "run", "restore", "verify"]
    state: Literal["pending", "active", "done", "failed", "skipped"]
    detail: str
    detail_zh: str


class HitlBackup(_Model):
    file: str = Field(description="file name in <data dir>/hitl-backups")
    path: str | None = None
    count: int = Field(description="parameters saved")
    captured_at: float | None = Field(description="wall clock, UTC epoch seconds")


class HitlChange(_Model):
    from_: float | int = Field(alias="from")
    to: float | int


class HitlMismatch(_Model):
    name: str
    expected: float | int | None = Field(description="backup value; null: not in the backup")
    actual: float | int | None = Field(description="value after the restore; null: not reported")


class HitlError(_Model):
    code: Literal["hitl_failed", "hitl_restore_mismatch"]
    step: str | None = None
    message: str
    message_zh: str


class HitlRun(_Model):
    sensor_hz: float = Field(description="HIL_SENSOR frames sent in the last second (250 when healthy)")
    gps_hz: float = Field(description="HIL_GPS frames sent in the last second (10)")
    actuator_frames: int = Field(description="HIL_ACTUATOR_CONTROLS received from the board")
    steps: int = Field(description="physics steps")
    since: float = Field(description="wall clock of the start, UTC epoch seconds")


class HitlStatus(_Model):
    id: str
    available: bool = Field(description="start is possible now")
    reason: str | None = Field(description="why not (English)")
    reason_zh: str | None
    cause: str | None = Field(
        default=None,
        description="sim | unidentified | ardupilot | other_firmware | not_serial | busy | pending | link | "
        "armed | possession",
    )
    state: HitlState
    steps: list[HitlStep]
    backup: HitlBackup | None
    changed: dict[str, HitlChange] = Field(description="parameters the session changed: {NAME: {from, to}}")
    mismatches: list[HitlMismatch]
    volatile: list[str] = Field(default_factory=list, description="self-changing parameters not compared")
    error: HitlError | None
    run: HitlRun | None
    pending_restore: bool = Field(description="a restore is owed from an earlier session")


class HitlBody(_Body):
    action: Literal["start", "stop"]
    confirm: str | None = Field(default=None, description="start: HITL (propellers removed)")


class HitlPose(_Model):
    x: float = Field(description="m east of the board's local origin (physics truth)")
    y: float
    z: float
    vx: float
    vy: float
    vz: float
    roll: float
    pitch: float
    yaw: float = Field(description="NED heading, radians")
    q: list[float] = Field(description="FRD to NED quaternion w, x, y, z")
    crashed: bool
    on_ground: bool


class HitlFirmware(_Model):
    family: Literal["px4"]
    version: str | None


class VehicleHitl(_Model):
    """The snapshot's ``vehicle.hitl``: null without a session. While ``running`` the vehicle's position,
    attitude and rates are the physics the board is fed."""

    state: HitlState
    engine: Literal["hitl"]
    label_en: str = Field(description="Hardware in the loop PX4 1.16.2")
    label_zh: str = Field(description="硬件在环 PX4 1.16.2")
    firmware: HitlFirmware
    pending_restore: bool
    pose: HitlPose | None


EXPORTED: dict[str, Any] = {
    "SerialPorts": SerialPorts,
    "SerialPort": SerialPort,
    "SerialLinkView": SerialLinkView,
    "HitlStatus": HitlStatus,
    "VehicleHitl": VehicleHitl,
}
