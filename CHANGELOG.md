# Changelog

All notable changes to ANet Relay are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Each release section is published as the
GitHub Release notes for its tag.

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

[0.1.0]: https://github.com/ANetResearch/ANet-Relay/releases/tag/v0.1.0
