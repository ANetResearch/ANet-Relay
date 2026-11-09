"""USB / UART MAVLink link to one flight controller (11 §7.1, §10).

A ``SerialLink`` owns one serial port. It never blocks the fleet loop: ``poll`` reads what the port has
buffered (``timeout=0``), ``write`` hands frames to the driver with a short write timeout. A read or write
error (the cable was pulled, the board rebooted and its USB device went away) closes the port and the link
reopens the same device about once a second; a USB device that comes back under another name (Linux
``ttyACM1`` after ``ttyACM0``) is found again by its USB serial number. Meanwhile the aircraft's heartbeat
ages and its link goes delayed, lost and offline like a UDP aircraft's; ``generation`` counts the opens so
a caller can tell that the device went away and came back (the hardware-in-the-loop reboot, ``twin.hitl``).

``list_ports`` describes the ports pyserial finds and marks the USB interfaces of known flight controllers
(``autopilot``) and plain USB-serial adapters (``usb-serial``: telemetry radios, FTDI cables). ``probe``
opens a port and waits for an autopilot HEARTBEAT, trying 115200, 57600 and 921600 baud for ``"auto"``
(``PROBE_S`` each); a USB CDC flight controller ignores the rate and answers at the first.

pyserial is imported lazily: without it the ports list is empty (``available: false``) and connecting
fails with ``serial_unavailable`` (``cause: "pyserial_missing"``). Tests pass their own ``opener``.
"""

from __future__ import annotations

import errno
import re
import time
from collections.abc import Callable
from typing import Any, ClassVar

from ..config import GCS_COMPID, GCS_SYSID
from .mavlink import Encoder, M, Parser

AUTO_RATES = (115200, 57600, 921600)
CDC_RATE = 115200  # the rate a USB CDC port is opened at; the device ignores it
BAUD_RATES = (9600, 19200, 38400, 57600, 115200, 230400, 460800, 500000, 921600, 1500000)
PROBE_S = 2.0
REOPEN_S = 1.0
WRITE_TIMEOUT_S = 0.05
READ_CHUNK = 65536

# USB vendor (and product) ids of flight controller interfaces. A vendor alone covers every product.
AUTOPILOT_USB: dict[int, tuple[int, ...] | None] = {
    0x26AC: None,  # 3D Robotics / PX4 (Pixhawk, Pixracer with PX4 bootloader ids)
    0x2DAE: None,  # CubePilot (Hex)
    0x3162: None,  # Holybro
    0x1209: None,  # pid.codes: ArduPilot ChibiOS 0x5740 / 0x5741, mRo boards
    0x0483: (0x5740,),  # STMicroelectronics virtual COM port (ChibiOS / Betaflight-class boards)
}
ADAPTER_USB = {0x10C4: "CP210x", 0x0403: "FTDI", 0x1A86: "CH340"}
LEGACY_UART = re.compile(r"^/dev/ttyS\d+$")
AUTOPILOT_TEXT = re.compile(
    r"px4|pixhawk|pixracer|ardupilot|chibios|cubeorange|cube orange|cubeblack|fmu|holybro|durandal|"
    r"kakute|matek|mro|\bcube\b",
    re.IGNORECASE,
)


class SerialUnavailable(Exception):
    """The port cannot be used. ``cause``: busy | not_found | permission | no_heartbeat | pyserial_missing |
    invalid | io."""

    TEXT_ZH: ClassVar[dict[str, str]] = {
        "busy": "串口被其他程序占用",
        "not_found": "找不到该串口",
        "permission": "没有该串口的访问权限",
        "no_heartbeat": "未收到飞控心跳",
        "pyserial_missing": "未安装 pyserial，串口不可用",
        "invalid": "串口参数无效",
        "io": "串口读写失败",
    }

    def __init__(self, cause: str, reason: str) -> None:
        super().__init__(reason)
        self.cause = cause
        self.reason = reason

    @property
    def reason_zh(self) -> str:
        return self.TEXT_ZH.get(self.cause, self.reason)


def _pyserial() -> Any:
    try:
        import serial  # type: ignore[import-not-found]
    except ImportError:
        raise SerialUnavailable("pyserial_missing", "pyserial is not installed") from None
    return serial


def available() -> bool:
    try:
        _pyserial()
    except SerialUnavailable:
        return False
    return True


def classify(vid: int | None, pid: int | None, text: str) -> str:
    """``autopilot``, ``usb-serial`` (a plain adapter) or ``other``."""
    if vid is not None:
        products = AUTOPILOT_USB.get(vid, ())
        if products is None or (pid is not None and pid in (products or ())):
            return "autopilot"
        if vid in ADAPTER_USB:
            return "usb-serial"
    if text and AUTOPILOT_TEXT.search(text):
        return "autopilot"
    return "other"


def _comports() -> list[Any]:
    """pyserial's port list (a test seam)."""
    _pyserial()
    from serial.tools import list_ports  # type: ignore[import-not-found]

    return list(list_ports.comports())


def port_view(info: Any) -> dict[str, Any]:
    vid = getattr(info, "vid", None)
    pid = getattr(info, "pid", None)
    description = getattr(info, "description", None)
    manufacturer = getattr(info, "manufacturer", None)
    product = getattr(info, "product", None)
    text = " ".join(str(x) for x in (description, manufacturer, product) if x and x != "n/a")
    kind = classify(vid, pid, text)
    return {
        "device": str(info.device),
        "name": getattr(info, "name", None) or str(info.device).rsplit("/", 1)[-1],
        "description": None if description in (None, "n/a") else str(description),
        "manufacturer": manufacturer,
        "vid": vid,
        "pid": pid,
        "serial_number": getattr(info, "serial_number", None),
        "autopilot": kind == "autopilot",
        "kind": kind,
        "adapter": ADAPTER_USB.get(vid) if kind == "usb-serial" and vid is not None else None,
    }


def _placeholder(port: dict[str, Any]) -> bool:
    """Linux lists every legacy PC UART (/dev/ttyS0-31) whether or not one is fitted; without a description
    or USB ids it is not a port anyone connects a flight controller to."""
    return bool(LEGACY_UART.match(port["device"])) and port["vid"] is None and not port["description"]


def list_ports() -> dict[str, Any]:
    """``GET /serial/ports``: flight controllers first, then adapters, then the rest, by device name."""
    try:
        infos = _comports()
    except SerialUnavailable as exc:
        return {"available": False, "reason": exc.reason, "reason_zh": exc.reason_zh, "ports": []}
    except OSError as exc:
        return {"available": True, "reason": str(exc), "reason_zh": "无法列出串口", "ports": []}
    rank = {"autopilot": 0, "usb-serial": 1, "other": 2}
    views = [port_view(i) for i in infos]
    ports = sorted((p for p in views if not _placeholder(p)), key=lambda p: (rank[p["kind"]], p["device"]))
    return {"available": True, "reason": None, "reason_zh": None, "ports": ports}


def find_port(device: str) -> dict[str, Any] | None:
    try:
        infos = _comports()
    except (SerialUnavailable, OSError):
        return None
    for info in infos:
        if str(info.device) == device:
            return port_view(info)
    return None


def _cause(exc: BaseException) -> str:
    code = getattr(exc, "errno", None)
    text = str(exc).lower()
    if code in (errno.EBUSY, errno.EAGAIN) or "busy" in text or "exclusively lock" in text:
        return "busy"
    if code in (errno.EACCES, errno.EPERM) or "permission" in text or "access is denied" in text:
        return "permission"
    if code in (errno.ENOENT, errno.ENODEV, errno.ENXIO) or "no such file" in text or "cannot find" in text:
        return "not_found"
    return "io"


def open_port(device: str, baud: int) -> Any:
    """Open ``device`` non-blocking with pyserial. Raises SerialUnavailable."""
    serial = _pyserial()
    try:
        return serial.serial_for_url(
            device, baudrate=baud, timeout=0, write_timeout=WRITE_TIMEOUT_S, exclusive=True
        )
    except ValueError as exc:
        raise SerialUnavailable("invalid", f"{device}: {exc}") from None
    except OSError as exc:  # serial.SerialException is an OSError
        cause = _cause(exc)
        if cause == "io" and exc.__cause__ is not None:
            cause = _cause(exc.__cause__)
        raise SerialUnavailable(cause, f"{device}: {exc}") from None


Opener = Callable[[str, int], Any]


def _read(port: Any) -> bytes:
    waiting = port.in_waiting
    if not waiting:
        return b""
    return bytes(port.read(min(int(waiting), READ_CHUNK)))


def is_autopilot_heartbeat(msg: Any) -> bool:
    return (
        msg.get_type() == "HEARTBEAT"
        and int(msg.autopilot) != M.MAV_AUTOPILOT_INVALID
        and int(msg.type) not in (M.MAV_TYPE_GCS, M.MAV_TYPE_ONBOARD_CONTROLLER)
    )


def probe_port(port: Any, seconds: float, clock: Callable[[], float] = time.monotonic) -> Any | None:
    """Wait up to ``seconds`` for an autopilot HEARTBEAT on an open port, sending GCS heartbeats twice a
    second (some links only start streaming once they hear a ground station). Returns the heartbeat."""
    parser = Parser()
    encoder = Encoder(GCS_SYSID, GCS_COMPID)
    heartbeat = encoder.encode(
        M.MAVLink_heartbeat_message(M.MAV_TYPE_GCS, M.MAV_AUTOPILOT_INVALID, 0, 0, M.MAV_STATE_ACTIVE, 3)
    )
    end = clock() + seconds
    next_beat = 0.0
    while True:
        now = clock()
        if now >= next_beat:
            next_beat = now + 0.5
            port.write(heartbeat)
        for msg in parser.parse(_read(port)):
            if is_autopilot_heartbeat(msg):
                return msg
        if clock() >= end:
            return None
        time.sleep(0.01)


def probe(
    device: str,
    baud: int | str,
    *,
    opener: Opener = open_port,
    cdc: bool = False,
    seconds: float | None = None,
) -> tuple[Any, int, Any]:
    """Open ``device`` and wait for a heartbeat: (open port, baud, heartbeat). ``baud`` is a rate or
    ``"auto"`` (115200, 57600, 921600 in turn; a USB CDC port (``cdc``) is tried once). Raises
    SerialUnavailable: the open errors at once, ``no_heartbeat`` when no rate answered."""
    window = PROBE_S if seconds is None else seconds
    if baud == "auto":
        rates: tuple[int, ...] = (CDC_RATE,) if cdc else AUTO_RATES
    else:
        rates = (int(baud),)
    for rate in rates:
        port = opener(device, rate)
        try:
            beat = probe_port(port, window)
        except OSError as exc:
            _close(port)
            raise SerialUnavailable(_cause(exc), f"{device}: {exc}") from None
        if beat is not None:
            return port, rate, beat
        _close(port)
    tried = ", ".join(str(r) for r in rates)
    raise SerialUnavailable("no_heartbeat", f"no autopilot heartbeat on {device} at {tried} baud")


def _close(port: Any) -> None:
    try:
        port.close()
    except Exception:
        pass


class SerialLink:
    """One open serial port with reopen-on-error. Used from the fleet thread only."""

    def __init__(
        self,
        device: str,
        baud: int,
        port: Any = None,
        *,
        opener: Opener = open_port,
        usb: dict[str, Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.device = device
        self.baud = baud
        self.opener = opener
        self.usb = usb  # {vid, pid, serial_number} of a USB device, to find it again under another name
        self.clock = clock
        self.port = port
        self.generation = 1 if port is not None else 0
        self.error: str | None = None
        self.next_open = 0.0
        self.rx_bytes = 0
        self.tx_bytes = 0
        self.closed = False  # closed for good (vehicle removed, Relay exiting)

    @property
    def ok(self) -> bool:
        return self.port is not None

    def poll(self) -> bytes:
        port = self.port
        if port is None:
            self.maintain()
            return b""
        try:
            data = _read(port)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            self._broken(exc)
            return b""
        self.rx_bytes += len(data)
        return data

    def write(self, data: bytes) -> bool:
        port = self.port
        if port is None or not data:
            return False
        try:
            port.write(data)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            if type(exc).__name__ == "SerialTimeoutException":
                return False  # the device is not reading fast enough; drop this frame
            self._broken(exc)
            return False
        self.tx_bytes += len(data)
        return True

    def _broken(self, exc: BaseException) -> None:
        port, self.port = self.port, None
        self.error = str(exc) or type(exc).__name__
        if port is not None:
            _close(port)
        self.next_open = self.clock() + REOPEN_S

    def _relocate(self) -> str | None:
        """The device's current name, looked up by its USB serial number."""
        usb = self.usb or {}
        number = usb.get("serial_number")
        if not number:
            return None
        try:
            infos = _comports()
        except (SerialUnavailable, OSError):
            return None
        for info in infos:
            if getattr(info, "serial_number", None) == number and getattr(info, "vid", None) == usb.get(
                "vid"
            ):
                return str(info.device)
        return None

    def maintain(self) -> bool:
        """Reopen a broken port when due. True when it opened."""
        if self.closed or self.port is not None or self.clock() < self.next_open:
            return False
        self.next_open = self.clock() + REOPEN_S
        try:
            self.port = self.opener(self.device, self.baud)
        except SerialUnavailable as exc:
            moved = self._relocate() if exc.cause == "not_found" else None
            if moved is None or moved == self.device:
                self.error = exc.reason
                return False
            try:
                self.port = self.opener(moved, self.baud)
            except SerialUnavailable as again:
                self.error = again.reason
                return False
            self.device = moved
        except OSError as exc:
            self.error = str(exc)
            return False
        self.generation += 1
        self.error = None
        return True

    def close(self) -> None:
        self.closed = True
        port, self.port = self.port, None
        if port is not None:
            _close(port)

    def view(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "baud": self.baud,
            "open": self.port is not None,
            "error": self.error,
            "reopened": max(0, self.generation - 1),
            "usb": self.usb,
        }


__all__ = [
    "AUTO_RATES",
    "SerialLink",
    "SerialUnavailable",
    "available",
    "classify",
    "find_port",
    "list_ports",
    "open_port",
    "probe",
    "probe_port",
]
