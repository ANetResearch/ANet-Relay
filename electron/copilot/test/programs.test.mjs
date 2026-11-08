// Copilot's program tools and the agent flight level (06 §5, §6, WP-4b), offline: a scripted Relay API and
// pi's faux provider, no backend needed.
//   node --test electron/copilot/test/programs.test.mjs
import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";

import { createModels, getCurrentSystemPrompt, getCurrentTools } from "@earendil-works/pi-ai";
import { fauxAssistantMessage, fauxProvider, fauxToolCall } from "@earendil-works/pi-ai/providers/faux";

import { createCopilot, RelayHttp, STICKS_TOOLS, systemPrompt, toolsFor } from "../agent.mjs";
import { PROGRAMS_GUIDE } from "../programs-guide.mjs";
import { judgeClient, numbered, PROGRAM_TOOL_NAMES, waitJob } from "../tools-programs.mjs";

const BASE = "http://127.0.0.1:8765";
const SIM = { id: "sim-1", label: "SIM-1", kind: "sim", claim: null, possession: null };
const REAL = { id: "real-1", label: "Quad 01", kind: "real", claim: null, possession: null };
const CONTRACT_TOOLS = [
  "program_list",
  "program_get",
  "program_create",
  "program_update",
  "program_validate",
  "program_test",
  "program_deploy",
  "program_stop",
  "program_resume",
  "program_params",
  "program_status",
  "program_log",
  "challenge_list",
  "challenge_submit",
  "job_status",
  "duel_run",
  "ladder",
  "airframe_set",
];

/** A Relay client stub: routes "METHOD path" to a handler (or an error) and records every call. */
function stubRelay(routes) {
  const calls = [];
  return {
    calls,
    async call(method, path, body) {
      calls.push({ method, path, body });
      const handler = routes[`${method} ${path}`] ?? routes[`${method} ${path.split("?")[0]}`];
      if (handler === undefined) {
        const err = new Error("not_found: Nothing is available at this path.");
        err.code = "not_found";
        throw err;
      }
      return typeof handler === "function" ? handler(body) : handler;
    },
  };
}

const byName = (relay, scope = "all", level = "full") => Object.fromEntries(toolsFor(relay, scope, undefined, level).map((t) => [t.name, t]));
const out = (result) => JSON.parse(result.content[0].text);

function offlineCopilot(routes, responses) {
  const faux = fauxProvider();
  const models = createModels();
  models.setProvider(faux.provider);
  faux.setResponses(responses);
  const relay = stubRelay(routes);
  const events = [];
  const copilot = createCopilot({ relay, models, model: faux.getModel(), emit: (e) => events.push(e) });
  return { copilot, relay, events };
}

test("the program tools of 06 §6 are in every scope, with labels in both languages", () => {
  assert.deepEqual([...PROGRAM_TOOL_NAMES].sort(), [...CONTRACT_TOOLS].sort());
  const labels = fs.readFileSync(new URL("../../../web/src/copilot/toolLabels.ts", import.meta.url), "utf8");
  for (const scope of ["flight", "flight+device", "all"]) {
    for (const level of ["sticks", "full"]) {
      const names = toolsFor(new RelayHttp(BASE), scope, undefined, level).map((t) => t.name);
      for (const name of CONTRACT_TOOLS) assert.ok(names.includes(name), `${scope}/${level}: ${name}`);
    }
  }
  for (const name of [...CONTRACT_TOOLS, "relay_arm", "relay_disarm", "relay_mode"]) assert.ok(labels.includes(`${name}:`), `${name} has no label`);
});

test("at the sticks level the high-level flight tools are left out", () => {
  const relay = new RelayHttp(BASE);
  const sticks = new Set(toolsFor(relay, "all", undefined, "sticks").map((t) => t.name));
  const full = new Set(toolsFor(relay, "all", undefined, "full").map((t) => t.name));
  for (const name of ["relay_takeoff", "relay_hover", "relay_move", "relay_yaw", "relay_plan", "relay_refly", "device_attitude", "device_velocity"]) {
    assert.ok(!sticks.has(name), `${name} at sticks`);
    assert.ok(full.has(name), `${name} at full`);
  }
  for (const name of STICKS_TOOLS) assert.ok(sticks.has(name), name);
  for (const name of ["relay_land", "relay_stop", "relay_sticks_for", "relay_arm", "relay_mode", "device_motor_test", "device_mode", "app_set_language"]) {
    assert.ok(sticks.has(name), name);
  }
});

test("the system prompt tells the model to write programs at the sticks level and carries the guide", () => {
  const sticks = systemPrompt("en", "all", "sticks");
  assert.match(sticks, /flight level to sticks/);
  assert.match(sticks, /write a program and deploy it/);
  assert.doesNotMatch(sticks, /call relay_plan once/);
  const full = systemPrompt("en", "all", "full");
  assert.doesNotMatch(full, /flight level to sticks/);
  assert.match(full, /call relay_plan once/);
  for (const prompt of [sticks, full, systemPrompt("zh", "flight", "sticks")]) {
    assert.ok(prompt.endsWith(PROGRAMS_GUIDE), "the guide closes the prompt");
    assert.match(prompt, /program_\*/);
  }
  assert.doesNotMatch(systemPrompt("en", "flight", "sticks"), /device_\*|app_\*/);
});

test("the guide in the prompt is docs/programs-guide.md", () => {
  const source = fs.readFileSync(new URL("../../../docs/programs-guide.md", import.meta.url), "utf8").trim();
  assert.equal(PROGRAMS_GUIDE, source, "run node scripts/programs-guide.mjs");
  for (const word of ["Sticks(", "PARAMS", "PID(", "StateMachine(", "hover throttle", "integral trim", "import math", "obs"]) {
    assert.ok(PROGRAMS_GUIDE.includes(word), word);
  }
});

test("Copilot reads the flight level before each prompt and swaps its tools and instructions", async () => {
  let level = "full";
  const seen = [];
  const capture = (context) => {
    seen.push({ tools: getCurrentTools(context.messages).map((t) => t.name), prompt: getCurrentSystemPrompt(context.messages) });
    return fauxAssistantMessage("ok");
  };
  const { copilot } = offlineCopilot({ "GET /settings": () => ({ agent_flight_level: level }), "GET /app/ui": { state: null } }, [capture, capture, capture]);
  assert.equal(copilot.flightLevel, "sticks", "sticks until Relay answers");
  await copilot.prompt("status");
  assert.equal(copilot.flightLevel, "full");
  level = "sticks";
  await copilot.prompt("status");
  copilot.reset();
  await copilot.prompt("status");
  assert.ok(seen[0].tools.includes("relay_takeoff") && !/flight level to sticks/.test(seen[0].prompt));
  assert.ok(!seen[1].tools.includes("relay_takeoff") && seen[1].tools.includes("program_deploy"));
  assert.match(seen[1].prompt, /flight level to sticks/);
  assert.ok(!seen[2].tools.includes("relay_plan"), "a reset keeps the level");
});

test("a Relay without the setting flies everything; an unreachable one keeps the last level", async () => {
  let fail = false;
  const { copilot } = offlineCopilot(
    {
      "GET /settings": () => {
        if (fail) throw new Error("backend busy");
        return {};
      },
      "GET /app/ui": { state: null },
    },
    [fauxAssistantMessage("ok"), fauxAssistantMessage("ok")],
  );
  await copilot.prompt("status");
  assert.equal(copilot.flightLevel, "full");
  fail = true;
  await copilot.prompt("status");
  assert.equal(copilot.flightLevel, "full");
});

test("program edits need no approval; deploying on a real aircraft waits for the operator", async () => {
  const routes = {
    "GET /settings": { agent_flight_level: "sticks" },
    "GET /app/ui": { state: { selected: "real-1", lang: "en" } },
    "GET /vehicles/real-1": REAL,
    "POST /vehicles/real-1/claim": {},
    "PUT /programs/p1": { program: { id: "p1", name: "Hover", rev: 3, source: "a\nb" }, validation: { ok: true, errors: [] } },
    "POST /vehicles/real-1/program": { id: "real-1", program: { status: "running" } },
  };
  const { copilot, relay, events } = offlineCopilot(routes, [
    fauxAssistantMessage(fauxToolCall("program_update", { program: "p1", patch: [{ start: 2, end: 2, text: "c" }], note: "trim" }), { stopReason: "toolUse" }),
    fauxAssistantMessage(fauxToolCall("program_deploy", { vehicle: "real-1", program: "p1" }), { stopReason: "toolUse" }),
    fauxAssistantMessage("Declined."),
  ]);
  const pending = copilot.prompt("tune and fly it");
  for (let i = 0; i < 150 && !events.some((e) => e.type === "approval"); i++) await new Promise((r) => setTimeout(r, 20));
  const approvals = events.filter((e) => e.type === "approval");
  assert.equal(approvals.length, 1, "only the deployment asks");
  assert.equal(approvals[0].tool, "program_deploy");
  assert.equal(approvals[0].request.danger, "program");
  assert.deepEqual(approvals[0].request.args, { program: "p1" });
  copilot.approve(approvals[0].id, false);
  await pending;
  assert.ok(relay.calls.some((c) => c.method === "PUT" && c.path === "/programs/p1"), "the edit went through");
  assert.ok(!relay.calls.some((c) => c.path === "/vehicles/real-1/program"), "declined: nothing deployed");
  assert.equal(events.find((e) => e.type === "tool_end" && e.tool === "program_update").error, false);
  assert.equal(events.find((e) => e.type === "tool_end" && e.tool === "program_deploy").error, true);
});

test("deploying on a simulator claims it and sends the scenario; arm and mode on a real aircraft ask", async () => {
  const relay = stubRelay({
    "GET /vehicles/sim-1": SIM,
    "POST /vehicles/sim-1/claim": {},
    "POST /vehicles/sim-1/program": (body) => ({ id: "sim-1", program: { status: "running", body } }),
    "PATCH /vehicles/sim-1/program/params": (body) => ({ id: "sim-1", params: body }),
    "POST /vehicles/sim-1/program/stop": { id: "sim-1", program: { status: "stopped" } },
    "GET /vehicles/sim-1/program/log": { lines: [{ seq: 4, text: "hover throttle 0.61" }], last_seq: 4 },
    "PUT /sims/sim-1/airframe": (body) => ({ id: "sim-1", ...body, hover_throttle: 0.63 }),
  });
  const tools = byName(relay);
  const airframe = { preset: "rough", seed: 3 };
  await tools.program_deploy.execute("1", { vehicle: "sim-1", program: "hover_trim", params: { target_alt: 1.2 }, sensors: { position: "uwb" }, seed: 7, airframe });
  assert.equal(relay.calls[1].path, "/vehicles/sim-1/claim", "claimed first");
  assert.deepEqual(relay.calls.at(-1).body, { program: "hover_trim", params: { target_alt: 1.2 }, sensors: { position: "uwb" }, seed: 7, airframe });
  const tuned = out(await tools.program_params.execute("2", { vehicle: "sim-1", params: { kp: 0.3 } }));
  assert.deepEqual(tuned.params, { kp: 0.3 });
  assert.equal(relay.calls.at(-1).method, "PATCH");
  await tools.program_log.execute("3", { vehicle: "sim-1", after: 2 });
  assert.equal(relay.calls.at(-1).path, "/vehicles/sim-1/program/log?after=2&limit=100");
  await tools.program_stop.execute("4", { vehicle: "sim-1" });
  const fitted = out(await tools.airframe_set.execute("5", { vehicle: "sim-1", preset: "typical", seed: 9 }));
  assert.deepEqual(relay.calls.at(-1), { method: "PUT", path: "/sims/sim-1/airframe", body: { preset: "typical", seed: 9 } });
  assert.equal(fitted.hover_throttle, 0.63);

  const { copilot, events } = offlineCopilot(
    { "GET /settings": { agent_flight_level: "sticks" }, "GET /app/ui": { state: null }, "GET /vehicles/real-1": REAL },
    [
      fauxAssistantMessage(fauxToolCall("relay_mode", { vehicle: "real-1", name: "STABILIZE" }), { stopReason: "toolUse" }),
      fauxAssistantMessage("Waiting."),
    ],
  );
  const pending = copilot.prompt("stabilize");
  for (let i = 0; i < 150 && !events.some((e) => e.type === "approval"); i++) await new Promise((r) => setTimeout(r, 20));
  const approval = events.find((e) => e.type === "approval");
  assert.equal(approval.tool, "relay_mode");
  assert.equal(approval.request.danger, "motion");
  copilot.approve(approval.id, false);
  await pending;
});

test("program_get numbers the source for line patches; an older revision can be read", async () => {
  const relay = stubRelay({
    "GET /programs/p1": { program: { id: "p1", name: "Hover", rev: 2, builtin: false, source: "def step(obs, ctx):\n    return Sticks()", params: {}, tests: [] } },
    "GET /programs/p1/revisions": { revisions: [{ rev: 1, source: "old", params: { a: 1 }, author: "copilot", note: "first", t: 1 }, { rev: 2, source: "x", params: {}, author: "operator", note: "edited", t: 2 }] },
  });
  const tools = byName(relay);
  const current = out(await tools.program_get.execute("1", { program: "p1" }));
  assert.equal(current.source_numbered, "1| def step(obs, ctx):\n2|     return Sticks()");
  assert.equal(current.lines, 2);
  assert.deepEqual(current.revisions.map((r) => r.rev), [2, 1], "newest first");
  assert.ok(!("source" in current.revisions[0]), "revisions come without their source");
  const old = out(await tools.program_get.execute("2", { program: "p1", rev: 1, numbered: false }));
  assert.equal(old.source, "old");
  assert.equal(old.rev, 1);
  assert.deepEqual(old.params, { a: 1 });
  await assert.rejects(() => tools.program_get.execute("3", { program: "p1", rev: 9 }), /revision_not_found/);
  assert.equal(numbered(Array.from({ length: 10 }, (_, i) => `l${i}`).join("\n")).split("\n")[9], "10| l9");
});

test("create, update, validate and test reach the program routes", async () => {
  const relay = stubRelay({
    "POST /programs": (body) => ({ program: { id: "p2", name: body.name, rev: 1, test_count: 0 }, validation: { ok: true } }),
    "PUT /programs/p2": (body) => ({ program: { id: "p2", name: "n", rev: 2, source: body.source ?? "x\ny" }, validation: { ok: false, errors: [{ line: 1 }] } }),
    "POST /programs/p2/validate": { ok: true, errors: [] },
    "POST /programs/p2/test": { passed: 1, total: 1, results: [] },
  });
  const tools = byName(relay);
  const created = out(await tools.program_create.execute("1", { name: "Mine", copy_of: "hover_trim" }));
  assert.deepEqual(relay.calls[0].body, { name: "Mine", copy_of: "hover_trim" });
  assert.equal(created.program.id, "p2");
  const patched = out(await tools.program_update.execute("2", { program: "p2", patch: [{ start: 1, end: 0, text: "import math" }], note: "math" }));
  assert.equal(patched.source_numbered, "1| x\n2| y");
  assert.equal(patched.validation.ok, false);
  const replaced = out(await tools.program_update.execute("3", { program: "p2", source: "s", note: "rewrite" }));
  assert.ok(!("source_numbered" in replaced));
  await tools.program_validate.execute("4", { program: "p2", source: "def step(obs, ctx): pass" });
  assert.deepEqual(relay.calls.at(-1).body, { source: "def step(obs, ctx): pass" });
  await tools.program_test.execute("5", { program: "p2", rev: 1 });
  assert.deepEqual(relay.calls.at(-1), { method: "POST", path: "/programs/p2/test", body: { rev: 1 } });
});

test("a challenge submission waits for its job and reports progress", async () => {
  let polls = 0;
  const relay = stubRelay({
    "GET /challenges": { challenges: [{ id: "hover" }] },
    "POST /challenges/hover/submit": { job: { id: "j1", state: "queued" } },
    "GET /jobs/j1": () => {
      polls += 1;
      return polls < 3 ? { id: "j1", state: "running", progress: polls / 3 } : { id: "j1", state: "done", result: { score: 84, passed: true } };
    },
    "POST /duels": { id: "j2", state: "queued" },
  });
  const tools = byName(relay);
  const updates = [];
  const result = out(await tools.challenge_submit.execute("1", { challenge: "hover", program: "hover_trim" }, undefined, (u) => updates.push(u.content[0].text)));
  assert.equal(result.state, "done");
  assert.equal(result.result.score, 84);
  assert.deepEqual(relay.calls[0].body, { program: "hover_trim" });
  assert.ok(updates.some((u) => /job j1: running 33 %/.test(u)));
  const queued = out(await tools.duel_run.execute("2", { programs: ["hunter_easy", "hunter_hard"], rounds: 3, wait: false }));
  assert.equal(queued.id, "j2");
  assert.deepEqual(relay.calls.at(-1).body, { programs: ["hunter_easy", "hunter_hard"], rounds: 3 });
  // a job that outlives the wait is handed back to job_status
  const slow = stubRelay({ "GET /jobs/j3": { state: "running", progress: 0.1 } });
  const late = await waitJob(judgeClient(slow), { id: "j3" }, undefined, undefined, 0);
  assert.equal(late.state, "running");
  assert.match(late.note, /job_status/);
});

test("without the judge API the judge tools say so instead of a bare not_found", async () => {
  const relay = stubRelay({});
  const tools = byName(relay);
  for (const [name, args] of [
    ["challenge_list", {}],
    ["challenge_submit", { challenge: "hover", program: "p" }],
    ["job_status", { job: "j1" }],
    ["duel_run", { programs: ["a", "b"] }],
    ["ladder", {}],
  ]) {
    await assert.rejects(() => tools[name].execute("1", args), (err) => err.code === "judge_unavailable" && /no judge API/.test(err.message), name);
  }
  // with the judge present, an unknown job is the backend's own error
  const present = stubRelay({ "GET /challenges": { challenges: [] } });
  await assert.rejects(() => byName(present).job_status.execute("1", { job: "nope" }), (err) => err.code === "not_found");
});

test("program_status reads the run view from the aircraft", async () => {
  const relay = stubRelay({
    "GET /vehicles/sim-1": { ...SIM, armed: true, mode: "STABILIZE", controller: "program", programs_allowed: true, program: { id: "hover_trim", status: "running", fps: 50 }, sim: { hover_assist: false, airframe: { preset: "typical", seed: 4 } } },
  });
  const status = out(await byName(relay).program_status.execute("1", { vehicle: "sim-1" }));
  assert.equal(status.program.status, "running");
  assert.equal(status.controller, "program");
  assert.deepEqual(status.sim, { hover_assist: false, airframe: { preset: "typical", seed: 4 } });
});
