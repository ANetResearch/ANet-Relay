import { describe, expect, it } from 'vitest'
import * as f from './format'

describe('value formatting (02 §4.4)', () => {
  it('formats HUD values like the design', () => {
    expect(f.angle((1.4 * Math.PI) / 180)).toBe('+01.4°')
    expect(f.angle((-0.8 * Math.PI) / 180)).toBe('−00.8°')
    expect(f.heading(184)).toBe('184°')
    expect(f.heading(-3)).toBe('357°')
    expect(f.metres(1.26)).toBe('1.26 m')
    expect(f.climb(0.2)).toBe('+0.2 m/s')
    expect(f.climb(-0.04)).toBe('+0.0 m/s')
    expect(f.volts(11.6)).toBe('11.6 V')
    expect(f.percent(62.4)).toBe('62 %')
  })
  it('returns null for unknown input so the UI shows a dash with a reason', () => {
    for (const fn of [f.angle, f.heading, f.metres, f.climb, f.volts, f.percent, f.duration, f.courseTime, f.split, f.clock]) {
      expect(fn(null)).toBeNull()
      expect(fn(Number.NaN)).toBeNull()
    }
  })
  it('formats times', () => {
    expect(f.duration(134)).toBe('02:14')
    expect(f.duration(3734)).toBe('1:02:14')
    expect(f.courseTime(12.48)).toBe('00:12.48')
    expect(f.courseTime(75.5)).toBe('01:15.50')
    expect(f.split(3.42)).toBe('+3.42')
  })
  it('maps the battery to five cells', () => {
    expect(f.batteryCells({ remaining_pct: 62, voltage: null, cells: null })).toBe(4)
    expect(f.batteryCells({ remaining_pct: 0, voltage: null, cells: null })).toBe(0)
    expect(f.batteryCells({ remaining_pct: 100, voltage: null, cells: null })).toBe(5)
    expect(f.batteryCells({ remaining_pct: null, voltage: 12.6, cells: 3 })).toBe(5)
    expect(f.batteryCells({ remaining_pct: null, voltage: null, cells: 3 })).toBeNull()
  })
  it('buckets ages', () => {
    expect(f.ageParts(100, 99.5)).toEqual({ unit: 'now', n: 0 })
    expect(f.ageParts(100, 88)).toEqual({ unit: 's', n: 12 })
    expect(f.ageParts(1000, 100)).toEqual({ unit: 'min', n: 15 })
  })
})
