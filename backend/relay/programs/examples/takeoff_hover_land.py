# Take off, hover, land: the plan "take off to 2 m, hover, land" as a program, with every number a parameter
# that the Params tab tunes while it flies. Raise "hover" when the aircraft does not lift off or sinks in the
# hold; "climb" is the extra throttle while climbing, "descent" the sink rate of the landing. With "learn" on,
# an integrator finds the throttle by itself: on the ground it adds throttle until the barometer shows a
# climb, in the hold it trims the height error away. On the ground after the landing it disarms.

PARAMS = {
    "target_alt": 2.0,
    "hold_s": 5.0,
    "hover": 0.5,
    "climb": 0.12,
    "descent": 0.35,
    "kp": 0.25,
    "kd": 0.18,
    "ki": 0.1,
    "kv": 0.3,
    "learn": True,
}
PARAM_RANGES = {
    "target_alt": [0.3, 3.0],
    "hold_s": [0.0, 120.0],
    "hover": [0.2, 0.85],
    "climb": [0.0, 0.4],
    "descent": [0.1, 1.0],
    "kp": [0.0, 1.0],
    "kd": [0.0, 1.0],
    "ki": [0.0, 0.5],
    "kv": [0.0, 1.0],
}

SPOOL_S = 1.0  # throttle ramp from zero to the climb throttle
LIFT_SEARCH = 0.05  # throttle per second added on the ground while nothing lifts (learn)
TRIM_LIMIT = 0.25
TOUCHDOWN_ALT = 0.08


def init(ctx):
    ctx.mem["trim"] = 0.0  # learned throttle on top of the hover parameter
    ctx.state = "ground"


def base(ctx):
    return clamp(ctx.params["hover"] + ctx.mem["trim"], 0.0, 1.0)


def learn(ctx, amount):
    if ctx.params["learn"]:
        ctx.mem["trim"] = clamp(ctx.mem["trim"] + amount, -TRIM_LIMIT, TRIM_LIMIT)


def step(obs, ctx):
    p = ctx.params
    if obs.alt is None or obs.vz is None:
        ctx.state = "no baro"
        return Sticks(throttle=0.0)
    if not obs.armed:
        if ctx.state not in ("ground", "landed"):
            ctx.state = "ground"
        return Sticks(throttle=0.0)
    if ctx.state in ("ground", "landed"):
        if ctx.state == "landed" and not obs.airborne:
            ctx.disarm()  # armed again on the ground after a landing: disarm (re-deploy to fly again)
            return Sticks(throttle=0.0)
        # deployed in the air (a new revision while flying) goes straight to the climb
        ctx.state = "climb" if obs.airborne else "spool"
        Timer.start(ctx, "phase")
    t = Timer.elapsed(ctx, "phase") or 0.0

    if ctx.state == "spool":
        if t >= SPOOL_S:
            ctx.state = "climb"
            Timer.start(ctx, "phase")
        return Sticks(throttle=(base(ctx) + p["climb"]) * t / SPOOL_S)

    if ctx.state == "climb":
        error = p["target_alt"] - obs.alt
        if obs.alt < 0.05 and obs.vz < 0.05 and t > 1.5:
            learn(ctx, LIFT_SEARCH * obs.dt)  # still on the ground: more throttle
        if abs(error) < 0.1:
            ctx.log("hold at", round(obs.alt, 2), "m, throttle", round(base(ctx), 3))
            ctx.state = "hold"
            Timer.start(ctx, "phase")
        push = clamp(p["kp"] * error, -p["climb"], p["climb"])
        return Sticks(throttle=clamp(base(ctx) + push - p["kd"] * obs.vz, 0.0, 1.0))

    if ctx.state == "hold":
        error = clamp(p["target_alt"] - obs.alt, -0.5, 0.5)
        learn(ctx, p["ki"] * error * obs.dt)
        if t >= p["hold_s"]:
            ctx.state = "descend"
            Timer.start(ctx, "phase")
        return Sticks(throttle=clamp(base(ctx) + p["kp"] * error - p["kd"] * obs.vz, 0.0, 1.0))

    if ctx.state == "descend":
        # slower over the last 40 cm; touchdown when the height and the sink rate are both near zero
        sink = p["descent"] if obs.alt > 0.4 else min(p["descent"], 0.2)
        if obs.alt < TOUCHDOWN_ALT and abs(obs.vz) < 0.15 and not obs.airborne:
            ctx.log("landed")
            ctx.state = "landed"
            ctx.disarm()
            return Sticks(throttle=0.0)
        return Sticks(throttle=clamp(base(ctx) + p["kv"] * (-sink - obs.vz), 0.0, 1.0))

    return Sticks(throttle=0.0)
