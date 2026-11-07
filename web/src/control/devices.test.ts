import { describe, expect, it } from 'vitest'
import { calibrateAxis, defaultProfile, MAX_PROFILES, padKind, putProfile, readMapped, sanitizeProfiles } from './devices'

const dev = (axes: number[], buttons: boolean[] = []) => ({ axes, buttons: buttons.map((pressed) => ({ pressed, value: pressed ? 1 : 0 })) })

describe('device kind (05 §6.3)', () => {
  it('standard mapping is a gamepad', () => {
    expect(padKind({ id: 'RadioMaster TX16S Joystick', mapping: 'standard', axes: [0, 0, -1, 0] })).toBe('gamepad')
  })
  it('known radio names are transmitters, case-insensitive', () => {
    for (const id of ['EdgeTX RadioMaster Boxer Joystick', 'OpenTX Jumper T-Pro', 'FrSky Taranis X9D+', 'TBS Tango 2', 'BETAFPV LiteRadio 3', 'radiomaster zorro', 'RadioMaster Pocket', 'TX16S'])
      expect(padKind({ id, mapping: '', axes: [0, 0, 0, 0] })).toBe('transmitter')
  })
  it('four axes with one resting at an end stop is a transmitter; the rest is generic', () => {
    expect(padKind({ id: 'USB Joystick (Vendor: 1209 Product: 4f54)', mapping: '', axes: [0, 0, -1, 0, 0] })).toBe('transmitter')
    expect(padKind({ id: 'Generic USB Gamepad', mapping: '', axes: [0, 0.01, 0, -0.02] })).toBe('generic')
    expect(padKind({ id: 'Two Axis Stick', mapping: '', axes: [-1, 0] })).toBe('generic')
  })
})

describe('profiles', () => {
  it('a transmitter defaults to AETR with an absolute throttle', () => {
    const p = defaultProfile('tx', 'transmitter')
    expect([p.axes.roll.index, p.axes.pitch.index, p.axes.throttle.index, p.axes.yaw.index]).toEqual([0, 1, 2, 3])
    const low = readMapped(dev([0, 0, -1, 0]), p)
    expect(low.throttle01).toBe(0)
    expect(low.logical.thr).toBe(-1)
    const mid = readMapped(dev([0.5, -0.5, 0, 0.25]), p)
    expect(mid.throttle01).toBe(0.5)
    expect(mid.logical).toEqual({ roll: 0.5, pitch: -0.5, yaw: 0.25, thr: 0 })
  })
  it('a generic controller defaults to the standard stick axes, y up', () => {
    const p = defaultProfile('g', 'generic')
    const r = readMapped(dev([0.2, -1, 0.3, -0.5]), p)
    expect(r.logical).toEqual({ roll: 0.3, pitch: 0.5, yaw: 0.2, thr: 1 })
  })
  it('calibrates around a measured centre and end points', () => {
    const m = { index: 0, invert: false, min: -0.8, center: 0.1, max: 0.9 }
    expect(calibrateAxis(0.1, m, true)).toBe(0)
    expect(calibrateAxis(0.9, m, true)).toBe(1)
    expect(calibrateAxis(-0.8, m, true)).toBe(-1)
    expect(calibrateAxis(0.5, m, true)).toBeCloseTo(0.5, 9)
    expect(calibrateAxis(-0.8, { ...m, invert: true }, true)).toBe(1)
    expect(calibrateAxis(-0.8, m, false)).toBe(-1)
    expect(calibrateAxis(0.9, m, false)).toBe(1)
    expect(calibrateAxis(Number.NaN, m, false)).toBe(-1)
  })
  it('reads switch bindings on axes and buttons', () => {
    const p = {
      ...defaultProfile('tx', 'transmitter'),
      switches: [
        { action: 'assist' as const, source: { type: 'axis' as const, index: 4, above: true } },
        { action: 'stop' as const, source: { type: 'axis' as const, index: 5, above: false } },
        { action: 'fire' as const, source: { type: 'button' as const, index: 1 } },
      ],
    }
    const r = readMapped(dev([0, 0, -1, 0, 1, -1], [false, true]), p)
    expect(r.switches).toEqual({ assist: true, fire: true, arm: false, stop: true })
    expect(readMapped(dev([0, 0, -1, 0, -1, 1], [false, false]), p).switches).toEqual({ assist: false, fire: false, arm: false, stop: false })
  })
  it('keeps at most eight valid profiles, newest first', () => {
    let all = {}
    for (let i = 0; i < MAX_PROFILES + 2; i++) all = putProfile(all, { ...defaultProfile(`dev-${i}`, 'generic'), updatedAt: 1000 + i })
    const ids = Object.keys(all)
    expect(ids).toHaveLength(MAX_PROFILES)
    expect(ids).not.toContain('dev-0')
    expect(ids).toContain(`dev-${MAX_PROFILES + 1}`)
  })
  it('sanitises stored junk', () => {
    expect(sanitizeProfiles(null)).toEqual({})
    expect(sanitizeProfiles([1, 2])).toEqual({})
    const s = sanitizeProfiles({ x: { kind: 'transmitter', axes: { roll: { index: 99, min: 1, max: -1 } }, switches: [{ action: 'nope' }, { action: 'fire', source: { type: 'button', index: 3 } }] } })
    expect(s.x.kind).toBe('transmitter')
    expect(s.x.axes.roll.index).toBe(0)
    expect(s.x.axes.roll.min).toBe(-1)
    expect(s.x.switches).toEqual([{ action: 'fire', source: { type: 'button', index: 3 } }])
  })
})
