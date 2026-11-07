// The arena row of the driving HUD (05 §6.4, §7.5): hit points scaled to 0..100 and the magazine with its reload
// progress, nothing at all outside the arena.
import { describe, expect, it } from 'vitest'
import { arenaHudSlot } from './ArenaDrivingHud'

describe('arena driving HUD slot', () => {
  it('is empty outside the arena', () => {
    expect(arenaHudSlot(null)).toEqual({})
  })

  it('scales hit points and passes the magazine and reload progress', () => {
    const slot = arenaHudSlot({ hp: 60, maxHp: 100, down: false, ammo: { n: 0, max: 24, reloading: true, progress: 0.5 } })
    expect(slot.hp).toBe(60)
    expect(slot.ammo).toEqual({ n: 0, max: 24, reloading: true, progress: 0.5 })
    expect(arenaHudSlot({ hp: 10, maxHp: 50, down: false, ammo: { n: 7, max: 24, reloading: false, progress: 0 } }).hp).toBe(20)
  })
})
