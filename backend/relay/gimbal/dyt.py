"""DYT gimbal pod protocol (12 §5.3): a pure codec, no I/O.

DYT (Shenyang Shangbo Zhixiang) frames, all multi-byte fields little endian, the checksum the low byte of the
sum of every byte before it:

* control frame, controller -> pod, 16 bytes: ``EB 90``, control code, X (S16), Y (S16), parameter 3 (U8),
  zoom rate (S8), 6 reserved bytes, checksum (table 1). ``command`` builds one; the ``Code`` values and the
  helper encoders below cover the whole table. The digital guidance command (0x3a) lays bytes 3-14 out
  differently (``geo_command``).
* carrier attitude frame ``EB 91``, 32 bytes, 1-60 Hz (table 3): the aircraft's attitude, position and time,
  which the pod needs for target solving and digital guidance (``CarrierAttitude``).
* periodic telemetry ``EE 16``, 32 bytes, 60 Hz (table 2): ``Telemetry``. Target position ``EE 18`` (table 4):
  ``TargetPosition``. Single status ``EE 19``, variable length (table 5): ``Status``.
* network control wraps every control or carrier frame as ``EB 90 N <N bytes> <checksum of those bytes>``
  (``wrap``); the pod answers in the plain protocol. A serial port carries the plain protocol both ways.

``Parser`` decodes the pod's stream from arbitrary byte chunks; ``CommandParser`` and ``WrapParser`` do the
same for the controller side (the fake pod in ``tools/fakes``). All of them resynchronise on the sync words
and count frames that fail their checksum (``bad``).

Angle conventions of the pod's frame (kept in ``Telemetry``): azimuth right positive, pitch up positive, roll
right positive; miss distances in degrees. The plain-text field names follow the vendor document.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from .lens import LENSES

CMD_LEN = 16
TELEMETRY_LEN = 32
SYNC_CMD = (0xEB, 0x90)
SYNC_CARRIER = (0xEB, 0x91)
SYNC_TELEMETRY = (0xEE, 0x16)
SYNC_TARGET = (0xEE, 0x18)
SYNC_STATUS = (0xEE, 0x19)
MAX_PENDING = 512  # bytes a parser keeps while waiting for the rest of a frame


class Code(IntEnum):
    """Control codes of table 1 (the ones Relay sends, plus the rest of the table for completeness)."""

    NOP = 0x00
    VISIBLE1 = 0x01
    VISIBLE2 = 0x02
    IR1 = 0x03
    IR2 = 0x04
    ENHANCE_ON = 0x05
    ENHANCE_OFF = 0x06
    DETECT_ON = 0x07
    DETECT_OFF = 0x08
    RECORD_ON = 0x09
    RECORD_OFF = 0x0A
    TRACK_POINT = 0x0D
    TRACK_STOP = 0x0E
    AUTO_LOCK = 0x0F
    ASSIST_TRACK = 0x10
    WHITE_HOT = 0x11
    BLACK_HOT = 0x12
    ALGO_AUTO = 0x13
    ALGO_PERSON = 0x14
    ALGO_VEHICLE = 0x15
    ALGO_BUILDING = 0x16
    RATE = 0x24  # 0x24: gimbal search, azimuth and pitch rates in 0.1 deg/s
    ZOOM_RATE = 0x25
    FRAME_ANGLE = 0x26
    MOTORS_ON = 0x27
    MOTORS_OFF = 0x28
    FOLLOW_OFF = 0x29
    FOLLOW_ON = 0x2A
    CENTER = 0x2B
    DRIFT = 0x2C
    LASER_ON = 0x2D
    LASER_OFF = 0x2E
    LOCK_ON = 0x30
    LOCK_OFF = 0x31
    SCAN = 0x32
    SCAN_STOP = 0x33
    CALIBRATE_GYRO = 0x39
    GEO = 0x3A
    EARTH_ANGLE = 0x3B
    ZERO_ALIGN = 0x3C
    PALETTE_PSEUDO = 0x50
    OSD_ON = 0x51
    OSD_OFF = 0x52
    LOW_LIGHT_OFF = 0x55
    LOW_LIGHT_ON = 0x56
    DIGITAL_ZOOM_ON = 0x58
    DIGITAL_ZOOM_OFF = 0x59
    ZOOM_TO = 0x5A
    PHOTO = 0x5B
    FOCUS_MODE = 0x5C
    FOCUS_POSITION = 0x5D


ALGORITHMS = {"auto": Code.ALGO_AUTO, "person": Code.ALGO_PERSON, "vehicle": Code.ALGO_VEHICLE,
              "building": Code.ALGO_BUILDING}  # fmt: skip
ALGORITHM_NAMES = ("auto", "person", "vehicle", "building")  # telemetry bits 5-4
GEO_EXIT, GEO_CENTER, GEO_POINT, GEO_CALIBRATE = 0x00, 0x01, 0x02, 0x0A
DEFAULT_ZOOM_RATE = 50  # the vendor examples carry this rate byte on "zoom to" and "track"


# ---------------------------------------------------------------------------- checksums and wrapping


def checksum(data: bytes | bytearray) -> int:
    """Low byte of the sum of ``data``."""
    return sum(data) & 0xFF


def _s16(v: float) -> int:
    return max(-32768, min(32767, round(v)))


def command(code: int, x: int = 0, y: int = 0, p3: int = 0, rate: int = 0) -> bytes:
    """A 16-byte control frame (table 1). ``x``/``y`` are S16 (unsigned 16-bit values such as a focus
    position are accepted and wrapped), ``p3`` U8, ``rate`` S8."""
    body = bytearray(15)
    body[0:2] = bytes(SYNC_CMD)
    body[2] = int(code) & 0xFF
    struct.pack_into("<hh", body, 3, _wrap16(x), _wrap16(y))
    body[7] = int(p3) & 0xFF
    struct.pack_into("<b", body, 8, max(-128, min(127, int(rate))))
    return bytes(body) + bytes([checksum(body)])


def _wrap16(v: int) -> int:
    v = int(v)
    if not -32768 <= v <= 65535:
        raise ValueError(f"{v} does not fit in 16 bits")
    return v - 65536 if v > 32767 else v


def wrap(data: bytes) -> bytes:
    """The network wrapper: ``EB 90 N <data> <low byte of the sum of data>``."""
    if not 0 < len(data) <= 255:
        raise ValueError("a wrapped block carries 1-255 bytes")
    return bytes((0xEB, 0x90, len(data))) + bytes(data) + bytes([checksum(data)])


# ---------------------------------------------------------------------------- control frame builders


def rate(yaw_dps: float, pitch_dps: float) -> bytes:
    """0x24: turn at the given rates, degrees per second (azimuth right positive, pitch up positive)."""
    return command(Code.RATE, _s16(yaw_dps * 10), _s16(pitch_dps * 10))


def stop() -> bytes:
    return command(Code.RATE)


def frame_angle(yaw_deg: float, pitch_deg: float) -> bytes:
    """0x26: go to the frame angles (azimuth, pitch), degrees."""
    return command(Code.FRAME_ANGLE, _s16(yaw_deg * 100), _s16(pitch_deg * 100))


def earth_angle(yaw_deg: float, pitch_deg: float) -> bytes:
    """0x3b: point at the space attitude (azimuth from north, elevation), degrees."""
    return command(Code.EARTH_ANGLE, _s16(yaw_deg * 100), _s16(pitch_deg * 100))


def zoom_to(level: float, rate: int = DEFAULT_ZOOM_RATE) -> bytes:
    """0x5a: zoom to ``level`` times (0.1x steps)."""
    return command(Code.ZOOM_TO, _s16(level * 10), 0, 0, rate)


def zoom_rate(rate: int) -> bytes:
    """0x25: zoom in (positive) or out (negative) at ``rate`` (-100..100); 0 stops."""
    return command(Code.ZOOM_RATE, 0, 0, 0, rate)


def lens(name: str, pip: str | None = None) -> bytes:
    """0x01-0x04: show ``name`` as the big picture; ``pip`` the small one (parameter 3, 0 cancels)."""
    big = LENSES.index(name)
    small = 0 if pip is None or pip == name else LENSES.index(pip) + 1
    return command(Code.VISIBLE1 + big, p3=small)


def track_point(x: int, y: int, p3: int = 1, rate: int = DEFAULT_ZOOM_RATE) -> bytes:
    """0x0d: track the picture point (x, y), pixels from the picture centre."""
    return command(Code.TRACK_POINT, x, y, p3, rate)


def scan(yaw_deg: float, pitch_deg: float, speed_dps: float) -> bytes:
    """0x32: azimuth scan; parameter 3 is the speed in 0.2 deg/s steps."""
    return command(Code.SCAN, _s16(yaw_deg * 100), _s16(pitch_deg * 100), round(speed_dps / 0.2))


def geo_command(state: int, lat: float = 0.0, lon: float = 0.0, alt_m: float = 0.0) -> bytes:
    """0x3a digital guidance: state (``GEO_*``) in byte 3, target latitude and longitude (1e-7 degrees,
    int32), altitude (S16, 0.2 m) in bytes 12-13."""
    body = bytearray(15)
    body[0:2] = bytes(SYNC_CMD)
    body[2] = Code.GEO
    body[3] = state & 0xFF
    struct.pack_into("<iih", body, 4, round(lat * 1e7), round(lon * 1e7), _s16(alt_m / 0.2))
    return bytes(body) + bytes([checksum(body)])


def on_off(on: bool, code_on: int, code_off: int) -> bytes:
    return command(code_on if on else code_off)


# ---------------------------------------------------------------------------- carrier attitude frame


@dataclass
class CarrierAttitude:
    """Table 3. Angles in degrees: yaw from north, east positive (-180..180); roll right positive; pitch nose
    up positive. ``utc`` is epoch seconds."""

    roll_deg: float = 0.0
    pitch_deg: float = 0.0
    yaw_deg: float = 0.0
    lat: float = 0.0
    lon: float = 0.0
    alt_m: float = 0.0  # above sea level
    rel_alt_m: float = 0.0  # above the take-off point
    utc: float = 0.0
    airspeed: float = 0.0
    groundspeed: float = 0.0


def _datetime_fields(utc: float) -> tuple[int, int, int, int, int, int, int]:
    t = time.gmtime(utc)
    year = max(0, t.tm_year - 2000)
    return year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec, int((utc % 1.0) * 100)


def _wrap180(deg: float) -> float:
    return (deg + 180.0) % 360.0 - 180.0


def carrier_frame(c: CarrierAttitude) -> bytes:
    """The 32-byte ``EB 91`` frame."""
    body = bytearray(31)
    body[0:2] = bytes(SYNC_CARRIER)
    struct.pack_into(
        "<hhhiihh",
        body,
        2,
        _s16(c.roll_deg * 100),
        _s16(c.pitch_deg * 100),
        _s16(_wrap180(c.yaw_deg) * 100),
        round(c.lat * 1e7),
        round(c.lon * 1e7),
        _s16(c.alt_m / 0.2),
        _s16(c.rel_alt_m / 0.2),
    )
    body[20:27] = bytes(_datetime_fields(c.utc))
    struct.pack_into("<HH", body, 27, min(65535, int(c.airspeed / 0.5)), min(65535, int(c.groundspeed / 0.5)))
    return bytes(body) + bytes([checksum(body)])


# ---------------------------------------------------------------------------- pod -> controller frames


@dataclass
class Telemetry:
    """The periodic ``EE 16`` frame (table 2) in engineering units."""

    track_source: str = "visible1"
    track_algorithm: str = "auto"
    auto_prompt: bool = False
    locked: bool = False
    enhance: bool = False
    recording: bool = False
    roll_control: bool = False
    motors: bool = True
    follow: bool = False
    lock: bool = False
    laser_on: bool = False
    zoom: float = 1.0  # 12 bits, 0.1x
    big: str = "visible1"
    small: str = "visible1"
    miss_x_deg: float = 0.0
    miss_y_deg: float = 0.0
    roll_deg: float = 0.0
    pitch_deg: float = 0.0
    yaw_deg: float = 0.0
    gate_w_px: int = 0
    gate_h_px: int = 0
    roll_dps: float = 0.0
    pitch_dps: float = 0.0
    yaw_dps: float = 0.0
    laser_range_m: float | None = None  # None: invalid (0 on the wire)
    selftest_done: bool = True
    gyro_fail: bool = False
    servo_error: bool = False
    image_error: bool = False


@dataclass
class TargetPosition:
    """The ``EE 18`` frame (table 4): where the pod solved the target to be."""

    lat: float
    lon: float
    alt_m: float
    rel_alt_m: float = 0.0
    utc: tuple[int, int, int, int, int, int, int] = (0, 0, 0, 0, 0, 0, 0)  # year-2000, mo, d, h, mi, s, cs


@dataclass
class Status:
    """The ``EE 19`` single status return (table 5): the control code it answers and its parameters."""

    code: int
    params: bytes = b""

    @property
    def geo_ok(self) -> bool | None:
        """For 0x3a: True when the pod reports success; None for other codes."""
        if self.code == Code.GEO and len(self.params) >= 2:
            return self.params[1] == 0
        return None


def encode_telemetry(t: Telemetry) -> bytes:
    body = bytearray(31)
    body[0:2] = bytes(SYNC_TELEMETRY)
    body[2] = (
        (LENSES.index(t.track_source) << 6)
        | (ALGORITHM_NAMES.index(t.track_algorithm) << 4)
        | (0x08 if t.auto_prompt else 0)
        | (0x04 if t.locked else 0)
    )
    body[3] = (
        (0x80 if t.enhance else 0)
        | (0x20 if t.recording else 0)
        | (0x10 if t.roll_control else 0)
        | (0x08 if t.motors else 0)
        | (0x04 if t.follow else 0)
        | (0x02 if t.lock else 0)
        | (0x01 if t.laser_on else 0)
    )
    zoom = max(0, min(0xFFF, round(t.zoom * 10)))
    body[4] = zoom & 0xFF
    body[5] = (LENSES.index(t.big) << 6) | (LENSES.index(t.small) << 4) | (zoom >> 8)
    struct.pack_into(
        "<hhhhh", body, 6, _s16(t.miss_x_deg / 0.05), _s16(t.miss_y_deg / 0.05),
        _s16(t.roll_deg * 100), _s16(t.pitch_deg * 100), _s16(t.yaw_deg * 100),
    )  # fmt: skip
    body[16] = min(255, t.gate_w_px // 4)
    body[17] = min(255, t.gate_h_px // 4)
    struct.pack_into("<hhh", body, 20, _s16(t.roll_dps * 100), _s16(t.pitch_dps * 100), _s16(t.yaw_dps * 100))
    laser = 0 if t.laser_range_m is None else max(1, min(65535, round(t.laser_range_m * 10)))
    struct.pack_into("<H", body, 26, laser)
    body[28] = (
        (0x80 if t.selftest_done else 0)
        | (0x04 if t.gyro_fail else 0)
        | (0x02 if t.servo_error else 0)
        | (0x01 if t.image_error else 0)
    )
    return bytes(body) + bytes([checksum(body)])


def encode_target(t: TargetPosition) -> bytes:
    body = bytearray(31)
    body[0:2] = bytes(SYNC_TARGET)
    struct.pack_into("<iihh", body, 2, round(t.lat * 1e7), round(t.lon * 1e7),
                     _s16(t.alt_m / 0.2), _s16(t.rel_alt_m / 0.2))  # fmt: skip
    body[14:21] = bytes(t.utc)
    return bytes(body) + bytes([checksum(body)])


def encode_status(code: int, params: bytes = b"") -> bytes:
    body = bytes((0xEE, 0x19, int(code) & 0xFF, len(params))) + bytes(params)
    return body + bytes([checksum(body)])


def decode_telemetry(f: bytes) -> Telemetry:
    s1, s2 = f[2], f[3]
    zoom = f[4] | ((f[5] & 0x0F) << 8)
    mx, my, roll, pitch, yaw = struct.unpack_from("<hhhhh", f, 6)
    rr, pr, yr = struct.unpack_from("<hhh", f, 20)
    (laser,) = struct.unpack_from("<H", f, 26)
    st = f[28]
    return Telemetry(
        track_source=LENSES[s1 >> 6],
        track_algorithm=ALGORITHM_NAMES[(s1 >> 4) & 3],
        auto_prompt=bool(s1 & 0x08),
        locked=bool(s1 & 0x04),
        enhance=bool(s2 & 0x80),
        recording=bool(s2 & 0x20),
        roll_control=bool(s2 & 0x10),
        motors=bool(s2 & 0x08),
        follow=bool(s2 & 0x04),
        lock=bool(s2 & 0x02),
        laser_on=bool(s2 & 0x01),
        zoom=zoom / 10.0,
        big=LENSES[f[5] >> 6],
        small=LENSES[(f[5] >> 4) & 3],
        miss_x_deg=mx * 0.05,
        miss_y_deg=my * 0.05,
        roll_deg=roll / 100.0,
        pitch_deg=pitch / 100.0,
        yaw_deg=yaw / 100.0,
        gate_w_px=f[16] * 4,
        gate_h_px=f[17] * 4,
        roll_dps=rr / 100.0,
        pitch_dps=pr / 100.0,
        yaw_dps=yr / 100.0,
        laser_range_m=None if laser == 0 else laser / 10.0,
        selftest_done=bool(st & 0x80),
        gyro_fail=bool(st & 0x04),
        servo_error=bool(st & 0x02),
        image_error=bool(st & 0x01),
    )


def decode_target(f: bytes) -> TargetPosition:
    lat, lon, alt, rel = struct.unpack_from("<iihh", f, 2)
    return TargetPosition(lat / 1e7, lon / 1e7, alt * 0.2, rel * 0.2, tuple(f[14:21]))  # type: ignore[arg-type]


def decode_carrier(f: bytes) -> CarrierAttitude:
    roll, pitch, yaw, lat, lon, alt, rel = struct.unpack_from("<hhhiihh", f, 2)
    y, mo, d, h, mi, s, cs = f[20:27]
    air, ground = struct.unpack_from("<HH", f, 27)
    try:
        import calendar

        utc = calendar.timegm((2000 + y, mo, d, h, mi, s)) + cs / 100.0
    except (ValueError, OverflowError):
        utc = 0.0
    return CarrierAttitude(roll / 100.0, pitch / 100.0, yaw / 100.0, lat / 1e7, lon / 1e7, alt * 0.2,
                           rel * 0.2, utc, air * 0.5, ground * 0.5)  # fmt: skip


@dataclass
class Command:
    """A decoded control frame (controller side, for the fake pod and for tests)."""

    code: int
    x: int
    y: int
    p3: int
    rate: int
    raw: bytes = field(default=b"", repr=False)

    @property
    def x_u16(self) -> int:
        return self.x & 0xFFFF


# ---------------------------------------------------------------------------- stream parsers


class _Stream:
    """Resynchronising frame splitter. Subclasses give the sync byte, the frame length for a sync word and
    the decoder (None when the checksum fails)."""

    SYNC0 = 0xEE

    def __init__(self) -> None:
        self._buf = bytearray()
        self.frames = 0  # frames accepted
        self.bad = 0  # frames whose checksum failed
        self.skipped = 0  # bytes discarded while looking for a sync word

    def _length(self, buf: bytearray, i: int) -> int | None:
        """Total frame length at ``i`` (``buf[i]`` is the first sync byte, ``buf[i + 1]`` exists); 0 when the
        second byte is no sync; None when more bytes are needed to tell."""
        raise NotImplementedError

    def _decode(self, frame: bytes) -> Any | None:
        raise NotImplementedError

    def feed(self, data: bytes) -> list[Any]:
        buf = self._buf
        buf += data
        out: list[Any] = []
        i = 0
        while i < len(buf):
            if buf[i] != self.SYNC0:
                i += 1
                self.skipped += 1
                continue
            if i + 1 >= len(buf):
                break
            n = self._length(buf, i)
            if n == 0:
                i += 1
                self.skipped += 1
                continue
            if n is None or i + n > len(buf):
                break
            obj = self._decode(bytes(buf[i : i + n]))
            if obj is None:
                self.bad += 1
                i += 1  # the sync word may have been noise: rescan from the next byte
                continue
            out.append(obj)
            self.frames += 1
            i += n
        del buf[:i]
        if len(buf) > MAX_PENDING:  # cannot happen with the lengths above; belt and braces
            del buf[:-MAX_PENDING]
        return out


class Parser(_Stream):
    """The pod's stream (``EE 16`` telemetry, ``EE 18`` target, ``EE 19`` status) to ``Telemetry``,
    ``TargetPosition`` and ``Status`` objects."""

    SYNC0 = 0xEE

    def _length(self, buf: bytearray, i: int) -> int | None:
        second = buf[i + 1]
        if second in (0x16, 0x18):
            return TELEMETRY_LEN
        if second == 0x19:
            if i + 3 >= len(buf):
                return None
            return 5 + buf[i + 3]
        return 0

    def _decode(self, frame: bytes) -> Any | None:
        if checksum(frame[:-1]) != frame[-1]:
            return None
        kind = frame[1]
        if kind == 0x16:
            return decode_telemetry(frame)
        if kind == 0x18:
            return decode_target(frame)
        return Status(frame[2], bytes(frame[4:-1]))


class CommandParser(_Stream):
    """The controller's stream (``EB 90`` control frames, ``EB 91`` carrier attitude) to ``Command`` and
    ``CarrierAttitude`` objects."""

    SYNC0 = 0xEB

    def _length(self, buf: bytearray, i: int) -> int | None:
        second = buf[i + 1]
        if second == 0x90:
            return CMD_LEN
        if second == 0x91:
            return 32
        return 0

    def _decode(self, frame: bytes) -> Any | None:
        if checksum(frame[:-1]) != frame[-1]:
            return None
        if frame[1] == 0x91:
            return decode_carrier(frame)
        x, y = struct.unpack_from("<hh", frame, 3)
        (rate_byte,) = struct.unpack_from("<b", frame, 8)
        return Command(frame[2], x, y, frame[7], rate_byte, frame)


class WrapParser(_Stream):
    """The network wrapper ``EB 90 N <data> <checksum>`` to the bytes of ``data`` (as ``bytes`` objects)."""

    SYNC0 = 0xEB

    def _length(self, buf: bytearray, i: int) -> int | None:
        if buf[i + 1] != 0x90:
            return 0
        if i + 2 >= len(buf):
            return None
        return 4 + buf[i + 2]

    def _decode(self, frame: bytes) -> Any | None:
        data = frame[3:-1]
        if not data or checksum(data) != frame[-1]:
            return None
        return bytes(data)
