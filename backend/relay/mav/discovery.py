"""Find MAVLink Wi-Fi bridges on the local subnet (R-02).

A mavesp8266 bridge serves a page titled "MavLink Bridge" on TCP 80. Bridges
that already stream to UDP 14550 appear on their own; quiet ones are found by
this HTTP probe and then woken with a GCS heartbeat.
"""

from __future__ import annotations

import re
import socket
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

PROBE_TIMEOUT_S = 0.35
PROBE_WORKERS = 64
_VERSION = re.compile(r"version[:\s]*v?([0-9]+(?:\.[0-9]+){1,3})", re.I)
_TITLE = re.compile(r"<title>([^<]*)</title>", re.I)


def identify_bridge_page(text: str) -> dict[str, Any] | None:
    """Return {version, title} when the page belongs to a MAVLink bridge, else None."""
    title_match = _TITLE.search(text)
    title = title_match.group(1).strip() if title_match else ""
    lowered = text.lower()
    is_mavesp = "mavlink bridge" in title.lower() or "mavlink wifi bridge" in lowered
    has_setup = "href='/setup'" in lowered or 'href="/setup"' in lowered
    if not (is_mavesp or (has_setup and "mavlink" in lowered)):
        return None
    match = _VERSION.search(text)
    return {"version": match.group(1) if match else None, "title": title or None}


def http_get(host: str, port: int, path: str, timeout: float, limit: int = 65536) -> str | None:
    """Minimal HTTP/1.0 GET used by the scanner (no redirects, no TLS)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect((host, port))
        hostname = host if port == 80 else f"{host}:{port}"
        sock.sendall(f"GET {path} HTTP/1.0\r\nHost: {hostname}\r\nConnection: close\r\n\r\n".encode("ascii"))
        chunks: list[bytes] = []
        total = 0
        while total < limit:
            try:
                piece = sock.recv(4096)
            except TimeoutError:
                break
            if not piece:
                break
            chunks.append(piece)
            total += len(piece)
        raw = b"".join(chunks).decode("latin-1", "replace")
        _, _, body = raw.partition("\r\n\r\n")
        return body if raw.startswith("HTTP/") else raw
    except OSError:
        return None
    finally:
        sock.close()


def probe_bridge(host: str, port: int = 80, timeout: float = PROBE_TIMEOUT_S) -> dict[str, Any] | None:
    body = http_get(host, port, "/", timeout, limit=8192)
    if body is None:
        return None
    found = identify_bridge_page(body)
    if found is None:
        return None
    return {"host": host, "http_port": port, "version": found["version"], "source": "http"}


def probe_many(
    hosts: list[str],
    on_result: Callable[[str, dict[str, Any] | None], None],
    port: int = 80,
    stop: Callable[[], bool] | None = None,
) -> None:
    """Probe hosts concurrently; ``on_result(host, record_or_none)`` runs for each host."""
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS, thread_name_prefix="relay-probe") as pool:
        futures = {pool.submit(probe_bridge, host, port): host for host in hosts}
        for future in as_completed(futures):
            host = futures[future]
            if stop is not None and stop():
                for pending in futures:
                    pending.cancel()
                return
            try:
                record = future.result()
            except Exception:
                record = None
            on_result(host, record)
