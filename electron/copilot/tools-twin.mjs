// Firmware twin tools (11 §12): the official PX4 or ArduPilot SITL firmware, with a real aircraft's version and
// parameters, flies against Relay's physics and appears as a simulator (sim-N) that every flight tool can fly. Names
// and parameters match the flight MCP server and the SDK. Reading needs nothing; capturing a profile and starting a
// twin are application operations like adding simulators, and a frame change follows the airframe rule. A twin is a
// simulator, so none of them needs approval; hardware in the loop (relay_hitl, tools-app.mjs) always does.
import { defineTool, enc, Type, VID } from "./tools-common.mjs";

const opt = (t) => Type.Optional(t);
const lit = (...values) => Type.Union(values.map((x) => Type.Literal(x)));

export const TWIN_TOOL_NAMES = [
  "relay_autopilots_list",
  "relay_firmware_identify",
  "relay_twin_profiles_list",
  "relay_twin_profile_get",
  "relay_twin_profile_capture",
  "relay_twin_start",
  "relay_twin_status",
  "relay_sim_frame",
];

/** 11 §5, §8.1: the frame fields; omitted values keep the preset's (or the current frame's on a change). */
const FRAME_FIELDS = {
  preset: opt(lit("micro-120", "small-250", "large-450")),
  mass_kg: opt(Type.Number({ exclusiveMinimum: 0, description: "take-off mass, kg" })),
  wheelbase_m: opt(Type.Number({ exclusiveMinimum: 0, description: "motor-to-motor diagonal, m" })),
  prop_in: opt(Type.Number({ exclusiveMinimum: 0, description: "propeller diameter, inches" })),
  cells: opt(Type.Integer({ minimum: 1, maximum: 14, description: "battery cells (S)" })),
  capacity_mah: opt(Type.Number({ exclusiveMinimum: 0 })),
};
export const FRAME = Type.Object(FRAME_FIELDS, { description: "physics frame: a preset and any of its values" });
export const HOME = Type.Object(
  {
    lat: Type.Number({ minimum: -89, maximum: 89 }),
    lon: Type.Number({ minimum: -180, maximum: 180 }),
    alt_m: opt(Type.Number({ minimum: -500, maximum: 9000, description: "above mean sea level" })),
  },
  { description: "home of the twin (omitted: the profile's)" },
);
const PROFILE = Type.String({ description: "twin profile id from relay_twin_profiles_list, e.g. tp-1a2b3c4d" });
const PACK = Type.String({ description: "runtime pack id from relay_autopilots_list, e.g. ardupilot-copter-4.7.2" });

/** Drop the undefined values of a body. */
const defined = (o) => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined));

export function twinTools(relay) {
  const call = (method, path, body) => relay.call(method, path, body);

  return [
    defineTool(
      "relay_autopilots_list",
      "Autopilot packs",
      "Installed autopilot runtime packs (official PX4 and ArduPilot SITL builds bundled with Relay or imported): id, family, version, git, size, license, origin, whether usable and how many twins run on each.",
      Type.Object({}),
      () => call("GET", "/autopilots"),
    ),
    defineTool(
      "relay_firmware_identify",
      "Identify firmware",
      "Firmware identity of an aircraft (family, version, git, board, from AUTOPILOT_VERSION), its parameter progress, whether a twin can fly its frame, and the matching runtime packs with the recommended one and the match level (exact, version, series, nearest, none). Below version, say that the control loop and parameter meanings may differ from the real aircraft.",
      Type.Object({ vehicle: VID }),
      (p) => call("GET", `/vehicles/${enc(p.vehicle)}/firmware`),
    ),
    defineTool("relay_twin_profiles_list", "Twin profiles", "Saved twin profiles: firmware identity, pack, match level, frame, home and how many parameters apply.", Type.Object({}), () =>
      call("GET", "/twins/profiles"),
    ),
    defineTool(
      "relay_twin_profile_get",
      "Read twin profile",
      "One twin profile with every parameter and the hardware-specific ones left out, each with its reason.",
      Type.Object({ profile: PROFILE }),
      (p) => call("GET", `/twins/profiles/${enc(p.profile)}`),
    ),
    defineTool(
      "relay_twin_profile_capture",
      "Capture twin profile",
      "Capture a twin profile from a connected real aircraft: its firmware identity and whole parameter table (hardware-specific parameters are left out). Fails with params_incomplete until every parameter is read and real_only for a simulator. profile recaptures into an existing profile (its name, frame and home stay); frame omitted: guessed from the battery.",
      Type.Object({ vehicle: VID, name: opt(Type.String({ maxLength: 80 })), frame: opt(FRAME), profile: opt(PROFILE) }),
      (p) => call("POST", "/twins/profiles", defined({ from_vehicle: p.vehicle, name: p.name, frame: p.frame, profile: p.profile })),
    ),
    defineTool(
      "relay_twin_start",
      "Start firmware twin",
      "Start firmware twins: the official PX4 or ArduPilot firmware runs against Relay's physics and appears as simulators (sim-N) that every flight tool flies, with the real control loop, preflight checks and failsafes. From a twin profile (the real aircraft's version and parameters) or a pack with its default parameters; neither means the newest ArduPilot pack. Returns the new ids once each firmware sends its first heartbeat (up to 15 s). Errors: twin_start_failed (detail.reason, detail.lines), twin_limit, pack_missing, frame_unsupported.",
      Type.Object({ profile: opt(PROFILE), pack: opt(PACK), frame: opt(FRAME), home: opt(HOME), count: opt(Type.Integer({ minimum: 1, maximum: 8 })) }),
      (p) => call("POST", "/sims", defined({ count: p.count ?? 1, engine: "sitl", profile: p.profile, pack: p.pack, frame: p.frame, home: p.home })),
    ),
    defineTool(
      "relay_twin_status",
      "Twin status",
      "A firmware twin's process state, pack, match level, profile, parameters applied and left out, frame, home, restarts, exit code with the last output, and lockstep figures (steps, stalls).",
      Type.Object({ vehicle: VID }),
      (p) => call("GET", `/sims/${enc(p.vehicle)}/twin`),
    ),
    defineTool(
      "relay_sim_frame",
      "Change simulator frame",
      "Change the physics frame of a disarmed simulator or firmware twin on the ground: a preset (micro-120, small-250, large-450) and any of mass, wheelbase, propeller, cells, capacity. A twin recomputes the motor thrust from its parameters. Fails with armed while armed or airborne.",
      Type.Object({ vehicle: VID, ...FRAME_FIELDS }),
      ({ vehicle, ...frame }) => call("PUT", `/sims/${enc(vehicle)}/frame`, defined(frame)),
    ),
  ];
}
