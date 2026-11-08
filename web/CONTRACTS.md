# ANet Relay web: contracts for WP6, WP7 and WP8

This file is the hand-over from WP5 (front-end foundation) to the engineers who fill the placeholders. The design
documents stay binding: `docs/design/02-experience.zh-CN.md` (surfaces, copy, motion) and
`docs/design/03-system.zh-CN.md` (§4 authority, §6 safety, §10 interfaces). Where this file and the design disagree,
the design wins; tell the lead.

## 1. Ground rules

1. **Only your directory.** WP6 owns `src/stage/`, `src/course/`. WP7 owns `src/control/`, `src/video/`. WP8 owns
   `src/fleet/`, `src/connect/`, `src/inspector/`, `src/agents/`, `src/records/`, `src/settings/`. Keep the exported
   names and props below; add files freely inside your directory. Changes elsewhere (shell, store, i18n keys,
   primitives) go through the lead.
2. **Copy lives in `src/i18n/en.ts` and `src/i18n/zh.ts`.** The key set for every surface in 02 already exists
   (`fleet.*`, `stage.*`, `possess.*`, `calib.*`, `insp.*`, `preflight.*`, `tele.*`, `param.*`, `sev.*`, `pad.*`,
   `conn.*`, `video.*`, `course.*`, `agents.*`, `records.*`, `set.*`). New keys go into both files in the same
   change; `src/i18n/i18n.test.ts` fails on any mismatch. Components call `useT()`; non-React code calls `t()`.
   Backend texts come in pairs (`text_en` / `text_zh`, `message_en` / `message_zh`): use `pickText(lang, en, zh)`.
3. **Colours only through tokens.** `var(--g50)` … `var(--g950)`, `var(--r500)` and the semantic tokens of
   `src/styles/theme.css`. No hex, rgb or named colours anywhere else (the linted exception is `theme.css`). For
   three.js materials read the computed token (`getComputedStyle(document.documentElement).getPropertyValue('--g500')`).
4. **Red has one owner.** Anything that might paint `r500` asks `useRedOwner()` (`src/store/selectors.ts`) and the
   helpers `armedTone(owner)` / `criticalTone(owner)` (`src/store/red.ts`): `'red'` means paint `r500`, `'strong'`
   means bold `g50`. Never decide red locally.
5. **Components only from `src/ui/components/ui`** (shadcn base-mira on Base UI). Icons only through
   `@/ui/icons` (`<Icon icon="…">`, `<StateIcon icon="…" alt={…}>`); register a missing key in
   `src/ui/icons/registry.ts` (lucide 1.48 geometry; nearest semantic icon when the exact name does not exist).
   No emoji. oxlint blocks direct `@base-ui/react`, `lucide` and `lucide-react` imports outside those folders.
6. **No per-frame React renders in the 3D loop.** Inside `useFrame` (or any rAF / canvas loop) read
   `useRelay.getState().fleet` and `backendNow()` directly; never call a hook, never `setState` per frame. React
   state may change at most on discrete events (selection, view switch, course state), never per snapshot.
7. **Sticks only through the InputLayer engine over the WebSocket.** `sendSticks(vehicle, {roll, pitch, yaw,
   throttle})` from `@/api` is called by `control/InputLayer` and nothing else. It is rate-limited to 50 Hz per
   vehicle, drops frames while the socket is not open and never queues them. Do not post sticks over REST from the
   window (`api.sticks` exists for completeness). Stop paths (`stopVehicle`, `stopAll`) never wait on UI state.
8. **Motion** uses the tokens in `src/styles/motion/*` (durations `--duration-quick|fast|medium|slow`, easing
   `--ease-smooth-out`) and must degrade under `html[data-motion="reduced"|"off"]` (`motionReduced()` in
   `@/ui/motion/tier`). Screenshots use `?motion=off`.
9. **Dev fixture.** `?fixture=busy|empty|alerts` runs the whole window on `src/dev/fixtures.ts` (6 aircraft: Quad 01
   PX4 idle, Quad 02 armed and possessed by PAD 1 with harness-a waiting, Quad 03 link lost while armed, Quad 04
   delayed under harness-a, SIM-1 idle, SIM-2 armed flying cage-loop under harness-a; one session; one critical alert).
   Its REST stand-in handles ack, stop, stop-all, possess (keyboard) and release (with handback). `&gallery=1` shows
   the primitives. `&lang=zh` and `&motion=off` work everywhere. Extend the fixture inside your work package's needs
   by asking the lead, or keep a private fixture next to your module.
10. **Gates** before hand-back: `npm run typecheck`, `npm run lint`, `npm test`, `npm run build`, and screenshots of
    your surfaces at 1440x900, 1280x800 and 1100x720 in EN and ZH (`npm run shots`, extend `scripts/shots.mjs`).

## 2. Shared building blocks

| Need | Use |
|---|---|
| Snapshot types | `import type { Fleet, Vehicle, Alert, RelayEvent } from '@/api'`; nested shapes as `Vehicle['link']`, `Vehicle['course']` (the generated file may rename nested interfaces) |
| REST | `api.*` from `@/api` (one helper per 03 §10.1 route; operator header added automatically); errors are `RelayApiError` with `code`, `status`, `text(lang)`, `detail`; show failures with `notifyError(err)` from `@/shell/notify` |
| Stream | `sendSticks`, `cancelSticks`, `stream()?.reconnect()` from `@/api` |
| Store | `useRelay` (backend state), `useUi` (window state), selectors in `@/store/selectors`, `backendNow()` from `@/store/relay` |
| Toasts | `notify(title, {level: 'info'|'warn', description})` from `@/shell/notify` (info 3 s, warn 5 s; never for critical) |
| Commands | `registerCommands(() => Command[])` from `@/shell/commands` |
| Keys | `registerHotkey({combo, handler, workspace?, inText?, inOverlay?, repeat?})` from `@/shell/keyboard`; combos are layout-independent (`'p'`, `'w'`, `'space'`, `'alt+mod+b'`); ⌘. and Esc are reserved by the shell |
| Shell actions | `stopAll`, `stopVehicle(id)`, `stopFocused`, `ackAlert(id)`, `possess(id)`, `release(id)`, `scanNetwork`, `addSim`, `moveSelection(±1)` from `@/shell/actions` |
| Formatting | `@/lib/format`: `angle`, `yaw`, `heading`, `metres`, `climb`, `speed`, `volts`, `amps`, `percent`, `hertz`, `millis`, `seconds1`, `duration`, `courseTime`, `split`, `clock`, `batteryCells` (all return `null` for unknown input) |

### Primitives (`@/ui/relay`)

| Primitive | Purpose (02 ref) |
|---|---|
| `LinkDot`, `LinkStatus link critical?` | Live / Delayed 3.4 s / Lost 7 s / Offline / Waiting with shape-coded dot (§2.3) |
| `ArmedWord armed` | ARMED under the single-red rule, "Disarmed" in g400 (§2.3) |
| `SimTag` | Mono "SIM" frame (§2.3, §6.4) |
| `useStatusBar(vehicle, hasCritical)` | Card left-bar state: `armed-red`, `armed-strong`, `critical-red`, `lost`, `idle` (§4.3) |
| `BatteryBar battery`, `BatteryReadout battery` | 5 cells 6 x 8, warn blink, critical by rule; "11.6 V 62 %" (§4.3) |
| `ControllerPill vehicle showWaiting? padDisconnected?`, `MorphPill spec` | YOU · PAD 1 / AGENT · harness-a / harness-a · waiting / Idle with width morph and cross-fade (§2.3, §5.4) |
| `Value value reason` | Tabular value or "—" with the reason in a tooltip (§1.5, §20 item 7) |
| `Readout label size` | hud-cap label over a value or hud-kpi number (§2.2, §4.4) |
| `TypeToConfirm word prompt actionLabel onConfirm onCancel?` | ARM, parameter name, MOTOR; enabled only on an exact match (§11) |
| `HoldProgress active durationMs`, `HoldTimer` | 1 px g50 hold line and its pure timer (§7.5) |
| `Section title right?`, `Group label count? collapsible?` | Panel section (title row 40) and hud-cap group (§2.4) |
| `EmptyState title body actions`, `Placeholder` | Left-aligned empty block, 220 wide (§4.3) |
| `useAgo(t)` | "12 s ago" on the backend clock |

## 3. Placeholders

Every placeholder renders a labelled dashed box (or `null` for invisible engines). Replace the body, keep the
export and the props.

### stage/Stage.tsx: `Stage` (WP6)
- **Mount:** `src/shell/FlyWorkspace.tsx`, centre panel, inside the stage frame (L0, rounded, no border). The frame
  is `position: relative`; fill it (`absolute inset-0`).
- **Props:** `{ children?: ReactNode }` — 3D layers that live inside the scene (`<CourseLayer />` is passed).
- **Reads:** `useRelay.getState()` in the frame loop; `useSelectedId()`, `useVehicleIds()` for discrete changes;
  `useSafetyVolume()`; `useRedOwner()` for the nose marker and labels.
- **Owns:** the HUD overlays of 02 §4.4 that belong to the stage itself (selected identity block top-left, view
  switch and layer menu top-right, bottom HUD strip 640 x 64, stick visualisation bottom-left while possessed, key
  hints), view cycling (register `c` with `registerHotkey`), replay state of 02 §13.
- **Must not:** paint the course HUD (CourseHud), video (VideoPip) or toasts.

### course/CourseLayer.tsx: `CourseLayer` (WP6)
- **Mount:** child of `<Stage>`; render three.js objects inside the scene (the placeholder renders DOM only because
  the Stage placeholder is DOM).
- **Reads:** `Vehicle['course']` of every aircraft, `api.courses()` for ring geometry (scaled by the backend).

### course/CourseHud.tsx: `CourseHud`, `CourseFinishCard` (WP6)
- **Mount:** stage overlay, `CourseHud` top centre (16 from the top), `CourseFinishCard` stage centre.
- **Props:** `CourseHud { vehicleId: string | null }` (the selected aircraft); `CourseFinishCard` none (it watches
  course state transitions itself). Return `null` when nothing applies.
- **Keys:** register `g` (start / stop course for the selected aircraft, Fly workspace).
- **Uses:** `courseTime`, `split` formatters; copy `course.*`; agent finishes toast through the shell (event
  `course_finished` already toasts for agents; do not duplicate).

### control/ControlTab.tsx: `ControlTab` (WP7)
- **Mount:** inside the Inspector's Control tab (WP8 renders `<ControlTab vehicleId={id} />`).
- **Props:** `{ vehicleId: string }`.
- **Uses:** `api.possess / release / arm / disarm / mode / skill / courseStart / courseStop / confirm /
  calibration / motorTest`, `TypeToConfirm` (ARM for real aircraft, MOTOR), `HoldProgress`, `ControllerPill`,
  preflight copy `preflight.*`, `possess.agentFlying` above the Possess button. Possess is the only solid g50
  button in the app (02 §5.1).

### control/InputLayer.tsx: `InputLayer` (WP7)
- **Mount:** once, at the app root (`src/app/App.tsx`). Renders nothing.
- **Owns:** keyboard source (register `p`, `w a s d`, `q e`, `r f`, `space` with `registerHotkey`, `workspace:
  'fly'`, `repeat: true` where needed), screen sticks state, gamepads (Gamepad API polling, DualSense mapping of
  02 §7.5, L2 + R2 stop), hold timers, auto-binding of sims (02 §7.3), stick stop rules (03 §6.1 H2: blur, link
  lost, pad disconnect).
- **Writes:** `useRelay.getState().setInputs({ gate, pads })` (`PadInfo` in `src/store/relay.ts`); the top bar
  reads it.
- **Sends:** `sendSticks` only; `cancelSticks(vehicle)` when input stops.

### control/GamepadHint.tsx: `GamepadHints` (WP7)
- **Mount:** once at the app root; position itself bottom centre, 120 above the window bottom, 400 x 88 cards, at
  most two (02 §7.2). Copy `pad.*`.

### control/PadsPopover.tsx: `PadsPopover` (WP7)
- **Mount:** body of the top-bar input-device popover (the shell owns trigger, popover, width 320, padding 12).
- **Props:** none. Reads `usePads()`.

### video/VideoPip.tsx: `VideoPip`, `VideoSourceGroup` (WP7)
- **Mount:** `VideoPip` in the stage overlay, bottom right (16 inset), `null` when nothing is bound; it may move
  itself to other corners (snap) and must not cover the course HUD or Stop all. `VideoSourceGroup` inside the
  Inspector Overview tab (WP8 renders it).
- **Props:** `VideoPip { vehicleId: string | null }`, `VideoSourceGroup { vehicleId: string }`.

### fleet/FleetPanel.tsx: `FleetPanel` (WP8)
- **Mount:** left resizable panel (L1 surface already drawn by the shell; fill it).
- **Reads:** `useVehicleGroups()`, `useVehicle(id)` per card (memoise cards by id), `useScan()`, `useAlertsForVehicle`.
- **Writes:** `useUi.getState().select(id)`; selection opens the inspector sheet automatically below 1200 px.
- **Uses:** `useStatusBar`, `LinkStatus`, `ArmedWord`, `BatteryReadout`, `ControllerPill showWaiting`, `SimTag`,
  `EmptyState`, `HoldProgress` (hold lines from the InputLayer), context menu from `ui/components/ui/context-menu`.
- **Actions:** `openConnect('scan'|'address'|'bridge')`, `scanNetwork()`, `addSim()`.

### connect/ConnectDialog.tsx: `ConnectDialog` (WP8)
- **Mount:** app root, controlled by the shell: `{ open, tab, onOpenChange }` from `useUi().connect`.
- **Width:** 560 (02 §8). Copy `conn.*`. Bridge wizard uses `api.network()`, `api.bridge(ip)`,
  `api.configureBridge(ip, …)`, `window.relay?.openBridgePage?.(ip)`.

### inspector/Inspector.tsx: `Inspector` (WP8)
- **Mount:** right resizable panel (`variant: 'panel'`), or inside the compact sheet (`variant: 'sheet'`, 360 wide).
- **Props:** `{ vehicleId: string | null; variant: 'panel' | 'sheet' }`.
- **Contains:** header 64, tabs Overview / Control / Telemetry / Parameters / Log; renders `<ControlTab>` and
  `<VideoSourceGroup>` from WP7. Parameters may use `@tanstack/react-virtual` (installed).

### agents/AgentsWorkspace.tsx: `AgentsWorkspace` (WP8)
- **Mount:** main area when the workspace is Agents (key `2`). Columns 360 | flexible | 320 (02 §12).
- **Reads:** `useSessions()`, `api.sessions()` for recent calls, `env().apiBase`.

### records/RecordsWorkspace.tsx: `RecordsWorkspace` (WP8)
- **Mount:** main area when the workspace is Records (key `3`).
- **Props:** `{ focus: { vehicle: string; t: number } | null }` — set when the relay timeline is clicked.
- **Uses:** `api.historySessions / historySession / historyExportUrl`, lieflat classes in `styles/lf.css` for
  charts and tables (`data-lf-table`).

### settings/SettingsDialog.tsx: `SettingsDialog` (WP8)
- **Mount:** app root, controlled: `{ open, section, onOpenChange }` from `useUi().settings`; ⌘, opens it.
- **Width:** 640 with categories on the left (02 §16). `api.settings()` / `api.putSettings()`; language through
  `setLang` from `@/i18n`.

## 4. Interfaces the shell assumes from WP3 / WP4

These are not fully pinned in 03; the shell implements the tolerant reading below.

- **WebSocket envelope:** `{type: 'hello'|'fleet'|'event', ...}` with the payload either under `data` (preferred),
  under `fleet` / `event`, or flat. Client frames: `{type: 'sticks', vehicle, roll, pitch, yaw, throttle}` and
  `{type: 'ping'}`. Operator token as query parameter `operator` (03 §6.2).
- **`GET /events?after=&limit=`** may return an array or `{events: [...]}`; used to fill the gap after a reconnect.
- **`window.relay.apiBase`** may be the origin or end in `/api/v1`; both are normalised.
- **Optional preload extensions** (typed in `src/api/env.ts`): `onBackendState(cb)` with
  `{phase: 'starting'|'spawned'|'healthy'|'failed'|'exited', reason?: 'timeout'|'ports_in_use'|'crashed', ports?}`
  drives the boot stages and the failure states of 02 §15; `restartBackend()`, `quit()`, `openBridgePage(ip)`;
  `onMenu(cb)` forwards menu accelerators (`'stop-all'`, `'toggle-fleet'`, `'toggle-inspector'`,
  `'toggle-timeline'`, `'settings'`, `'command-palette'`). Without them the window falls back to health polling.
- **Types:** `npm run types` regenerates `src/api/types.ts` from `src/api/schema.json` once WP3 exports it;
  `src/api/types.test.ts` then fails on drift (CI: `node scripts/gen-types.mjs --check`).

## 5. WP8 additions (fleet, connect, inspector, agents, records, settings)

Appended by WP8. These are small stores and hooks other work packages read or write; each lives inside a WP8
directory, so nobody edits the shell or the shared store for them.

### records/replayState.ts: `useReplay` (read by WP6, written by WP8)
- **What:** the replay state of the stage (02 §13). While `active`, the Records workspace mounts `<Stage />` in its
  replay viewport (the Fly workspace is not mounted then, so there is only ever one stage) and moves the cursor.
- **Shape:** `{ active: boolean; recordId: number | null; vehicleId: string | null; label: string; kind: 'real' |
  'sim'; t: number | null /* unix s at the cursor */; frame: ReplayFrame | null; playing: boolean }` with
  `ReplayFrame = { t; armed: boolean; mode: string | null; roll; pitch; yaw /* rad */; x; y; z /* ENU m, sim or
  local frame */; alt: number | null; throttle: number | null /* 0..100 */; controller: 'none' | 'human' | 'agent'
  | null }` (all numbers `number | null`). Helpers: `exitReplay()`, `isReplaying()`.
- **Stage rules (WP6):** read it in the frame loop with `useReplay.getState()` (no hook per frame). While `active`,
  draw only the replayed aircraft (`label`, `kind`) at `frame`: position when x/y/z are present, otherwise on its
  pad slot at `alt`; props spin while `armed`. Show `t('stage.replay', { t: clock(frame.t) })` top-left with an
  "Exit replay" button that calls `exitReplay()`. Hide the live-only overlays (bottom HUD strip, stick
  visualisation, key hints, course HUD) and do not register live hotkeys for the replayed aircraft. The replay
  viewport is about 420 x 300 at 1440 x 900, so overlays must fit that size.
- **Esc:** 02 §13 says Esc leaves the replay, but Esc with no overlay also stops the selected aircraft (02 §3,
  shell keyboard layer). Advertising Esc for leaving a replay invites an accidental stop, so WP8 uses an explicit
  Exit replay button and does not bind Esc. The lead decides whether the shell should treat Esc as "exit replay"
  first while the Records workspace is shown.

### fleet/holds.ts: `useHolds` (written by WP7 InputLayer, read by WP8 fleet cards and WP6 stage labels)
- **What:** hold-to-confirm progress lines of 02 §7.5 (Options 1 s, L1 + R1 1 s, ○ 1 s, pointer holds).
- **API:** `setHold(vehicleId, { kind: string; durationMs: number } | null)` on press / release; readers use
  `useHold(vehicleId)` → `{ kind, durationMs, startedAt } | null` and render `<HoldProgress active durationMs>`.
  The card draws it along its bottom edge. Clear the hold on release, on completion and on pad disconnect.

### settings/prefs.ts: `usePrefs` (window preferences, read by WP6 and WP7)
- **What:** preferences that only the window uses, persisted in the backend settings under `ui` (so they survive a
  reinstall of the window) and cached in localStorage `relay.prefs` for the first frame.
- **Shape:** `{ units: 'm' | 'ft'; startView: 'overview' | 'chase' | 'top'; chaseOnCourse: boolean; padDeadzone:
  number /* 0.04..0.15, default 0.08 */; padExpo: number /* 0..0.6, default 0.3 */; bootMode: 'full' | 'short' |
  'minimal' /* v0.3 WP-A, default 'short'; mirrored into localStorage relay.boot.mode for the next boot */ }`.
- **API:** `usePrefs((p) => p.padDeadzone)`, `usePrefs.getState()` in loops, `setPrefs(patch)` to write (debounced
  PUT /settings). WP7's pads popover and the Controllers page of Settings edit the same two values through it; the
  stage reads `startView` once at mount and `chaseOnCourse` when a course starts. `formatLength(m, units)` and
  `useLength()` format metres or feet; WP8 surfaces use them, the shared `@/lib/format` does not yet.

### inspector/tabs.ts: `useInspectorTab`, `openInspector(id, tab?)`
- **What:** the selected inspector tab (`'overview' | 'control' | 'telemetry' | 'parameters' | 'log'`).
- **Use:** `openInspector(id, 'control')` selects the aircraft, expands the inspector (or opens the compact sheet)
  and switches the tab. The fleet card menu "Calibrate signs" and the Overview preflight line use it; WP7 may use it
  for "Open preflight" style links. Control-tab sub-sections (calibration) stay WP7's: when `focus === 'calibration'`
  in the store, ControlTab may scroll the Calibration group into view and call `clearFocus()`.

### WP8 copy (`fleet/copy.ts`)
- WP8's surfaces needed about 150 strings that are not in `src/i18n/{en,zh}.ts`. They live in `fleet/copy.ts`
  (`en8`, `zh8`, same key style, no key overlaps with the shared dictionaries) and are read with `useT8()` /
  `t8()`, which fall back to the shared dictionaries for every shared key. `fleet/copy.test.ts` enforces identical
  key sets and no overlap. The lead can fold them into `src/i18n` at any time; call sites do not change.

## 6. WP7 additions (control, video)

Everything below lives in `src/control/` and `src/video/`. Placeholder exports and props of §3 are unchanged.

### Input engine (`control/engine.ts`, hosted by `control/InputLayer.tsx`)
- **Rules in one place:** keyboard (P, W A S D, Q E, R F, Space; Fly workspace, registered with `registerHotkey`),
  on-screen sticks (Control tab), gamepads (Gamepad API, W3C standard mapping, DualSense map of 02 §7.5). Frames go
  out only for aircraft the snapshot shows possessed by the matching source, at 50 Hz through `sendSticks`.
- **Stops (03 §6.1 H2, H3):** window blur / hidden stops every human stick at once and resumes only after a stick
  moves; link `lost` / `offline` stops that aircraft and resumes after a movement once the link is back; a
  disconnected controller stops at once, keeps its possession 3 s for the same controller id, then releases it.
- **Sources:** the Possess button and `P` possess with `input: 'keyboard'`; keyboard and on-screen sticks are one
  window source (either flies an aircraft possessed by `keyboard` or `screen`). Pads possess with
  `{input: 'gamepad', pad}`. Auto-binding (02 §7.3) picks the lowest-numbered sim with no possession **and no agent
  controller** (plugging in a pad never suspends an agent silently; Options still possesses it).
- **Throttle (03 §5.2):** dead zone, then expo, then the piecewise curve through (−1, 0), (0, centre), (+1, 1);
  centre = `calibration.hover` in manual / auto mode classes, 0.5 in `althold` / `position`. Keyboard throttle starts
  low on the ground in a manual mode (so the aircraft can arm) and at centre when airborne (no height loss).
- **Shared preferences:** dead zone and expo are WP8's `settings/prefs.ts` (`padDeadzone`, `padExpo`); the pads
  popover writes them with `setPrefs`.

### Read-only state for other packages (`control/store.ts`)
| Need | Use |
|---|---|
| Hold progress line on cards / stage labels | mirrored into WP8's `fleet/holds.ts` (`useHold(id)`; kinds `options`, `arm`, `reset`) |
| Pad disconnected pill | `usePadDisconnected(id)` → pass to `ControllerPill padDisconnected` |
| Stick visualisation in a frame loop | `getLiveSticks(id)` → `{frame: {roll, pitch, yaw, throttle}, centre, sending, source, at} \| null` (non-reactive) |
| Paused / awaiting movement | `useInputPaused()`, `useAwaiting(id)` → `'focus' \| 'link' \| null` |

### Window events (dispatched on `window`)
- `relay:view-cycle` — DualSense touchpad press; the stage cycles views like `c`.
- `relay:pad-bound` — `detail: {vehicle, pad}` when a connection card names an aircraft; the stage pulses its ring.

### Stage overlays owned by WP7 (portalled into `[data-stage-frame]`)
- **Input notices** (02 §5.3, §5.4, §7.3): top-centre chips at 16 px from the top (80 px while the selected aircraft
  runs a course, below the course HUD): "Input paused · window not focused", "Move a stick to resume", "Link
  restored · move a stick to resume", "<aircraft> is airborne with no controller" (persistent while the backend's
  `airborne_no_controller` alert is open). Stage-centre L3 card: "Link lost · … · sticks stopped", "Controller n
  disconnected · … sticks stopped". z-index 4; WP6 overlays should stay at or below 3 in those two areas.
- **VideoPip** renders into the stage frame itself (not into the bottom-right wrapper of FlyWorkspace, which stays
  empty) so it can snap to any corner: top corners 52 px from the top (80 px during a course), bottom corners 16 px
  from the bottom, 16 px from the side; 320–640 px wide, 16:9. The stage lifts its HUD strip above it
  (`[data-video-pip]`, WP6 `usePipClearance`).

### Inspector parts
- `ControlTab` has no vertical padding of its own (the inspector's tab content provides `py-3`). It mounts WP6's
  `course/CourseControls.tsx` (`CourseControls({vehicleId})`) when that file exists, else a minimal course group.
  `openInspector(id, 'control', 'calibration')` opens and scrolls to the Calibration group, then `clearFocus()`.
- `VideoSourceGroup({vehicleId, thumbnail = true, framed = false})` is the **body** of the Overview "Video" block:
  the host draws the title and side padding (WP8 `Block`). `framed` adds its own group label. `VideoThumb({vehicleId})`
  is the 16:9 thumbnail alone (click shows the PiP). Bindings persist per aircraft id by device label
  (`localStorage relay.video`); one device feeds one aircraft.
- `MappingReference` (control/PadsPopover.tsx) is the 02 §7.5 table; Settings › Controllers may reuse it.

### Copy
- WP7 strings missing from `src/i18n` live in `control/strings.ts` (`useCT()` / `ct()`, keys `ctl.*`, `pf.*`, `rc.*`,
  `pad.*` (no overlap with shared keys), `map.*`, `vid.*`); `control/strings.test.ts` enforces identical key sets and
  placeholders. The lead may fold them into `src/i18n` without touching call sites' behaviour.

### Requests to the lead (not changed by WP7)
1. `api/ws.ts` `writeSticks` sends only `{vehicle, roll, pitch, yaw, throttle}`. The backend accepts optional
   `input` / `pad` on a sticks frame and then checks them against the possession (H1). Passing the source through
   `sendSticks(vehicle, frame, source?)` would let the backend reject a frame from the wrong input.
2. WS `{"type": "error", ...}` frames (e.g. `not_possessed` for sticks) are dropped by `parseMessage`; surfacing them
   would let the window show why sticks are refused.

## 7. WP6 additions (stage, course)

Everything below lives in `src/stage/` and `src/course/`. Placeholder exports and props of §3 are unchanged
(`Stage({children})`, `CourseLayer()`, `CourseHud({vehicleId})`, `CourseFinishCard()`).

### Stage state (`stage/store.ts`)
- `useStage`: `view` (`'overview' | 'chase' | 'top'`), `layers` (`grid`, `safety: 'auto' | 'on' | 'off'`, `course`,
  `labels`, `trails`), `setView`, `cycleView`, `setLayer`, `requestFlyTo(id)`, `courseHudShown`. View and layers
  persist in `localStorage relay.stage`. Selecting overview again re-frames.
- `stagePulse(vehicleId)`: one 350 ms ripple on the aircraft's ring (used for `relay:pad-bound`; any package may call it).
- Start view comes from WP8 `usePrefs().startView`; chase on course start from `usePrefs().chaseOnCourse`.

### Inputs consumed
- Window events `relay:view-cycle` (cycles like `c`) and `relay:pad-bound` (`detail.vehicle` pulses).
- `[data-video-pip]` inside the stage frame: the HUD strip and stick panel lift above a bottom-corner PiP.
- WP8 `useReplay` (stage shows only the replayed aircraft, Exit button top-left), `useHold(id)` (hold progress
  under stage labels).
- Course HUD visibility: while the selected aircraft runs a course, stage overlays in the top band start at 80 px on
  stages narrower than 1000 px (matches WP7's notice offset).

### Course parts for other packages
- `course/CourseControls.tsx`: `CourseControls({vehicleId})` — the Control tab's "Ring course" group (select,
  Start / Stop, best, honest no-position note, "agent is flying" note). Mounted by WP7's `CourseGroup`.
- `CourseBrief({vehicleId, className?})` / `courseBriefText(id)` — "Ring 2/4 · 00:07.1" for a fleet card's third row
  (renders nothing without an active run; refreshes at 10 Hz without re-rendering the host).
- `toggleCourseOn(vehicleId, courseId?)` — the `g` key and the palette command use it; starts the chosen course or
  stops the active run, toasts on refusal.
- `useCourse` (`course/store.ts`): course definitions, chosen course (`localStorage relay.course`), best times seen in
  this window, completion card; `watchCourses()` is reference counted.

### Hotkeys and commands
- `c` cycles views (chase skipped when nothing can be chased); `g` starts / stops the course of the selected aircraft
  (Fly workspace). Palette: view switch, re-frame, start / stop course.

### Copy
- WP6 strings missing from `src/i18n` live in `stage/copy.ts` (`useT6()` / `t6()`, keys `stage.*`, `course.*` that
  the shared dictionary lacks; falls back to the shared i18n). The lead may fold them into `src/i18n`.

### Dev probe
- Dev builds only: `window.__relayStage = {live, rig, framingExtras, stage}` for screenshot scripts. Not present in
  production builds.

## 8. v0.3.0 WP-C additions (control feel, transmitters, driving HUD; docs/design/05 §5, §6)

### Fire input hook (`control/actions.ts`, consumed by the arena package)
- **`setFireHandler((vehicle, on) => void): () => void`** installs the hold-to-fire transport (05 §7.4) and returns
  a function that restores the no-op default. The input engine calls it through `EngineActions.fire` with
  `on = true` at the press of R1, keyboard X or a bound fire switch, again every 100 ms while held (the backend
  treats 300 ms without a refresh as released) and `on = false` at the release, on blur, on possession end or when
  the sticks stop flowing. Only possessed simulated aircraft whose sticks are being sent; never a real aircraft.
- `useInputUi((s) => s.firing)` lists the aircraft a fire input is held for (for a crosshair or HUD cue).

### Driving HUD (`stage/DrivingHud.tsx`, mounted by `Stage()`)
- Bottom left above the stick panel, 220 wide (`DRIVING_HUD_W`), `DRIVING_HUD_H` high in the stage collision
  layout; only while this window flies an aircraft (`useDrivenId()`).
- **Arena slot:** `setDrivingHudSlot(vehicleId, { hp?: number /* 0..100 */, ammo?: { n, max, reloading,
  progress? /* 0..1 */ } } | null)` feeds the third row; `<DrivingHud hp ammo>` props win over the feed. Without
  either, row 3 shows only the throttle bar. `DrivingHudView` + `hudModel()` are the pure parts (SSR-tested).

### Control preferences (`settings/prefs.ts`)
- New fields: `controlFeel: 'gentle' | 'standard' | 'sport'`, `stickMode: 1 | 2`, `invert: {roll, pitch, yaw,
  throttle}`, `throttleDetent: boolean`, `padProfiles: Record<deviceId, PadProfile>` (at most 8, `control/devices.ts`).
  `setControlFeel(feel)` also sets `padExpo` to the preset's expo. All persist under `settings.ui`.

### Input engine (`control/engine.ts`)
- `EngineActions` gains `simAssist(vehicle, on)`, `fire(vehicle, on)` and `setFeel(feel)`; `EngineDeps.prefs()`
  returns the control preferences (missing fields take `DEFAULT_CONTROL`), `EngineDeps.profiles()` the learned
  profiles. `AwaitReason` gains `'throttle'` (H5: a transmitter's throttle must be lowest, or centred in hold
  modes, before sticks flow). `setPadLearning(pad, on)` (InputLayer) takes a controller out of flight while the
  mapping wizard runs.
- Hotkeys added in the Fly workspace: `z` (throttle to the hover point), `h` (hover assist), `x` (held: fire).

## 9. v0.3.0 WP-A additions (opening, voxel loaders)

Design: `docs/design/05-v030.zh-CN.md` §2. Use these instead of a spinner or a "Loading…" line for panel-level
loading; buttons keep `Spinner`.

- **`ui/voxel/VoxelLoader.tsx`**: `<VoxelLoader size={16 | 24 | 40} label? caption? />`, a 3 x 3 x 3 voxel cube
  that builds and tears down in a loop while it holds a loop slot (`ui/motion/budget.ts` `useLoopSlot()`, at most two
  on screen) and is static otherwise and in reduced motion. `role="status"`, named "Loading" unless `label` is given.
- **`ui/voxel/VoxelWipe.tsx`**: `<VoxelWipe trigger={key} />` inside a positioned container: a 16 px g850 voxel
  wave over that container when `trigger` changes (workspace switch in `App.tsx`, replay open in Records).
- **`stage/StageLoading.tsx`** and **`stage/loading.ts`** (`useStageModel`, `markStageModelReady()`): the stage's
  model loader; the scene marks the model ready (GLB, fallback airframe or the no-WebGL notice).
- **Opening**: `app/bootFilm.ts` (pure timeline, plan and act machine; v0.4 order of 06 §8: cold, film, cut,
  assemble, hold, then wait / reveal; `fullClips(n)`, `CLIPS`, `COOP_CLIPS`), `app/bootCinema.ts` (DOM layers
  `#boot-film`, `#boot-voxels` inside `#boot-mask`), `app/boot.css`. Durations are the `--boot-*` and `--voxel-*`
  tokens in `styles/motion/ext-tokens.css`; JS reads them with `ui/motion/tokens.ts` `msToken()`.
- The pre-flight list's first poll (`control/PreflightList.tsx`) and a saved bench report loading
  (`bench/ReportView.tsx`) show `<VoxelLoader size={24} />` (wired at integration).

## 10. v0.3.0 integration (WP-E: seams between the packages)

- **Fire transport:** `InputLayer` installs `setFireHandler((vehicle, on) => sendFire(vehicle, on))` (`arena/store.ts`,
  the stream `fire` message). The input engine's `FireTracker` (WP-C) is the only fire timing; R1 fires only without
  L1 (L1 + R1 stays the arm chord), keyboard `X` is a flight key.
- **Driving HUD row 3:** `Stage()` mounts `arena/ArenaDrivingHud`, which feeds `DrivingHud` its `hp` (scaled to
  0..100) and `ammo` (with `progress`, the arena view's `local.reload[vid]`) from `useArenaLocal(useDrivenId())`.
- **Bench and hover assist:** the flight steps S9 / S10 fail at once on a simulator with hover assist off (finding
  `hover_assist_off`) and land and fail when it is switched off in flight; the Bench tab shows the finding text.
- **Copy:** bench and arena keep their own dictionaries (`bench/copy.ts`, `arena/copy.ts`, each with an en / zh parity
  test); the shared `i18n` files carry only the keys other modules read.

## 11. v0.4.0 WP-3 additions (Lab, 06 §7)

- **Workspace:** `Workspace` gains `'lab'` (keys `4` and `mod+4`, command `ws.lab`, app-control `set_workspace`).
  `src/lab/LabWorkspace.tsx` loads lazily (CodeMirror is in its chunk). The Lab owns `src/lab/`; its copy is
  `lab/copy.ts` (`useTL()` / `tL()`, en / zh parity test).
- **Store:** `lab/store.ts` `useLab` (program list, open program, unsaved `source` / `params` / `tests`, validation,
  tab, challenge, Run-tab aircraft, judge and duel jobs). `isDirty(state)`, `testsOf(program)`.
- **API:** `lab/api.ts` `labApi.*` covers the WP-1 program routes and the WP-2b routes (`/challenges`,
  `/challenges/{id}/submit`, `/jobs/{id}`, `/duels`, `/ladder`, `/traces/{id}`). WP-2b bodies go through
  `normChallenges`, `normJob`, `normLadder`; the backend shapes (`schemas_judge.py`) are in the file header.
  `lab/trace.ts fromJudgeTrace` converts the evaluator's version 1 trace for `parseTrace`. WP-5:
  `lab/judgeWire.test.ts` checks both against `lab/fixtures/judge-bodies.json`, written from real responses by
  `backend/tools/judge_fixture.py` (rerun it when a judge response shape changes). `RestTransport` methods now
  include `PATCH` (live parameters).
- **Replay of several aircraft:** `useReplay` gains `crafts: ReplayCraft[] | null` (`{id, label, sim, frame, program,
  state, hp}`). While set, the stage places every craft by `frame.x/y/z` (`stage/live.ts`), `Fleet` mounts one
  aircraft per craft and the label reads `PROGRAM · <program> · <state> · HP n` with no ARMED word. Craft ids are
  `trace:<id>` (`lab/trace.ts traceStageId`) so they never collide with live aircraft. Records sets `crafts: null`.
  `lab/traceReplay.ts` (`openTrace`, `openTraceById`, `closeTrace`, `seekTrace`, `stepTrace`) drives it;
  `TraceLayer` (scene, next to `ArenaLayer`) draws the rounds and the arena ring; `TraceReplayBar` (Fly stage
  overlay, bottom centre) plays, scrubs and exits, and closes the replay when an aircraft is newly possessed.
- **Stage label:** a live aircraft under `controller === 'program'` reads `PROGRAM · <name> · <state>`.
- **Inspector:** the Control tab mounts `lab/ProgramGroup` after the course group.
