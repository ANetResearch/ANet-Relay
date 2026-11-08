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
[Onboard programs](docs/programs.md) ·
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
| Agent API | Local REST and WebSocket API with sessions, claims, sticks, skills (take off, land, hold, move, yaw, return, go to, attitude, velocity), sequences, re-fly from past flights and events; Python SDK; three MCP servers for flight, device-level and application control | Available |
| Sticks-only agents | Agent flight level `sticks` by default: sessions fly with sticks, arming, manual modes, fire and onboard programs only; high-level skills come back at level `full`, which only the operator can set | Available |
| Onboard programs | Restricted Python state machines that run at 50 Hz on this computer, read only what the aircraft senses and output sticks; written, tested and tuned live by people or agents in the Lab, the API, MCP and Copilot | Available |
| Challenges and duels | Ten judged challenges with hidden seeds, duels of 2 to 8 programs with an Elo ladder, a headless evaluator faster than real time and multi-aircraft replay | Available |
| Airframe defects | Simulators fly with payload, centre of gravity offset, level error, unequal motors, propeller damage and IMU noise (presets ideal, typical, rough) | Available |
| Device-level interface | Down to single motors and output channels: device overview, any MAVLink message, parameter reads and writes, raw command catalogue, single-motor tests, actuators, RC channel override, message rates, reboot, calibration, home, flight termination; hazardous operations need a confirm word | Available |
| Application control | Agents operate Relay itself: controllers and app status, language and workspace, selection, network scan, fleet and simulators, settings, screenshots; can be switched off in Settings | Available |
| Copilot | Built-in chat agent on [pi](https://github.com/earendil-works/pi) in a floating, always-on-top window that can be placed anywhere and never blocks other windows; flight, device-level and application operations in plain language, writes, tests, judges and deploys onboard programs, with any OpenAI- or Anthropic-compatible endpoint and key; every motion and device write on a real aircraft waits for the operator's approval; the key stays in the app data folder, never in the system keychain unless chosen | Available |
| Flight health | Rolling diagnostics while flying: attitude oscillation, hands-off lean, yaw drift, altitude hold, hover throttle, motor balance, vibration, battery sag, link and stick stream, each with its likely cause and fix | Available |
| Simulator | Up to 8 simulated aircraft that behave like ArduPilot or PX4 vehicles: a rigid body with four rotors after Gazebo's motor model, a simulated flight controller and a battery model; identical to real aircraft through the API | Available |
| Collisions | Aircraft are 0.15 m spheres and rings solid tori, swept at 250 Hz; 2 m/s or more crashes both, slower contacts bounce; also between terminals on the LAN | Available |
| Physical simulator | Per-simulator hover assist: off, a simulator flies like a racing quad without barometer or position source (throttle is thrust, ground effect, motor lag, turbulence); switchable in flight | Available |
| Bench procedures | Five step-by-step procedures from link check to sensor calibration, motor order, vibration, hover and step-response tests; measured verdicts, parameter suggestions and a saved bilingual report | Available |
| Controls and transmitters | Gentle, Standard and Sport feel, Mode 1 and Mode 2, per-axis inversion, RC transmitters in USB joystick mode with a mapping wizard, and a driving HUD | Available |
| LAN arena | Relay terminals on one network see each other's simulators and fire virtual rounds with shared ballistics, hits and scores; real aircraft never take part | Available |
| Dogfights and leaderboard | One call or one sentence to Copilot starts a timed fight of gunners and rammers flown by programs; matches across the LAN and a leaderboard for downs, survival, accuracy and rams | Available |
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
| macOS, Apple silicon | `ANet-Relay-<version>-mac-arm64.dmg` or `.zip` |
| macOS, Intel | `ANet-Relay-<version>-mac-x64.dmg` or `.zip` |
| Linux x64 | `ANet-Relay-<version>-linux-x64.AppImage` or `.tar.gz` |
| Windows x64 | `ANet-Relay-<version>-win-x64.exe` (installer) |

Verify downloads against `SHA256SUMS.txt`. The macOS build is signed with a Developer ID and notarized by Apple: drag it to Applications and open it. The Windows installer is not code-signed yet; when SmartScreen warns, choose More info → Run anyway.

The application bundles its backend; no Python or Node installation is needed to run it.

## Quick start

1. Start ANet Relay. The first launch of each version plays a short director's cut while the backend starts; any key, click or controller button skips it, and Settings → General → Opening sequence sets Full, Short or Minimal.
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
| Hover assist (simulators) or altitude hold (real aircraft) | `H` | Triangle |
| Throttle to the hover point | `Z` | — |
| Control feel sharper or gentler | — | D-pad up / down |
| Fire (simulators, LAN arena) | `X`, held | R1, held |
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
    relay.takeoff("sim-1", alt=1.2)                  # agent flight level "full" (Settings → Safety); see below
    relay.course_start("sim-1", "cage-loop")
    relay.land("sim-1")
```

**Copilot.** The desktop app has a built-in agent: open it with the **Copilot** button or `⌘J` / `Ctrl+J`, set the endpoint, model and API key in **Settings → Copilot**, and ask, for example, "take off to 1 m, hover, go forward 1 m and back, turn 45° and land" (at the agent flight level Full) or "write a program that holds 1 m on SIM-1 and pass the hover challenge" (at the default Sticks only level, where Copilot flies through programs). Copilot is a separate floating window: it stays above other applications, can be dragged anywhere on screen, and the main window and other applications stay usable while it is shown. It works through the same session, claim and API as any agent, with flight, device-level and application tool sets chosen in Settings, and on a real aircraft shows an approval card before every motion command and device write. The key is stored encrypted in the app data folder, readable only by your user account; the system keychain is an opt-in setting, and Relay never asks for your login password.

Language models connect through MCP:

```sh
claude mcp add anet-relay -- python3 /path/to/ANet-Relay/mcp/anet_relay_mcp.py --name claude-code                     # flight
claude mcp add anet-relay-device -- python3 /path/to/ANet-Relay/mcp/anet_relay_device_mcp.py --name claude-code-device  # device level
claude mcp add anet-relay-app -- python3 /path/to/ANet-Relay/mcp/anet_relay_app_mcp.py --name claude-code-app           # application control
```

| Interface | Address |
|---|---|
| REST | `http://127.0.0.1:8765/api/v1` (first free port from 8765 to 8775) |
| WebSocket | `ws://127.0.0.1:8765/api/v1/stream` |
| OpenAPI | `http://127.0.0.1:8765/openapi.json`, interactive at `/docs` |
| MCP | over stdio: `mcp/anet_relay_mcp.py` (flight, 20 tools; 13 at the agent flight level `sticks`), `mcp/anet_relay_device_mcp.py` (device level, 36), `mcp/anet_relay_app_mcp.py` (application control, onboard programs and dogfights, 65) |
| Python SDK | `sdk/python/anet_relay_client.py` and six examples |

The API listens on the loopback interface only. Agents control simulators and real aircraft through the same calls; real aircraft additionally require the operator's preflight steps and `confirm: "ARM"`.

## Onboard programs and duels

A language model decides in hundreds of milliseconds to tens of seconds; a quadcopter needs its sticks corrected fifty times a second. ANet Relay puts the fast loop on the aircraft's stick path: an **onboard program** is restricted Python with `PARAMS`, `init(ctx)` and `step(obs, ctx) -> Sticks` that runs at 50 Hz in an isolated program host on this computer. It reads only what the aircraft can sense (attitude, rates, accelerations, barometric height, a position only where a source exists, battery, motor output, arena state and contacts) and outputs the four sticks and the trigger. People and agents write the program; the program flies; the agent watches its state, log and scores at its own pace, changes the code or moves its parameters live.

- **Write and test.** Built-in examples `hover_trim`, `liftoff`, `patrol`, `hunter_easy` / `hunter_medium` / `hunter_hard` and `rammer` are read-only and can be duplicated. Every save is a revision with its author (the operator or the agent's session) and a note. Unit tests feed fixed observations and check the sticks, like the sample cases of a programming exercise.
- **Challenges.** Ten scenarios, `liftoff`, `hover`, `heading`, `square`, `rings`, `pursuit`, `duel_easy` / `duel_medium` / `duel_hard` and `ram_easy`, are judged in a headless evaluator faster than real time on visible and hidden seeds whose airframes differ, so a program tuned to one airframe does not pass. Scores run from 0 to 100 with the best result and the history of each challenge.
- **Duels.** Two to eight programs fly free-for-all rounds in the arena; the results move an Elo ladder. Every judged seed and round leaves a 10 Hz trace that replays every aircraft and its rounds on the Fly stage. Programs deployed on local simulators also fly live in the LAN arena against programs on other terminals; the network carries flight state only, never code.
- **Real airframes.** With hover assist off, simulators carry assembly defects: payload, centre of gravity offset, level calibration error, unequal motors, a damaged propeller and IMU noise, as the presets ideal, typical (the default) and rough or as custom values, deterministic by seed. The values never appear in a program's observation: they show in flight.
- **Safety.** A person who possesses the aircraft pauses the program at once, and releasing never resumes it. Emergency stop, link loss and a crash stop it; the 0.8 s stick timeout applies. A real aircraft accepts programs only after the operator turns on **Allow programs on this aircraft** in the inspector (confirm word `PROGRAM`, reset on every connection) and preflight passes, and its `fire` output is ignored. Programs come only from this computer.

```python
with RelayClient() as relay:
    relay.open_session("my-agent")
    pid = relay.program_create("My hover", copy_of="hover_trim")["program"]["id"]
    assert relay.program_validate(pid)["ok"]
    print(relay.program_test(pid)["passed"])                    # unit tests in the program host
    print(relay.challenge_submit("hover", pid)["result"])       # headless, hidden seeds
    relay.claim("sim-1")
    relay.mode("sim-1", "STABILIZE")
    relay.arm("sim-1")
    relay.program_deploy("sim-1", pid, airframe={"preset": "rough", "seed": 7})
    relay.program_params("sim-1", ki=0.2)                       # tune while it flies
```

Programs are written in the [Lab](#lab-and-onboard-programs) or through the API, the SDK, the application MCP server and Copilot. The [onboard programs guide](docs/programs.md) covers the language, the observation, the `relay` library, the challenges and duels; the [agent API](docs/agent-api.md#onboard-programs) lists the routes.

## Sticks-only agents

At the default agent flight level **Sticks only** (`sticks`) an agent flies the way a person does: sticks (the 50 Hz WebSocket stream, single frames and the `sticks` skill), arming and disarming, the manual modes STABILIZE, MANUAL and ACRO, fire, onboard programs and the safety actions (emergency stop, and `land` as a safety action, tagged `safety` in its event). Take-off, hold, move, turn, go-to, return, velocity and attitude skills, sequences that contain them, re-fly and the altitude, position and automatic modes return 403 `sticks_only` to every session, REST, WebSocket and MCP alike. The flight MCP server then lists only the 13 tools that still work, and Copilot drops the high-level tools and tells its model that stable flight needs a program. Agents face what a pilot faces: a hover that drifts, a throttle that is too low, a battery that sags. Only the operator can change the level, in **Settings → Safety → Agent flight level**; **Full** (`full`) restores every v0.3 tool. The operator's own controls in the window are never limited by it.

## Physics and collisions

Every simulator is a rigid body with a quaternion attitude and an inertia tensor, lifted by four rotors modelled after Gazebo's `MulticopterMotorModel`: rotor speed follows the motor command with separate spin-up and spin-down times, thrust and drag torque follow the rotor speed, and rotor drag and rolling moment act on sideways airflow. Body drag differs per axis, the battery sags under load, and ground effect, propeller wake and gusts act as before. A simulated flight controller flies it the way PX4 or ArduPilot would: an angle loop, a rate loop with a filtered derivative and a limited integral, and a mixer that keeps attitude priority when the motors saturate. The stick map is unchanged (30 degrees of lean and 120 deg/s of yaw at full stick), the hover throttle is 0.50 and the top speed at 30 degrees is about 7 m/s. Airframe defects are physical: an offset centre of gravity is a moment, unequal motors are unequal thrust, a damaged propeller loses and shakes thrust. The same seed and inputs give the same flight, which the evaluator relies on.

Aircraft collide. Each simulator is a 0.15 m sphere and each ring of an active course a solid torus with a 0.03 m tube. All local simulators are stepped together at 250 Hz and each step is swept against the other simulators, the rings and the aircraft of LAN peers, so a fast aircraft never passes through another. At a closing speed of 2 m/s or more both aircraft crash; below it they bounce with a restitution of 0.4 and the off-centre push upsets their attitude, which a program or a pilot can recover. A ring pass counts only when the whole aircraft clears the tube; a scrape still counts and is reported as `ring_touch`. A shot-down aircraft keeps its volume as it falls. Programs see the last contact in `obs.collision` and the closing speed of every contact; the `collision` event and the `crashed` alert name the cause and the place. On the LAN each terminal judges its own aircraft and tells the other terminal with a `COLLIDE` packet, which that terminal checks against its own view before it applies it.

The [simulation guide](docs/simulation.md) explains what the built-in simulator is for, how it compares with Gazebo, AirSim, Flightmare and others, and how to connect PX4 SITL with Gazebo or ArduPilot SITL to Relay as an ordinary MAVLink aircraft.

## Dogfights and leaderboard

Ask Copilot for "a six-aircraft free-for-all, four gunners and two rammers", choose **Start dogfight…** in the Arena menu of the top bar, or call `POST /scenarios/dogfight`. Relay takes or adds the simulators, places them around the arena edge facing the centre, deploys `hunter_medium` on the gunners and `rammer` on the rammers, arms them and lets the programs take off; they fly with sticks only. Shot-down and crashed aircraft respawn after 5 s, and at the end the programs stop and the aircraft land. You can possess any aircraft at any time. Off the LAN the dogfight runs in practice mode on this terminal; on the LAN it joins the running match.

A match starts with a dogfight or with `POST /arena/match` on any terminal and resets the statistics on every Relay 0.5 terminal in the arena. The **Leaderboard** panel on the Fly stage, `GET /arena/leaderboard` and the `leaderboard` tool rank every aircraft on the LAN by downs, survival, accuracy or rams, with its controller (program, agent or person), hits, shots, deaths, crashes, damage and its longest survival. Shoot-downs and rams throw voxel explosions, light contacts throw sparks, and the chase camera shakes when they are close.

```python
with RelayClient() as relay:
    relay.open_session("arena-agent")
    relay.dogfight_start(aircraft=6, roles={"gunner": 4, "rammer": 2}, duration_s=180)
    print(relay.dogfight_status()["aircraft"])
    print(relay.leaderboard("survival")["rows"][0])
    relay.dogfight_stop()
```

Agents on other terminals join by flying their own simulators, with sticks or a deployed program; the leaderboard shows each controller's name. Dogfights are application operations (the operator, or any session while **Allow agents to operate Relay** is on). Real aircraft never take part in dogfights, matches, collisions with remote aircraft or rounds. The [agent API](docs/agent-api.md#dogfights-matches-and-the-leaderboard) lists the routes, and the [programs guide](docs/programs.md) describes `rammer` and `obs.collision`.

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

### Bench procedures

Open the inspector's **Bench** tab, or **Run bench check** under the preflight list on the **Control** tab, and choose a procedure: `full_bench` for a new, modified or repaired aircraft, `preflight_quick` before each session, `motor_check` after a motor or ESC change, `vibration_check` after a propeller or motor change, and `hover_tune` while tuning. Steps run in order: link and firmware, battery at rest, parameter baseline, sensor calibration, stick directions, motor order and direction with the propellers removed (drawn from the reported frame), ground vibration, propellers fitted, a hover test at 0.6 m, roll, pitch and yaw step responses, and hover learning. Each step shows live measurements and its verdict against a stated limit; suggested parameter changes name the parameter and the direction and are applied only on the operator's answer. The existing confirm words (`CALIBRATE`, `MOTOR`, `ARM`) and limits apply on a real aircraft. The report is saved under **Records → Bench reports** and exports as JSON or a bilingual Markdown table. Agents run the same procedures through the API and the device MCP server; on a real aircraft only the operator answers the propeller and motor questions.

### Physical simulator and hover assist

Each simulator has a hover-assist switch (`H` or Triangle while flying it; the default for new simulators and the turbulence strength are in Settings → Safety). With hover assist on, a simulator holds height and position as before. With it off, it flies like a racing quad without barometer or position source: the attitude still self-levels, but the throttle is thrust, the hover point rises as the battery sags, ground effect, motor lag, turbulence and propeller wake act, and altitude and position hold modes are refused. The switch works in flight without a thrust jump. Bench flight steps need hover assist and say so when it is off.

### Controls and transmitters

Three feel presets, Gentle, Standard and Sport, scale tilt, yaw and keyboard reach without changing flight controller parameters; the throttle keeps a linear curve with an optional centre detent in hold modes. Mode 1 and Mode 2 and per-axis inversion are in Settings → Controllers. RC transmitters in USB joystick mode (EdgeTX, OpenTX, RadioMaster, Jumper, FrSky, TBS and others) are recognised with an AETR default and an absolute throttle; **Set up mapping** in the controller panel learns axes, directions, centres and end points for any non-standard device and binds switches to hover assist, fire, arm (simulators) and stop. A transmitter starts sending sticks only once its throttle is lowest, or centred in hold modes. While an aircraft is flown, the driving HUD above the stick panel shows the mode, hover assist, the feel and the throttle actually sent against the hover point.

### LAN arena

The **Arena** switch in the top bar joins every Relay terminal on the same network; there are no rooms or lobby, and switching it off leaves. Remote simulators appear on the stage in graphite with the terminal's callsign (Settings → Network), on twelve seats around a 10 m circle. Hold R1 or `X` to fire from a possessed, airborne simulator: 8 rounds per second, 24 per magazine. Rounds follow the same ballistic model on every terminal; the owner of the aircraft that is hit judges the hit, five hits bring a simulator down, and it respawns after 5 s. A scoreboard, a crosshair and the hit direction appear on the stage, and the driving HUD shows hit points and ammunition. Simulators on one terminal can also fire at each other for practice, and since 0.5 the aircraft of all terminals collide and share a leaderboard (see [Dogfights and leaderboard](#dogfights-and-leaderboard)). **Real aircraft never join the arena and are never affected by virtual rounds**: only simulators are shared, and the arena's UDP socket carries no control, so nothing received from the network reaches sticks, arming, claims, parameters, skills or plans.

### Lab and onboard programs

The **Lab** workspace (⌘4 / Ctrl+4) is where flight programs are written: restricted Python that runs at 50 Hz on this computer, reads only what the aircraft can sense and outputs sticks. Programs and examples on the left with the challenges; the editor in the middle (save makes a revision, problems are marked inline); Params, Tests, Run, Judge, Duel and History on the right. Deployed programs fly as `PROGRAM · <name> · <state>`, possession pauses them, and judged runs and duels replay with every aircraft on the Fly stage. The [onboard programs guide](docs/programs.md) covers the language, the observation, the `relay` library, the challenges, duels and the API.

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
| `backend/relay/` | MAVLink links and drivers, control authority, sessions and skills, simulator, ring courses, preflight, bench procedures (`procedures/`), LAN arena (`peers/`), history, REST and WebSocket API |
| `web/` | Interface: React, three.js stage, ANet Graphite theme, English and Chinese |
| `sdk/python/`, `mcp/` | Python SDK with examples; MCP server |
| `tools/fakes/` | Fake bridge and flight controller for testing without hardware |
| `e2e/` | Playwright tests and the screen walkthrough at three window sizes in two languages |
| `docs/` | Agent API guide, onboard programs guide, hardware guide and flight tuning guide (Chinese); `docs/design/` holds the product, experience and system design (Chinese) |

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
