# Writing flight programs

A flight program is a small Python program that Relay runs on the aircraft's control loop at 50 Hz. It reads
an observation and returns four stick values, exactly like a person with a radio. You write it, test it and
tune it at your own pace; the program does the fast work. This page is the compact reference that Copilot and
other agents receive; the full documentation is `docs/programs.md`.

## Shape of a program

```python
PARAMS = {"target_alt": 1.0, "kp": 0.25, "kd": 0.18, "ki": 0.12, "hover": 0.5}
PARAM_RANGES = {"target_alt": [0.3, 3.0], "hover": [0.2, 0.8]}   # optional bounds for live tuning

def init(ctx):                  # once, at deployment
    ctx.mem["trim"] = ctx.params["hover"]
    ctx.state = "climb"

def step(obs, ctx):             # every 20 ms; must return Sticks
    p = ctx.params
    if obs.alt is None or not obs.armed:
        return Sticks(throttle=0.0)
    error = clamp(p["target_alt"] - obs.alt, -0.5, 0.5)
    if obs.airborne:
        ctx.mem["trim"] = clamp(ctx.mem["trim"] + p["ki"] * error * obs.dt, 0.25, 0.8)
    ctx.state = "hold" if abs(error) < 0.1 else "climb"
    return Sticks(throttle=clamp(ctx.mem["trim"] + p["kp"] * error - p["kd"] * obs.vz, 0.0, 1.0))
```

- `Sticks(roll=0, pitch=0, yaw=0, throttle=0, fire=False)`: roll, pitch, yaw in -1..1, throttle in 0..1;
  out-of-range values are clipped. In the manual modes (STABILIZE; PX4 MANUAL / STABILIZED) roll and pitch
  command a lean angle (full stick = 30 degrees; roll right +, pitch forward + which tilts the nose down), yaw
  commands a turn rate (full stick = 120 deg/s, clockwise +) and throttle is thrust, not a height target.
- `PARAMS` values are numbers, booleans or strings of at most 32 characters. They can be changed live while
  the program runs (type and range follow the default and `PARAM_RANGES`).
- `ctx`: `mem` (a dict kept across frames), `params` (read-only, live), `state` (a name of at most 32
  characters shown on the stage), `log(*args)` and `print` (at most 4 lines per frame), `t` (seconds since
  deployment), `rng` (seeded random numbers: `random()`, `uniform`, `gauss`, `randint`, `choice`).
- Returning `None` or raising counts as a fault: that frame flies safe sticks (level, throttle held). 25 faults
  in a row (0.5 s) end the program as `faulted` and the aircraft goes back to the safety layer.

## Observation `obs` (read-only, attribute access; a missing sensor is `None`)

| Field | Unit | Meaning |
|---|---|---|
| `t`, `dt` | s | Program time and the interval of this frame (about 0.02) |
| `armed`, `airborne`, `mode` | | Armed, off the ground (landing detector), autopilot mode name |
| `roll`, `pitch`, `yaw` | deg | Attitude as the flight controller estimates it; roll right-wing-down +, pitch nose-up +, yaw is the heading 0-360 |
| `p`, `q`, `r` | deg/s | Body rates |
| `ax`, `ay`, `az` | m/s^2 | Body specific force (forward, right, up); about +9.81 on `az` at rest |
| `alt`, `vz` | m, m/s | Barometric height above the take-off point and climb rate |
| `pos`, `vel` | m, m/s | (x east, y north, z up), only with a position source (10 Hz, noisy) |
| `battery` | | `v_cell` (volts per cell), `pct` |
| `throttle_out` | 0-1 | Mean motor output the controller actually sends |
| `hp`, `ammo`, `reloading` | | Arena state; `None` outside the arena |
| `contacts` | list | Other aircraft within 30 m, nearest first: `id`, `team`, `dist`, `bearing` (deg right of the nose), `elevation`, `rel` and `vrel` as (forward, right, up), `hp`, `closing` (approach speed, m/s) |
| `collision` | | Last contact within 1 s, else `None`: `t_ago`, `with` (aircraft id, remote key or `ring:<n>`), `v_rel` (closing speed, m/s), `normal` (toward the aircraft, forward / right / up). Use it to switch into a recovery state |
| `arena` | | `radius`, `floor`, `ceiling` of the field, when there is one |
| `sticks_in` | Sticks | The person's sticks, in `copilot` deployment mode |

The airframe's defects (centre of gravity, level calibration error, motor differences, payload, a damaged
propeller, sensor noise) are never in `obs`. They show only in how the aircraft flies: you find them by
measuring.

## Library (available without import)

- `clamp(x, lo, hi)`, `wrap180(deg)`, `lerp(a, b, t)`, `deadband(x, d)`, `lowpass(prev, x, alpha)`.
- `PID(kp, ki, kd, i_limit=None, out_limit=None)`: `pid.step(error, dt)`, `pid.reset()`; `i_limit` bounds the
  integral term's output (anti-windup); gains are attributes you may retune from `ctx.params`.
- `StateMachine(initial)`: `sm.add(name, fn)` with `fn(obs, ctx) -> Sticks` or `(Sticks, next_state)`;
  `sm.step(obs, ctx)` runs the current state, applies the transition, keeps `ctx.state` and counts dwell time.
  Create it at module level, outside `init` and `step`.
- `Timer.start(ctx, name)`, `Timer.elapsed(ctx, name)`, `Timer.done(ctx, name, seconds)` (starts itself),
  `Timer.running`, `Timer.stop`.
- `import math` (or `from math import ...`) is the only import.

## Language limits

A subset of Python 3, checked before it runs. Allowed: functions (no decorators), assignment, `if`, `for`,
`while`, `break`, `continue`, `pass`, `return`, `try` / `except Exception`, comprehensions, lambda, f-strings,
`assert`. Not allowed: other imports, classes, `global` / `nonlocal`, `with`, `yield`, `async`, any name or
attribute starting with `_`, `exec`, `eval`, `compile`, `open`, `getattr`, `setattr`, `globals`, `locals`,
`vars`, `dir`, `type`, `input`, `breakpoint`. Builtins: `abs min max round sum len range enumerate zip sorted
reversed int float bool str list dict tuple set isinstance any all map filter divmod pow print`. Source at most
64 KB; one frame should take well under 2 ms. Keep values that change in `ctx.mem` (`global` is not
allowed).

## How to make an aircraft fly well

1. **Measure the hover throttle first.** It depends on weight, payload and battery, so do not assume 0.5.
   Ramp the throttle slowly on the ground until `alt` or `vz` shows a climb and keep that value as the
   starting trim (the built-in `liftoff` program does this). `throttle_out` in flight tells you the same.
2. **Hold height with an integral trim.** `throttle = trim + kp * height_error - kd * vz`, and in the air let
   `trim` integrate the height error slowly (`ki` around 0.1). The integrator absorbs battery sag, payload and
   ground effect. Clamp the error and the trim so a bad reading cannot run away (see `hover_trim`).
3. **Cancel drift with attitude trims.** A level calibration error or an offset centre of gravity makes a
   level-looking aircraft lean and drift. Without a position source, the accelerometer (`ax`, `ay`) feels
   the push before drag builds up: integrate it slowly into a small opposite roll and pitch trim. With a
   position source, close the loop on position (below). Yaw drift from motor differences: hold a heading with
   a P or PI term on `wrap180(target - obs.yaw)`.
4. **Structure PID loops from the outside in.** Position error -> desired velocity (P, clamped) -> velocity
   error -> lean angle (PI) -> roll and pitch sticks (lean / 30 degrees). Rotate world errors into the body
   frame first: `fwd = n * cos(h) + e * sin(h)`, `right = -n * sin(h) + e * cos(h)` with `h` the heading in
   radians, `n` the north (y) and `e` the east (x) component. Low-pass noisy derivatives, limit integrators and
   clamp every output. Start with small gains and raise them live with parameter changes.
5. **Use a state machine for behaviour.** Name the phases (`search`, `climb`, `hold`, `approach`, `shoot`,
   `evade`, `land`), give each a function and switch on clear conditions with a little hysteresis and a
   minimum dwell time (`Timer`). The state name shows on the stage, so it doubles as a status display.
6. **Test before you fly.** Unit tests feed one observation and check the output ranges; missing fields take
   the "hovering level at 1 m, armed, airborne" defaults. Then fly a simulator with hover assist off, then a
   `rough` airframe, then a challenge: the challenge's hidden seeds use other airframes, so a program tuned to
   one seed fails.

## Unit tests

`tests` is a list of `{name, obs: {...}, mem?: {...}, state?: str, params?: {...}, expect: {...}}`; `expect`
takes `roll`, `pitch`, `yaw`, `throttle` as `[low, high]`, `fire` as a boolean and `state` as a name. Example:
`{"name": "below target climbs", "obs": {"alt": 0.5, "vz": 0}, "expect": {"throttle": [0.55, 1.0], "state": "climb"}}`.

## Workflow with the tools

1. `program_list` / `program_get`: start from a built-in (`hover_trim`, `liftoff`, `patrol`, `hunter_easy`,
   `hunter_medium`, `hunter_hard`, `rammer`, `figure8_evader`; read-only, copy with `program_create {copy_of}`) or
   write a new one.
2. `program_create` / `program_update`: every change is a new revision with a `note`. `program_update` takes
   a whole `source` or a line `patch` `[{start, end, text}]` (1-based, inclusive; all numbers refer to the
   current revision; `end = start - 1` inserts before `start`). Both return the validation result.
3. `program_validate`: language check only, with line and column of every problem.
4. `program_test`: run the unit tests.
5. `challenge_list` / `challenge_submit` / `job_status`: judge the program on a fixed scenario faster than real
   time; `duel_run` / `ladder` pit programs against each other.
6. `program_deploy` on a simulator: the aircraft must be claimed, in a manual mode (STABILIZE) and armed
   (`relay_mode`, `relay_arm`); `airframe_set` or the deploy `airframe` chooses its defects; switch hover
   assist off for physical flight. Then `program_status` and `program_log` show the state, frame rate, faults
   and log; `program_params` tunes it live; `program_stop` ends it.

Rules that always hold: a person who takes over the aircraft pauses the program (it does not resume by itself;
use `program_resume`), an emergency stop, a lost link or a crash stops it, and releasing your claim or pushing
sticks yourself ends it. Real aircraft accept programs only after the operator allows them in the inspector and
approves the deployment; `fire` is ignored there.
