// Relay Copilot (03 §16): a small built-in agent on pi (@earendil-works/pi-agent-core, pi-ai).
// It runs in the main process so the API key never reaches the renderer, talks to the
// configured LLM endpoint, and flies aircraft through Relay's own agent API as the session
// "copilot": it claims what it flies, a person who possesses an aircraft always wins, and
// every motion of a real aircraft waits for the operator's approval in the chat.
// Bundled into agent.bundle.mjs by scripts/build-copilot.mjs for the packaged app.
import { Agent } from "@earendil-works/pi-agent-core";
import { createModels, createProvider, Type } from "@earendil-works/pi-ai";
import { anthropicMessagesApi } from "@earendil-works/pi-ai/api/anthropic-messages.lazy";
import { openAICompletionsApi } from "@earendil-works/pi-ai/api/openai-completions.lazy";
import { openAIResponsesApi } from "@earendil-works/pi-ai/api/openai-responses.lazy";

export const APIS = ["openai-completions", "anthropic-messages", "openai-responses"];
const PROVIDER = "relay-copilot";
const SESSION_NAME = "copilot";
const HEARTBEAT_MS = 5000;
const APPROVAL_TIMEOUT_MS = 120_000;
const SKILL_TIMEOUT_MS = 90_000;
const PLAN_TIMEOUT_MS = 240_000;
const MOTION_TOOLS = new Set(["relay_takeoff", "relay_move", "relay_yaw", "relay_hover", "relay_land", "relay_plan"]);

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

const VID = Type.String({ description: "Aircraft id from relay_fleet, e.g. sim-1 or real-10-7-7-36" });
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

function compactVehicle(v) {
  return {
    id: v.id,
    label: v.label,
    kind: v.kind,
    autopilot: v.autopilot,
    link: v.link?.state,
    armed: v.armed,
    landed: v.landed,
    mode: v.mode,
    altitude_m: v.altitude_m,
    heading_deg: v.heading_deg,
    battery: v.battery && { voltage: v.battery.voltage, remaining_pct: v.battery.remaining_pct, level: v.battery.level },
    controller: v.controller,
    claim: v.claim?.name ?? null,
    possessed: Boolean(v.possession),
    capabilities: v.capabilities,
    preflight_ok: v.preflight?.ok,
    skill: v.skill,
    plan: v.plan && { state: v.plan.state, index: v.plan.index, count: v.plan.count, detail: v.plan.detail },
  };
}

const text = (value) => ({ content: [{ type: "text", text: typeof value === "string" ? value : JSON.stringify(value) }], details: {} });
const sleep = (ms, signal) =>
  new Promise((resolve, reject) => {
    const onAbort = () => {
      clearTimeout(t);
      reject(new Error("aborted"));
    };
    const t = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });

/** Build the Copilot tool list on a Relay API client. */
export function relayTools(relay) {
  const vehicle = (id) => relay.call("GET", `/vehicles/${encodeURIComponent(id)}`);

  const claimFor = async (id) => {
    const v = await vehicle(id);
    if (v.possession) {
      const err = new Error(
        `possessed_by_human: the operator is flying ${v.label} (${v.possession.input}). Ask them to release it (P, or hold Options) to hand it to you.`,
      );
      err.code = "possessed_by_human";
      throw err;
    }
    if (v.claim && v.claim.name !== SESSION_NAME) {
      throw new Error(`claimed_by_other: ${v.claim.name} holds ${v.label}. Do not take it over unless the user asks.`);
    }
    if (!v.claim) await relay.call("POST", `/vehicles/${encodeURIComponent(id)}/claim`);
    return v;
  };

  const waitSkill = async (id, skillId, signal, timeoutMs = SKILL_TIMEOUT_MS) => {
    const end = Date.now() + timeoutMs;
    while (Date.now() < end) {
      const v = await vehicle(id);
      const s = v.skill;
      if (!s || s.id !== skillId) return { state: "superseded", vehicle: compactVehicle(v) };
      if (s.state !== "running") return { state: s.state, detail: s.detail, vehicle: compactVehicle(v) };
      await sleep(250, signal);
    }
    return { state: "timeout" };
  };

  const skill = async (id, name, params, signal) => {
    await claimFor(id);
    const started = await relay.call("POST", `/vehicles/${encodeURIComponent(id)}/skills/${name}`, params);
    const result = await waitSkill(id, started.skill.id, signal);
    if (result.state === "failed") throw new Error(`${name} failed: ${result.detail}`);
    return result;
  };

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
        const started = await relay.call("POST", `/vehicles/${encodeURIComponent(p.vehicle)}/plan`, {
          steps: p.steps,
          name: "copilot",
          confirm: "ARM",
        });
        const planId = started.plan.id;
        const end = Date.now() + PLAN_TIMEOUT_MS;
        let shown = -1;
        while (Date.now() < end) {
          const v = await vehicle(p.vehicle);
          const plan = v.plan;
          if (!plan || plan.id !== planId) throw new Error("the sequence was replaced by another command");
          if (plan.index !== shown && plan.state === "running") {
            shown = plan.index;
            onUpdate?.(text(`step ${plan.index + 1} of ${plan.count}: ${plan.steps[plan.index]?.action}`));
          }
          if (plan.state === "done") return text({ state: "done", results: plan.results, vehicle: compactVehicle(v) });
          if (plan.state !== "running") throw new Error(`sequence ${plan.state} at step ${plan.index + 1}: ${plan.detail}`);
          await sleep(300, signal);
        }
        throw new Error("the sequence did not finish in time");
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
  ];
}

// ---------------------------------------------------------------------------
// Copilot

export function systemPrompt(lang) {
  return [
    "You are Copilot, the assistant built into ANet Relay, a ground station for small quadcopters on a local network.",
    "You can read aircraft status and fly aircraft with the relay_* tools. Simulated aircraft have ids sim-N; real ones real-<ip>.",
    "Units: metres, seconds, degrees. Moves are relative to the aircraft's nose; yaw is positive clockwise (right).",
    "Rules:",
    "- For several actions in a row (for example take off, hover, move, turn, land), plan them and call relay_plan once.",
    "- Before flying, check relay_fleet or relay_vehicle when you are not sure which aircraft is meant or whether it is ready.",
    "- If the user did not name an aircraft, use the selected aircraft given in the message context; if none, ask.",
    "- A person can possess any aircraft at any time and always wins. If a tool reports possessed_by_human, stop and tell the user to release the aircraft first. Never retry around it.",
    "- Real aircraft only move after the operator approves each command in the chat; if they decline, do not insist.",
    "- Without a position estimate horizontal moves are open-loop estimates: say so when you report distances.",
    "- Use relay_stop only for an emergency the user asks for. Prefer relay_land.",
    "- Keep answers short and factual. Report what was flown and the results, not your reasoning.",
    lang === "zh" ? "Answer in Simplified Chinese." : "Answer in the language the user writes in.",
  ].join("\n");
}

function summarise(name, args) {
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
    default:
      return { action: name };
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
export function createCopilot({ relay, models, model, emit, lang = () => "en" }) {
  const approvals = new Map();
  let approvalSeq = 0;
  let vehicleKinds = new Map();

  const kindOf = async (id) => {
    if (!vehicleKinds.has(id)) {
      try {
        const v = await relay.call("GET", `/vehicles/${encodeURIComponent(id)}`);
        vehicleKinds.set(id, { kind: v.kind, label: v.label });
      } catch {
        return null;
      }
    }
    return vehicleKinds.get(id);
  };

  const requestApproval = (toolCallId, name, args, info) =>
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
      emit({ type: "approval", id, toolCallId, tool: name, vehicle: args.vehicle, label: info.label, request: summarise(name, args) });
    });

  const agent = new Agent({
    initialState: { systemPrompt: systemPrompt(lang()), model, tools: relayTools(relay), thinkingLevel: "off" },
    streamFn: models.streamSimple.bind(models),
    toolExecution: "sequential",
    identity: { attribution: null },
    beforeToolCall: async ({ toolCall, args }) => {
      if (!MOTION_TOOLS.has(toolCall.name)) return undefined;
      const info = await kindOf(args.vehicle);
      if (!info || info.kind !== "real") return undefined;
      const ok = await requestApproval(toolCall.id, toolCall.name, args, info);
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
      const prefix = context.vehicle ? `[Selected aircraft: ${context.vehicle}]\n` : "";
      await agent.prompt(prefix + userText);
    },
    abort() {
      for (const resolve of approvals.values()) resolve(false);
      agent.abort();
    },
    reset() {
      this.abort();
      agent.reset();
      agent.state.tools = relayTools(relay);
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
