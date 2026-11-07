# Changelog

All notable changes to ANet Relay are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Each release section is published as the
GitHub Release notes for its tag.

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

[0.2.0]: https://github.com/ANetResearch/ANet-Relay/releases/tag/v0.2.0
[0.1.0]: https://github.com/ANetResearch/ANet-Relay/releases/tag/v0.1.0
