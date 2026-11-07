"""ANet Relay application-level MCP server (04 §5): operate the Relay app itself.

    python mcp/anet_relay_app_mcp.py [--name mcp-app] [--base http://127.0.0.1:8765]

Tools are named ``app_*``. Interface actions (language, workspace, selection, view, dialogs, gamepads,
possession, records, Copilot window, screenshots) are forwarded to the open Relay window through
``POST /app/ui`` (04 §3); the rest wrap the existing fleet, network, settings, alert and record routes.
Fleet, network, settings and alert calls are app operations: an agent session may run them while
agents_app_control is on (the default); the operator token (--operator-token) is never required for them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from relay_mcp_common import (
    VID,
    McpServer,
    RelayClient,
    Tool,
    http_tool,
    image_result,
    run_main,
    schema_tool,
)

SERVER_NAME = "anet-relay-app"

INSTRUCTIONS = (
    "ANet Relay application control: read the app's state and operate everything a person can do in its "
    "window. Interface tools (app_set_language, app_set_workspace, app_select_vehicle, ...) act on the open "
    "window and fail with ui_unavailable (503) when no window is connected, ui_timeout (504) when it does "
    "not answer within 5 s, ui_action_failed (409) when the action itself is refused, and "
    "app_control_disabled (403) when the operator switched off agent control of the app in Settings. "
    "Every action is visible to the person: a notice 'session <name> ...' appears in the window. "
    "Start with app_status and app_ui_state. Fleet, network, settings and alert tools are app operations: "
    "they work while agents_app_control is on and fail with app_control_disabled (403) when it is off. "
    "Safety: app_possess takes manual control of an aircraft as the operator and displaces agents; use it "
    "only when asked. app_stop_all cuts the motors of every aircraft. app_remove_vehicle and the sim "
    "tools change the fleet. Flight and device control live in the anet-relay and anet-relay-device servers."
)

APP = "App operation (needs agents_app_control). "
LANG = {"type": "string", "enum": ["en", "zh"], "description": "en English, zh Simplified Chinese"}


def _unwrap(result: dict) -> Any:
    return result.get("data", result) if isinstance(result, dict) else result


def ui_tool(
    name: str,
    action: str,
    description: str,
    properties: dict | None = None,
    required: list[str] | None = None,
    read_only: bool = False,
    destructive: bool = False,
) -> Tool:
    """One tool per interface action: POST /app/ui {action, args}."""

    def handler(r: RelayClient, a: dict) -> Any:
        # An explicit null is kept for `vehicle` (select_vehicle null clears the selection).
        return _ui_call(r, action, {k: v for k, v in a.items() if v is not None or k == "vehicle"})

    return schema_tool(
        name, description, properties, required, read_only=read_only, destructive=destructive, handler=handler
    )


def _ui_call(r: RelayClient, action: str, args: dict) -> dict:
    """Like ``RelayClient.ui`` but keeps explicit nulls (select_vehicle with null clears the selection)."""
    body = {"action": action, "args": args}
    return r.request("POST", "/app/ui", body, auth="session")


def _screenshot(r: RelayClient, a: dict):
    args = {"max_width": a["max_width"]} if a.get("max_width") else {}
    result = _ui_call(r, "screenshot", args)
    data = _unwrap(result)
    if not isinstance(data, dict) or not data.get("png_base64"):
        return result
    return image_result(data["png_base64"], {k: data.get(k) for k in ("width", "height")})


def _settings_set(r: RelayClient, a: dict) -> dict:
    return r.request("PUT", "/settings", a["settings"])


def _export(r: RelayClient, a: dict) -> Any:
    fmt = a.get("fmt") or "csv"
    limit = int(a.get("max_chars") or 20000)
    raw = r.request("GET", f"/history/sessions/{int(a['session_id'])}/export", query={"fmt": fmt})
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    out = {"session_id": a["session_id"], "format": fmt, "chars": len(text), "truncated": len(text) > limit}
    out["content"] = text[:limit]
    return out


def build_tools() -> list[Tool]:
    t: list[Tool] = [
        http_tool(
            "app_status",
            "Application status: version, uptime, platform, HTTP port, scan state, counts (vehicles, real, "
            "sims, sessions, gamepads, alerts), whether a window is connected, and whether agents may "
            "control the app.",
            "GET",
            "/app",
            read_only=True,
        ),
        http_tool(
            "app_ui_state",
            "The window's current interface state: language, workspace, selected aircraft, view, inspector, "
            "open dialog, Copilot window, preferences, gamepads, possession, window geometry, open record. "
            "state is null when no window is connected; age_s says how stale it is.",
            "GET",
            "/app/ui",
            read_only=True,
        ),
        http_tool(
            "app_gamepads",
            "Gamepads the window sees: index, id, name, mapping, connected, bound aircraft.",
            "GET",
            "/app/gamepads",
            read_only=True,
        ),
        http_tool(
            "app_actions",
            "Catalogue of interface actions with parameters, as the backend publishes it.",
            "GET",
            "/app/actions",
            read_only=True,
        ),
        ui_tool(
            "app_set_language",
            "set_language",
            "Switch the interface language (the native menu follows).",
            {"lang": LANG},
            ["lang"],
        ),
        ui_tool(
            "app_set_workspace",
            "set_workspace",
            "Switch workspace: fly (stage and controls), agents (sessions and activity) or records.",
            {"workspace": {"type": "string", "enum": ["fly", "agents", "records"]}},
            ["workspace"],
        ),
        ui_tool(
            "app_select_vehicle",
            "select_vehicle",
            "Select an aircraft in the window (null clears the selection). Selection alone controls nothing.",
            {"vehicle": {**VID, "type": ["string", "null"]}},
            ["vehicle"],
        ),
        ui_tool(
            "app_set_view",
            "set_view",
            "Stage camera: overview, chase (behind the selected aircraft) or top.",
            {"view": {"type": "string", "enum": ["overview", "chase", "top"]}},
            ["view"],
        ),
        ui_tool(
            "app_open_inspector",
            "open_inspector",
            "Open the inspector panel, optionally on a tab (tab names are listed by app_actions).",
            {"tab": {"type": "string"}},
        ),
        ui_tool("app_close_inspector", "close_inspector", "Close the inspector panel."),
        ui_tool(
            "app_open_settings",
            "open_settings",
            "Open the settings dialog, optionally at a section (section names are listed by app_actions).",
            {"section": {"type": "string"}},
        ),
        ui_tool(
            "app_open_connect",
            "open_connect",
            "Open the connect dialog on a tab: scan (discover on the network), address (connect by "
            "address) or bridge (provision a bridge).",
            {"tab": {"type": "string", "enum": ["scan", "address", "bridge"]}},
        ),
        ui_tool("app_close_dialogs", "close_dialogs", "Close every dialog and floating panel."),
        ui_tool(
            "app_set_pref",
            "set_pref",
            "Change an interface preference. units: m or ft; startView: overview, chase or top; "
            "chaseOnCourse: boolean (auto chase while flying a course); padDeadzone: 0.04..0.15; "
            "padExpo: 0..0.6.",
            {
                "key": {
                    "type": "string",
                    "enum": ["units", "startView", "chaseOnCourse", "padDeadzone", "padExpo"],
                },
                "value": {"type": ["string", "number", "boolean"]},
            },
            ["key", "value"],
        ),
        ui_tool(
            "app_notify",
            "notify",
            "Show a notice to the person in the window (use sparingly; keep it short and factual).",
            {
                "text": {"type": "string", "minLength": 1, "maxLength": 300},
                "level": {"type": "string", "enum": ["info", "warn"], "default": "info"},
            },
            ["text"],
        ),
        ui_tool(
            "app_bind_gamepad",
            "bind_gamepad",
            "Bind a gamepad to an aircraft (default: the selected one). Binding does not start control; "
            "the person still has to possess.",
            {
                "index": {"type": "integer", "minimum": 1, "maximum": 4, "description": "pad slot 1..4"},
                "vehicle": VID,
            },
            ["index"],
        ),
        ui_tool(
            "app_unbind_gamepad",
            "unbind_gamepad",
            "Remove a gamepad's aircraft binding.",
            {"index": {"type": "integer", "minimum": 1, "maximum": 4}},
            ["index"],
        ),
        ui_tool(
            "app_possess",
            "possess",
            "Take manual control of an aircraft as the operator through the normal window flow: "
            "possession wins over every agent and cancels their running skills. Only on explicit request.",
            {
                "vehicle": VID,
                "input": {"type": "string", "enum": ["keyboard", "screen", "gamepad"], "default": "keyboard"},
                "pad": {"type": "integer", "minimum": 1, "maximum": 4, "description": "gamepad slot"},
            },
            ["vehicle"],
            destructive=True,
        ),
        ui_tool(
            "app_release",
            "release",
            "Release the operator's possession of an aircraft (claims resume automatically).",
            {"vehicle": VID},
            ["vehicle"],
        ),
        ui_tool(
            "app_open_record",
            "open_record",
            "Open a flight record in the records workspace.",
            {"session_id": {"type": "integer", "minimum": 1}},
            ["session_id"],
        ),
        ui_tool(
            "app_replay",
            "replay",
            "Control playback of the open record: play, pause or seek to t seconds.",
            {
                "action": {"type": "string", "enum": ["play", "pause", "seek"]},
                "t": {"type": "number", "minimum": 0, "description": "seconds from the record start (seek)"},
            },
            ["action"],
        ),
        ui_tool(
            "app_toggle_copilot",
            "toggle_copilot",
            "Show or hide the floating Copilot window (open=true shows, false hides, omitted toggles).",
            {"open": {"type": "boolean"}},
        ),
        ui_tool(
            "app_window",
            "window",
            "Main window: focus, minimize, maximize, restore or fullscreen.",
            {
                "action": {
                    "type": "string",
                    "enum": ["focus", "minimize", "maximize", "restore", "fullscreen"],
                }
            },
            ["action"],
        ),
        schema_tool(
            "app_screenshot",
            "Screenshot of the main window, returned as an image. Fails with ui_unavailable when the window "
            "is not shown (minimized windows may capture empty).",
            {"max_width": {"type": "integer", "minimum": 200, "maximum": 1920, "default": 1280}},
            read_only=True,
            handler=_screenshot,
        ),
        http_tool(
            "app_scan_network",
            APP + "Scan the local networks for Relay-compatible aircraft and bridges now. Found "
            "aircraft join the fleet; follow progress in app_status (scan) and relay_events.",
            "POST",
            "/discover",
            {
                "networks": {
                    "type": "array",
                    "items": {"type": "string", "description": "CIDR, e.g. 10.7.7.0/24"},
                    "description": "networks to scan; omit for the detected local ones",
                },
                "http_port": {"type": "integer", "minimum": 1, "maximum": 65535, "default": 80},
            },
        ),
        http_tool(
            "app_connect_address",
            APP + "Connect an aircraft by host name or IP address.",
            "POST",
            "/vehicles",
            {
                "host": {"type": "string", "minLength": 1},
                "port": {"type": "integer", "minimum": 1, "maximum": 65535},
            },
            ["host"],
        ),
        http_tool(
            "app_remove_vehicle",
            APP + "Remove an aircraft from the fleet (its link is closed; simulators are deleted).",
            "DELETE",
            "/vehicles/{vehicle}",
            {"vehicle": VID},
            ["vehicle"],
            destructive=True,
        ),
        http_tool(
            "app_sims_add",
            APP + "Add simulated aircraft (1..8).",
            "POST",
            "/sims",
            {
                "count": {"type": "integer", "minimum": 1, "maximum": 8, "default": 1},
                "autopilot": {"type": "string", "enum": ["ardupilot", "px4"], "default": "ardupilot"},
                "position": {
                    "type": "boolean",
                    "default": True,
                    "description": "false: no position estimate (barometer only)",
                },
            },
        ),
        http_tool(
            "app_sims_remove",
            APP + "Remove the given simulators, or all of them when `id` is omitted.",
            "DELETE",
            "/sims",
            {
                "id": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "simulator ids, e.g. sim-2",
                }
            },
            destructive=True,
        ),
        http_tool(
            "app_sim_reset",
            APP + "Reset a simulator to the ground at its start position.",
            "POST",
            "/sims/{vehicle}/reset",
            {"vehicle": VID},
            ["vehicle"],
        ),
        http_tool(
            "app_settings_get",
            "Application settings (including agents_app_control and safety volume).",
            "GET",
            "/settings",
            read_only=True,
        ),
        schema_tool(
            "app_settings_set",
            APP + "Update application settings; send only the keys to change (see app_settings_get "
            "for names and types). agents_app_control is granted and withdrawn by the operator in the "
            "window only; a change to it from an agent fails with operator_required.",
            {"settings": {"type": "object", "minProperties": 1, "description": "settings keys to change"}},
            ["settings"],
            handler=_settings_set,
        ),
        http_tool(
            "app_alerts_list", "Alerts with their acknowledgement state.", "GET", "/alerts", read_only=True
        ),
        http_tool(
            "app_alert_ack",
            APP + "Acknowledge one alert.",
            "POST",
            "/alerts/{alert_id}/ack",
            {"alert_id": {"type": "string", "description": "id from app_alerts_list"}},
            ["alert_id"],
        ),
        http_tool(
            "app_stop_all",
            APP + "EMERGENCY STOP FOR THE WHOLE FLEET: throttle to zero and disarm every aircraft. "
            "Airborne aircraft fall. Only in a genuine emergency.",
            "POST",
            "/stop-all",
            destructive=True,
        ),
        http_tool(
            "app_network",
            "Network status: interfaces, subnets, whether the host is on a bridge hotspot, UDP listener "
            "state.",
            "GET",
            "/network",
            read_only=True,
        ),
        http_tool(
            "app_records_list",
            "Flight records, newest first: id, vehicle, kind, label, autopilot, times.",
            "GET",
            "/history/sessions",
            {
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 50},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "vehicle": VID,
            },
            read_only=True,
        ),
        http_tool(
            "app_record_detail",
            "One flight record with its summary, events, control segments and course runs.",
            "GET",
            "/history/sessions/{session_id}",
            {"session_id": {"type": "integer", "minimum": 1}},
            ["session_id"],
            read_only=True,
        ),
        schema_tool(
            "app_record_export",
            "Export a flight record as csv (samples) or json; the text is cut at max_chars.",
            {
                "session_id": {"type": "integer", "minimum": 1},
                "fmt": {"type": "string", "enum": ["csv", "json"], "default": "csv"},
                "max_chars": {"type": "integer", "minimum": 1000, "maximum": 200000, "default": 20000},
            },
            ["session_id"],
            read_only=True,
            handler=_export,
        ),
        http_tool(
            "app_refly_options",
            "What a record can be flown again from: plan (recorded actions), path (recorded track) and "
            "sticks (recorded inputs), plus the recommended mode. Fly it with relay_refly.",
            "GET",
            "/history/sessions/{session_id}/refly",
            {"session_id": {"type": "integer", "minimum": 1}},
            ["session_id"],
            read_only=True,
        ),
        http_tool(
            "app_courses_list", "Training courses with their rings.", "GET", "/courses", read_only=True
        ),
    ]
    return t


class Server(McpServer):
    def __init__(self, base: str, name: str, out=None, operator_token: str | None = None) -> None:
        super().__init__(
            base,
            name,
            server_name=SERVER_NAME,
            title="ANet Relay App",
            instructions=INSTRUCTIONS,
            tools=build_tools(),
            operator_token=operator_token,
            out=out,
        )


def main(argv: list[str] | None = None) -> int:
    return run_main(
        "anet-relay-app-mcp", "ANet Relay application MCP server (stdio)", "mcp-app", Server, argv
    )


if __name__ == "__main__":
    raise SystemExit(main())
