import { describe, expect, it } from 'vitest'
import { ARENA_DICTS, type ArenaKey } from './copy'

describe('arena copy', () => {
  it('has identical key sets in English and Chinese, none empty', () => {
    const en = Object.keys(ARENA_DICTS.en).sort()
    expect(Object.keys(ARENA_DICTS.zh).sort()).toEqual(en)
    for (const lang of ['en', 'zh'] as const) for (const k of en) expect(ARENA_DICTS[lang][k as ArenaKey].trim()).not.toBe('')
  })
  it('keeps placeholders aligned', () => {
    const ph = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()
    for (const k of Object.keys(ARENA_DICTS.en) as ArenaKey[]) expect(ph(ARENA_DICTS.zh[k])).toEqual(ph(ARENA_DICTS.en[k]))
  })
  it('uses no exclamation marks and states the first-use disclosure exactly', () => {
    for (const lang of ['en', 'zh'] as const) for (const s of Object.values(ARENA_DICTS[lang])) expect(s).not.toMatch(/[!！]/)
    expect(ARENA_DICTS.en['arena.disclosure']).toBe('Your simulated aircraft and callsign are shared with Relay terminals on this network.')
    expect(ARENA_DICTS.zh['arena.disclosure']).toBe('你的模拟机与呼号将共享给本网络中的 Relay 终端。')
  })
})
