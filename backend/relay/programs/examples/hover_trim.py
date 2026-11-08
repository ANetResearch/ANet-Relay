# Hover trim: climb to a target height and hold it on the barometer, with an integrator that learns the
# hover throttle (battery sag, payload and ground effect move it).
#
# Drift hold without a position source (WP-2b, 06 §3.2 "hover"): a centre of gravity off the middle and a
# level calibration error make the aircraft lean and slide with centred sticks. The board cannot see the
# level error, but the accelerometer can see the air: thrust acts along the body axis, so the forward and
# right specific forces are the drag of the airframe moving through the air (about -drag * velocity).
# On the ground the program first averages the accelerometer (the mount error), then in the air it turns
# the remainder into a velocity, integrates it into a dead-reckoned position and holds that position with
# small roll and pitch commands (proportional, damping and a slow integral that learns the lean).
import math

PARAMS = {
    "target_alt": 1.0,
    "kp": 0.25,
    "kd": 0.18,
    "ki": 0.12,
    "hover": 0.5,
    "climb_limit": 0.5,
    "drift": True,
    "drag": 0.35,
    "pos_kp": 0.6,
    "pos_kv": 0.9,
    "pos_ki": 0.05,
    "level_s": 3.0,
}
PARAM_RANGES = {
    "target_alt": [0.3, 3.0],
    "kp": [0.0, 1.0],
    "kd": [0.0, 1.0],
    "ki": [0.0, 1.0],
    "hover": [0.2, 0.8],
    "climb_limit": [0.1, 1.0],
    "drag": [0.05, 2.0],
    "pos_kp": [0.0, 3.0],
    "pos_kv": [0.0, 3.0],
    "pos_ki": [0.0, 1.0],
    "level_s": [0.2, 5.0],
}
TILT_RAD = 0.5236  # a full roll or pitch stick asks for 30 degrees (ANGLE_MAX)
LEAN_MAX = 0.2  # largest roll or pitch stick the drift hold uses


def init(ctx):
    ctx.mem["trim"] = ctx.params["hover"]
    ctx.mem["level"] = [0.0, 0.0, 0.0, 0]  # accelerometer sums on the ground and their count
    ctx.mem["bias"] = None
    ctx.mem["x"] = [0.0, 0.0]  # dead-reckoned east / north offset since take-off
    ctx.mem["ix"] = [0.0, 0.0]
    ctx.mem["v"] = [0.0, 0.0]
    ctx.state = "climb"


def level(obs, ctx):
    """Average the resting accelerometer: what it reads with the frame level is the mount error."""
    if obs.ax is None or obs.ay is None or obs.az is None:
        return
    sums = ctx.mem["level"]
    sums[0] += obs.ax
    sums[1] += obs.ay
    sums[2] += obs.az
    sums[3] += 1
    if sums[3] * obs.dt >= ctx.params["level_s"]:
        n = sums[3]
        ctx.mem["bias"] = [sums[0] / n, sums[1] / n, sums[2] / n]


def drift_hold(obs, ctx):
    """(roll, pitch) sticks that hold the dead-reckoned position; level sticks without a bias."""
    p = ctx.params
    bias = ctx.mem["bias"]
    if not p["drift"] or bias is None or obs.ax is None or obs.yaw is None:
        return 0.0, 0.0
    # the mount error grows with the thrust: scale the resting reading by the vertical specific force
    scale = obs.az / bias[2] if bias[2] > 1.0 else 1.0
    v_fwd = -(obs.ax - bias[0] * scale) / p["drag"]
    v_right = -(obs.ay - bias[1] * scale) / p["drag"]
    yaw = math.radians(obs.yaw)
    s, c = math.sin(yaw), math.cos(yaw)
    ve, vn = v_fwd * s + v_right * c, v_fwd * c - v_right * s
    x, ix, v = ctx.mem["x"], ctx.mem["ix"], ctx.mem["v"]
    x[0] += ve * obs.dt
    x[1] += vn * obs.dt
    v[0] = lowpass(v[0], ve, 0.15)
    v[1] = lowpass(v[1], vn, 0.15)
    ix[0] = clamp(ix[0] + x[0] * obs.dt, -2.0, 2.0)
    ix[1] = clamp(ix[1] + x[1] * obs.dt, -2.0, 2.0)
    ae = -p["pos_kp"] * x[0] - p["pos_kv"] * v[0] - p["pos_ki"] * ix[0]
    an = -p["pos_kp"] * x[1] - p["pos_kv"] * v[1] - p["pos_ki"] * ix[1]
    a_fwd, a_right = ae * s + an * c, ae * c - an * s
    roll = clamp(a_right / 9.81 / TILT_RAD, -LEAN_MAX, LEAN_MAX)
    pitch = clamp(a_fwd / 9.81 / TILT_RAD, -LEAN_MAX, LEAN_MAX)
    return roll, pitch


def step(obs, ctx):
    p = ctx.params
    if obs.alt is None or obs.vz is None:
        ctx.state = "no baro"
        return Sticks(throttle=0.0)
    if not obs.airborne and obs.alt < 0.05:
        level(obs, ctx)
    if not obs.armed:
        ctx.state = "disarmed"
        ctx.mem["trim"] = p["hover"]
        return Sticks(throttle=0.0)
    if not obs.airborne and p["drift"] and obs.ax is not None and ctx.mem["bias"] is None:
        ctx.state = "level"
        return Sticks(throttle=0.0)
    error = clamp(p["target_alt"] - obs.alt, -p["climb_limit"], p["climb_limit"])
    roll, pitch = 0.0, 0.0
    if obs.airborne:
        ctx.mem["trim"] = clamp(ctx.mem["trim"] + p["ki"] * error * obs.dt, 0.25, 0.8)
        roll, pitch = drift_hold(obs, ctx)
    throttle = ctx.mem["trim"] + p["kp"] * error - p["kd"] * obs.vz
    ctx.state = "hold" if abs(p["target_alt"] - obs.alt) < 0.1 else "climb"
    return Sticks(roll=roll, pitch=pitch, throttle=clamp(throttle, 0.0, 1.0))
