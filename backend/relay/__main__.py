"""Entry point: ``python -m relay [--dev]`` (also the PyInstaller entry).

Environment (set by the Electron shell): RELAY_HTTP_PORT, RELAY_OPERATOR_TOKEN, RELAY_DATA_DIR,
RELAY_LISTEN_PORT, RELAY_ALLOW_LOOPBACK, RELAY_LIFELINE=stdin (exit when the shell's pipe closes).
Tests only: RELAY_SCAN_NETWORKS="127.0.0.0/29:8080" is the default scan (needs RELAY_ALLOW_LOOPBACK).
The API binds 127.0.0.1 only. In ``--dev`` mode without RELAY_OPERATOR_TOKEN a token is generated and
printed to stderr; otherwise a missing token disables operator actions.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import secrets
import signal
import sys
import threading
from pathlib import Path

import uvicorn

from .api.app import create_app
from .config import VERSION, default_data_dir, env_flag, env_int, load_settings
from .fleet import FleetService

API_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _logging(data_dir: Path, level: str) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    root.addHandler(stream)
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        rotating = logging.handlers.RotatingFileHandler(
            data_dir / "backend.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        rotating.setFormatter(fmt)
        root.addHandler(rotating)
    except OSError as exc:
        logging.getLogger("relay").warning("file logging disabled: %s", exc)


def _lifeline() -> None:
    """Exit when stdin closes: the desktop shell keeps the pipe open for its whole life."""
    try:
        while sys.stdin.buffer.read(4096):
            pass
    except (OSError, ValueError):
        pass
    sys.stderr.write("relay: shell went away (stdin closed); shutting down\n")
    os.kill(os.getpid(), signal.SIGTERM)


def _scan_default() -> tuple[list[str], int] | None:
    """Default scan target for end-to-end tests: ``CIDR[,CIDR...][:http_port]``, loopback runs only."""
    raw = os.environ.get("RELAY_SCAN_NETWORKS", "").strip()
    if not raw or not env_flag("RELAY_ALLOW_LOOPBACK"):
        return None
    nets, _, port = raw.partition(":")
    try:
        return [n.strip() for n in nets.split(",") if n.strip()], int(port or 80)
    except ValueError:
        logging.getLogger("relay").warning("ignoring malformed RELAY_SCAN_NETWORKS")
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="relay", description=f"ANet Relay backend {VERSION}")
    parser.add_argument(
        "--dev", action="store_true", help="development mode: print a generated operator token"
    )
    parser.add_argument(
        "--port", type=int, default=None, help=f"HTTP port on {API_HOST} (default {DEFAULT_PORT})"
    )
    parser.add_argument("--data-dir", type=Path, default=None, help="records, settings and logs")
    parser.add_argument("--listen-port", type=int, default=None, help="UDP port for MAVLink (default 14550)")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    data_dir = args.data_dir or default_data_dir()
    _logging(data_dir, args.log_level)
    log = logging.getLogger("relay")
    port = args.port or env_int("RELAY_HTTP_PORT") or DEFAULT_PORT
    token = os.environ.get("RELAY_OPERATOR_TOKEN", "").strip()
    if not token:
        token = secrets.token_hex(32)
        if args.dev:
            sys.stderr.write(f"relay: operator token (dev) {token}\n")
            sys.stderr.flush()
        else:
            token = ""
            log.warning("no RELAY_OPERATOR_TOKEN: operator actions are disabled")

    settings = load_settings(data_dir)
    if args.listen_port is not None:
        settings.listen_port = args.listen_port
    service = FleetService(
        settings,
        data_dir,
        operator_token=token,
        allow_loopback=env_flag("RELAY_ALLOW_LOOPBACK"),
        http_port=port,
    )
    service.scan_default = _scan_default()
    app = create_app(service)
    try:
        (data_dir / "http-port").write_text(str(port), "utf-8")
    except OSError:
        pass
    if os.environ.get("RELAY_LIFELINE") == "stdin" and sys.stdin is not None:
        threading.Thread(target=_lifeline, name="relay-lifeline", daemon=True).start()
    log.info("ANet Relay %s on http://%s:%d (data %s)", VERSION, API_HOST, port, data_dir)
    uvicorn.run(
        app,
        host=API_HOST,
        port=port,
        log_level=args.log_level.lower(),
        ws="websockets-sansio",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
