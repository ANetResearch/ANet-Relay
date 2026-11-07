// Application tools (04 §3, §5, §6.2): everything an operator can do in the ANet Relay window and its backend
// outside a single aircraft. Interface actions go through POST /app/ui (the window runs them and shows who did it);
// the rest are the backend's own endpoints. Names match the app MCP server (app_*). No approval is needed.
import { defineTool, enc, Type } from "./tools-common.mjs";

const opt = (t) => Type.Optional(t);
const lit = (...values) => Type.Union(values.map((x) => Type.Literal(x)));
const empty = Type.Object({});

/** Interface actions: tool name -> {action, label, description, parameters}. */
const UI = [
  ["app_set_language", "set_language", "Set language", "Switch the interface language of the ANet Relay window (also the native menus).", Type.Object({ lang: lit("en", "zh") })],
  ["app_set_workspace", "set_workspace", "Open workspace", "Open the Fly, Agents or Records workspace.", Type.Object({ workspace: lit("fly", "agents", "records") })],
  [
    "app_select_vehicle",
    "select_vehicle",
    "Select aircraft",
    "Select an aircraft in the window (null clears the selection). The selection is what the operator means by 'this aircraft'.",
    Type.Object({ vehicle: Type.Union([Type.String(), Type.Null()]) }),
  ],
  ["app_set_view", "set_view", "Set stage view", "Switch the 3D stage camera: overview, chase (needs a selected aircraft) or top.", Type.Object({ view: lit("overview", "chase", "top") })],
  [
    "app_open_inspector",
    "open_inspector",
    "Open inspector",
    "Open the inspector panel of the selected aircraft, optionally on a tab.",
    Type.Object({ tab: opt(lit("overview", "control", "telemetry", "parameters", "log")) }),
  ],
  ["app_close_inspector", "close_inspector", "Close inspector", "Close the inspector panel.", empty],
  [
    "app_open_settings",
    "open_settings",
    "Open settings",
    "Open the settings dialog, optionally on a section.",
    Type.Object({ section: opt(lit("general", "safety", "controllers", "copilot", "network", "data", "about")) }),
  ],
  ["app_open_connect", "open_connect", "Open connect dialog", "Open the connection dialog on its scan, address or bridge tab.", Type.Object({ tab: opt(lit("scan", "address", "bridge")) })],
  ["app_close_dialogs", "close_dialogs", "Close dialogs", "Close every open dialog, popover and sheet.", empty],
  [
    "app_set_pref",
    "set_pref",
    "Set preference",
    "Change a window preference: units (m|ft), startView (overview|chase|top), chaseOnCourse (boolean), padDeadzone (0.04..0.15), padExpo (0..0.6).",
    Type.Object({ key: lit("units", "startView", "chaseOnCourse", "padDeadzone", "padExpo"), value: Type.Union([Type.String(), Type.Number(), Type.Boolean()]) }),
  ],
  ["app_notify", "notify", "Show notice", "Show a short notice to the operator in the window.", Type.Object({ text: Type.String({ maxLength: 500 }), level: opt(lit("info", "warn")) })],
  [
    "app_bind_gamepad",
    "bind_gamepad",
    "Bind controller",
    "Bind a connected controller (1..4) to an aircraft so its sticks fly it. Defaults to the selected aircraft.",
    Type.Object({ index: Type.Integer({ minimum: 1, maximum: 4 }), vehicle: opt(Type.String()) }),
  ],
  ["app_unbind_gamepad", "unbind_gamepad", "Release controller", "Release the aircraft a controller is bound to.", Type.Object({ index: Type.Integer({ minimum: 1, maximum: 4 }) })],
  [
    "app_possess",
    "possess",
    "Possess aircraft",
    "Take control of an aircraft as the operator (the window's own possess flow). The operator then flies it and wins over every agent.",
    Type.Object({ vehicle: Type.String(), input: opt(lit("keyboard", "screen", "gamepad")), pad: opt(Type.Integer({ minimum: 1, maximum: 4 })) }),
  ],
  ["app_release", "release", "Release possession", "Release an aircraft the operator possesses.", Type.Object({ vehicle: Type.String() })],
  ["app_open_record", "open_record", "Open record", "Open a flight record in the Records workspace for review.", Type.Object({ session_id: Type.Integer({ minimum: 1 }) })],
  [
    "app_replay",
    "replay",
    "Control replay",
    "Play, pause or seek the open record. seek takes seconds from the start of the record (or unix seconds).",
    Type.Object({ action: lit("play", "pause", "seek"), t: opt(Type.Number({ minimum: 0 })) }),
  ],
  ["app_toggle_copilot", "toggle_copilot", "Copilot window", "Show or hide the floating Copilot window (toggles when open is omitted).", Type.Object({ open: opt(Type.Boolean()) })],
  ["app_window", "window", "Main window", "Focus, minimise, maximise, restore or toggle fullscreen of the main window.", Type.Object({ action: lit("focus", "minimize", "maximize", "restore", "fullscreen") })],
];

export function appTools(relay) {
  const call = (method, path, body) => relay.call(method, path, body);
  const ui = (action, args) => call("POST", "/app/ui", { action, args: args ?? {} });

  const tools = UI.map(([name, action, label, description, parameters]) => defineTool(name, label, description, parameters, (p) => ui(action, p)));

  tools.push(
    defineTool("app_ui_state", "Window state", "What the window shows now: language, workspace, selected aircraft, view, inspector, dialogs, preferences, controllers, possession, window size, open record.", empty, () =>
      call("GET", "/app/ui"),
    ),
    defineTool(
      "app_screenshot",
      "Screenshot",
      "Capture the main window. Copilot does not receive the image itself; use app_ui_state and the data tools for facts. The size is reported.",
      Type.Object({ max_width: opt(Type.Integer({ minimum: 160, maximum: 1920 })) }),
      async (p) => {
        const res = await ui("screenshot", p);
        const d = res.data ?? {};
        return { ok: res.ok, width: d.width, height: d.height, png_bytes: d.png_base64 ? Math.floor((d.png_base64.length * 3) / 4) : 0, image: "captured, not shown to the model" };
      },
    ),
    defineTool("app_info", "App status", "ANet Relay itself: version, uptime, ports, scan state, counts of aircraft, sessions, controllers and alerts, and whether a window is connected.", empty, () =>
      call("GET", "/app"),
    ),
    defineTool("app_gamepads", "Controllers", "Game controllers connected to the window with their binding.", empty, () => call("GET", "/app/gamepads")),
    defineTool("app_actions", "Interface actions", "Catalogue of the interface actions the window accepts.", empty, () => call("GET", "/app/actions")),
    defineTool("app_scan", "Scan network", "Start a scan for aircraft on the local network.", empty, () => call("POST", "/discover")),
    defineTool(
      "app_connect",
      "Connect by address",
      "Connect to an aircraft or telemetry radio at a host (and UDP port).",
      Type.Object({ host: Type.String(), port: opt(Type.Integer({ minimum: 1, maximum: 65535 })) }),
      (p) => call("POST", "/vehicles", { host: p.host, port: p.port }),
    ),
    defineTool("app_remove_vehicle", "Remove aircraft", "Remove an aircraft from the fleet (simulators are deleted).", Type.Object({ vehicle: Type.String() }), (p) =>
      call("DELETE", `/vehicles/${enc(p.vehicle)}`),
    ),
    defineTool(
      "app_add_sim",
      "Add simulators",
      "Add simulated aircraft: count, autopilot px4 or ardupilot, with or without a position estimate.",
      Type.Object({ count: opt(Type.Integer({ minimum: 1, maximum: 8 })), autopilot: opt(lit("px4", "ardupilot")), position: opt(Type.Boolean()) }),
      (p) => call("POST", "/sims", { count: p.count ?? 1, autopilot: p.autopilot, position: p.position }),
    ),
    defineTool("app_remove_sims", "Remove all simulators", "Delete every simulated aircraft.", empty, () => call("DELETE", "/sims")),
    defineTool("app_reset_sim", "Reset simulator", "Reset one simulated aircraft to its start state.", Type.Object({ vehicle: Type.String() }), (p) =>
      call("POST", `/sims/${enc(p.vehicle)}/reset`),
    ),
    defineTool("app_settings_get", "Read settings", "The persistent Relay settings (safety volume, interface preferences, agent control switch).", empty, () => call("GET", "/settings")),
    defineTool(
      "app_settings_set",
      "Write settings",
      "Merge a partial settings object into the Relay settings (for example {agents_app_control: true}). Only the keys you pass change.",
      Type.Object({ patch: Type.Record(Type.String(), Type.Any()) }),
      (p) => call("PUT", "/settings", p.patch),
    ),
    defineTool("app_alerts", "Alerts", "Open and recent alerts of the fleet.", empty, () => call("GET", "/alerts")),
    defineTool("app_ack_alert", "Acknowledge alert", "Acknowledge one alert by id.", Type.Object({ id: Type.String() }), (p) => call("POST", `/alerts/${enc(p.id)}/ack`)),
    defineTool("app_stop_all", "Stop all", "EMERGENCY ONLY: cut the motors of every aircraft. Airborne aircraft fall. Use only when the user asks for it.", empty, () => call("POST", "/stop-all")),
    defineTool("app_network", "Network", "Network interfaces, subnets, whether the computer is on a telemetry hotspot and the UDP listener state.", empty, () => call("GET", "/network")),
    defineTool("app_courses", "Courses", "Preset and custom ring courses, scaled to the safety volume.", empty, () => call("GET", "/courses")),
    defineTool("app_records", "Flight records", "All recorded flights with id, aircraft, start, duration and highlights.", empty, () => call("GET", "/history/sessions")),
    defineTool("app_record", "Flight record", "Detail of one flight record: samples summary, events and control segments.", Type.Object({ session_id: Type.Integer({ minimum: 1 }) }), (p) =>
      call("GET", `/history/sessions/${p.session_id}`),
    ),
    defineTool(
      "app_refly_options",
      "Refly options",
      "What a record offers for flying it again: a plan, a path or the stick timeline, and the recommended mode. Fly it with relay_refly.",
      Type.Object({ session_id: Type.Integer({ minimum: 1 }) }),
      (p) => call("GET", `/history/sessions/${p.session_id}/refly`),
    ),
  );
  return tools;
}

/** Names of the interface-action tools, for the renderer's labels. */
export const UI_TOOL_NAMES = UI.map((u) => u[0]);
