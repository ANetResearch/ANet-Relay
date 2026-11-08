# Patrol without a position source: hold height on the barometer, fly forward for a fixed time, brake,
# turn 90 degrees on the gyro heading, repeat. The square is approximate: drift is not corrected.

PARAMS = {
    "target_alt": 1.2,
    "leg_s": 2.0,
    "pitch": 0.25,
    "turn_deg": 90.0,
    "kp": 0.25,
    "kd": 0.18,
    "ki": 0.12,
}
PARAM_RANGES = {"target_alt": [0.5, 3.0], "leg_s": [0.5, 10.0], "pitch": [0.0, 0.6], "turn_deg": [-180, 180]}


def init(ctx):
    ctx.mem["trim"] = 0.5
    ctx.mem["heading"] = None
    ctx.state = "climb"


def altitude(obs, ctx):
    p = ctx.params
    error = clamp(p["target_alt"] - obs.alt, -0.5, 0.5)
    if obs.airborne:
        ctx.mem["trim"] = clamp(ctx.mem["trim"] + p["ki"] * error * obs.dt, 0.25, 0.8)
    return clamp(ctx.mem["trim"] + p["kp"] * error - p["kd"] * obs.vz, 0.0, 1.0)


def climb(obs, ctx):
    thr = altitude(obs, ctx)
    if abs(ctx.params["target_alt"] - obs.alt) < 0.15 and Timer.done(ctx, "settle", 1.5):
        Timer.start(ctx, "leg")
        return Sticks(throttle=thr), "leg"
    return Sticks(throttle=thr), None


def leg(obs, ctx):
    thr = altitude(obs, ctx)
    if Timer.done(ctx, "leg", ctx.params["leg_s"]):
        Timer.start(ctx, "brake")
        return Sticks(throttle=thr), "brake"
    return Sticks(pitch=ctx.params["pitch"], throttle=thr), None


def brake(obs, ctx):
    thr = altitude(obs, ctx)
    if Timer.done(ctx, "brake", 0.6):
        ctx.mem["heading"] = (obs.yaw + ctx.params["turn_deg"]) % 360
        return Sticks(throttle=thr), "turn"
    return Sticks(pitch=-ctx.params["pitch"], throttle=thr), None


def turn(obs, ctx):
    thr = altitude(obs, ctx)
    error = wrap180(ctx.mem["heading"] - obs.yaw)
    if abs(error) < 4:
        Timer.start(ctx, "leg")
        return Sticks(throttle=thr), "leg"
    return Sticks(yaw=clamp(error / 60.0, -0.6, 0.6), throttle=thr), None


sm = StateMachine("climb").add("climb", climb).add("leg", leg).add("brake", brake).add("turn", turn)


def step(obs, ctx):
    if obs.alt is None or obs.vz is None or obs.yaw is None:
        ctx.state = "no sensors"
        return Sticks(throttle=0.0)
    if not obs.armed:
        ctx.state = "disarmed"
        return Sticks(throttle=0.0)
    return sm.step(obs, ctx)
