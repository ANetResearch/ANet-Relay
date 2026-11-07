import { describe, expect, it } from 'vitest'
import {
  CLASS_RAMP_MS,
  DEFAULT_CONTROL,
  detent,
  FEELS,
  logicalSticks,
  radialDeadzone,
  shapeLogical,
  stepFeel,
  ThrottleContinuity,
  throttleFromStick,
  type ControlPrefs,
} from './shaping'

const prefs = (over: Partial<ControlPrefs> = {}): ControlPrefs => ({ ...DEFAULT_CONTROL, expo: 0, ...over })

describe('feel presets (05 §6.1)', () => {
  it('match the contract table', () => {
    expect(FEELS.gentle).toEqual({ tilt: 0.5, yaw: 0.5, expo: 0.45, keys: 0.6 })
    expect(FEELS.standard).toEqual({ tilt: 0.75, yaw: 0.7, expo: 0.3, keys: 0.8 })
    expect(FEELS.sport).toEqual({ tilt: 1, yaw: 1, expo: 0.15, keys: 1 })
    expect(DEFAULT_CONTROL.feel).toBe('standard')
  })
  it('steps and clamps at the ends', () => {
    expect(stepFeel('standard', 1)).toBe('sport')
    expect(stepFeel('sport', 1)).toBe('sport')
    expect(stepFeel('standard', -1)).toBe('gentle')
    expect(stepFeel('gentle', -1)).toBe('gentle')
  })
  it('scales roll, pitch and yaw but never the throttle', () => {
    const l = { roll: 1, pitch: -1, yaw: 1, thr: 1 }
    for (const feel of ['gentle', 'standard', 'sport'] as const) {
      const f = shapeLogical(l, 0.5, false, prefs({ feel }), true)
      expect(Math.hypot(f.roll, f.pitch)).toBeCloseTo(FEELS[feel].tilt, 6)
      expect(f.yaw).toBeCloseTo(FEELS[feel].yaw, 6)
      expect(f.throttle).toBe(1)
    }
  })
})

describe('radial dead zone', () => {
  it('zeroes inside the circle and rescales the length outside', () => {
    expect(radialDeadzone(0.05, 0.05, 0.08)).toEqual([0, 0])
    const [x, y] = radialDeadzone(1, 0, 0.08)
    expect(x).toBeCloseTo(1, 9)
    expect(y).toBe(0)
  })
  it('keeps the direction of a diagonal push (no axis jumps in late)', () => {
    // per-axis dead zones would give x > 0 and y = 0 here
    const [x, y] = radialDeadzone(0.2, 0.07, 0.08)
    expect(x).toBeGreaterThan(0)
    expect(y).toBeGreaterThan(0)
    expect(y / x).toBeCloseTo(0.07 / 0.2, 6)
  })
  it('is continuous at the edge and clamps junk', () => {
    expect(Math.hypot(...radialDeadzone(0.0801, 0, 0.08))).toBeLessThan(0.001)
    expect(radialDeadzone(Number.NaN, 2, 0.1)).toEqual([0, 1])
  })
})

describe('throttle line (05 §6.2)', () => {
  it('is linear in self-levelling modes, centred on the hover point, with no expo', () => {
    expect(throttleFromStick(0, 0.35, false, true)).toBe(0.35)
    expect(throttleFromStick(0.5, 0.35, false, true)).toBeCloseTo(0.675, 9)
    expect(throttleFromStick(-0.5, 0.35, false, true)).toBeCloseTo(0.175, 9)
    const f = shapeLogical({ roll: 0, pitch: 0, yaw: 0, thr: 0.5 }, 0.5, false, prefs({ expo: 0.6 }), true)
    expect(f.throttle).toBeCloseTo(0.75, 9)
  })
  it('reads the centre band as hold in hold modes when the detent is on', () => {
    expect(detent(0.05)).toBe(0)
    expect(detent(-0.06)).toBe(0)
    expect(detent(1)).toBe(1)
    expect(detent(0.07)).toBeLessThan(0.02)
    expect(throttleFromStick(0.05, 0.35, true, true)).toBe(0.5)
    expect(throttleFromStick(0.05, 0.35, true, false)).toBeCloseTo(0.525, 9)
    expect(throttleFromStick(1, 0.35, true, true)).toBe(1)
  })
})

describe('stick modes and inversion', () => {
  const raw = { lx: 0.1, ly: 0.2, rx: 0.3, ry: 0.4 }
  it('Mode 2 and Mode 1', () => {
    expect(logicalSticks(raw, 2)).toEqual({ roll: 0.3, pitch: 0.4, yaw: 0.1, thr: 0.2 })
    expect(logicalSticks(raw, 1)).toEqual({ roll: 0.3, pitch: 0.2, yaw: 0.1, thr: 0.4 })
  })
  it('inverts each axis on its own', () => {
    const l = { roll: 1, pitch: 1, yaw: 1, thr: 1 }
    const f = shapeLogical(l, 0.5, false, prefs({ feel: 'sport', invert: { roll: true, pitch: false, yaw: true, throttle: true } }), false)
    expect(f).toEqual({ roll: -1, pitch: 1, yaw: -1, throttle: 0 })
  })
})

describe('throttle continuity across a mode class change', () => {
  it('ramps a jump larger than 0.05 over 300 ms', () => {
    const c = new ThrottleContinuity()
    expect(c.apply('a', 'm', 0.62, 0)).toBe(0.62)
    expect(c.apply('a', 'm', 0.62, 20)).toBe(0.62)
    const first = c.apply('a', 'h', 0.5, 40)
    expect(first).toBeCloseTo(0.62, 9)
    expect(c.ramping('a')).toBe(true)
    expect(c.apply('a', 'h', 0.5, 40 + CLASS_RAMP_MS / 2)).toBeCloseTo(0.56, 9)
    expect(c.apply('a', 'h', 0.5, 40 + CLASS_RAMP_MS)).toBe(0.5)
    expect(c.ramping('a')).toBe(false)
  })
  it('switches at once when the new mapping is close', () => {
    const c = new ThrottleContinuity()
    c.apply('a', 'm', 0.52, 0)
    expect(c.apply('a', 'h', 0.5, 20)).toBe(0.5)
    expect(c.ramping('a')).toBe(false)
  })
  it('follows the stick during the ramp target', () => {
    const c = new ThrottleContinuity()
    c.apply('a', 'm', 0.3, 0)
    c.apply('a', 'h', 0.5, 10)
    expect(c.apply('a', 'h', 0.7, 10 + CLASS_RAMP_MS / 2)).toBeCloseTo(0.5, 9)
  })
})
