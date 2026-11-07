import { describe, expect, it } from 'vitest'
import { en } from '@/i18n/en'
import { zh } from '@/i18n/zh'
import { en8, translate8, zh8 } from './copy'

describe('WP8 copy', () => {
  it('has identical key sets in English and Chinese', () => {
    expect(Object.keys(zh8).sort()).toEqual(Object.keys(en8).sort())
  })
  it('does not overlap the shared dictionaries', () => {
    const shared = new Set([...Object.keys(en), ...Object.keys(zh)])
    expect(Object.keys(en8).filter((k) => shared.has(k))).toEqual([])
  })
  it('keeps the same placeholders in both languages', () => {
    const ph = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()
    for (const k of Object.keys(en8) as (keyof typeof en8)[]) expect(ph(zh8[k]), k).toEqual(ph(en8[k]))
  })
  it('has no empty strings and no exclamation marks', () => {
    for (const s of [...Object.values(en8), ...Object.values(zh8)]) {
      expect(s.length).toBeGreaterThan(0)
      expect(s).not.toMatch(/[!！]/)
    }
  })
  it('falls back to the shared dictionaries', () => {
    expect(translate8('en', 'action.possess')).toBe('Possess')
    expect(translate8('zh', 'fleet.sortBy')).toBe('排序方式')
    expect(translate8('en', 'fleet.removed', { aircraft: 'Quad 03' })).toBe('Quad 03 removed from the fleet')
  })
})
