// Copilot against a running backend with pi's faux provider (scripted model, no network).
//   RELAY_API=http://127.0.0.1:8765 RELAY_OPERATOR_TOKEN=... node --test electron/copilot/test/copilot.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

import { createModels } from "@earendil-works/pi-ai";
import { fauxAssistantMessage, fauxProvider, fauxText, fauxToolCall } from "@earendil-works/pi-ai/providers/faux";

import fs from "node:fs";

import { createCopilot, RelayHttp, relayTools, systemPrompt, toolsFor } from "../agent.mjs";
import { dangerResolver } from "../tools-device.mjs";
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
      assert.match(t.name, /^(relay|device|app)_[a-z_]+$/);
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
