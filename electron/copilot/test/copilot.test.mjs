// Copilot against a running backend with pi's faux provider (scripted model, no network).
//   RELAY_API=http://127.0.0.1:8765 RELAY_OPERATOR_TOKEN=... node --test electron/copilot/test/copilot.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

import { createModels } from "@earendil-works/pi-ai";
import { fauxAssistantMessage, fauxProvider, fauxText, fauxToolCall } from "@earendil-works/pi-ai/providers/faux";

import { createCopilot, RelayHttp, relayTools } from "../agent.mjs";

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
