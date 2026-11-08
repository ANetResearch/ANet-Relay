# Figure-eight evader: fly a figure eight around the arena centre on a position source and stay alive.
# Needs obs.pos and obs.vel (deploy with sensors {position: "gps"} or "uwb"); without them it holds height.
#
# Path: a lemniscate of Gerono, x = A sin(wt), y = (A / 2) sin(2wt), period period_s, around (cx, cy). The
# program tracks the moving point with a velocity feed-forward plus a position correction, and turns the
# commanded acceleration into tilt through the stick map (full stick = 30 degrees). Height holds alt on the
# barometer with an integrator that learns the hover throttle, and weaves by alt_weave so that a gunner
# that matches heights keeps missing.
#
# Evading comes first: a contact that closes faster than threat_closing inside threat_dist is a threat. The
# program then pushes sideways to the line of sight (the side that keeps it inside the arena) and changes
# height for dodge_s, then rejoins the figure eight where the path is by then. It never fires.

import math

PARAMS = {
    "alt": 1.6,
    "alt_weave": 0.35,
    "size": 3.0,
    "period_s": 11.0,
    "cx": 0.0,
    "cy": 0.0,
    "kp_pos": 1.2,
    "kv": 2.2,
    "v_max": 3.0,
    "max_tilt": 0.8,
    "kp": 0.25,
    "kd": 0.18,
    "ki": 0.12,
    "threat_dist": 3.5,
    "threat_closing": 1.2,
    "dodge_s": 0.9,
    "dodge_acc": 6.0,
    "dodge_up": 0.6,
    "team": "",
}
PARAM_RANGES = {
    "alt": [0.6, 3.5],
    "alt_weave": [0.0, 1.0],
    "size": [1.0, 8.0],
    "period_s": [4.0, 40.0],
    "kp_pos": [0.0, 4.0],
    "kv": [0.0, 6.0],
    "v_max": [0.5, 6.0],
    "max_tilt": [0.1, 1.0],
    "threat_dist": [0.5, 10.0],
    "threat_closing": [0.0, 10.0],
    "dodge_s": [0.1, 3.0],
    "dodge_acc": [0.0, 12.0],
    "dodge_up": [0.0, 1.5],
}
G = 9.81
TILT_RAD = 0.5236  # a full roll or pitch stick asks for 30 degrees


def init(ctx):
    ctx.mem["trim"] = 0.5
    ctx.mem["t8"] = None  # time on the figure eight
    ctx.mem["dodge"] = None  # [until, side_e, side_n, dz]
    ctx.state = "climb"


def threat(obs, ctx):
    """The most urgent contact on another team that is closing in, or None."""
    p = ctx.params
    best = None
    for c in obs.contacts or ():
        if c.hp == 0 or c.dist is None or c.rel is None:
            continue
        if p["team"] and c.team == p["team"]:
            continue
        closing = c.get("closing")
        if closing is None or c.dist > p["threat_dist"] or closing < p["threat_closing"]:
            continue
        urgency = closing / max(0.3, c.dist)
        if best is None or urgency > best[0]:
            best = (urgency, c)
    return None if best is None else best[1]


def start_dodge(obs, ctx, c, s, co):
    """Push sideways to the line of sight, toward the arena centre when there is a choice; change height."""
    p = ctx.params
    fwd, right = c.rel[0], c.rel[1]
    n = math.hypot(fwd, right) or 1.0
    # the two sideways directions in the heading frame, then in east / north
    side_f, side_r = -right / n, fwd / n
    e1, n1 = side_f * s + side_r * co, side_f * co - side_r * s
    to_e, to_n = p["cx"] - obs.pos[0], p["cy"] - obs.pos[1]
    if e1 * to_e + n1 * to_n < 0:
        e1, n1 = -e1, -n1
    dz = p["dodge_up"] if obs.alt is not None and obs.alt < p["alt"] else -p["dodge_up"]
    ctx.mem["dodge"] = [obs.t + p["dodge_s"], e1, n1, dz]


def step(obs, ctx):
    p = ctx.params
    if not obs.armed:
        ctx.state = "disarmed"
        ctx.mem["trim"] = 0.5
        return Sticks(throttle=0.0)
    if obs.alt is None or obs.vz is None:
        ctx.state = "no baro"
        return Sticks(throttle=0.0)

    target_alt = p["alt"]
    roll, pitch = 0.0, 0.0
    have_pos = obs.pos is not None and obs.vel is not None and obs.yaw is not None
    if not have_pos:
        ctx.state = "no position"
    elif not obs.airborne or obs.alt < 0.5 * p["alt"]:
        ctx.state = "climb"
    else:
        yaw = math.radians(obs.yaw)
        s, co = math.sin(yaw), math.cos(yaw)
        if ctx.mem["t8"] is None:
            ctx.mem["t8"] = 0.0
        w = 2.0 * math.pi / p["period_s"]
        ctx.mem["t8"] += obs.dt
        t8 = ctx.mem["t8"]
        a = p["size"]
        te = p["cx"] + a * math.sin(w * t8)
        tn = p["cy"] + 0.5 * a * math.sin(2.0 * w * t8)
        fe = a * w * math.cos(w * t8)
        fn = a * w * math.cos(2.0 * w * t8)
        ve = clamp(fe + p["kp_pos"] * (te - obs.pos[0]), -p["v_max"], p["v_max"])
        vn = clamp(fn + p["kp_pos"] * (tn - obs.pos[1]), -p["v_max"], p["v_max"])
        ae = p["kv"] * (ve - obs.vel[0])
        an = p["kv"] * (vn - obs.vel[1])
        target_alt += p["alt_weave"] * math.sin(1.7 * w * t8)

        c = threat(obs, ctx)
        dodge = ctx.mem["dodge"]
        if c is not None and (dodge is None or obs.t >= dodge[0]):
            start_dodge(obs, ctx, c, s, co)
            dodge = ctx.mem["dodge"]
        if dodge is not None and obs.t < dodge[0]:
            ae += p["dodge_acc"] * dodge[1]
            an += p["dodge_acc"] * dodge[2]
            target_alt += dodge[3]
            ctx.state = "dodge"
        else:
            ctx.mem["dodge"] = None
            ctx.state = "eight"

        a_fwd, a_right = ae * s + an * co, ae * co - an * s
        roll = clamp(math.atan2(a_right, G) / TILT_RAD, -p["max_tilt"], p["max_tilt"])
        pitch = clamp(math.atan2(a_fwd, G) / TILT_RAD, -p["max_tilt"], p["max_tilt"])

    if obs.arena is not None:
        target_alt = clamp(target_alt, 0.5, obs.arena.ceiling - 0.6)
    error = clamp(target_alt - obs.alt, -0.6, 0.6)
    if obs.airborne:
        ctx.mem["trim"] = clamp(ctx.mem["trim"] + p["ki"] * error * obs.dt, 0.25, 0.8)
    throttle = ctx.mem["trim"] + p["kp"] * error - p["kd"] * obs.vz
    # leaning loses lift: add it back so the height holds through the turns
    tilt = math.hypot(roll, pitch) * TILT_RAD
    throttle = throttle / max(0.7, math.cos(tilt))
    return Sticks(roll=roll, pitch=pitch, yaw=0.0, throttle=clamp(throttle, 0.0, 1.0))
