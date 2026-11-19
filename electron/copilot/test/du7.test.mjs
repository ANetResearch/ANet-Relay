// v0.6.0 (12 §3-§5): control frames, speed tracking, built-in plans and the gimbal pod in Copilot's tools and prompt.
// Offline: a scripted Relay API, no backend needed.
import assert from "node:assert/strict";
import { test } from "node:test";

import { relayTools, RelayHttp, systemPrompt, toolsFor } from "../agent.mjs";
import { gimbalTools, GIMBAL_ACTIONS } from "../tools-gimbal.mjs";

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

const SIM = { id: "sim-1", label: "SIM-1", kind: "sim", claim: null, possession: null };
const DONE = { ...SIM, skill: { id: 3, name: "move", state: "done", detail: null }, plan: { id: 5, name: "du7_box", state: "done", index: 5, count: 6, steps: [], results: [] } };
const byName = (tools) => Object.fromEntries(tools.map((t) => [t.name, t]));
const fly = (relay) => byName(toolsFor(relay, "all", undefined, "full"));
const flyRoutes = {
  "GET /vehicles/sim-1": DONE,
  "POST /vehicles/sim-1/claim": {},
  "POST /vehicles/sim-1/skills/takeoff": { skill: { id: 3 } },
  "POST /vehicles/sim-1/skills/move": { skill: { id: 3 } },
  "POST /vehicles/sim-1/skills/yaw": { skill: { id: 3 } },
  "POST /vehicles/sim-1/plan": { plan: { id: 5 } },
};

test("takeoff passes speed and settle_s only when given", async () => {
  const relay = stubRelay(flyRoutes);
  const tools = fly(relay);
  await tools.relay_takeoff.execute("1", { vehicle: "sim-1", alt: 2, speed: 0.5, settle_s: 1 });
  assert.deepEqual(relay.calls.find((c) => c.path.endsWith("/skills/takeoff")).body, { alt: 2, speed: 0.5, settle_s: 1, confirm: "ARM" });
  await tools.relay_takeoff.execute("1", { vehicle: "sim-1", alt: 1 });
  assert.deepEqual(relay.calls.filter((c) => c.path.endsWith("/skills/takeoff")).at(-1).body, { alt: 1, confirm: "ARM" });
});

test("move: named directions stay frame independent, x / y / z carry their frame, the old call is unchanged", async () => {
  const relay = stubRelay(flyRoutes);
  const tools = fly(relay);
  const body = () => relay.calls.filter((c) => c.path.endsWith("/skills/move")).at(-1).body;
  await tools.relay_move.execute("1", { vehicle: "sim-1", forward: 1 });
  assert.deepEqual(body(), { forward: 1, right: 0, up: 0 });
  await tools.relay_move.execute("1", { vehicle: "sim-1", left: 1, speed: 0.5 });
  assert.deepEqual(body(), { left: 1, speed: 0.5 });
  await tools.relay_move.execute("1", { vehicle: "sim-1", x: 1, y: -0.5, frame: "frd", settle_s: 0.5 });
  assert.deepEqual(body(), { x: 1, y: -0.5, frame: "frd", settle_s: 0.5 });
  await assert.rejects(() => tools.relay_move.execute("1", { vehicle: "sim-1", forward: 1, x: 1 }), /not both/);
});

test("yaw passes its frame; plan flies a built-in plan or steps, not both", async () => {
  const relay = stubRelay(flyRoutes);
  const tools = fly(relay);
  await tools.relay_yaw.execute("1", { vehicle: "sim-1", deg: 30 });
  assert.deepEqual(relay.calls.filter((c) => c.path.endsWith("/skills/yaw")).at(-1).body, { deg: 30 });
  await tools.relay_yaw.execute("1", { vehicle: "sim-1", deg: 30, frame: "relay" });
  assert.deepEqual(relay.calls.filter((c) => c.path.endsWith("/skills/yaw")).at(-1).body, { deg: 30, frame: "relay" });
  await tools.relay_plan.execute("1", { vehicle: "sim-1", builtin: "du7_box" }, undefined, () => {});
  assert.deepEqual(relay.calls.find((c) => c.path === "/vehicles/sim-1/plan").body, { builtin: "du7_box", name: "du7_box", confirm: "ARM" });
  const steps = [{ action: "takeoff", alt: 1 }, { action: "move", left: 1, frame: "flu" }];
  await tools.relay_plan.execute("1", { vehicle: "sim-1", steps }, undefined, () => {});
  assert.deepEqual(relay.calls.filter((c) => c.path === "/vehicles/sim-1/plan").at(-1).body, { steps, name: "copilot", confirm: "ARM" });
  await assert.rejects(() => tools.relay_plan.execute("1", { vehicle: "sim-1" }), /steps or builtin/);
  await assert.rejects(() => tools.relay_plan.execute("1", { vehicle: "sim-1", steps, builtin: "du7_box" }), /steps or builtin/);
});

test("relay_plans_builtin lists the ready-made plans", async () => {
  const relay = stubRelay({ "GET /plans/builtin": { plans: [{ id: "du7_box", steps: [] }] } });
  const out = await fly(relay).relay_plans_builtin.execute("1", {});
  assert.deepEqual(JSON.parse(out.content[0].text), [{ id: "du7_box", steps: [] }]);
});

test("gimbal tools: status reads, control claims first and sends the action, captures take a limit", async () => {
  const view = { kind: "sim", yaw_deg: 0, pitch_deg: -30, lens: "visible1", zoom: 1 };
  const relay = stubRelay({
    "GET /vehicles/sim-1": SIM,
    "POST /vehicles/sim-1/claim": {},
    "GET /vehicles/sim-1/gimbal": view,
    "POST /vehicles/sim-1/gimbal": { id: "sim-1", gimbal: view },
    "GET /vehicles/sim-1/gimbal/captures": { id: "sim-1", total: 0, captures: [] },
  });
  const tools = byName(gimbalTools(relay));
  assert.deepEqual(JSON.parse((await tools.relay_gimbal_status.execute("1", { vehicle: "sim-1" })).content[0].text), view);
  assert.ok(!relay.calls.some((c) => c.path.endsWith("/claim")), "reading claims nothing");
  await tools.relay_gimbal_control.execute("1", { vehicle: "sim-1", action: "angle", yaw_deg: 20, pitch_deg: -45 });
  assert.ok(relay.calls.some((c) => c.path.endsWith("/claim")), "claimed first");
  assert.deepEqual(relay.calls.at(-1), { method: "POST", path: "/vehicles/sim-1/gimbal", body: { action: "angle", yaw_deg: 20, pitch_deg: -45 } });
  await tools.relay_gimbal_control.execute("1", { vehicle: "sim-1", action: "look_at", x: 3, y: 0, z: 0, frame: "flu" });
  assert.deepEqual(relay.calls.at(-1).body, { action: "look_at", x: 3, y: 0, z: 0, frame: "flu" });
  await tools.relay_gimbal_control.execute("1", { vehicle: "sim-1", action: "photo" });
  assert.deepEqual(relay.calls.at(-1).body, { action: "photo" });
  await tools.relay_gimbal_captures.execute("1", { vehicle: "sim-1", limit: 5 });
  assert.equal(relay.calls.at(-1).path, "/vehicles/sim-1/gimbal/captures?limit=5");
  const desc = tools.relay_gimbal_control.description;
  for (const action of GIMBAL_ACTIONS) assert.ok(desc.includes(action), `${action} is described`);
  assert.equal(GIMBAL_ACTIONS.length, 25);
});

test("the gimbal tools are in every scope and at the sticks level; built-in plans and goto-like flight are not", () => {
  const relay = new RelayHttp("http://127.0.0.1:1");
  for (const scope of ["flight", "flight+device", "all"]) {
    for (const level of ["sticks", "full"]) {
      const names = new Set(toolsFor(relay, scope, undefined, level).map((t) => t.name));
      for (const n of ["relay_gimbal_status", "relay_gimbal_control", "relay_gimbal_captures"]) assert.ok(names.has(n), `${n} ${scope} ${level}`);
      assert.equal(names.has("relay_plans_builtin"), level === "full", `plans_builtin ${level}`);
    }
  }
  assert.ok(relayTools(relay).some((t) => t.name === "relay_plans_builtin"));
});

test("fleet and status show model, nav and a gimbal summary", async () => {
  const vehicle = {
    ...SIM,
    link: { state: "ok" },
    model: "anet-du7",
    nav: { frame: "flu", position: [0, 0, 1], yaw_deg: 90 },
    gimbal: { kind: "sim", link: "ok", yaw_deg: 0, pitch_deg: -30, lens: "visible1", zoom: 1, recording: false, photos: 2, fov_deg: [60, 36], tracking: {} },
  };
  const relay = stubRelay({ "GET /fleet": { safety_volume: {}, vehicles: [vehicle] }, "GET /vehicles/sim-1": vehicle });
  const tools = fly(relay);
  const fleet = JSON.parse((await tools.relay_fleet.execute("1", {})).content[0].text).vehicles[0];
  assert.equal(fleet.model, "anet-du7");
  assert.equal(fleet.nav.frame, "flu");
  assert.deepEqual(Object.keys(fleet.gimbal), ["kind", "link", "yaw_deg", "pitch_deg", "lens", "zoom", "recording", "photos"]);
  const full = JSON.parse((await tools.relay_vehicle.execute("1", { vehicle: "sim-1" })).content[0].text);
  assert.deepEqual(full.gimbal.fov_deg, [60, 36]);
});

test("the prompt and the tool descriptions teach flu and when to pass frame, and never the old clockwise rule", () => {
  const relay = new RelayHttp("http://127.0.0.1:1");
  for (const level of ["full", "sticks"]) {
    const prompt = systemPrompt("en", "all", level);
    assert.match(prompt, /flu/);
    assert.match(prompt, /counter-clockwise/);
    assert.match(prompt, /LEFT/);
    assert.match(prompt, /relay is the pre-0\.6 axes/);
    assert.match(prompt, /ANetDU7/);
    assert.match(prompt, /relay_gimbal_control/);
    assert.doesNotMatch(prompt, /yaw is positive clockwise \(right\)/);
  }
  const tools = byName(toolsFor(relay, "all", undefined, "full"));
  for (const name of ["relay_move", "relay_yaw", "relay_plan", "device_velocity"]) {
    assert.match(tools[name].description, /flu/, name);
    assert.match(tools[name].description, /frd/, name);
  }
  assert.doesNotMatch(tools.relay_yaw.description, /positive turns right \(clockwise\)\.$/);
  assert.doesNotMatch(tools.device_velocity.description, /ENU, m\/s/);
  assert.ok("frame" in tools.device_velocity.parameters.properties && "reference" in tools.device_velocity.parameters.properties);
  assert.match(systemPrompt("en", "flight", "full"), /relay_plans_builtin/);
  assert.doesNotMatch(systemPrompt("en", "flight", "sticks"), /relay_plans_builtin/);
});

test("add_sim and settings tools describe the ANetDU7 default and control_frame", async () => {
  const relay = stubRelay({ "POST /sims": { added: ["sim-1"] }, "PUT /settings": { ok: true } });
  const tools = byName(toolsFor(relay, "all", undefined, "full"));
  assert.match(tools.app_add_sim.description, /ANetDU7/);
  assert.match(tools.app_settings_set.description, /control_frame/);
  await tools.app_add_sim.execute("1", { frame: { preset: "micro-120" } });
  assert.deepEqual(JSON.parse(JSON.stringify(relay.calls.at(-1).body)), { count: 1, frame: { preset: "micro-120" } });
  await tools.app_settings_set.execute("1", { patch: { control_frame: "frd" } });
  assert.deepEqual(relay.calls.at(-1), { method: "PUT", path: "/settings", body: { control_frame: "frd" } });
});
