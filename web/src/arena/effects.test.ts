import { afterEach, describe, expect, it } from 'vitest'
import type { RelayEvent } from '@/api'
import { initMotionTier } from '@/ui/motion/tier'
import { effectsOf, FxDedupe, HARD_CRASH_V, LIVE_EVENT_S, liveEvents, type Vec3 } from './effects'
import { addShake, cameraShake, clearShakes, SHAKE_MAX, SHAKE_RANGE, shakeOffset } from './shake'

let seq = 0
const ev = (kind: string, vehicle: string | null, data: Record<string, unknown>): RelayEvent =>
  ({ seq: ++seq, t: 0, kind, vehicle, session: null, text_en: kind, text_zh: kind, data }) as unknown as RelayEvent

const where: Record<string, Vec3> = { 'sim-1': [1, 0, 2], 'sim-2': [3, 0, 1], 'peer/sim-1': [5, 5, 3] }
const locate = (k: string) => where[k] ?? null

describe('event -> effect mapping (07 §5)', () => {
  it('explodes a shot-down aircraft where it is, local or remote, and falls back to the hit point', () => {
    const fx = effectsOf([
      ev('arena_down', 'sim-1', { side: 'taken', target: 'sim-1', point: [0, 0, 0] }),
      ev('arena_down', 'sim-2', { side: 'hit', target: 'peer/sim-1', point: [9, 9, 9] }),
      ev('arena_down', 'sim-2', { side: 'hit', target: 'peer/sim-9', point: [7, 7, 2] }),
    ], locate).fx
    expect(fx.map((f) => [f.kind, f.key, f.p])).toEqual([
      ['explosion', 'sim-1', [1, 0, 2]],
      ['explosion', 'peer/sim-1', [5, 5, 3]],
      ['explosion', 'peer/sim-9', [7, 7, 2]],
    ])
  })

  it('throws sparks at a collision point and explodes every aircraft it crashed', () => {
    const light = effectsOf([ev('collision', 'sim-1', { a: 'sim-1', b: 'sim-2', point: [2, 0, 1.5], v_rel: 1.1, crashed: [], kind: 'craft' })], locate)
    expect(light.fx.map((f) => [f.kind, f.p])).toEqual([['spark', [2, 0, 1.5]]])
    const ram = effectsOf([ev('collision', 'sim-1', { a: 'sim-1', b: 'peer/sim-1', point: [2, 0, 1.5], v_rel: 3.4, crashed: ['sim-1', 'peer/sim-1'], kind: 'craft' })], locate)
    expect(ram.fx.map((f) => [f.kind, f.key])).toEqual([['spark', null], ['explosion', 'sim-1'], ['explosion', 'peer/sim-1']])
  })

  it('raises dust on an ordinary ground crash, explodes a hard one or a ram, and flashes a grazed ring', () => {
    const out = effectsOf([
      ev('crashed', 'sim-1', { reason_code: 'ground', point: [1, 1, 0.3] }),
      ev('crashed', 'sim-2', {}),
      ev('crashed', 'sim-1', { reason_code: 'ground', point: [1, 1, 0], speed: HARD_CRASH_V + 1 }),
      ev('crashed', 'sim-2', { reason_code: 'collision', point: [3, 0, 1] }),
      ev('crashed', 'sim-2', { reason_code: 'shot_down', point: [3, 0, 0] }),
      ev('ring_touch', 'sim-1', { vehicle: 'sim-1', ring: 2, point: [0, 0, 1] }),
      ev('ring_touch', 'sim-1', { vehicle: 'sim-1', ring: 1 }),
      ev('ring_touch', 'sim-1', { vehicle: 'sim-1', ring: 0 }),
      ev('ring_touch', 'sim-1', { vehicle: 'sim-1', ring: -1 }),
    ], locate)
    expect(out.fx.map((f) => [f.kind, f.key, f.p[2]])).toEqual([
      ['dust', null, 0.05],
      ['dust', null, 0.05],
      ['explosion', 'sim-1', 0],
      ['explosion', 'sim-2', 1],
      ['dust', null, 0],
    ])
    // the backend's ring numbers are 1-based (the event text); the course layer's ring index is 0-based
    expect(out.rings).toEqual([{ vehicle: 'sim-1', ring: 1 }, { vehicle: 'sim-1', ring: 0 }])
  })

  it('makes effects of live events only: a backlog fetched after a reconnect is history', () => {
    const at = (t: number) => ({ ...ev('collision', 'sim-1', { point: [0, 0, 1], crashed: ['sim-1'] }), t })
    const batch = [at(100), at(1000 - LIVE_EVENT_S - 0.1), at(1000 - LIVE_EVENT_S + 0.1), at(1000)]
    expect(liveEvents(batch, 1000).map((e) => e.t)).toEqual([1000 - LIVE_EVENT_S + 0.1, 1000])
    expect(liveEvents([{ t: Number.NaN }], 1000)).toEqual([])
  })

  it('ignores other events and malformed data', () => {
    expect(effectsOf([ev('arena_hit', 'sim-1', { point: [0, 0, 0] }), ev('collision', null, { point: 'x', crashed: [1, null] })], () => null)).toEqual({ fx: [], rings: [] })
  })

  it('explodes one aircraft once per window, and contact effects always', () => {
    const d = new FxDedupe()
    expect(d.allow('sim-1', 0, 1.2)).toBe(true)
    expect(d.allow('sim-1', 0.5, 1.2)).toBe(false)
    expect(d.allow('sim-2', 0.5, 1.2)).toBe(true)
    expect(d.allow(null, 0.5, 1.2)).toBe(true)
    expect(d.allow('sim-1', 1.3, 1.2)).toBe(true)
  })
})

describe('chase camera shake (07 §5)', () => {
  afterEach(() => {
    initMotionTier('')
    clearShakes()
  })
  const src = { p: [0, 1, 0] as const, t0: 0, strength: 1, phase: 0.3 }

  it('stays within 3 cm, decays to nothing at its span and is weaker with distance', () => {
    let peak = 0
    for (let t = 0; t <= 0.12; t += 0.002) peak = Math.max(peak, Math.hypot(...shakeOffset([src, src, src], t, [0, 1.2, 0.5], 0.12)))
    expect(peak).toBeGreaterThan(0.005)
    expect(peak).toBeLessThanOrEqual(SHAKE_MAX + 1e-12)
    expect(shakeOffset([src], 0.12, [0, 1, 0.5], 0.12)).toEqual([0, 0, 0])
    expect(shakeOffset([src], 0.2, [0, 1, 0.5], 0.12)).toEqual([0, 0, 0])
    expect(shakeOffset([src], 0.03, [SHAKE_RANGE + 0.1, 1, 0], 0.12)).toEqual([0, 0, 0])
    const near = Math.hypot(...shakeOffset([src], 0.013, [0.5, 1, 0], 0.12))
    const far = Math.hypot(...shakeOffset([src], 0.013, [4, 1, 0], 0.12))
    expect(near).toBeGreaterThan(far)
    // deterministic
    expect(shakeOffset([src], 0.05, [1, 1, 1], 0.12)).toEqual(shakeOffset([src], 0.05, [1, 1, 1], 0.12))
  })

  it('never shakes under reduced or off motion', () => {
    for (const tier of ['reduced', 'off']) {
      initMotionTier(`?motion=${tier}`)
      addShake([0, 1, 0], 1, 0.2)
      expect([...cameraShake({ x: 0, y: 1, z: 0.5 })]).toEqual([0, 0, 0])
    }
  })
})
