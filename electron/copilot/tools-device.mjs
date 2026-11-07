// Device tools (04 §2, §6.2): one tool per device-level endpoint, down to a single motor, actuator or MAVLink
// command. Names match the device MCP server (device_*). Tools with a danger level fill in the confirm word
// themselves: the operator's approval in the chat is the confirmation for a real aircraft (agent.mjs gates them).
import { defineTool, enc, flightHelpers, Type, VID } from "./tools-common.mjs";

const num = (d, o = {}) => Type.Number({ description: d, ...o });
const opt = (t) => Type.Optional(t);

export const CONFIRM_WORDS = {
  motor: "MOTOR",
  arm: "ARM",
  force_arm: "FORCE",
  reboot: "REBOOT",
  calibrate: "CALIBRATE",
  terminate: "TERMINATE",
};

/** Device tools that always carry a danger level (device_command and device_arm depend on their arguments). */
const STATIC_DANGER = {
  device_motor_test: "motor",
  device_actuator: "motor",
  device_reboot: "reboot",
  device_calibrate: "calibrate",
  device_terminate: "terminate",
  device_rc: "rc",
  device_attitude: "motion",
  device_velocity: "motion",
  device_param_set: "param",
  device_mode: "motion",
  device_procedure_start: "motion", // WP-B: a bench procedure may spin motors and fly (05 §4)
};

/** WP-B (05 §4.3): the danger of answering a bench prompt follows the operation the prompt starts. */
const PROMPT_DANGER = {
  props_removed: "motor",
  motor_check: "motor", // every answer but none moves on to spinning the next motor
  arm_start: "arm",
  calibrate_gyro: "calibrate",
  calibrate_level: "calibrate",
  calibrate_baro: "calibrate",
  apply_param: "param",
};

/** The prompt of the waiting step of a vehicle's bench run, or null. */
export async function waitingPrompt(relay, vehicle, step) {
  try {
    const run = await relay.call("GET", `/vehicles/${enc(vehicle)}/procedure`);
    const s = (run?.steps ?? []).find((x) => x.id === (step ?? run?.step));
    return s?.state === "waiting" ? (s.prompt ?? null) : null;
  } catch {
    return null;
  }
}

/** Raw commands that move an aircraft (backend fleet_device.MOTION_COMMANDS): approved like any motion. */
const MOTION_COMMANDS = new Set(["DO_SET_MODE", "NAV_TAKEOFF", "NAV_LAND", "NAV_RETURN_TO_LAUNCH", "NAV_LOITER_UNLIM", "DO_REPOSITION", "MISSION_START", "DO_PAUSE_CONTINUE"]);
const FORCE_ARM_MAGIC = 21196;
const MAX_MOTORS = 8; // outputs 1..8 may drive a motor (backend device.is_motor_output with the largest frame)

/** The level the backend will apply to a raw command (device.danger_level): parameters can raise it. */
function rawLevel(e, params) {
  const name = String(e.name).replace(/^MAV_CMD_/, "");
  const p = params ?? [];
  if (name === "COMPONENT_ARM_DISARM" && Number(p[1]) === FORCE_ARM_MAGIC) return "force_arm";
  if ((name === "DO_SET_SERVO" || name === "DO_REPEAT_SERVO") && Number(p[0]) >= 1 && Number(p[0]) <= MAX_MOTORS) return "motor";
  if (e.danger) return e.danger;
  return MOTION_COMMANDS.has(name) ? "motion" : null;
}

/**
 * The danger level of a tool call, or null when it needs no approval. Raw commands are looked up in the command
 * catalogue; a command the catalogue does not know counts as dangerous.
 */
export function dangerResolver(relay) {
  let catalogue = null;
  /** The catalogue, or null when it cannot be read (not cached: the next call tries again). */
  const load = async () => {
    if (catalogue) return catalogue;
    try {
      const res = await relay.call("GET", "/commands");
      catalogue = Array.isArray(res) ? res : (res.commands ?? []);
      return catalogue;
    } catch {
      return null;
    }
  };
  const entry = async (command) => {
    const wanted = String(command).replace(/^MAV_CMD_/, "").toUpperCase();
    const list = await load();
    return list?.find((c) => String(c.name).replace(/^MAV_CMD_/, "").toUpperCase() === wanted || String(c.id) === wanted) ?? null;
  };
  const danger = async (name, args) => {
    if (name === "device_arm") return args.force ? "force_arm" : "arm";
    if (name === "device_procedure_answer") {
      // "keep" and "skip" start nothing; every other answer takes the level of the prompt's operation
      if (args.answer === "keep" || args.answer === "skip") return null;
      const prompt = await waitingPrompt(relay, args.vehicle, args.step);
      return prompt ? (PROMPT_DANGER[prompt.id] ?? null) : null;
    }
    if (name === "device_command") {
      const e = await entry(args.command);
      return e ? rawLevel(e, args.params) : "raw"; // unknown or unreadable: dangerous
    }
    return STATIC_DANGER[name] ?? null;
  };
  return Object.assign(danger, { entry });
}

/** The confirm word to send with a dangerous call, from its danger level or catalogue entry. */
export function confirmFor(danger, entry) {
  // the level wins over the entry: parameters can raise a command to MOTOR or FORCE
  return CONFIRM_WORDS[danger] ?? entry?.confirm ?? undefined;
}

export function deviceTools(relay, danger = dangerResolver(relay)) {
  const { vehicle, skill, claimFor } = flightHelpers(relay);
  const v = (p) => `/vehicles/${enc(p.vehicle)}`;
  const call = (method, path, body) => relay.call(method, path, body);
  // Writes need the controller role: claim first (a person who possesses the aircraft still wins). Reads that the
  // backend also allows an operator to make try the claim and let the backend decide.
  const claimed = (fn, strict = true) => async (p, ctx) => {
    try {
      await claimFor(p.vehicle);
    } catch (err) {
      if (strict) throw err;
    }
    return fn(p, ctx);
  };

  return [
    defineTool("device_overview", "Device overview", "Motors (count, layout, output PWM), servos, RC channels, frame, sensors, estimator, vibration, home, parameters loaded and message rates of one aircraft.", Type.Object({ vehicle: VID }), (p) =>
      call("GET", `${v(p)}/device`),
    ),
    defineTool("device_preflight", "Preflight checks", "Preflight check items of one aircraft with their state.", Type.Object({ vehicle: VID }), (p) => call("GET", `${v(p)}/preflight`)),
    defineTool("device_modes", "Flight modes", "Current mode and the list of modes the autopilot offers.", Type.Object({ vehicle: VID }), async (p) => {
      const a = await vehicle(p.vehicle);
      return { mode: a.mode, modes: a.modes };
    }),
    defineTool("device_messages", "MAVLink messages", "Every MAVLink message type seen from the aircraft with its latest frame time and rate.", Type.Object({ vehicle: VID }), (p) =>
      call("GET", `${v(p)}/messages`),
    ),
    defineTool("device_message", "MAVLink message", "Latest frame of one MAVLink message, e.g. ATTITUDE, SERVO_OUTPUT_RAW, RC_CHANNELS, BATTERY_STATUS.", Type.Object({ vehicle: VID, name: Type.String({ description: "Message name, e.g. SERVO_OUTPUT_RAW" }) }), (p) =>
      call("GET", `${v(p)}/messages/${enc(p.name)}`),
    ),
    defineTool("device_param_get", "Read parameter", "Read one autopilot parameter by name (waits up to 2 s when the table is not loaded).", Type.Object({ vehicle: VID, name: Type.String() }), (p) =>
      call("GET", `${v(p)}/params/${enc(p.name)}`),
    ),
    defineTool(
      "device_params_read",
      "Read parameters",
      "Read up to 32 parameters by name in one call. Returns values and the names that were not found.",
      Type.Object({ vehicle: VID, names: Type.Array(Type.String(), { minItems: 1, maxItems: 32 }) }),
      claimed((p) => call("POST", `${v(p)}/params/read`, { names: p.names }), false),
    ),
    defineTool(
      "device_params_list",
      "List parameters",
      "The loaded parameter table, optionally filtered by a name prefix (for example SERVO or MOT_). Returns at most `limit` entries.",
      Type.Object({ vehicle: VID, prefix: opt(Type.String()), limit: opt(num("Maximum entries, default 200", { minimum: 1, maximum: 2000 })) }),
      async (p) => {
        const res = await call("GET", `${v(p)}/params`);
        const all = Array.isArray(res) ? res : (res.params ?? res.items ?? []);
        const prefix = (p.prefix ?? "").toUpperCase();
        const rows = all.filter((x) => !prefix || String(x.name).toUpperCase().startsWith(prefix));
        const limit = p.limit ?? 200;
        return { total: rows.length, shown: Math.min(rows.length, limit), params: rows.slice(0, limit) };
      },
    ),
    defineTool("device_params_refresh", "Refresh parameters", "Ask the autopilot to resend its whole parameter table.", Type.Object({ vehicle: VID }), claimed((p) => call("POST", `${v(p)}/params/refresh`), false),
    ),
    defineTool(
      "device_param_set",
      "Write parameter",
      "Write one autopilot parameter. Changes flight behaviour: a real aircraft asks the operator first. Read the old value first and report it.",
      Type.Object({ vehicle: VID, name: Type.String(), value: Type.Number() }),
      claimed((p) => call("POST", `${v(p)}/params`, { name: p.name, value: p.value, confirm: p.name })),
    ),
    defineTool("device_commands", "Command catalogue", "The raw MAVLink commands device_command accepts, with parameters, danger level and conditions.", Type.Object({}), () => call("GET", "/commands")),
    defineTool(
      "device_command",
      "Raw MAVLink command",
      "Send one MAV_CMD from the catalogue (see device_commands) with params p1..p7 and wait for COMMAND_ACK. Use a higher-level tool when one exists.",
      Type.Object({
        vehicle: VID,
        command: Type.Union([Type.String(), Type.Number()], { description: 'e.g. "MAV_CMD_DO_SET_SERVO" or 183' }),
        params: opt(Type.Array(Type.Number(), { maxItems: 7, description: "p1..p7, missing ones are 0" })),
        frame: opt(Type.Integer()),
        use_int: opt(Type.Boolean()),
        timeout_s: opt(num("Seconds to wait for the ack, at most 10", { minimum: 0.5, maximum: 10 })),
      }),
      claimed(async (p) => {
        const body = { command: p.command, params: p.params ?? [], frame: p.frame, use_int: p.use_int, timeout_s: p.timeout_s };
        const level = await danger("device_command", p);
        if (level) body.confirm = confirmFor(level, await danger.entry(p.command));
        return call("POST", `${v(p)}/command`, body);
      }),
    ),
    defineTool(
      "device_motor_test",
      "Motor test",
      "Spin one motor (1..8) or all in turn at throttle_pct for seconds, with the aircraft disarmed and propellers removed on real aircraft (up to 30 % real, 100 % simulated).",
      Type.Object({
        vehicle: VID,
        motor: Type.Union([Type.Integer({ minimum: 1, maximum: 8 }), Type.Literal("all")]),
        throttle_pct: num("Throttle percent", { minimum: 0, maximum: 100 }),
        seconds: num("Duration", { minimum: 0.2, maximum: 5 }),
      }),
      claimed((p) => call("POST", `${v(p)}/motors/test`, { motor: p.motor, throttle_pct: p.throttle_pct, seconds: p.seconds, confirm: CONFIRM_WORDS.motor })),
    ),
    defineTool("device_motors_stop", "Stop motor tests", "Stop every running motor test immediately.", Type.Object({ vehicle: VID }), claimed((p) => call("POST", `${v(p)}/motors/stop`), false)),
    defineTool(
      "device_actuator",
      "Actuator output",
      "Drive one output channel (1..16) with value -1..1 or a pwm of 800..2200 for seconds (0 = hold). A motor channel counts as a motor test and needs the aircraft disarmed.",
      Type.Object({
        vehicle: VID,
        index: Type.Integer({ minimum: 1, maximum: 16 }),
        value: opt(num("Normalised -1..1", { minimum: -1, maximum: 1 })),
        pwm: opt(num("Microseconds", { minimum: 800, maximum: 2200 })),
        seconds: opt(num("0 holds until changed", { minimum: 0, maximum: 10 })),
      }),
      claimed((p) => call("POST", `${v(p)}/actuators/set`, { index: p.index, value: p.value, pwm: p.pwm, seconds: p.seconds, confirm: CONFIRM_WORDS.motor })),
    ),
    defineTool(
      "device_rc",
      "RC channel override",
      "Override RC channels for one frame (it expires after 0.8 s; repeat to hold). Use channels {\"1\": 1500, ...} in microseconds or axes {roll, pitch, yaw, throttle, aux1..aux6} in -1..1.",
      Type.Object({
        vehicle: VID,
        channels: opt(Type.Record(Type.String(), Type.Number({ minimum: 1000, maximum: 2000 }))),
        axes: opt(Type.Record(Type.String(), Type.Number({ minimum: -1, maximum: 1 }))),
      }),
      claimed((p) => call("POST", `${v(p)}/rc`, { channels: p.channels, axes: p.axes })),
    ),
    defineTool(
      "device_attitude",
      "Attitude target",
      "Hold a roll / pitch attitude with a yaw rate and thrust for seconds (up to 10), then return to altitude hold. Aircraft must be armed and airborne.",
      Type.Object({
        vehicle: VID,
        roll_deg: num("-30..30", { minimum: -30, maximum: 30 }),
        pitch_deg: num("-30..30, negative = nose down", { minimum: -30, maximum: 30 }),
        yaw_rate_dps: opt(num("-90..90", { minimum: -90, maximum: 90 })),
        thrust: num("0..1", { minimum: 0, maximum: 1 }),
        seconds: num("(0, 10]", { minimum: 0.1, maximum: 10 }),
      }),
      (p, ctx) => skill(p.vehicle, "attitude", { roll_deg: p.roll_deg, pitch_deg: p.pitch_deg, yaw_rate_dps: p.yaw_rate_dps ?? 0, thrust: p.thrust, seconds: p.seconds }, ctx.signal),
    ),
    defineTool(
      "device_velocity",
      "Velocity target",
      "Fly a velocity (ENU, m/s, each at most 2) for seconds (up to 10). Needs a position estimate and stays inside the safety volume.",
      Type.Object({
        vehicle: VID,
        vx: num("East m/s", { minimum: -2, maximum: 2 }),
        vy: num("North m/s", { minimum: -2, maximum: 2 }),
        vz: num("Up m/s", { minimum: -2, maximum: 2 }),
        yaw_rate_dps: opt(num("-90..90", { minimum: -90, maximum: 90 })),
        seconds: num("(0, 10]", { minimum: 0.1, maximum: 10 }),
      }),
      (p, ctx) => skill(p.vehicle, "velocity", { vx: p.vx, vy: p.vy, vz: p.vz, yaw_rate_dps: p.yaw_rate_dps, seconds: p.seconds }, ctx.signal),
    ),
    defineTool(
      "device_message_interval",
      "Message rate",
      "Set how often the aircraft streams one MAVLink message (hz 0..50, 0 stops it).",
      Type.Object({ vehicle: VID, message: Type.String({ description: "e.g. ATTITUDE" }), hz: num("0..50", { minimum: 0, maximum: 50 }) }),
      claimed((p) => call("POST", `${v(p)}/message-interval`, { message: p.message, hz: p.hz }), false),
    ),
    defineTool("device_request_message", "Request message", "Ask the aircraft to send one MAVLink message once.", Type.Object({ vehicle: VID, message: Type.String() }), claimed((p) => call("POST", `${v(p)}/request-message`, { message: p.message }), false),
    ),
    defineTool("device_reboot", "Reboot autopilot", "Reboot the flight controller. Aircraft must be disarmed. Link drops for several seconds.", Type.Object({ vehicle: VID }), claimed((p) => call("POST", `${v(p)}/reboot`, { confirm: CONFIRM_WORDS.reboot })),
    ),
    defineTool(
      "device_calibrate",
      "Calibrate sensor",
      "Calibrate a sensor on a disarmed aircraft that sits level and still: gyro, level, baro or accel_simple.",
      Type.Object({ vehicle: VID, sensor: Type.Union(["gyro", "level", "baro", "accel_simple"].map((s) => Type.Literal(s))) }),
      claimed((p) => call("POST", `${v(p)}/calibrate`, { sensor: p.sensor, confirm: CONFIRM_WORDS.calibrate })),
    ),
    defineTool(
      "device_home",
      "Set home",
      "Set the home position to the current position (here: true) or to lat, lon (degrees) and alt (metres).",
      Type.Object({ vehicle: VID, here: opt(Type.Boolean()), lat: opt(Type.Number()), lon: opt(Type.Number()), alt: opt(Type.Number()) }),
      claimed((p) => call("POST", `${v(p)}/home`, p.here ? { here: true } : { lat: p.lat, lon: p.lon, alt: p.alt })),
    ),
    defineTool("device_terminate", "Flight termination", "EMERGENCY ONLY: terminate the flight, which stops the motors at once and force-disarms. An airborne aircraft falls.", Type.Object({ vehicle: VID }), claimed((p) => call("POST", `${v(p)}/terminate`, { confirm: CONFIRM_WORDS.terminate })),
    ),
    defineTool(
      "device_arm",
      "Arm",
      "Arm the motors. force: true skips the autopilot's own pre-arm checks and is a last resort. Prefer relay_takeoff, which arms for you.",
      Type.Object({ vehicle: VID, force: opt(Type.Boolean()) }),
      claimed((p) => call("POST", `${v(p)}/arm`, p.force ? { arm: true, force: true, confirm: CONFIRM_WORDS.force_arm } : { arm: true, confirm: CONFIRM_WORDS.arm })),
    ),
    defineTool("device_disarm", "Disarm", "Disarm the motors (refused while flying; use relay_land).", Type.Object({ vehicle: VID }), claimed((p) => call("POST", `${v(p)}/arm`, { arm: false }))),
    defineTool("device_mode", "Set flight mode", "Switch the flight mode by name (see device_modes).", Type.Object({ vehicle: VID, name: Type.String() }), claimed((p) => call("POST", `${v(p)}/mode`, { name: p.name })),
    ),
    // bench procedures (WP-B, 05 §4): the run is driven step by step; prompts marked operator_only are answered
    // by the person at the Relay window, and the approval in the chat supplies a prompt's confirm word.
    defineTool("device_procedure_list", "Bench procedures", "The bench procedures (full_bench, preflight_quick, motor_check, vibration_check, hover_tune) with their steps and estimated duration.", Type.Object({}), () =>
      call("GET", "/procedures"),
    ),
    defineTool(
      "device_procedure_start",
      "Start bench procedure",
      "Start a bench procedure on a disarmed aircraft on the ground. Steps that spin motors or fly ask a prompt first; flight steps take off to 0.6 m and land by themselves. auto_answer (simulators only) answers the motor check from the servo outputs.",
      Type.Object({
        vehicle: VID,
        procedure: Type.Union(["full_bench", "preflight_quick", "motor_check", "vibration_check", "hover_tune"].map((s) => Type.Literal(s))),
        hover_s: opt(num("Hover length of S9, 5..60 s", { minimum: 5, maximum: 60 })),
        auto_answer: opt(Type.Boolean()),
      }),
      claimed((p) => {
        const options = {};
        if (p.hover_s !== undefined) options.hover_s = p.hover_s;
        if (p.auto_answer) options.auto_answer = true;
        return call("POST", `${v(p)}/procedure`, { procedure: p.procedure, options });
      }),
    ),
    defineTool("device_procedure_status", "Bench procedure status", "The current or most recent bench run: state, current step and its prompt (answers, operator_only), and per step the verdict, metrics, findings and suggested parameter changes.", Type.Object({ vehicle: VID }), async (p) => {
      const run = await call("GET", `${v(p)}/procedure`);
      for (const s of run?.steps ?? []) delete s.live; // the live chart series are for the window
      return run;
    }),
    defineTool(
      "device_procedure_answer",
      "Answer bench prompt",
      "Answer the waiting prompt of a bench step with one of prompt.answers[].id. Prompts with operator_only: true are for the person at the Relay window: tell the operator instead of answering.",
      Type.Object({ vehicle: VID, step: Type.String({ description: "Waiting step id, e.g. S4" }), answer: Type.String() }),
      claimed(async (p) => {
        const body = { step: p.step, answer: p.answer };
        const prompt = await waitingPrompt(relay, p.vehicle, p.step);
        if (prompt?.confirm_word) body.confirm = prompt.confirm_word;
        return call("POST", `${v(p)}/procedure/answer`, body);
      }),
    ),
    defineTool("device_procedure_abort", "Abort bench procedure", "Abort the running bench procedure: motor tests stop and an airborne aircraft lands.", Type.Object({ vehicle: VID }), claimed((p) => call("POST", `${v(p)}/procedure/abort`), false)),
    defineTool("device_test_runs", "Bench reports", "Saved bench reports, newest first, optionally for one aircraft.", Type.Object({ vehicle: opt(VID), limit: opt(Type.Integer({ minimum: 1, maximum: 200 })) }), (p) => {
      const q = new URLSearchParams();
      if (p.vehicle) q.set("vehicle", p.vehicle);
      q.set("limit", String(p.limit ?? 20));
      return call("GET", `/test-runs?${q}`);
    }),
    defineTool("device_test_run", "Bench report", "One saved bench report with every step, metric, finding and suggestion.", Type.Object({ run_id: Type.Integer() }), (p) => call("GET", `/test-runs/${enc(p.run_id)}`)),
  ];
}
