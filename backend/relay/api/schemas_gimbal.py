"""Gimbal pod (12 §5): the ``vehicle.gimbal`` view, request bodies and response shapes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

LensName = Literal["visible1", "visible2", "ir1", "ir2"]


class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow", json_schema_serialization_defaults_required=True)


class GimbalTracking(_Model):
    state: Literal["off", "searching", "locked"]
    algorithm: Literal["auto", "person", "vehicle", "building"]
    x_deg: float = Field(description="miss distance, right positive")
    y_deg: float = Field(description="miss distance, down positive")
    box_px: list[int] = Field(description="the tracking gate [width, height] in pixels; [0, 0] when off")


class GimbalLaser(_Model):
    on: bool
    range_m: float | None = Field(description="null: off or no valid return")


class GimbalSelftest(_Model):
    done: bool
    gyro_ok: bool
    servo_ok: bool
    image_ok: bool


class GimbalTarget(_Model):
    lat: float
    lon: float
    alt: float


class GimbalFocus(_Model):
    mode: Literal["auto", "manual"]
    position: int | None = Field(description="manual focus value 4090..49152")


class GimbalView(_Model):
    """``vehicle.gimbal``. Angles are the pod's frame angles: ``yaw_deg`` right positive, ``pitch_deg`` up
    positive (looking down is negative), ``roll_deg`` right positive; independent of ``control_frame``."""

    kind: Literal["sim", "dyt"] = Field(description="sim: simulated pod; dyt: a real DYT pod")
    link: Literal["ok", "connecting", "lost"]
    address: str | None = Field(
        description='"10.0.0.5:2000" or the serial device; null for the simulated pod'
    )
    yaw_deg: float
    pitch_deg: float
    roll_deg: float
    yaw_dps: float
    pitch_dps: float
    mode: Literal["follow", "lock", "rate", "angle", "track", "scan", "off"]
    lens: LensName
    pip: LensName | None = Field(description="the lens of the small picture, or null")
    zoom: float = Field(description="magnification of the current lens")
    zoom_range: list[float]
    digital_zoom: bool
    fov_deg: list[float] = Field(description="[horizontal, vertical] field of view now (estimated for DYT)")
    palette: Literal["white_hot", "black_hot", "pseudo"]
    tracking: GimbalTracking
    laser: GimbalLaser
    recording: bool
    photos: int = Field(description="photos taken this session")
    motors: bool
    enhance: bool
    osd: bool
    low_light: bool
    selftest: GimbalSelftest
    target: GimbalTarget | None = Field(description="the target position the pod solved (DYT table 4)")
    focus: GimbalFocus
    debug: dict[str, Any] | None = Field(
        default=None, description="DYT link counters: frames, bad_frames (checksum failures), rx_bytes ..."
    )
    age_s: float | None = Field(description="seconds since the pod's last state; null before the first")


GimbalAction = Literal[
    "rate",
    "stop",
    "angle",
    "center",
    "follow",
    "lock",
    "motors",
    "zoom",
    "zoom_rate",
    "digital_zoom",
    "lens",
    "palette",
    "photo",
    "record",
    "track",
    "track_stop",
    "laser",
    "focus",
    "enhance",
    "osd",
    "low_light",
    "scan",
    "scan_stop",
    "geo_track",
    "look_at",
]


class GimbalBody(_Body):
    """``{"action": ..., ...}``. Each action reads its own parameters (12 §5.2); the others are ignored."""

    action: GimbalAction
    yaw_dps: float | None = Field(default=None, description="rate: azimuth rate, +-100 deg/s, right positive")
    pitch_dps: float | None = Field(default=None, description="rate: pitch rate, +-100 deg/s, up positive")
    yaw_deg: float | None = Field(default=None, description="angle, scan: azimuth, +-180 degrees")
    pitch_deg: float | None = Field(default=None, description="angle, scan: pitch, -120..+90 degrees")
    space: Literal["frame", "earth"] | None = Field(
        default=None,
        description="angle: frame angles of the pod (default), or earth: azimuth from north and elevation",
    )
    on: bool | None = None
    level: float | None = Field(default=None, description="zoom: magnification of the current lens")
    rate: float | None = Field(default=None, description="zoom_rate: -100..100, 0 stops")
    lens: LensName | None = None
    pip: LensName | None = Field(default=None, description="lens: the small picture, null cancels it")
    palette: Literal["white_hot", "black_hot", "pseudo"] | None = None
    x: float | None = Field(
        default=None, description="track: picture pixel, centre 0, right positive; look_at: local x"
    )
    y: float | None = Field(
        default=None, description="track: picture pixel, centre 0, down positive; look_at: local y"
    )
    z: float | None = Field(default=None, description="look_at: local z")
    frame: Literal["flu", "frd"] | None = Field(default=None, description="look_at: the frame of x, y, z")
    algorithm: Literal["auto", "person", "vehicle", "building"] | None = None
    mode: str | None = Field(default=None, description="focus: auto | manual; geo_track: center | exit")
    position: int | None = Field(default=None, description="focus manual: 4090..49152")
    speed_dps: float | None = Field(default=None, description="scan: sweep speed, 0.2..51 deg/s")
    lat: float | None = None
    lon: float | None = None
    alt: float | None = Field(default=None, description="geo_track: altitude above sea level, metres")

    def params(self) -> dict[str, Any]:
        return self.model_dump(exclude={"action"}, exclude_none=True)


class GimbalAttachBody(_Body):
    transport: Literal["tcp", "serial", "sim"]
    host: str | None = Field(default=None, description="tcp: the pod's address")
    port: int = Field(default=2000, ge=1, le=65535, description="tcp: the pod's network control port")
    device: str | None = Field(default=None, description="serial: /dev/ttyUSB0, COM5")
    baud: int = Field(default=115200, ge=1200, le=3000000)


class GimbalResult(_Model):
    id: str
    gimbal: GimbalView


class GimbalDetached(_Model):
    id: str
    detached: bool


class GimbalCapture(_Model):
    seq: int
    kind: Literal["photo", "record_start", "record_stop"]
    t: float = Field(description="wall clock, epoch seconds")
    nav: dict[str, Any] | None = Field(description="the aircraft's pose, as vehicle.nav (12 §3.3)")
    gimbal: dict[str, float | None] = Field(description="yaw_deg, pitch_deg, roll_deg")
    lens: LensName
    zoom: float | None
    laser_range_m: float | None
    target: GimbalTarget | None


class GimbalCaptures(_Model):
    id: str
    total: int = Field(description="captures this pod has logged (the log keeps the last 500)")
    captures: list[GimbalCapture] = Field(description="newest last")
