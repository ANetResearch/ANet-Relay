// Mapping wizard for transmitters and generic controllers (05 §6.3, WP-C): "push roll / pitch / throttle / yaw"
// finds each axis and its direction, "move every stick" records the end points, "centre the sticks, throttle
// lowest" records the centres, then switches may be bound. A pure state machine fed with raw device snapshots
// (the component polls the Gamepad API); unit-tested in wizard.test.ts.
import type { AxisMap, LogicalAxis, PadProfile, SwitchAction, SwitchBinding, SwitchSource } from './devices'

export type WizardStep = 'roll' | 'pitch' | 'throttle' | 'yaw' | 'range' | 'centre' | 'switches'
export const WIZARD_STEPS: readonly WizardStep[] = ['roll', 'pitch', 'throttle', 'yaw', 'range', 'centre', 'switches']

/** Axis travel from the step's starting position that identifies the axis being pushed. */
export const DETECT_DELTA = 0.5
/** Travel an axis must show over the whole wizard before the range step completes. */
export const RANGE_SPAN = 1.0
/** Change that identifies a switch (axes 4-7 jump end to end). */
export const SWITCH_DELTA = 0.8

export interface Snapshot {
  axes: readonly number[]
  buttons: readonly boolean[]
}

export interface WizardState {
  step: WizardStep
  kind: 'transmitter' | 'generic'
  /** axes at the start of the current detection step */
  baseline: number[] | null
  found: Partial<Record<LogicalAxis, { index: number; invert: boolean }>>
  /** lowest and highest raw value seen per device axis */
  min: number[]
  max: number[]
  /** raw axes in the centre step (latest reading) */
  centre: number[] | null
  /** a switch binding in progress and the device state when it began */
  binding: { action: SwitchAction; base: Snapshot } | null
  switches: SwitchBinding[]
}

const STICK_STEPS: readonly LogicalAxis[] = ['roll', 'pitch', 'throttle', 'yaw']

export function startWizard(kind: 'transmitter' | 'generic'): WizardState {
  return { step: 'roll', kind, baseline: null, found: {}, min: [], max: [], centre: null, binding: null, switches: [] }
}

const finite = (v: number | undefined) => (typeof v === 'number' && Number.isFinite(v) ? v : 0)

function track(s: WizardState, snap: Snapshot): Pick<WizardState, 'min' | 'max'> {
  const min = [...s.min]
  const max = [...s.max]
  snap.axes.forEach((raw, i) => {
    const v = finite(raw)
    min[i] = min[i] === undefined ? v : Math.min(min[i], v)
    max[i] = max[i] === undefined ? v : Math.max(max[i], v)
  })
  return { min, max }
}

function assigned(s: WizardState): Set<number> {
  return new Set(Object.values(s.found).map((f) => f!.index))
}

/** Advances the wizard with one device reading (call every frame while it is open). */
export function feed(s: WizardState, snap: Snapshot): WizardState {
  const next: WizardState = { ...s, ...track(s, snap) }
  if ((STICK_STEPS as readonly string[]).includes(s.step)) {
    const axis = s.step as LogicalAxis
    if (!s.baseline) return { ...next, baseline: snap.axes.map(finite) }
    const taken = assigned(s)
    let best = -1
    let delta = 0
    snap.axes.forEach((raw, i) => {
      if (taken.has(i)) return
      const d = finite(raw) - (s.baseline![i] ?? 0)
      if (Math.abs(d) > Math.abs(delta)) {
        best = i
        delta = d
      }
    })
    if (best < 0 || Math.abs(delta) < DETECT_DELTA) return next
    const found = { ...s.found, [axis]: { index: best, invert: delta < 0 } }
    const order = STICK_STEPS.indexOf(axis)
    return { ...next, found, baseline: null, step: order < STICK_STEPS.length - 1 ? STICK_STEPS[order + 1] : 'range' }
  }
  if (s.step === 'centre') return { ...next, centre: snap.axes.map(finite) }
  if (s.step === 'switches' && s.binding) {
    const source = detectSwitch(s, snap)
    if (!source) return next
    const switches = [...s.switches.filter((b) => b.action !== s.binding!.action), { action: s.binding.action, source }]
    return { ...next, switches, binding: null }
  }
  return next
}

function detectSwitch(s: WizardState, snap: Snapshot): SwitchSource | null {
  const base = s.binding!.base
  const sticks = assigned(s)
  for (let i = 0; i < snap.buttons.length; i++) if (snap.buttons[i] && !base.buttons[i]) return { type: 'button', index: i }
  for (let i = 0; i < snap.axes.length; i++) {
    if (sticks.has(i)) continue
    const v = finite(snap.axes[i])
    if (Math.abs(v - finite(base.axes[i])) >= SWITCH_DELTA && Math.abs(v) > 0.5) return { type: 'axis', index: i, above: v > 0 }
  }
  return null
}

/** Whether Next is available on the current step. */
export function canAdvance(s: WizardState): boolean {
  if (s.step === 'range') return STICK_STEPS.every((a) => s.found[a] && (s.max[s.found[a]!.index] ?? 0) - (s.min[s.found[a]!.index] ?? 0) >= RANGE_SPAN)
  if (s.step === 'centre') return s.centre !== null
  return s.step === 'switches'
}

export function advance(s: WizardState): WizardState {
  if (!canAdvance(s)) return s
  if (s.step === 'range') return { ...s, step: 'centre', centre: null }
  if (s.step === 'centre') return { ...s, step: 'switches' }
  return s
}

/** One step back; going back over a stick step forgets that axis so it can be detected again. */
export function retreat(s: WizardState): WizardState {
  const i = WIZARD_STEPS.indexOf(s.step)
  if (i <= 0) return { ...s, baseline: null }
  const prev = WIZARD_STEPS[i - 1]
  const found = { ...s.found }
  if ((STICK_STEPS as readonly string[]).includes(prev)) delete found[prev as LogicalAxis]
  return { ...s, step: prev, found, baseline: null, binding: null }
}

export function beginBind(s: WizardState, action: SwitchAction, snap: Snapshot): WizardState {
  return { ...s, binding: { action, base: { axes: snap.axes.map(finite), buttons: [...snap.buttons] } } }
}

export function clearBind(s: WizardState, action: SwitchAction): WizardState {
  return { ...s, switches: s.switches.filter((b) => b.action !== action), binding: s.binding?.action === action ? null : s.binding }
}

/** The learned profile; null until the centre step has a reading. */
export function buildProfile(s: WizardState, id: string, now: number): PadProfile | null {
  if (!s.centre || !STICK_STEPS.every((a) => s.found[a])) return null
  const axes = {} as Record<LogicalAxis, AxisMap>
  for (const a of STICK_STEPS) {
    const f = s.found[a]!
    const min = s.min[f.index] ?? -1
    const max = s.max[f.index] ?? 1
    const nonCentred = a === 'throttle' && s.kind === 'transmitter'
    const raw = s.centre[f.index] ?? 0
    const center = nonCentred ? (min + max) / 2 : Math.min(max, Math.max(min, raw))
    axes[a] = { index: f.index, invert: f.invert, min, center, max }
  }
  return { id, kind: s.kind, axes, switches: s.switches, updatedAt: now }
}
