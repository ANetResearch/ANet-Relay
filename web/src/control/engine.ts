// Input engine (02 §5, §7; 03 §5.2, §6.1 H1-H4). One instance per window, ticked at 50 Hz by InputLayer.
// It reads the keyboard, the on-screen sticks and the Gamepad API, applies the binding rules and the stick shaping,
// and sends human sticks only for aircraft the fleet snapshot shows as possessed by the matching source.
// Stop rules: no frames while the window is unfocused, while the link is lost or for a disconnected controller;
// after focus returns or the link recovers, frames resume only once a stick moves. Everything that touches the
// outside world goes through EngineDeps so the rules are unit-tested (engine.test.ts).
import type { Vehicle } from '@/api'
import { HoldTimer } from '@/ui/relay/HoldProgress'
import {
  acceptsSticks,
  allocateIndex,
  autoBindTarget,
  HOLD_MS,
  isWindowSource,
  linkUsable,
  modeToggleTarget,
  optionsDecision,
  optionsHoldVehicle,
  possessedBy,
  RECONNECT_GRACE_MS,
  sourceOf,
  type SourceKey,
} from './bindings'
import {
  FLIGHT_KEYS,
  isStopChord,
  KEY_SLEW_PER_S,
  keyTargets,
  keyThrottle,
  moved,
  padFrame,
  padModel,
  readPad,
  roundFrame,
  slew,
  throttleCentre,
  throttleCurve,
  type FlightKey,
  type GamepadLike,
  type PadModel,
  type PadReading,
  type ShapePrefs,
  type StickFrame,
} from './mapping'
import type { AwaitReason, HintCard, HoldKind, HoldState } from './store'

/** Axis change that counts as "moved a stick" after a pause (raw units). */
export const RESUME_THRESHOLD = 0.2
const PENDING_MS = 1500
/** WP-D arena: a held trigger is refreshed this often; the backend releases it 300 ms after the last (05 §7.4). */
export const FIRE_REFRESH_MS = 100

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
  /** WP-D arena: hold-type fire input of a simulator (R1 on a pad, X on the keyboard); WP-C adds the same hook */
  fire?(vehicle: string, on: boolean): void
}

export interface EngineDeps {
  now(): number
  gamepads(): readonly (GamepadLike | null)[]
  vehicles(): readonly Vehicle[]
  selected(): string | null
  focused(): boolean
  send(vehicle: string, f: StickFrame): void
  cancel(vehicle: string): void
  prefs(): ShapePrefs
  act: EngineActions
}

export interface EnginePad {
  index: number
  id: string
  model: PadModel
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
  frames: Map<string, { frame: StickFrame; centre: number; sending: boolean; source: SourceKey }>
}

interface Slot {
  index: number
  id: string
  browserIndex: number
  model: PadModel
  connected: boolean
  lostAt: number | null
  released: boolean
  reading: PadReading | null
  prev: PadReading['pressed'] | null
  holds: Record<HoldKind, HoldTimer>
  holdVehicle: Partial<Record<HoldKind, string | null>>
  stopLatched: boolean
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
  // WP-D arena fire (05 §7.4): X held for the window source; vehicle -> last refresh
  private fireKeyHeld = false
  private firing = new Map<string, number>()

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

  /** WP-D arena: X pressed (true) for the window source; the key-up arrives through keyUp('x'). */
  fireKey(on: boolean): void {
    this.fireKeyHeld = on
    if (on) this.activity++
  }

  keyUp(k: string): void {
    if (k === 'x') this.fireKeyHeld = false
    // R / F integrate by key time, not by tick count: a short tap between two slow ticks still moves the throttle
    if (k === 'r' || k === 'f') {
      if (!this.paused) this.integrateThrottle(this.deps.now())
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
    const prefs = d.prefs()
    const hints: HintCard[] = []

    // focus (02 §5.3): blur stops every human stick at once; focus back waits for a stick movement
    const focused = d.focused()
    if (!focused && !this.paused) this.blur()
    if (focused && this.paused) this.paused = false

    this.expirePending(now)
    this.pollPads(now, vehicles, selected, prefs, hints)

    // keyboard axes slew toward the held-key targets
    if (!this.paused) {
      const tg = keyTargets(this.held)
      this.kb.roll = slew(this.kb.roll, tg.roll, KEY_SLEW_PER_S, dt)
      this.kb.pitch = slew(this.kb.pitch, tg.pitch, KEY_SLEW_PER_S, dt)
      this.kb.yaw = slew(this.kb.yaw, tg.yaw, KEY_SLEW_PER_S, dt)
      this.integrateThrottle(now)
    }

    const frames: EngineOutput['frames'] = new Map()
    const driven = new Set<string>()
    const padLost: EngineOutput['padLost'] = {}

    for (const v of vehicles) {
      if (!v.possession || v.controller !== 'human') continue
      const source = sourceOf(v.possession)
      driven.add(v.id)
      const centre = throttleCentre(v)

      // a new possession (or a new source on it): seed the window throttle, start without a wait
      if (this.known.get(v.id) !== source) {
        this.known.set(v.id, source)
        this.awaits.delete(v.id)
        this.pending.delete(source)
        // airborne: centre (= hover or hold) so the switch does not drop height; on the ground in a self-levelling
        // mode: throttle low, so the aircraft can be armed
        if (isWindowSource(source)) {
          const airborne = v.landed === 'air' || v.landed === 'takeoff' || (v.armed && v.landed !== 'ground')
          this.throttleY = !airborne && v.mode_class === 'manual' ? -1 : 0
        }
      }

      let frame: StickFrame | null = null
      let base: Await['base'] = null
      if (isWindowSource(source)) {
        frame = {
          roll: this.screen.right.active ? this.screen.right.x : this.kb.roll,
          pitch: this.screen.right.active ? this.screen.right.y : this.kb.pitch,
          yaw: this.screen.left.active ? this.screen.left.x : this.kb.yaw,
          throttle: throttleCurve(this.throttleY, centre),
        }
        base = this.activity
      } else {
        const pad = Number(source.slice(4))
        const slot = this.slots.find((s) => s.index === pad)
        if (slot && slot.connected && slot.reading) {
          frame = padFrame(slot.reading, centre)
          base = slot.reading.raw
        } else if (this.gateOpen) {
          padLost[v.id] = { pad, since: slot?.lostAt ?? now }
        }
      }

      // link (H3): lost stops frames; recovery waits for a stick movement
      const usable = linkUsable(v.link.state)
      if (!usable) this.lostLink.add(v.id)
      else if (this.lostLink.has(v.id)) {
        this.lostLink.delete(v.id)
        this.awaits.set(v.id, { reason: 'link', base })
      }

      // focus came back: wait for a movement
      if (this.paused) {
        if (!this.awaits.has(v.id) || this.awaits.get(v.id)!.reason !== 'link') this.awaits.set(v.id, { reason: 'focus', base: null })
      }
      const w = this.awaits.get(v.id)
      if (w && !this.paused && frame) {
        if (w.base === null) w.base = base
        else if (resumed(w.base, base)) this.awaits.delete(v.id)
      }

      const send = Boolean(frame) && focused && !this.paused && usable && !this.awaits.has(v.id)
      const out = frame ? roundFrame(frame) : { roll: 0, pitch: 0, yaw: 0, throttle: centre }
      frames.set(v.id, { frame: out, centre, sending: send, source })
      if (send) {
        d.send(v.id, out)
        this.sending.add(v.id)
      } else if (this.sending.has(v.id)) {
        d.cancel(v.id)
        this.sending.delete(v.id)
      }
    }

    this.fireTick(now, vehicles, frames)

    // possessions that ended: stop and forget
    for (const id of [...this.known.keys()]) {
      if (driven.has(id)) continue
      this.known.delete(id)
      this.awaits.delete(id)
      this.lostLink.delete(id)
      this.releasing.delete(id)
      if (this.sending.delete(id)) d.cancel(id)
    }

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
          index: s.index, id: s.id, model: s.model, connected: s.connected,
          vehicle: possessedBy(vehicles, `pad:${s.index}`)?.id ?? null, raw: s.reading?.raw ?? null,
        }))
        .sort((a, b) => a.index - b.index),
      holds: this.holdStates(),
      padLost,
      paused: this.paused,
      awaiting,
      hints,
      frames,
    }
  }

  // ------------------------------------------------------------------ WP-D arena fire (05 §7.4)
  // R1 alone on the controller that flies the aircraft (L1 + R1 stays the arm chord), X for the window source.
  // Only while frames are being sent (focus, link, no resume wait), simulators only; WP-C binds R1 / X in its
  // own mapping, so this block is reconciled with that one at merge.

  private fireTick(now: number, vehicles: readonly Vehicle[], frames: EngineOutput['frames']): void {
    const fire = this.deps.act.fire
    if (!fire) return
    for (const v of vehicles) {
      const f = frames.get(v.id)
      let held = false
      if (f && f.sending && v.kind === 'sim') {
        if (isWindowSource(f.source)) held = this.fireKeyHeld
        else {
          const r = this.slots.find((s) => s.index === Number(f.source.slice(4)))?.reading
          held = Boolean(r && r.pressed.r1 && !r.pressed.l1)
        }
      }
      const last = this.firing.get(v.id)
      if (held) {
        if (last === undefined || now - last >= FIRE_REFRESH_MS) {
          fire(v.id, true)
          this.firing.set(v.id, now)
        }
      } else if (last !== undefined) {
        fire(v.id, false)
        this.firing.delete(v.id)
      }
    }
    for (const id of [...this.firing.keys()]) {
      if (frames.has(id)) continue
      fire(id, false)
      this.firing.delete(id)
    }
  }

  private releaseFire(): void {
    this.fireKeyHeld = false
    for (const id of this.firing.keys()) this.deps.act.fire?.(id, false)
    this.firing.clear()
  }

  /** Window lost focus: stop everything human now. */
  blur(): void {
    this.releaseFire()
    this.paused = true
    this.held.clear()
    this.throttleKeys.clear()
    this.kb = { roll: 0, pitch: 0, yaw: 0 }
    this.screen.right = { x: 0, y: 0, active: false }
    this.screen.left = { x: 0, active: false }
    for (const s of this.slots) for (const h of Object.values(s.holds)) h.release()
    for (const id of this.sending) this.deps.cancel(id)
    this.sending.clear()
  }

  /** Release everything this engine sends (unmount). */
  dispose(): void {
    this.releaseFire()
    for (const id of this.sending) this.deps.cancel(id)
    this.sending.clear()
  }

  // ------------------------------------------------------------------ gamepads

  private pollPads(now: number, vehicles: readonly Vehicle[], selected: string | null, prefs: ShapePrefs, hints: HintCard[]): void {
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
        index, id: p.id, browserIndex: p.index, model: padModel(p), connected: true, lostAt: null, released: false,
        reading: null, prev: null, holds: newHolds(), holdVehicle: {}, stopLatched: false,
      }
      this.slots.push(slot)
      hints.push(this.connectHint(slot, vehicles, now))
    }
    for (const idx of [...this.overflowWarned]) if (!present.some((p) => p.index === idx)) this.overflowWarned.delete(idx)

    // readings and buttons
    for (const s of this.slots) {
      if (!s.connected) continue
      const p = present.find((g) => g.index === s.browserIndex)
      if (!p) continue
      const r = readPad(p, prefs)
      s.reading = r
      this.buttons(s, r, now, vehicles, selected)
      s.prev = r.pressed
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

  private buttons(s: Slot, r: PadReading, now: number, vehicles: readonly Vehicle[], selected: string | null): void {
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

    // L1 + R1 held 1 s: arm / disarm, simulated aircraft only (H4)
    const chord = r.pressed.l1 && r.pressed.r1
    const chordEdge = chord && !((s.prev?.l1 ?? false) && (s.prev?.r1 ?? false))
    if (mine && mine.kind === 'real') {
      if (chordEdge) d.act.notice({ kind: 'realArm' })
      this.hold(s, 'arm', false, now, null, () => undefined)
    } else {
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

    // single presses
    if (edge('triangle') && mine && acceptsSticks(mine.mode_class)) {
      const target = modeToggleTarget(mine)
      if (target) void d.act.mode(mine.id, target).catch(() => undefined)
    }
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

function resumed(base: Await['base'], cur: Await['base']): boolean {
  if (base === null || cur === null) return false
  if (typeof base === 'number' || typeof cur === 'number') return base !== cur
  return moved(base, cur) > RESUME_THRESHOLD
}

export function isFlightKey(k: string): k is FlightKey {
  return (FLIGHT_KEYS as readonly string[]).includes(k)
}
