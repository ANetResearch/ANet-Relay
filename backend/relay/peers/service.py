"""PeerService: the arena's one UDP socket and the table of remote terminals (05 §7.3).

The socket binds the arena port on all interfaces with SO_REUSEADDR (and SO_REUSEPORT where the platform has
it), so several Relays on one machine can share it, joins the IPv4 multicast group 239.255.71.14 on every
usable interface with loopback enabled and TTL 1, and sends each datagram to the group and to every
interface's directed broadcast address (for access points that drop multicast). Receivers drop their own
packets by instance id and duplicates by instance and sequence number.

Threads: the receive thread only reads datagrams, checks size, parses them (``wire.decode``), drops duplicates
and applies the per-source rate limit; it hands structured packets to the fleet thread through a bounded inbox
that ``tick`` drains. Everything else, the peer table included, changes only on the fleet thread. The inbox
replaces ``FleetService.post`` because every posted command marks the fleet snapshot dirty, which would lift
snapshot building to the peer packet rate (05 §7.5: the arena must not raise the snapshot rate).

Nothing here touches a vehicle. ``tick`` returns shots and hits for the arena rules and the joins and
departures for its events; peers live in ``self.peers`` and nowhere else.
"""

from __future__ import annotations

import ipaddress
import logging
import math
import select
import socket
import struct
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import wire

log = logging.getLogger("relay.peers")

GROUP = "239.255.71.14"
DEFAULT_PORT = 47114
MAX_PEERS = 15
RATE_PER_S = 60.0
STALE_S = 2.0
REMOVE_S = 6.0
PING_PERIOD_S = 2.0
STATE_HZ = 20.0
INBOX_LIMIT = 4096
DRAIN_LIMIT = 512
SOURCES_LIMIT = 256
SEEN_SEQS = 256
SEEN_SHOTS = 1024
IFACE_REFRESH_S = 5.0
MAX_IFACES = 8
RECV_POLL_S = 0.1
RTT_SAMPLES = 5

Interfaces = Callable[[], list[tuple[str, str]]]


def default_interfaces() -> list[tuple[str, str]]:
    """(address, directed broadcast) of usable interfaces: IPv4, not loopback or link-local, /16 to /30."""
    from ..netinfo import local_networks

    out = []
    for item in local_networks():
        try:
            address = ipaddress.IPv4Address(item["ip"])
        except ValueError:
            continue
        if address.is_loopback or address.is_link_local or not 16 <= int(item["prefix"]) <= 30:
            continue
        out.append((item["ip"], item["broadcast"]))
    return out[:MAX_IFACES]


@dataclass
class Peer:
    instance: int
    ip: str
    first_seen: float
    last_seen: float
    callsign: str = ""
    seat: int | None = None
    joined: bool = False
    last_state_seq: int | None = None
    state_mono_ms: int = 0
    state_at: float = 0.0
    aircraft: dict[int, wire.Aircraft] = field(default_factory=dict)
    hits: int = 0
    downs: int = 0
    deaths: int = 0
    rtt: deque[float] = field(default_factory=lambda: deque(maxlen=RTT_SAMPLES))

    @property
    def key(self) -> str:
        return f"{self.instance:016x}"

    @property
    def rtt_ms(self) -> float | None:
        if not self.rtt:
            return None
        ordered = sorted(self.rtt)
        return round(ordered[len(ordered) // 2], 1)

    def stale(self, now: float) -> bool:
        return now - self.last_seen > STALE_S


@dataclass(frozen=True, slots=True)
class Received:
    packet: wire.Packet
    ip: str
    at: float  # receiver clock when the datagram arrived


@dataclass
class TickResult:
    """What one drain produced for the arena rules (fleet thread)."""

    joined: list[Peer] = field(default_factory=list)
    left: list[tuple[Peer, str]] = field(default_factory=list)
    # (peer, shot, age_s): age is how long the bullet has flown at receipt, including half the round trip
    shots: list[tuple[Peer, wire.Shot, float]] = field(default_factory=list)
    hits: list[tuple[Peer, wire.Hit]] = field(default_factory=list)
    states: list[Peer] = field(default_factory=list)


class _Bucket:
    __slots__ = ("at", "tokens")

    def __init__(self, now: float) -> None:
        self.tokens = RATE_PER_S
        self.at = now

    def take(self, now: float) -> bool:
        self.tokens = min(RATE_PER_S, self.tokens + (now - self.at) * RATE_PER_S)
        self.at = now
        if self.tokens < 1.0:
            return False
        self.tokens -= 1.0
        return True


def _seq_newer(a: int, b: int) -> bool:
    """a is after b on the 32-bit sequence circle."""
    return 0 < ((a - b) & 0xFFFFFFFF) < 0x80000000


class PeerService:
    def __init__(
        self,
        instance: int,
        port: int = DEFAULT_PORT,
        *,
        clock: Callable[[], float] = time.monotonic,
        interfaces: Interfaces = default_interfaces,
        group: str = GROUP,
    ) -> None:
        if not 0 < instance < 2**64:
            raise ValueError("instance")
        self.instance = instance
        self.port = port
        self.group = group
        self.clock = clock
        self._interfaces_fn = interfaces
        self.interfaces: tuple[tuple[str, str], ...] = ()
        self._joined: set[str] = set()
        self.sock: socket.socket | None = None
        self.error: str | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._inbox: deque[Received] = deque()
        self._sources: OrderedDict[tuple[str, int], _Bucket] = OrderedDict()
        self._seen: dict[int, deque[int]] = {}
        self._seq = 0
        self._t0 = clock()
        self.peers: dict[int, Peer] = {}
        self._shots_seen: deque[tuple[int, int]] = deque(maxlen=SEEN_SHOTS)
        self._shots_set: set[tuple[int, int]] = set()
        self._pongs: list[tuple[int, int, float]] = []  # (instance, their mono_ms, our receipt time)
        self._next_ping = 0.0
        self.counters: dict[str, int] = {
            "rx": 0,
            "tx": 0,
            "tx_error": 0,
            "malformed": 0,
            "oversize": 0,
            "own": 0,
            "duplicate": 0,
            "rate_limited": 0,
            "peer_cap": 0,
            "inbox_full": 0,
        }

    # ------------------------------------------------------------------ lifecycle

    @property
    def ok(self) -> bool:
        return self.sock is not None

    def mono_ms(self, now: float | None = None) -> int:
        now = self.clock() if now is None else now
        return int((now - self._t0) * 1000.0) & 0xFFFFFFFF

    def open(self) -> bool:
        """Bind the arena port and start the receive thread. False (and ``error``) when it is unusable."""
        if self.sock is not None:
            return True
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            sock.bind(("", self.port))
            sock.setblocking(False)
        except OSError as exc:
            self.error = f"UDP {self.port}: {exc.strerror or exc}"
            return False
        self.sock = sock
        self.error = None
        self._joined = set()
        self._stop.clear()
        self._thread = threading.Thread(target=self._recv_loop, name="relay-peers", daemon=True)
        self._thread.start()
        return True

    def close(self, bye: bool = True) -> None:
        sock = self.sock
        if sock is None:
            return
        if bye:
            self.send(wire.encode_bye(self._next_seq(), self.instance, self.mono_ms()))
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        self._thread = None
        self.sock = None
        try:
            sock.close()
        except OSError:
            pass
        self.peers.clear()
        self._inbox.clear()

    # ------------------------------------------------------------------ sending (fleet thread)

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        return self._seq

    def send(self, data: bytes) -> None:
        """One datagram to the group on every interface and to every interface's directed broadcast."""
        sock = self.sock
        if sock is None or len(data) > wire.MAX_PACKET:
            return
        targets: list[tuple[str | None, tuple[str, int]]] = []
        ifaces = self.interfaces
        if ifaces:
            for ip, bcast in ifaces:
                targets.append((ip, (self.group, self.port)))
                if bcast and bcast != ip:
                    targets.append((None, (bcast, self.port)))
        else:
            targets.append((None, (self.group, self.port)))
        for iface, addr in dict.fromkeys(targets):
            try:
                if iface is not None:
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface))
                sock.sendto(data, addr)
                self.counters["tx"] += 1
            except OSError:
                self.counters["tx_error"] += 1

    def send_state(self, state: wire.State) -> None:
        self.send(wire.encode_state(self._next_seq(), self.instance, self.mono_ms(), state))

    def send_shot(self, shot: wire.Shot) -> None:
        self.send(wire.encode_shot(self._next_seq(), self.instance, self.mono_ms(), shot))

    def send_hit(self, hit: wire.Hit) -> None:
        self.send(wire.encode_hit(self._next_seq(), self.instance, self.mono_ms(), hit))

    def median_rtt(self) -> int | None:
        values = [p.rtt_ms for p in self.peers.values() if p.rtt_ms is not None]
        if not values:
            return None
        values.sort()
        return int(values[len(values) // 2])

    # ------------------------------------------------------------------ receiving (receive thread)

    def _refresh_interfaces(self) -> None:
        try:
            found = tuple(self._interfaces_fn())
        except Exception:  # interface probing must never stop the receive loop
            found = self.interfaces
        self.interfaces = found
        sock = self.sock
        if sock is None:
            return
        wanted = {ip for ip, _ in found} or {"0.0.0.0"}
        for ip in wanted - self._joined:
            mreq = struct.pack("4s4s", socket.inet_aton(self.group), socket.inet_aton(ip))
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            except OSError:
                pass  # already a member through another address, or the interface went away
            self._joined.add(ip)

    def _recv_loop(self) -> None:
        refresh_at = 0.0
        while not self._stop.is_set():
            sock = self.sock
            if sock is None:
                return
            mono = time.monotonic()
            if mono >= refresh_at:
                refresh_at = mono + IFACE_REFRESH_S
                self._refresh_interfaces()
            try:
                ready, _, _ = select.select([sock], [], [], RECV_POLL_S)
            except (OSError, ValueError):
                return
            if not ready:
                continue
            for _ in range(256):
                try:
                    data, addr = sock.recvfrom(2048)
                except (BlockingIOError, InterruptedError):
                    break
                except OSError:
                    break
                self.receive(data, addr[0], self.clock())

    def receive(self, data: bytes, ip: str, at: float) -> None:
        """Size, parse, self and duplicate drop, rate limit; then queue for the fleet thread. Thread-safe."""
        self.counters["rx"] += 1
        if len(data) > wire.MAX_PACKET:
            self.counters["oversize"] += 1
            return
        try:
            packet = wire.decode(data)
        except (wire.WireError, struct.error, ValueError):
            self.counters["malformed"] += 1
            return
        head = packet.header
        if head.instance == self.instance:
            self.counters["own"] += 1
            return
        seen = self._seen.get(head.instance)
        if seen is None:
            if len(self._seen) >= SOURCES_LIMIT:
                self._seen.pop(next(iter(self._seen)))
            seen = self._seen[head.instance] = deque(maxlen=SEEN_SEQS)
        if head.seq in seen:
            self.counters["duplicate"] += 1
            return
        seen.append(head.seq)
        key = (ip, head.instance)
        bucket = self._sources.get(key)
        if bucket is None:
            bucket = self._sources[key] = _Bucket(at)
            if len(self._sources) > SOURCES_LIMIT:
                self._sources.popitem(last=False)
        else:
            self._sources.move_to_end(key)
        if not bucket.take(at):
            self.counters["rate_limited"] += 1
            return
        if len(self._inbox) >= INBOX_LIMIT:
            self.counters["inbox_full"] += 1
            return
        self._inbox.append(Received(packet, ip, at))

    # ------------------------------------------------------------------ fleet thread

    def tick(self, now: float) -> TickResult:
        """Drain the inbox into the peer table, answer pings, expire peers. Fleet thread only."""
        out = TickResult()
        for _ in range(DRAIN_LIMIT):
            try:
                item = self._inbox.popleft()
            except IndexError:
                break
            self._apply(item, now, out)
        for instance, their_ms, at in self._pongs:
            hold = round(max(0.0, now - at) * 1000.0)
            self.send(
                wire.encode_pong(
                    self._next_seq(), self.instance, self.mono_ms(now), wire.Pong(instance, their_ms, hold)
                )
            )
        self._pongs.clear()
        if now >= self._next_ping and self.sock is not None:
            self._next_ping = now + PING_PERIOD_S
            self.send(wire.encode_ping(self._next_seq(), self.instance, self.mono_ms(now)))
        for instance, peer in list(self.peers.items()):
            if now - peer.last_seen > REMOVE_S:
                del self.peers[instance]
                if peer.joined:
                    out.left.append((peer, "timeout"))
        return out

    def _apply(self, item: Received, now: float, out: TickResult) -> None:
        packet = item.packet
        head = packet.header
        peer = self.peers.get(head.instance)
        if packet.type == wire.BYE:
            if peer is not None:
                del self.peers[head.instance]
                if peer.joined:
                    out.left.append((peer, "bye"))
            return
        if peer is None:
            if len(self.peers) >= MAX_PEERS:
                self.counters["peer_cap"] += 1
                return
            peer = Peer(head.instance, item.ip, first_seen=item.at, last_seen=item.at)
            self.peers[head.instance] = peer
        peer.last_seen = max(peer.last_seen, item.at)
        body = packet.body
        if packet.type == wire.STATE and isinstance(body, wire.State):
            if peer.last_state_seq is not None and not _seq_newer(head.seq, peer.last_state_seq):
                return  # out of order: a newer state is already applied
            peer.last_state_seq = head.seq
            peer.callsign = body.callsign or "Relay"
            peer.seat = body.seat
            peer.state_mono_ms = head.mono_ms
            peer.state_at = item.at
            peer.aircraft = {a.index: a for a in body.aircraft}
            peer.hits, peer.downs, peer.deaths = body.hits, body.downs, body.deaths
            if not peer.joined:
                peer.joined = True
                out.joined.append(peer)
            out.states.append(peer)
            for shot in body.shots:
                self._shot(peer, shot, head.mono_ms, item.at, now, out)
        elif packet.type == wire.SHOT and isinstance(body, wire.Shot):
            self._shot(peer, body, head.mono_ms, item.at, now, out)
        elif packet.type == wire.HIT and isinstance(body, wire.Hit):
            out.hits.append((peer, body))
        elif packet.type == wire.PING:
            self._pongs.append((head.instance, head.mono_ms, item.at))
        elif packet.type == wire.PONG and isinstance(body, wire.Pong):
            if body.to_instance != self.instance:
                return
            rtt = ((self.mono_ms(item.at) - body.echo_ms) & 0xFFFFFFFF) - body.hold_ms
            if 0 <= rtt < 10_000:
                peer.rtt.append(float(rtt))

    def _shot(
        self, peer: Peer, shot: wire.Shot, sent_ms: int, at: float, now: float, out: TickResult
    ) -> None:
        key = (peer.instance, shot.bullet)
        if key in self._shots_set:
            return
        if len(self._shots_seen) == self._shots_seen.maxlen:
            self._shots_set.discard(self._shots_seen[0])
        self._shots_seen.append(key)
        self._shots_set.add(key)
        flown_ms = (sent_ms - shot.t0_ms) & 0xFFFFFFFF
        if flown_ms >= 0x80000000:
            flown_ms = 0
        half = (peer.rtt_ms or 0.0) / 2000.0
        age = flown_ms / 1000.0 + half + max(0.0, now - at)
        if math.isfinite(age):
            out.shots.append((peer, shot, age))

    def view(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "port": self.port,
            "error": self.error,
            "instance": f"{self.instance:016x}",
            "peers": len(self.peers),
            "interfaces": [ip for ip, _ in self.interfaces],
            "counters": dict(self.counters),
        }
