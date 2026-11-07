// Control feel (05 §6.1, §6.2; WP-C): feel presets, Mode 1 / Mode 2, per-axis invert, the radial dead zone of the
// attitude stick, the linear throttle with its optional hold detent, and the throttle continuity ramp across a mode
// class change. Pure functions and one small state holder, unit-tested in shaping.test.ts.
import { clamp, deadzone, expo, throttleCurve, type StickFrame } from './mapping'

export type ControlFeel = 'gentle' | 'standard' | 'sport'
export const FEEL_ORDER: readonly ControlFeel[] = ['gentle', 'standard', 'sport']

export interface FeelPreset {
  /** fraction of full stick for roll and pitch (and so of the autopilot's maximum tilt) */
  tilt: number
  /** fraction of full stick for yaw */
  yaw: number
  /** expo on roll, pitch and yaw; the throttle has none */
  expo: number
  /** keyboard reach of W A S D (and R / F in hold modes) */
  keys: number
}

/** 05 §6.1: stick scaling only; flight controller parameters are never changed. */
export const FEELS: Readonly<Record<ControlFeel, FeelPreset>> = {
  gentle: { tilt: 0.5, yaw: 0.5, expo: 0.45, keys: 0.6 },
  standard: { tilt: 0.75, yaw: 0.7, expo: 0.3, keys: 0.8 },
  sport: { tilt: 1, yaw: 1, expo: 0.15, keys: 1 },
}

/** One step softer (-1) or sharper (+1), clamped at the ends. */
export function stepFeel(f: ControlFeel, delta: 1 | -1): ControlFeel {
  const i = FEEL_ORDER.indexOf(f)
  return FEEL_ORDER[clamp((i < 0 ? 1 : i) + delta, 0, FEEL_ORDER.length - 1)]
}

/** Mode 2: throttle and yaw on the left stick. Mode 1: pitch and yaw on the left, throttle and roll on the right. */
export type StickMode = 1 | 2

export interface AxisInvert {
  roll: boolean
  pitch: boolean
  yaw: boolean
  throttle: boolean
}
export const NO_INVERT: AxisInvert = { roll: false, pitch: false, yaw: false, throttle: false }

export interface ControlPrefs {
  /** 0.04..0.15 */
  deadzone: number
  /** 0..0.6; the feel presets set it, the slider fine-tunes it */
  expo: number
  feel: ControlFeel
  stickMode: StickMode
  invert: AxisInvert
  /** in altitude / position hold, stick centre +-0.06 reads as exactly 0.5 */
  throttleDetent: boolean
}

export const DEFAULT_CONTROL: ControlPrefs = {
  deadzone: 0.08,
  expo: 0.3,
  feel: 'standard',
  stickMode: 2,
  invert: NO_INVERT,
  throttleDetent: true,
}

/** Logical stick positions before shaping: roll / pitch / yaw -1..1 and the throttle stick y -1..1 (up +). */
export interface Logical {
  roll: number
  pitch: number
  yaw: number
  thr: number
}

/** Physical sticks (y up positive) to logical axes for a stick mode. */
export function logicalSticks(raw: { lx: number; ly: number; rx: number; ry: number }, mode: StickMode): Logical {
  return mode === 1
    ? { roll: raw.rx, pitch: raw.ly, yaw: raw.lx, thr: raw.ry }
    : { roll: raw.rx, pitch: raw.ry, yaw: raw.lx, thr: raw.ly }
}

/**
 * Radial dead zone with rescale (05 §6.1): the vector length, not each axis, is compared with the dead zone, so a
 * diagonal push starts smoothly instead of one axis jumping in after the other. Output length reaches 1.
 */
export function radialDeadzone(x: number, y: number, dz: number): [number, number] {
  const fx = Number.isFinite(x) ? clamp(x, -1, 1) : 0
  const fy = Number.isFinite(y) ? clamp(y, -1, 1) : 0
  const mag = Math.hypot(fx, fy)
  if (mag <= dz) return [0, 0]
  const scaled = Math.min(1, (mag - dz) / (1 - dz))
  return [(fx / mag) * scaled, (fy / mag) * scaled]
}

export const DETENT = 0.06

/** Stick y with a flat centre band of +-width that reads as 0, rescaled outside so the ends still reach +-1. */
export function detent(y: number, width = DETENT): number {
  const v = clamp(Number.isFinite(y) ? y : 0, -1, 1)
  const a = Math.abs(v)
  if (a <= width) return 0
  return (Math.sign(v) * (a - width)) / (1 - width)
}

/**
 * Throttle from the throttle stick y (05 §6.2): linear, no expo. Self-levelling modes centre on the hover point;
 * hold modes centre on 0.5 (the autopilot holds height there), optionally with the detent.
 */
export function throttleFromStick(y: number, centre: number, hold: boolean, detentOn: boolean): number {
  if (!hold) return throttleCurve(y, centre)
  return throttleCurve(detentOn ? detent(y) : y, 0.5)
}

/** Shapes one logical stick set into a frame: dead zones, expo, feel scaling, invert and the throttle line. */
export function shapeLogical(l: Logical, centre: number, hold: boolean, p: ControlPrefs, radial: boolean): StickFrame {
  const f = FEELS[p.feel] ?? FEELS.standard
  const inv = p.invert ?? NO_INVERT
  let roll: number
  let pitch: number
  if (radial) [roll, pitch] = radialDeadzone(l.roll, l.pitch, p.deadzone)
  else [roll, pitch] = [deadzone(l.roll, p.deadzone), deadzone(l.pitch, p.deadzone)]
  const yaw = deadzone(l.yaw, p.deadzone)
  const sign = (on: boolean) => (on ? -1 : 1)
  const z = (x: number) => x || 0 // no negative zero on the wire
  return {
    roll: z(sign(inv.roll) * expo(roll, p.expo) * f.tilt),
    pitch: z(sign(inv.pitch) * expo(pitch, p.expo) * f.tilt),
    yaw: z(sign(inv.yaw) * expo(yaw, p.expo) * f.yaw),
    throttle: throttleFromStick(sign(inv.throttle) * clamp(Number.isFinite(l.thr) ? l.thr : 0, -1, 1), centre, hold, p.throttleDetent),
  }
}

/** Mode classes whose throttle centre means "hold" (03 §5.4). */
export function isHoldClass(modeClass: string | null | undefined): boolean {
  return modeClass === 'althold' || modeClass === 'position'
}

// ---------------------------------------------------------------------------------------------- continuity

export const CLASS_RAMP_MS = 300
export const CLASS_JUMP = 0.05

interface ThrottleTrack {
  key: string
  out: number
  ramp: { from: number; start: number } | null
}

/**
 * Throttle continuity across a mapping change (05 §6.2): when the mode class (or hover assist) changes and the new
 * mapping would move the throttle by more than 0.05 at once, the output slides to it over 300 ms instead.
 */
export class ThrottleContinuity {
  private tracks = new Map<string, ThrottleTrack>()

  /** `key` names the mapping (mode class, hover assist, centre); `target` is the throttle under it now. */
  apply(id: string, key: string, target: number, now: number): number {
    const t = this.tracks.get(id)
    if (!t) {
      this.tracks.set(id, { key, out: target, ramp: null })
      return target
    }
    if (t.key !== key) {
      t.key = key
      t.ramp = Math.abs(target - t.out) > CLASS_JUMP ? { from: t.out, start: now } : null
    }
    let out = target
    if (t.ramp) {
      const k = (now - t.ramp.start) / CLASS_RAMP_MS
      if (k >= 1) t.ramp = null
      else out = t.ramp.from + (target - t.ramp.from) * Math.max(0, k)
    }
    t.out = out
    return out
  }

  ramping(id: string): boolean {
    return Boolean(this.tracks.get(id)?.ramp)
  }

  forget(id: string): void {
    this.tracks.delete(id)
  }
}
