# Onboard programs, challenges and duels

[中文](programs.zh-CN.md) · Design contract (Chinese): [06 v0.4.0](design/06-v040.zh-CN.md) §2, §3, §7

An onboard program is a piece of restricted Python that runs isolated on this computer. Every 20 ms (50 Hz) it
reads an observation and returns a set of stick values. Agents no longer fly the aircraft frame by frame; they write
its brain: they write programs, run tests, deploy, tune parameters live, submit challenges, organise duels and read
the logs and replays.

| Layer | Rate | Implemented by |
|---|---|---|
| Reflexes: attitude stabilisation, motor mixing | 250 Hz and above | The flight controller (real) / the simulated autopilot (sim) |
| Behaviour: onboard programs (state machines) | 50 Hz | Programs written by people or agents: observation in, sticks out |
| Decisions: agents | Seconds to minutes | Copilot and external agents: write programs, tune, test, duel |

Programs and agents use **sticks only**: the output is roll, pitch, yaw and throttle plus the fire switch. High-level
commands such as "take off to 1 m" or "move 1 m forward" are closed to them (agent flight level `sticks` by
default, see the [Agent API](agent-api.md)).

## 1. Quick start: the Lab workspace

1. Press **⌘4 / Ctrl+4** (or **Lab** in the top bar).
2. Under **Programs** on the left, **+** creates a program; select an example and use **Duplicate** for an
   editable copy (examples are read only).
3. Edit the source. Problems are marked inline (dotted underline and a square next to the line number); the status
   line shows the first one, and clicking it moves the caret there. **⌘S / Ctrl+S** saves; every save makes a new
   revision.
4. **Tests** runs the unit tests in one click: one row per test with the verdict, the actual output and each
   expectation missed.
5. **Run** chooses an aircraft (start with a simulator) and a mode; **Deploy revision N** starts it. Status, state
   name, frame rate, faults and the tail of the log follow below.
6. **Params**: while the program runs, the sliders tune it live; otherwise they edit the values saved with the
   program.
7. **Judge** submits the saved revision to a challenge; **Duel** lets 2 to 8 programs play each other. Both offer
   **Open replay**, which replays every aircraft on the Fly stage.

On the stage, an aircraft flown by a program is labelled `PROGRAM · <name> · <state>`. The Fly inspector's
**Control** tab has a **Program** group: deploy, stop, resume and status.

## 2. Program structure

```python
PARAMS = {"target_alt": 1.0, "hover": 0.5, "kp": 0.3, "kd": 0.15}   # tunable while running
PARAM_RANGES = {"target_alt": [0.3, 3.0]}                            # optional ranges


def init(ctx):
    """Called once on deployment."""
    ctx.state = "climb"


def step(obs, ctx):
    """Called every 20 ms; must return Sticks."""
    p = ctx.params
    if not obs.armed or obs.alt is None:
        ctx.state = "idle"
        return Sticks(throttle=0.0)
    error = p["target_alt"] - obs.alt
    ctx.state = "hold" if abs(error) < 0.1 else "climb"
    return Sticks(throttle=clamp(p["hover"] + p["kp"] * error - p["kd"] * obs.vz, 0.0, 1.0))
```

- `PARAMS`: numbers, booleans or strings of at most 32 characters. Type and range follow the default: booleans stay
  booleans and integers stay integers; the range is `PARAM_RANGES[name]`, else `|value| <= max(10 x |default|, 1)`
  (10 for integers).
- `Sticks(roll=0, pitch=0, yaw=0, throttle=0, fire=False)`: roll / pitch / yaw in [−1, 1], throttle in [0, 1], the
  same axes as a gamepad. Values out of range are clipped; a non-finite value fails the frame.
- `ctx`: `mem` (a dictionary kept across frames), `params` (read only, follows live tuning), `state` (the state name,
  at most 32 characters, shown on the stage label and in the inspector), `log(*args)` (at most 4 lines per frame),
  `t` (seconds since deployment), `rng` (deterministic random numbers from the deployment seed). `print()` also
  writes to the log.
- Failures: when `step` returns `None` or raises, the frame outputs safe sticks (attitude centred, throttle held) and
  counts a fault; 25 faults in a row (0.5 s) put the program in `faulted` and hand control back to the safety layer.

## 3. Language

A subset of Python 3, checked against an AST allow-list before anything runs:

- Allowed: function definitions (no decorators), assignment, `if` / `for` / `while` / `break` / `continue` /
  `pass` / `return`, `try` / `except Exception`, expressions, comprehensions, lambdas, f-strings, `assert`;
  `import math` or `from math import ...` is the only import.
- Not allowed: every other import, class definitions, `global` / `nonlocal`, `with`, `yield` / `async`, any name or
  attribute starting with `_`, and `exec`, `eval`, `compile`, `open`, `getattr`, `setattr`, `globals`, `locals`,
  `vars`, `dir`, `type`, `__import__`, `input`, `breakpoint`.
- Built-ins: `abs min max round sum len range enumerate zip sorted reversed int float bool str list dict tuple set
  isinstance any all map filter divmod pow print`.
- Source of at most 64 KB.

Every deployment runs in its own program host process with no network or file access, so one program cannot
reach, slow down or crash another aircraft's program. Each frame has a 2 ms budget (soft, counted); a frame that
misses 15 ms outputs safe sticks, and 3 missed frames in a row end the program as `faulted`. A host that grows past
512 MB of resident memory is ended on every platform and the program faults with `memory`. Programs come only from
this computer (the Lab, Copilot, the local API); the LAN arena exchanges flight state, never code.

## 4. Observation `obs`

Only what the aircraft itself can know; attribute access; missing values are `None`.

| Field | Unit | Meaning |
|---|---|---|
| `t`, `dt` | s | Program time, frame interval |
| `armed`, `airborne`, `mode` | — | Armed, off the ground, flight mode name |
| `roll`, `pitch`, `yaw` | deg | Attitude; yaw is the heading 0–360 |
| `p`, `q`, `r` | deg/s | Body rates |
| `ax`, `ay`, `az` | m/s² | Body acceleration (simulators; real aircraft when they send IMU messages) |
| `alt`, `vz` | m, m/s | Barometric height above the take-off point and climb rate; `None` without a barometer |
| `pos`, `vel` | m, m/s (ENU) | Only with a position source; simulators by scenario, with noise, at 10 Hz |
| `battery` | `{v_cell, pct}` | Cell voltage and estimated charge |
| `throttle_out` | 0–1 | Mean motor output of the flight controller |
| `hp`, `ammo`, `reloading` | — | Arena hit points, rounds, reloading |
| `contacts` | list | Other aircraft the sensors see: `id, team, dist, bearing, elevation, rel, vrel, hp` (heading frame, within 30 m, at most 16) |
| `arena` | `{radius, floor, ceiling}` | Arena bounds (arena and challenges) |
| `sticks_in` | Sticks | The person's sticks (copilot mode) |

A simulator's airframe defects (centre of gravity, level calibration, motor efficiency, prop damage, noise; 06 §4)
are **not** in the observation. They show only in how the aircraft flies, as on a real one.

## 5. The `relay` library

Available in every program without an import:

| Name | Meaning |
|---|---|
| `clamp(x, lo, hi)`, `wrap180(deg)`, `lerp(a, b, t)`, `deadband(x, d)`, `lowpass(prev, x, alpha)` | Everyday maths |
| `PID(kp, ki, kd, i_limit, out_limit)` | `pid.step(error, dt)`, `pid.reset()`; `i_limit` bounds the integral term, `out_limit` the output |
| `StateMachine(initial)` | `sm.add(name, fn)` with `fn(obs, ctx) -> (Sticks, next_state | None)` (a bare Sticks stays); `sm.step(obs, ctx)` runs the current state, applies the transition, keeps `ctx.state` and counts transitions and dwell time |
| `Timer` | `Timer.start(ctx, name)`, `Timer.elapsed(ctx, name)`, `Timer.done(ctx, name, seconds)`, kept in `ctx.mem` |

Common practice: find the hover throttle first (an integrator learns the trim); height with PD plus an integral;
horizontal motion with small tilts and the heading; behaviour as a state machine (search → approach → fire →
evade).

## 6. Unit tests

```json
{"name": "Below target climbs", "obs": {"alt": 0.5, "vz": 0.0},
 "expect": {"throttle": [0.55, 1.0], "roll": [0, 0], "state": "climb"}}
```

Observation fields left out hover level at 1 m. Optional: `mem`, `state` (the starting state), `params`. In
`expect`, roll / pitch / yaw / throttle are `[low, high]`, `fire` is a boolean and `state` is text. The Lab's
**Tests** tab runs the tests against the editor's current source; saving first is not needed.

## 7. Deployment and safety

- Modes: `pilot` gives the program the sticks alone; `copilot` passes the person's sticks in as `obs.sticks_in`
  and replaces them with the program's output.
- Who may deploy: the operator or the aircraft's controller. The program then holds the sticks as the program
  controller, shown on the timeline as `PROGRAM · <name>`.
- People always win: possession pauses the program at once; releasing does not resume it, **Resume** does.
  Emergency stop, link loss and a crash stop the program. The 0.8 s stick timeout is unchanged.
- Real aircraft: off by default. The operator turns on **Allow programs on this aircraft** in the inspector under
  **Control → Program** (confirm word `PROGRAM`, reset on every connection), and the preflight check must pass.
  Fire output is ignored on real aircraft.
- Live tuning: `PATCH /vehicles/{id}/program/params {name: value}` is how an agent reaches the fast loop from its
  slow time scale.

## 8. Examples

| id | Meaning |
|---|---|
| `hover_trim` | Hover with integral trim: an integrator learns the hover throttle, attitude stays level |
| `liftoff` | Take-off by throttle search |
| `patrol` | Timed-turn patrol without a position source |
| `hunter_easy` / `hunter_medium` / `hunter_hard` | Arena hunters at three levels: search → approach → fire → evade, with a dodge when a contact closes fast (`dodge_s`, `threat_dist`, `threat_closing`) |
| `rammer` | Ram an opponent, never fire: climb → search → pursue (proportional navigation on the nearest contact) → ram → recover; reads `obs.collision` when the simulator reports one |

Examples are read only; duplicate one to change it.

## 9. Challenges

A challenge is a deterministic scenario with a scoring rule, run faster than real time in the headless evaluator.
Each has visible sample seeds and hidden seeds (with different airframe defects, so a program cannot be tuned to one
seed). Scores run from 0 to 100; the best score and the number of submissions are kept per challenge.

| id | Scenario | Pass condition |
|---|---|---|
| `liftoff` | Payload puts the hover throttle near 0.62; no position source | Off the ground within 5 s and 1.0 ± 0.2 m held for 3 s |
| `hover` | Forward centre of gravity and level calibration error; barometer, no position source | Within 20 s: drift at most 0.8 m, height 1.0 ± 0.25 m |
| `heading` | Unequal motor efficiency makes the yaw drift | Heading error at most 10° for 15 s |
| `square` | UWB-style position (10 Hz, σ 5 cm) | Pass the four corners in order (radius 0.4 m); faster scores higher |
| `rings` | The ring course | Every ring, timed |
| `pursuit` | A target aircraft on a scripted path | Share of 60 s within 2 m of the target |
| `duel_easy` / `duel_medium` / `duel_hard` | A built-in hunter as opponent | Win (shoot it down and survive); a draw counts remaining hit points |
| `ram_easy` | A hovering target on the spawn ring; guns off; contacts and UWB-style position | The target crashes from a collision with you within 60 s; faster and harder scores higher |

The Lab's **Judge** tab: choose a challenge → **Submit revision N** → progress → score, verdict, metrics and one row
per seed, each with a **Replay**.

## 10. Duels and Elo

The **Duel** tab takes 2 to 8 programs, a number of rounds and a seed; **Run duel** starts it. The result is the Elo
ladder (rank, Elo, change, wins–losses–draws) and every round (winner, hits, survival time), each with a replay. The
same (scenario, seed, program revision) always gives the same result.

Live duels: deploy programs to several local simulators and fly them in the arena in real time; programs deployed by
other terminals on the LAN fly in the same arena.

**Dogfight**: `POST /scenarios/dogfight` (Copilot and MCP `dogfight_start`, SDK `dogfight_start`) sets up a timed
fight of 2 to 8 simulators in one call: a spawn ring facing the centre, a program per role (gunners fire with
`hunter_medium` by default, rammers fly `rammer` and never fire), free-for-all or 2 / 3 teams (the `team` parameter
of each program), respawn after 5 s, then landing and the final standings on the leaderboard
(`GET /arena/leaderboard`: downs, survival, accuracy, rams). Programs fly with sticks only; a person can possess any
aircraft at any time. On the LAN it joins the shared match; otherwise the arena runs as local practice. Simulators
only; see `agent-api.md` (Dogfights, matches and the leaderboard).

**Replay**: opening a trace switches to the Fly workspace, where the stage replays every aircraft and every round,
with play / pause, 1x / 4x / 16x, a scrubber and Exit at the bottom. The replay closes by itself when an aircraft
starts being flown by hand, so the stage shows the live fleet again.

## 11. API

All paths are under `/api/v1`; errors keep the `{"error": {code, message_en, message_zh, detail}}` body. Reading is
anonymous; changes need the operator or an agent session (revision authors are `operator` or the session name).

| Method and path | Meaning |
|---|---|
| `GET /programs` | Programs, examples included |
| `POST /programs` | Create `{name, description?, source?, params?, tests?, note?, copy_of?}` |
| `GET /programs/{id}` · `PUT /programs/{id}` · `DELETE /programs/{id}` | Read / change (whole `source` or a line `patch: [{start, end, text}]`; a source or parameter change makes a revision) / delete |
| `GET /programs/{id}/revisions` | The last 50 revisions `[{rev, source, params, author, note, t}]` |
| `POST /programs/{id}/validate` | AST check only `{source?}` → `{ok, errors: [{line, col, end_line, end_col, code, message_en, message_zh}], params, param_ranges}` |
| `POST /programs/{id}/test` | Unit tests `{source?, tests?, rev?, seed?}` |
| `POST /vehicles/{id}/program` | Deploy `{program, rev?, params?, mode: pilot | copilot}` |
| `POST /vehicles/{id}/program/stop` · `.../resume` | Stop / resume |
| `PATCH /vehicles/{id}/program/params` | Live parameters `{name: value}` |
| `GET /vehicles/{id}/program/log?after=` | Log (ring of 500) `{lines: [{seq, t, level, text}], last_seq}` |
| `POST /vehicles/{id}/program/allow` | Allow programs on a real aircraft `{allow, confirm: "PROGRAM"}` (operator only) |
| `GET /challenges` | Challenges and best scores |
| `POST /challenges/{id}/submit` | Submit `{program, rev?}` → evaluation job |
| `GET /jobs/{id}` | `{state: queued | running | done | failed | cancelled, progress, result}` |
| `POST /duels` | Duel `{programs: [2..8], rounds, seed, scenario?}` → evaluation job |
| `GET /ladder` | The Elo ladder |
| `GET /traces/{id}` | A trace (format below) |

In the snapshot, `vehicle.program` is the run view `{id, name, rev, state, status: running | paused | faulted |
stopped, reason, mode, fps, faults, overruns, last_error, params, log_seq}` and `vehicle.programs_allowed` tells
whether a real aircraft accepts programs.

Copilot, the app MCP server and the SDK offer the same operations as tools: `program_list`, `program_get`,
`program_create`, `program_update`, `program_validate`, `program_test`, `program_deploy`, `program_stop`,
`program_resume`, `program_params`, `program_status`, `program_log`, `challenge_list`, `challenge_submit`,
`job_status`, `duel_run`, `ladder`, `airframe_set`. The app MCP action `set_workspace` accepts `lab`.

### Trace format

```json
{
  "id": "tr-…", "kind": "duel", "title": "duel_easy · seed 3", "hz": 10, "duration_s": 42.3,
  "arena": {"radius": 8, "floor": 0.3, "ceiling": 4},
  "aircraft": [{
    "id": "a", "name": "Hunter (easy)", "program": "hunter_easy", "rev": 1, "team": "a",
    "samples": {"t": [], "x": [], "y": [], "z": [], "roll": [], "pitch": [], "yaw": [],
                "throttle": [], "hp": [], "state": [], "armed": []}
  }],
  "shots": [{"id": "s1", "owner": "a", "t0": 3.2, "p": [0, 0, 1.5], "v": [35, 0, 0], "t_end": 3.6}],
  "events": [{"t": 3.6, "kind": "hit", "vehicle": "b", "by": "a"}]
}
```

Times are seconds from the start; positions are ENU metres in the arena frame (origin at the arena centre); angles
are degrees, yaw clockwise from north; throttle 0–1. `samples` may also be rows in the same order. Rounds fly the
arena integrator (1/240 s, drag and gravity) from `(p, v)` at `t0` until `t_end` (a hit) or the 2.5 s lifetime, so a
trace does not store every bullet sample.
