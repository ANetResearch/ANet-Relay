# Arena hunter, easy level: search -> approach -> shoot -> evade, as a state machine over the contacts.
# Height is held on the barometer and matched to the target's; the gun points along the nose, so the
# hunter levels out and lines up the heading before it fires.
# Easy: slow to react, turns gently, never leads the target and does not evade.

import math

PARAMS = {
    "target_alt": 1.5,
    "kp": 0.25,
    "kd": 0.18,
    "ki": 0.12,
    "search_yaw": 0.2,
    "reaction_s": 1.2,
    "turn_gain": 0.01,
    "speed": 0.2,
    "standoff": 5.0,
    "fire_dist": 7.0,
    "aim_deg": 6.0,
    "lead": 0.0,
    "evade_s": 0.0,
}


def init(ctx):
    ctx.mem["trim"] = 0.5
    ctx.mem["hp"] = None
    ctx.mem["seen"] = None
    ctx.state = "search"


def target(obs, ctx):
    """The nearest contact that is still flying, or None."""
    best = None
    for c in obs.contacts or ():
        if c.hp == 0 or c.dist is None:
            continue
        if best is None or c.dist < best.dist:
            best = c
    return best


def altitude(obs, ctx, want):
    p = ctx.params
    if obs.arena is not None:
        want = clamp(want, obs.arena.floor + 0.5, obs.arena.ceiling - 0.5)
    error = clamp(want - obs.alt, -0.5, 0.5)
    if obs.airborne:
        ctx.mem["trim"] = clamp(ctx.mem["trim"] + p["ki"] * error * obs.dt, 0.25, 0.8)
    return clamp(ctx.mem["trim"] + p["kp"] * error - p["kd"] * obs.vz, 0.0, 1.0)


def aim_error(c, ctx):
    """Degrees to turn so the shot meets the target (with lead on the higher levels)."""
    lead = ctx.params["lead"]
    right = c.rel[1] + lead * c.vrel[1] * c.dist / 35.0
    return math.degrees(math.atan2(right, max(0.1, c.rel[0])))


def hit_taken(obs, ctx):
    hp = obs.hp
    before = ctx.mem["hp"]
    ctx.mem["hp"] = hp
    return hp is not None and before is not None and hp < before


def search(obs, ctx):
    thr = altitude(obs, ctx, ctx.params["target_alt"])
    c = target(obs, ctx)
    if c is not None:
        if ctx.mem["seen"] is None:
            ctx.mem["seen"] = ctx.t
        if ctx.t - ctx.mem["seen"] >= ctx.params["reaction_s"]:
            return Sticks(throttle=thr), "approach"
    else:
        ctx.mem["seen"] = None
    return Sticks(yaw=ctx.params["search_yaw"], throttle=thr), None


def approach(obs, ctx):
    c = target(obs, ctx)
    if c is None:
        return Sticks(throttle=altitude(obs, ctx, ctx.params["target_alt"])), "search"
    if hit_taken(obs, ctx) and ctx.params["evade_s"] > 0:
        Timer.start(ctx, "evade")
        return Sticks(throttle=altitude(obs, ctx, obs.alt)), "evade"
    thr = altitude(obs, ctx, obs.alt + c.rel[2] + 0.05)
    err = aim_error(c, ctx)
    yaw = clamp(err * ctx.params["turn_gain"], -1.0, 1.0)
    gap = c.dist - ctx.params["standoff"]
    pitch = clamp(gap * 0.15, -ctx.params["speed"], ctx.params["speed"])
    if abs(err) > 25:
        pitch = 0.0
    if abs(err) < ctx.params["aim_deg"] and c.dist < ctx.params["fire_dist"] and abs(gap) < 1.5:
        return Sticks(yaw=yaw, pitch=pitch, throttle=thr), "shoot"
    return Sticks(yaw=yaw, pitch=pitch, throttle=thr), None


def shoot(obs, ctx):
    c = target(obs, ctx)
    if c is None:
        return Sticks(throttle=altitude(obs, ctx, ctx.params["target_alt"])), "search"
    if hit_taken(obs, ctx) and ctx.params["evade_s"] > 0:
        Timer.start(ctx, "evade")
        return Sticks(throttle=altitude(obs, ctx, obs.alt)), "evade"
    thr = altitude(obs, ctx, obs.alt + c.rel[2] + 0.05)
    err = aim_error(c, ctx)
    yaw = clamp(err * ctx.params["turn_gain"], -1.0, 1.0)
    level = abs(obs.pitch or 0.0) < 6.0
    fire = abs(err) < ctx.params["aim_deg"] and level and not obs.reloading
    if abs(err) > 2 * ctx.params["aim_deg"] or c.dist > ctx.params["fire_dist"] + 1.0:
        return Sticks(yaw=yaw, throttle=thr), "approach"
    return Sticks(yaw=yaw, throttle=thr, fire=fire), None


def evade(obs, ctx):
    side = 1.0 if int(ctx.t * 2) % 2 == 0 else -1.0
    thr = altitude(obs, ctx, ctx.params["target_alt"] + 0.4 * side)
    if Timer.done(ctx, "evade", ctx.params["evade_s"]):
        return Sticks(throttle=thr), "approach"
    return Sticks(roll=0.5 * side, pitch=-0.2, throttle=thr), None


sm = StateMachine("search")
sm.add("search", search).add("approach", approach).add("shoot", shoot).add("evade", evade)


def step(obs, ctx):
    if obs.alt is None or obs.vz is None:
        ctx.state = "no baro"
        return Sticks(throttle=0.0)
    if not obs.armed:
        ctx.state = "disarmed"
        return Sticks(throttle=0.0)
    return sm.step(obs, ctx)
