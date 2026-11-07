// Transmitters and non-standard controllers (05 §6.3, WP-C): device kind detection, per-device mapping profiles
// (axes, direction, per-axis calibration, switch bindings) kept in settings.ui.padProfiles, the AETR default of a
// transmitter in USB joystick mode, and reading a device through its profile. Pure functions (devices.test.ts).
import { clamp, type GamepadLike } from './mapping'
import type { Logical } from './shaping'

/** `gamepad`: W3C standard mapping. `transmitter`: RC radio in USB joystick mode (throttle does not centre). */
export type PadKind = 'gamepad' | 'transmitter' | 'generic'

/** Radio and joystick-mode names seen in the Gamepad API id string (case-insensitive). */
const TRANSMITTER_NAMES =
  /edgetx|opentx|radiomaster|jumper|frsky|taranis|\btbs\b|tango|mambo|betafpv|lite ?radio|boxer|zorro|pocket|tx16s|tx12|t-?lite|t-?pro|horus|x9d|x-?lite|flysky|crossfire/i

/**
 * Kind of a connected device (05 §6.3): standard mapping is a gamepad; otherwise a known radio name, or four or more
 * axes of which one rests at an end stop (a throttle without a centring spring), is a transmitter; the rest generic.
 */
export function padKind(p: Pick<GamepadLike, 'id' | 'mapping' | 'axes'>): PadKind {
  if (p.mapping === 'standard') return 'gamepad'
  if (TRANSMITTER_NAMES.test(p.id)) return 'transmitter'
  const axes = p.axes ?? []
  if (axes.length >= 4 && axes.slice(0, 4).some((v) => Number.isFinite(v) && Math.abs(v) > 0.9)) return 'transmitter'
  return 'generic'
}

export type LogicalAxis = 'roll' | 'pitch' | 'throttle' | 'yaw'
export const LOGICAL_AXES: readonly LogicalAxis[] = ['roll', 'pitch', 'throttle', 'yaw']

/** One device axis: index, direction and calibration (raw values at the ends and the centre). */
export interface AxisMap {
  index: number
  invert: boolean
  min: number
  center: number
  max: number
}

export type SwitchAction = 'assist' | 'fire' | 'arm' | 'stop'
export const SWITCH_ACTIONS: readonly SwitchAction[] = ['assist', 'fire', 'arm', 'stop']

/** A switch is an axis past +-0.5 (two- and three-position switches, axes 4-7) or a button. */
export type SwitchSource = { type: 'axis'; index: number; above: boolean } | { type: 'button'; index: number }

export interface SwitchBinding {
  action: SwitchAction
  source: SwitchSource
}

export interface PadProfile {
  /** Gamepad API id string of the device */
  id: string
  kind: 'transmitter' | 'generic'
  axes: Record<LogicalAxis, AxisMap>
  switches: SwitchBinding[]
  /** ms since the epoch; the oldest profile is dropped beyond MAX_PROFILES */
  updatedAt: number
}

export const MAX_PROFILES = 8

const axis = (index: number, invert = false): AxisMap => ({ index, invert, min: -1, center: 0, max: 1 })

/**
 * Default mapping of a device without a learned profile. Transmitters: AETR (axis 0 roll, 1 pitch, 2 throttle,
 * 3 yaw), absolute throttle. Generic controllers: the standard stick axes (0 / 1 left, 2 / 3 right, y down).
 */
export function defaultProfile(id: string, kind: 'transmitter' | 'generic'): PadProfile {
  const axes =
    kind === 'transmitter'
      ? { roll: axis(0), pitch: axis(1), throttle: axis(2), yaw: axis(3) }
      : { roll: axis(2), pitch: axis(3, true), throttle: axis(1, true), yaw: axis(0) }
  return { id, kind, axes, switches: [], updatedAt: 0 }
}

/**
 * Raw axis value to -1..1 through its calibration. Centred axes map min..center..max piecewise; the throttle of a
 * transmitter (`centred` false) maps min..max linearly. Inversion applies after calibration.
 */
export function calibrateAxis(raw: number, m: AxisMap, centred: boolean): number {
  const v = Number.isFinite(raw) ? raw : centred ? m.center : m.min
  let out: number
  if (centred) {
    const lo = m.center - m.min
    const hi = m.max - m.center
    out = v >= m.center ? (hi > 1e-3 ? (v - m.center) / hi : 0) : lo > 1e-3 ? (v - m.center) / lo : 0
  } else {
    const span = m.max - m.min
    out = span > 1e-3 ? ((v - m.min) / span) * 2 - 1 : -1
  }
  out = clamp(out, -1, 1)
  return m.invert ? -out : out
}

export function switchActive(p: Pick<GamepadLike, 'axes' | 'buttons'>, s: SwitchSource): boolean {
  if (s.type === 'button') return Boolean(p.buttons[s.index]?.pressed)
  const v = p.axes[s.index]
  if (!Number.isFinite(v)) return false
  return s.above ? v > 0.5 : v < -0.5
}

export interface MappedReading {
  logical: Logical
  /** Mode 2 positions for the live preview and "move a stick to resume" */
  raw: { lx: number; ly: number; rx: number; ry: number }
  switches: Record<SwitchAction, boolean>
  /** throttle stick 0..1 (for H5) */
  throttle01: number
}

/** Reads a transmitter or generic device through its profile. */
export function readMapped(p: Pick<GamepadLike, 'axes' | 'buttons'>, profile: PadProfile): MappedReading {
  const centredThrottle = profile.kind !== 'transmitter'
  const get = (a: LogicalAxis) => calibrateAxis(p.axes[profile.axes[a].index] ?? Number.NaN, profile.axes[a], a !== 'throttle' || centredThrottle)
  const logical: Logical = { roll: get('roll'), pitch: get('pitch'), yaw: get('yaw'), thr: get('throttle') }
  const switches = { assist: false, fire: false, arm: false, stop: false } as Record<SwitchAction, boolean>
  for (const b of profile.switches) if (switchActive(p, b.source)) switches[b.action] = true
  return {
    logical,
    raw: { lx: logical.yaw, ly: logical.thr, rx: logical.roll, ry: logical.pitch },
    switches,
    throttle01: (logical.thr + 1) / 2,
  }
}

// ---------------------------------------------------------------------------------------------- persistence

const finiteIn = (v: unknown, lo: number, hi: number): v is number => typeof v === 'number' && Number.isFinite(v) && v >= lo && v <= hi

function sanitizeAxis(raw: unknown, fallback: AxisMap): AxisMap {
  const r = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const index = finiteIn(r.index, 0, 31) ? Math.round(r.index) : fallback.index
  const min = finiteIn(r.min, -1.5, 1.5) ? r.min : fallback.min
  const max = finiteIn(r.max, -1.5, 1.5) ? r.max : fallback.max
  const center = finiteIn(r.center, -1.5, 1.5) ? r.center : fallback.center
  if (!(min < max) || center < min || center > max) return { ...fallback, index, invert: r.invert === true }
  return { index, invert: r.invert === true, min, center, max }
}

function sanitizeSwitch(raw: unknown): SwitchBinding | null {
  const r = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  if (!SWITCH_ACTIONS.includes(r.action as SwitchAction)) return null
  const s = (r.source && typeof r.source === 'object' ? r.source : {}) as Record<string, unknown>
  if (!finiteIn(s.index, 0, 63)) return null
  if (s.type === 'button') return { action: r.action as SwitchAction, source: { type: 'button', index: Math.round(s.index) } }
  if (s.type === 'axis') return { action: r.action as SwitchAction, source: { type: 'axis', index: Math.round(s.index), above: s.above !== false } }
  return null
}

/** Accepts the stored `padProfiles` object and returns at most MAX_PROFILES valid profiles keyed by device id. */
export function sanitizeProfiles(raw: unknown): Record<string, PadProfile> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {}
  const out: PadProfile[] = []
  for (const [id, value] of Object.entries(raw as Record<string, unknown>)) {
    if (!id || id.length > 200 || !value || typeof value !== 'object') continue
    const r = value as Record<string, unknown>
    const kind = r.kind === 'transmitter' ? 'transmitter' : 'generic'
    const base = defaultProfile(id, kind)
    const axesRaw = (r.axes && typeof r.axes === 'object' ? r.axes : {}) as Record<string, unknown>
    const axes = {} as Record<LogicalAxis, AxisMap>
    for (const a of LOGICAL_AXES) axes[a] = sanitizeAxis(axesRaw[a], base.axes[a])
    const switches = (Array.isArray(r.switches) ? r.switches : []).map(sanitizeSwitch).filter((s): s is SwitchBinding => s !== null).slice(0, SWITCH_ACTIONS.length)
    out.push({ id, kind, axes, switches, updatedAt: finiteIn(r.updatedAt, 0, 1e15) ? r.updatedAt : 0 })
  }
  out.sort((a, b) => b.updatedAt - a.updatedAt)
  return Object.fromEntries(out.slice(0, MAX_PROFILES).map((p) => [p.id, p]))
}

/** Adds or replaces a profile, keeping the MAX_PROFILES most recent. */
export function putProfile(all: Readonly<Record<string, PadProfile>>, p: PadProfile): Record<string, PadProfile> {
  return sanitizeProfiles({ ...all, [p.id]: p })
}

/** Removes a device's profile (the device falls back to its default mapping). */
export function dropProfile(all: Readonly<Record<string, PadProfile>>, id: string): Record<string, PadProfile> {
  const next = { ...all }
  delete next[id]
  return next
}
