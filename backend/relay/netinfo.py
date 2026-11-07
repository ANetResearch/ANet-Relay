"""Local IPv4 interfaces and subnets for the LAN scan and the bridge wizard (03 §9).

Relay cannot read the Wi-Fi SSID portably; the operator types it in the
wizard. Interface discovery uses ``ip -o -4 addr`` on Linux and ``ifconfig``
elsewhere, with a UDP-socket fallback that finds the default-route address.
"""

from __future__ import annotations

import ipaddress
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from typing import Any

from .config import BRIDGE_AP_NETWORK

MIN_SCAN_PREFIX = 22
CACHE_S = 5.0
_AP_NET = ipaddress.IPv4Network(BRIDGE_AP_NETWORK)
_IFCONFIG_INET = re.compile(
    r"\binet (?:addr:)?(\d+\.\d+\.\d+\.\d+)\s+(?:netmask|Mask:)\s*(0x[0-9a-fA-F]+|\d+\.\d+\.\d+\.\d+)"
)
_IP_ADDR = re.compile(r"^\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)")


def interface_record(iface: str, ip: str, prefix: int) -> dict[str, Any]:
    network = ipaddress.IPv4Network(f"{ip}/{prefix}", strict=False)
    address = ipaddress.IPv4Address(ip)
    skipped = None
    if address.is_loopback or address.is_link_local:
        skipped = "loopback_or_link_local"
    elif prefix < MIN_SCAN_PREFIX or prefix > 30:
        skipped = "prefix_out_of_range"
    return {
        "iface": iface,
        "ip": ip,
        "prefix": prefix,
        "network": str(network),
        "broadcast": str(network.broadcast_address),
        "scannable": skipped is None,
        "skipped": skipped,
        "bridge_ap": address in _AP_NET,
    }


def parse_ifconfig(text: str) -> list[dict[str, Any]]:
    """macOS/BSD (hex netmask) and Linux net-tools (dotted netmask) output."""
    iface = None
    found = []
    for line in text.splitlines():
        if line and not line[0].isspace():
            iface = re.split(r"[:\s]", line, maxsplit=1)[0]
        match = _IFCONFIG_INET.search(line)
        if not match or not iface:
            continue
        ip, mask = match.groups()
        if mask.startswith("0x"):
            prefix = bin(int(mask, 16)).count("1")
        else:
            prefix = ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
        found.append(interface_record(iface, ip, prefix))
    return found


def parse_ip_addr(text: str) -> list[dict[str, Any]]:
    """Linux ``ip -o -4 addr show`` output."""
    found = []
    for line in text.splitlines():
        match = _IP_ADDR.match(line.strip())
        if match:
            iface, ip, prefix = match.groups()
            found.append(interface_record(iface, ip, int(prefix)))
    return found


def _run(cmd: list[str]) -> str | None:
    if shutil.which(cmd[0]) is None:
        return None
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=2.0, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def _default_route_ip() -> str | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def _probe() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if sys.platform.startswith("linux"):
        text = _run(["ip", "-o", "-4", "addr", "show"])
        if text:
            found = parse_ip_addr(text)
    if not found:
        text = _run(["ifconfig"])
        if text:
            found = parse_ifconfig(text)
    if not found:
        ip = _default_route_ip()
        if ip:
            found = [interface_record("default", ip, 24)]
    return found


class _Cache:
    lock = threading.Lock()
    at = 0.0
    value: list[dict[str, Any]] = []  # noqa: RUF012


def local_networks(max_age: float = CACHE_S) -> list[dict[str, Any]]:
    with _Cache.lock:
        if _Cache.at and time.monotonic() - _Cache.at < max_age:
            return [dict(item) for item in _Cache.value]
    value = _probe()
    with _Cache.lock:
        _Cache.value = value
        _Cache.at = time.monotonic()
    return [dict(item) for item in value]


def local_ips() -> set[str]:
    return {item["ip"] for item in local_networks()}


def on_bridge_ap(networks: list[dict[str, Any]]) -> bool:
    return any(item["bridge_ap"] for item in networks)


def scan_hosts(networks: list[dict[str, Any]]) -> list[str]:
    hosts: list[str] = []
    for item in networks:
        if not item.get("scannable"):
            continue
        own = item.get("ip")
        hosts.extend(str(h) for h in ipaddress.IPv4Network(item["network"]).hosts() if str(h) != own)
    return list(dict.fromkeys(hosts))
