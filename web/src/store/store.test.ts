import { describe, expect, it } from 'vitest'
import { computeRedOwner, armedTone, criticalTone, damageTone } from './red'
import { share } from './share'

describe('single-red rule (02 §2.3)', () => {
  const crit = { id: 'a', level: 'critical' as const, acked: false }
  const warn = { id: 'b', level: 'warn' as const, acked: false }
  it('gives red to an unacknowledged critical alert first', () => {
    expect(computeRedOwner([crit], [{ armed: true }])).toBe('critical')
    expect(armedTone('critical')).toBe('strong')
    expect(criticalTone('critical')).toBe('red')
  })
  it('falls back to armed when the critical alert is acknowledged (backend or locally)', () => {
    expect(computeRedOwner([{ ...crit, acked: true }], [{ armed: true }])).toBe('armed')
    expect(computeRedOwner([crit], [{ armed: true }], new Set(['a']))).toBe('armed')
    expect(armedTone('armed')).toBe('red')
    // the arena hit flash on our own aircraft belongs to the armed semantics, never beside a critical alert
    expect(damageTone('armed')).toBe('red')
    expect(damageTone('critical')).toBe('strong')
    expect(damageTone('none')).toBe('strong')
    expect(criticalTone('armed')).toBe('strong')
  })
  it('has no red when nothing is armed and no critical alert is open', () => {
    expect(computeRedOwner([warn], [{ armed: false }])).toBe('none')
    expect(computeRedOwner(undefined, undefined)).toBe('none')
  })
})

describe('structural sharing', () => {
  it('keeps identity of unchanged branches', () => {
    const a = { t: 1, vehicles: [{ id: 'x', link: { state: 'ok' } }, { id: 'y', link: { state: 'ok' } }] }
    const b = { t: 2, vehicles: [{ id: 'x', link: { state: 'ok' } }, { id: 'y', link: { state: 'lost' } }] }
    const s = share(a, b)
    expect(s).not.toBe(a)
    expect(s.vehicles[0]).toBe(a.vehicles[0])
    expect(s.vehicles[1]).not.toBe(a.vehicles[1])
    expect(s.vehicles[1].link.state).toBe('lost')
    expect(share(a, structuredClone(a))).toBe(a)
  })
  it('handles added and removed keys', () => {
    const a: Record<string, unknown> = { x: 1 }
    expect(share(a, { x: 1, y: 2 })).toEqual({ x: 1, y: 2 })
    expect(share({ x: 1, y: 2 }, { x: 1 })).toEqual({ x: 1 })
  })
})
