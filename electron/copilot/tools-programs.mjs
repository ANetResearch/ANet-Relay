// Program tools (06 §6, WP-4b): Copilot writes, tests, judges and deploys onboard flight programs instead of
// flying the 50 Hz loop itself. Names match the app MCP server and the SDK (program_*, challenge_*, job_status,
// duel_run, ladder, airframe_set). Editing a program is only code and needs no approval; deploying, resuming or
// retuning one on a real aircraft waits for the operator (agent.mjs, PROGRAM_DANGER).
import { defineTool, enc, flightHelpers, sleep, text, Type, VID } from "./tools-common.mjs";

const opt = (t) => Type.Optional(t);
const lit = (...values) => Type.Union(values.map((x) => Type.Literal(x)));
const PID = Type.String({ description: "Program id from program_list, e.g. hover_trim" });
const ANY_OBJECT = Type.Record(Type.String(), Type.Any());
const TEST = Type.Object(
  {
    name: opt(Type.String()),
    obs: opt(ANY_OBJECT),
    mem: opt(ANY_OBJECT),
    state: opt(Type.String()),
    params: opt(ANY_OBJECT),
    expect: ANY_OBJECT,
  },
  { description: "obs fields not given hover level at 1 m; expect: roll/pitch/yaw/throttle [low, high], fire bool, state name" },
);
const AIRFRAME = Type.Object(
  {
    preset: opt(lit("ideal", "typical", "rough", "custom")),
    params: opt(ANY_OBJECT),
    seed: opt(Type.Integer({ minimum: 0, maximum: 2 ** 31 - 1 })),
  },
  { description: "Simulator defects: {preset} (fresh seed), {preset, seed}, {seed} or {params: {mass_kg, payload_kg, cg_offset_cm, level_trim_deg, motor_gain, prop_damage, damaged_prop, imu_noise}}" },
);

/** Calls that deploy, resume or retune a program: approved by the operator on a real aircraft (danger "program").
 * program_params is one of them: its new values steer the flight loop as directly as a new deployment. */
export const PROGRAM_DANGER = { program_deploy: "program", program_resume: "program", program_params: "program" };
export const JOB_POLL_MS = 500;
export const JOB_WAIT_MS = 180_000;
const JOB_FINAL = new Set(["done", "failed", "error", "cancelled", "canceled"]);
const REVISIONS_SHOWN = 20;

/** The source with a line number in front of every line ("  12| code"); the numbers are not part of the code. */
export function numbered(source) {
  const lines = String(source ?? "").split("\n");
  const width = String(lines.length).length;
  return lines.map((line, i) => `${String(i + 1).padStart(width)}| ${line}`).join("\n");
}

const summary = (p) =>
  p && {
    id: p.id,
    name: p.name,
    builtin: p.builtin,
    rev: p.rev,
    author: p.author,
    tests: p.test_count ?? p.tests?.length ?? 0,
    description: p.description,
  };

/** Judge routes (06 §3.4). A Relay without them answers not_found for every path: say so instead. */
export function judgeClient(relay) {
  return async (method, path, body) => {
    try {
      return await relay.call(method, path, body);
    } catch (err) {
      if (err.code !== "not_found") throw err;
      let present = path === "/challenges" ? false : null;
      if (present === null) {
        try {
          await relay.call("GET", "/challenges");
          present = true;
        } catch (probe) {
          present = probe.code !== "not_found";
        }
      }
      if (present) throw err;
      const e = new Error("judge_unavailable: this Relay has no judge API (challenges, jobs, duels, ladder). It comes with Relay 0.4.0; until then test with program_test and fly with program_deploy.");
      e.code = "judge_unavailable";
      throw e;
    }
  };
}

const jobId = (res) => res?.job?.id ?? res?.job_id ?? res?.id ?? null;

/** Wait for an evaluation job; on timeout or abort the job keeps running and job_status reads it later. */
export async function waitJob(call, res, signal, onUpdate, timeoutMs = JOB_WAIT_MS) {
  const id = jobId(res);
  if (id === null) return res;
  const end = Date.now() + timeoutMs;
  let shown = null;
  for (;;) {
    const job = await call("GET", `/jobs/${enc(id)}`);
    const view = job?.job ?? job;
    if (JOB_FINAL.has(view?.state)) return view;
    const progress = typeof view?.progress === "number" ? Math.round(view.progress * 100) : null;
    if (progress !== shown) {
      shown = progress;
      onUpdate?.(text(`job ${id}: ${view?.state ?? "queued"}${progress === null ? "" : ` ${progress} %`}`));
    }
    if (Date.now() >= end) return { ...view, id, note: "still running: read it later with job_status" };
    try {
      await sleep(JOB_POLL_MS, signal);
    } catch {
      return { ...view, id, note: "stopped waiting: the job keeps running; read it with job_status" };
    }
  }
}

/** Build the program, judge and airframe tools on a Relay API client. */
export function programTools(relay) {
  const call = (method, path, body) => relay.call(method, path, body);
  const judge = judgeClient(relay);
  const { vehicle, claimFor } = flightHelpers(relay);
  const prog = (vid) => `/vehicles/${enc(vid)}/program`;
  const claimed = (fn) => async (p, ctx) => {
    await claimFor(p.vehicle);
    return fn(p, ctx);
  };
  const pick = (p, keys) => Object.fromEntries(keys.filter((k) => p[k] !== undefined).map((k) => [k, p[k]]));

  return [
    defineTool("program_list", "Programs", "List the flight programs: built-in examples (read-only; copy them with program_create copy_of) and your own, with revision and test count.", Type.Object({}), async () => {
      const res = await call("GET", "/programs");
      return { programs: (res.programs ?? []).map(summary) };
    }),
    defineTool(
      "program_get",
      "Read program",
      "Read a program: source, PARAMS, unit tests and the recent revisions, newest first (author, note). The source comes as source_numbered, each line prefixed with its number and '| ' for line patches; those prefixes are not part of the code. numbered: false returns the plain source. rev reads an older revision.",
      Type.Object({ program: PID, rev: opt(Type.Integer({ minimum: 1 })), numbered: opt(Type.Boolean()) }),
      async (p) => {
        const { program } = await call("GET", `/programs/${enc(p.program)}`);
        const { revisions = [] } = await call("GET", `/programs/${enc(p.program)}/revisions`);
        let { source, params } = program;
        let rev = program.rev;
        if (p.rev !== undefined && p.rev !== program.rev) {
          const old = revisions.find((r) => r.rev === p.rev);
          if (!old) throw new Error(`revision_not_found: ${p.program} has no revision ${p.rev}`);
          ({ source, params, rev } = old);
        }
        const body = p.numbered === false ? { source } : { source_numbered: numbered(source), lines: String(source).split("\n").length };
        return {
          ...summary(program),
          rev,
          current_rev: program.rev,
          ...body,
          params,
          tests: program.tests,
          revisions: [...revisions]
            .sort((a, b) => b.rev - a.rev)
            .slice(0, REVISIONS_SHOWN)
            .map((r) => ({ rev: r.rev, author: r.author, note: r.note, t: r.t })),
        };
      },
    ),
    defineTool(
      "program_create",
      "New program",
      "Create a program from source (or copy_of an existing or built-in one). Returns its id, revision 1 and the validation result. Creating and editing programs needs no approval.",
      Type.Object({
        name: Type.String({ maxLength: 200 }),
        description: opt(Type.String()),
        source: opt(Type.String({ description: "the whole program; omitted with copy_of" })),
        params: opt(ANY_OBJECT),
        tests: opt(Type.Array(TEST)),
        note: opt(Type.String()),
        copy_of: opt(Type.String()),
      }),
      async (p) => {
        const res = await call("POST", "/programs", pick(p, ["name", "description", "source", "params", "tests", "note", "copy_of"]));
        return { program: summary(res.program), validation: res.validation };
      },
    ),
    defineTool(
      "program_update",
      "Edit program",
      "Change a program; every change is a new revision with your note. Give either source (the whole new text) or patch: line edits [{start, end, text}], 1-based and inclusive, all numbered against the current revision (end = start - 1 inserts before start; empty text deletes). Also name, description, params (stored defaults), tests. Returns the validation result, and after a patch the new numbered source.",
      Type.Object({
        program: PID,
        source: opt(Type.String()),
        patch: opt(Type.Array(Type.Object({ start: Type.Integer({ minimum: 1 }), end: Type.Integer({ minimum: 0 }), text: Type.String() }), { minItems: 1 })),
        name: opt(Type.String({ maxLength: 200 })),
        description: opt(Type.String()),
        params: opt(ANY_OBJECT),
        tests: opt(Type.Array(TEST)),
        note: Type.String({ description: "what changed and why (shown in History)" }),
      }),
      async (p) => {
        const res = await call("PUT", `/programs/${enc(p.program)}`, pick(p, ["source", "patch", "name", "description", "params", "tests", "note"]));
        const out = { program: summary(res.program), validation: res.validation };
        if (p.patch) out.source_numbered = numbered(res.program?.source);
        return out;
      },
    ),
    defineTool(
      "program_validate",
      "Check program",
      "Check a program's source against the language rules without running it: every problem with line, column and reason, plus the PARAMS found. source checks that text instead of the stored one.",
      Type.Object({ program: PID, source: opt(Type.String()) }),
      (p) => call("POST", `/programs/${enc(p.program)}/validate`, pick(p, ["source"])),
    ),
    defineTool(
      "program_test",
      "Run unit tests",
      "Run the program's unit tests (or the tests given) once each in the program host: actual outputs and pass or fail per test. rev tests an older revision; source tests unsaved text.",
      Type.Object({ program: PID, rev: opt(Type.Integer({ minimum: 1 })), source: opt(Type.String()), tests: opt(Type.Array(TEST)), seed: opt(Type.Integer({ minimum: 0 })) }),
      (p) => call("POST", `/programs/${enc(p.program)}/test`, pick(p, ["rev", "source", "tests", "seed"])),
    ),
    defineTool(
      "program_deploy",
      "Deploy program",
      "Run a program revision on an aircraft at 50 Hz; it then holds the sticks as PROGRAM until it is stopped. Claims the aircraft first. mode pilot (default: the program flies) or copilot (it transforms the person's sticks). " +
        "Simulators: sensors {baro, position none|uwb|gps, contacts}, arena {radius, floor, ceiling}, seed, airframe. arm true (the usual way to test a program from the ground) sets STABILIZE and arms once the program is in place, so it flies at once; the answer's arm.ok says whether the autopilot armed, and stopping the program on the ground disarms again. Deploying a new revision while the program flies takes over in the air without landing. A real aircraft needs the operator's 'Allow programs' switch, passed preflight checks and the operator's approval in the chat.",
      Type.Object({
        vehicle: VID,
        program: PID,
        rev: opt(Type.Integer({ minimum: 1 })),
        params: opt(ANY_OBJECT),
        mode: opt(lit("pilot", "copilot")),
        sensors: opt(Type.Object({ baro: opt(Type.Boolean()), position: opt(lit("none", "uwb", "gps")), contacts: opt(Type.Boolean()) })),
        arena: opt(Type.Object({ radius: Type.Number(), floor: Type.Number(), ceiling: Type.Number() })),
        seed: opt(Type.Integer({ minimum: 0 })),
        airframe: opt(AIRFRAME),
        team: opt(Type.String({ maxLength: 16, description: "Team of this aircraft in every contact list; filled into the program's team parameter. Aircraft on one team never target each other." })),
        arm: opt(Type.Boolean({ description: "Arm in STABILIZE once the program is in place (09 §2)" })),
      }),
      // the approval in the chat is the confirmation: the word ARM goes with an arm request
      claimed((p) => call("POST", prog(p.vehicle), { ...pick(p, ["program", "rev", "params", "mode", "sensors", "arena", "seed", "airframe", "team", "arm"]), ...(p.arm ? { confirm: "ARM" } : {}) })),
    ),
    defineTool("program_stop", "Stop program", "Stop the program on an aircraft; the sticks go back to the safety layer (an airborne simulator in a manual mode then descends: land it or stop it).", Type.Object({ vehicle: VID }), claimed((p) => call("POST", `${prog(p.vehicle)}/stop`))),
    defineTool(
      "program_resume",
      "Resume program",
      "Resume a paused program (possession pauses it and releasing does not resume it). A real aircraft waits for the operator's approval.",
      Type.Object({ vehicle: VID }),
      claimed((p) => call("POST", `${prog(p.vehicle)}/resume`)),
    ),
    defineTool(
      "program_params",
      "Tune program",
      "Change parameters of the running program live: {name: value}; type and range follow its PARAMS defaults and PARAM_RANGES. The slow channel by which you steer the fast loop.",
      Type.Object({ vehicle: VID, params: ANY_OBJECT }),
      claimed((p) => call("PATCH", `${prog(p.vehicle)}/params`, p.params)),
    ),
    defineTool("program_status", "Program status", "The program running on an aircraft: id, name, revision, state, status (running, paused, faulted, stopped), frame rate, faults, last error, mode, live params; and the aircraft's armed state, mode, height and controller.", Type.Object({ vehicle: VID }), async (p) => {
      const v = await vehicle(p.vehicle);
      return {
        vehicle: v.id,
        program: v.program ?? null,
        programs_allowed: v.programs_allowed,
        controller: v.controller,
        armed: v.armed,
        landed: v.landed,
        mode: v.mode,
        altitude_m: v.altitude_m,
        possessed: Boolean(v.possession),
        sim: v.sim ? { hover_assist: v.sim.hover_assist, airframe: v.sim.airframe } : null,
      };
    }),
    defineTool(
      "program_log",
      "Program log",
      "Lines the program logged (ctx.log, print), its errors and its events; after: the last seq already read.",
      Type.Object({ vehicle: VID, after: opt(Type.Integer({ minimum: 0 })), limit: opt(Type.Integer({ minimum: 1, maximum: 500 })) }),
      (p) => {
        const q = new URLSearchParams();
        if (p.after !== undefined) q.set("after", String(p.after));
        q.set("limit", String(p.limit ?? 100));
        return call("GET", `${prog(p.vehicle)}/log?${q}`);
      },
    ),
    defineTool("challenge_list", "Challenges", "The flight challenges (liftoff, hover, heading, square, rings, pursuit, duels): scenario, pass condition, best score and status.", Type.Object({}), () => judge("GET", "/challenges")),
    defineTool(
      "challenge_submit",
      "Submit to challenge",
      "Judge a program revision on a challenge in the headless evaluator (faster than real time, visible and hidden seeds with different airframes). wait (default true) waits for the result: score 0-100, passed, metrics per seed, trace id.",
      Type.Object({ challenge: Type.String(), program: PID, rev: opt(Type.Integer({ minimum: 1 })), wait: opt(Type.Boolean()) }),
      async (p, ctx) => {
        const res = await judge("POST", `/challenges/${enc(p.challenge)}/submit`, pick(p, ["program", "rev"]));
        return p.wait === false ? res : waitJob(judge, res, ctx.signal, ctx.onUpdate);
      },
    ),
    defineTool("job_status", "Evaluation job", "State, progress and result of an evaluation job (challenge submission or duel).", Type.Object({ job: Type.String() }), (p) => judge("GET", `/jobs/${enc(p.job)}`)),
    defineTool(
      "duel_run",
      "Run duels",
      "Pit 2 to 8 programs against each other in the arena for some rounds in the headless evaluator; the result has each round's winner, hits and survival times and the Elo table. wait (default true) waits for it.",
      Type.Object({
        programs: Type.Array(PID, { minItems: 2, maxItems: 8 }),
        rounds: opt(Type.Integer({ minimum: 1, maximum: 20 })),
        seed: opt(Type.Integer({ minimum: 0 })),
        scenario: opt(ANY_OBJECT),
        wait: opt(Type.Boolean()),
      }),
      async (p, ctx) => {
        const res = await judge("POST", "/duels", pick(p, ["programs", "rounds", "seed", "scenario"]));
        return p.wait === false ? res : waitJob(judge, res, ctx.signal, ctx.onUpdate);
      },
    ),
    defineTool("ladder", "Ladder", "The Elo ladder of programs from all duels so far.", Type.Object({}), () => judge("GET", "/ladder")),
    defineTool(
      "airframe_set",
      "Set airframe",
      "Fit assembly defects to a simulator (they act with hover assist off): preset ideal | typical | rough with a seed, or params for a custom airframe. Returns the airframe and its hover throttle. Simulators only.",
      Type.Object({ vehicle: VID, preset: opt(lit("ideal", "typical", "rough", "custom")), params: opt(ANY_OBJECT), seed: opt(Type.Integer({ minimum: 0, maximum: 2 ** 31 - 1 })) }),
      (p) => call("PUT", `/sims/${enc(p.vehicle)}/airframe`, pick(p, ["preset", "params", "seed"])),
    ),
  ];
}

/** Names of the program tools, for the renderer's labels and the tests. */
export const PROGRAM_TOOL_NAMES = programTools({ call: async () => ({}) }).map((t) => t.name);
