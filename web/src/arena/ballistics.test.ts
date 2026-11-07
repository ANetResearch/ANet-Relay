import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { advance, DT, flightAt, G, HIT_RADIUS, K, muzzle, sweep, TTL_STEPS } from './ballistics'

interface BallisticsVectors {
  constants: { dt: number; g: number; k: number; muzzle: number; ttl_steps: number; hit_radius: number }
  cases: { p0: number[]; v0: number[]; samples: { steps: number; p: number[]; v: number[] }[] }[]
  muzzle: { heading: number; pitch: number; aircraft_v: number[]; v: number[] }[]
  sweep: { p0: number[]; p1: number[]; c0: number[]; c1: number[]; t: number | null }[]
}

const vectors = JSON.parse(readFileSync(resolve(import.meta.dirname, '../../../docs/design/vectors/ballistics.json'), 'utf8')) as BallisticsVectors

function close(a: readonly number[], b: readonly number[], digits = 9): void {
  a.forEach((x, i) => expect(x).toBeCloseTo(b[i], digits))
}

describe('ballistics (shared vectors with the backend)', () => {
  it('uses the same constants', () => {
    const c = vectors.constants
    expect([c.dt, c.g, c.k, c.ttl_steps, c.hit_radius]).toEqual([DT, G, K, TTL_STEPS, HIT_RADIUS])
  })
  it('integrates to the same states', () => {
    for (const c of vectors.cases) {
      for (const s of c.samples) {
        const out = advance(c.p0, c.v0, s.steps)
        close(out.p, s.p)
        close(out.v, s.v)
      }
    }
  })
  it('computes the same muzzle velocity and sweep', () => {
    for (const m of vectors.muzzle) close(muzzle(m.heading, m.pitch, m.aircraft_v), m.v)
    for (const s of vectors.sweep) {
      const t = sweep(s.p0, s.p1, s.c0, s.c1, HIT_RADIUS)
      if (s.t === null) expect(t).toBeNull()
      else expect(t).toBeCloseTo(s.t, 9)
    }
  })
  it('flight state follows the step lattice, continues from a cache and expires', () => {
    const shot = { t0: 100, p: [0, 0, 5], v: [0, 35, 0] }
    const direct = flightAt(shot, 100 + 60 * DT + DT / 2)
    expect(direct.steps).toBe(60)
    close(direct.p, advance(shot.p, shot.v, 60).p, 12)
    const cached = flightAt(shot, 100 + 120 * DT + 1e-9, flightAt(shot, 100 + 30 * DT + 1e-9))
    close(cached.p, advance(shot.p, shot.v, 120).p, 12)
    expect(flightAt(shot, 100 + 3).done).toBe(true)
    expect(flightAt({ t0: 0, p: [0, 0, 0.01], v: [0, 0, -5] }, 0.1).done).toBe(true)
  })
})
