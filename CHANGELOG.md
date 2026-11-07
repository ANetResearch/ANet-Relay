# Changelog

All notable changes to ANet Relay are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Each release section is published as the
GitHub Release notes for its tag.

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

[0.2.1]: https://github.com/ANetResearch/ANet-Relay/releases/tag/v0.2.1
[0.2.0]: https://github.com/ANetResearch/ANet-Relay/releases/tag/v0.2.0
[0.1.0]: https://github.com/ANetResearch/ANet-Relay/releases/tag/v0.1.0
