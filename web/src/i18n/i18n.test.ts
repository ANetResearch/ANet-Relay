// R-61: English and Chinese carry exactly the same keys and the same placeholders; no empty strings; no emoji.
import { describe, expect, it } from 'vitest'
import { en } from './en'
import { zh } from './zh'
import { format, translate } from './index'

const placeholders = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()
const EMOJI = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{1F000}-\u{1F2FF}]/u

describe('i18n', () => {
  it('has identical key sets', () => {
    expect(Object.keys(zh).sort()).toEqual(Object.keys(en).sort())
  })

  it('keeps placeholders in both languages', () => {
    for (const k of Object.keys(en) as (keyof typeof en)[]) {
      expect(placeholders(zh[k]), k).toEqual(placeholders(en[k]))
    }
  })

  it('has no empty strings, exclamation marks or emoji', () => {
    for (const [k, v] of [...Object.entries(en), ...Object.entries(zh)]) {
      expect(v.trim().length, k).toBeGreaterThan(0)
      expect(v, k).not.toMatch(/[!！]/)
      expect(EMOJI.test(v), k).toBe(false)
    }
  })

  it('formats placeholders', () => {
    expect(format('{a} to {b}', { a: 'Quad 03', b: 'harness-a' })).toBe('Quad 03 to harness-a')
    expect(format('{missing}', {})).toBe('{missing}')
    expect(translate('zh', 'relay.handback', { aircraft: 'Quad 03', agent: 'harness-a' })).toBe('Quad 03 已交还给 harness-a')
    expect(translate('en', 'relay.handback', { aircraft: 'Quad 03', agent: 'harness-a' })).toBe('Quad 03 handed back to harness-a')
  })
})
