import { describe, expect, it } from 'vitest'
import { en } from '@/i18n/en'
import { zh } from '@/i18n/zh'
import { en8, zh8 } from '@/fleet/copy'
import { enB, translateB, zhB } from './copy'

const EMOJI = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{1F000}-\u{1F2FF}]/u

describe('bench copy (WP-B)', () => {
  it('has identical key sets in English and Chinese', () => {
    expect(Object.keys(zhB).sort()).toEqual(Object.keys(enB).sort())
  })
  it('does not overlap the shared or the WP8 dictionaries', () => {
    const other = new Set([...Object.keys(en), ...Object.keys(zh), ...Object.keys(en8), ...Object.keys(zh8)])
    expect(Object.keys(enB).filter((k) => other.has(k))).toEqual([])
  })
  it('keeps the same placeholders in both languages', () => {
    const ph = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()
    for (const k of Object.keys(enB) as (keyof typeof enB)[]) expect(ph(zhB[k]), k).toEqual(ph(enB[k]))
  })
  it('has no empty strings, exclamation marks or emoji', () => {
    for (const [k, s] of [...Object.entries(enB), ...Object.entries(zhB)]) {
      expect(s.trim().length, k).toBeGreaterThan(0)
      expect(s, k).not.toMatch(/[!！]/)
      expect(EMOJI.test(s), k).toBe(false)
    }
  })
  it('covers every run, step, verdict, suggestion state and step kind the backend sends', () => {
    for (const s of ['running', 'waiting', 'passed', 'warned', 'failed', 'aborted']) expect(`bench.state.${s}` in enB, s).toBe(true)
    for (const s of ['pending', 'running', 'waiting', 'done', 'skipped', 'aborted']) expect(`bench.stepState.${s}` in enB, s).toBe(true)
    for (const s of ['pass', 'warn', 'fail', 'skipped']) expect(`bench.verdict.${s}` in enB, s).toBe(true)
    for (const s of ['proposed', 'applied', 'kept', 'failed', 'unavailable']) expect(`bench.sugg.${s}` in enB, s).toBe(true)
    for (const s of ['auto', 'operator', 'operator_device', 'device', 'flight']) expect(`bench.kind.${s}` in enB, s).toBe(true)
  })
  it('names the inspector tab in both languages and falls back to the other dictionaries', () => {
    expect(translateB('en', 'insp.tab.bench')).toBe('Bench')
    expect(translateB('zh', 'insp.tab.bench')).toBe('标定')
    expect(translateB('en', 'fleet.sortBy')).toBe('Sort by')
    expect(translateB('zh', 'bench.progress', { index: 3, total: 12 })).toBe('第 3 / 12 步')
  })
})
