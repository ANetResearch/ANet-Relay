"""Shared core of the ANet Relay MCP servers (04 §5): stdio, JSON-RPC 2.0, standard library only.

Every server (flight, device, app) is a list of ``Tool`` objects handed to ``McpServer``. The core owns
protocol negotiation, the agent session (opened at start, renewed in the background, reopened when the
lease is lost), error mapping to ``isError`` tool results and image content for screenshots. Protocol
messages go to stdout, one JSON object per line; logs go to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import traceback
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from anet_relay_client import DEFAULT_BASE, RelayClient, RelayError
except ImportError:  # running from the repository checkout
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sdk" / "python"))
    from anet_relay_client import DEFAULT_BASE, RelayClient, RelayError

__all__ = [
    "DEFAULT_BASE",
    "VID",
    "WAIT",
    "McpServer",
    "RelayClient",
    "RelayError",
    "Tool",
    "ToolResult",
    "http_tool",
    "image_result",
    "log",
    "run_main",
    "schema_tool",
]

SERVER_VERSION = "0.4.0"
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SESSION_ERRORS = ("session_expired", "session_required", "auth_required")

VID = {"type": "string", "description": "Vehicle id from relay_fleet, e.g. sim-1 or real-10-7-7-36"}
WAIT = {"type": "boolean", "description": "Wait until the skill finishes (default true)", "default": True}


@dataclass
class ToolResult:
    """Raw MCP content (text and image blocks) for tools that return more than one JSON document."""

    content: list[dict]
    is_error: bool = False


def image_result(png_base64: str, caption: Any = None, mime: str = "image/png") -> ToolResult:
    content: list[dict] = [{"type": "image", "data": png_base64, "mimeType": mime}]
    if caption is not None:
        content.append({"type": "text", "text": json.dumps(caption, ensure_ascii=False)})
    return ToolResult(content)


@dataclass
class Tool:
    name: str
    description: str
    properties: dict = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    handler: Callable[[RelayClient, dict], Any] | None = None
    read_only: bool = False
    destructive: bool = False

    def listing(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": self.properties,
                "required": self.required,
                "additionalProperties": False,
            },
            "annotations": {
                "readOnlyHint": self.read_only,
                "destructiveHint": self.destructive,
                "openWorldHint": True,
            },
        }


def schema_tool(
    name: str,
    description: str,
    properties: dict | None = None,
    required: list[str] | None = None,
    read_only: bool = False,
    destructive: bool = False,
    handler: Callable[[RelayClient, dict], Any] | None = None,
) -> Tool:
    return Tool(name, description, properties or {}, required or [], handler, read_only, destructive)


def http_tool(
    name: str,
    description: str,
    method: str,
    path: str,
    properties: dict | None = None,
    required: list[str] | None = None,
    query: tuple[str, ...] = (),
    auth: str = "session",
    read_only: bool = False,
    destructive: bool = False,
    body_extra: dict | None = None,
    post: Callable[[Any, dict], Any] | None = None,
) -> Tool:
    """A tool that is one REST call. ``{name}`` in ``path`` takes the argument of that name; arguments
    listed in ``query`` become query parameters; for POST / PUT / PATCH the remaining ones are the JSON
    body (for GET / DELETE they are query parameters too). ``post`` shapes the response."""
    props = properties or {}

    def handler(r: RelayClient, a: dict) -> Any:
        used: set[str] = set()

        def fill(match_name: str) -> str:
            used.add(match_name)
            if a.get(match_name) is None:
                raise KeyError(match_name)
            return urllib.parse.quote(str(a[match_name]), safe="")

        url = _fill_path(path, fill)
        rest = {k: v for k, v in a.items() if k not in used and v is not None}
        q = {k: rest.pop(k) for k in list(rest) if k in query}
        body = None
        if method in ("POST", "PUT", "PATCH"):
            body = {**(body_extra or {}), **rest}
        else:
            q.update(rest)
        result = r.request(method, url, body, auth=auth, query=q or None)
        return post(result, a) if post else result

    return Tool(name, description, props, required or [], handler, read_only, destructive)


def _fill_path(path: str, fill: Callable[[str], str]) -> str:
    out, i = [], 0
    while i < len(path):
        if path[i] == "{":
            j = path.index("}", i)
            out.append(fill(path[i + 1 : j]))
            i = j + 1
        else:
            out.append(path[i])
            i += 1
    return "".join(out)


class McpServer:
    def __init__(
        self,
        base: str,
        name: str,
        *,
        server_name: str,
        title: str,
        instructions: str,
        tools: list[Tool],
        operator_token: str | None = None,
        out=None,
    ) -> None:
        self.relay = RelayClient(base, operator_token=operator_token)
        self.name = name
        self.server_name = server_name
        self.title = title
        self.instructions = instructions
        self.tools = {t.name: t for t in tools}
        self.listing = [t.listing() for t in tools]
        self.out = out or sys.stdout
        self.write_lock = threading.Lock()
        self.session_lock = threading.Lock()

    # ------------------------------------------------------------------ session

    def ensure_session(self, stale: str | None = None) -> None:
        """Open the session if there is none, or replace ``stale`` (the id a call found expired).

        Calls run on their own threads: when several find the same session expired, only the first
        reopens it; the others see a newer id and reuse it."""
        with self.session_lock:
            current = self.relay.session_id
            if current and (stale is None or current != stale):
                return
            self.relay.close()
            self.relay.open_session(self.name)
            log(self.server_name, f"opened session {self.relay.session_id} as {self.name}")

    def call_tool(self, name: str, args: dict) -> Any:
        tool = self.tools[name]
        self.ensure_session()
        used = self.relay.session_id
        try:
            return tool.handler(self.relay, args)
        except RelayError as exc:
            if exc.code in SESSION_ERRORS:
                self.ensure_session(stale=used)
                return tool.handler(self.relay, args)
            raise

    # ------------------------------------------------------------------ listing hooks

    def list_tools(self) -> list[dict]:
        """The ``tools/list`` answer; a server may narrow it (the flight server does at the sticks level)."""
        return self.listing

    def current_instructions(self) -> str:
        return self.instructions

    # ------------------------------------------------------------------ JSON-RPC

    def send(self, message: dict) -> None:
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self.write_lock:
            self.out.write(line + "\n")
            self.out.flush()

    def reply(self, rid: Any, result: Any = None, error: tuple[int, str] | None = None) -> None:
        if error is not None:
            self.send({"jsonrpc": "2.0", "id": rid, "error": {"code": error[0], "message": error[1]}})
        else:
            self.send({"jsonrpc": "2.0", "id": rid, "result": result})

    def handle_line(self, line: str) -> threading.Thread | None:
        line = line.strip()
        if not line:
            return None
        try:
            msg = json.loads(line)
        except ValueError:
            self.reply(None, error=(-32700, "Parse error"))
            return None
        if isinstance(msg, list):  # batches (2025-03-26) are answered one by one
            for item in msg:
                self.handle_message(item)
            return None
        return self.handle_message(msg)

    def handle_message(self, msg: Any) -> threading.Thread | None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            if isinstance(msg, dict) and ("result" in msg or "error" in msg):
                return None  # a response to a request we never send
            self.reply(msg.get("id") if isinstance(msg, dict) else None, error=(-32600, "Invalid Request"))
            return None
        method, rid, params = msg["method"], msg.get("id"), msg.get("params") or {}
        if rid is None:  # notification: initialized, cancelled, ...
            return None
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            self.reply(
                rid,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": self.server_name, "title": self.title, "version": SERVER_VERSION},
                    "instructions": self.current_instructions(),
                },
            )
        elif method == "ping":
            self.reply(rid, {})
        elif method == "tools/list":
            self.reply(rid, {"tools": self.list_tools()})
        elif method == "tools/call":
            name = params.get("name")
            if name not in self.tools:
                self.reply(rid, error=(-32602, f"Unknown tool: {name}"))
                return None
            args = params.get("arguments") or {}
            if not isinstance(args, dict):
                self.reply(rid, error=(-32602, "arguments must be an object"))
                return None
            tool = self.tools[name]
            missing = [k for k in tool.required if k not in args]  # an explicit null is a value (deselect)
            unknown = sorted(set(args) - set(tool.properties))
            if missing or unknown:
                # a tool error the model can read and correct, instead of a half-formed request to Relay
                parts = [f"missing required argument(s): {', '.join(missing)}"] if missing else []
                if unknown:
                    parts.append(
                        f"unknown argument(s): {', '.join(unknown)}; expected {', '.join(tool.properties)}"
                    )
                self.reply(rid, self._text_result("invalid arguments: " + "; ".join(parts), True))
                return None
            worker = threading.Thread(target=self._run_tool, args=(rid, name, args), daemon=True)
            worker.start()  # long skills must not block ping or other calls
            return worker
        elif method in ("resources/list", "prompts/list"):
            self.reply(rid, {method.split("/")[0]: []})
        else:
            self.reply(rid, error=(-32601, f"Method not found: {method}"))
        return None

    def _text_result(self, text: str, is_error: bool) -> dict:
        return {"content": [{"type": "text", "text": text}], "isError": is_error}

    def _run_tool(self, rid: Any, name: str, args: dict) -> None:
        try:
            result = self.call_tool(name, args)
            if isinstance(result, ToolResult):
                self.reply(rid, {"content": result.content, "isError": result.is_error})
            else:
                self.reply(rid, self._text_result(json.dumps(result, ensure_ascii=False, indent=1), False))
        except RelayError as exc:
            self.reply(rid, self._text_result(json.dumps(error_body(exc), ensure_ascii=False), True))
        except (KeyError, TypeError, ValueError) as exc:
            self.reply(rid, self._text_result(f"invalid arguments: {exc}", True))
        except Exception:  # pragma: no cover - keep the server alive
            log(self.server_name, traceback.format_exc())
            self.reply(rid, self._text_result("internal error in the MCP server", True))

    def serve(self, stream=None) -> None:
        stream = stream or sys.stdin
        try:
            self.ensure_session()
        except RelayError as exc:
            log(
                self.server_name,
                f"Relay not reachable yet ({exc.message}); the session opens on the first call",
            )
        workers: list[threading.Thread] = []
        for line in stream:
            worker = self.handle_line(line)
            if worker is not None:
                workers.append(worker)
        for worker in workers:
            worker.join(timeout=120)
        self.relay.close()


def error_body(exc: RelayError) -> dict:
    """Relay error as a tool result body: stable code, English and Chinese message, detail."""
    body: dict[str, Any] = {"error": exc.code, "message": exc.message}
    if exc.message_zh:
        body["message_zh"] = exc.message_zh
    if exc.detail:
        body["detail"] = exc.detail
    return body


def log(server: str, text: str) -> None:
    sys.stderr.write(f"{server}-mcp: {text}\n")
    sys.stderr.flush()


def run_main(
    prog: str, description: str, default_name: str, build: Callable[..., McpServer], argv: list[str] | None
) -> int:
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument(
        "--name", default=default_name, help=f"agent session name shown in Relay (default {default_name})"
    )
    parser.add_argument(
        "--base", default=os.environ.get("RELAY_API", DEFAULT_BASE), help="Relay API base URL"
    )
    parser.add_argument(
        "--operator-token",
        default=os.environ.get("RELAY_OPERATOR_TOKEN"),
        help="operator token for operator-only calls (default: $RELAY_OPERATOR_TOKEN)",
    )
    args = parser.parse_args(argv)
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    build(args.base, args.name, operator_token=args.operator_token).serve()
    return 0
