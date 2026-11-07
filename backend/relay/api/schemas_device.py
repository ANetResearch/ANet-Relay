"""Request bodies and response shapes of the device-level API (04 §2)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow", json_schema_serialization_defaults_required=True)


# ---------------------------------------------------------------------------- responses


class Ack(_Model):
    result: int
    result_name: str
    progress: int


class CommandResult(_Model):
    command: str
    ack: Ack | None


class CommandParam(_Model):
    index: int
    name: str
    unit: str
    min: float | None = None
    max: float | None = None


class CommandEntry(_Model):
    id: int
    name: str
    label_en: str
    label_zh: str
    params: list[CommandParam]
    danger: Literal["motor", "reboot", "calibrate", "terminate", "arm"] | None
    needs_disarmed: bool
    autopilots: list[str]


class CommandCatalogue(_Model):
    commands: list[CommandEntry]
    confirm_words: dict[str, str]


class MessageFrame(_Model):
    name: str
    fields: dict[str, Any]
    t: float
    hz: float


class ParamValue(_Model):
    name: str
    value: float
    type: str


class ParamsRead(_Model):
    values: dict[str, float]
    missing: list[str]


class MotorsInfo(_Model):
    count: int
    layout: str
    outputs_us: list[int]
    test_supported: bool


class ServoOutput(_Model):
    index: int
    us: int


class FrameInfo(_Model):
    mav_type: int | None
    name: str | None


class DeviceOverview(_Model):
    id: str
    motors: MotorsInfo
    servos: list[ServoOutput]
    rc: list[int]
    frame: FrameInfo
    sensors: list[dict[str, Any]]
    estimator: dict[str, Any] | None
    vibration: dict[str, Any] | None
    home: dict[str, Any] | None
    params: dict[str, int]
    message_rates: dict[str, float]


# ---------------------------------------------------------------------------- request bodies


class ParamsReadBody(_Body):
    names: list[str] = Field(min_length=1, max_length=32)


class CommandBody(_Body):
    command: str | int = Field(description="MAV_CMD_ name or number; only catalogue commands are accepted")
    params: list[float | None] = Field(default_factory=list, max_length=7, description="p1..p7; null is NaN")
    frame: int | None = Field(default=None, description="MAV_FRAME for COMMAND_INT")
    use_int: bool = False
    confirm: str | None = None
    timeout_s: float = Field(default=3.0, gt=0, le=10)


class MotorsTestBody(_Body):
    motor: int | Literal["all"]
    throttle_pct: float = Field(ge=0, le=100, description="0 to 30 on a real aircraft, 0 to 100 simulated")
    seconds: float = Field(default=1.0, ge=0.2, le=5)
    confirm: str | None = None


class ActuatorBody(_Body):
    index: int = Field(ge=1, le=16)
    value: float | None = Field(default=None, ge=-1, le=1)
    pwm: float | None = Field(default=None, ge=800, le=2200)
    seconds: float = Field(default=0.0, ge=0, le=10, description="0 holds until changed")
    confirm: str | None = None


class RcBody(_Body):
    channels: dict[str, int] | None = Field(default=None, description='{"1": 1000..2000, ... "18": ...}')
    axes: dict[str, float] | None = Field(
        default=None, description="roll, pitch, yaw -1..1; throttle 0..1; aux1..aux6 -1..1"
    )


class MessageIntervalBody(_Body):
    message: str
    hz: float = Field(ge=0, le=50, description="0 stops the stream")


class RequestMessageBody(_Body):
    message: str


class ConfirmOnlyBody(_Body):
    confirm: str | None = None


class CalibrateBody(_Body):
    sensor: Literal["gyro", "level", "baro", "accel_simple"]
    confirm: str | None = None


class HomeBody(_Body):
    here: bool = False
    lat: float | None = None
    lon: float | None = None
    alt: float | None = Field(default=None, description="metres above sea level")
