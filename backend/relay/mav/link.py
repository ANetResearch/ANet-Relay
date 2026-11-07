"""The single UDP socket shared by every real vehicle (03 §5.1).

All bridges send to 0.0.0.0:<listen_port>; packets are demultiplexed by source
IP in the fleet service. Replies go back to the address the bridge sent from.
Packets from this computer's own addresses are dropped unless
RELAY_ALLOW_LOOPBACK=1 (tests and the fake vehicle run on 127.0.0.x).
"""

from __future__ import annotations

import errno
import ipaddress
import select
import socket

MAX_DATAGRAM = 65535


class UdpLink:
    def __init__(self, port: int, host: str = "0.0.0.0", allow_loopback: bool = False) -> None:
        self.host = host
        self.port = port
        self.allow_loopback = allow_loopback
        self.sock: socket.socket | None = None
        self.error: str | None = None
        self.bound_port: int | None = None
        self.local_ips: set[str] = set()
        self.rx_packets = 0
        self.tx_packets = 0
        self.dropped_local = 0

    @property
    def ok(self) -> bool:
        return self.sock is not None

    def open(self) -> bool:
        if self.sock is not None:
            return True
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((self.host, self.port))
            sock.setblocking(False)
        except OSError as exc:
            sock.close()
            if exc.errno == errno.EADDRINUSE:
                self.error = f"UDP {self.port} is in use by another program"
            else:
                self.error = f"UDP {self.port}: {exc.strerror or exc}"
            return False
        self.sock = sock
        self.bound_port = sock.getsockname()[1]
        self.error = None
        return True

    def close(self) -> None:
        sock, self.sock = self.sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def wait(self, timeout: float) -> None:
        """Block until a datagram is readable or the timeout elapses."""
        sock = self.sock
        if sock is None or timeout <= 0:
            return
        try:
            select.select([sock], [], [], timeout)
        except (OSError, ValueError):
            pass

    def is_local(self, ip: str) -> bool:
        if self.allow_loopback:
            return False
        try:
            if ipaddress.IPv4Address(ip).is_loopback:
                return True
        except ValueError:
            return True
        return ip in self.local_ips

    def _is_self(self, addr: tuple[str, int]) -> bool:
        if addr[1] != self.bound_port:
            return False
        if addr[0] in self.local_ips:
            return True
        try:
            return ipaddress.IPv4Address(addr[0]).is_loopback
        except ValueError:
            return False

    def recv_all(self, limit: int = 1024) -> list[tuple[bytes, tuple[str, int]]]:
        sock = self.sock
        packets: list[tuple[bytes, tuple[str, int]]] = []
        if sock is None:
            return packets
        while len(packets) < limit:
            try:
                data, addr = sock.recvfrom(MAX_DATAGRAM)
            except BlockingIOError:
                break
            except ConnectionResetError:
                continue  # Windows reports ICMP port-unreachable on the next recv
            except OSError:
                break
            if self.is_local(addr[0]) or self._is_self(addr):
                self.dropped_local += 1
                continue
            self.rx_packets += 1
            packets.append((data, (addr[0], addr[1])))
        return packets

    def sendto(self, data: bytes, addr: tuple[str, int]) -> bool:
        sock = self.sock
        if sock is None:
            return False
        try:
            sock.sendto(data, addr)
            self.tx_packets += 1
            return True
        except OSError:
            return False
