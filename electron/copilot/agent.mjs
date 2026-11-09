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

import { PROGRAMS_GUIDE } from "./programs-guide.mjs";
import { appTools } from "./tools-app.mjs";
import { compactVehicle, defineTool, enc, flightHelpers, SESSION_NAME, text, Type, VID } from "./tools-common.mjs";
import { dangerResolver, deviceTools } from "./tools-device.mjs";
import { PROGRAM_DANGER, programTools } from "./tools-programs.mjs";
import { matchTools } from "./tools-match.mjs"; // WP-B (07 §3-§4)

export const APIS = ["openai-completions", "anthropic-messages", "openai-responses"];
/** 08 §2: auto leaves thinking to the endpoint; off, low and high declare a reasoning model and set it. */
export const THINKING = ["auto", "off", "low", "high"];
export const MAX_TOKENS = { min: 1024, max: 131072, default: 16384 };
const PROVIDER = "relay-copilot";
const TRACE_TEXT = 12000; // characters of tool arguments and results kept for the run trace (08 §3)
const THINKING_FLUSH_MS = 120;
const DRAFT_EVERY_MS = 250;
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
  "relay_plan_tune",
  "relay_sticks_for",
  "relay_refly",
]);
export const TOOL_SCOPES = ["flight", "flight+device", "all"];
// WP-4b (06 §5, §6): arming and manual modes for stick and program flight; approved like motion on a real aircraft.
const RELAY_DANGER = { relay_arm: "arm", relay_mode: "motion" };
export const FLIGHT_LEVELS = ["sticks", "full"];
/** Flight tools an agent session may still use at agent_flight_level "sticks" (06 §5; as the flight MCP server). */
export const STICKS_TOOLS = new Set([
  "relay_fleet",
  "relay_vehicle",
  "relay_arm",
  "relay_disarm",
  "relay_mode",
  "relay_land",
  "relay_cancel",
  "relay_release",
  "relay_stop",
  "relay_sticks_for",
]);
/** Device tools Relay refuses at "sticks" for every session (attitude and velocity targets fly for the agent). */
const STICKS_HIDDEN_DEVICE = new Set(["device_attitude", "device_velocity"]);

// ---------------------------------------------------------------------------
// Model

/** A pi Models collection with one provider/model built from the user's endpoint settings. */
export function buildModels(cfg, apiKey) {
  if (!APIS.includes(cfg.api)) throw new Error(`unsupported API ${cfg.api}`);
  const thinking = THINKING.includes(cfg.thinking) ? cfg.thinking : "auto";
  const maxTokens = Number.isFinite(cfg.maxTokens) ? Math.min(MAX_TOKENS.max, Math.max(MAX_TOKENS.min, Math.round(cfg.maxTokens))) : MAX_TOKENS.default;
  const model = {
    id: cfg.model,
    name: cfg.model,
    api: cfg.api,
    provider: PROVIDER,
    baseUrl: cfg.baseUrl,
    // 08 §2: a reasoning model only when the operator chose a thinking level, so that "off" can switch it off
    reasoning: thinking !== "auto",
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 128_000,
    maxTokens,
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
  return { models, model: models.getModel(PROVIDER, cfg.model), thinking };
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

/** 09 §3: throttle sticks of the stick-flown sequence (backend plans.TUNING_RANGES). */
const TUNING = Type.Object(
  {
    climb: Type.Optional(Type.Number({ minimum: 0.55, maximum: 1, description: "throttle stick for lift-off and climb" })),
    hover: Type.Optional(Type.Number({ minimum: 0.35, maximum: 0.65, description: "throttle stick that holds the height" })),
  },
  { description: "climb must stay at least 0.05 above hover" },
)
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
    // WP-4b: stick and program flight start with a manual mode and arming (06 §5)
    defineTool(
      "relay_arm",
      "Arm",
      "Arm the motors (claims the aircraft first). For stick or program flight: set a manual mode with relay_mode first. A real aircraft waits for the operator's approval.",
      Type.Object({ vehicle: VID }),
      async (p) => {
        await claimFor(p.vehicle);
        return relay.call("POST", `/vehicles/${enc(p.vehicle)}/arm`, { arm: true, confirm: "ARM" });
      },
    ),
    defineTool("relay_disarm", "Disarm", "Disarm the motors (refused while flying; use relay_land).", Type.Object({ vehicle: VID }), async (p) => {
      await claimFor(p.vehicle);
      return relay.call("POST", `/vehicles/${enc(p.vehicle)}/arm`, { arm: false });
    }),
    defineTool(
      "relay_mode",
      "Set flight mode",
      "Switch the flight mode by name. Manual modes for sticks and programs: STABILIZE or ACRO (ArduPilot), MANUAL or STABILIZED (PX4). At the sticks flight level altitude, position and automatic modes are refused.",
      Type.Object({ vehicle: VID, name: Type.String() }),
      async (p) => {
        await claimFor(p.vehicle);
        return relay.call("POST", `/vehicles/${enc(p.vehicle)}/mode`, { name: p.name });
      },
    ),
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
        "Prefer this for multi-step requests. A failed step stops the sequence and the aircraft holds. " +
        "Aircraft without a position estimate fly it on the throttle stick in altitude hold: tuning {climb 0.55-1 (default 0.72), hover 0.35-0.65 (default 0.5)} sets the lift-off/climb stick and the stick that holds height; raise climb when it does not lift off, hover when it sinks.",
      parameters: Type.Object({ vehicle: VID, steps: Type.Array(STEP, { minItems: 1, maxItems: 24 }), tuning: Type.Optional(TUNING) }),
      executionMode: "sequential",
      execute: async (_id, p, signal, onUpdate) => {
        await claimFor(p.vehicle);
        const started = await relay.call("POST", `/vehicles/${enc(p.vehicle)}/plan`, {
          steps: p.steps,
          name: "copilot",
          confirm: "ARM",
          ...(p.tuning ? { tuning: p.tuning } : {}),
        });
        return text(await waitPlan(p.vehicle, started.plan.id, signal, onUpdate));
      },
    },
    {
      name: "relay_plan_tune",
      label: "Tune sequence throttle",
      description:
        "Change the throttle tuning {climb?, hover?} of the aircraft's sequence while it flies (also the hold it left behind); the step in flight takes it at once. " +
        "Read relay_vehicle (altitude_m, climb_ms, throttle_pct) to judge: no lift-off means climb is too low, sinking in a hover means hover is too low.",
      parameters: Type.Object({ vehicle: VID, tuning: TUNING }),
      execute: async (_id, p) => {
        await claimFor(p.vehicle);
        return text(await relay.call("PATCH", `/vehicles/${enc(p.vehicle)}/plan/tuning`, p.tuning));
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

/**
 * The tool list for a scope setting (04 §6.2): flight, flight+device or all. Program tools are in every scope; at
 * agent_flight_level "sticks" (06 §5) the tools Relay would refuse are left out.
 */
export function toolsFor(relay, scope = "all", danger = dangerResolver(relay), level = "full") {
  const sticks = level !== "full";
  const tools = relayTools(relay).filter((t) => !sticks || STICKS_TOOLS.has(t.name));
  tools.push(...programTools(relay));
  tools.push(...matchTools(relay)); // WP-B: dogfight and leaderboard, in every scope
  if (scope === "flight") return tools;
  tools.push(...deviceTools(relay, danger).filter((t) => !sticks || !STICKS_HIDDEN_DEVICE.has(t.name)));
  if (scope === "flight+device") return tools;
  tools.push(...appTools(relay));
  return tools;
}

export function systemPrompt(lang, scope = "all", level = "full") {
  const sticks = level !== "full";
  const parts = [
    "You are Copilot, the assistant built into ANet Relay, a ground station for small quadcopters on a local network.",
    "Simulated aircraft have ids sim-N; real ones real-<ip>.",
    "Units: metres, seconds, degrees. Moves are relative to the aircraft's nose; yaw is positive clockwise (right).",
  ];
  if (sticks) {
    parts.push(
      "Flight level: the operator has set the agent flight level to sticks. You control flight only through sticks or onboard programs: high-level flight (take-off, hover, move, turn, goto, sequences, re-fly, altitude and position hold modes) is not available to you and Relay refuses it with sticks_only. For stable flight, write a program and deploy it; do not try to fly a closed loop with stick pushes.",
    );
  }
  parts.push(
    "Tool sets:",
    sticks
      ? "- relay_* (flight): read status, set a manual mode, arm and disarm, push the sticks for a few seconds (relay_sticks_for), land as a safety action, release the claim and stop."
      : "- relay_* (flight): read status and fly with takeoff, hover, move, yaw, land, whole sequences (relay_plan), timed stick pushes (relay_sticks_for), arming, manual modes and flying a recorded flight again (relay_refly).",
    "- program_*, challenge_*, job_status, duel_run, ladder, airframe_set (programs): write, check, test, judge and deploy onboard flight programs that fly the aircraft at 50 Hz, tune them live, read their status and log, run duels, and set simulator airframe defects. The guide at the end of these instructions explains how to write them.",
    // WP-B (07 §3-§4)
    "- dogfight_start, dogfight_stop, dogfight_status, leaderboard (arena): start a timed dogfight of simulated aircraft in one call (Relay adds the simulators, arms them and deploys a program per role; the programs fly with sticks only), stop it, read its state and the leaderboard of this terminal and its LAN peers.",
  );
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
    sticks
      ? "- To fly: claim, relay_mode to a manual mode (STABILIZE), relay_arm, then program_deploy a tested program; watch program_status and program_log and tune with program_params. relay_sticks_for is only for a short open-loop push."
      : "- For several actions in a row (for example take off, hover, move, turn, land), plan them and call relay_plan once.",
    "- Programs: write -> program_validate -> program_test -> challenge_submit or a simulator -> program_deploy -> program_params. Editing programs needs no approval; deploying, resuming or retuning (program_params) one on a real aircraft waits for the operator. Releasing the claim, pushing sticks yourself or starting a skill ends your program.",
    "- Before flying, check relay_fleet or relay_vehicle when you are not sure which aircraft is meant or whether it is ready.",
    // WP-B (07 §3): phrases that mean a dogfight go straight to dogfight_start; it sets up the aircraft itself
    '- Dogfights: call dogfight_start directly, without arming or deploying anything first. "开一场 6 机混战，4 架开火 2 架撞击" or "a six-aircraft free-for-all, four shooting and two ramming" -> dogfight_start {aircraft: 6, teams: "ffa", roles: {gunner: 4, rammer: 2}}; "两队对抗" / "two teams" -> teams: 2; "三队" -> teams: 3; "打 5 分钟" -> duration_s: 300; "不复活" -> respawn: false; "用困难猎手" -> programs: {gunner: "hunter_hard"}. "谁击落最多" / "排行榜" -> leaderboard {by: "downs"}; "谁活得最久" -> by: "survival"; "命中率" -> by: "accuracy"; "撞毁最多" -> by: "rams". Simulators only: never put a real aircraft in a dogfight.',
    // 08 §5: custom fights with per-aircraft roles, programs, teams and position sources
    '- Custom fights: when aircraft need different programs, targets or sensors, call dogfight_start once with entrants (one entry per aircraft) instead of adding simulators and deploying programs yourself; it sets up the simulators, practice arena (so rounds count), teams, sensors, arming and programs. Put attackers on one team and their target on another so they only chase it. "全局状态 / global status / knows where it is" -> position: "gps"; "只有视角 / only its own view" -> position: "none" (it sees other aircraft only as contacts). Example: "三架飞机，A 尽量飞 8 字并优先躲避、可获取全局状态，一架撞毁 A，一架用子弹打掉 A" -> dogfight_start {entrants: [{role: "pilot", program: "figure8_evader", team: "A", position: "gps", label: "A"}, {role: "rammer", team: "B"}, {role: "gunner", team: "B"}]}. figure8_evader is a built-in example that flies a figure eight on its position source and dodges; to change its behaviour, copy it with program_create, edit and test it, then pass your program id. Report the aircraft ids and roles, then watch dogfight_status and leaderboard.',
    "- If the user did not name an aircraft, use the selected aircraft given in the message context; if none, ask.",
    "- A person can possess any aircraft at any time and always wins. If a tool reports possessed_by_human, stop and tell the user to release the aircraft first. Never retry around it.",
    "- Real aircraft only move after the operator approves each command in the chat; this covers every motion tool, arming, mode changes, program deployment and every device tool that can spin a motor, drive an output, override RC, reboot, calibrate, terminate, force-arm, write a parameter or fly a target. If they decline, do not insist. The approval is the confirmation: never ask the user to type a confirm word.",
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
  return `${parts.join("\n")}\n\n${PROGRAMS_GUIDE}`;
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
// Approval ids stay unique across agent rebuilds, so a card from an earlier conversation never matches a new one.
let approvalSeq = 0;

// Run numbers stay unique across agent rebuilds, like approval ids.
let runSeq = 0;

/** "{...}" of a tool call's arguments for the trace, cut at TRACE_TEXT characters. */
const traceJson = (value) => {
  try {
    const out = JSON.stringify(value ?? {}, null, 1);
    return out.length > TRACE_TEXT ? `${out.slice(0, TRACE_TEXT)}…` : out;
  } catch {
    return "";
  }
};

const usageOf = (u) =>
  u
    ? {
        input: u.input ?? 0,
        output: u.output ?? 0,
        reasoning: u.reasoning ?? null,
        cacheRead: u.cacheRead ?? 0,
        total: u.totalTokens ?? (u.input ?? 0) + (u.output ?? 0),
      }
    : null;

/**
 * The run trace (08 §3) from pi agent events: run, turn and tool timing, thinking in batches, the progress of tool
 * arguments the model is writing, and per-turn stop reasons and token usage. Calls `emit` with renderer events.
 */
export function createTracer(emit, { model = "", thinking = "auto", now = () => Date.now(), timers = { setTimeout, clearTimeout } } = {}) {
  let run = null;
  let pending = "";
  let flushTimer = null;
  let lastDraft = 0;

  const flush = () => {
    if (flushTimer) timers.clearTimeout(flushTimer);
    flushTimer = null;
    if (pending && run) emit({ type: "thinking", run: run.id, turn: run.turn, delta: pending });
    pending = "";
  };

  return {
    handle(event) {
      switch (event.type) {
        case "agent_start":
          run = { id: ++runSeq, at: now(), turn: 0, turnAt: 0, tools: 0, usage: { input: 0, output: 0, reasoning: 0, cacheRead: 0, total: 0 }, outcome: "done", toolAt: new Map() };
          emit({ type: "run_start", run: run.id, at: run.at, model, thinking });
          emit({ type: "busy", busy: true });
          break;
        case "message_start":
          if (event.message.role === "assistant" && run) {
            run.turn += 1;
            run.turnAt = now();
            lastDraft = 0;
            emit({ type: "assistant_start", run: run.id, turn: run.turn });
            emit({ type: "turn_start", run: run.id, turn: run.turn, at: run.turnAt });
          }
          break;
        case "message_update": {
          const e = event.assistantMessageEvent;
          if (!e || !run) break;
          if (e.type === "text_delta") {
            flush();
            emit({ type: "text", run: run.id, turn: run.turn, delta: e.delta });
          } else if (e.type === "thinking_delta") {
            pending += e.delta;
            flushTimer ??= timers.setTimeout(flush, THINKING_FLUSH_MS);
          } else if (e.type === "toolcall_start" || e.type === "toolcall_delta") {
            flush();
            const t = now();
            if (e.type === "toolcall_start" || t - lastDraft >= DRAFT_EVERY_MS) {
              lastDraft = t;
              const block = e.partial?.content?.[e.contentIndex];
              const chars = block?.partialJson?.length ?? block?.partialArgs?.length ?? JSON.stringify(block?.arguments ?? {}).length;
              emit({ type: "draft", run: run.id, turn: run.turn, tool: block?.name ?? "", chars });
            }
          }
          break;
        }
        case "message_end":
          if (event.message.role === "assistant" && run) {
            flush();
            const m = event.message;
            const content = m.content ?? [];
            const toolCalls = content.filter((c) => c.type === "toolCall").map((c) => ({ id: c.id, name: c.name, args: traceJson(c.arguments) }));
            const textChars = content.filter((c) => c.type === "text").reduce((n, c) => n + c.text.length, 0);
            const thinkingChars = content.filter((c) => c.type === "thinking").reduce((n, c) => n + (c.thinking?.length ?? 0), 0);
            const usage = usageOf(m.usage);
            if (usage) {
              for (const k of ["input", "output", "cacheRead", "total"]) run.usage[k] += usage[k];
              run.usage.reasoning += usage.reasoning ?? 0;
            }
            const error = m.stopReason === "error" ? m.errorMessage || "error" : null;
            const aborted = m.stopReason === "aborted";
            // 08 §1: a turn that ran out of output, or said nothing at all, is reported instead of vanishing
            const empty = !error && !aborted && textChars === 0 && toolCalls.length === 0;
            // the run ends the way its last turn ended
            if (error) run.outcome = "error";
            else if (aborted) run.outcome = "aborted";
            else if (m.stopReason === "length") run.outcome = "length";
            else if (empty) run.outcome = "empty";
            else run.outcome = "done";
            emit({
              type: "assistant_end",
              run: run.id,
              turn: run.turn,
              error,
              aborted,
              stopReason: m.stopReason ?? null,
              usage,
              ms: now() - run.turnAt,
              toolCalls,
              textChars,
              thinkingChars,
              empty,
            });
          }
          break;
        case "tool_execution_start":
          if (run) {
            run.tools += 1;
            run.toolAt.set(event.toolCallId, now());
          }
          emit({ type: "tool_start", run: run?.id, turn: run?.turn, at: now(), id: event.toolCallId, tool: event.toolName, args: event.args });
          break;
        case "tool_execution_update":
          emit({ type: "tool_update", id: event.toolCallId, text: event.partialResult?.content?.[0]?.text ?? "" });
          break;
        case "tool_execution_end": {
          const full = event.result?.content?.[0]?.text ?? "";
          const started = run?.toolAt.get(event.toolCallId);
          emit({
            type: "tool_end",
            id: event.toolCallId,
            tool: event.toolName,
            error: Boolean(event.isError),
            text: full.slice(0, 600),
            full: full.length > TRACE_TEXT ? `${full.slice(0, TRACE_TEXT)}…` : full,
            ms: started === undefined ? null : now() - started,
          });
          break;
        }
        case "agent_end":
          flush();
          if (run) {
            const at = now();
            emit({ type: "run_end", run: run.id, at, ms: at - run.at, turns: run.turn, tools: run.tools, usage: run.usage, outcome: run.outcome });
          }
          run = null;
          emit({ type: "busy", busy: false });
          break;
        default:
          break;
      }
    },
    /** A run the agent aborted before it ended (the window still gets run_end and busy false). */
    get running() {
      return run !== null;
    },
  };
}

export function createCopilot({ relay, models, model, emit, lang = () => "en", scope = () => "all", thinking = "auto", autoApprove = () => false }) {
  const approvals = new Map();
  let vehicleKinds = new Map();
  const danger = dangerResolver(relay);
  // agent_flight_level (06 §5): "sticks" until Relay says otherwise; read again before every prompt
  let flightLevel = "sticks";

  /** The approval level a tool call needs on a real aircraft, or null (04 §6.2). */
  const levelOf = async (name, args) => {
    if (MOTION_TOOLS.has(name)) return "motion";
    if (RELAY_DANGER[name]) return RELAY_DANGER[name];
    if (PROGRAM_DANGER[name]) return PROGRAM_DANGER[name];
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
    initialState: {
      systemPrompt: systemPrompt(lang(), scope(), flightLevel),
      model,
      tools: toolsFor(relay, scope(), danger, flightLevel),
      // 08 §2: low and high ask for that effort; auto and off send none (off also switches a reasoning model off)
      thinkingLevel: thinking === "low" || thinking === "high" ? thinking : "off",
    },
    streamFn: models.streamSimple.bind(models),
    toolExecution: "sequential",
    identity: { attribution: null },
    beforeToolCall: async ({ toolCall, args }) => {
      const level = await levelOf(toolCall.name, args);
      if (!level) return undefined;
      const info = await kindOf(args.vehicle);
      // fail closed: an aircraft Copilot cannot identify is treated as real
      if (info && info.kind !== "real") return undefined;
      if (!info) return { block: true, reason: "Could not read this aircraft from Relay to check whether it is real. Check the id with relay_fleet and try again." };
      if (autoApprove()) {
        // 09 §4: "Allow all" is on; the chat still records what was approved on the operator's behalf
        const id = `ap-${++approvalSeq}`;
        emit({ type: "approval", id, toolCallId: toolCall.id, tool: toolCall.name, vehicle: args.vehicle, label: info.label, request: summarise(toolCall.name, args, level), auto: true });
        emit({ type: "approval_closed", id, approved: true, reason: "auto" });
        return undefined;
      }
      const ok = await requestApproval(toolCall.id, toolCall.name, args, info, level);
      return ok ? undefined : { block: true, reason: "The operator declined this command. Do not retry it unless asked." };
    },
  });

  const tracer = createTracer(emit, { model: model?.id ?? "", thinking });
  agent.subscribe((event) => tracer.handle(event));

  /** The level Relay reports (a Relay without the setting flies everything); the last one read when unreachable. */
  const readLevel = async () => {
    try {
      const settings = await relay.call("GET", "/settings");
      const level = settings?.agent_flight_level;
      return FLIGHT_LEVELS.includes(level) ? level : "full";
    } catch {
      return flightLevel;
    }
  };

  /** Swap the tools and the system prompt when the operator changed the flight level between prompts. */
  const applyLevel = (level, force = false) => {
    if (level === flightLevel && !force) return;
    flightLevel = level;
    agent.state.tools = toolsFor(relay, scope(), danger, level);
    const [head, ...rest] = agent.state.messages;
    const prompt = systemPrompt(lang(), scope(), level);
    agent.state.messages = head?.role === "system" ? [{ ...head, content: prompt }, ...rest] : agent.state.messages;
  };

  return {
    get flightLevel() {
      return flightLevel;
    },
    async prompt(userText, context = {}) {
      vehicleKinds = new Map();
      applyLevel(await readLevel());
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
      applyLevel(flightLevel, true);
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
