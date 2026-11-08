# Arena rammer: climb -> search -> pursue -> ram -> recover, a state machine over the contacts. Sticks only.
# Pursuit is proportional navigation: the line-of-sight rate from the contact's relative position and
# velocity (heading frame) times the closing speed and the gain N gives the sideways acceleration, plus a
# push along the line of sight that grows to full when the target is close. Accelerations become tilt
# through the stick map (full stick = 35 degrees). Height follows the target on the barometer. After a
# contact (obs.collision) the rammer levels out and holds its height before it looks for the next target.
# On the way in it weaves across the line of sight (a square wave of sideways push) so that a gunner that
# does not lead its shots misses; inside ram_dist it stops weaving and pushes straight in. It never fires.

import math

PARAMS = {
    "cruise_alt": 1.5,
    "kp": 0.25,
    "kd": 0.18,
    "ki": 0.12,
    "nav_gain": 3.0,
    "push": 4.0,
    "ram_push": 7.0,
    "ram_dist": 2.5,
    "max_tilt": 0.8,
    "yaw_gain": 0.02,
    "search_yaw": 0.35,
    "recover_s": 1.5,
    "weave": 5.0,
    "weave_s": 0.5,
    "team": "",
}

TILT_FULL = math.tan(math.radians(35.0)) * 9.81  # m/s^2 of horizontal acceleration at full stick


def init(ctx):
    ctx.mem["trim"] = 0.5
    ctx.mem["hit_t"] = None
    ctx.state = "climb"


def enemy(obs, ctx):
    """The nearest contact on another team that is still flying, or None."""
    best = None
    for c in obs.contacts or ():
        if c.hp == 0 or c.dist is None or c.rel is None or c.vrel is None:
            continue
        if ctx.params["team"] and c.team == ctx.params["team"]:
            continue
        if best is None or c.dist < best.dist:
            best = c
    return best


def closing(c):
    """Closing speed (m/s): the sensor's value when it reports one, else from the relative motion."""
    value = c.get("closing")
    if value is not None:
        return value
    d = max(0.1, c.dist)
    return -(c.rel[0] * c.vrel[0] + c.rel[1] * c.vrel[1] + c.rel[2] * c.vrel[2]) / d


def altitude(obs, ctx, want, tilt):
    p = ctx.params
    if obs.arena is not None:
        want = clamp(want, obs.arena.floor + 0.5, obs.arena.ceiling - 0.5)
    error = clamp(want - obs.alt, -0.5, 0.5)
    if obs.airborne:
        ctx.mem["trim"] = clamp(ctx.mem["trim"] + p["ki"] * error * obs.dt, 0.25, 0.8)
    level = ctx.mem["trim"] / max(0.6, math.cos(tilt))  # more thrust while tilted
    return clamp(level + p["kp"] * error - p["kd"] * obs.vz, 0.0, 1.0)


def pn_accel(c, ctx, push, weave=0.0):
    """Desired horizontal acceleration (forward, right) in m/s^2: PN sideways plus a push along the sight,
    plus ``weave`` across it."""
    fwd, right = c.rel[0], c.rel[1]
    r2 = max(0.01, fwd * fwd + right * right)
    r = math.sqrt(r2)
    los_rate = (fwd * c.vrel[1] - right * c.vrel[0]) / r2  # rad/s, positive turning from nose to right
    vc = max(0.5, closing(c))
    side = ctx.params["nav_gain"] * vc * los_rate + weave
    ux, uy = fwd / r, right / r  # along the line of sight
    px, py = -uy, ux  # the way the line of sight turns
    return push * ux + side * px, push * uy + side * py


def weave_accel(ctx):
    """Sideways push of the weave: +weave and -weave in turn, each for weave_s."""
    p = ctx.params
    if p["weave"] <= 0 or p["weave_s"] <= 0:
        return 0.0
    return p["weave"] if int(ctx.t / p["weave_s"]) % 2 == 0 else -p["weave"]


def sticks_for(obs, ctx, c, push, weave=0.0):
    p = ctx.params
    ax, ay = pn_accel(c, ctx, push, weave)
    pitch = clamp(ax / TILT_FULL, -p["max_tilt"], p["max_tilt"])
    roll = clamp(ay / TILT_FULL, -p["max_tilt"], p["max_tilt"])
    tilt = math.radians(35.0) * math.sqrt(pitch * pitch + roll * roll)
    yaw = clamp(c.bearing * p["yaw_gain"], -1.0, 1.0)
    thr = altitude(obs, ctx, obs.alt + c.rel[2], tilt)
    return Sticks(roll=roll, pitch=pitch, yaw=yaw, throttle=thr)


def hit_recently(obs, ctx):
    col = obs.get("collision")
    if col is None:
        return False
    t_ago = col.get("t_ago")
    return t_ago is not None and t_ago < 0.5


def climb(obs, ctx):
    thr = altitude(obs, ctx, ctx.params["cruise_alt"], 0.0)
    if obs.airborne and obs.alt > ctx.params["cruise_alt"] - 0.3:
        return Sticks(throttle=thr), "search"
    return Sticks(throttle=thr), None


def search(obs, ctx):
    thr = altitude(obs, ctx, ctx.params["cruise_alt"], 0.0)
    if enemy(obs, ctx) is not None:
        return Sticks(throttle=thr), "pursue"
    return Sticks(yaw=ctx.params["search_yaw"], throttle=thr), None


def pursue(obs, ctx):
    if hit_recently(obs, ctx):
        Timer.start(ctx, "recover")
        return Sticks(throttle=altitude(obs, ctx, obs.alt, 0.0)), "recover"
    c = enemy(obs, ctx)
    if c is None:
        return Sticks(throttle=altitude(obs, ctx, ctx.params["cruise_alt"], 0.0)), "search"
    if c.dist < ctx.params["ram_dist"] and closing(c) > 0.0:
        return sticks_for(obs, ctx, c, ctx.params["ram_push"]), "ram"
    return sticks_for(obs, ctx, c, ctx.params["push"], weave_accel(ctx)), None


def ram(obs, ctx):
    if hit_recently(obs, ctx):
        Timer.start(ctx, "recover")
        return Sticks(throttle=altitude(obs, ctx, obs.alt, 0.0)), "recover"
    c = enemy(obs, ctx)
    if c is None:
        return Sticks(throttle=altitude(obs, ctx, ctx.params["cruise_alt"], 0.0)), "search"
    if c.dist > ctx.params["ram_dist"] * 1.5 or closing(c) < -0.5:
        return sticks_for(obs, ctx, c, ctx.params["push"]), "pursue"
    return sticks_for(obs, ctx, c, ctx.params["ram_push"]), None


def recover(obs, ctx):
    # level out against the push of the contact: sticks centred, height held, then look again
    thr = altitude(obs, ctx, max(obs.alt, ctx.params["cruise_alt"] - 0.3), 0.0)
    if Timer.done(ctx, "recover", ctx.params["recover_s"]):
        return Sticks(throttle=thr), "search"
    return Sticks(throttle=thr), None


sm = StateMachine("climb")
sm.add("climb", climb).add("search", search).add("pursue", pursue).add("ram", ram).add("recover", recover)


def step(obs, ctx):
    if obs.alt is None or obs.vz is None:
        ctx.state = "no baro"
        return Sticks(throttle=0.0)
    if not obs.armed:
        ctx.state = "disarmed"
        return Sticks(throttle=0.0)
    return sm.step(obs, ctx)
