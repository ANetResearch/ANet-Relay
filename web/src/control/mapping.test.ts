import { describe, expect, it } from 'vitest'
import {
  deadzone,
  expo,
  isStopChord,
  keyTargets,
  keyThrottle,
  padFrame,
  padModel,
  readPad,
  shapeAxis,
  slew,
  stickForThrottle,
  throttleCentre,
  throttleCurve,
  type GamepadLike,
} from './mapping'

const pad = (axes: number[], pressed: number[] = [], values: Record<number, number> = {}): GamepadLike => ({
  id: 'DualSense Wireless Controller (STANDARD GAMEPAD Vendor: 054c Product: 0ce6)',
  index: 0,
  connected: true,
  mapping: 'standard',
  axes,
  buttons: Array.from({ length: 18 }, (_, i) => ({ pressed: pressed.includes(i), value: values[i] ?? (pressed.includes(i) ? 1 : 0) })),
})

describe('dead zone', () => {
  it('zeroes inside and rescales outside', () => {
    expect(deadzone(0.05, 0.08)).toBe(0)
    expect(deadzone(-0.08, 0.08)).toBe(0)
    expect(deadzone(1, 0.08)).toBe(1)
    expect(deadzone(-1, 0.08)).toBe(-1)
    expect(deadzone(0.54, 0.08)).toBeCloseTo(0.5, 6)
  })
  it('is continuous at the edge and clamps junk', () => {
    expect(deadzone(0.0801, 0.08)).toBeLessThan(0.001)
    expect(deadzone(3, 0.1)).toBe(1)
    expect(deadzone(Number.NaN, 0.1)).toBe(0)
  })
})

describe('expo', () => {
  it('keeps the end points and the sign', () => {
    for (const e of [0, 0.3, 0.6]) {
      expect(expo(0, e)).toBe(0)
      expect(expo(1, e)).toBeCloseTo(1, 9)
      expect(expo(-1, e)).toBeCloseTo(-1, 9)
    }
    expect(expo(-0.5, 0.3)).toBeCloseTo(-expo(0.5, 0.3), 9)
  })
  it('softens the centre', () => {
    expect(expo(0.5, 0)).toBe(0.5)
    expect(expo(0.5, 0.3)).toBeCloseTo(0.7 * 0.5 + 0.3 * 0.125, 9)
    expect(expo(0.2, 0.6)).toBeLessThan(0.2)
  })
  it('shapeAxis applies the dead zone first', () => {
    expect(shapeAxis(0.07, { deadzone: 0.08, expo: 0.3 })).toBe(0)
    expect(shapeAxis(1, { deadzone: 0.08, expo: 0.3 })).toBeCloseTo(1, 9)
  })
})

describe('throttle curve', () => {
  it('is piecewise linear through 0, the centre and 1', () => {
    expect(throttleCurve(-1, 0.35)).toBe(0)
    expect(throttleCurve(0, 0.35)).toBe(0.35)
    expect(throttleCurve(1, 0.35)).toBe(1)
    expect(throttleCurve(-0.5, 0.35)).toBeCloseTo(0.175, 9)
    expect(throttleCurve(0.5, 0.35)).toBeCloseTo(0.675, 9)
  })
  it('round-trips with stickForThrottle', () => {
    for (const c of [0.2, 0.35, 0.5, 0.7]) for (const y of [-1, -0.3, 0, 0.4, 1]) expect(stickForThrottle(throttleCurve(y, c), c)).toBeCloseTo(y, 9)
  })
  it('uses the hover point in self-levelling modes and 0.5 in hold modes', () => {
    const cal = { signs: { roll: 1 as const, pitch: 1 as const, yaw: 1 as const }, hover: 0.35 }
    expect(throttleCentre({ mode_class: 'manual', calibration: cal })).toBe(0.35)
    expect(throttleCentre({ mode_class: 'auto', calibration: cal })).toBe(0.35)
    expect(throttleCentre({ mode_class: 'althold', calibration: cal })).toBe(0.5)
    expect(throttleCentre({ mode_class: 'position', calibration: cal })).toBe(0.5)
    expect(throttleCentre(null)).toBe(0.5)
  })
})

describe('DualSense mapping (Mode 2)', () => {
  const prefs = { deadzone: 0.08, expo: 0 }
  it('detects the model', () => {
    expect(padModel(pad([]))).toBe('dualsense')
    expect(padModel({ id: '054c-0df2-DualSense Edge', mapping: '' })).toBe('dualsense')
    expect(padModel({ id: 'Xbox Wireless Controller', mapping: 'standard' })).toBe('standard')
    expect(padModel({ id: 'Generic USB Joystick', mapping: '' })).toBe('unknown')
  })
  it('maps left stick to throttle and yaw, right stick to pitch and roll, y up positive', () => {
    const f = padFrame(readPad(pad([0.5, -1, -1, -1]), prefs), 0.5)
    expect(f.yaw).toBeGreaterThan(0)
    expect(f.throttle).toBe(1)
    expect(f.roll).toBe(-1)
    expect(f.pitch).toBe(1)
    const centred = padFrame(readPad(pad([0.03, 0.02, -0.05, 0.07]), prefs), 0.35)
    expect(centred).toEqual({ roll: 0, pitch: 0, yaw: 0, throttle: 0.35 })
    const down = padFrame(readPad(pad([0, 1, 0, 1]), prefs), 0.35)
    expect(down.throttle).toBe(0)
    expect(down.pitch).toBe(-1)
  })
  it('stop chord needs both triggers fully pressed', () => {
    expect(isStopChord(readPad(pad([0, 0, 0, 0], [6, 7]), prefs))).toBe(true)
    expect(isStopChord(readPad(pad([0, 0, 0, 0], [], { 6: 1, 7: 0.9 }), prefs))).toBe(false)
    expect(isStopChord(readPad(pad([0, 0, 0, 0], [6]), prefs))).toBe(false)
  })
})

describe('keyboard', () => {
  it('targets and slew', () => {
    expect(keyTargets(new Set(['w', 'd', 'e']))).toEqual({ roll: 0.5, pitch: 0.5, yaw: 0.6 })
    expect(keyTargets(new Set(['w', 's']))).toEqual({ roll: 0, pitch: 0, yaw: 0 })
    expect(slew(0, 0.5, 5, 0.02)).toBeCloseTo(0.1, 9)
    expect(slew(0.45, 0.5, 5, 0.02)).toBe(0.5)
  })
  it('R / F move the throttle and release holds it', () => {
    const up = keyThrottle(0, new Set(['r']), 0.5)
    expect(up).toBeCloseTo(0.4, 9)
    expect(keyThrottle(up, new Set(), 0.5)).toBe(up)
    expect(keyThrottle(0.9, new Set(['r']), 1)).toBe(1)
    expect(keyThrottle(-0.9, new Set(['f']), 1)).toBe(-1)
  })
})
