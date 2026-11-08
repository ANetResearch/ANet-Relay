// Dogfight and leaderboard tools (07 §3-§4, WP-B): one call starts a timed fight of simulators, where gunners fire and
// rammers fly into their targets, each flown by an onboard program with sticks only (06 §5); Relay arms them, the
// programs take off themselves, and the operator can possess any of them at any time. Names and parameters match the
// app MCP server and the SDK (dogfight_start, dogfight_stop, dogfight_status, leaderboard). Starting and stopping are
// application operations (agents_app_control); simulators only, never a real aircraft. No approval is needed.
import { defineTool, Type } from "./tools-common.mjs";

const opt = (t) => Type.Optional(t);
const lit = (...values) => Type.Union(values.map((x) => Type.Literal(x)));

export const MATCH_TOOL_NAMES = ["dogfight_start", "dogfight_stop", "dogfight_status", "leaderboard"];
export const BOARD_SORTS = ["downs", "survival", "accuracy", "rams"];

export const DOGFIGHT_PARAMS = Type.Object({
  aircraft: opt(Type.Integer({ minimum: 2, maximum: 12, description: "Simulated aircraft in the fight (Relay runs at most 8). Default 6." })),
  teams: opt(Type.Union([Type.Literal("ffa"), Type.Literal(2), Type.Literal(3)], { description: "ffa: everyone against everyone (default); 2 or 3: teams" })),
  roles: opt(
    Type.Object(
      {
        gunner: Type.Integer({ minimum: 0, maximum: 12, description: "Aircraft that fire" }),
        rammer: Type.Integer({ minimum: 0, maximum: 12, description: "Aircraft that ram (never fire)" }),
      },
      { description: "gunner + rammer = aircraft. Default two gunners to one rammer." },
    ),
  ),
  programs: opt(
    Type.Object(
      {
        gunner: opt(Type.String({ description: "Program id for gunners (default hunter_medium)" })),
        rammer: opt(Type.String({ description: "Program id for rammers (default rammer)" })),
      },
      { description: "Program per role: built-in examples (hunter_easy, hunter_medium, hunter_hard, rammer) or your own" },
    ),
  ),
  arena: opt(
    Type.Object({
      radius: opt(Type.Number({ minimum: 3, maximum: 30, description: "m, default 8" })),
      ceiling: opt(Type.Number({ minimum: 2, maximum: 10, description: "m, default 4" })),
    }),
  ),
  airframe: opt(lit("ideal", "typical", "rough")),
  hover_assist: opt(Type.Boolean({ description: "Default false: physical flight, as in duels" })),
  duration_s: opt(Type.Number({ minimum: 30, maximum: 1800, description: "Seconds; the fight ends by itself. Default 180." })),
  respawn: opt(Type.Boolean({ description: "Back at the spawn point 5 s after a shoot-down or crash (default true)" })),
  seed: opt(Type.Integer({ minimum: 0, maximum: 2 ** 31 - 1 })),
  vehicles: opt(Type.Array(Type.String(), { description: "Simulators (sim-N) to use first; others are added. Real aircraft are refused." })),
  // 08 §5: a custom fight, one entry per aircraft
  entrants: opt(
    Type.Array(
      Type.Object({
        role: lit("gunner", "rammer", "pilot"),
        program: opt(Type.String({ description: "Program id. Required for a pilot (for example figure8_evader or one you wrote); gunner and rammer default to hunter_medium and rammer" })),
        team: opt(Type.String({ maxLength: 16, description: "Team name: aircraft on one team never target each other. Default: the aircraft's own id" })),
        position: opt(lit("none", "uwb", "gps")),
        params: opt(Type.Record(Type.String(), Type.Any(), { description: "Program parameters" })),
        label: opt(Type.String({ maxLength: 16, description: "A short name for the aircraft, e.g. A" })),
      }),
      {
        minItems: 2,
        maxItems: 8,
        description:
          "A custom fight: one entry per aircraft, in spawn order; replaces aircraft, roles and teams. role pilot flies its own program and never fires; " +
          "position gps (or uwb) gives that program its own global position and velocity (obs.pos, obs.vel), none gives only its own sensors and the contacts it sees.",
      },
    ),
  ),
});

/** The dogfight request body from the tool parameters (only the keys given). */
export function dogfightBody(p) {
  const out = {};
  for (const key of ["aircraft", "teams", "roles", "programs", "arena", "airframe", "hover_assist", "duration_s", "respawn", "seed", "vehicles", "entrants"]) {
    if (p[key] !== undefined) out[key] = p[key];
  }
  return out;
}

const compactBoard = (board) => ({
  by: board.by,
  match: board.match,
  rows: (board.rows ?? []).map((r, i) => ({
    place: i + 1,
    key: r.key,
    aircraft: r.callsign ? `${r.callsign} / ${r.aircraft}` : r.aircraft,
    local: r.local,
    controller: r.controller?.name ? `${r.controller.kind}: ${r.controller.name}` : r.controller?.kind,
    downs: r.downs,
    hits: r.hits,
    shots: r.shots,
    accuracy: r.accuracy,
    rams: r.rams,
    deaths: r.deaths,
    crashes: r.crashes,
    alive_s: r.alive_s,
    best_alive_s: r.best_alive_s,
  })),
});

export function matchTools(relay) {
  const call = (method, path, body) => relay.call(method, path, body);
  return [
    defineTool(
      "dogfight_start",
      "Start dogfight",
      "Start a timed dogfight of simulated aircraft in the arena: Relay picks or adds the simulators, spawns them on a ring facing the centre, deploys a program per role (gunners fire, rammers fly into their targets and never fire), arms them, and the programs take off and fight with sticks only. On the LAN the fight joins the shared match; otherwise it runs as local practice. Returns the scenario with its aircraft and roles; read the standings with leaderboard.",
      DOGFIGHT_PARAMS,
      (p) => call("POST", "/scenarios/dogfight", dogfightBody(p)),
    ),
    defineTool("dogfight_stop", "Stop dogfight", "End the running dogfight early: programs stop, airborne aircraft land, the final standings stay on the leaderboard.", Type.Object({}), () =>
      call("DELETE", "/scenarios/dogfight"),
    ),
    defineTool(
      "dogfight_status",
      "Dogfight status",
      "The dogfight: running or not, phase, start and end time, configuration, every aircraft with its role, program, team and state (flying, down, human when the operator possesses it), and the match.",
      Type.Object({}),
      () => call("GET", "/scenarios/dogfight"),
    ),
    defineTool(
      "leaderboard",
      "Leaderboard",
      "The arena leaderboard of this terminal and every LAN peer, one row per aircraft, sorted by downs (default), survival, accuracy or rams: controller, downs, hits, shots, accuracy, rams, deaths, crashes, current and longest survival. Rows of a Relay 0.4 peer have no statistics.",
      Type.Object({ by: opt(lit(...BOARD_SORTS)) }),
      async (p) => compactBoard(await call("GET", `/arena/leaderboard?by=${p.by ?? "downs"}`)),
    ),
  ];
}
