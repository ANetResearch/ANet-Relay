// Application tools (04 §3, §5, §6.2): everything an operator can do in the ANet Relay window and its backend
// outside a single aircraft. Interface actions go through POST /app/ui (the window runs them and shows who did it);
// the rest are the backend's own endpoints. Names match the app MCP server (app_*). No approval is needed, except for
// hardware in the loop (relay_hitl), which agent.mjs puts before the operator on every start and stop (11 §12).
import { defineTool, enc, Type } from "./tools-common.mjs";
import { FRAME, HOME } from "./tools-twin.mjs";

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
    Type.Object({ tab: opt(lit("overview", "control", "bench", "telemetry", "parameters", "log")) }),
  ],
  ["app_close_inspector", "close_inspector", "Close inspector", "Close the inspector panel.", empty],
  [
    "app_open_settings",
    "open_settings",
    "Open settings",
    "Open the settings dialog, optionally on a section.",
    Type.Object({ section: opt(lit("general", "safety", "controllers", "copilot", "network", "autopilots", "data", "about")) }),
  ],
  ["app_open_connect", "open_connect", "Open connect dialog", "Open the connection dialog on its scan, address, serial or bridge tab.", Type.Object({ tab: opt(lit("scan", "address", "serial", "bridge")) })],
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
      "Connect aircraft",
      "Connect to an aircraft or telemetry radio at a host (and UDP port), or to a flight controller on a USB serial port (serial {device, baud}; list the ports with relay_serial_ports). Give host or serial, not both. A serial link fails with serial_unavailable (device busy, not found, no heartbeat, pyserial missing).",
      Type.Object({
        host: opt(Type.String()),
        port: opt(Type.Integer({ minimum: 1, maximum: 65535 })),
        serial: opt(
          Type.Object({
            device: Type.String({ description: "device from relay_serial_ports, e.g. /dev/ttyACM0 or COM5" }),
            baud: opt(Type.Union([Type.Integer({ minimum: 1200, maximum: 4000000 }), Type.Literal("auto")], { description: "auto (default) tries 115200, 57600 and 921600; USB autopilots ignore it" })),
          }),
        ),
      }),
      (p) => {
        if ((p.host === undefined) === (p.serial === undefined)) throw new Error("invalid arguments: give host or serial, not both");
        return call("POST", "/vehicles", p.serial ? { serial: p.serial } : { host: p.host, port: p.port });
      },
    ),
    defineTool(
      "relay_serial_ports",
      "Serial ports",
      "Serial ports on this computer: {available, reason, ports}; each port has device, name, description, manufacturer, USB VID/PID, serial number, autopilot (a known flight-controller USB interface), kind (autopilot | usb-serial | other), adapter (CP210x, FTDI, CH340) and vehicle (the aircraft already on it). available false: pyserial is missing. Connect one with app_connect {serial}; the aircraft id is usb-<port name>.",
      empty,
      () => call("GET", "/serial/ports"),
    ),
    defineTool("app_remove_vehicle", "Remove aircraft", "Remove an aircraft from the fleet (simulators are deleted).", Type.Object({ vehicle: Type.String() }), (p) =>
      call("DELETE", `/vehicles/${enc(p.vehicle)}`),
    ),
    defineTool(
      "app_add_sim",
      "Add simulators",
      "Add simulated aircraft: count, autopilot px4 or ardupilot, with or without a position estimate, with or without hover assist (omitted: the sim_hover_assist setting), and a frame. engine relay (default) is Relay's fast simulator; engine sitl starts firmware twins (the official firmware against Relay's physics) from a profile or a pack, as relay_twin_start does.",
      Type.Object({
        count: opt(Type.Integer({ minimum: 1, maximum: 8 })),
        autopilot: opt(lit("px4", "ardupilot")),
        position: opt(Type.Boolean()),
        hover_assist: opt(Type.Boolean()),
        engine: opt(lit("relay", "sitl")),
        profile: opt(Type.String({ description: "sitl: twin profile id" })),
        pack: opt(Type.String({ description: "sitl: runtime pack id" })),
        frame: opt(FRAME),
        home: opt(HOME),
      }),
      (p) =>
        call("POST", "/sims", {
          count: p.count ?? 1,
          autopilot: p.autopilot,
          position: p.position,
          hover_assist: p.hover_assist,
          ...(p.engine ? { engine: p.engine } : {}),
          ...(p.profile ? { profile: p.profile } : {}),
          ...(p.pack ? { pack: p.pack } : {}),
          ...(p.frame ? { frame: p.frame } : {}),
          ...(p.home ? { home: p.home } : {}),
        }),
    ),
    defineTool("app_remove_sims", "Remove all simulators", "Delete every simulated aircraft.", empty, () => call("DELETE", "/sims")),
    defineTool(
      "app_reset_sim",
      "Reset simulator",
      "Reset one simulated aircraft to its start state, upright on the ground; this also recovers a crashed one. A firmware twin restarts its firmware with the same profile: the link drops until the new heartbeat (event twin_started with restart true, or twin_start_failed).",
      Type.Object({ vehicle: Type.String() }),
      (p) => call("POST", `/sims/${enc(p.vehicle)}/reset`),
    ),
    defineTool(
      "app_sim_assist",
      "Simulator hover assist",
      "Switch hover assist of one simulated aircraft. On: altitude and position hold. Off: physical flight; the throttle is thrust, hold modes are refused and an airborne aircraft moves to STABILIZE. Allowed armed and in flight. Needs the claim on the aircraft, or an aircraft nobody controls.",
      Type.Object({ vehicle: Type.String(), on: Type.Boolean() }),
      (p) => call("POST", `/sims/${enc(p.vehicle)}/assist`, { on: p.on }),
    ),
    defineTool("app_settings_get", "Read settings", "The persistent Relay settings (safety volume, interface preferences, agent control switch).", empty, () => call("GET", "/settings")),
    defineTool(
      "app_settings_set",
      "Write settings",
      "Merge a partial settings object into the Relay settings (for example {retention_days: 30}). Only the keys you pass change. agents_app_control belongs to the operator and cannot be changed here.",
      Type.Object({ patch: Type.Record(Type.String(), Type.Any()) }),
      (p) => call("PUT", "/settings", p.patch),
    ),
    defineTool("app_alerts", "Alerts", "Open and recent alerts of the fleet.", empty, () => call("GET", "/alerts")),
    defineTool("app_ack_alert", "Acknowledge alert", "Acknowledge one alert by id.", Type.Object({ id: Type.String() }), (p) => call("POST", `/alerts/${enc(p.id)}/ack`)),
    defineTool(
      "relay_hitl",
      "Hardware in the loop",
      "PX4 hardware in the loop (11 §7) on a real PX4 aircraft connected over a USB serial link. status reads the session (state, steps, backup, changed parameters, mismatches, volatile parameters not compared, and cause when start is not possible: sim, unidentified, ardupilot, other_firmware, not_serial, busy, pending, link, armed, possession). start backs up every parameter, sets SYS_HITL, reboots the board and feeds it Relay's physics while the firmware keeps the motors locked; it needs confirm HITL, a disarmed aircraft with the propellers removed and the operator's possession. stop writes the backup back, reboots and verifies it. ArduPilot has no HIL: recommend a firmware twin. start and stop always wait for the operator's approval, also with Allow all.",
      Type.Object({ vehicle: Type.String(), action: lit("status", "start", "stop"), confirm: opt(Type.Literal("HITL", { description: "required for start" })) }),
      (p) => {
        if (p.action === "status") return call("GET", `/vehicles/${enc(p.vehicle)}/hitl`);
        if (p.action === "start" && p.confirm !== "HITL") throw new Error("invalid arguments: start needs confirm HITL");
        return call("POST", `/vehicles/${enc(p.vehicle)}/hitl`, { action: p.action, ...(p.confirm ? { confirm: p.confirm } : {}) });
      },
    ),
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
