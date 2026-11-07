"""Bridge provisioning for mavesp8266 Wi-Fi bridges (03 §9, R-04).

Read: ``GET /`` identifies the firmware; ``GET /getparameters`` (HTML table of
packed integers) and ``GET /setup`` (form with readable strings) give the
current settings; ``GET /status.json`` adds packet counters when present.
Write: ``GET /setparameters?...&reboot=1`` with only the changed and required
fields. Passwords are never returned, logged or stored.

Unknown firmware falls back to the first form on the page, submitted as-is
with the operator's values.
"""

from __future__ import annotations

import ipaddress
import json
import struct
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from ..errors import RelayError, err
from .discovery import identify_bridge_page

TIMEOUT_S = 4.0
MAX_PAGE = 256 * 1024
SSID_MAX = 16
PASSWORD_MIN = 8
PASSWORD_MAX = 16
BAUD_RATES = (57600, 115200, 230400, 460800, 921600)
MODE_AP = 0
MODE_STATION = 1


def _fetch(host: str, port: int, path: str, timeout: float = TIMEOUT_S) -> str:
    authority = host if port == 80 else f"{host}:{port}"
    url = f"http://{authority}{path}"
    request = urllib.request.Request(url, headers={"User-Agent": "ANet-Relay"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(MAX_PAGE).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise err("bridge_unreachable", host=host, reason=f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        reason = getattr(exc, "reason", None) or exc
        raise err("bridge_unreachable", host=host, reason=str(reason)) from exc


# ---------------------------------------------------------------------------- parsing


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_parameters_table(html_text: str) -> dict[str, int]:
    parser = _TableParser()
    parser.feed(html_text)
    table: dict[str, int] = {}
    for row in parser.rows:
        if len(row) >= 2 and row[0] and row[0].upper() == row[0] and row[0] != "NAME":
            try:
                table[row[0]] = int(row[1])
            except ValueError:
                continue
    return table


def unpack_ip(value: int | None) -> str | None:
    if value is None:
        return None
    return ".".join(str(b) for b in struct.pack("<I", value & 0xFFFFFFFF))


def pack_ip(text: str) -> int:
    return struct.unpack("<I", ipaddress.IPv4Address(text).packed)[0]


def unpack_text(table: dict[str, int], prefix: str) -> str | None:
    parts = [table.get(f"{prefix}{i}") for i in range(1, 5)]
    if all(p is None for p in parts):
        return None
    raw = b"".join(struct.pack("<I", (p or 0) & 0xFFFFFFFF) for p in parts)
    return raw.split(b"\x00", 1)[0].decode("utf-8", "replace")


def unpack_version(value: int | None) -> str | None:
    if not value:
        return None
    return f"{(value >> 24) & 0xFF}.{(value >> 16) & 0xFF}.{value & 0xFFFF}"


class _FormParser(HTMLParser):
    """Collects the first form: action, method, inputs, selects and their labels."""

    def __init__(self) -> None:
        super().__init__()
        self.form: dict[str, Any] | None = None
        self._in_form = False
        self._done = False
        self._text: list[str] = []
        self._select: dict[str, Any] | None = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if self._done:
            return
        if tag == "form" and self.form is None:
            self.form = {
                "action": a.get("action", ""),
                "method": a.get("method", "get").lower(),
                "fields": [],
            }
            self._in_form = True
            self._text = []
            return
        if not self._in_form or self.form is None:
            return
        label = " ".join("".join(self._text).split()).strip(" :")
        if tag == "input":
            kind = a.get("type", "text").lower()
            if kind in ("submit", "button", "reset", "file"):
                return
            name = a.get("name")
            if not name:
                return
            self.form["fields"].append(
                {
                    "name": name,
                    "type": kind,
                    "value": a.get("value", ""),
                    "checked": "checked" in a,
                    "label": label,
                }
            )
            self._text = []
        elif tag == "select":
            self._select = {
                "name": a.get("name", ""),
                "type": "select",
                "options": [],
                "value": "",
                "label": label,
            }
            self._text = []
        elif tag == "option" and self._select is not None:
            self._select["options"].append(a.get("value", ""))
            if "selected" in a:
                self._select["value"] = a.get("value", "")

    def handle_endtag(self, tag):
        if tag == "select" and self._select is not None and self.form is not None:
            if not self._select["value"] and self._select["options"]:
                self._select["value"] = self._select["options"][0]
            self.form["fields"].append(self._select)
            self._select = None
        elif tag == "form" and self._in_form:
            self._in_form = False
            self._done = True

    def handle_data(self, data):
        if self._in_form:
            self._text.append(data)


def parse_first_form(html_text: str) -> dict[str, Any] | None:
    parser = _FormParser()
    parser.feed(html_text)
    return parser.form


def form_values(form: dict[str, Any] | None) -> dict[str, str]:
    """Current values by name; radio groups resolve to the checked value."""
    values: dict[str, str] = {}
    if not form:
        return values
    for item in form["fields"]:
        if item["type"] == "radio":
            if item.get("checked"):
                values[item["name"]] = item["value"]
            else:
                values.setdefault(item["name"], values.get(item["name"], ""))
        elif item["type"] == "checkbox":
            if item.get("checked"):
                values[item["name"]] = item["value"] or "on"
        else:
            values[item["name"]] = item["value"]
    return values


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def describe_mavesp(table: dict[str, int], setup: dict[str, str]) -> dict[str, Any]:
    """Merge the parameter table and the setup form into one readable settings record."""
    mode_raw = _int(setup.get("mode")) if setup.get("mode") not in (None, "") else table.get("WIFI_MODE")
    sta_ip = setup.get("ipsta") or unpack_ip(table.get("WIFI_IPSTA"))
    return {
        "version": unpack_version(table.get("SW_VER")),
        "mode": {MODE_AP: "ap", MODE_STATION: "station"}.get(mode_raw if mode_raw is not None else -1),
        "ip": unpack_ip(table.get("WIFI_IPADDRESS")),
        "ap_ssid": setup.get("ssid") or unpack_text(table, "WIFI_SSID"),
        "channel": _int(setup.get("channel")) or table.get("WIFI_CHANNEL"),
        "sta_ssid": setup.get("ssidsta") or unpack_text(table, "WIFI_SSIDSTA"),
        "sta_ip": sta_ip,
        "sta_gateway": setup.get("gatewaysta") or unpack_ip(table.get("WIFI_GATEWAYSTA")),
        "sta_subnet": setup.get("subnetsta") or unpack_ip(table.get("WIFI_SUBNET_STA")),
        "dhcp": (sta_ip or "0.0.0.0") == "0.0.0.0",
        "host_port": _int(setup.get("hport")) or table.get("WIFI_UDP_HPORT"),
        "client_port": _int(setup.get("cport")) or table.get("WIFI_UDP_CPORT"),
        "baud": _int(setup.get("baud")) or table.get("UART_BAUDRATE"),
        "has_ap_password": bool(setup.get("pwd") or unpack_text(table, "WIFI_PASSWORD")),
        "has_sta_password": bool(setup.get("pwdsta") or unpack_text(table, "WIFI_PWDSTA")),
    }


def _mask_form(form: dict[str, Any] | None) -> dict[str, Any] | None:
    if form is None:
        return None
    out = {"action": form["action"], "method": form["method"], "fields": []}
    for item in form["fields"]:
        entry = dict(item)
        if entry["type"] == "password" or "pass" in entry["name"].lower() or "pwd" in entry["name"].lower():
            entry["value"] = ""
            entry["secret"] = True
        out["fields"].append(entry)
    return out


# ---------------------------------------------------------------------------- read / write


def read_bridge(host: str, http_port: int = 80) -> dict[str, Any]:
    index = _fetch(host, http_port, "/")
    identified = identify_bridge_page(index)
    record: dict[str, Any] = {"host": host, "http_port": http_port, "kind": "unknown", "settings": None}
    if identified is None:
        form = parse_first_form(index)
        record["form"] = _mask_form(form)
        record["version"] = None
        return record
    table: dict[str, int] = {}
    setup: dict[str, str] = {}
    try:
        table = parse_parameters_table(_fetch(host, http_port, "/getparameters"))
    except RelayError:
        table = {}
    try:
        setup = form_values(parse_first_form(_fetch(host, http_port, "/setup")))
    except RelayError:
        setup = {}
    if not table and not setup:
        record["form"] = _mask_form(parse_first_form(index))
        record["version"] = identified["version"]
        return record
    settings = describe_mavesp(table, setup)
    settings["version"] = settings["version"] or identified["version"]
    record.update(kind="mavesp8266", version=settings["version"], settings=settings, form=None)
    try:
        record["status"] = _flatten_numbers(json.loads(_fetch(host, http_port, "/status.json", timeout=2.0)))
    except (RelayError, ValueError):
        record["status"] = None
    return record


def _flatten_numbers(data: Any, prefix: str = "") -> dict[str, float]:
    out: dict[str, float] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            out.update(_flatten_numbers(value, f"{prefix}{key}."))
    else:
        try:
            out[prefix.rstrip(".")] = float(data)
        except (TypeError, ValueError):
            pass
    return out


def _ip_or_zero(value: Any, field: str) -> str:
    text = str(value or "0.0.0.0").strip()
    try:
        return str(ipaddress.IPv4Address(text))
    except ValueError as exc:
        raise err("invalid_request", reason=f"{field} must be an IPv4 address") from exc


def configure_query(current: dict[str, Any] | None, desired: dict[str, Any]) -> dict[str, str]:
    """Arguments for /setparameters: required fields plus the ones that change. Raises RelayError."""
    current = current or {}
    mode = str(desired.get("mode", "station")).lower()
    if mode not in ("station", "ap"):
        raise err("invalid_request", reason="mode must be station or ap")
    args: dict[str, str] = {"mode": str(MODE_STATION if mode == "station" else MODE_AP)}
    if mode == "station":
        ssid = str(desired.get("ssid") or "").strip()
        password = str(desired.get("password") or "")
        if not 1 <= len(ssid) <= SSID_MAX:
            raise err("invalid_request", reason=f"network name must be 1-{SSID_MAX} characters")
        if password and not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
            raise err("invalid_request", reason=f"password must be {PASSWORD_MIN}-{PASSWORD_MAX} characters")
        args["ssidsta"] = ssid
        args["pwdsta"] = password
        args["ipsta"] = _ip_or_zero(desired.get("ip"), "ip")
        args["gatewaysta"] = _ip_or_zero(desired.get("gateway"), "gateway")
        args["subnetsta"] = _ip_or_zero(desired.get("subnet"), "subnet")
    for key, arg in (("host_port", "hport"), ("client_port", "cport")):
        value = desired.get(key, current.get(key) or (14550 if key == "host_port" else 14555))
        port = _int(value)
        if port is None or not 1 <= port <= 65535:
            raise err("invalid_request", reason=f"{key} must be 1-65535")
        args[arg] = str(port)
    if desired.get("baud") is not None:
        baud = _int(desired["baud"])
        if baud not in BAUD_RATES:
            raise err("invalid_request", reason="baud is not a supported rate")
        if baud != current.get("baud"):
            args["baud"] = str(baud)
    if desired.get("channel") is not None:
        channel = _int(desired["channel"])
        if channel is None or not 1 <= channel <= 13:
            raise err("invalid_request", reason="channel must be 1-13")
        if channel != current.get("channel"):
            args["channel"] = str(channel)
    if desired.get("ap_ssid"):
        ap_ssid = str(desired["ap_ssid"]).strip()
        if not 1 <= len(ap_ssid) <= SSID_MAX:
            raise err("invalid_request", reason=f"hotspot name must be 1-{SSID_MAX} characters")
        if ap_ssid != current.get("ap_ssid"):
            args["ssid"] = ap_ssid
    if desired.get("ap_password"):
        ap_password = str(desired["ap_password"])
        if not PASSWORD_MIN <= len(ap_password) <= PASSWORD_MAX:
            raise err(
                "invalid_request", reason=f"hotspot password must be {PASSWORD_MIN}-{PASSWORD_MAX} characters"
            )
        args["pwd"] = ap_password
    args["reboot"] = "1"
    return args


SECRET_ARGS = {"pwdsta", "pwd"}


def redact(args: dict[str, str]) -> dict[str, str]:
    return {k: ("***" if v and _secret(k) else v) for k, v in args.items()}


def _secret(name: str) -> bool:
    lowered = name.lower()
    return name in SECRET_ARGS or "pass" in lowered or "pwd" in lowered


def configure_bridge(host: str, http_port: int, desired: dict[str, Any]) -> dict[str, Any]:
    """Write settings and restart the bridge. Returns the redacted arguments that were sent."""
    record = read_bridge(host, http_port)
    if record["kind"] != "mavesp8266":
        form = desired.get("form")
        if not isinstance(form, dict) or record.get("form") is None:
            raise err("bridge_unknown_firmware", host=host)
        return submit_form(host, http_port, record["form"], form)
    args = configure_query(record["settings"], desired)
    query = urllib.parse.urlencode(args)
    try:
        _fetch(host, http_port, f"/setparameters?{query}", timeout=TIMEOUT_S)
    except RelayError as exc:
        # The bridge restarts about 100 ms after saving; a dropped connection after the request is expected.
        if "timed out" not in exc.message_en and "reset" not in exc.message_en.lower():
            raise
    return {"kind": "mavesp8266", "submitted": sorted(args), "args": redact(args), "rebooting": True}


def submit_form(host: str, http_port: int, form: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """Fallback: submit the page's first form with operator-edited values."""
    names = {item["name"] for item in form["fields"]}
    current = form_values(form)
    payload = {k: str(v) for k, v in values.items() if k in names}
    merged = {**current, **payload}
    action = form.get("action") or "/"
    if not action.startswith("/"):
        action = "/" + action
    encoded = urllib.parse.urlencode(merged)
    authority = host if http_port == 80 else f"{host}:{http_port}"
    try:
        if form.get("method") == "post":
            request = urllib.request.Request(f"http://{authority}{action}", data=encoded.encode("ascii"))
        else:
            request = urllib.request.Request(f"http://{authority}{action}?{encoded}")
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            response.read(MAX_PAGE)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise err("bridge_unreachable", host=host, reason=str(getattr(exc, "reason", exc))) from exc
    return {"kind": "form", "submitted": sorted(payload), "args": redact(payload), "rebooting": False}
