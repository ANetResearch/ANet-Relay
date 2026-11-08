import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import { burstLife, burstVoxels, DOWN_SHARDS, DUST_CHUNKS, DUST_RING, EXPLOSION_DUST, EXPLOSION_SHARDS, floorSize, HIT_SHARDS, seedOf, SPARKS, type Burst } from './voxelFx'

const hit: Burst = { p: [1, 2, 3], t0: 0, seed: seedOf('s1'), kind: 'hit' }

describe('voxel hit bursts (06 §11)', () => {
  it('opens with a white core, then shards only, and ends at its life', () => {
    const first = burstVoxels(hit, 0.01, 0.08, 0.65)
    expect(first[0].tone).toBe('core')
    expect(first).toHaveLength(1 + HIT_SHARDS)
    const later = burstVoxels(hit, 0.3, 0.08, 0.65)
    expect(later).toHaveLength(HIT_SHARDS)
    expect(later.some((c) => c.tone === 'red') && later.some((c) => c.tone === 'white')).toBe(true)
    expect(burstVoxels(hit, 0.66, 0.08, 0.65)).toHaveLength(0)
    expect(burstVoxels(hit, -0.01, 0.08, 0.65)).toHaveLength(0)
  })

  it('is a pure function of its age, so a replay can be scrubbed', () => {
    const a = burstVoxels(hit, 0.2, 0.08, 0.65).map((c) => ({ ...c }))
    burstVoxels(hit, 0.5, 0.08, 0.65)
    expect(burstVoxels(hit, 0.2, 0.08, 0.65)).toEqual(a)
    const other = burstVoxels({ ...hit, seed: seedOf('s2') }, 0.2, 0.08, 0.65)
    expect(other).not.toEqual(a)
  })

  it('flies the shards outwards and keeps them above the ground', () => {
    const near = burstVoxels(hit, 0.05, 0.08, 0.65).filter((c) => c.tone !== 'core')
    const far = burstVoxels(hit, 0.4, 0.08, 0.65)
    const spread = (cs: { x: number; z: number }[]) => Math.max(...cs.map((c) => Math.hypot(c.x - 1, c.z - 3)))
    expect(spread(far)).toBeGreaterThan(spread(near))
    expect(spread(far)).toBeGreaterThan(0.5)
    const low = burstVoxels({ ...hit, p: [0, 0.02, 0] }, 0.6, 0.08, 0.65)
    expect(low.every((c) => c.y > 0)).toBe(true)
  })

  it('throws a larger grey and red burst on a shoot-down, and one still cube under reduced motion', () => {
    const down = burstVoxels({ ...hit, kind: 'down' }, 0.3, 0.08, 1)
    expect(down).toHaveLength(DOWN_SHARDS)
    expect(down.some((c) => c.tone === 'grey')).toBe(true)
    const still = burstVoxels({ ...hit, kind: 'still' }, 0.3, 0.08, 0.65)
    expect(still).toEqual([{ x: 1, y: 2, z: 3, size: 0.06, tone: 'core', glow: 1 }])
  })
})

describe('screen-size floor', () => {
  it('keeps a far cube at its minimum pixels and a near cube at its true size', () => {
    const cam = new THREE.PerspectiveCamera(50, 1.6, 0.01, 1000)
    cam.position.set(0, 0, 0)
    const near = floorSize(new THREE.Vector3(0, 0, -0.5), 0.03, cam, 900, 4)
    expect(near).toBe(0.03)
    const far = floorSize(new THREE.Vector3(0, 0, -40), 0.03, cam, 900, 4)
    const px = (far / (2 * 40 * Math.tan(THREE.MathUtils.degToRad(25)))) * 900
    expect(px).toBeCloseTo(4, 6)
  })
})

describe('v0.5 explosions, sparks and dust (07 §5)', () => {
  const at = (kind: Burst['kind'], p: [number, number, number] = [0, 2, 0]): Burst => ({ p, t0: 0, seed: seedOf(`x:${kind}`), kind })
  const LIFE = 1.2
  const CORE = 0.12

  it('swells a white-hot core for its first 120 ms, then throws 60 cooling voxels', () => {
    const early = burstVoxels(at('explosion'), 0.03, CORE, LIFE)
    expect(early.filter((c) => c.tone === 'core')).toHaveLength(1)
    const mid = burstVoxels(at('explosion'), 0.1, CORE, LIFE)
    expect(mid.find((c) => c.tone === 'core')!.size).toBeGreaterThan(early.find((c) => c.tone === 'core')!.size)
    const shards = (age: number) => burstVoxels(at('explosion'), age, CORE, LIFE).filter((c) => c.tone === 'heat')
    expect(shards(0.03)).toHaveLength(0)
    expect(shards(0.2)).toHaveLength(EXPLOSION_SHARDS)
    expect(burstVoxels(at('explosion'), 0.3, CORE, LIFE).some((c) => c.tone === 'core')).toBe(false)
    // white -> r400 -> r600 -> g500: the mean heat rises with age
    const heat = (age: number) => shards(age).reduce((s, c) => s + (c.heat ?? 0), 0) / EXPLOSION_SHARDS
    expect(heat(0.1)).toBeLessThan(0.3)
    expect(heat(0.5)).toBeGreaterThan(heat(0.2))
    expect(heat(1.0)).toBeGreaterThan(0.9)
  })

  it('lets the voxels fall and settle on the ground, then fade out by the end of its life', () => {
    const late = burstVoxels(at('explosion', [0, 0.4, 0]), 1.0, CORE, LIFE).filter((c) => c.tone === 'heat')
    // most shards have landed: their centre is half their edge above the ground
    expect(late.filter((c) => Math.abs(c.y - c.size / 2) < 1e-9).length).toBeGreaterThan(EXPLOSION_SHARDS / 2)
    expect(late.every((c) => c.y >= c.size / 2 - 1e-9)).toBe(true)
    const end = burstVoxels(at('explosion'), LIFE, CORE, LIFE)
    expect(end.every((c) => c.size < 1e-6)).toBe(true)
    expect(burstVoxels(at('explosion'), LIFE + 0.01, CORE, LIFE)).toHaveLength(0)
  })

  it('raises a grey dust ring along the ground only below 1 m', () => {
    const low = burstVoxels(at('explosion', [0, 0.5, 0]), 0.4, CORE, LIFE).filter((c) => c.tone === 'dust' || c.tone === 'dustDark')
    expect(low).toHaveLength(EXPLOSION_DUST)
    expect(low.every((c) => c.y < 0.2)).toBe(true)
    expect(Math.min(...low.map((c) => Math.hypot(c.x, c.z)))).toBeGreaterThan(0.3)
    const high = burstVoxels(at('explosion', [0, 2.5, 0]), 0.4, CORE, LIFE).filter((c) => c.tone === 'dust' || c.tone === 'dustDark')
    expect(high).toHaveLength(0)
  })

  it('throws 16 white and red sparks and a brief white shock cube on a collision', () => {
    const first = burstVoxels(at('spark'), 0.02, 0.08, 0.65)
    expect(first[0].tone).toBe('core')
    expect(first).toHaveLength(1 + SPARKS)
    const later = burstVoxels(at('spark'), 0.2, 0.08, 0.65)
    expect(later).toHaveLength(SPARKS)
    expect(later.some((c) => c.tone === 'white') && later.some((c) => c.tone === 'red' || c.tone === 'deep')).toBe(true)
    expect(burstVoxels(at('spark'), 0.66, 0.08, 0.65)).toHaveLength(0)
  })

  it('raises a dust ring and a few grey chunks on an ordinary ground crash', () => {
    const d = burstVoxels(at('dust', [0, 0.05, 0]), 0.3, 0.08, LIFE)
    expect(d).toHaveLength(DUST_RING + DUST_CHUNKS)
    expect(d.some((c) => c.tone === 'grey')).toBe(true)
    expect(d.every((c) => c.tone !== 'red' && c.tone !== 'heat' && c.tone !== 'core')).toBe(true)
  })

  it('is deterministic per seed and age, so the replay can be scrubbed', () => {
    for (const kind of ['explosion', 'spark', 'dust'] as const) {
      const a = burstVoxels(at(kind, [0, 0.6, 0]), 0.35, CORE, LIFE).map((c) => ({ ...c }))
      burstVoxels(at(kind, [0, 0.6, 0]), 0.9, CORE, LIFE)
      expect(burstVoxels(at(kind, [0, 0.6, 0]), 0.35, CORE, LIFE)).toEqual(a)
      expect(burstVoxels({ ...at(kind, [0, 0.6, 0]), seed: 99 }, 0.35, CORE, LIFE)).not.toEqual(a)
    }
  })

  it('gives each kind its own life and shows one still white cube for that life under reduced motion', () => {
    const lives = { burst: 0.65, down: 1, explosion: 1.2 }
    expect([burstLife('hit', lives), burstLife('spark', lives), burstLife('down', lives), burstLife('explosion', lives), burstLife('dust', lives)]).toEqual([0.65, 0.65, 1, 1.2, 1.2])
    expect(burstLife('explosion', { burst: 0.65, down: 0.5 })).toBe(1)
    const still = burstVoxels({ ...at('explosion'), still: true }, 1.1, CORE, 1.2)
    expect(still).toEqual([{ x: 0, y: 2, z: 0, size: 0.16, tone: 'core', glow: 1 }])
    expect(burstVoxels({ ...at('spark'), still: true }, 0.7, 0.08, 0.65)).toHaveLength(0)
  })
})
