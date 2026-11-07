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
  it('keeps the opening sequence setting (WP-A, 05 §2.1)', () => {
    expect(DEFAULT_PREFS.bootMode).toBe('short')
    expect(sanitizePrefs({ bootMode: 'minimal' }).bootMode).toBe('minimal')
    expect(sanitizePrefs({ bootMode: 'cinematic' }).bootMode).toBe('short')
  })
  it('formats metres and feet', () => {
    expect(formatLength(1.26, 'm')).toBe('1.26 m')
    expect(formatLength(1.26, 'ft')).toBe('4.1 ft')
    expect(formatLength(null, 'm')).toBeNull()
  })
})
