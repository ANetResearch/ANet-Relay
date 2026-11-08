# Hover trim: climb to a target height and hold it on the barometer, with an integrator that learns the
# hover throttle (battery sag, payload and ground effect move it). Attitude stays level: without a
# position source the aircraft drifts with the air, as a real one would.

PARAMS = {"target_alt": 1.0, "kp": 0.25, "kd": 0.18, "ki": 0.12, "hover": 0.5, "climb_limit": 0.5}
PARAM_RANGES = {
    "target_alt": [0.3, 3.0],
    "kp": [0.0, 1.0],
    "kd": [0.0, 1.0],
    "ki": [0.0, 1.0],
    "hover": [0.2, 0.8],
    "climb_limit": [0.1, 1.0],
}


def init(ctx):
    ctx.mem["trim"] = ctx.params["hover"]
    ctx.state = "climb"


def step(obs, ctx):
    p = ctx.params
    if obs.alt is None or obs.vz is None:
        ctx.state = "no baro"
        return Sticks(throttle=0.0)
    if not obs.armed:
        ctx.state = "disarmed"
        ctx.mem["trim"] = p["hover"]
        return Sticks(throttle=0.0)
    error = clamp(p["target_alt"] - obs.alt, -p["climb_limit"], p["climb_limit"])
    if obs.airborne:
        ctx.mem["trim"] = clamp(ctx.mem["trim"] + p["ki"] * error * obs.dt, 0.25, 0.8)
    throttle = ctx.mem["trim"] + p["kp"] * error - p["kd"] * obs.vz
    ctx.state = "hold" if abs(p["target_alt"] - obs.alt) < 0.1 else "climb"
    return Sticks(throttle=clamp(throttle, 0.0, 1.0))
