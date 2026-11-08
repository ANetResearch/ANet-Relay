"""The scripted target of the ``pursuit`` challenge (06 §3.2): a judge-owned program, flown through the same
program engine and stick path as any other.

It climbs to ``alt`` on the barometer, then follows a circle of ``radius`` around the arena centre at
``speed`` (counter-clockwise from its spawn on the east side), using the UWB position source and holding a
north heading. It never fires.
"""

from __future__ import annotations

PROGRAM_ID = "judge_target"
NAME = "Pursuit target"
NAME_ZH = "追踪目标"

SOURCE = """# Pursuit target (judge): climb, then fly a circle around the arena centre on the position source.
import math

PARAMS = {"alt": 1.2, "radius": 3.0, "speed": 0.5, "start_s": 3.0, "kp": 0.25, "kd": 0.18, "ki": 0.12}


def init(ctx):
    ctx.mem["trim"] = 0.5
    ctx.mem["phase"] = None
    ctx.state = "climb"


def altitude(obs, ctx):
    p = ctx.params
    error = clamp(p["alt"] - obs.alt, -0.5, 0.5)
    if obs.airborne:
        ctx.mem["trim"] = clamp(ctx.mem["trim"] + p["ki"] * error * obs.dt, 0.25, 0.8)
    return clamp(ctx.mem["trim"] + p["kp"] * error - p["kd"] * obs.vz, 0.0, 1.0)


def step(obs, ctx):
    p = ctx.params
    if obs.alt is None or obs.vz is None or not obs.armed:
        ctx.state = "idle"
        return Sticks(throttle=0.0)
    thr = altitude(obs, ctx)
    yaw = clamp(-wrap180(obs.yaw or 0.0) * 0.02, -0.4, 0.4)
    if obs.pos is None or obs.vel is None or obs.t < p["start_s"]:
        return Sticks(yaw=yaw, throttle=thr)
    if ctx.mem["phase"] is None:
        ctx.mem["phase"] = math.atan2(obs.pos[1], obs.pos[0])
        ctx.mem["t0"] = obs.t
    ctx.state = "circle"
    w = p["speed"] / p["radius"]
    a = ctx.mem["phase"] + w * (obs.t - ctx.mem["t0"])
    want = (p["radius"] * math.cos(a), p["radius"] * math.sin(a))
    ve = -p["speed"] * math.sin(a) + 0.8 * (want[0] - obs.pos[0])
    vn = p["speed"] * math.cos(a) + 0.8 * (want[1] - obs.pos[1])
    ae = clamp(1.5 * (ve - obs.vel[0]), -2.0, 2.0)
    an = clamp(1.5 * (vn - obs.vel[1]), -2.0, 2.0)
    h = math.radians(obs.yaw or 0.0)
    fwd = an * math.cos(h) + ae * math.sin(h)
    right = ae * math.cos(h) - an * math.sin(h)
    pitch = clamp(fwd / 9.81 / 0.5236, -0.3, 0.3)
    roll = clamp(right / 9.81 / 0.5236, -0.3, 0.3)
    return Sticks(roll=roll, pitch=pitch, yaw=yaw, throttle=thr)
"""

__all__ = ["NAME", "NAME_ZH", "PROGRAM_ID", "SOURCE"]
