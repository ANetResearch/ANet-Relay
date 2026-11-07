#!/usr/bin/env python3
"""Fake aircraft: one mavesp8266 Wi-Fi bridge plus one flight controller (03 §7.3).

The flight controller is ``relay.sim.SimAutopilot`` (PX4 or ArduPilot flavour) speaking real MAVLink 2
over UDP, exactly as an ESP8266 bridge forwards a Pixracer's serial link. The bridge's web interface
reproduces the mavesp8266 routes ``/``, ``/setup``, ``/getparameters``, ``/setparameters``, ``/reboot``
and ``/status.json``. Bridge settings persist to a JSON file; after a reboot the bridge disappears and,
depending on the new settings, re-appears in Station mode (when the configured network matches) or
falls back to its own hotspot address.

Examples::

    python tools/fakes/fake_vehicle.py --autopilot ardupilot --bind 127.0.0.2:14555 \\
        --gcs 127.0.0.1:14550 --http 127.0.0.2:8080
    python tools/fakes/fake_vehicle.py --autopilot px4 --bind 127.0.0.3:14555 --no-position
    python tools/fakes/fake_vehicle.py --drop-after 20          # stop talking after 20 s

Relay must run with RELAY_ALLOW_LOOPBACK=1 to accept aircraft on 127.0.0.x.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import signal
import socket
import struct
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    from relay.sim.autopilot import SimAutopilot
except ImportError:  # running from a checkout without the backend installed
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    from relay.sim.autopilot import SimAutopilot

VERSION = (1, 2, 2)
TICK_S = 0.005
REBOOT_DELAY_S = 0.1
DEFAULT_BRIDGE = {
    "mode": 0,  # 0 AccessPoint, 1 Station
    "ssid": "anet4d-a01",
    "pwd": "fake-bridge-pass",
    "channel": 11,
    "ssidsta": "",
    "pwdsta": "",
    "ipsta": "0.0.0.0",
    "gatewaysta": "0.0.0.0",
    "subnetsta": "0.0.0.0",
    "hport": 14550,
    "cport": 14555,
    "baud": 921600,
    "debug": 0,
}
HEADER = "<!doctype html><html><head><title>MavLink Bridge</title></head><body>"


def _addr(text: str, default_port: int) -> tuple[str, int]:
    host, _, port = text.rpartition(":")
    if not host:
        return text, default_port
    return host, int(port)


def _pack_ip(text: str) -> int:
    try:
        return struct.unpack("<I", socket.inet_aton(text))[0]
    except OSError:
        return 0


def _pack_text(text: str) -> list[int]:
    raw = text.encode("utf-8")[:16].ljust(16, b"\x00")
    return list(struct.unpack("<4I", raw))


class FakeVehicle:
    def __init__(
        self,
        autopilot: str = "ardupilot",
        bind: tuple[str, int] = ("127.0.0.2", 14555),
        gcs: tuple[str, int] = ("127.0.0.1", 14550),
        http: tuple[str, int] | None = ("127.0.0.2", 8080),
        position: bool = True,
        drop_after: float | None = None,
        drop_for: float | None = None,
        state_file: Path | None = None,
        network: str = "anet4d",
        network_password: str = "fake-bridge-pass",
        sta_bind: str | None = None,
        reboot_s: float = 1.5,
        sta_timeout_s: float = 3.0,
        sysid: int = 1,
        keep_received: int = 4000,
        hover_assist: bool = True,
        turbulence: float = 0.0,
    ) -> None:
        self.ap_host = bind[0]
        self.sta_host = sta_bind or bind[0]
        self.udp_port = bind[1]
        self.gcs = gcs
        self.http_port = http[1] if http else None
        self.network = network
        self.network_password = network_password
        self.reboot_s = reboot_s
        self.sta_timeout_s = sta_timeout_s
        self.drop_after = drop_after
        self.drop_for = drop_for
        # Hover assist off (05 §5) is passed only when asked for, so the default path stays the v0.2 call.
        physical = {} if hover_assist else {"hover_assist": False, "turbulence": turbulence}
        self.autopilot = SimAutopilot(autopilot, sysid=sysid, position=position, name="fake", **physical)
        self.autopilot.keep_received = keep_received
        self.state_file = (
            state_file or Path(tempfile.gettempdir()) / f"anet-relay-fake-{bind[0]}-{bind[1]}.json"
        )
        self.bridge = dict(DEFAULT_BRIDGE)
        if self.state_file.exists():
            try:
                self.bridge.update(json.loads(self.state_file.read_text("utf-8")))
            except (OSError, ValueError):
                pass
        self.host = self.ap_host if self.bridge["mode"] == 0 else self.sta_host
        self.lock = threading.Lock()
        self.sock: socket.socket | None = None
        self.httpd: ThreadingHTTPServer | None = None
        self.learned_gcs: tuple[str, int] | None = None
        self.dropped = False
        self.online = False
        self.started = 0.0
        self.stats = {"rx": 0, "tx": 0}
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.reboots = 0

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> FakeVehicle:
        self.started = time.monotonic()
        self._bring_up(self.host)
        thread = threading.Thread(target=self._loop, name="fake-mavlink", daemon=True)
        thread.start()
        self._threads.append(thread)
        return self

    def stop(self) -> None:
        self._stop.set()
        self._bring_down()
        for thread in self._threads:
            thread.join(timeout=2.0)

    def set_drop(self, dropped: bool) -> None:
        self.dropped = dropped

    def _bring_up(self, host: str) -> None:
        with self.lock:
            self.host = host
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, self.udp_port))
            sock.setblocking(False)
            self.sock = sock
            self.learned_gcs = None
            if self.http_port:
                self.httpd = ThreadingHTTPServer((host, self.http_port), _handler(self))
                self.httpd.daemon_threads = True
                thread = threading.Thread(target=self.httpd.serve_forever, name="fake-http", daemon=True)
                thread.start()
            self.online = True

    def _bring_down(self) -> None:
        with self.lock:
            self.online = False
            if self.sock is not None:
                self.sock.close()
                self.sock = None
            httpd, self.httpd = self.httpd, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()

    def reboot(self) -> None:
        """Restart the bridge ~100 ms later; it re-appears according to its saved settings."""

        def run() -> None:
            time.sleep(REBOOT_DELAY_S)
            self.reboots += 1
            self._bring_down()
            time.sleep(self.reboot_s)
            if self._stop.is_set():
                return
            if self.bridge["mode"] == 1:
                if self.bridge["ssidsta"] == self.network and self.bridge["pwdsta"] == self.network_password:
                    self._bring_up(self.sta_host)
                    return
                time.sleep(self.sta_timeout_s)  # cannot join: fall back to the hotspot
            if not self._stop.is_set():
                self._bring_up(self.ap_host)

        threading.Thread(target=run, name="fake-reboot", daemon=True).start()

    def save(self) -> None:
        try:
            self.state_file.write_text(json.dumps(self.bridge), "utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------------ MAVLink

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            if self.drop_after is not None and now - self.started >= self.drop_after:
                end = None if self.drop_for is None else self.drop_after + self.drop_for
                self.dropped = end is None or now - self.started < end
            with self.lock:
                sock = self.sock
            if sock is not None:
                self._receive(sock, now)
            out = self.autopilot.step(now)
            if sock is not None and out and not self.dropped:
                target = self.learned_gcs or self.gcs
                try:
                    sock.sendto(out, target)
                    self.stats["tx"] += 1
                except OSError:
                    pass
            time.sleep(TICK_S)

    def _receive(self, sock: socket.socket, now: float) -> None:
        while True:
            try:
                data, addr = sock.recvfrom(65535)
            except (BlockingIOError, OSError):
                return
            if self.dropped:
                continue
            self.stats["rx"] += 1
            self.learned_gcs = addr
            self.autopilot.receive(data, now)

    def received(self, mtype: str | None = None, since: float = 0.0) -> list[Any]:
        """Decoded messages the flight controller received (for tests)."""
        return [
            m
            for t, m in list(self.autopilot.received)
            if t >= since and (mtype is None or m.get_type() == mtype)
        ]

    # ------------------------------------------------------------------ bridge pages

    def parameters_table(self) -> list[tuple[str, int]]:
        b = self.bridge
        version = (VERSION[0] << 24) | (VERSION[1] << 16) | VERSION[2]
        rows: list[tuple[str, int]] = [
            ("SW_VER", version),
            ("DEBUG_ENABLED", int(b["debug"])),
            ("WIFI_MODE", int(b["mode"])),
            ("WIFI_CHANNEL", int(b["channel"])),
            ("WIFI_UDP_HPORT", int(b["hport"])),
            ("WIFI_UDP_CPORT", int(b["cport"])),
            ("WIFI_IPADDRESS", _pack_ip(self.host)),
        ]
        for prefix, key in (
            ("WIFI_SSID", "ssid"),
            ("WIFI_PASSWORD", "pwd"),
            ("WIFI_SSIDSTA", "ssidsta"),
            ("WIFI_PWDSTA", "pwdsta"),
        ):
            for i, part in enumerate(_pack_text(b[key]), start=1):
                rows.append((f"{prefix}{i}", part))
        rows += [
            ("WIFI_IPSTA", _pack_ip(b["ipsta"])),
            ("WIFI_GATEWAYSTA", _pack_ip(b["gatewaysta"])),
            ("WIFI_SUBNET_STA", _pack_ip(b["subnetsta"])),
            ("UART_BAUDRATE", int(b["baud"])),
        ]
        return rows

    def page_root(self) -> str:
        v = ".".join(str(x) for x in VERSION)
        return (
            f"{HEADER}Version: {v}<p>\n<ul>\n<li><a href='/getstatus'>Get Status</a>\n"
            "<li><a href='/setup'>Setup</a>\n<li><a href='/getparameters'>Get Parameters</a>\n"
            "<li><a href='/update'>Update Firmware</a>\n<li><a href='/reboot'>Reboot</a>\n</ul></body>"
        )

    def page_parameters(self) -> str:
        rows = "".join(f"<tr><td>{name}</td><td>{value}</td></tr>" for name, value in self.parameters_table())
        head = '<tr><td width="240">Name</td><td>Value</td></tr>'
        return f"{HEADER}<p>Parameters</p><table>{head}{rows}</table></body>"

    def page_setup(self) -> str:
        b = self.bridge
        e = html.escape

        def field(label: str, name: str) -> str:
            return f"{label}:&nbsp;<input type='text' name='{name}' value='{e(str(b[name]))}'><br>"

        ap = " checked" if b["mode"] == 0 else ""
        sta = " checked" if b["mode"] == 1 else ""
        return (
            f"{HEADER}<h1>Setup</h1>\n<form action='/setparameters' method='post'>\n"
            f"WiFi Mode:&nbsp;<input type='radio' name='mode' value='0'{ap}>AccessPoint\n"
            f"<input type='radio' name='mode' value='1'{sta}>Station<br>\n"
            + field("AP SSID", "ssid")
            + field("AP Password (min len 8)", "pwd")
            + field("WiFi Channel", "channel")
            + field("Station SSID", "ssidsta")
            + field("Station Password", "pwdsta")
            + field("Station IP", "ipsta")
            + field("Station Gateway", "gatewaysta")
            + field("Station Subnet", "subnetsta")
            + field("Host Port", "hport")
            + field("Client Port", "cport")
            + field("Baudrate", "baud")
            + "<input type='submit' value='Save'></form></body>"
        )

    def page_status(self) -> str:
        return json.dumps(
            {
                "gcs": {
                    "packets_received": self.stats["rx"],
                    "packets_lost": 0,
                    "packets_sent": self.stats["tx"],
                },
                "vehicle": {
                    "packets_received": self.stats["tx"],
                    "packets_lost": 0,
                    "packets_sent": self.stats["rx"],
                },
            }
        )

    def set_parameters(self, args: dict[str, str]) -> tuple[bool, bool]:
        """Apply /setparameters arguments. Returns (ok, reboot)."""
        b = dict(self.bridge)
        try:
            for key in ("mode", "channel", "hport", "cport", "baud", "debug"):
                if key in args:
                    b[key] = int(args[key])
            for key in ("ssid", "pwd", "ssidsta", "pwdsta"):
                if key in args:
                    if len(args[key]) > 16:
                        return False, False
                    b[key] = args[key]
            for key in ("ipsta", "gatewaysta", "subnetsta"):
                if key in args:
                    socket.inet_aton(args[key])
                    b[key] = args[key]
        except (ValueError, OSError):
            return False, False
        if b["mode"] not in (0, 1):
            return False, False
        self.bridge = b
        self.save()
        return True, "reboot" in args


def _handler(fake: FakeVehicle):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ESP8266WebServer"

        def log_message(self, fmt: str, *args: Any) -> None:
            pass

        def _send(self, status: int, body: str, kind: str = "text/html") -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

        def _args(self) -> dict[str, str]:
            parsed = urllib.parse.urlsplit(self.path)
            args = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                body = self.rfile.read(length).decode("utf-8", "replace")
                args.update(urllib.parse.parse_qsl(body, keep_blank_values=True))
            return args

        def _route(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/":
                self._send(200, fake.page_root())
            elif path == "/setup":
                self._send(200, fake.page_setup())
            elif path == "/getparameters":
                self._send(200, fake.page_parameters())
            elif path == "/status.json":
                self._send(200, fake.page_status(), "application/json")
            elif path == "/setparameters":
                ok, reboot = fake.set_parameters(self._args())
                if not ok:
                    self._send(400, "Bad argument")
                    return
                self._send(200, fake.page_parameters())
                if reboot:
                    fake.reboot()
            elif path == "/reboot":
                self._send(200, f"{HEADER}Rebooting...</body>")
                fake.reboot()
            else:
                self._send(404, "File Not Found\n\n", "text/plain")

        def do_GET(self) -> None:
            self._route()

        def do_POST(self) -> None:
            self._route()

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fake mavesp8266 bridge plus flight controller for ANet Relay"
    )
    parser.add_argument("--autopilot", choices=("px4", "ardupilot"), default="ardupilot")
    parser.add_argument("--bind", default="127.0.0.2:14555", help="bridge MAVLink address (AP mode)")
    parser.add_argument(
        "--sta-bind", default=None, help="address used after joining the network in Station mode"
    )
    parser.add_argument("--gcs", default="127.0.0.1:14550", help="ground station address to send to")
    parser.add_argument("--http", default="127.0.0.2:8080", help="bridge web interface; 'off' disables it")
    parser.add_argument(
        "--no-position", action="store_true", help="no local position estimate (PX4 without flow)"
    )
    parser.add_argument(
        "--no-assist",
        action="store_true",
        help="hover assist off: no barometer or position source, physical flight (05 §5)",
    )
    parser.add_argument("--turbulence", type=float, default=0.3, help="turbulence 0..1 with --no-assist")
    parser.add_argument("--drop-after", type=float, default=None, help="stop all traffic after N seconds")
    parser.add_argument("--drop-for", type=float, default=None, help="resume traffic after this many seconds")
    parser.add_argument("--state-file", type=Path, default=None, help="persisted bridge settings")
    parser.add_argument(
        "--network", default="anet4d", help="Wi-Fi network the bridge can join in Station mode"
    )
    parser.add_argument("--network-password", default="fake-bridge-pass")
    parser.add_argument("--reboot-s", type=float, default=1.5)
    parser.add_argument("--sysid", type=int, default=1)
    args = parser.parse_args(argv)

    bind = _addr(args.bind, 14555)
    http = None if args.http == "off" else _addr(args.http, 80)
    if http is not None and http[0] != bind[0]:
        http = (bind[0], http[1])
    fake = FakeVehicle(
        autopilot=args.autopilot,
        bind=bind,
        gcs=_addr(args.gcs, 14550),
        http=http,
        position=not args.no_position,
        drop_after=args.drop_after,
        drop_for=args.drop_for,
        state_file=args.state_file,
        network=args.network,
        network_password=args.network_password,
        sta_bind=args.sta_bind,
        reboot_s=args.reboot_s,
        sysid=args.sysid,
        keep_received=0,
        hover_assist=not args.no_assist,
        turbulence=min(1.0, max(0.0, args.turbulence)),
    ).start()
    sys.stderr.write(
        f"fake {args.autopilot} on udp {fake.host}:{bind[1]} -> {args.gcs}"
        + (f", http {fake.host}:{http[1]}" if http else "")
        + "\n"
    )
    done = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: done.set())
    done.wait()
    fake.stop()
    return 0


if __name__ == "__main__":
    os.environ.setdefault("MAVLINK20", "1")
    raise SystemExit(main())
