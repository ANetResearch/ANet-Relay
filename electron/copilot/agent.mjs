// Relay Copilot (03 §16): a small built-in agent on pi (@earendil-works/pi-agent-core, pi-ai).
// It runs in the main process so the API key never reaches the renderer, talks to the
// configured LLM endpoint, and flies aircraft through Relay's own agent API as the session
// "copilot": it claims what it flies, a person who possesses an aircraft always wins, and
// every motion of a real aircraft waits for the operator's approval in the chat.
// Bundled into agent.bundle.mjs by scripts/build-copilot.mjs for the packaged app.
import { Agent } from "@earendil-works/pi-agent-core";
import { createModels, createProvider } from "@earendil-works/pi-ai";
import { anthropicMessagesApi } from "@earendil-works/pi-ai/api/anthropic-messages.lazy";
import { openAICompletionsApi } from "@earendil-works/pi-ai/api/openai-completions.lazy";
import { openAIResponsesApi } from "@earendil-works/pi-ai/api/openai-responses.lazy";

import { appTools } from "./tools-app.mjs";
import { compactVehicle, defineTool, enc, flightHelpers, SESSION_NAME, text, Type, VID } from "./tools-common.mjs";
import { dangerResolver, deviceTools } from "./tools-device.mjs";

export const APIS = ["openai-completions", "anthropic-messages", "openai-responses"];
const PROVIDER = "relay-copilot";
const HEARTBEAT_MS = 5000;
const APPROVAL_TIMEOUT_MS = 120_000;
/** Flight tools that move an aircraft; on a real one each waits for the operator's approval (04 §6.2). */
const MOTION_TOOLS = new Set([
  "relay_takeoff",
  "relay_move",
  "relay_yaw",
  "relay_hover",
  "relay_land",
  "relay_plan",
  "relay_sticks_for",
  "relay_refly",
]);
export const TOOL_SCOPES = ["flight", "flight+device", "all"];

// ---------------------------------------------------------------------------
// Model

/** A pi Models collection with one provider/model built from the user's endpoint settings. */
export function buildModels(cfg, apiKey) {
  if (!APIS.includes(cfg.api)) throw new Error(`unsupported API ${cfg.api}`);
  const model = {
    id: cfg.model,
    name: cfg.model,
    api: cfg.api,
    provider: PROVIDER,
    baseUrl: cfg.baseUrl,
    reasoning: false,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 128_000,
    maxTokens: 4096,
    // Generic OpenAI-compatible servers (vLLM, Ollama, gateways) understand the classic subset only.
    compat:
      cfg.api === "openai-completions"
        ? { supportsDeveloperRole: false, supportsReasoningEffort: false, supportsStore: false }
        : undefined,
  };
  const provider = createProvider({
    id: PROVIDER,
    name: "Relay Copilot",
    baseUrl: cfg.baseUrl,
    auth: { apiKey: { name: "Copilot API key", resolve: async () => ({ auth: { apiKey } }) } },
    models: [model],
    api: {
      "openai-completions": openAICompletionsApi(),
      "anthropic-messages": anthropicMessagesApi(),
      "openai-responses": openAIResponsesApi(),
    },
  });
  const models = createModels({ identity: { attribution: null } });
  models.setProvider(provider);
  return { models, model: models.getModel(PROVIDER, cfg.model) };
}

// ---------------------------------------------------------------------------
// Relay API client (agent session)

export class RelayHttp {
  constructor(base) {
    this.base = base.replace(/\/$/, "");
    this.token = null;
    this.timer = null;
  }

  async request(method, path, body) {
    const headers = { "content-type": "application/json" };
    if (this.token) headers["X-Relay-Session"] = this.token;
    const res = await fetch(`${this.base}/api/v1${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await res.text();
    const data = text ? JSON.parse(text) : {};
    if (!res.ok) {
      const e = data.error || {};
      const error = new Error(`${e.code || res.status}: ${e.message_en || res.statusText}`);
      error.code = e.code;
      error.detail = e.detail;
      throw error;
    }
    return data;
  }

  async ensureSession() {
    if (this.token) return;
    const opened = await this.request("POST", "/sessions", { name: SESSION_NAME });
    this.token = opened.token;
    clearInterval(this.timer);
    // Claims last as long as the session lease: keep it alive while the app runs.
    this.timer = setInterval(() => {
      this.request("POST", "/sessions/heartbeat").catch((err) => {
        if (/session/.test(err.code || "")) this.token = null;
      });
    }, HEARTBEAT_MS);
    this.timer.unref?.();
  }

  async call(method, path, body) {
    await this.ensureSession();
    try {
      return await this.request(method, path, body);
    } catch (err) {
      if (!/^session_(expired|required)$|^auth_required$/.test(err.code || "")) throw err;
      this.token = null;
      await this.ensureSession();
      return this.request(method, path, body);
    }
  }

  async close() {
    clearInterval(this.timer);
    this.timer = null;
    if (!this.token) return;
    await this.request("DELETE", "/sessions").catch(() => {});
    this.token = null;
  }
}

// ---------------------------------------------------------------------------
// Tools

const STEP = Type.Object(
  {
    action: Type.Union(["takeoff", "hover", "move", "yaw", "land"].map((a) => Type.Literal(a))),
    alt: Type.Optional(Type.Number({ description: "takeoff: metres above the ground" })),
    seconds: Type.Optional(Type.Number({ description: "hover: seconds" })),
    forward: Type.Optional(Type.Number({ description: "move: metres forward, negative = back" })),
    right: Type.Optional(Type.Number({ description: "move: metres right, negative = left" })),
    up: Type.Optional(Type.Number({ description: "move: metres up, negative = down" })),
    deg: Type.Optional(Type.Number({ description: "yaw: degrees, positive = clockwise (right)" })),
  },
  { additionalProperties: false },
);

/** Build the Copilot tool list on a Relay API client. */
export function relayTools(relay) {
  const { vehicle, claimFor, waitSkill, skill, waitPlan } = flightHelpers(relay);

  return [
    {
      name: "relay_fleet",
      label: "Fleet",
      description: "List every aircraft: link, armed, mode, altitude, heading, battery, who controls it, capabilities.",
      parameters: Type.Object({}),
      execute: async () => {
        const fleet = await relay.call("GET", "/fleet");
        return text({ safety_volume: fleet.safety_volume, vehicles: fleet.vehicles.map(compactVehicle) });
      },
    },
    {
      name: "relay_vehicle",
      label: "Aircraft status",
      description: "Full status of one aircraft, including preflight results and recent status text.",
      parameters: Type.Object({ vehicle: VID }),
      execute: async (_id, p) => {
        const v = await vehicle(p.vehicle);
        return text({
          ...compactVehicle(v),
          preflight: v.preflight,
          statustext: (v.statustext || []).slice(-5),
          gps: v.gps,
          link: v.link,
        });
      },
    },
    {
      name: "relay_takeoff",
      label: "Take off",
      description:
        "Arm (simulators directly; real aircraft after the operator approves) and climb to alt metres, then hold. " +
        "Works without a position estimate (climbs in altitude hold).",
      parameters: Type.Object({ vehicle: VID, alt: Type.Number({ minimum: 0.2, maximum: 3, default: 1 }) }),
      executionMode: "sequential",
      execute: async (_id, p, signal) => text(await skill(p.vehicle, "takeoff", { alt: p.alt ?? 1, confirm: "ARM" }, signal)),
    },
    {
      name: "relay_hover",
      label: "Hover",
      description: "Hold altitude (and position when estimated) for the given seconds.",
      parameters: Type.Object({ vehicle: VID, seconds: Type.Number({ minimum: 0, maximum: 60, default: 3 }) }),
      executionMode: "sequential",
      execute: async (_id, p, signal) => text(await skill(p.vehicle, "hold", { seconds: p.seconds ?? 3 }, signal)),
    },
    {
      name: "relay_move",
      label: "Move",
      description:
        "Move relative to the nose: forward (negative back), right (negative left), up (negative down), metres. " +
        "Without a position estimate the horizontal distance is an open-loop estimate (at most 2 m).",
      parameters: Type.Object({
        vehicle: VID,
        forward: Type.Optional(Type.Number({ minimum: -2, maximum: 2 })),
        right: Type.Optional(Type.Number({ minimum: -2, maximum: 2 })),
        up: Type.Optional(Type.Number({ minimum: -1, maximum: 1 })),
      }),
      executionMode: "sequential",
      execute: async (_id, p, signal) =>
        text(await skill(p.vehicle, "move", { forward: p.forward ?? 0, right: p.right ?? 0, up: p.up ?? 0 }, signal)),
    },
    {
      name: "relay_yaw",
      label: "Turn",
      description: "Turn in place by deg degrees relative to the current heading; positive turns right (clockwise).",
      parameters: Type.Object({ vehicle: VID, deg: Type.Number({ minimum: -360, maximum: 360 }) }),
      executionMode: "sequential",
      execute: async (_id, p, signal) => text(await skill(p.vehicle, "yaw", { deg: p.deg }, signal)),
    },
    {
      name: "relay_land",
      label: "Land",
      description: "Land in place and disarm.",
      parameters: Type.Object({ vehicle: VID }),
      executionMode: "sequential",
      execute: async (_id, p, signal) => text(await skill(p.vehicle, "land", {}, signal)),
    },
    {
      name: "relay_plan",
      label: "Fly a sequence",
      description:
        "Fly a whole sequence and wait for it: takeoff {alt}, hover {seconds}, move {forward,right,up}, yaw {deg}, land. " +
        "Prefer this for multi-step requests. A failed step stops the sequence and the aircraft holds.",
      parameters: Type.Object({ vehicle: VID, steps: Type.Array(STEP, { minItems: 1, maxItems: 24 }) }),
      executionMode: "sequential",
      execute: async (_id, p, signal, onUpdate) => {
        await claimFor(p.vehicle);
        const started = await relay.call("POST", `/vehicles/${enc(p.vehicle)}/plan`, {
          steps: p.steps,
          name: "copilot",
          confirm: "ARM",
        });
        return text(await waitPlan(p.vehicle, started.plan.id, signal, onUpdate));
      },
    },
    {
      name: "relay_cancel",
      label: "Cancel sequence",
      description: "Cancel a running sequence; an airborne aircraft holds where it is.",
      parameters: Type.Object({ vehicle: VID }),
      execute: async (_id, p) => text(await relay.call("POST", `/vehicles/${encodeURIComponent(p.vehicle)}/plan/cancel`)),
    },
    {
      name: "relay_release",
      label: "Release",
      description: "Release Copilot's claim on an aircraft when the user wants to fly it or hand it to another agent.",
      parameters: Type.Object({ vehicle: VID }),
      execute: async (_id, p) => text(await relay.call("POST", `/vehicles/${encodeURIComponent(p.vehicle)}/unclaim`)),
    },
    {
      name: "relay_stop",
      label: "Emergency stop",
      description: "EMERGENCY ONLY: cut the motors and disarm. An airborne aircraft falls. Use relay_land otherwise.",
      parameters: Type.Object({ vehicle: VID }),
      execute: async (_id, p) => text(await relay.call("POST", `/vehicles/${encodeURIComponent(p.vehicle)}/stop`)),
    },
    defineTool(
      "relay_sticks_for",
      "Push sticks",
      "Hold stick positions for N seconds (at most 10), then centre them: roll/pitch/yaw in -1..1 (roll right+, pitch forward+, yaw clockwise+), throttle 0..1. " +
        "For fine, open-loop control the other tools cannot express. The aircraft must be armed and in a manual or altitude-hold mode.",
      Type.Object({
        vehicle: VID,
        roll: Type.Number({ minimum: -1, maximum: 1 }),
        pitch: Type.Number({ minimum: -1, maximum: 1 }),
        yaw: Type.Number({ minimum: -1, maximum: 1 }),
        throttle: Type.Number({ minimum: 0, maximum: 1 }),
        seconds: Type.Number({ minimum: 0.1, maximum: 10 }),
      }),
      (p, ctx) => skill(p.vehicle, "sticks", { roll: p.roll, pitch: p.pitch, yaw: p.yaw, throttle: p.throttle, seconds: p.seconds }, ctx.signal),
    ),
    defineTool(
      "relay_refly",
      "Fly a record again",
      "Fly a recorded flight again on this aircraft (see app_refly_options): mode plan (the recorded sequence), path (the recorded track as waypoints), " +
        "sticks (the recorded stick timeline at speed 0.25..2) or auto (recommended). Waits until it finishes.",
      Type.Object({
        vehicle: VID,
        session_id: Type.Integer({ minimum: 1 }),
        mode: Type.Optional(Type.Union(["auto", "plan", "path", "sticks"].map((m) => Type.Literal(m)))),
        speed: Type.Optional(Type.Number({ minimum: 0.25, maximum: 2 })),
      }),
      async (p, ctx) => {
        await claimFor(p.vehicle);
        const started = await relay.call("POST", `/vehicles/${enc(p.vehicle)}/refly`, {
          session_id: p.session_id,
          mode: p.mode ?? "auto",
          speed: p.speed,
          confirm: "ARM",
        });
        if (started.plan) return waitPlan(p.vehicle, started.plan.id, ctx.signal, ctx.onUpdate);
        if (started.skill) {
          const result = await waitSkill(p.vehicle, started.skill.id, ctx.signal);
          if (result.state === "failed") throw new Error(`refly failed: ${result.detail}`);
          return result;
        }
        return started;
      },
    ),
  ];
}

// ---------------------------------------------------------------------------
// Copilot

/** The tool list for a scope setting (04 §6.2): flight, flight+device or all. */
export function toolsFor(relay, scope = "all", danger = dangerResolver(relay)) {
  const tools = relayTools(relay);
  if (scope === "flight") return tools;
  tools.push(...deviceTools(relay, danger));
  if (scope === "flight+device") return tools;
  tools.push(...appTools(relay));
  return tools;
}

export function systemPrompt(lang, scope = "all") {
  const parts = [
    "You are Copilot, the assistant built into ANet Relay, a ground station for small quadcopters on a local network.",
    "Simulated aircraft have ids sim-N; real ones real-<ip>.",
    "Units: metres, seconds, degrees. Moves are relative to the aircraft's nose; yaw is positive clockwise (right).",
    "Tool sets:",
    "- relay_* (flight): read status and fly with takeoff, hover, move, yaw, land, whole sequences (relay_plan), timed stick pushes (relay_sticks_for) and flying a recorded flight again (relay_refly).",
  ];
  if (scope !== "flight") {
    parts.push(
      "- device_* (device): the aircraft down to a single motor, actuator output, RC channel, parameter or MAVLink command: overview, messages, parameters, raw command catalogue and commands, motor test, actuator, RC override, attitude and velocity targets, message rates, reboot, calibration, home, termination, arm (also forced), modes.",
    );
  }
  if (scope === "all") {
    parts.push(
      "- app_* (application): the ANet Relay window and backend: language, workspace, selection, stage view, inspector, settings, connect dialog, preferences, controllers and possession, records and replay, the Copilot window, the main window, screenshots, network scan, connecting, simulators, settings, alerts, records and refly options. The window shows a notice for what you do there.",
    );
  }
  parts.push(
    "Rules:",
    "- For several actions in a row (for example take off, hover, move, turn, land), plan them and call relay_plan once.",
    "- Before flying, check relay_fleet or relay_vehicle when you are not sure which aircraft is meant or whether it is ready.",
    "- If the user did not name an aircraft, use the selected aircraft given in the message context; if none, ask.",
    "- A person can possess any aircraft at any time and always wins. If a tool reports possessed_by_human, stop and tell the user to release the aircraft first. Never retry around it.",
    "- Real aircraft only move after the operator approves each command in the chat; this covers every motion tool and every device tool that can spin a motor, drive an output, override RC, reboot, calibrate, terminate, force-arm, write a parameter or fly a target. If they decline, do not insist. The approval is the confirmation: never ask the user to type a confirm word.",
  );
  if (scope !== "flight") {
    parts.push(
      "- Use the least powerful tool that does the job. Prefer relay_* skills over device_attitude, device_velocity, device_rc or device_command. Read first (device_overview, device_param_get), then write, and report old and new values.",
      "- Motor tests need a disarmed aircraft; tell the user to remove the propellers of a real aircraft before you request one. Use device_motors_stop if anything looks wrong.",
      "- device_terminate and relay_stop cut the motors; use them only for an emergency the user asks for.",
    );
  } else {
    parts.push("- Use relay_stop only for an emergency the user asks for. Prefer relay_land.");
  }
  if (scope === "all") {
    parts.push("- Interface actions need no approval but change what the operator sees: do them only when asked, and say what you changed. Do not close dialogs the operator is working in.");
  }
  parts.push(
    "- Without a position estimate horizontal moves are open-loop estimates: say so when you report distances.",
    "- Keep answers short and factual. Report what was done and the results, not your reasoning.",
    "- Each message starts with a context line: the selected aircraft and the interface language. When the interface language is zh, answer in Simplified Chinese.",
    lang === "zh" ? "Answer in Simplified Chinese." : "Answer in the language the user writes in.",
  );
  return parts.join("\n");
}

function summarise(name, args, danger) {
  const a = args || {};
  switch (name) {
    case "relay_takeoff":
      return { action: "takeoff", alt: a.alt ?? 1 };
    case "relay_hover":
      return { action: "hover", seconds: a.seconds ?? 3 };
    case "relay_move":
      return { action: "move", forward: a.forward ?? 0, right: a.right ?? 0, up: a.up ?? 0 };
    case "relay_yaw":
      return { action: "yaw", deg: a.deg };
    case "relay_land":
      return { action: "land" };
    case "relay_plan":
      return { action: "plan", steps: a.steps };
    default: {
      // everything else: the renderer shows the tool name and its arguments
      const { vehicle: _vehicle, ...rest } = a;
      return { action: name, tool: name, danger: danger ?? "motion", args: rest };
    }
  }
}

/**
 * Create a Copilot.
 * @param {object} o
 * @param {RelayHttp} o.relay Relay API client
 * @param {object} o.models pi Models collection
 * @param {object} o.model pi Model
 * @param {(event: object) => void} o.emit renderer events
 * @param {() => string} [o.lang]
 */
export function createCopilot({ relay, models, model, emit, lang = () => "en", scope = () => "all" }) {
  const approvals = new Map();
  let approvalSeq = 0;
  let vehicleKinds = new Map();
  const danger = dangerResolver(relay);

  /** The approval level a tool call needs on a real aircraft, or null (04 §6.2). */
  const levelOf = async (name, args) => {
    if (MOTION_TOOLS.has(name)) return "motion";
    if (name.startsWith("device_")) return danger(name, args || {});
    return null;
  };

  const kindOf = async (id) => {
    if (!vehicleKinds.has(id)) {
      try {
        const v = await relay.call("GET", `/vehicles/${enc(id)}`);
        vehicleKinds.set(id, { kind: v.kind, label: v.label });
      } catch {
        return null;
      }
    }
    return vehicleKinds.get(id);
  };

  const requestApproval = (toolCallId, name, args, info, level) =>
    new Promise((resolve) => {
      const id = `ap-${++approvalSeq}`;
      const timer = setTimeout(() => {
        approvals.delete(id);
        emit({ type: "approval_closed", id, approved: false, reason: "timeout" });
        resolve(false);
      }, APPROVAL_TIMEOUT_MS);
      approvals.set(id, (ok) => {
        clearTimeout(timer);
        approvals.delete(id);
        emit({ type: "approval_closed", id, approved: ok });
        resolve(ok);
      });
      emit({ type: "approval", id, toolCallId, tool: name, vehicle: args.vehicle, label: info.label, request: summarise(name, args, level) });
    });

  const agent = new Agent({
    initialState: { systemPrompt: systemPrompt(lang(), scope()), model, tools: toolsFor(relay, scope(), danger), thinkingLevel: "off" },
    streamFn: models.streamSimple.bind(models),
    toolExecution: "sequential",
    identity: { attribution: null },
    beforeToolCall: async ({ toolCall, args }) => {
      const level = await levelOf(toolCall.name, args);
      if (!level) return undefined;
      const info = await kindOf(args.vehicle);
      if (!info || info.kind !== "real") return undefined;
      const ok = await requestApproval(toolCall.id, toolCall.name, args, info, level);
      return ok ? undefined : { block: true, reason: "The operator declined this command. Do not retry it unless asked." };
    },
  });

  agent.subscribe((event) => {
    switch (event.type) {
      case "agent_start":
        emit({ type: "busy", busy: true });
        break;
      case "message_start":
        if (event.message.role === "assistant") emit({ type: "assistant_start" });
        break;
      case "message_update": {
        const e = event.assistantMessageEvent;
        if (e?.type === "text_delta") emit({ type: "text", delta: e.delta });
        break;
      }
      case "message_end":
        if (event.message.role === "assistant") {
          const m = event.message;
          emit({ type: "assistant_end", error: m.stopReason === "error" ? m.errorMessage || "error" : null, aborted: m.stopReason === "aborted" });
        }
        break;
      case "tool_execution_start":
        emit({ type: "tool_start", id: event.toolCallId, tool: event.toolName, args: event.args });
        break;
      case "tool_execution_update":
        emit({ type: "tool_update", id: event.toolCallId, text: event.partialResult?.content?.[0]?.text ?? "" });
        break;
      case "tool_execution_end":
        emit({
          type: "tool_end",
          id: event.toolCallId,
          tool: event.toolName,
          error: Boolean(event.isError),
          text: (event.result?.content?.[0]?.text ?? "").slice(0, 600),
        });
        break;
      case "agent_end":
        emit({ type: "busy", busy: false });
        break;
      default:
        break;
    }
  });

  return {
    async prompt(userText, context = {}) {
      vehicleKinds = new Map();
      // The window knows which aircraft is selected and which language is shown (GET /app/ui, 04 §3.2).
      let state = null;
      try {
        const ui = await relay.call("GET", "/app/ui");
        state = ui?.state ?? (ui?.lang ? ui : null);
      } catch {
        // no window connected: fall back to what the caller knows
      }
      const selected = state?.selected ?? context.vehicle ?? null;
      const uiLang = state?.lang === "zh" || state?.lang === "en" ? state.lang : lang();
      const prefix = (selected ? `[Selected aircraft: ${selected}]\n` : "") + `[Interface language: ${uiLang}]\n`;
      await agent.prompt(prefix + userText);
    },
    abort() {
      for (const resolve of approvals.values()) resolve(false);
      agent.abort();
    },
    reset() {
      this.abort();
      agent.reset();
      agent.state.tools = toolsFor(relay, scope(), danger);
    },
    approve(id, ok) {
      const resolve = approvals.get(id);
      if (resolve) resolve(Boolean(ok));
      return Boolean(resolve);
    },
    get busy() {
      return agent.state.isStreaming;
    },
  };
}

/** One short request to check the endpoint, model and key. */
export async function testEndpoint(cfg, apiKey) {
  const { models, model } = buildModels(cfg, apiKey);
  const started = Date.now();
  const reply = await models.completeSimple(
    model,
    { messages: [{ role: "user", content: "Reply with the single word: ready", timestamp: Date.now() }] },
    { maxTokens: 16 },
  );
  if (reply.stopReason === "error") throw new Error(reply.errorMessage || "request failed");
  const textOut = reply.content.filter((c) => c.type === "text").map((c) => c.text).join("").trim();
  return { ok: true, ms: Date.now() - started, reply: textOut.slice(0, 80) };
}
