# Lift-off by throttle search: on the ground, raise the throttle slowly until the barometer shows a climb,
# take that throttle as the hover point, then climb to the target and hold it (as in hover_trim).

PARAMS = {"target_alt": 1.0, "ramp": 0.25, "start": 0.3, "kp": 0.25, "kd": 0.18, "ki": 0.1}
PARAM_RANGES = {"target_alt": [0.3, 3.0], "ramp": [0.05, 1.0], "start": [0.1, 0.6]}


def init(ctx):
    ctx.mem["throttle"] = ctx.params["start"]
    ctx.mem["hover"] = None
    ctx.state = "search"


def search(obs, ctx):
    ctx.mem["throttle"] = min(0.9, ctx.mem["throttle"] + ctx.params["ramp"] * obs.dt)
    if obs.alt > 0.08 or obs.vz > 0.15:
        # the climb starts a little above hover: keep a margin below the found value
        ctx.mem["hover"] = ctx.mem["throttle"] - 0.03
        ctx.log("hover throttle", round(ctx.mem["hover"], 3))
        return Sticks(throttle=ctx.mem["throttle"]), "climb"
    return Sticks(throttle=ctx.mem["throttle"]), None


def hold(obs, ctx):
    p = ctx.params
    error = clamp(p["target_alt"] - obs.alt, -0.5, 0.5)
    ctx.mem["hover"] = clamp(ctx.mem["hover"] + p["ki"] * error * obs.dt, 0.2, 0.85)
    throttle = ctx.mem["hover"] + p["kp"] * error - p["kd"] * obs.vz
    nxt = "hold" if abs(p["target_alt"] - obs.alt) < 0.1 else None
    return Sticks(throttle=clamp(throttle, 0.0, 1.0)), nxt


sm = StateMachine("search")
sm.add("search", search)
sm.add("climb", hold)
sm.add("hold", hold)


def step(obs, ctx):
    if obs.alt is None or obs.vz is None:
        ctx.state = "no baro"
        return Sticks(throttle=0.0)
    if not obs.armed:
        init(ctx)
        sm.state = "search"
        return Sticks(throttle=0.0)
    return sm.step(obs, ctx)
