// Gimbal pod tools (12 §5): read the pod of an aircraft, run one action, read the photo and recording log. The pod
// is not a flight action: the session that controls the aircraft may use it at every flight level (agent.mjs keeps
// these tools in every scope and at "sticks"), and a pod moves without the operator's approval, also on a real
// aircraft. Names and parameters match the flight MCP server (relay_gimbal_*) and the SDK (gimbal_*).
import { defineTool, defined, enc, flightHelpers, Type, VID } from "./tools-common.mjs";

const opt = (t) => Type.Optional(t);
const lit = (...values) => Type.Union(values.map((x) => Type.Literal(x)));

export const GIMBAL_TOOL_NAMES = ["relay_gimbal_status", "relay_gimbal_control", "relay_gimbal_captures"];
export const GIMBAL_ACTIONS = [
  "rate", "stop", "angle", "center", "follow", "lock", "motors", "zoom", "zoom_rate", "digital_zoom", "lens",
  "palette", "photo", "record", "track", "track_stop", "laser", "focus", "enhance", "osd", "low_light", "scan",
  "scan_stop", "geo_track", "look_at",
]; // prettier-ignore
const LENS = lit("visible1", "visible2", "ir1", "ir2");
const PARAMS = ["yaw_dps", "pitch_dps", "yaw_deg", "pitch_deg", "space", "on", "level", "rate", "lens", "pip", "palette", "x", "y", "z", "frame", "algorithm", "mode", "position", "speed_dps", "lat", "lon", "alt"];

export function gimbalTools(relay) {
  const { claimFor } = flightHelpers(relay);
  return [
    defineTool(
      "relay_gimbal_status",
      "Gimbal status",
      "The gimbal pod of an aircraft: kind (sim | dyt), link, yaw_deg (right +), pitch_deg (up +, looking down is negative), roll_deg, mode, lens (visible1 zoom, visible2 wide, ir1, ir2), pip, zoom and zoom_range, fov_deg [horizontal, vertical], palette, tracking, laser range, recording, photos, motors and age_s. gimbal_missing (404) when the aircraft has none. Angles are the pod's own and do not follow the control frame.",
      Type.Object({ vehicle: VID }),
      (p) => relay.call("GET", `/vehicles/${enc(p.vehicle)}/gimbal`),
    ),
    defineTool(
      "relay_gimbal_control",
      "Gimbal control",
      "Run one gimbal pod action on an aircraft (claims it first); works at every flight level. Angles are the pod's own: yaw right +, pitch up + (looking down is negative), independent of the control frame. " +
        "Actions and parameters: rate {yaw_dps, pitch_dps: +-100, until stop}; stop; angle {yaw_deg +-180, pitch_deg -120..90, space: frame (default, relative to the nose) | earth (compass azimuth, elevation)}; center; follow {on}; lock {on}; motors {on}; " +
        "zoom {level} on the current lens (visible1 1-30, ir1 1-8, the other two are fixed: gimbal_unsupported); zoom_rate {rate -100..100, 0 stops}; digital_zoom {on}; lens {lens, pip? the small picture}; palette {palette: white_hot | black_hot | pseudo}; photo; record {on}; " +
        "track {x, y pixels from the centre of the 1280 x 720 picture, algorithm?}; track_stop; laser {on}; focus {mode: auto | manual, position 4090..49152}; enhance, osd, low_light {on}; scan {yaw_deg, pitch_deg, speed_dps 0.2..51}; scan_stop; geo_track {lat, lon, alt} or {mode: center | exit}; " +
        "look_at {x, y, z, frame?}: a local point in the control frame (flu east-north-up, frd north-east-down) the pod then points at. Returns the pod's view right after. Errors: gimbal_missing, gimbal_offline, gimbal_unsupported, gimbal_busy, invalid_request.",
      Type.Object({
        vehicle: VID,
        action: lit(...GIMBAL_ACTIONS),
        yaw_dps: opt(Type.Number({ minimum: -100, maximum: 100, description: "rate: right +" })),
        pitch_dps: opt(Type.Number({ minimum: -100, maximum: 100, description: "rate: up +" })),
        yaw_deg: opt(Type.Number({ minimum: -180, maximum: 180, description: "angle, scan: right +" })),
        pitch_deg: opt(Type.Number({ minimum: -120, maximum: 90, description: "angle, scan: up +" })),
        space: opt(lit("frame", "earth")),
        on: opt(Type.Boolean({ description: "follow, lock, motors, digital_zoom, record, laser, enhance, osd, low_light" })),
        level: opt(Type.Number({ exclusiveMinimum: 0, description: "zoom: times, on the current lens" })),
        rate: opt(Type.Number({ minimum: -100, maximum: 100, description: "zoom_rate" })),
        lens: opt(LENS),
        pip: opt(LENS),
        palette: opt(lit("white_hot", "black_hot", "pseudo")),
        x: opt(Type.Number({ description: "track: pixel, right +; look_at: local x" })),
        y: opt(Type.Number({ description: "track: pixel, down +; look_at: local y" })),
        z: opt(Type.Number({ description: "look_at: local z" })),
        frame: opt(lit("flu", "frd")),
        algorithm: opt(lit("auto", "person", "vehicle", "building")),
        mode: opt(lit("auto", "manual", "center", "exit")),
        position: opt(Type.Integer({ minimum: 4090, maximum: 49152 })),
        speed_dps: opt(Type.Number({ minimum: 0.2, maximum: 51 })),
        lat: opt(Type.Number({ minimum: -90, maximum: 90 })),
        lon: opt(Type.Number({ minimum: -180, maximum: 180 })),
        alt: opt(Type.Number({ minimum: -6000, maximum: 6000, description: "geo_track: metres above sea level" })),
      }),
      async (p) => {
        await claimFor(p.vehicle);
        return relay.call("POST", `/vehicles/${enc(p.vehicle)}/gimbal`, { action: p.action, ...defined(Object.fromEntries(PARAMS.map((k) => [k, p[k]]))) });
      },
    ),
    defineTool(
      "relay_gimbal_captures",
      "Gimbal captures",
      "Photos and recording starts and stops of the aircraft's pod, newest last: seq, kind (photo | record_start | record_stop), time, the aircraft's nav pose, the gimbal angles, lens, zoom, laser range and the target the pod solved.",
      Type.Object({ vehicle: VID, limit: opt(Type.Integer({ minimum: 1, maximum: 500 })) }),
      (p) => relay.call("GET", `/vehicles/${enc(p.vehicle)}/gimbal/captures${p.limit ? `?limit=${p.limit}` : ""}`),
    ),
  ];
}
