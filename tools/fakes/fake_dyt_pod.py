#!/usr/bin/env python3
"""Fake DYT gimbal pod on a TCP port, for Relay's real-pod path (12 §5.3).

``FakePod`` is the pod's state machine: it turns the control frames of the DYT protocol into state changes
(rates, frame angles, zoom, lens, recording, laser, follow / lock, motors, photo count) and builds the 32-byte
``EE 16`` telemetry from it; ``step(dt)`` advances it. ``FakeDytServer`` wraps it in a TCP server that speaks
the network wrapper (``EB 90 N <data> <checksum>`` in, plain frames out), streams the telemetry at 60 Hz
(``--hz``) and keeps what it received (``received`` control frames, ``attitudes`` carrier frames). The codec
is Relay's own ``relay.gimbal.dyt``.

    python tools/fakes/fake_dyt_pod.py --port 2000 [--host 127.0.0.1] [--hz 60]

Hooks for tests: ``pause()`` / ``resume()`` stop and restart the stream while keeping the connection,
``drop()`` closes the client, ``garbage(n)`` injects bytes that are no frame.
"""

from __future__ import annotations

import argparse
import math
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

try:
    from relay.gimbal import dyt
    from relay.gimbal.lens import LENSES, zoom_range
except ImportError:  # running from a checkout without the backend installed
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    from relay.gimbal import dyt
    from relay.gimbal.lens import LENSES, zoom_range

Code = dyt.Code
TURN_DPS = 60.0  # how fast a commanded frame angle is reached
ZOOM_SLEW = 5.0  # x per second
LASER_RANGE_M = 123.4


class FakePod:
    def __init__(self) -> None:
        self.t = dyt.Telemetry(pitch_deg=0.0)
        self.rate = (0.0, 0.0)  # commanded deg/s
        self.goto: tuple[float, float] | None = None
        self.zoom_to: float | None = None
        self.zoom_rate = 0
        self.zooms = {name: 1.0 for name in LENSES}
        self._zoom = 1.0  # unrounded; the telemetry carries 0.1x steps
        self.photos = 0
        self.geo: tuple[float, float, float] | None = None
        self.replies: list[bytes] = []  # status frames to send back
        self.t.motors = True

    def apply(self, c: dyt.Command) -> None:
        t, code = self.t, c.code
        if code == Code.RATE:
            self.rate = (c.x / 10.0, c.y / 10.0)
            self.goto = None
        elif code in (Code.FRAME_ANGLE, Code.EARTH_ANGLE):
            self.goto = (c.x / 100.0, c.y / 100.0)
            self.rate = (0.0, 0.0)
        elif code == Code.CENTER:
            self.goto, self.rate = (0.0, 0.0), (0.0, 0.0)
        elif code in (Code.VISIBLE1, Code.VISIBLE2, Code.IR1, Code.IR2):
            self.zooms[t.big] = t.zoom
            t.big = LENSES[code - Code.VISIBLE1]
            t.small = LENSES[c.p3 - 1] if 1 <= c.p3 <= 4 else t.big
            t.zoom = self.zooms[t.big]
            self._zoom = t.zoom
            self.zoom_to, self.zoom_rate = None, 0
        elif code == Code.ZOOM_TO:
            lo, hi = zoom_range(t.big)
            self.zoom_to, self.zoom_rate = min(max(c.x / 10.0, lo), hi), 0
        elif code == Code.ZOOM_RATE:
            self.zoom_rate, self.zoom_to = c.rate, None
        elif code == Code.RECORD_ON:
            t.recording = True
        elif code == Code.RECORD_OFF:
            t.recording = False
        elif code == Code.LASER_ON:
            t.laser_on = True
        elif code == Code.LASER_OFF:
            t.laser_on = False
        elif code == Code.MOTORS_ON:
            t.motors = True
        elif code == Code.MOTORS_OFF:
            t.motors = False
            self.rate, self.goto = (0.0, 0.0), None
        elif code == Code.FOLLOW_ON:
            t.follow, t.lock = True, False
        elif code == Code.FOLLOW_OFF:
            t.follow = False
        elif code == Code.LOCK_ON:
            t.lock, t.follow = True, False
        elif code == Code.LOCK_OFF:
            t.lock = False
        elif code == Code.ENHANCE_ON:
            t.enhance = True
        elif code == Code.ENHANCE_OFF:
            t.enhance = False
        elif code == Code.PHOTO:
            self.photos += 1
        elif code == Code.TRACK_POINT:
            t.locked = True
            t.gate_w_px = t.gate_h_px = 96
            t.miss_x_deg, t.miss_y_deg = 0.05 * (c.x / 64), 0.05 * (c.y / 64)
        elif code == Code.TRACK_STOP:
            t.locked = False
            t.gate_w_px = t.gate_h_px = 0
            t.miss_x_deg = t.miss_y_deg = 0.0
        elif code in (Code.ALGO_AUTO, Code.ALGO_PERSON, Code.ALGO_VEHICLE, Code.ALGO_BUILDING):
            t.track_algorithm = dyt.ALGORITHM_NAMES[code - Code.ALGO_AUTO]
        elif code == Code.GEO and c.raw:
            self.replies.append(dyt.encode_status(Code.GEO, bytes((c.raw[3], 0))))  # state, 0 = success

    def step(self, dt: float) -> None:
        t = self.t
        if t.motors:
            yaw_dps, pitch_dps = self.rate
            if self.goto is not None:
                gy = max(-TURN_DPS, min(TURN_DPS, (self.goto[0] - t.yaw_deg) / max(dt, 1e-3)))
                gp = max(-TURN_DPS, min(TURN_DPS, (self.goto[1] - t.pitch_deg) / max(dt, 1e-3)))
                yaw_dps, pitch_dps = gy, gp
                if abs(self.goto[0] - t.yaw_deg) < 1e-6 and abs(self.goto[1] - t.pitch_deg) < 1e-6:
                    self.goto = None
            t.yaw_dps, t.pitch_dps = yaw_dps, pitch_dps
            t.yaw_deg = max(-180.0, min(180.0, t.yaw_deg + yaw_dps * dt))
            t.pitch_deg = max(-120.0, min(90.0, t.pitch_deg + pitch_dps * dt))
        else:
            t.yaw_dps = t.pitch_dps = 0.0
        lo, hi = zoom_range(t.big)
        z = self._zoom
        if self.zoom_rate:
            z += self.zoom_rate / 100.0 * ZOOM_SLEW * dt
        elif self.zoom_to is not None:
            step = ZOOM_SLEW * dt
            z = self.zoom_to if abs(self.zoom_to - z) <= step else z + math.copysign(step, self.zoom_to - z)
        self._zoom = max(lo, min(hi, z))
        t.zoom = round(self._zoom, 1)
        t.laser_range_m = LASER_RANGE_M if t.laser_on else None

    def telemetry(self) -> bytes:
        return dyt.encode_telemetry(self.t)


class FakeDytServer:
    """One listening socket, one client at a time; every thread ends within ~0.1 s of ``stop()``."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, hz: float = 60.0) -> None:
        self.pod = FakePod()
        self.hz = hz
        self.received: list[dyt.Command] = []
        self.attitudes: list[dyt.CarrierAttitude] = []
        self.sent_frames = 0
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind((host, port))
        self._listener.listen(2)
        self._listener.settimeout(0.05)
        self.port = self._listener.getsockname()[1]
        self.host = host
        self._stop = threading.Event()
        self._streaming = threading.Event()
        self._streaming.set()
        self._drop = threading.Event()
        self._inject = b""
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="fake-dyt-pod", daemon=True)

    def start(self) -> FakeDytServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(2.0)
        self._listener.close()

    def pause(self) -> None:
        self._streaming.clear()

    def resume(self) -> None:
        self._streaming.set()

    def drop(self) -> None:
        self._drop.set()

    def garbage(self, data: bytes) -> None:
        with self._lock:
            self._inject += data

    def __enter__(self) -> FakeDytServer:
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._listener.accept()
            except (TimeoutError, OSError):
                continue
            try:
                self._serve(conn)
            except OSError:
                pass
            finally:
                conn.close()

    def _serve(self, conn: socket.socket) -> None:
        conn.settimeout(0.002)
        wrap, cmds = dyt.WrapParser(), dyt.CommandParser()
        period = 1.0 / self.hz
        last = time.monotonic()
        next_tx = last
        self._drop.clear()
        while not self._stop.is_set() and not self._drop.is_set():
            try:
                data = conn.recv(4096)
                if not data:
                    return
                for block in wrap.feed(data):
                    for item in cmds.feed(block):
                        if isinstance(item, dyt.Command):
                            self.received.append(item)
                            self.pod.apply(item)
                        else:
                            self.attitudes.append(item)
            except TimeoutError:
                pass
            now = time.monotonic()
            if now >= next_tx:
                self.pod.step(min(now - last, 0.1))
                last = now
                next_tx = now + period
                out = b""
                if self._streaming.is_set():
                    out = self.pod.telemetry()
                    self.sent_frames += 1
                    for reply in self.pod.replies:
                        out += reply
                    self.pod.replies.clear()
                with self._lock:
                    out += self._inject
                    self._inject = b""
                if out:
                    conn.settimeout(1.0)
                    conn.sendall(out)
                    conn.settimeout(0.002)


def main() -> None:
    ap = argparse.ArgumentParser(description="Fake DYT gimbal pod (TCP, network wrapper)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--hz", type=float, default=60.0)
    args = ap.parse_args()
    server = FakeDytServer(args.host, args.port, args.hz).start()
    print(f"fake DYT pod listening on {args.host}:{server.port}", flush=True)
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
