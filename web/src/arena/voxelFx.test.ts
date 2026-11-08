import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import { burstVoxels, DOWN_SHARDS, floorSize, HIT_SHARDS, seedOf, type Burst } from './voxelFx'

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
