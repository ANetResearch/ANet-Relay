// Input engine (02 §5, §7; 03 §5.2, §6.1 H1-H4). One instance per window, ticked at 50 Hz by InputLayer.
// It reads the keyboard, the on-screen sticks and the Gamepad API, applies the binding rules and the stick shaping,
// and sends human sticks only for aircraft the fleet snapshot shows as possessed by the matching source.
// Stop rules: no frames while the window is unfocused, while the link is lost or for a disconnected controller;
// after focus returns or the link recovers, frames resume only once a stick moves. Everything that touches the
// outside world goes through EngineDeps so the rules are unit-tested (engine.test.ts).
// v0.3 (05 §6, WP-C): control feel presets, Mode 1 and axis inversion, a radial dead zone on the attitude stick, a
// linear throttle with an optional hold detent and a 300 ms continuity ramp across mode class changes, momentary
// R / F in hold modes, Z (throttle to the hover point), H / Triangle (hover assist), hold-to-fire on R1 and X,
// D-pad up / down for the feel preset, transmitters and learned controller profiles with switch bindings, and H5.
import type { Vehicle } from '@/api'
import { HoldTimer } from '@/ui/relay/HoldProgress'
import {
  allocateIndex,
  assistAction,
  autoBindTarget,
  HOLD_MS,
  isWindowSource,
  linkUsable,
  optionsDecision,
  optionsHoldVehicle,
  possessedBy,
  RECONNECT_GRACE_MS,
  sourceOf,
  throttleReady,
  type SourceKey,
} from './bindings'
import { defaultProfile, padKind, readMapped, type MappedReading, type PadKind, type PadProfile, type SwitchAction } from './devices'
import { FireTracker } from './fire'
import {
  FLIGHT_KEYS,
  isStopChord,
  KEY_SLEW_PER_S,
  keyTargets,
  keyThrottle,
  moved,
  padModel,
  readPad,
  roundFrame,
  slew,
  throttleCentre,
  type FlightKey,
  type GamepadLike,
  type PadModel,
  type PadReading,
  type ShapePrefs,
  type StickFrame,
} from './mapping'
import {
  DEFAULT_CONTROL,
  FEELS,
  isHoldClass,
  logicalSticks,
  shapeLogical,
  stepFeel,
  ThrottleContinuity,
  throttleFromStick,
  type ControlFeel,
  type ControlPrefs,
  type Logical,
} from './shaping'
import type { AwaitReason, HintCard, HoldKind, HoldState } from './store'

/** Axis change that counts as "moved a stick" after a pause (raw units). */
export const RESUME_THRESHOLD = 0.2
const PENDING_MS = 1500
/** A held fire input is refreshed this often; the backend releases it 300 ms after the last (05 §7.4). */
export { FIRE_REFRESH_MS } from './fire'

export type EngineNotice =
  | { kind: 'realArm' }
  | { kind: 'maxPads' }
  | { kind: 'taken'; vehicle: string; by: SourceKey }
  | { kind: 'noSelection' }

export interface EngineActions {
  possess(vehicle: string, pad: number | null): Promise<unknown>
  release(vehicle: string): Promise<unknown>
  arm(vehicle: string, on: boolean): Promise<unknown>
  stop(vehicle: string): void
  mode(vehicle: string, name: string): Promise<unknown>
  course(vehicle: string): void
  resetSim(vehicle: string): Promise<unknown>
  moveSelection(delta: 1 | -1): void
  viewCycle(): void
  notice(n: EngineNotice): void
  /** hover assist of a simulated aircraft (05 §5.2) */
  simAssist(vehicle: string, on: boolean): Promise<unknown>
  /**
   * Hold-to-fire (05 §7.4): `on` at the press and again every 100 ms while held, `off` at the release. Only for
   * possessed simulated aircraft whose sticks are flowing. The arena package supplies the transport.
   */
  fire(vehicle: string, on: boolean): void
  /** D-pad up / down: the window's control feel preset changed */
  setFeel(feel: ControlFeel): void
}

export interface EngineDeps {
  now(): number
  gamepads(): readonly (GamepadLike | null)[]
  vehicles(): readonly Vehicle[]
  selected(): string | null
  focused(): boolean
  send(vehicle: string, f: StickFrame): void
  cancel(vehicle: string): void
  /** dead zone and expo, plus the v0.3 control preferences (missing fields take DEFAULT_CONTROL) */
  prefs(): ShapePrefs & Partial<ControlPrefs>
  /** learned controller profiles by device id (05 §6.3) */
  profiles?(): Readonly<Record<string, PadProfile>>
  act: EngineActions
}

export interface EnginePad {
  index: number
  id: string
  model: PadModel
  /** gamepad, transmitter or generic (05 §6.3) */
  kind: PadKind
  /** a learned profile maps this device */
  profiled: boolean
  connected: boolean
  vehicle: string | null
  /** raw axes (y up positive) of the last reading, for the live preview */
  raw: PadReading['raw'] | null
}

export interface EngineOutput {
  gateOpen: boolean
  pads: EnginePad[]
  holds: Record<string, HoldState>
  padLost: Record<string, { pad: number; since: number }>
  paused: boolean
  awaiting: Record<string, AwaitReason>
  /** new connection cards since the last tick */
  hints: HintCard[]
  /** frames computed this tick, by vehicle */
  frames: Map<string, { frame: StickFrame; centre: number; sending: boolean; source: SourceKey; hold: boolean }>
  /** aircraft a fire input is held for this tick (05 §7.4) */
  firing: string[]
}

interface Slot {
  index: number
  id: string
  browserIndex: number
  model: PadModel
  kind: PadKind
  profiled: boolean
  connected: boolean
  lostAt: number | null
  released: boolean
  reading: PadReading | null
  /** logical sticks of this tick (before shaping) and how to shape them */
  logical: Logical | null
  radial: boolean
  /** transmitter / profiled reading: switches and the absolute throttle */
  mapped: MappedReading | null
  prev: PadReading['pressed'] | null
  prevSwitch: Record<SwitchAction, boolean> | null
  holds: Record<HoldKind, HoldTimer>
  holdVehicle: Partial<Record<HoldKind, string | null>>
  stopLatched: boolean
  /** the mapping wizard runs on this device: it flies nothing (05 §6.3) */
  learning: boolean
}

interface Await {
  reason: AwaitReason
  /** pad raw axes or the window activity counter at the moment the wait began */
  base: PadReading['raw'] | number | null
}

function newHolds(): Record<HoldKind, HoldTimer> {
  return { options: new HoldTimer(HOLD_MS), arm: new HoldTimer(HOLD_MS), reset: new HoldTimer(HOLD_MS) }
}

export class InputEngine {
  private slots: Slot[] = []
  private overflowWarned = new Set<number>()
  private gateOpen = false
  private paused = false
  private lastTick: number | null = null

  // window source (keyboard + on-screen sticks)
  private held = new Set<FlightKey>()
  private kb = { roll: 0, pitch: 0, yaw: 0 }
  private throttleY = 0
  /** R / F held: when each was last integrated into throttleY. */
  private throttleKeys = new Map<FlightKey, number>()
  private screen = { left: { x: 0, active: false }, right: { x: 0, y: 0, active: false } }
  private activity = 0

  private awaits = new Map<string, Await>()
  private lostLink = new Set<string>()
  private known = new Map<string, SourceKey>()
  private sending = new Set<string>()
  private pending = new Map<SourceKey, { vehicle: string; until: number }>()
  private releasing = new Set<string>()
  private hintSeq = 0

  // WP-C (05 §6)
  /** R / F in hold modes: momentary stick y, slewed */
  private holdY = 0
  /** mode class of the aircraft the window source drives: 'hold' | 'manual' | null */
  private windowClass: 'hold' | 'manual' | null = null
  private continuity = new ThrottleContinuity()
  private fire = new FireTracker()
  private learningPads = new Set<number>()

  constructor(private deps: EngineDeps) {}

  // ------------------------------------------------------------------ external input

  /** First user interaction (02 §7.1): browsers expose controllers only after it. */
  openGate(): void {
    this.gateOpen = true
  }

  keyDown(k: FlightKey): void {
    if ((k === 'r' || k === 'f') && !this.throttleKeys.has(k)) this.throttleKeys.set(k, this.deps.now())
    this.held.add(k)
    this.activity++
  }

  keyUp(k: string): void {
    // R / F integrate by key time, not by tick count: a short tap between two slow ticks still moves the throttle
    if (k === 'r' || k === 'f') {
      if (!this.paused && this.windowClass !== 'hold') this.integrateThrottle(this.deps.now())
      this.throttleKeys.delete(k)
    }
    this.held.delete(k as FlightKey)
  }

  private integrateThrottle(now: number): void {
    for (const [k, since] of this.throttleKeys) {
      const dt = Math.max(0, (now - since) / 1000)
      this.throttleY = keyThrottle(this.throttleY, new Set([k]), dt)
      this.throttleKeys.set(k, now)
    }
  }

  /** Space (02 §3): roll, pitch and yaw back to centre; the throttle holds. */
  centre(): void {
    for (const k of ['w', 'a', 's', 'd', 'q', 'e'] as FlightKey[]) this.held.delete(k)
    this.kb = { roll: 0, pitch: 0, yaw: 0 }
    this.activity++
  }

  /** Z (05 §6.1): the throttle stick back to the hover point (hold modes: back to centre). */
  throttleToHover(): void {
    this.throttleY = 0
    this.holdY = 0
    this.activity++
  }

  /** H (05 §6.3): hover assist of the aircraft this window flies, as Triangle on a controller. */
  toggleAssist(): void {
    const v = this.deps.vehicles().find((x) => x.possession && x.controller === 'human' && isWindowSource(sourceOf(x.possession)))
    if (v) this.runAssist(v)
  }

  private runAssist(v: Vehicle): void {
    const a = assistAction(v)
    if (!a) return
    if (a.kind === 'assist') void this.deps.act.simAssist(v.id, a.on).catch(() => undefined)
    else void this.deps.act.mode(v.id, a.name).catch(() => undefined)
  }

  /** The mapping wizard (05 §6.3) holds this controller: its sticks, buttons and switches act on nothing. */
  setLearning(pad: number, on: boolean): void {
    if (on) this.learningPads.add(pad)
    else this.learningPads.delete(pad)
  }

  /** On-screen sticks: x, y in -1..1 (y up positive). The left y is the throttle and stays where it is left. */
  setScreen(side: 'left' | 'right', x: number, y: number, active: boolean): void {
    if (side === 'left') {
      this.screen.left = { x: active ? x : 0, active }
      if (active) this.throttleY = Math.max(-1, Math.min(1, y))
    } else {
      this.screen.right = active ? { x, y, active } : { x: 0, y: 0, active: false }
    }
    if (active) this.activity++
  }

  /** Current window-source throttle stick (for the on-screen left stick). */
  get windowThrottleY(): number {
    return this.throttleY
  }

  /** Is `vehicle` driven by the keyboard / on-screen source of this window? */
  windowDrives(vehicle: Vehicle | null): boolean {
    return Boolean(vehicle?.possession && isWindowSource(sourceOf(vehicle.possession)))
  }

  // ------------------------------------------------------------------ the tick

  tick(): EngineOutput {
    const d = this.deps
    const now = d.now()
    const dt = this.lastTick === null ? 0 : Math.min(0.1, (now - this.lastTick) / 1000)
    this.lastTick = now
    const vehicles = d.vehicles()
    const selected = d.selected()
    const prefs: ControlPrefs = { ...DEFAULT_CONTROL, ...d.prefs() }
    const feel = FEELS[prefs.feel] ?? FEELS.standard
    const hints: HintCard[] = []

    // focus (02 §5.3): blur stops every human stick at once; focus back waits for a stick movement
    const focused = d.focused()
    if (!focused && !this.paused) this.blur()
    if (focused && this.paused) this.paused = false

    this.expirePending(now)
    this.pollPads(now, vehicles, selected, prefs, hints)

    // keyboard axes slew toward the held-key targets; reach follows the control feel (05 §6.1)
    if (!this.paused) {
      const tg = keyTargets(this.held, feel.keys, feel.yaw)
      this.kb.roll = slew(this.kb.roll, tg.roll, KEY_SLEW_PER_S, dt)
      this.kb.pitch = slew(this.kb.pitch, tg.pitch, KEY_SLEW_PER_S, dt)
      this.kb.yaw = slew(this.kb.yaw, tg.yaw, KEY_SLEW_PER_S, dt)
      if (this.windowClass === 'hold') {
        // hold modes: R / F climb and sink while held and the stick springs back to centre (hold) on release
        const dir = (this.held.has('r') ? 1 : 0) - (this.held.has('f') ? 1 : 0)
        this.holdY = slew(this.holdY, dir * feel.keys, KEY_SLEW_PER_S, dt)
        for (const k of this.throttleKeys.keys()) this.throttleKeys.set(k, now)
      } else {
        this.integrateThrottle(now)
      }
    }

    const frames: EngineOutput['frames'] = new Map()
    const driven = new Set<string>()
    const padLost: EngineOutput['padLost'] = {}

    for (const v of vehicles) {
      if (!v.possession || v.controller !== 'human') continue
      const source = sourceOf(v.possession)
      driven.add(v.id)
      const centre = throttleCentre(v)
      const hold = isHoldClass(v.mode_class)
      const airborne = v.landed === 'air' || v.landed === 'takeoff' || (v.armed && v.landed !== 'ground')
      const padSlot = isWindowSource(source) ? undefined : this.slots.find((s) => s.index === Number(source.slice(4)))

      // a new possession (or a new source on it): seed the window throttle, start without a wait
      if (this.known.get(v.id) !== source) {
        this.known.set(v.id, source)
        this.awaits.delete(v.id)
        this.pending.delete(source)
        this.continuity.forget(v.id)
        // airborne: centre (= hover or hold) so the switch does not drop height; on the ground in a self-levelling
        // mode: throttle low, so the aircraft can be armed
        if (isWindowSource(source)) {
          this.throttleY = !airborne && v.mode_class === 'manual' ? -1 : 0
          this.holdY = 0
          this.windowClass = hold ? 'hold' : 'manual'
        }
        // H5 (05 §6.3): a transmitter's throttle does not spring back, so it must be brought to its start position
        if (padSlot?.kind === 'transmitter') this.awaits.set(v.id, { reason: 'throttle', base: null })
      }

      // the mode class of the window-flown aircraft changed: re-seat the throttle stick (05 §6.2)
      if (isWindowSource(source)) {
        const cls = hold ? 'hold' : 'manual'
        if (this.windowClass !== cls) {
          this.windowClass = cls
          this.holdY = 0
          if (cls === 'manual') this.throttleY = airborne ? 0 : -1
        }
      }

      let frame: StickFrame | null = null
      let base: Await['base'] = null
      let throttle01: number | null = null
      if (isWindowSource(source)) {
        const y = this.screen.left.active || !hold ? this.throttleY : this.holdY
        frame = {
          roll: this.screen.right.active ? this.screen.right.x * feel.tilt : this.kb.roll,
          pitch: this.screen.right.active ? this.screen.right.y * feel.tilt : this.kb.pitch,
          yaw: this.screen.left.active ? this.screen.left.x * feel.yaw : this.kb.yaw,
          throttle: throttleFromStick(y, centre, hold, prefs.throttleDetent && !this.screen.left.active),
        }
        base = this.activity
      } else {
        const pad = Number(source.slice(4))
        const slot = padSlot
        if (slot && slot.connected && slot.reading && slot.logical && !slot.learning) {
          frame = shapeLogical(slot.logical, centre, hold, prefs, slot.radial)
          base = slot.reading.raw
          if (slot.mapped) throttle01 = slot.mapped.throttle01
        } else if (slot && slot.learning) {
          frame = null
        } else if (this.gateOpen) {
          padLost[v.id] = { pad, since: slot?.lostAt ?? now }
        }
      }
      // continuity across a mode class or hover assist change (05 §6.2)
      if (frame) frame.throttle = this.continuity.apply(v.id, `${hold ? 'h' : 'm'}|${v.sim ? String(v.sim.hover_assist) : '-'}`, frame.throttle, now)

      // link (H3): lost stops frames; recovery waits for a stick movement
      const usable = linkUsable(v.link.state)
      if (!usable) this.lostLink.add(v.id)
      else if (this.lostLink.has(v.id)) {
        this.lostLink.delete(v.id)
        this.awaits.set(v.id, { reason: 'link', base })
      }

      // focus came back: wait for a movement
      if (this.paused) {
        const reason = this.awaits.get(v.id)?.reason
        if (reason !== 'link' && reason !== 'throttle') this.awaits.set(v.id, { reason: 'focus', base: null })
      }
      const w = this.awaits.get(v.id)
      if (w && w.reason === 'throttle') {
        if (frame && throttle01 !== null && throttleReady(throttle01, hold)) this.awaits.delete(v.id)
      } else if (w && !this.paused && frame) {
        if (w.base === null) w.base = base
        else if (resumed(w.base, base)) this.awaits.delete(v.id)
      }

      const send = Boolean(frame) && focused && !this.paused && usable && !this.awaits.has(v.id)
      const out = frame ? roundFrame(frame) : { roll: 0, pitch: 0, yaw: 0, throttle: centre }
      frames.set(v.id, { frame: out, centre, sending: send, source, hold })
      if (send) {
        d.send(v.id, out)
        this.sending.add(v.id)
      } else if (this.sending.has(v.id)) {
        d.cancel(v.id)
        this.sending.delete(v.id)
      }
    }

    // possessions that ended: stop and forget
    for (const id of [...this.known.keys()]) {
      if (driven.has(id)) continue
      this.known.delete(id)
      this.awaits.delete(id)
      this.lostLink.delete(id)
      this.releasing.delete(id)
      this.continuity.forget(id)
      if (this.sending.delete(id)) d.cancel(id)
    }
    if (![...driven].some((id) => isWindowSource(this.known.get(id) ?? 'pad:0'))) this.windowClass = null

    // hold-to-fire (05 §7.4): R1, X or a fire switch, for possessed simulated aircraft whose sticks flow
    const wanted = new Set<string>()
    for (const v of vehicles) {
      const f = frames.get(v.id)
      if (!f || !f.sending || v.kind !== 'sim') continue
      if (isWindowSource(f.source)) {
        if (this.held.has('x')) wanted.add(v.id)
        continue
      }
      const slot = this.slots.find((s) => s.index === Number(f.source.slice(4)))
      if (!slot || !slot.connected || slot.learning) continue
      // R1 alone: L1 + R1 stays the arm chord and never fires (WP-D binding)
      if ((slot.kind !== 'transmitter' && slot.reading?.pressed.r1 && !slot.reading.pressed.l1) || slot.mapped?.switches.fire) wanted.add(v.id)
    }
    this.fire.tick(wanted, now, (id, on) => d.act.fire(id, on))

    // a disconnected controller keeps its aircraft for RECONNECT_GRACE_MS, then releases it (02 §7.3)
    for (const s of this.slots) {
      if (s.connected || s.lostAt === null || s.released) continue
      if (now - s.lostAt < RECONNECT_GRACE_MS) continue
      s.released = true
      const v = possessedBy(vehicles, `pad:${s.index}`)
      if (v && !this.releasing.has(v.id)) {
        this.releasing.add(v.id)
        void d.act.release(v.id).catch(() => this.releasing.delete(v.id))
      }
    }
    this.slots = this.slots.filter((s) => s.connected || !s.released)

    const awaiting: Record<string, AwaitReason> = {}
    for (const [id, w] of this.awaits) if (driven.has(id)) awaiting[id] = w.reason

    return {
      gateOpen: this.gateOpen,
      pads: this.slots
        .map((s) => ({
          index: s.index, id: s.id, model: s.model, kind: s.kind, profiled: s.profiled, connected: s.connected,
          vehicle: possessedBy(vehicles, `pad:${s.index}`)?.id ?? null, raw: s.reading?.raw ?? null,
        }))
        .sort((a, b) => a.index - b.index),
      holds: this.holdStates(),
      padLost,
      paused: this.paused,
      awaiting,
      hints,
      frames,
      firing: [...wanted],
    }
  }

  /** Window lost focus: stop everything human now. */
  blur(): void {
    this.paused = true
    this.held.clear()
    this.throttleKeys.clear()
    this.kb = { roll: 0, pitch: 0, yaw: 0 }
    this.screen.right = { x: 0, y: 0, active: false }
    this.screen.left = { x: 0, active: false }
    for (const s of this.slots) for (const h of Object.values(s.holds)) h.release()
    for (const id of this.sending) this.deps.cancel(id)
    this.sending.clear()
    this.holdY = 0
    this.fire.releaseAll((id, on) => this.deps.act.fire(id, on))
  }

  /** Release everything this engine sends (unmount). */
  dispose(): void {
    for (const id of this.sending) this.deps.cancel(id)
    this.sending.clear()
    this.fire.releaseAll((id, on) => this.deps.act.fire(id, on))
  }

  // ------------------------------------------------------------------ gamepads

  private pollPads(now: number, vehicles: readonly Vehicle[], selected: string | null, prefs: ControlPrefs, hints: HintCard[]): void {
    let list: readonly (GamepadLike | null)[] = []
    try {
      list = this.deps.gamepads()
    } catch {
      list = []
    }
    const present = list.filter((p): p is GamepadLike => Boolean(p && p.connected))
    if (present.length && !this.gateOpen) this.gateOpen = true

    // disconnects
    for (const s of this.slots) {
      if (!s.connected) continue
      const still = present.find((p) => p.index === s.browserIndex && p.id === s.id)
      if (still) continue
      s.connected = false
      s.lostAt = now
      s.reading = null
      for (const h of Object.values(s.holds)) h.release()
      const v = possessedBy(vehicles, `pad:${s.index}`)
      if (v) {
        if (this.sending.delete(v.id)) this.deps.cancel(v.id)
        // a simulated aircraft hovers while it waits for the controller (02 §7.3); real ones follow their failsafe
        if (v.kind === 'sim' && v.armed && v.landed !== 'ground' && v.modes.includes('LOITER') && v.mode !== 'LOITER') {
          void this.deps.act.mode(v.id, 'LOITER').catch(() => undefined)
        }
      }
    }

    // connects and reconnects
    for (const p of present) {
      if (this.slots.some((s) => s.connected && s.browserIndex === p.index && s.id === p.id)) continue
      const back = this.slots.find((s) => !s.connected && !s.released && s.id === p.id)
      if (back) {
        back.connected = true
        back.lostAt = null
        back.browserIndex = p.index
        back.prev = null
        continue
      }
      const index = allocateIndex(this.slots.map((s) => s.index))
      if (index === null) {
        if (!this.overflowWarned.has(p.index)) {
          this.overflowWarned.add(p.index)
          this.deps.act.notice({ kind: 'maxPads' })
        }
        continue
      }
      const slot: Slot = {
        index, id: p.id, browserIndex: p.index, model: padModel(p), kind: padKind(p), profiled: false, connected: true,
        lostAt: null, released: false, reading: null, logical: null, radial: true, mapped: null, prev: null,
        prevSwitch: null, holds: newHolds(), holdVehicle: {}, stopLatched: false, learning: false,
      }
      this.slots.push(slot)
      hints.push(this.connectHint(slot, vehicles, now))
    }
    for (const idx of [...this.overflowWarned]) if (!present.some((p) => p.index === idx)) this.overflowWarned.delete(idx)

    // readings and buttons
    const profiles = this.deps.profiles?.() ?? {}
    for (const s of this.slots) {
      if (!s.connected) continue
      const p = present.find((g) => g.index === s.browserIndex)
      if (!p) continue
      s.learning = this.learningPads.has(s.index)
      const learned = s.kind !== 'gamepad' ? profiles[s.id] : undefined
      s.profiled = Boolean(learned)
      let r = readPad(p, prefs)
      if (s.kind === 'transmitter' || learned) {
        // through the device profile (a transmitter defaults to AETR); a transmitter has no gamepad buttons
        const m = readMapped(p, learned ?? defaultProfile(s.id, s.kind === 'transmitter' ? 'transmitter' : 'generic'))
        s.mapped = m
        s.logical = m.logical
        s.radial = false
        if (s.kind === 'transmitter') r = { ...r, pressed: noButtons(), l2: 0, r2: 0 }
        r = { ...r, raw: m.raw }
      } else {
        s.mapped = null
        s.logical = logicalSticks(r.raw, prefs.stickMode)
        s.radial = prefs.stickMode === 2
      }
      s.reading = r
      if (s.learning) {
        for (const h of Object.values(s.holds)) h.release()
      } else {
        this.buttons(s, r, now, vehicles, selected, prefs.feel)
        this.switches(s, now, vehicles)
      }
      s.prev = r.pressed
      s.prevSwitch = s.mapped?.switches ?? null
    }
  }

  /** Switch bindings of a transmitter or profiled controller (05 §6.3): assist, arm (sims), stop; fire is read in tick. */
  private switches(s: Slot, now: number, vehicles: readonly Vehicle[]): void {
    const sw = s.mapped?.switches
    if (!sw) return
    const d = this.deps
    const mine = possessedBy(vehicles, `pad:${s.index}`)
    const prev = s.prevSwitch
    // stop: a switch thrown to its stop position stops this aircraft at once, like L2 + R2 (also while unfocused)
    if (sw.stop && !(prev?.stop ?? false) && prev !== null && mine) d.act.stop(mine.id)
    if (this.paused || prev === null) return
    // hover assist follows the switch position, on each change
    if (sw.assist !== prev.assist && mine && mine.kind === 'sim' && mine.sim && mine.sim.hover_assist !== sw.assist) {
      void d.act.simAssist(mine.id, sw.assist).catch(() => undefined)
    }
    // arm: held 1 s, simulated aircraft only (as L1 + R1)
    if (mine && mine.kind === 'real') {
      if (sw.arm && !prev.arm) d.act.notice({ kind: 'realArm' })
    } else if (s.kind === 'transmitter' || sw.arm) {
      this.hold(s, 'arm', sw.arm && Boolean(mine), now, mine?.id ?? null, () => {
        if (mine) void d.act.arm(mine.id, !mine.armed).catch(() => undefined)
      })
    }
  }

  private connectHint(slot: Slot, vehicles: readonly Vehicle[], now: number): HintCard {
    const exclude = new Set([...this.pending.values()].map((p) => p.vehicle))
    const target = autoBindTarget(vehicles, exclude)
    let state: HintCard['state'] = 'noFree'
    let vehicle: string | null = null
    if (target) {
      state = 'flying'
      vehicle = target.id
      this.pending.set(`pad:${slot.index}`, { vehicle: target.id, until: now + PENDING_MS })
      void this.deps.act.possess(target.id, slot.index).catch(() => this.pending.delete(`pad:${slot.index}`))
    } else if (vehicles.some((v) => !v.possession)) {
      state = 'holdOptions'
    }
    return { key: `pad-${slot.index}-${++this.hintSeq}`, pad: slot.index, model: slot.model, state, vehicle, createdAt: now }
  }

  private buttons(s: Slot, r: PadReading, now: number, vehicles: readonly Vehicle[], selected: string | null, feel: ControlFeel): void {
    const d = this.deps
    const mine = possessedBy(vehicles, `pad:${s.index}`)
    const edge = (b: keyof PadReading['pressed']) => r.pressed[b] && !(s.prev?.[b] ?? false)

    // L2 + R2 (02 §7.5): instant stop of this controller's aircraft, works even while the window is unfocused
    if (isStopChord(r)) {
      if (!s.stopLatched && mine) d.act.stop(mine.id)
      s.stopLatched = true
    } else if (r.l2 < 0.5 && r.r2 < 0.5) {
      s.stopLatched = false
    }
    if (this.paused || s.prev === null) return

    // Options held 1 s: possess the selected aircraft or release this controller's one
    this.hold(s, 'options', r.pressed.options, now, optionsHoldVehicle(vehicles, s.index, selected), () => {
      const dec = optionsDecision(vehicles, s.index, selected)
      if (dec.action === 'release') void d.act.release(dec.vehicle).catch(() => undefined)
      else if (dec.action === 'possess') {
        this.pending.set(`pad:${s.index}`, { vehicle: dec.vehicle, until: now + PENDING_MS })
        void d.act.possess(dec.vehicle, s.index).catch(() => undefined)
      } else if (dec.reason === 'taken') d.act.notice({ kind: 'taken', vehicle: dec.vehicle, by: dec.by })
      else d.act.notice({ kind: 'noSelection' })
    })
    if (r.pressed.options && edge('options') && !optionsHoldVehicle(vehicles, s.index, selected)) {
      const dec = optionsDecision(vehicles, s.index, selected)
      if (dec.action === 'blocked') d.act.notice(dec.reason === 'taken' ? { kind: 'taken', vehicle: dec.vehicle, by: dec.by } : { kind: 'noSelection' })
    }

    // L1 + R1 held 1 s: arm / disarm, simulated aircraft only (H4); a transmitter arms through its switch instead
    const chord = r.pressed.l1 && r.pressed.r1
    const chordEdge = chord && !((s.prev?.l1 ?? false) && (s.prev?.r1 ?? false))
    const armSwitch = Boolean(s.mapped?.switches.arm)
    if (mine && mine.kind === 'real') {
      if (chordEdge) d.act.notice({ kind: 'realArm' })
      if (!armSwitch) this.hold(s, 'arm', false, now, null, () => undefined)
    } else if (!armSwitch && s.kind !== 'transmitter') {
      this.hold(s, 'arm', chord && Boolean(mine), now, mine?.id ?? null, () => {
        if (mine) void d.act.arm(mine.id, !mine.armed).catch(() => undefined)
      })
    }

    // Circle held 1 s: reset a disarmed simulated aircraft to its spawn point
    const resetTarget = (mine ?? vehicles.find((v) => v.id === selected) ?? null) as Vehicle | null
    const canReset = Boolean(resetTarget && resetTarget.kind === 'sim' && !resetTarget.armed)
    this.hold(s, 'reset', r.pressed.circle && canReset, now, canReset ? resetTarget!.id : null, () => {
      if (resetTarget) void d.act.resetSim(resetTarget.id).catch(() => undefined)
    })

    // single presses; Triangle is hover assist (05 §6.3): the sim's switch, or stabilise <-> altitude hold on a real one
    if (edge('triangle') && mine) this.runAssist(mine)
    if (edge('up')) d.act.setFeel(stepFeel(feel, 1))
    if (edge('down')) d.act.setFeel(stepFeel(feel, -1))
    if (edge('square')) {
      const target = mine?.id ?? selected
      if (target) d.act.course(target)
    }
    if (!mine && edge('left')) d.act.moveSelection(-1)
    if (!mine && edge('right')) d.act.moveSelection(1)
    if (edge('touchpad')) d.act.viewCycle()
  }

  private hold(s: Slot, kind: HoldKind, pressed: boolean, now: number, vehicle: string | null, fire: () => void): void {
    const h = s.holds[kind]
    if (!pressed || !vehicle) {
      h.release()
      s.holdVehicle[kind] = null
      return
    }
    if (s.holdVehicle[kind] && s.holdVehicle[kind] !== vehicle) h.release()
    h.press(now)
    s.holdVehicle[kind] = vehicle
    if (h.poll(now)) fire()
  }

  private holdStates(): Record<string, HoldState> {
    const out: Record<string, HoldState> = {}
    const now = this.deps.now()
    for (const s of this.slots) {
      for (const kind of ['options', 'arm', 'reset'] as HoldKind[]) {
        const h = s.holds[kind]
        const v = s.holdVehicle[kind]
        if (!v || !h.pressed || h.progress(now) >= 1) continue
        out[v] = { kind, pad: s.index, startedAt: now - h.progress(now) * h.durationMs, durationMs: h.durationMs }
      }
    }
    return out
  }

  private expirePending(now: number): void {
    for (const [k, p] of this.pending) if (now > p.until) this.pending.delete(k)
  }
}

/** All gamepad buttons released: a transmitter's buttons carry no gamepad meaning (its switches are bound instead). */
const NO_BUTTONS: PadReading['pressed'] = readPad({ id: '', index: 0, connected: true, mapping: '', axes: [], buttons: [] }, DEFAULT_CONTROL).pressed
const noButtons = (): PadReading['pressed'] => NO_BUTTONS

function resumed(base: Await['base'], cur: Await['base']): boolean {
  if (base === null || cur === null) return false
  if (typeof base === 'number' || typeof cur === 'number') return base !== cur
  return moved(base, cur) > RESUME_THRESHOLD
}

export function isFlightKey(k: string): k is FlightKey {
  return (FLIGHT_KEYS as readonly string[]).includes(k)
}
