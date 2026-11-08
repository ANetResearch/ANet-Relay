# Changelog

All notable changes to ANet Relay are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Each release section is published as the
GitHub Release notes for its tag.

## [0.4.0] - 2026-11-08

Onboard programs that fly at 50 Hz on the stick path, judged challenges and duels
between programs, agents limited to sticks by default, simulators with real
airframe defects, a Lab workspace for writing programs, Copilot that writes and
judges them, an opening that starts with the film, and Copilot keys stored without
the system keychain.

### Added

- Onboard programs: restricted Python with `PARAMS`, `init(ctx)` and
  `step(obs, ctx) -> Sticks`, checked against an AST whitelist and run at 50 Hz
  in an isolated program host process per deployment (`--program-host`; no
  network or file access, a lower priority than the backend, memory and CPU
  limits on POSIX and a 512 MB resident-memory watchdog on every platform). The observation holds only what
  the aircraft measures: attitude, rates, accelerations, barometric height, a
  position only where a source exists, battery, motor output, arena state and
  contacts. The `relay` library provides `clamp`, `wrap180`, `lerp`,
  `deadband`, `lowpass`, `PID`, `StateMachine` and `Timer`. Programs are stored
  in `<data_dir>/programs/` with the last 50 revisions, their author and a note,
  parameters and unit tests. Six read-only examples: `hover_trim`, `liftoff`,
  `patrol` and `hunter_easy`, `hunter_medium`, `hunter_hard`.
- Deployment: `POST /vehicles/{id}/program` in `pilot` or `copilot` mode, stop,
  resume, live parameters (`PATCH .../program/params`), the log ring and
  `vehicle.program` in the snapshot. The program holds the sticks as the
  controller `PROGRAM · <name>` on the stage and the relay timeline. Possession
  pauses it at once and release never resumes it; emergency stop, link loss and
  a crash stop it; the 0.8 s stick timeout is unchanged. Real aircraft accept a
  program only after the operator turns on **Allow programs on this aircraft**
  (confirm word `PROGRAM`, reset on every connection) and preflight passes, and
  their `fire` output is ignored. Seven `program_*` events.
- Challenges and duels: nine challenges (`liftoff`, `hover`, `heading`,
  `square`, `rings`, `pursuit`, `duel_easy`, `duel_medium`, `duel_hard`) with
  visible and hidden seeds whose airframes differ, scored 0 to 100 on the
  simulator's true state, with best results and history. Duels of 2 to 8
  programs in free-for-all rounds with a pairwise Elo ladder. Both run in a
  headless evaluator in its own worker process (`--judge-worker`), at most two
  jobs at once, deterministic for the same scenario, seeds and revisions. Every
  judged seed and round keeps a 10 Hz trace of every aircraft and its rounds.
  `GET /challenges`, `POST /challenges/{id}/submit`, `POST /duels`,
  `GET /jobs/{id}`, `DELETE /jobs/{id}`, `GET /ladder`, `GET /traces/{id}` and
  the event `judge_finished`.
- Agent flight level: the setting `agent_flight_level`, `sticks` by default
  and changed only by the operator (Settings → Safety). At `sticks` sessions fly
  with sticks, arming, the manual modes, fire, programs and the safety actions;
  take-off, hold, move, turn, go-to, return, velocity and attitude skills, plans
  that contain them, re-fly and the hold and automatic modes return 403
  `sticks_only` over REST, WebSocket and MCP. `land` stays available as a safety
  action. The flight MCP server lists 13 of its 20 tools at this level. `full`
  restores every v0.3 tool; the operator's own controls are never limited.
- Airframe defects for simulators with hover assist off: payload, centre of
  gravity offset (an integrator corrects 70 % of the lean within 2 s), level
  calibration error, unequal motors, a damaged propeller and IMU noise. Presets
  `ideal`, `typical` (the default, setting `sim_airframe_default`) and `rough`,
  or custom values, deterministic by seed. `PUT` and `GET /sims/{id}/airframe`,
  `airframe` on `POST /sims` and on a program deployment, the event
  `sim_airframe_changed` and an **Airframe** group in the inspector. The
  snapshot and the observation carry only the preset and the seed. Simulators
  answer `SCALED_IMU` on request.
- Lab workspace (key `4`, `⌘4` / `Ctrl+4`): programs, examples and challenges on
  the left; a CodeMirror editor with Python highlighting and problems from the
  validator in the middle; Params, Tests, Run, Judge, Duel and History tabs on
  the right. A **Program** group in the Control tab of the inspector deploys,
  stops and resumes. Judged runs and duels replay with every aircraft and its
  rounds on the Fly stage with the Records replay controls.
- Copilot and MCP: 18 program, judge, duel and airframe tools in Copilot
  (`program_list`, `program_get`, `program_create`, `program_update`,
  `program_validate`, `program_test`, `program_deploy`, `program_stop`,
  `program_resume`, `program_params`, `program_status`, `program_log`,
  `challenge_list`, `challenge_submit`, `job_status`, `duel_run`, `ladder`,
  `airframe_set`) plus `relay_arm`, `relay_disarm` and `relay_mode`, in every
  tool scope (109 tools in all, 101 at `sticks`). Writing programs needs no
  approval; deploying or resuming one on a real aircraft waits for the
  operator. At `sticks` Copilot drops the high-level flight tools and its
  instructions say that stable flight needs a program. The same tools and
  `program_guide` in the application MCP server (now 61 tools), and the same
  calls in the Python SDK.
- Documentation: the onboard programs guide (`docs/programs.md`,
  `docs/programs.zh-CN.md`), the compact programs guide shared with Copilot and
  MCP (`docs/programs-guide.md`), and the program, judge, airframe and flight
  level sections of the agent API guide.
- Opening films: eight cooperation films (formation, hand-over, ground and air,
  a quadruped launching a drone, a VTOL leading a swarm, assembly into a
  formation, a gate race and a night search), and nine camera-move films
  (barrel roll, pull-back with spin, fisheye, spiral climb, vortex orbit, FPV
  flip, helix, dolly zoom and whip pan).

### Changed

- Opening: the film now comes first, then the diagonal voxel cut and the voxel
  logo, a short hold and the reveal (about 7.8 s for the director's cut, 4.4 s
  for the short cut). The director's cut plays a camera-move film and a
  cooperation film; the short cut rotates through all nineteen films by launch
  count. Skipping
  goes straight to the complete logo, held at least 300 ms. While the backend
  starts, the logo waits with the progress line under it.
- Copilot API keys: stored in `copilot-secrets.json` in the app data folder,
  encrypted with AES-256-GCM under a random per-installation key
  (`copilot-secrets.key`); both files and `copilot.json` are readable only by
  the user's account. The system keychain is an opt-in setting
  (Settings → Copilot → Store keys in the system keychain); Relay calls it
  only then, never at start-up, when Copilot opens or when its status is read.
  A key saved in the keychain by an earlier version is not decrypted on its
  own: Copilot offers **Enter key again** or **Keep using keychain**. Relay
  never reads or needs the login password.
- Simulators created without an airframe get the `typical` airframe with a
  random seed, so with hover assist off they no longer hover hands-off.
- Agents and scripts that flew with skills or plans need the agent flight level
  `full`; at the default `sticks` they receive 403 `sticks_only`.

### Removed

- The `cut-breakthrough` opening film.

### Downloads

| Platform | File |
|---|---|
| macOS (Apple silicon) | `ANet-Relay-0.4.0-mac-arm64.dmg`, `ANet-Relay-0.4.0-mac-arm64.zip` |
| macOS (Intel) | `ANet-Relay-0.4.0-mac-x64.dmg`, `ANet-Relay-0.4.0-mac-x64.zip` |
| Linux x64 | `ANet-Relay-0.4.0-linux-x64.AppImage`, `ANet-Relay-0.4.0-linux-x64.tar.gz` |
| Windows x64 | `ANet-Relay-0.4.0-win-x64.exe` |

Verify downloads against `SHA256SUMS.txt`. The macOS build is signed and
notarized: drag it to Applications and open it. The Windows installer is not
code-signed; when SmartScreen warns, choose More info → Run anyway.

### Known limitations

- The headless evaluator reaches more than 10 times real time with one
  aircraft, 6 to 8 times with two and about 4 times with four.
- The built-in programs pass `liftoff`, `hover` and `duel_easy`; `heading`,
  `square`, `rings` and `pursuit` have no built-in program that passes them yet.
- On Windows the program host has no CPU-time limit; the 15 ms frame deadline
  and the memory watchdog still end a runaway program.

## [0.3.1] - 2026-11-07

### Fixed

- macOS installer window: the icon labels sit on graphite nameplates, so they
  read clearly whether Finder draws them black (light appearance) or white
  (dark appearance). In 0.3.0 the black labels of the light appearance were
  hard to read on the dark background.

## [0.3.0] - 2026-11-07

A director's-cut opening, bench procedures that calibrate and test an aircraft
step by step, a physical flight model for simulators, control feel presets and
RC transmitters, and a LAN arena in which simulated aircraft see and shoot at
each other.

### Added

- Opening: on the first launch of each version a 5.6 s director's cut plays (a
  voxel logo, a diagonal voxel cut into a film, a voxel reveal); later launches
  play a 3.2 s short cut. Any key, click or controller button skips it, and
  Settings → General → Opening sequence chooses Full, Short (the default) or
  Minimal (the 1.2 s opening of 0.2). The 8 s and 20 s start-up warnings keep
  their timing, the emergency stop works during the opening, and with reduced
  motion it takes at most 400 ms and loads no video. Panel loading, workspace
  switches, replays and the 3D stage use the same voxel language.
- Bench procedures: `full_bench`, `preflight_quick`, `motor_check`,
  `vibration_check` and `hover_tune` chain twelve steps (link and firmware,
  battery at rest, parameter baseline, sensor calibration, stick directions,
  motor order and direction, ground vibration, propellers fitted, hover test,
  step response, hover learning, report). Each step measures, judges against a
  stated limit and suggests parameter changes by name and direction; nothing is
  written without the operator's consent, and the existing confirm words and
  limits apply. Reports are saved, listed under Records → Bench reports and
  exported as JSON or a bilingual Markdown table. New inspector tab **Bench**,
  `GET /procedures`, `/vehicles/{id}/procedure` (start, answer, abort),
  `/test-runs`, five `procedure_*` events, seven tools each in the device MCP
  server (now 36 tools) and in Copilot, and SDK calls.
- Simulator hover assist: every simulator has a hover-assist switch (setting
  `sim_hover_assist`, on by default). Off, it flies like a racing quad without
  barometer or position source: the throttle is thrust, the hover point rises as
  the battery sags, hold modes are refused, and ground effect, motor lag,
  turbulence (setting `sim_turbulence`) and propeller wake act. The switch works
  in flight without a thrust jump. `POST /sims/{id}/assist`, event
  `sim_assist_changed`, `vehicle.sim` in the snapshot, MCP `app_sim_assist`
  (the app MCP server now has 42 tools).
- Control feel: Gentle, Standard (the default) and Sport presets scale tilt, yaw
  and keyboard reach; the throttle has its own linear curve with an optional
  centre detent in hold modes; the right stick has a radial dead zone; Mode 1
  and per-axis inversion. Keys `Z` (throttle to the hover point) and `H` (hover
  assist); Triangle switches hover assist on a simulator and altitude hold on a
  real aircraft; the D-pad changes the feel.
- RC transmitters in USB joystick mode (EdgeTX, OpenTX, RadioMaster, Jumper,
  FrSky, TBS and others) and non-standard controllers: an AETR default with an
  absolute throttle, a mapping wizard that learns axes, directions, centres and
  end points, and switch bindings for hover assist, fire, arm (simulators) and
  stop. A transmitter starts sending sticks only once its throttle is lowest, or
  centred in hold modes.
- Driving HUD on the stage while an aircraft is flown: aircraft, controller,
  mode, hover assist, feel, the throttle actually sent against the hover point,
  and in the arena the aircraft's hit points and ammunition.
- LAN arena: the top-bar **Arena** switch joins every Relay terminal on the same
  network without rooms or a lobby. Remote simulators appear on the stage in
  graphite; rounds follow a shared ballistic model; the owner of the aircraft
  that is hit judges the hit; 20 damage per round, five bring a simulator down,
  and it respawns after 5 s. Scoreboard, crosshair and hit direction on the
  stage. `GET /arena`, `POST /vehicles/{id}/fire`, the stream messages `arena`
  and `fire`, events `peer_joined`, `peer_left`, `arena_hit` and `arena_down`,
  settings `peer_port`, `peer_callsign` and `arena_auto_respawn`, SDK `arena()`
  and `fire()`. Real aircraft never join the arena and are never affected by
  virtual rounds. The arena adds one UDP socket (port 47114 by default) that
  carries no control: nothing received from the network reaches sticks, arming,
  claims, parameters, skills, plans or device calls.
- Installer art: a DMG window background and layout on macOS, and wizard sidebar
  and header images in the Windows installer.

### Changed

- Keyboard pitch and roll reach follows the control feel (0.6, 0.8 or 1.0)
  instead of a fixed half stick; in hold modes `R` and `F` climb and descend
  only while held.
- The flight tuning and hardware guides (Chinese) start from the bench
  procedure.

### Fixed

- The `app://` protocol answers byte-range requests with 206 and 416, so video
  served from the application bundle can seek.

### Downloads

| Platform | File |
|---|---|
| macOS (Apple silicon) | `ANet-Relay-0.3.0-mac-arm64.dmg`, `ANet-Relay-0.3.0-mac-arm64.zip` |
| macOS (Intel) | `ANet-Relay-0.3.0-mac-x64.dmg`, `ANet-Relay-0.3.0-mac-x64.zip` |
| Linux x64 | `ANet-Relay-0.3.0-linux-x64.AppImage`, `ANet-Relay-0.3.0-linux-x64.tar.gz` |
| Windows x64 | `ANet-Relay-0.3.0-win-x64.exe` |

Verify downloads against `SHA256SUMS.txt`. The macOS build is signed and
notarized: drag it to Applications and open it. The Windows installer is not
code-signed; when SmartScreen warns, choose More info → Run anyway.

### Known limitations

- The arena has been tested with several terminals on one machine over
  loopback; networks that block both multicast and broadcast keep terminals
  apart.
- The flight steps of a bench procedure (S9, S10) need altitude hold: on a
  simulator with hover assist off they fail at once with that reason.
- Transmitter detection and the mapping wizard have been tested with synthetic
  controller readings only, not yet with transmitter hardware.

## [0.2.1] - 2026-11-07

A device-level interface down to single motors, application control for agents,
re-fly from past flights, three MCP servers, Copilot in a floating window, and the
first signed and notarized macOS build.

### Added

- Device-level API: device overview (motors and outputs, RC, frame, sensors,
  estimator, vibration, home, message rates), the newest frame of any MAVLink
  message, parameters read by name, a catalogue of raw MAVLink commands, single-
  and all-motor tests, actuator outputs, RC channel override, message intervals,
  reboot, sensor calibration, home and flight termination. Hazardous operations
  need a confirm word (`MOTOR`, `FORCE`, `REBOOT`, `CALIBRATE`, `TERMINATE`) on a
  real aircraft; termination needs it on every aircraft.
- Skills `sticks` (a timed stick hold), `attitude` (`SET_ATTITUDE_TARGET`) and
  `velocity`, also usable as plan steps.
- Application control: `GET /app`, `/app/ui`, `/app/gamepads`, `/app/actions` and
  `POST /app/ui` run interface actions in the open window (language, workspace,
  selection, view, inspector, dialogs, preferences, controller bindings,
  possession, records and replay, the Copilot window, screenshots). Agent sessions
  may also scan the network, connect and remove aircraft, manage simulators,
  change settings, acknowledge alerts and stop all while the `agents_app_control`
  setting is on (the default); only the operator grants or withdraws it.
- Re-fly: every record now stores its sticks and their source. `GET
  /history/sessions/{id}/refly` lists what a flight offers and `POST
  /vehicles/{id}/refly` flies it again as its plan, its track or its recorded
  sticks.
- Three MCP servers: `anet-relay` (flight, 20 tools), `anet-relay-device` (29
  tools) and `anet-relay-app` (41 tools), with argument checks before a call
  reaches Relay. The Python SDK covers every new endpoint; new example
  `device_tour.py`.
- Copilot runs in its own floating window: always on top, not modal, placed
  anywhere and remembered, with opacity and a tool scope (`flight`,
  `flight+device`, `all`) in Settings → Copilot. Device writes and mode changes
  on a real aircraft need the operator's approval like motion commands, and Stop
  in the Copilot window also ends the plan or skill Copilot started.
- macOS builds are signed with a Developer ID (Hardened Runtime), notarized and
  stapled, and ship as dmg and zip.

### Fixed

- The preflight item "Autopilot accepts Relay stick input" no longer stays on
  "waiting for parameters" when the parameter table never delivers a parameter:
  essential parameters are read by name, a parameter that cannot be read is
  reported as a warning, and parameter frames from a MAVLink bridge are ignored.
- A PX4 aircraft that has landed takes off again from a skill, plan or re-fly
  instead of being refused in LAND.
- The Copilot window picks up endpoint settings saved in the main window at once.
- The Windows build: the build scripts no longer pass Python code through cmd.exe,
  which split it on spaces; a Windows installer is published for the first time.

### Downloads

| Platform | File |
|---|---|
| macOS (Apple silicon) | `ANet-Relay-0.2.1-mac-arm64.dmg`, `ANet-Relay-0.2.1-mac-arm64.zip` |
| macOS (Intel) | `ANet-Relay-0.2.1-mac-x64.dmg`, `ANet-Relay-0.2.1-mac-x64.zip` |
| Linux x64 | `ANet-Relay-0.2.1-linux-x64.AppImage`, `ANet-Relay-0.2.1-linux-x64.tar.gz` |
| Windows x64 | `ANet-Relay-0.2.1-win-x64.exe` |

Verify downloads against `SHA256SUMS.txt`. The macOS build is signed and
notarized: drag it to Applications and open it. The Windows installer is new in
this release and not code-signed; when SmartScreen warns, choose More info → Run
anyway.

### Known limitations

- Motor tests use `MAV_CMD_ACTUATOR_TEST` on PX4 and `MAV_CMD_DO_MOTOR_TEST` on
  ArduPilot, and the motor number follows each autopilot's own order. This has been
  verified on simulators; on real hardware, check the order with the propellers
  off before relying on it.
- Actuator outputs 1 to the motor count are treated as motors: `MOTOR`, a
  disarmed aircraft, 0.2 to 5 s and at most 30 % on a real aircraft. ArduPilot may
  ignore `DO_SET_SERVO` on an output its mixer drives as a motor; use the motor
  test for those outputs.

## [0.2.0] - 2026-11-07

Flight actions and sequences that work without a position source, flight health
diagnostics, and Copilot, a built-in agent.

### Added

- Flight actions in the inspector's Control tab: take off to a set height, hover,
  move forward, back, sideways and vertically by a step, turn left or right, land.
- Sequences: an editable list of steps (for example take off 1 m, hover 3 s,
  forward 1 m, back 1 m, turn 45°, land) flown step by step, with progress, cancel
  and takeover by any stick movement. API `POST /vehicles/{id}/plan` and
  `/plan/cancel`, events `plan_*`, SDK `plan()` and the `bench_routine.py` example.
- Skills `move {forward, right, up}` and `yaw {deg}`, and `hold {seconds}`. Without
  a position estimate, take-off, hold, move and land run in altitude hold on the
  sticks; horizontal moves are open-loop, dead-reckoned estimates of at most 2 m.
- Flight health in Overview: attitude oscillation, hands-off lean, yaw drift,
  altitude hold, hover throttle, motor balance, vibration and clipping, battery
  sag, link and stick stream, each with its likely cause and fix. The vehicle
  snapshot carries the same diagnostics as `health`.
- Copilot: a chat agent built on pi in the desktop app (`⌘J` / `Ctrl+J`). It reads
  status, takes off, moves, turns, lands and plans sequences through the agent
  API; every motion command on a real aircraft waits for the operator's approval.
  Settings → Copilot configures the endpoint (OpenAI-compatible, Anthropic or
  OpenAI Responses), model and API key; the key is encrypted with the system
  keychain.
- MCP server 0.2.0 with `relay_move`, `relay_yaw`, `relay_plan` and
  `relay_hold(seconds)`: 18 tools.
- Flight tuning guide (`docs/flight-tuning.zh-CN.md`, Chinese).

### Changed

- Possessing an aircraft on the ground that sits in a manual-throttle mode
  (STABILIZE, STABILIZED, MANUAL) switches it to altitude hold when it has a
  barometer and the possession mode is `auto`. In flight the mode is not changed.
- PX4 aircraft are asked for `VIBRATION` at 2 Hz; the simulator reports it.

### Fixed

- `GET /events` no longer fails for every read that includes a plan event.

### Downloads

| Platform | File |
|---|---|
| macOS (Apple silicon) | `ANet-Relay-0.2.0-mac-arm64.zip` |
| macOS (Intel) | `ANet-Relay-0.2.0-mac-x64.zip` |
| Linux x64 | `ANet-Relay-0.2.0-linux-x64.AppImage`, `ANet-Relay-0.2.0-linux-x64.tar.gz` |

Verify downloads against `SHA256SUMS.txt`. The macOS build carries an ad-hoc
signature; before the first launch, move the app to Applications and run:

```sh
xattr -dr com.apple.quarantine "/Applications/ANet Relay.app"
```

### Known limitations

- Without a local position source, moves are timed tilts: the distance is an
  estimate (about ±30 % on simulators) and the aircraft drifts slowly while it
  holds. Optical flow with a rangefinder gives closed-loop moves.
- Flight health thresholds are coarse and point at a cause; confirm with the
  autopilot log.
- Copilot sends the conversation and tool results to the configured endpoint.

## [0.1.0] - 2026-11-07

First public release of ANet Relay, ground control for agent swarms.

### Added

- Fleet link over MAVLink 2 and UDP: listening on 14550, subnet scan, connect by
  address, per-aircraft link state; PX4 and ArduPilot drivers.
- Fly workspace: fleet bar, 3D stage with the Caddx GoFilm20 model and overview,
  chase and top cameras, inspector with Overview, Control, Telemetry (raw MAVLink),
  Parameters and Log tabs, alert bar and alert centre, command palette.
- Possession from the keyboard, on-screen sticks or up to four gamepads (DualSense
  and standard mapping), with a line-drawn hint card when a controller connects.
- Control authority: agent sessions and claims, human possession that always wins,
  hand-back to the suspended claim with the `relay_handback` event, and the relay
  timeline that shows who controlled each aircraft.
- Agent API on the loopback interface: REST, WebSocket stream, OpenAPI, skills
  (take off, land, hold, return, go to) within a safety volume, events; Python SDK
  with four examples; MCP server with 15 tools.
- Simulator with up to 8 ArduPilot-like aircraft, dynamics and battery model.
- Ring course: cage loop, slalom and climb presets with timing, splits, best times
  and a finish card, for simulators, agents and real aircraft with a local position
  estimate.
- Preflight checks with GCS input parameter checks, stick direction calibration and
  typed `ARM` confirmation for real aircraft; Stop and Stop all (`⌘.`).
- Four-step bridge wizard for ESP8266 `mavesp8266` bridges.
- Records: local SQLite history, charts, event and control tracks, 3D replay, CSV
  and JSON export.
- FPV picture-in-picture from UVC capture devices.
- English and Simplified Chinese interface and native menus; 1.2 s boot sequence.
- Packages for macOS (arm64, x64, ad-hoc signed) and Linux x64 (AppImage, tar.gz).

### Downloads

| Platform | File |
|---|---|
| macOS (Apple silicon) | `ANet-Relay-0.1.0-mac-arm64.zip` |
| macOS (Intel) | `ANet-Relay-0.1.0-mac-x64.zip` |
| Linux x64 | `ANet-Relay-0.1.0-linux-x64.AppImage`, `ANet-Relay-0.1.0-linux-x64.tar.gz` |

Verify downloads against `SHA256SUMS.txt`.

The macOS build carries an ad-hoc signature, not a Developer ID. Before the first
launch, move the app to Applications and remove the quarantine attribute:

```sh
xattr -dr com.apple.quarantine "/Applications/ANet Relay.app"
```

### Known limitations

- The macOS packages are not notarised; see the quarantine step above.
- The Windows installer is built on a best-effort basis and has not been tested.
- Real aircraft fly the ring course only with a local position estimate at 5 Hz or
  more; without one, the course is unavailable.
- On machines without a GPU the 3D stage drops to a reduced-quality mode capped at
  12 fps so that stick input keeps its rate.
- Stick timing has been verified on simulators and fake aircraft; check stick
  directions with the propellers removed before the first flight of each aircraft.

[0.4.0]: https://github.com/ANetResearch/ANet-Relay/releases/tag/v0.4.0
[0.3.1]: https://github.com/ANetResearch/ANet-Relay/releases/tag/v0.3.1
[0.3.0]: https://github.com/ANetResearch/ANet-Relay/releases/tag/v0.3.0
[0.2.1]: https://github.com/ANetResearch/ANet-Relay/releases/tag/v0.2.1
[0.2.0]: https://github.com/ANetResearch/ANet-Relay/releases/tag/v0.2.0
[0.1.0]: https://github.com/ANetResearch/ANet-Relay/releases/tag/v0.1.0
