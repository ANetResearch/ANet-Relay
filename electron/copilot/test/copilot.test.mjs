// Copilot against a running backend with pi's faux provider (scripted model, no network).
//   RELAY_API=http://127.0.0.1:8765 RELAY_OPERATOR_TOKEN=... node --test electron/copilot/test/copilot.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

import { createModels } from "@earendil-works/pi-ai";
import { fauxAssistantMessage, fauxProvider, fauxText, fauxToolCall } from "@earendil-works/pi-ai/providers/faux";

import fs from "node:fs";

import { buildModels, createCopilot, createTracer, RelayHttp, relayTools, systemPrompt, toolsFor } from "../agent.mjs";
import { confirmFor, dangerResolver } from "../tools-device.mjs";
import { DEFAULT_HEIGHT, DEFAULT_WIDTH, defaultBounds, fitBounds, MIN_HEIGHT, MIN_WIDTH } from "../window-bounds.mjs";

const BASE = process.env.RELAY_API || "http://127.0.0.1:8765";
const OPERATOR = process.env.RELAY_OPERATOR_TOKEN || "";

async function operator(method, path, body) {
  const res = await fetch(`${BASE}/api/v1${path}`, {
    method,
    headers: { "content-type": "application/json", "X-Relay-Operator": OPERATOR },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return res.json();
}

function setup(responses) {
  const faux = fauxProvider();
  const models = createModels();
  models.setProvider(faux.provider);
  faux.setResponses(responses);
  const events = [];
  const relay = new RelayHttp(BASE);
  const copilot = createCopilot({ relay, models, model: faux.getModel(), emit: (e) => events.push(e) });
  return { copilot, relay, events };
}

test("tools are well formed", () => {
  const tools = relayTools(new RelayHttp(BASE));
  assert.ok(tools.length >= 10);
  for (const t of tools) assert.ok(t.name && t.label && t.description && t.parameters && t.execute);
});

// ---------------------------------------------------------------------------
// Offline tests (04 §6): scripted model and a scripted Relay API, no backend needed

/** A Relay client stub: routes "METHOD path" to a handler and records every call. */
function stubRelay(routes) {
  const calls = [];
  return {
    calls,
    async call(method, path, body) {
      calls.push({ method, path, body });
      const handler = routes[`${method} ${path}`] ?? routes[`${method} ${path.split("?")[0]}`];
      if (handler === undefined) throw new Error(`stub: no route for ${method} ${path}`);
      return typeof handler === "function" ? handler(body) : handler;
    },
  };
}

const REAL = { id: "real-1", label: "Quad 01", kind: "real", claim: null, possession: null };
const SIM = { id: "sim-1", label: "SIM-1", kind: "sim", claim: null, possession: null };
const CATALOGUE = {
  commands: [
    { id: 183, name: "MAV_CMD_DO_SET_SERVO", danger: null },
    { id: 246, name: "MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN", danger: "reboot", confirm: "REBOOT" },
  ],
};

function offlineCopilot(routes, responses, scope = "all") {
  const faux = fauxProvider();
  const models = createModels();
  models.setProvider(faux.provider);
  faux.setResponses(responses);
  const relay = stubRelay(routes);
  const events = [];
  const copilot = createCopilot({ relay, models, model: faux.getModel(), emit: (e) => events.push(e), scope: () => scope });
  return { copilot, relay, events };
}

test("every scope has well-formed, uniquely named tools with a display label", () => {
  const labels = fs.readFileSync(new URL("../../../web/src/copilot/toolLabels.ts", import.meta.url), "utf8");
  const sizes = {};
  for (const scope of ["flight", "flight+device", "all"]) {
    const tools = toolsFor(new RelayHttp(BASE), scope);
    sizes[scope] = tools.length;
    assert.equal(new Set(tools.map((t) => t.name)).size, tools.length, `${scope}: duplicate names`);
    for (const t of tools) {
      assert.ok(t.name && t.label && t.description && t.parameters && t.execute, t.name);
      assert.match(t.name, /^((relay|device|app|program|challenge|job|duel|airframe|dogfight)_[a-z_]+|ladder|leaderboard)$/);
      assert.ok(labels.includes(`${t.name}:`), `${t.name} has no entry in web/src/copilot/toolLabels.ts`);
    }
  }
  assert.ok(sizes.flight < sizes["flight+device"] && sizes["flight+device"] < sizes.all);
  const names = (scope) => new Set(toolsFor(new RelayHttp(BASE), scope).map((t) => t.name));
  assert.ok(names("flight").has("relay_sticks_for") && names("flight").has("relay_refly"));
  assert.ok(!names("flight").has("device_motor_test") && names("flight+device").has("device_motor_test"));
  assert.ok(!names("flight+device").has("app_set_language") && names("all").has("app_set_language"));
  assert.ok(names("all").has("app_screenshot") && names("all").has("device_attitude"));
});

test("the system prompt describes the tool sets of the scope", () => {
  assert.doesNotMatch(systemPrompt("en", "flight"), /device_\*|app_\*/);
  assert.match(systemPrompt("en", "flight+device"), /device_\*/);
  assert.doesNotMatch(systemPrompt("en", "flight+device"), /app_\*/);
  const all = systemPrompt("zh", "all");
  assert.match(all, /relay_\*/);
  assert.match(all, /device_\*/);
  assert.match(all, /app_\*/);
  assert.match(all, /approves/);
  assert.match(all, /Simplified Chinese/);
});

test("danger levels: static tools, force arm and raw commands from the catalogue", async () => {
  const relay = stubRelay({ "GET /commands": CATALOGUE });
  const danger = dangerResolver(relay);
  assert.equal(await danger("device_motor_test", {}), "motor");
  assert.equal(await danger("device_actuator", {}), "motor");
  assert.equal(await danger("device_rc", {}), "rc");
  assert.equal(await danger("device_arm", {}), "arm");
  assert.equal(await danger("device_arm", { force: true }), "force_arm");
  assert.equal(await danger("device_overview", {}), null);
  assert.equal(await danger("device_command", { command: "MAV_CMD_DO_SET_SERVO" }), null);
  assert.equal(await danger("device_command", { command: 246 }), "reboot");
  assert.equal(await danger("device_command", { command: "MAV_CMD_NOT_LISTED" }), "raw");
});

test("danger levels follow the parameters, mode changes are motion, and an unreadable catalogue is not cached", async () => {
  const catalogue = {
    commands: [
      { id: 183, name: "MAV_CMD_DO_SET_SERVO", danger: null },
      { id: 400, name: "MAV_CMD_COMPONENT_ARM_DISARM", danger: "arm", confirm: "ARM" },
      { id: 176, name: "MAV_CMD_DO_SET_MODE", danger: null },
      { id: 511, name: "MAV_CMD_SET_MESSAGE_INTERVAL", danger: null },
    ],
  };
  let fail = true;
  const relay = stubRelay({
    "GET /commands": () => {
      if (fail) throw new Error("backend busy");
      return catalogue;
    },
  });
  const danger = dangerResolver(relay);
  assert.equal(await danger("device_command", { command: "DO_SET_MODE" }), "raw", "unreadable: dangerous");
  fail = false;
  assert.equal(await danger("device_command", { command: "DO_SET_MODE" }), "motion", "read again once it answers");
  assert.equal(await danger("device_command", { command: "MAV_CMD_DO_SET_SERVO", params: [1, 1500] }), "motor");
  assert.equal(await danger("device_command", { command: "MAV_CMD_DO_SET_SERVO", params: [9, 1500] }), null);
  assert.equal(await danger("device_command", { command: 400, params: [1, 21196] }), "force_arm");
  assert.equal(await danger("device_command", { command: 400, params: [1] }), "arm");
  assert.equal(await danger("device_command", { command: "SET_MESSAGE_INTERVAL", params: [30, 1e5] }), null);
  assert.equal(await danger("device_mode", { name: "LAND" }), "motion");
  assert.equal(confirmFor("force_arm", catalogue.commands[1]), "FORCE");
  assert.equal(confirmFor("arm", catalogue.commands[1]), "ARM");
});

test("an aircraft Copilot cannot look up is treated as real: the command is blocked", async () => {
  const routes = {
    "GET /app/ui": { state: { selected: null, lang: "en" } },
    "GET /vehicles/real-9": () => {
      throw new Error("backend busy");
    },
    "GET /commands": CATALOGUE,
  };
  const { copilot, relay, events } = offlineCopilot(routes, [
    fauxAssistantMessage(fauxToolCall("device_motor_test", { vehicle: "real-9", motor: 1, throttle_pct: 5, seconds: 1 }), { stopReason: "toolUse" }),
    fauxAssistantMessage("Could not check the aircraft."),
  ]);
  await copilot.prompt("spin motor 1 on real-9");
  assert.ok(!relay.calls.some((c) => c.path.endsWith("/motors/test")), "nothing sent");
  const end = events.find((e) => e.type === "tool_end" && e.tool === "device_motor_test");
  assert.equal(end.error, true);
  assert.match(end.text, /Could not read this aircraft/);
});

test("device tools claim, send the confirm word and report the result", async () => {
  const relay = stubRelay({
    "GET /vehicles/sim-1": SIM,
    "POST /vehicles/sim-1/claim": {},
    "POST /vehicles/sim-1/motors/test": { ok: true },
    "GET /commands": CATALOGUE,
    "POST /vehicles/sim-1/command": { command: "MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN", ack: { result: 0, result_name: "ACCEPTED" } },
  });
  const tools = Object.fromEntries(toolsFor(relay, "all").map((t) => [t.name, t]));
  const out = await tools.device_motor_test.execute("1", { vehicle: "sim-1", motor: 2, throttle_pct: 10, seconds: 1 });
  assert.match(out.content[0].text, /"ok":true/);
  const post = relay.calls.find((c) => c.path === "/vehicles/sim-1/motors/test");
  assert.deepEqual(post.body, { motor: 2, throttle_pct: 10, seconds: 1, confirm: "MOTOR" });
  assert.ok(relay.calls.some((c) => c.path === "/vehicles/sim-1/claim"), "claimed first");

  await tools.device_command.execute("2", { vehicle: "sim-1", command: 246, params: [1] });
  const cmd = relay.calls.filter((c) => c.path === "/vehicles/sim-1/command").at(-1);
  assert.equal(cmd.body.confirm, "REBOOT");
});

test("bench prompts: the danger follows the prompt and the answer carries its confirm word (WP-B)", async () => {
  const run = (prompt) => ({ step: "S4", steps: [{ id: "S4", state: "waiting", prompt, live: { t: [1] } }] });
  let prompt = { id: "calibrate_gyro", confirm_word: "CALIBRATE", operator_only: false };
  const relay = stubRelay({
    "GET /vehicles/real-1": REAL,
    "POST /vehicles/real-1/claim": {},
    "GET /vehicles/real-1/procedure": () => run(prompt),
    "POST /vehicles/real-1/procedure/answer": { state: "running" },
  });
  const danger = dangerResolver(relay);
  assert.equal(await danger("device_procedure_answer", { vehicle: "real-1", step: "S4", answer: "start" }), "calibrate");
  assert.equal(await danger("device_procedure_answer", { vehicle: "real-1", step: "S4", answer: "skip" }), null);
  assert.equal(await danger("device_procedure_start", { vehicle: "real-1" }), "motion");
  assert.equal(await danger("device_procedure_status", { vehicle: "real-1" }), null);
  const tools = Object.fromEntries(toolsFor(relay, "all").map((t) => [t.name, t]));
  await tools.device_procedure_answer.execute("1", { vehicle: "real-1", step: "S4", answer: "start" });
  const post = relay.calls.find((c) => c.path === "/vehicles/real-1/procedure/answer");
  assert.deepEqual(post.body, { step: "S4", answer: "start", confirm: "CALIBRATE" });
  const status = await tools.device_procedure_status.execute("2", { vehicle: "real-1" });
  assert.doesNotMatch(status.content[0].text, /"live"/);
  prompt = { id: "arm_start", confirm_word: "ARM", operator_only: false };
  assert.equal(await danger("device_procedure_answer", { vehicle: "real-1", step: "S4", answer: "start" }), "arm");
});

test("a person who possesses the aircraft blocks device writes", async () => {
  const relay = stubRelay({ "GET /vehicles/sim-1": { ...SIM, possession: { input: "keyboard" } } });
  const tools = Object.fromEntries(toolsFor(relay, "all").map((t) => [t.name, t]));
  await assert.rejects(() => tools.device_motor_test.execute("1", { vehicle: "sim-1", motor: 1, throttle_pct: 5, seconds: 1 }), /possessed_by_human/);
});

test("app tools post interface actions to /app/ui", async () => {
  const relay = stubRelay({ "POST /app/ui": (body) => ({ ok: true, data: { lang: body.args.lang } }), "GET /app/ui": { state: { selected: "sim-1" } } });
  const tools = Object.fromEntries(toolsFor(relay, "all").map((t) => [t.name, t]));
  const out = await tools.app_set_language.execute("1", { lang: "zh" });
  assert.match(out.content[0].text, /"lang":"zh"/);
  assert.deepEqual(relay.calls[0], { method: "POST", path: "/app/ui", body: { action: "set_language", args: { lang: "zh" } } });
  await tools.app_ui_state.execute("2", {});
  assert.equal(relay.calls[1].path, "/app/ui");
});

test("the screenshot tool never hands the image to the model", async () => {
  const relay = stubRelay({ "POST /app/ui": { ok: true, data: { png_base64: "A".repeat(400), width: 800, height: 500 } } });
  const tools = Object.fromEntries(toolsFor(relay, "all").map((t) => [t.name, t]));
  const out = await tools.app_screenshot.execute("1", { max_width: 800 });
  const text = out.content[0].text;
  assert.doesNotMatch(text, /AAAA/);
  assert.match(text, /"width":800/);
});

test("a real aircraft needs approval for a motor test; declining blocks the call", async () => {
  const routes = {
    "GET /app/ui": { state: { selected: "real-1", lang: "zh" } },
    "GET /vehicles/real-1": REAL,
    "GET /commands": CATALOGUE,
    "POST /vehicles/real-1/claim": {},
    "POST /vehicles/real-1/motors/test": { ok: true },
  };
  let userMessage = "";
  const { copilot, relay, events } = offlineCopilot(routes, [
    (context) => {
      userMessage = JSON.stringify(context.messages.at(-1));
      return fauxAssistantMessage(fauxToolCall("device_motor_test", { vehicle: "real-1", motor: 1, throttle_pct: 5, seconds: 1 }), { stopReason: "toolUse" });
    },
    fauxAssistantMessage("The operator declined."),
  ]);
  const pending = copilot.prompt("spin motor 1");
  for (let i = 0; i < 100 && !events.some((e) => e.type === "approval"); i++) await new Promise((r) => setTimeout(r, 20));
  const approval = events.find((e) => e.type === "approval");
  assert.ok(approval, "approval requested");
  assert.equal(approval.tool, "device_motor_test");
  assert.equal(approval.request.danger, "motor");
  assert.equal(approval.request.tool, "device_motor_test");
  assert.deepEqual(approval.request.args, { motor: 1, throttle_pct: 5, seconds: 1 });
  assert.equal(copilot.approve(approval.id, false), true);
  await pending;
  assert.ok(!relay.calls.some((c) => c.path.endsWith("/motors/test")), "declined: nothing sent");
  assert.match(userMessage, /Selected aircraft: real-1/);
  assert.match(userMessage, /Interface language: zh/);
  const end = events.find((e) => e.type === "tool_end" && e.tool === "device_motor_test");
  assert.equal(end.error, true);
});

test("approving runs the call; simulators and read tools never ask", async () => {
  const routes = {
    "GET /app/ui": { state: null },
    "GET /vehicles/real-1": REAL,
    "GET /vehicles/sim-1": SIM,
    "GET /commands": CATALOGUE,
    "POST /vehicles/real-1/claim": {},
    "POST /vehicles/sim-1/claim": {},
    "POST /vehicles/real-1/reboot": { ok: true },
    "POST /vehicles/sim-1/reboot": { ok: true },
    "GET /vehicles/real-1/device": { motors: { count: 4 } },
  };
  const { copilot, relay, events } = offlineCopilot(routes, [
    fauxAssistantMessage(fauxToolCall("device_overview", { vehicle: "real-1" }), { stopReason: "toolUse" }),
    fauxAssistantMessage(fauxToolCall("device_reboot", { vehicle: "sim-1" }), { stopReason: "toolUse" }),
    fauxAssistantMessage(fauxToolCall("device_reboot", { vehicle: "real-1" }), { stopReason: "toolUse" }),
    fauxAssistantMessage("Rebooted."),
  ]);
  const pending = copilot.prompt("reboot both", { vehicle: "sim-1" });
  for (let i = 0; i < 150 && !events.some((e) => e.type === "approval"); i++) await new Promise((r) => setTimeout(r, 20));
  const approvals = events.filter((e) => e.type === "approval");
  assert.equal(approvals.length, 1, "only the real aircraft asks");
  assert.equal(approvals[0].vehicle, "real-1");
  assert.equal(approvals[0].request.danger, "reboot");
  copilot.approve(approvals[0].id, true);
  await pending;
  const reboots = relay.calls.filter((c) => c.path.endsWith("/reboot"));
  assert.deepEqual(reboots.map((c) => c.path), ["/vehicles/sim-1/reboot", "/vehicles/real-1/reboot"]);
  assert.ok(reboots.every((c) => c.body.confirm === "REBOOT"));
  assert.ok(relay.calls.some((c) => c.path === "/vehicles/real-1/device"));
});

test("interface actions need no approval even with a real aircraft selected", async () => {
  const routes = { "GET /app/ui": { state: { selected: "real-1" } }, "POST /app/ui": { ok: true, data: { selected: "real-1" } } };
  const { copilot, events } = offlineCopilot(routes, [
    fauxAssistantMessage(fauxToolCall("app_select_vehicle", { vehicle: "real-1" }), { stopReason: "toolUse" }),
    fauxAssistantMessage("Selected."),
  ]);
  await copilot.prompt("select it");
  assert.ok(!events.some((e) => e.type === "approval"));
  assert.equal(events.find((e) => e.type === "tool_end" && e.tool === "app_select_vehicle").error, false);
});

test("window placement: default bottom-right, restore within the visible displays", () => {
  const primary = { x: 0, y: 0, width: 1920, height: 1080 };
  const d = defaultBounds(primary);
  assert.deepEqual(d, { x: 1920 - DEFAULT_WIDTH - 16, y: 1080 - DEFAULT_HEIGHT - 16, width: DEFAULT_WIDTH, height: DEFAULT_HEIGHT });
  // dragged to the left of the main window stays where it was
  const left = { x: 40, y: 120, width: 420, height: 600 };
  assert.deepEqual(fitBounds(left, [primary], primary), left);
  // a second display that was unplugged: back to the default place
  const gone = { x: 2200, y: 100, width: 420, height: 600 };
  assert.deepEqual(fitBounds(gone, [primary], primary), defaultBounds(primary, 420, 600));
  // on a second display that still exists
  const second = { x: 1920, y: 0, width: 1920, height: 1080 };
  assert.deepEqual(fitBounds(gone, [primary, second], primary), gone);
  // garbage and tiny sizes
  assert.deepEqual(fitBounds({ x: "a" }, [primary], primary), defaultBounds(primary));
  const tiny = fitBounds({ x: 10, y: 10, width: 50, height: 50 }, [primary], primary);
  assert.equal(tiny.width, MIN_WIDTH);
  assert.equal(tiny.height, MIN_HEIGHT);
  // title bar above the top edge of every display cannot be grabbed
  assert.deepEqual(fitBounds({ x: 100, y: -500, width: 420, height: 600 }, [primary], primary), defaultBounds(primary, 420, 600));
});

test("copilot flies the bench routine on a simulator as one plan", { skip: !OPERATOR }, async () => {
  await operator("PUT", "/settings", { agent_flight_level: "full" }); // 06 §5: high-level skills need "full"
  await operator("DELETE", "/sims");
  await operator("POST", "/sims", { count: 1, autopilot: "px4", position: false });
  await new Promise((r) => setTimeout(r, 3000));
  const steps = [
    { action: "takeoff", alt: 1 },
    { action: "hover", seconds: 1 },
    { action: "move", forward: 1 },
    { action: "move", forward: -1 },
    { action: "yaw", deg: 45 },
    { action: "land" },
  ];
  const { copilot, relay, events } = setup([
    fauxAssistantMessage([fauxText("Flying the sequence."), fauxToolCall("relay_plan", { vehicle: "sim-1", steps })], { stopReason: "toolUse" }),
    fauxAssistantMessage("Done: took off, hovered, moved forward and back about 1 m, turned 45 degrees and landed."),
  ]);
  try {
    await copilot.prompt("起飞，悬停，前进1米，后退1米，右转45度，降落", { vehicle: "sim-1" });
    const end = events.find((e) => e.type === "tool_end" && e.tool === "relay_plan");
    assert.ok(end, "plan tool ran");
    assert.equal(end.error, false, end.text);
    assert.match(end.text, /"state":"done"/);
    assert.ok(events.some((e) => e.type === "tool_update" && /step 3 of 6/.test(e.text)));
    assert.ok(events.some((e) => e.type === "text"));
  } finally {
    await relay.close();
  }
});

test("possession blocks copilot and it reports why", { skip: !OPERATOR }, async () => {
  await operator("POST", "/vehicles/sim-1/possess", { input: "keyboard" });
  const { copilot, relay, events } = setup([
    fauxAssistantMessage(fauxToolCall("relay_takeoff", { vehicle: "sim-1", alt: 1 }), { stopReason: "toolUse" }),
    fauxAssistantMessage("The operator is flying SIM-1; release it to hand it over."),
  ]);
  try {
    await copilot.prompt("take off", { vehicle: "sim-1" });
    const end = events.find((e) => e.type === "tool_end" && e.tool === "relay_takeoff");
    assert.equal(end.error, true);
    assert.match(end.text, /possessed_by_human/);
  } finally {
    await operator("POST", "/vehicles/sim-1/release");
    await relay.close();
  }
});

/** Manual timers for the tracer: flush() runs the pending thinking batch. */
function manualTimers() {
  let fn = null;
  return { timers: { setTimeout: (f) => ((fn = f), 1), clearTimeout: () => (fn = null) }, flush: () => fn?.() };
}

test("run trace (08 §3): runs, turns, batched thinking, drafts, usage and outcome", () => {
  const out = [];
  let clock = 1000;
  const mt = manualTimers();
  const tr = createTracer((e) => out.push(e), { model: "deepseek-flash", thinking: "auto", now: () => clock, timers: mt.timers });
  const assistant = (content, stopReason, usage) => ({ role: "assistant", content, stopReason, usage });
  tr.handle({ type: "agent_start" });
  tr.handle({ type: "message_start", message: { role: "user" } });
  tr.handle({ type: "message_start", message: { role: "assistant" } });
  tr.handle({ type: "message_update", assistantMessageEvent: { type: "thinking_delta", delta: "Pilot " } });
  tr.handle({ type: "message_update", assistantMessageEvent: { type: "thinking_delta", delta: "needs gps." } });
  mt.flush();
  const call = { type: "toolCall", id: "c1", name: "dogfight_start", arguments: {}, partialArgs: '{"entr' };
  tr.handle({ type: "message_update", assistantMessageEvent: { type: "toolcall_start", contentIndex: 0, partial: { content: [call] } } });
  clock += 4000;
  const usage = { input: 1200, output: 300, cacheRead: 100, cacheWrite: 0, reasoning: 250, totalTokens: 1600 };
  tr.handle({ type: "message_end", message: assistant([{ type: "thinking", thinking: "Pilot needs gps." }, { ...call, arguments: { entrants: [] } }], "toolUse", usage) });
  tr.handle({ type: "tool_execution_start", toolCallId: "c1", toolName: "dogfight_start", args: { entrants: [] } });
  clock += 900;
  tr.handle({ type: "tool_execution_end", toolCallId: "c1", toolName: "dogfight_start", result: { content: [{ type: "text", text: "x".repeat(20000) }] }, isError: false });
  tr.handle({ type: "message_start", message: { role: "assistant" } });
  tr.handle({ type: "message_end", message: assistant([], "length", usage) });
  tr.handle({ type: "agent_end" });

  const types = out.map((e) => e.type);
  assert.deepEqual(types, ["run_start", "busy", "assistant_start", "turn_start", "thinking", "draft", "assistant_end", "tool_start", "tool_end", "assistant_start", "turn_start", "assistant_end", "run_end", "busy"]);
  const [start] = out;
  assert.deepEqual([start.model, start.thinking, start.at], ["deepseek-flash", "auto", 1000]);
  assert.equal(out.find((e) => e.type === "thinking").delta, "Pilot needs gps.");
  assert.deepEqual(out.find((e) => e.type === "draft"), { type: "draft", run: start.run, turn: 1, tool: "dogfight_start", chars: 6 });
  const end1 = out.find((e) => e.type === "assistant_end");
  assert.equal(end1.stopReason, "toolUse");
  assert.equal(end1.ms, 4000);
  assert.equal(end1.usage.reasoning, 250);
  assert.equal(end1.toolCalls[0].name, "dogfight_start");
  assert.equal(end1.empty, false);
  const toolEnd = out.find((e) => e.type === "tool_end");
  assert.equal(toolEnd.ms, 900);
  assert.equal(toolEnd.text.length, 600);
  assert.ok(toolEnd.full.length <= 12001);
  const end2 = out.filter((e) => e.type === "assistant_end")[1];
  assert.deepEqual([end2.turn, end2.stopReason, end2.empty], [2, "length", true]);
  const runEnd = out.find((e) => e.type === "run_end");
  assert.deepEqual([runEnd.turns, runEnd.tools, runEnd.outcome], [2, 1, "length"]);
  assert.equal(runEnd.usage.output, 600);
  assert.equal(out.at(-1).busy, false);
});

test("thinking setting (08 §2): auto leaves the model non-reasoning, any level declares it", () => {
  const cfg = { api: "openai-completions", baseUrl: "https://api.deepseek.com/v1", model: "deepseek-flash" };
  const auto = buildModels(cfg, "k");
  assert.deepEqual([auto.thinking, auto.model.reasoning, auto.model.maxTokens], ["auto", false, 16384]);
  const off = buildModels({ ...cfg, thinking: "off", maxTokens: 999999 }, "k");
  assert.deepEqual([off.thinking, off.model.reasoning, off.model.maxTokens], ["off", true, 131072]);
  assert.equal(buildModels({ ...cfg, thinking: "bogus" }, "k").thinking, "auto");
});
