import * as THREE from 'three'
import { afterEach, describe, expect, it } from 'vitest'
import { initMotionTier } from '@/ui/motion/tier'
import { applyMarks, fx } from './ArenaLayer'
import { incomingAngle } from './ArenaHud'
import type { ArenaMark } from './store'
import { markRemainingMs } from './timing'

describe('arena HUD geometry', () => {
  it('points the hit arc towards the shooter as the camera sees it', () => {
    const cam = new THREE.PerspectiveCamera()
    cam.position.set(0, 0.5, 2)
    cam.lookAt(0, 0.5, 0) // looking north (scene -Z)
    cam.updateMatrixWorld()
    // a round flying south came from the north: straight ahead, the top of the screen
    expect(incomingAngle([0, -35, 0], cam)).toBeCloseTo(0, 6)
    // a round flying west came from the east: the right edge
    expect(incomingAngle([-35, 0, 0], cam)).toBeCloseTo(90, 6)
    expect(Math.abs(incomingAngle([35, 0, 0], cam))).toBeCloseTo(90, 6)
  })

  it('turns marks into a flash on our aircraft and debris at the hit point', () => {
    const calls: string[] = []
    const api = {
      debris: (_at: THREE.Vector3, n: number) => void calls.push(`debris ${n}`),
      flash: (vid: string) => void calls.push(`flash ${vid}`),
    }
    const mark = (kind: ArenaMark['kind'], down = false): ArenaMark => ({ seq: 1, kind, vehicle: 'sim-1', at: 0, point: [0, 0, 1], dir: null, down })
    applyMarks([mark('taken', true), mark('hit')], api, () => new THREE.Vector3())
    expect(calls).toEqual(['flash sim-1', 'debris 8', 'debris 18', 'debris 8'])
  })
})

describe('arena marks under reduced motion', () => {
  afterEach(() => {
    initMotionTier('')
    fx.voxels = []
    fx.flashes = []
  })

  it('unmounts a HUD mark after its token duration, whether or not an animation ran', () => {
    expect(markRemainingMs(1000, 1000, 120)).toBe(120)
    expect(markRemainingMs(1000, 1080, 120)).toBe(40)
    expect(markRemainingMs(1000, 1500, 120)).toBe(0)
    expect(markRemainingMs(1000, 900, 120)).toBe(120) // a clock read before the mark never extends it
    expect(markRemainingMs(1000, 1000, 0)).toBe(0) // unreadable token: no mark rather than a stuck one
  })

  it('throws no debris and no flash in reduced or off motion, remote shoot-downs included', () => {
    fx.enabled = true
    fx.debris(new THREE.Vector3(), 18, new THREE.Color(), 500, 0.6)
    fx.flash('sim-1', 80)
    expect([fx.voxels.length, fx.flashes.length]).toEqual([18, 1])
    for (const tier of ['reduced', 'off']) {
      fx.voxels = []
      fx.flashes = []
      initMotionTier(`?motion=${tier}`)
      fx.debris(new THREE.Vector3(), 18, new THREE.Color(), 500, 0.6)
      fx.flash('sim-1', 80)
      expect([fx.voxels.length, fx.flashes.length]).toEqual([0, 0])
    }
  })
})
