import { describe, expect, it } from 'vitest'
import { DEFAULT_PREFS, formatLength, sanitizePrefs } from './prefs'

describe('window preferences', () => {
  it('falls back to defaults for missing or invalid values', () => {
    expect(sanitizePrefs(null)).toEqual(DEFAULT_PREFS)
    expect(sanitizePrefs({ units: 'yd', startView: 'side', padDeadzone: 'x' })).toEqual(DEFAULT_PREFS)
  })
  it('clamps the controller ranges', () => {
    const p = sanitizePrefs({ padDeadzone: 0.5, padExpo: -1 })
    expect(p.padDeadzone).toBe(0.15)
    expect(p.padExpo).toBe(0)
  })
  it('formats metres and feet', () => {
    expect(formatLength(1.26, 'm')).toBe('1.26 m')
    expect(formatLength(1.26, 'ft')).toBe('4.1 ft')
    expect(formatLength(null, 'm')).toBeNull()
  })
})

describe('control preferences (05 §6.1, §6.3)', () => {
  it('defaults to Standard, Mode 2, no inversion, detent on, no profiles', () => {
    const p = sanitizePrefs({})
    expect(p.controlFeel).toBe('standard')
    expect(p.stickMode).toBe(2)
    expect(p.invert).toEqual({ roll: false, pitch: false, yaw: false, throttle: false })
    expect(p.throttleDetent).toBe(true)
    expect(p.padProfiles).toEqual({})
  })
  it('accepts valid values and rejects junk', () => {
    const p = sanitizePrefs({ controlFeel: 'sport', stickMode: 1, invert: { pitch: true, yaw: 'yes' }, throttleDetent: false })
    expect(p.controlFeel).toBe('sport')
    expect(p.stickMode).toBe(1)
    expect(p.invert).toEqual({ roll: false, pitch: true, yaw: false, throttle: false })
    expect(p.throttleDetent).toBe(false)
    expect(sanitizePrefs({ controlFeel: 'turbo', stickMode: 3 })).toMatchObject({ controlFeel: 'standard', stickMode: 2 })
  })
  it('keeps learned profiles through the round trip', () => {
    const profile = { id: 'radio', kind: 'transmitter', axes: {}, switches: [], updatedAt: 5 }
    const p = sanitizePrefs({ padProfiles: { radio: profile } })
    expect(p.padProfiles.radio.kind).toBe('transmitter')
    expect(p.padProfiles.radio.axes.throttle.index).toBe(2)
  })
})
