<div align="center">

<img src="brand/icon.png" alt="ANet Relay icon" width="112" />

<h3>ANet Relay</h3>

ANet Relay is the desktop ground control station for small drone fleets on a local Wi-Fi network, built for teams in which people and agents fly together. It plays the role of QGroundControl for agent swarms: it discovers and links every MAVLink aircraft on the network, shows everything each one reports, and lets an operator possess any aircraft with a gamepad or the keyboard while external agents and language models fly the rest through a local API. When the operator lets go, control is relayed back to the agent that held it. A built-in simulator and a virtual ring course make the whole loop available without hardware, and the same rings can be flown with real aircraft that have a position estimate.

[![Release](https://img.shields.io/github/v/release/ANetResearch/ANet-Relay?label=release&color=1f1f1f)](https://github.com/ANetResearch/ANet-Relay/releases/latest)
[![CI](https://github.com/ANetResearch/ANet-Relay/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ANetResearch/ANet-Relay/actions/workflows/ci.yml)
[![Platforms](https://img.shields.io/badge/platforms-macOS%20%C2%B7%20Linux-1f1f1f)](https://github.com/ANetResearch/ANet-Relay/releases/latest)
[![Agent API](https://img.shields.io/badge/agent%20API-REST%20%C2%B7%20WS%20%C2%B7%20MCP-e0322d)](docs/agent-api.md)
[![License](https://img.shields.io/badge/license-modified%20Apache--2.0-1f1f1f)](LICENSE)

**[Download](https://github.com/ANetResearch/ANet-Relay/releases/latest)** · **[Agent API](docs/agent-api.md)** ·
[Hardware setup (zh)](docs/hardware.zh-CN.md) ·
[Design documents (zh)](docs/design/) ·
[Changelog](CHANGELOG.md) · [ANet4D](https://github.com/ANetResearch/ANet4D) · [ANet](https://github.com/ANetResearch/ANet)

**English** · [简体中文](README.zh-CN.md)

</div>

<br/>

<img src="docs/media/hero.png" alt="ANet Relay flying four simulated aircraft: the operator flies SIM-1 through the cage-loop ring course from the keyboard while agents harness-a and harness-b hold the other three" width="100%" />

<sub>**Screenshot.** Four simulated aircraft. The operator flies SIM-1 through ring 3 of the cage-loop course from the keyboard; agents `harness-a` and `harness-b` hold the other three. The relay timeline at the bottom shows who controlled each aircraft over the last minute.</sub>

## Capabilities

| Capability | Details | Status |
|---|---|---|
| Fleet link | MAVLink 2 over UDP: listens on 14550, scans the local subnets, connects by address; one link per aircraft with live, degraded and lost states | Available |
| Everything visible | Attitude, altitude, battery, GPS and local position, mode, landed state, status text, raw MAVLink inspector, full parameter table with search and editing, per-aircraft log | Available |
| Possession | Take over any aircraft with a DualSense or standard gamepad, the keyboard or on-screen sticks; several gamepads fly several aircraft at once | Available |
| Relay to agents | Possession always wins and suspends the agent's claim; on release the claim resumes and the agent receives `relay_handback` | Available |
| Flight actions and sequences | Take off, hover, move forward, back, sideways and vertically, turn and land from the inspector, or as an editable sequence flown step by step; works in altitude hold without a position source (moves are then open-loop estimates) | Available |
| Agent API | Local REST and WebSocket API with sessions, claims, sticks, skills (take off, land, hold, move, yaw, return, go to), sequences and events; Python SDK; MCP server for language models | Available |
| Copilot | Built-in chat agent on [pi](https://github.com/earendil-works/pi): status, take-off, moves, sequences and planning in plain language, with any OpenAI- or Anthropic-compatible endpoint and key; every motion on a real aircraft waits for the operator's approval | Available |
| Flight health | Rolling diagnostics while flying: attitude oscillation, hands-off lean, yaw drift, altitude hold, hover throttle, motor balance, vibration, battery sag, link and stick stream, each with its likely cause and fix | Available |
| Simulator | Up to 8 simulated aircraft that behave like ArduPilot vehicles, with dynamics and a battery model; identical to real aircraft through the API | Available |
| Ring course | Virtual ring sequences (cage loop, slalom, climb) with timing, splits and a chase camera, for simulators and for real aircraft with a local position estimate | Available |
| Bridge setup | Four-step wizard that moves a new ESP8266 MAVLink bridge from its hotspot onto the team network | Available |
| Preflight | Link, autopilot, battery, prearm, GCS input parameters, stick direction check, propellers, mode, failsafe and position; arming a real aircraft requires typing `ARM` | Available |
| Records | Every flight is recorded locally; charts, control track, event track, 3D replay, CSV and JSON export | Available |
| FPV video | Picture-in-picture from a UVC capture device per aircraft | Available |
| Languages | English by default, Simplified Chinese in one click, including native menus | Available |

## Download

Packages for every release are on the [Releases](https://github.com/ANetResearch/ANet-Relay/releases/latest) page.

| Platform | File |
|---|---|
| macOS, Apple silicon | `ANet-Relay-<version>-mac-arm64.zip` |
| macOS, Intel | `ANet-Relay-<version>-mac-x64.zip` |
| Linux x64 | `ANet-Relay-<version>-linux-x64.AppImage` or `.tar.gz` |

Verify downloads against `SHA256SUMS.txt`. The macOS build carries an ad-hoc signature, not a Developer ID: move the app to Applications and remove the quarantine attribute before the first launch.

```sh
xattr -dr com.apple.quarantine "/Applications/ANet Relay.app"
```

The application bundles its backend; no Python or Node installation is needed to run it.

## Quick start

1. Start ANet Relay. The ANet mark lights up while the backend starts.
2. Click **Add sim** at the bottom of the fleet bar to add a simulated aircraft, or connect a gamepad: it binds to the first free simulator and offers the ring course.
3. Press **P** (or hold **Options** on the gamepad) to possess the selected aircraft. Arm, take off and fly; release with **P** again.
4. To let an agent fly, run one of the examples against the running app:

```sh
git clone https://github.com/ANetResearch/ANet-Relay.git && cd ANet-Relay
python3 sdk/python/examples/hover_sim.py         # claim sim-1, take off, hover, land
python3 sdk/python/examples/watch_handback.py    # possess sim-1 in the app, then release it
```

The SDK and the MCP server use the Python standard library only. The [Agent API guide](docs/agent-api.md) covers the control model, every endpoint, the events and the move from simulators to real aircraft.

## Fly

**Real aircraft.** ANet Relay targets small quadcopters with a Pixracer-class flight controller (PX4 or ArduPilot) behind an ESP8266 `mavesp8266` Wi-Fi bridge, flown without an RC transmitter: all manual input is MAVLink stick emulation. Join the bridge to the team network with **Connect… → Set up a new bridge**; aircraft on the network appear in the fleet on their own. The [hardware guide](docs/hardware.zh-CN.md) (Chinese) covers wiring, bridge parameters and flight controller parameters.

**Controls.**

| Input | Keyboard | DualSense |
|---|---|---|
| Possess or release | `P` | Options, hold 1 s |
| Throttle and yaw | `R` `F` / `Q` `E` | Left stick |
| Pitch and roll | `W` `S` / `A` `D` | Right stick |
| Arm or disarm (simulators) | — | L1 + R1, hold 1 s |
| Stop this aircraft | `Esc` | L2 + R2, fully pressed |
| Stop all aircraft | `⌘.` / `Ctrl+.` | — |
| Flight mode | Inspector | Triangle |
| Ring course | `G` | Square |
| Command palette | `⌘K` / `Ctrl+K` | — |

**Flight actions and sequences.** Possess the aircraft and open the inspector's **Control** tab. **Flight actions** takes off to a set height, hovers, moves by a step in any direction, turns and lands. **Sequence** holds an editable list of steps, for example take off 1 m, hover 3 s, forward 1 m, back 1 m, turn 45°, land, and flies it in one go; any stick movement takes over and ends it. On a real aircraft both ask for `ARM` to be typed before arming. Without a local position source (optical flow, motion capture, UWB) the aircraft flies in altitude hold, holds height on the barometer and drifts slowly; moves are then timed tilts whose distance is an estimate. The same sequence runs from a script: `python3 sdk/python/examples/bench_routine.py --vehicle <id> --arm-real`.

**Unsteady after take-off?** Check the inspector's **Overview → Flight health** while hovering and follow the [flight tuning guide](docs/flight-tuning.zh-CN.md) (Chinese). The most common cause is flying in STABILIZE, where the throttle goes straight to the motors: Relay moves an aircraft it possesses on the ground from STABILIZE to altitude hold when it has a barometer.

**Stop semantics.** Stop sets throttle to zero for 50 ms and then requests a disarm; it never changes who controls the aircraft. Releasing possession stops stick input: on PX4 Relay stops sending manual control instead of sending zero throttle.

## For agents

Every aircraft has at most one controller at a time. An agent opens a **session**, **claims** aircraft and flies them with sticks or skills. A person who **possesses** an aircraft always wins; the claim is suspended and every control call returns `possessed_by_human` until the person releases, when the agent receives `relay_handback` and decides what to do next.

```python
from anet_relay_client import RelayClient          # sdk/python, standard library only

with RelayClient() as relay:                         # http://127.0.0.1:8765
    relay.open_session("my-agent")
    relay.claim("sim-1")
    relay.takeoff("sim-1", alt=1.2)
    relay.course_start("sim-1", "cage-loop")
    relay.land("sim-1")
```

**Copilot.** The desktop app has a built-in agent: open it with the **Copilot** button or `⌘J` / `Ctrl+J`, set the endpoint, model and API key in **Settings → Copilot**, and ask, for example, "take off to 1 m, hover, go forward 1 m and back, turn 45° and land". Copilot works through the same session, claim and skill API as any agent, never sends raw sticks, and on a real aircraft shows an approval card before every motion command. The key is stored encrypted with the system keychain.

Language models connect through MCP:

```sh
claude mcp add anet-relay -- python3 /path/to/ANet-Relay/mcp/anet_relay_mcp.py --name claude-code
```

| Interface | Address |
|---|---|
| REST | `http://127.0.0.1:8765/api/v1` (first free port from 8765 to 8775) |
| WebSocket | `ws://127.0.0.1:8765/api/v1/stream` |
| OpenAPI | `http://127.0.0.1:8765/openapi.json`, interactive at `/docs` |
| MCP | `mcp/anet_relay_mcp.py`, 18 tools over stdio |
| Python SDK | `sdk/python/anet_relay_client.py` and five examples |

The API listens on the loopback interface only. Agents control simulators and real aircraft through the same calls; real aircraft additionally require the operator's preflight steps and `confirm: "ARM"`.

## Feature tour

### Possession and relay

<img src="docs/media/relay.png" alt="SIM-2 handed back to harness-a: the hand-back toast, the agent pill in the inspector and the relay timeline" width="100%" />

The relay timeline along the bottom gives every aircraft a lane: white for the operator, grey for an agent, empty when nobody is flying. Taking over an agent's aircraft needs no confirmation, because an operator must be able to take over at once. Releasing it hands control back to the agent whose claim was suspended, and the controller label morphs from **You** to the agent's name. An aircraft in the air without a controller raises a standing alert.

<sub>**Screenshot.** The operator has just released SIM-2, which is handed back to `harness-a`.</sub>

### Gamepads

<img src="docs/media/gamepad.png" alt="DualSense connected card: a line-drawn controller, Controller 1 flying SIM-1, and a button to fly the ring course" width="100%" />

Connecting a controller draws a DualSense line by line on a hint card, lights its two sticks in turn and pulses the ring under the aircraft it was bound to. Gamepads bind on their own only to simulators; binding one to a real aircraft is always an explicit choice. Up to four gamepads fly four aircraft at once. If a gamepad disconnects, Relay stops its stick stream at once, puts a simulator in the air into loiter, and waits three seconds for the same gamepad before releasing the aircraft.

### Ring course

<img src="docs/media/course.png" alt="Chase camera behind SIM-1 as it approaches ring 3 of the cage loop, with the course timer" width="100%" />

Rings are drawn in three states (next, upcoming, passed) that differ in shape and brightness, never in red. Each pass flashes the ring and pops the split at its centre; the HUD shows ring count, time, last split and best time, and the finish card lists every split. The chase camera follows the aircraft like an FPV simulator. Agents can fly the same course through the API (`course_sim.py`). Real aircraft fly the virtual rings when they report a local position at 5 Hz or more; otherwise the course is unavailable and Relay says why.

<img src="docs/media/course-finish-zh.png" alt="Course finish card in Chinese with a new best time and four splits" width="100%" />

<sub>**Screenshot.** The finish card with a new best time, in the Chinese interface.</sub>

### Agents workspace

<img src="docs/media/agents.png" alt="Agents workspace: sessions, claims matrix, recent calls and the interface addresses with the MCP command" width="100%" />

The Agents workspace lists the live sessions with their leases, a claims matrix of aircraft against controllers, each session's recent calls with status and latency, and the addresses an agent needs, including the MCP command ready to copy.

### Records and replay

<img src="docs/media/records.png" alt="Records workspace: voltage, altitude, roll and pitch, throttle charts, event and control tracks, and a 3D replay" width="100%" />

Every flight is stored in a local SQLite database: telemetry, events, control changes and course results. A record opens as synchronised charts with event and control tracks; play it back in 3D at 1×, 4× or 16×, or export CSV and JSON.

### Bridge setup

<img src="docs/media/bridge.png" alt="Add aircraft dialog, step 1 of the bridge wizard: join the aircraft's hotspot" width="100%" />

A factory-fresh bridge starts as a hotspot. The wizard reads its settings, writes the team network's name, password and ports through the bridge's own setup interface, reboots it and waits for the aircraft to rejoin on the team network.

## Safety

ANet Relay flies real aircraft. Fly them only inside a netted area with a padded floor and with propeller guards, keep an operator at the Relay window, and verify stick directions with the propellers removed before the first flight of each aircraft. Possession, **Stop** and **Stop all** override every agent at any time. Preflight blocks arming until the link, battery, prearm checks and GCS input parameters (`MAV_GCS_SYSID` and `RC_OVERRIDE_TIME` on ArduPilot, `COM_RC_IN_MODE` on PX4) are in order. In STABILIZE there is no altitude or position hold: an aircraft without a controller keeps its last throttle until the autopilot's failsafe acts.

## Build from source

Requirements: Python 3.12 and Node 22.12 or later.

```sh
git clone https://github.com/ANetResearch/ANet-Relay.git && cd ANet-Relay
python3.12 -m venv .venv && .venv/bin/pip install -e "./backend[dev]"
npm ci && npm ci --prefix web
npm run dev                 # Vite, Electron and the backend with one operator token
npm test                    # backend pytest, web unit tests and build, end-to-end suite
npm run dist:mac            # or dist:linux; packages land in release/
```

The backend runs on its own with `.venv/bin/python -m relay --dev`. Pushing a `v*` tag builds macOS (arm64, x64) and Linux packages on GitHub Actions and publishes them with the matching [CHANGELOG](CHANGELOG.md) section.

## Repository layout

| Path | Contents |
|---|---|
| `electron/` | Desktop shell: window, `app://relay` protocol, native menus, backend supervisor, Copilot agent (`electron/copilot/`) |
| `backend/relay/` | MAVLink links and drivers, control authority, sessions and skills, simulator, ring courses, preflight, history, REST and WebSocket API |
| `web/` | Interface: React, three.js stage, ANet Graphite theme, English and Chinese |
| `sdk/python/`, `mcp/` | Python SDK with examples; MCP server |
| `tools/fakes/` | Fake bridge and flight controller for testing without hardware |
| `e2e/` | Playwright tests and the screen walkthrough at three window sizes in two languages |
| `docs/` | Agent API guide, hardware guide and flight tuning guide (Chinese); `docs/design/` holds the product, experience and system design (Chinese) |

<details>
<summary><b>Relation to ANet</b></summary>

<br/>

ANet Relay belongs to the ANet ecosystem from Agent Network Research, alongside [ANet](https://github.com/ANetResearch/ANet) and [ANet4D](https://github.com/ANetResearch/ANet4D). ANet4D simulates collaborative embodied agents at city scale; ANet Relay is where the same idea meets real aircraft on a bench: agents fly, people take over, and control is relayed between them under one set of rules.

</details>

## License and attribution

- **Code.** Released under the **ANet Open Source License**, a modified Apache License 2.0. All ANet logos and copyright notices must be retained. See [LICENSE](LICENSE) for the full terms.
- **Aircraft model.** The 3D aircraft is a model of the Caddx GoFilm20 airframe, used for visualisation only.

## Acknowledgements

ANet Relay builds on [Electron](https://www.electronjs.org), [pymavlink](https://github.com/ArduPilot/pymavlink), [FastAPI](https://fastapi.tiangolo.com), [three.js](https://threejs.org), [React Three Fiber](https://github.com/pmndrs/react-three-fiber), [ArduPilot](https://ardupilot.org), [PX4](https://px4.io) and [mavesp8266](https://github.com/dogmaphobic/mavesp8266). QGroundControl set the standard for what a ground station shows.

## Contributing

Issues and pull requests are welcome in English or Chinese. Changes to the control model, the agent API or the stop semantics must begin with an issue; `npm test` is the merge gate.

<div align="center">
<br/>
<img src="docs/media/anet-avatar.png" alt="Agent Network Research" width="48" />
<br/>
<sub>Agent Network Research</sub>
</div>
