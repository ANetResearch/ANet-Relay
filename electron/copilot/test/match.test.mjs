// Copilot's dogfight and leaderboard tools (07 §3-§4, WP-B), offline: a scripted Relay API, no backend needed.
//   node --test electron/copilot/test/match.test.mjs
import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";

import { RelayHttp, systemPrompt, toolsFor } from "../agent.mjs";
import { BOARD_SORTS, dogfightBody, MATCH_TOOL_NAMES } from "../tools-match.mjs";

const BASE = "http://127.0.0.1:8765";
const CONTRACT_TOOLS = ["dogfight_start", "dogfight_stop", "dogfight_status", "leaderboard"];

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

const byName = (relay, scope = "all", level = "sticks") => Object.fromEntries(toolsFor(relay, scope, undefined, level).map((t) => [t.name, t]));
const out = (result) => JSON.parse(result.content[0].text);

test("the dogfight tools of 07 §3 are in every scope and level, with labels in both languages", () => {
  assert.deepEqual([...MATCH_TOOL_NAMES].sort(), [...CONTRACT_TOOLS].sort());
  const labels = fs.readFileSync(new URL("../../../web/src/copilot/toolLabels.ts", import.meta.url), "utf8");
  for (const scope of ["flight", "flight+device", "all"]) {
    for (const level of ["sticks", "full"]) {
      const names = toolsFor(new RelayHttp(BASE), scope, undefined, level).map((t) => t.name);
      for (const name of CONTRACT_TOOLS) assert.ok(names.includes(name), `${scope}/${level}: ${name}`);
    }
  }
  for (const name of CONTRACT_TOOLS) assert.ok(labels.includes(`${name}:`), `${name} has no label`);
});

test("dogfight_start sends the request body of 07 §3 and nothing it was not given", async () => {
  const relay = stubRelay({ "POST /scenarios/dogfight": (body) => ({ running: true, phase: "booting", config: body, aircraft: [] }) });
  const tools = byName(relay);
  const params = { aircraft: 6, teams: "ffa", roles: { gunner: 4, rammer: 2 }, duration_s: 300 };
  const res = out(await tools.dogfight_start.execute("1", params));
  assert.equal(res.running, true);
  assert.deepEqual(relay.calls.at(-1), { method: "POST", path: "/scenarios/dogfight", body: params });
  assert.deepEqual(dogfightBody({ teams: 2, extra: 1 }), { teams: 2 });
  const schema = tools.dogfight_start.parameters;
  for (const key of ["aircraft", "teams", "roles", "programs", "arena", "airframe", "hover_assist", "duration_s", "respawn", "seed", "vehicles"]) {
    assert.ok(key in schema.properties, key);
  }
  assert.equal(schema.properties.aircraft.maximum, 12);
});

test("dogfight_stop, dogfight_status and leaderboard call their routes", async () => {
  const board = {
    by: "survival",
    match: { id: "0a0b0c0d", started_at: 1, ends_at: 181, running: true },
    rows: [
      { key: "sim-1", aircraft: "SIM-1", callsign: "Bay", local: true, controller: { kind: "program", name: "Rammer r1" }, downs: 0, hits: 0, shots: 0, accuracy: null, rams: 2, deaths: 0, crashes: 1, alive_s: 12.5, best_alive_s: 40, damage: 0 },
      { key: "00ab/sim-2", aircraft: "SIM-2", callsign: "Old", local: false, controller: { kind: "none", name: "" }, downs: null, hits: null, shots: null, accuracy: null, rams: null, deaths: null, crashes: null, alive_s: null, best_alive_s: null, damage: null },
    ],
  };
  const relay = stubRelay({
    "DELETE /scenarios/dogfight": { running: false, phase: "ending" },
    "GET /scenarios/dogfight": { running: true, phase: "running" },
    "GET /arena/leaderboard": board,
  });
  const tools = byName(relay, "flight");
  assert.equal(out(await tools.dogfight_stop.execute("1", {})).phase, "ending");
  assert.equal(out(await tools.dogfight_status.execute("2", {})).phase, "running");
  const res = out(await tools.leaderboard.execute("3", { by: "survival" }));
  assert.equal(relay.calls.at(-1).path, "/arena/leaderboard?by=survival");
  assert.equal(res.rows[0].place, 1);
  assert.equal(res.rows[0].controller, "program: Rammer r1");
  assert.equal(res.rows[1].aircraft, "Old / SIM-2");
  assert.equal(res.rows[1].downs, null);
  await tools.leaderboard.execute("4", {});
  assert.equal(relay.calls.at(-1).path, "/arena/leaderboard?by=downs");
  assert.deepEqual(BOARD_SORTS, ["downs", "survival", "accuracy", "rams"]);
});

test("the system prompt maps a one-line dogfight request to dogfight_start", () => {
  for (const prompt of [systemPrompt("zh", "all", "sticks"), systemPrompt("en", "flight", "full")]) {
    assert.match(prompt, /开一场 6 机混战，4 架开火 2 架撞击/);
    assert.match(prompt, /dogfight_start \{aircraft: 6, teams: "ffa", roles: \{gunner: 4, rammer: 2\}\}/);
    assert.match(prompt, /leaderboard \{by: "downs"\}/);
    assert.match(prompt, /never put a real aircraft in a dogfight/);
  }
});
