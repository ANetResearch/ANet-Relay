import { describe, expect, it } from 'vitest'
import { CONTROL_DICTS } from './strings'

describe('WP7 copy', () => {
  it('has identical key sets in English and Chinese, none empty', () => {
    const en = Object.keys(CONTROL_DICTS.en).sort()
    const zh = Object.keys(CONTROL_DICTS.zh).sort()
    expect(zh).toEqual(en)
    for (const lang of ['en', 'zh'] as const) for (const k of en) expect(CONTROL_DICTS[lang][k as keyof typeof CONTROL_DICTS.en].trim()).not.toBe('')
  })
  it('keeps placeholders aligned', () => {
    const ph = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()
    for (const k of Object.keys(CONTROL_DICTS.en) as (keyof typeof CONTROL_DICTS.en)[]) expect(ph(CONTROL_DICTS.zh[k])).toEqual(ph(CONTROL_DICTS.en[k]))
  })
  it('uses no exclamation marks', () => {
    for (const lang of ['en', 'zh'] as const) for (const s of Object.values(CONTROL_DICTS[lang])) expect(s).not.toMatch(/[!！]/)
  })
})
