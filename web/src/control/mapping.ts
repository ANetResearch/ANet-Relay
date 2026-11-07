// Input shaping and the DualSense map (02 §7.5, 03 §5.2). Pure functions, unit-tested in mapping.test.ts.
// Stick frame convention (03 §5.2): roll right +, pitch forward +, yaw clockwise +, each -1..1; throttle 0..1.
// Gamepad API axes are y-down, so stick "up" is a negative raw value.
import type { Vehicle } from '@/api'

export interface StickFrame {
  roll: number
  pitch: number
  yaw: number
  throttle: number
}

export interface ShapePrefs {
  /** 0.04..0.15, default 0.08 (02 §7.4) */
  deadzone: number
  /** 0..0.6, default 0.3 (02 §7.4) */
  expo: number
}

export const DEFAULT_PREFS: ShapePrefs = { deadzone: 0.08, expo: 0.3 }
export const DEADZONE_RANGE: [number, number] = [0.04, 0.15]
export const EXPO_RANGE: [number, number] = [0, 0.6]

export const clamp = (x: number, lo: number, hi: number) => (x < lo ? lo : x > hi ? hi : x)
const finite = (x: number) => (Number.isFinite(x) ? x : 0)

/** Per-axis dead zone with rescale, so output is continuous at the edge and still reaches +-1. */
export function deadzone(x: number, dz: number): number {
  const v = clamp(finite(x), -1, 1)
  const a = Math.abs(v)
  if (a <= dz) return 0
  return (Math.sign(v) * (a - dz)) / (1 - dz)
}

/** Classic RC expo: (1 - e) x + e x^3. Keeps 0 -> 0 and +-1 -> +-1, softens the centre. */
export function expo(x: number, e: number): number {
  const v = clamp(finite(x), -1, 1)
  const k = clamp(e, 0, 1)
  return (1 - k) * v + k * v * v * v
}

/** Dead zone then expo: the shaping applied to every analogue stick axis. */
export function shapeAxis(x: number, prefs: ShapePrefs): number {
  return expo(deadzone(x, prefs.deadzone), prefs.expo)
}

/**
 * Throttle curve (03 §5.2): stick y in [-1, 1] (up positive) -> throttle 0..1, piecewise linear through
 * (-1, 0), (0, centre), (+1, 1). The centre is the vehicle's hover point in self-levelling modes and 0.5 in
 * altitude / position hold, where the autopilot reads mid-stick as "hold".
 */
export function throttleCurve(y: number, centre: number): number {
  const v = clamp(finite(y), -1, 1)
  const c = clamp(centre, 0.05, 0.95)
  return v <= 0 ? c * (1 + v) : c + (1 - c) * v
}

/** Throttle centre for a vehicle in its current mode (02 §7.5 left stick row, §5.5 hover point). */
export function throttleCentre(v: Pick<Vehicle, 'mode_class' | 'calibration'> | null): number {
  if (!v) return 0.5
  if (v.mode_class === 'althold' || v.mode_class === 'position') return 0.5
  return clamp(v.calibration?.hover ?? 0.5, 0.2, 0.7)
}

/** Inverse of throttleCurve: the stick y that yields `throttle` (used to seed the keyboard and screen throttle). */
export function stickForThrottle(throttle: number, centre: number): number {
  const t = clamp(finite(throttle), 0, 1)
  const c = clamp(centre, 0.05, 0.95)
  return t <= c ? t / c - 1 : (t - c) / (1 - c)
}

// ---------------------------------------------------------------------------------------------- gamepads

/** W3C "standard" mapping indices; DualSense reports this layout in Chromium. */
export const BTN = {
  cross: 0,
  circle: 1,
  square: 2,
  triangle: 3,
  l1: 4,
  r1: 5,
  l2: 6,
  r2: 7,
  create: 8,
  options: 9,
  l3: 10,
  r3: 11,
  up: 12,
  down: 13,
  left: 14,
  right: 15,
  ps: 16,
  touchpad: 17,
} as const
export type ButtonName = keyof typeof BTN

export const AXIS = { lx: 0, ly: 1, rx: 2, ry: 3 } as const

/** Triggers count as "fully pressed" for the L2 + R2 stop above this value. */
export const TRIGGER_FULL = 0.95

export interface GamepadLike {
  id: string
  index: number
  connected: boolean
  mapping: string
  axes: readonly number[]
  buttons: readonly { pressed: boolean; value: number }[]
  timestamp?: number
}

export type PadModel = 'dualsense' | 'standard' | 'unknown'

/** DualSense (Sony 054c, product 0ce6 / 0df2 Edge) by name or USB ids, else standard / unknown by mapping. */
export function padModel(p: Pick<GamepadLike, 'id' | 'mapping'>): PadModel {
  const id = p.id.toLowerCase()
  if (id.includes('dualsense') || (id.includes('054c') && (id.includes('0ce6') || id.includes('0df2')))) return 'dualsense'
  return p.mapping === 'standard' ? 'standard' : 'unknown'
}

export interface PadReading {
  /** shaped axes, y up positive */
  lx: number
  ly: number
  rx: number
  ry: number
  /** raw axes (y up positive) for the live preview */
  raw: { lx: number; ly: number; rx: number; ry: number }
  pressed: Record<ButtonName, boolean>
  l2: number
  r2: number
}

function button(p: GamepadLike, i: number): { pressed: boolean; value: number } {
  const b = p.buttons[i]
  return b ? { pressed: Boolean(b.pressed), value: finite(b.value) } : { pressed: false, value: 0 }
}

export function readPad(p: GamepadLike, prefs: ShapePrefs): PadReading {
  const ax = (i: number) => clamp(finite(p.axes[i] ?? 0), -1, 1)
  const raw = { lx: ax(AXIS.lx), ly: -ax(AXIS.ly), rx: ax(AXIS.rx), ry: -ax(AXIS.ry) }
  const pressed = {} as Record<ButtonName, boolean>
  for (const [name, i] of Object.entries(BTN) as [ButtonName, number][]) pressed[name] = button(p, i).pressed
  const l2 = button(p, BTN.l2)
  const r2 = button(p, BTN.r2)
  return {
    lx: shapeAxis(raw.lx, prefs),
    ly: shapeAxis(raw.ly, prefs),
    rx: shapeAxis(raw.rx, prefs),
    ry: shapeAxis(raw.ry, prefs),
    raw,
    pressed,
    l2: Math.max(l2.value, l2.pressed ? 1 : 0),
    r2: Math.max(r2.value, r2.pressed ? 1 : 0),
  }
}

/** Mode 2 (02 §7.5): left stick throttle / yaw, right stick pitch / roll. */
export function padFrame(r: PadReading, centre: number): StickFrame {
  return { roll: r.rx, pitch: r.ry, yaw: r.lx, throttle: throttleCurve(r.ly, centre) }
}

/** L2 + R2 both pressed to the end (02 §7.5): instant stop, no hold. */
export function isStopChord(r: Pick<PadReading, 'l2' | 'r2'>): boolean {
  return r.l2 >= TRIGGER_FULL && r.r2 >= TRIGGER_FULL
}

/** Largest axis change between two readings; used for "move a stick to resume". */
export function moved(a: PadReading['raw'], b: PadReading['raw']): number {
  return Math.max(Math.abs(a.lx - b.lx), Math.abs(a.ly - b.ly), Math.abs(a.rx - b.rx), Math.abs(a.ry - b.ry))
}

// ---------------------------------------------------------------------------------------------- keyboard

/** Keyboard deflection for W A S D / Q E (fraction of full stick) and throttle slew while R / F are held. */
export const KEY_DEFLECTION = 0.5
export const KEY_YAW_DEFLECTION = 0.6
export const KEY_SLEW_PER_S = 5
export const KEY_THROTTLE_PER_S = 0.8

export type FlightKey = 'w' | 'a' | 's' | 'd' | 'q' | 'e' | 'r' | 'f'
export const FLIGHT_KEYS: readonly FlightKey[] = ['w', 'a', 's', 'd', 'q', 'e', 'r', 'f']

/** Target roll / pitch / yaw for the held keys (W forward, S back, A left, D right, Q / E yaw). */
export function keyTargets(held: ReadonlySet<string>): { roll: number; pitch: number; yaw: number } {
  const k = (c: string) => (held.has(c) ? 1 : 0)
  return {
    roll: (k('d') - k('a')) * KEY_DEFLECTION,
    pitch: (k('w') - k('s')) * KEY_DEFLECTION,
    yaw: (k('e') - k('q')) * KEY_YAW_DEFLECTION,
  }
}

/** Moves `cur` toward `target` by at most rate * dt. */
export function slew(cur: number, target: number, ratePerS: number, dtS: number): number {
  const step = ratePerS * Math.max(0, dtS)
  if (Math.abs(target - cur) <= step) return target
  return cur + Math.sign(target - cur) * step
}

/** R raises, F lowers the throttle stick; released keys hold the value (02 §3). */
export function keyThrottle(y: number, held: ReadonlySet<string>, dtS: number): number {
  const dir = (held.has('r') ? 1 : 0) - (held.has('f') ? 1 : 0)
  return clamp(y + dir * KEY_THROTTLE_PER_S * Math.max(0, dtS), -1, 1)
}

/** Round to 4 decimals so frames stay small on the wire. */
export function roundFrame(f: StickFrame): StickFrame {
  const r = (x: number) => Math.round(x * 1e4) / 1e4
  return { roll: r(clamp(f.roll, -1, 1)), pitch: r(clamp(f.pitch, -1, 1)), yaw: r(clamp(f.yaw, -1, 1)), throttle: r(clamp(f.throttle, 0, 1)) }
}
