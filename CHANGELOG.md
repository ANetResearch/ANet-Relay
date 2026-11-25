# Changelog

All notable changes to ANet Relay are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Each release section is published as the
GitHub Release notes for its tag.

## [0.6.2] - 2026-11-25

The Timeline now edits flight plans, not stick movements: a list of steps
(take off, move, go to, hover, turn, land, return) with their settings. Change
a setting, run, watch, hold, change again, then save the plan by name.

### Added

- **Flight plans in the Timeline** (the default; the stick timeline of 0.6.1
  is behind the Plan | Sticks switch in the document bar, `Alt+K`). Top to
  bottom: document bar, the stage with a top-down path preview and live values,
  the transport, a time strip whose step blocks always fill the width with the
  planned and the flown height under them, the step list, and the code.
- **Step list from the keyboard.** `←` `→` pick a step, `↑` `↓` change the
  value, `Tab` moves to the next setting, digits type a value, `Alt+←` `→`
  reorder, `T M H Y G L R` add a step, `Ctrl/⌘+D` duplicates, `Delete`
  removes. `?` lists every key.
- **Plan code.** One step per line, aligned in columns (`takeoff alt 10`,
  `move forward 10 speed 1`, `hover 5`, `land`), with `plan`, `speed`,
  `settle` and `frame` header lines and `#` comments. The list and the code
  are one document and follow each other; a text with problems names the line
  and the rule, and cannot fly.
- **Run from anywhere** with `Ctrl/⌘+Enter` (`Ctrl/⌘+Shift+Enter` from the
  selected step). `Space` cancels the plan and the aircraft holds. Each step
  shows how it ended and how long it took; the height of the run is drawn over
  the plan, with the run before as a dashed line. The last 12 runs can be
  reopened.
- **Safety volume check before take-off.** The monitor shows the volume the
  plan needs; when the setting is too small, Run offers to enlarge it.
- **Run without the safety volume**: a separate trigger (`Ctrl/⌘+Alt+Enter`)
  for the operator. That one plan is not checked against the volume; the
  setting stays, arming confirmation, pilot override and the flight monitors
  stay. `POST /vehicles/{id}/plan {"unlimited": true}`, refused for agent
  sessions with `operator_required`.
- **Saved plans.** `Ctrl/⌘+S` keeps the plan in the backend under its name.
  `GET /plans/saved`, `PUT` / `DELETE /plans/saved/{id}`, and
  `POST /vehicles/{id}/plan {"saved": id}`. MCP and Copilot `relay_plan` take
  `saved` and `relay_plans_builtin` lists them; the SDK has `plans_saved()`,
  `plan_save()`, `plan_delete()` and `plan(saved=...)`.
- Built-in plans in the Control tab have **Edit in Timeline**.

### Changed

- Plan steps accept a take-off height up to 30 m (was 10); the safety volume
  decides what is allowed.

### Fixed

- A plan flown under keyboard possession was aborted as an operator override
  when a step changed the flight mode: the keyboard's resting throttle follows
  the mode. A throttle-only change in the first two seconds of a step now
  counts as the new rest; roll, pitch and yaw still override at any time, and
  a gamepad is not affected.

## [0.6.1] - 2026-11-25

Stick timelines get their own workspace, built for tuning: change a number,
run, watch, stop, change again. The sequence can be written as code at any
time, and it runs from the code.

### Added

- **ANetDU7 outdoor square.** The onboard test node that was flown on the real
  aircraft now has an outdoor version, `vehicles/anet-du7/tests/u7_mavros_square10.py`
  (take off to 10 m, a 10 m square, land), and Relay flies the same way:
  built-in plan `du7_square10` (Control tab, `POST /vehicles/{id}/plan
  {"builtin": "du7_square10"}`, MCP, SDK, Copilot). Set the safety volume to
  at least 22 x 22 x 10.5 m first.

- **Timeline workspace** (fifth tab, key `5`, `Alt+5` from anywhere). Top to
  bottom: the document bar, the stage with a live monitor (sticks at the
  cursor, altitude, attitude, battery, the four trims), the transport, the
  block strip, then the tracks and the code. It replaces the dock under the
  Fly stage; the Control tab, the command palette and Records "Open as
  sequence" lead here.
- **A time axis that fits.** The tracks always fill the width: resizing the
  window, opening the code or folding the stage re-fits. `Ctrl`/`⌘` + wheel or
  `Alt+=` zooms in, `\` fits again, `Z` zooms to the selection. The lanes
  stretch with the height.
- **Keyboard editing.** `←` `→` move between blocks (`Shift` extends), `Tab`
  changes track, `↑` `↓` change the stick of every selected block, `Alt+←/→`
  the duration, `Ctrl/⌘+←/→` the start; typing a number sets the stick; `R`
  hold or ramp, `S` split, `I` insert, `Ctrl/⌘+D` duplicate, `Delete`,
  `[` `]` cursor to the next key time. `?` opens the key sheet. Blocks are
  also selected with `Ctrl/⌘`+click and `Shift`+click, and dragged together.
- **Ripple across tracks** (on by default, `G`): a longer or shorter block
  moves the later keys of the other tracks too.
- **Code view** (`Alt+T` tracks, `Alt+S` split, `Alt+C` code; `Ctrl/⌘+E`
  moves the focus). The sequence as an aligned time table: one row per moment
  at which a stick changes, one column per stick, `+0.5` for "half a second
  after the row above", `~` for a ramp, names after the value. Both views
  show one document: typing moves the blocks, dragging a block rewrites the
  number, the caret selects the block of its cell, the row being flown is
  marked. `Alt+↑/↓` nudges the number under the caret. A text with problems
  names the row and the rule, and never flies. The `.ars` file is unchanged.
- **Run from anywhere in the workspace**: `Ctrl/⌘+Enter` runs from the start
  and `Ctrl/⌘+Shift+Enter` from the cursor, in the tracks, the code and the
  fields alike; `Space` stops the sequence. Run possesses an idle simulator
  and puts a disarmed or crashed one back on its spawn point by itself;
  `0.5× / 1× / 2×` sets the speed. A real aircraft is asked for in the
  transport bar itself (possession, typed `ARM`), so nothing is pushed off
  screen.
- **Response lane**: the altitude of the run against sequence time, with the
  previous run as a dashed line and both peaks; "Restore the previous run's
  sequence" goes back in one click.
- **Library and templates**: `Ctrl/⌘+S` saves the sequence under its name
  (kept per viewer); the Open menu lists saved sequences, four templates
  (take-off and land, throttle step, pitch and roll doublet, blank) and
  import / export. Dropping an `.ars` file on the workspace imports it.
- `Alt+1` … `Alt+5` switch workspace from anywhere, text fields and editors
  included.
- **Choosing what a found aircraft is.** Every fleet card carries a model tag
  (`DU7` or `Micro`). A real aircraft nobody has chosen a model for shows a
  bright `Model?` tag; clicking the tag chooses ANetDU7 (large) or Micro 120
  (small) on the spot. `vehicle.model_confirmed` is false until then. The
  connection dialog has a "New aircraft are" switch (the `real_model_default`
  setting) for what newly found aircraft are taken for.

### Changed

- **Speed-limited flight of `takeoff` / `move` / `goto` follows the new test
  node.** Level speed is capped at `speed` and at a braking curve, so fast
  legs stop in time; vertical speed is capped at 0.5 m/s whatever `speed` is
  (a take-off with `speed` above 0.5 no longer climbs faster). A target counts
  `settle_s` after the aircraft first comes within 0.2 m instead of after an
  uninterrupted `settle_s` inside it, and a leg that never gets there ends at
  its approach limit with `approach limit reached … m from the target` in
  `skill.detail`. Plan steps accept `forward` / `right` / `left` and move
  `x` / `y` up to 30 m (was 5).

- `Esc` stops the aircraft on the first press while it flies a stick timeline,
  whatever is selected and also from the code editor. Before, a selected block
  took the first press.
- The step plan of the Control tab is called "Action plan" (动作计划); the
  stick editor is the "Timeline" (时间轴); the strip at the bottom stays the
  "Relay timeline" (控制权时间线). The strip is folded to its header row in
  Records, Lab and Timeline, and remembers its state per workspace.
- Undo and redo keep the selected block selected.

### Fixed

- A stick timeline started right after a keyboard possession, or from a
  different flight mode, could stop with "operator override": the keyboard's
  resting throttle moves with the flight mode (bottom in a stick mode, middle
  in a hold mode), and that jump was read as the pilot taking over. The rest
  position is now taken again while the timeline switches mode and arms.

## [0.6.0] - 2026-11-19

The ANetDU7 becomes the main aircraft: the default simulator, a gimbal pod that
people, agents and Copilot can drive, control frames that default to FLU, and
the speed-limited flight of its onboard test node. The small aircraft stay
available.

### Added

- **ANetDU7** (2.5 kg, 500 mm wheelbase, 684 × 684 × 211 mm with its guard
  frame, CUAV X7+ running PX4). `vehicle.model` is `anet-du7` or `micro`. The
  simulator has an `anet-du7` frame preset (10 inch propellers, 4S 5300 mAh,
  guard-frame drag) and flies the PX4 flavour unless an autopilot is named;
  `GET /capabilities` lists the model sizes the stage draws. Real aircraft are
  shown as `anet-du7` (setting `real_model_default`) until the operator sets
  them with `POST /vehicles/{id}/model` (event `model_changed`).
- **Control frames.** Skills and plan steps read their numbers in a control
  frame: the new setting `control_frame` (`flu` by default, or `frd`) or a
  request's `frame` (`flu`, `frd` or `relay`). FLU is forward-left-up in the
  body, east-north-up locally, yaw 0 along +x and counter-clockwise positive;
  FRD is forward-right-down, north-east-down, compass yaw, clockwise positive;
  `relay` is Relay's own axes, the meaning before 0.6. `move` also takes `x`,
  `y`, `z` and the named directions `left` and `down`; `velocity` takes
  `reference` (`body`, so the direction follows the nose, or `local`). The
  named directions are the same in every frame. `skill_started` params are
  echoed in `relay` axes, so a recorded skill flies the same route under any
  setting. `vehicle.nav` reports position, velocity, body velocity, yaw and
  yaw rate in the control frame; `GET /capabilities` adds `control_frame`,
  `control_frames`, `models` and `sim_frame_default`.
- **Speed-limited flight.** `takeoff`, `move` and `goto` take `speed` (0.1 to 5
  m/s) and `settle_s`; the ANetDU7 flies them that way by default at 0.5 m/s,
  as its onboard test node `u7_mavros_simple.py` does: a position error
  becomes a body velocity (gain 0.8, capped, 0.5 m/s² acceleration limit, 20
  Hz, no yaw correction), PX4 arms in POSCTL after two seconds of zero
  velocity and switches to OFFBOARD, and the target counts when the aircraft
  stays within 0.2 m for `settle_s`. Monitors for a tilt over 20°, a position
  jump, position telemetry older than 1 s and overspeed ask the autopilot to
  land and fail the skill; a mode changed from outside ends it without
  further setpoints.
- **Ready-made plans.** `GET /plans/builtin` and `POST /vehicles/{id}/plan
  {"builtin": "du7_box"}`: take off to 2 m, 1 m forward and back, 1 m left and
  back, land, at 0.5 m/s.
- **Gimbal pod.** `vehicle.gimbal` with `GET`/`POST`/`DELETE
  /vehicles/{id}/gimbal`, `POST /vehicles/{id}/gimbal/attach` and `GET
  /vehicles/{id}/gimbal/captures`. ANetDU7 simulators carry a simulated pod;
  a real DYT pod attaches over TCP (port 2000) or serial. One set of 25
  actions drives both: rate, angle, centre, follow and lock, zoom, lens
  (visible zoom, visible wide, infrared zoom, infrared fixed) with a
  picture-in-picture lens, palette, photo, record, track, laser, focus,
  enhance, OSD, low light, scan, geo-track and `look_at` (a local point in the
  control frame). Pod angles are the pod's own (yaw right, pitch up) and do not
  follow the control frame. Allowed at every agent flight level for the
  session that controls the aircraft. The DYT protocol (control frames,
  telemetry, carrier attitude, network wrapper) is implemented and tested
  against its documented examples and a replayed fake pod. Photos and
  recording changes are logged with the aircraft's pose.
  Events `gimbal_attached`, `gimbal_detached`, `gimbal_link`,
  `gimbal_capture`, `gimbal_record`, `gimbal_lens`; errors `gimbal_missing`,
  `gimbal_offline`, `gimbal_unsupported`, `gimbal_busy`.
- **The stage and the window** draw the ANetDU7 model at real size with
  turning rotors and a pod that follows `vehicle.gimbal`, render the pod's
  picture-in-picture, and offer the aircraft preset in the Add simulator
  dialog and the control frame in Settings.
- **Copilot, MCP and SDK.**
  - Flight MCP server and Copilot: `relay_gimbal_status`,
    `relay_gimbal_control` and `relay_gimbal_captures` (also at the sticks
    level), `relay_plans_builtin` and `builtin` on `relay_plan`; `frame`,
    `speed` and `settle_s` on `relay_takeoff`, `relay_move`, `relay_goto` (MCP)
    and plan steps, `frame` on `relay_yaw`, `left`, `down` and `x`, `y`, `z` on
    `relay_move`, `goto` steps in plans; `relay_fleet` shows `model`, `nav` and
    a gimbal summary. The flight server now has 38 tools, 29 at the sticks
    level. `device_velocity` takes `frame` and `reference`.
  - App MCP server: `app_sims_add` takes the aircraft preset
    (`frame.preset`), `app_settings_set` documents `control_frame`,
    `sim_frame_default` and `real_model_default`, and the operator actions
    `app_set_model`, `app_gimbal_attach` and `app_gimbal_detach` (they need
    `--operator-token`). The app server now has 70 tools.
  - The SDK adds `gimbal()`, `gimbal_do()`, `gimbal_angle()`,
    `gimbal_rate()`, `gimbal_zoom()`, `gimbal_lens()`, `gimbal_photo()`,
    `gimbal_record()`, `gimbal_look_at()`, `gimbal_captures()`,
    `gimbal_attach()`, `gimbal_detach()`, `plans_builtin()`, `set_model()`,
    `plan(builtin=)`, `frame`, `reference`, `speed` and `settle_s` on the
    flight methods, `left`, `down`, `x`, `y`, `z` on `move()`, and
    `add_sims(frame=)`. New example `du7_box_and_gimbal.py`.
- **`vehicles/anet-du7/`** holds everything about the aircraft: the onboard
  workspace, the interface documents, the DYT gimbal protocol notes, the
  real-aircraft test node and how to connect it to Relay. The two branches of
  the onboard repository `auto-lcx/yix_drone_ws` (`main`, the earlier
  workspace with its UI, maps and drivers, and `new_u7_src`, the ANetDU7
  onboard stack) were merged with their full history into
  `vehicles/anet-du7/onboard/main` and `vehicles/anet-du7/onboard/new_u7_src`;
  the code is independent of Relay's build and installers. The hardware guide
  has an ANetDU7 section with the first-flight checklist.

### Changed

- **A new simulator is an ANetDU7**, in PX4 flavour, with a simulated gimbal
  pod and speed-limited flight, instead of the 120 g ArduPilot-style aircraft.
  For the small aircraft pass `frame: {"preset": "micro-120"}` (or
  `small-250`, `large-450`) to `POST /sims`, or set `sim_frame_default`.
  Dogfights, judged challenges, duels and the built-in programs always use
  `micro-120` and behave as before; firmware twins keep their profile's frame.
- **Parameters without a named direction are read in FLU by default**:
  `yaw {deg}` and the heading of `goto` are counter-clockwise from east
  (before: clockwise, compass), `velocity` follows the nose (before: east,
  north, up) and its `vy` is left, and `yaw_rate_dps` of `velocity` is
  counter-clockwise. `forward`, `right` and `up` and the stick values are
  unchanged. Send `frame: "relay"` (SDK `frame="relay"`) to get the old
  meaning of any call exactly; the ring course numbers are in those axes, and
  `course_sim.py` passes it.
- **Real aircraft are `anet-du7` until set otherwise**, so take-off, `move` and
  `goto` with a position estimate fly on speed-limited body velocity at 0.5
  m/s with the new monitors. Set the aircraft or `real_model_default` to
  `micro` to keep the autopilot's own setpoints.
- The SDK's `add_sims` no longer sends `autopilot="ardupilot"` by default (the
  server chooses PX4 for an ANetDU7), and `takeoff`, `move` and `goto` derive
  their wait timeout from `speed`.
- Frame presets are `anet-du7`, `micro-120`, `small-250` and `large-450`.
- Descriptions of the flight tools in the MCP servers, Copilot and the SDK
  state the control frame; Copilot's instructions teach FLU and when to pass
  `frame`.

## [0.5.4] - 2026-11-09

Firmware twins fly the official PX4 and ArduPilot firmware against Relay's
physics with a real aircraft's version and parameters, PX4 flight controllers
on USB run hardware in the loop, and the simulator gains frames, acro mode and
crashes that tumble.

### Added

- **Firmware twins (SITL).** The desktop build of the official firmware, the
  same version as the real aircraft and loaded with its parameters, runs as a
  child process of Relay in lockstep with Relay's physics. To the rest of Relay
  it is a simulator: claims, sticks, skills, plans, timelines, programs, the
  stage and the arena work unchanged, with the firmware's own control loop,
  arming checks and failsafes. The firmware only talks to Relay on loopback.
  - The pack's firmware on screen: Settings → Autopilot packs, an Add
    simulator dialog with the fast simulator or a firmware twin, a firmware
    block in the inspector (identity, Generate twin profile, Start twin) and a
    twin inspector with the process, lockstep and parameter details.
  - Relay identifies a real aircraft's firmware (family, version, git hash,
    board) and ranks the installed packs: exact, version, series, nearest.
  - Twin profiles capture an aircraft's whole parameter table; calibration,
    device ids, serial ports and other hardware-specific parameters stay out,
    each with its reason.
  - Frame presets `micro-120`, `small-250` and `large-450` with adjustable
    mass, wheelbase, propeller and battery; a twin hovers at the same throttle
    as the real aircraft.
  - Without a real aircraft, a twin starts from any pack with its default
    parameters and a baseline tuned to hover out of the box.
- **Autopilot runtime packs** ship with the installer: ArduCopter 4.7.2,
  4.6.3, 4.5.7, 4.4.4 and 4.3.8, PX4 1.17.0, 1.16.2, 1.15.4 and 1.14.4 (PX4
  not on Windows). 1.7 to 2.6 MB compressed per pack (Windows ArduCopter packs
  about 4.4 MB with their Cygwin DLLs), 16 to 22 MB per installer.
  Settings lists them and imports further packs. Format:
  [docs/autopilot-packs.md](docs/autopilot-packs.md).
  - ArduPilot packs built from source bind SITL's RC input and the JSON
    physics socket to 127.0.0.1 (`"loopback": true` in the manifest), so no
    firmware socket listens on other interfaces. The linux-x64 ArduPilot packs
    are now static builds from source; the official binary stays available as
    a build option.
- **Hardware in the loop (PX4).** Over a USB serial link Relay backs up the
  flight controller's parameters, switches it to HITL, feeds it sensor data
  from its physics while the firmware keeps the motors locked, and afterwards
  restores and verifies every parameter. A step-by-step dialog in the
  inspector shows each stage. ArduPilot removed HIL support; Relay explains
  this and recommends a firmware twin.
  - Only `SYS_HITL` and `SYS_AUTOSTART` change; the whole parameter table is
    backed up first, and the restore reads every parameter back and compares
    it with the backup (flight-time counters and the flight UUID excepted).
  - Starting needs the operator to type HITL, a disarmed aircraft and the
    operator's possession.
  - If Relay quits during a session, the parameters are restored the next
    time the board connects over USB, once it is disarmed.
  - The flow is tested against a scripted PX4 board on a pseudo-terminal; the
    full flow on a real board is verified once the larger PX4 aircraft is
    available.
- **Serial link.** Connect an aircraft over a USB serial port (the flight
  controller's own USB socket or a USB-serial adapter) from the USB serial tab
  of the Connect dialog; known flight-controller interfaces and USB-serial
  adapters are marked and the baud rate is detected automatically. An
  unplugged board goes delayed, lost and offline like a UDP aircraft and
  reconnects when it comes back, also under a new device name. Needs pyserial
  3.5, which ships with the backend.
- **Simulator physics.**
  - Rigid-body ground contact at the feet, propeller tips and top: a normal
    landing stays upright, a crash bounces, rolls and comes to rest in its
    final attitude. Reset restores it upright.
  - **Acro mode** in the fast simulator: the sticks set body rates with no
    self-levelling or tilt limit, so it flips, rolls and flies inverted.
    Timelines take `mode: "acro"`.
  - Sensors with noise and bias for the twins and HITL: IMU, barometer,
    magnetometer and GPS.
  - The stage interpolates attitude with quaternions, so flips and inverted
    flight do not jump; twins and HITL aircraft carry an engine badge.
  - **Reset simulator** in the inspector, the fleet card menu, the command
    palette and a prompt on the stage over a crashed simulator; holding
    Circle on the gamepad also resets a crashed simulator.
  - Timelines arm with the throttle the mode needs: 0 on ArduPilot, 50 % on
    PX4 in the hold modes.
- API: `GET /autopilots`, `POST /autopilots/import`, `DELETE
  /autopilots/{id}`, `GET /vehicles/{id}/firmware`, `GET`/`POST
  /twins/profiles`, `GET`/`PUT`/`DELETE /twins/profiles/{pid}`, `POST /sims`
  with `engine`, `profile`, `pack`, `frame` and `home`, `GET /sims/{id}/twin`,
  `PUT /sims/{id}/frame`, `GET /serial/ports`, `POST /vehicles` with `serial`,
  `GET`/`POST /vehicles/{id}/hitl`. Snapshot fields `transport` (`udp`,
  `serial`, `sim`), `serial` and `hitl`. Events `twin_started`, `twin_exited`,
  `twin_start_failed`, `autopilot_pack_imported`, `hitl_started`,
  `hitl_restored`, `hitl_failed`. Errors `pack_missing`, `pack_unsupported`,
  `twin_start_failed`, `twin_limit`, `frame_unsupported`, `twin_unsupported`,
  `profile_not_found`, `params_incomplete`, `hitl_unavailable`,
  `hitl_restore_mismatch`, `serial_unavailable`.
- Copilot, MCP and SDK:
  - Copilot and the flight MCP server: `relay_autopilots_list`,
    `relay_firmware_identify`, `relay_twin_profiles_list`,
    `relay_twin_profile_get`, `relay_twin_profile_capture`,
    `relay_twin_start`, `relay_twin_status` and `relay_sim_frame`, available
    at both flight levels. The flight server now has 34 tools, 26 at the
    sticks level.
  - Copilot and the app MCP server: `relay_hitl` (status, start, stop; start
    needs `confirm: "HITL"`), `relay_serial_ports`, connecting over serial and
    adding twins with `engine: "sitl"`. The app server now has 67 tools.
  - Copilot asks the operator for every hardware-in-the-loop start and stop,
    on any aircraft and also while **Allow all** is on; that approval card has
    no Allow all button.
  - The SDK adds `autopilots()`, `firmware()`, `twin_profiles()`,
    `twin_profile()`, `capture_twin_profile()`, `update_twin_profile()`,
    `delete_twin_profile()`, `start_twin()`, `twin()`, `set_frame()`,
    `serial_ports()`, `serial_ports_status()`, `connect()` and `hitl()`.

### Changed

- Resetting a firmware twin restarts its firmware with the same profile; as
  before, a reset brings a crashed simulator back upright on the ground. The
  Copilot and MCP reset tools say so.

## [0.5.3] - 2026-11-09

Stick timelines with their own file format, simulators that drift like a real
airframe in altitude hold, and a fix for real aircraft that would not arm from
the Lab.

### Added

- **Timeline sequences** (`.ars`, ANet Relay Sequence): four stick tracks
  (throttle, pitch, roll, yaw) of blocks on one time line, opened from Control →
  Timeline sequence, the command palette or Records → Open as sequence. The dock
  under the stage shows the playhead while it flies.
  - Drag a block up or down to change its stick, drag its right edge to change
    how long it lasts. The arrow keys nudge the stick by 0.01 and the length by
    0.1 s. Blocks can be split, inserted and removed, edits can be undone, and
    blocks hold or ramp to the next one.
  - Both sticks at a glance: a strip above the time line draws the two sticks
    at every moment a stick changes, and two stick pads show them at the
    cursor. Drag a pad to set both sticks from that moment on (Mode 1 or 2,
    as in Settings → Controllers).
  - A trim on all four axes is added to every frame and changes in flight:
    cancel a forward drift with a little negative pitch.
  - Edits to blocks not yet flown reach the aircraft at once.
  - Record: the sticks an armed aircraft receives (gamepad, keyboard, plan,
    program) become blocks.
  - Takeoff template, import and export of `.ars` files. The same file flies
    simulators and real aircraft (ARM confirmation on real ones).
  - API: `POST`/`PATCH /vehicles/{id}/sequence`,
    `POST /vehicles/{id}/sequence/stop`, `POST /vehicles/{id}/sequence/record`,
    `GET /history/sessions/{sid}/sequence`; events `sequence_started`,
    `sequence_edited`, `sequence_recording`.
- Simulator airframe:
  - **Also with altitude hold** (`with_assist`): the CG offset, level error and
    the other defects act with hover assist on, so a timeline can be tuned on a
    simulator that tilts and drifts like the real aircraft.
  - **Random level error per arming** (`arm_bias_deg`): a new error of up to 3°
    at every arming, like a gyro bias that changes from flight to flight.
  - The inspector shows this flight's level error (`level_now_deg`).
- Copilot, flight MCP and SDK:
  - New tools `relay_timeline_run`, `relay_timeline_edit`,
    `relay_timeline_stop`, `relay_timeline_record` and
    `relay_timeline_from_record`. The flight MCP server now has 26 tools, 18 at
    the sticks level, where timelines fly in stabilize.
  - `airframe_set` takes `with_assist` and `arm_bias_deg`.
  - The SDK adds `timeline_*`, `load_ars` and `save_ars`.

### Fixed

- Real aircraft did not arm from the Lab. PX4 and ArduPilot arm STABILIZE or
  ALT_HOLD only while they see pilot input ("No manual control input",
  "Throttle below failsafe"), and a program released the sticks while the
  aircraft was disarmed. Now:
  - Programs hold centred zero-throttle sticks while disarmed.
  - An arming deployment sends them before it switches mode and arms.
  - `POST /vehicles/{id}/arm` sends them first when nothing else feeds the
    aircraft.
  - Skills that arm (take-off, plans, re-fly, timelines) send 0.3 s of
    centred zero-throttle sticks before the arm command.
  - Simulators refuse to arm without pilot input, as the autopilots do.
  - An aircraft that is already armed gets no zero-throttle sticks, and an
    emergency stop or a change of control during the short wait cancels the
    arm instead of arming after it.
- Timelines in the air never drop the throttle: one started in flight begins
  on its own first frame, a stabilize timeline cannot start in the air on a
  ground throttle, and a stop holds the throttle of that moment rather than the
  one the timeline ends on.
- Esc in the timeline dock first clears the block selection and otherwise
  still stops the aircraft. Undo and redo reach an aircraft that is flying the
  timeline.

## [0.5.2] - 2026-11-09

Programs can be tried out in the Lab with one click, sequences get adjustable
throttle, Copilot replies are formatted and copyable, and Copilot can be told to
stop asking for approval.

### Added

- Lab: **Arm and start** on the Run tab (on by default). The deployment
  switches to STABILIZE and arms once the program is in place, so it flies at
  once; a real aircraft asks for the word ARM first. Stopping the program on the
  ground disarms again. The Fly inspector's Program group follows the same
  preference. `POST /vehicles/{id}/program` takes `arm` and `confirm`; the answer
  reports `arm {ok, code?, reason?}`, and the operator no longer has to possess
  the aircraft for it.
- `ctx.disarm()`: a program disarms the aircraft after its landing; refused in
  the air.
- Built-in program `takeoff_hover_land`: take off to `target_alt`, hover for
  `hold_s` seconds, land at `descent` m/s and disarm. Every throttle value is a
  parameter to tune while it flies; with `learn` on it finds the hover throttle
  itself.
- Sequence throttle: the Control tab's sequence has a **Throttle** panel
  (Climb, Hold) for aircraft without a position estimate, with the live height,
  climb rate, throttle stick and motor output. Changes apply at once, also while
  the sequence flies. API: `tuning {climb, hover}` on `POST /vehicles/{id}/plan`,
  `PATCH /vehicles/{id}/plan/tuning`, event `plan_tuned`, `plan.tuning` in the
  snapshot.
- Copilot **Allow all**: from an approval card or Settings → Copilot. Commands
  on real aircraft then run without asking and are still recorded in the chat; a
  red badge in the Copilot title bar shows it is on and turns it off with a
  click. Preflight, the Allow programs switch, possession and emergency stop are
  unchanged.
- Copilot formatting and copying: replies render Markdown (headings, nested and
  task lists, tables with alignment, code blocks, quotes, links opened in the
  system browser); the transcript is selectable, every Relay window has a
  Cut / Copy / Paste / Select All context menu, replies, tables and code blocks
  have copy buttons.
- Copilot, the flight MCP server and the SDK: `tuning` on plans and
  `relay_plan_tune` / `plan_tune()`; `program_deploy` takes `arm`.

### Changed

- Deploying a new revision while a program flies takes over in the air without
  landing (now covered by tests and stated in the Lab).
- README animations are animated WebP (3.4 MB instead of 14 MB of GIFs).

## [0.5.1] - 2026-11-08

Copilot shows what it is doing, reasoning models such as DeepSeek V4 no longer
end a turn in silence, custom dogfights describe each aircraft on its own, and
the README opens with a flatter animated banner.

### Added

- Copilot run trace: every message gets a small red pixel aircraft that flies
  while Copilot works and names the phase (waiting for the model, thinking,
  writing, preparing a tool call, a tool running, waiting for approval) with
  the elapsed time; while it is out of view the same chip sits above the input.
  A click opens the trace: each model call with its time, input, output and
  thinking tokens and stop reason, expandable to the full thinking, the reply
  and the tool calls it asked for, and each tool with its arguments and full
  result. The main process now forwards thinking, tool-argument drafting, usage
  and stop reasons (`run_start`, `turn_start`, `thinking`, `draft`, `run_end`
  and extended `assistant_end`, `tool_start`, `tool_end`).
- Settings → Copilot: **Thinking** (`auto`, `off`, `low`, `high`) and **Max
  output tokens** (1024–131072, default 16384, thinking included).
- Custom dogfights: `POST /scenarios/dogfight` takes `entrants` (2–8), one entry
  per aircraft with `role` (`gunner`, `rammer`, or `pilot`, which never fires),
  `program`, `team`, `position` (`none`, `uwb`, `gps`), `params` and `label`.
  Copilot, the application MCP server and the SDK pass it through; Copilot maps
  requests such as "A flies a figure eight and evades with global position, one
  aircraft rams A, one shoots A" onto it in one call.
- `POST /vehicles/{id}/program` takes `team`: the aircraft's team in every
  contact list, also written into a program's `team` parameter.
- Built-in program `figure8_evader`: a figure eight around the arena centre on
  a position source with a weaving height, and a sideways dodge with a height
  change when a contact of another team closes in; it never fires.

### Fixed

- Copilot with reasoning endpoints: the model was declared non-reasoning with a
  4096-token output limit, so DeepSeek V4 could spend the whole turn thinking
  and stop with `length`, and the empty turn disappeared from the chat. The
  limit is now configurable with a higher default, and a turn that runs out of
  output or says nothing leaves a notice and its trace.
- The Copilot setup text said the key is kept in the system keychain; it is
  stored encrypted in the app data folder unless the keychain option is on.

### Changed

- README banner: 960 × 300 animation with the ANet logo, the app icon and the
  name over the formation shot; the loop fades back to its first frame.

## [0.5.0] - 2026-11-08

A rigid-body flight model with four rotors after Gazebo's motor model, collision
volumes for aircraft and rings, dogfights started in one sentence, matches and a
leaderboard shared across the LAN, and voxel explosions.

### Added

- Collisions: every simulator is a sphere of 0.15 m and every ring a solid torus
  with a 0.03 m tube. The fleet steps all simulators together at 250 Hz and
  sweeps each step against the other simulators, the rings of the active course
  and the aircraft of LAN peers, so fast aircraft never pass through each other.
  At a closing speed of 2 m/s or more both aircraft crash; below it they bounce
  with a restitution of 0.4 and the off-centre push upsets their attitude. A
  shot-down aircraft keeps its volume while it falls. Ring passes count only
  when the whole aircraft clears the tube; a pass that scrapes the tube still
  counts and reports `ring_touch`. Events `collision` and `ring_touch`; the
  `crashed` alert carries `reason_code` (`ground`, `collision`, `shot_down`),
  the point and what the aircraft hit.
- Program observation: `obs.collision` (the last contact within 1 s: `t_ago`,
  `with`, `v_rel`, `normal`) and `closing` on every contact. The evaluator makes
  course rings solid and writes `collision` and `crash` events into the trace.
- Dogfights: `POST`, `GET` and `DELETE /scenarios/dogfight` start a timed fight
  of 2 to 8 simulators (free-for-all or 2 or 3 teams) with gunners and rammers,
  spawn points around the arena edge, the airframe, hover assist, respawn and a
  seed. Relay adds the missing simulators, deploys each role's program, arms
  the aircraft and lets the programs take off; at the end it stops them, lands
  the aircraft and keeps the final standings. Off the LAN the arena runs in
  practice mode without a socket. Real aircraft are refused
  (`dogfight_real_aircraft`). Events `dogfight_started` and `dogfight_finished`.
- Built-in program `rammer`: proportional-navigation pursuit of the nearest
  enemy that weaves across the line of sight on the way in, a full push at close
  range and a recovery state after the contact; it never fires. The hunters
  side-step an aircraft that comes straight at them. New challenge `ram_easy`
  (bring down a hovering target by collision, guns off), ten challenges in all.
- Matches and leaderboard: a match starts with a dogfight or with
  `POST /arena/match` on any terminal and resets the statistics of every
  version 2 terminal; `DELETE /arena/match` ends it. `GET /arena/leaderboard`
  with `by` = `downs`, `survival`, `accuracy` or `rams` merges this terminal's
  lines with those its peers report: controller, downs, hits, shots, accuracy,
  rams, deaths, crashes, survival time, best survival time and damage. The
  arena view carries `match` and `board_rev`. Events `match_started` and
  `match_finished`.
- Arena wire protocol 2 with `COLLIDE`, `MATCH` and a statistics block in
  `STATE`. Each terminal judges contacts of its own aircraft only and sends
  `COLLIDE` to the owner of the other aircraft, which checks age, distance,
  rate and pair cooldown against its own view before it applies the same rule.
  Packets that Relay 0.4 knows are still sent as version 1, so 0.4 and 0.5
  terminals see each other and hit each other with rounds; collisions and the
  leaderboard work between 0.5 terminals.
- Copilot, the application MCP server and the SDK: `dogfight_start`,
  `dogfight_stop`, `dogfight_status` and `leaderboard` (Copilot now has 113
  tools in all, 105 at `sticks`; the application MCP server 65), and the SDK's
  `match_start` and `match_end`. "A six-aircraft free-for-all, four gunners and
  two rammers" maps to one `dogfight_start` call.
- Fly workspace: **Start dogfight…** in the Arena menu of the top bar opens a
  dialog with the dogfight settings. A collapsible **Leaderboard** panel with
  Downs, Survival, Accuracy and Rams tabs, the match countdown and **End match**
  appears while the arena is on; this terminal's aircraft are set in bold.
- Effects: voxel explosions for shoot-downs, rams and hard ground impacts (a
  white-hot core, 60 cubes that cool from white to red to grey, fall and settle,
  and a dust ring near the ground), sparks at light contacts, dust and debris for
  ordinary crashes, and a short camera shake in the chase view within 5 m. Rings
  are drawn as voxel tori with their real tube radius; the next ring is outlined
  in white and flashes on a touch. A **Collision volumes** layer (off by default)
  shows every aircraft's collision sphere and its hit sphere. Lab replays show
  the same effects. Under reduced motion every effect is a still white cube.
- Documentation: `docs/simulation.md` and `docs/simulation.zh-CN.md` (what the
  built-in simulator is for, the simulators in the field and how to connect PX4
  SITL with Gazebo or ArduPilot SITL), and the dogfight, match, leaderboard,
  `obs.collision` and `rammer` sections of the agent API and programs guides.

### Changed

- Simulator physics: a rigid body with quaternion attitude and an inertia
  tensor, flown by four rotors after Gazebo's `MulticopterMotorModel` (first
  order rotor speed with separate spin-up and spin-down times, thrust and drag
  torque from the rotor speed, rotor drag and rolling moment) and body drag per
  axis. A simulated flight controller runs an angle loop, a rate loop with
  filtered derivative and limited integral, and a mixer that keeps attitude
  priority when the motors saturate; `SERVO_OUTPUT_RAW` reports its outputs.
  Airframe defects act physically: a centre of gravity offset is a moment,
  motor differences are per-rotor thrust factors, a damaged propeller loses and
  shakes thrust, and the level error sits in the attitude estimate. Hover
  throttle stays at 0.50 and the top speed at 30 degrees near 7 m/s; the stick
  map, modes and hover assist are unchanged. Flights are still deterministic
  by seed.
- Bullet tracers are 120 ms long with a larger head glow.
- Stage trails restart when an aircraft jumps more than 3 m between two
  samples (a respawn or a reset) instead of drawing a line across the stage.
- The leaderboard panel coalesces its reads at most four times a second
  instead of aborting the previous read on every revision, so it keeps
  updating while rounds are flying.
- A shorter README in English and Chinese with new key art and animations, and
  a [wiki](https://github.com/ANetResearch/ANet-Relay/wiki) with the guides
  that used to live in the README.

## [0.4.1] - 2026-11-08

Bullets and hits that read from across the arena, and a reworked set of opening
films.

### Changed

- Bullets: every round is now a chain of voxel cubes with a white-hot head and a
  bright red tail inside an additive glow. Cubes never shrink below a few pixels,
  so rounds stay visible from far away. This terminal's rounds (or the first
  aircraft's in a replay) are a lighter red than everyone else's.
- Hits: every hit throws a voxel burst at the hit point, a white core that
  swells and fades and red and white shards that fly out and fall. A shoot-down
  throws a larger grey and red burst. Under reduced motion a still white cube
  marks the hit instead.
- Lab replays: duel and challenge replays now show the same tracers and the
  bursts of every hit and shoot-down. Scrubbing back and forth shows the same
  frame each time.
- Opening films: three camera-move films are replaced by five new chase films
  (tunnel, pillar slalom, dogfight, tower dive and water skim) in the style of
  the chase. The director's cut opens on an action film, starting with the
  chase, and then a cooperation film. The opening's length is unchanged.

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

[0.5.2]: https://github.com/ANetResearch/ANet-Relay/releases/tag/v0.5.2
[0.5.1]: https://github.com/ANetResearch/ANet-Relay/releases/tag/v0.5.1
[0.5.0]: https://github.com/ANetResearch/ANet-Relay/releases/tag/v0.5.0
[0.4.1]: https://github.com/ANetResearch/ANet-Relay/releases/tag/v0.4.1
[0.4.0]: https://github.com/ANetResearch/ANet-Relay/releases/tag/v0.4.0
[0.3.1]: https://github.com/ANetResearch/ANet-Relay/releases/tag/v0.3.1
[0.3.0]: https://github.com/ANetResearch/ANet-Relay/releases/tag/v0.3.0
[0.2.1]: https://github.com/ANetResearch/ANet-Relay/releases/tag/v0.2.1
[0.2.0]: https://github.com/ANetResearch/ANet-Relay/releases/tag/v0.2.0
[0.1.0]: https://github.com/ANetResearch/ANet-Relay/releases/tag/v0.1.0
