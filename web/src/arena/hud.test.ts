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

  it('turns marks into a flash on our aircraft and a voxel burst at every hit point', () => {
    const calls: string[] = []
    const api = {
      burst: (_at: unknown, kind: string) => void calls.push(`burst ${kind}`),
      flash: (vid: string) => void calls.push(`flash ${vid}`),
      explode: (key: string | null) => (calls.push(`explode ${key}`), true),
    }
    const mark = (kind: ArenaMark['kind'], down = false): ArenaMark => ({ seq: 1, kind, vehicle: 'sim-1', at: 0, point: [0, 0, 1], dir: null, down })
    applyMarks([mark('taken', true), mark('hit'), mark('hit', true)], api, () => new THREE.Vector3())
    // a shoot-down burst belongs to the aircraft that went down, so only a hit taken carries one here
    // v0.5 (07 §5): the shoot-down is an explosion of that aircraft
    expect(calls).toEqual(['flash sim-1', 'burst hit', 'explode sim-1', 'burst hit', 'burst hit'])
  })
})

describe('arena marks under reduced motion', () => {
  afterEach(() => {
    initMotionTier('')
    fx.clear()
  })

  it('unmounts a HUD mark after its token duration, whether or not an animation ran', () => {
    expect(markRemainingMs(1000, 1000, 120)).toBe(120)
    expect(markRemainingMs(1000, 1080, 120)).toBe(40)
    expect(markRemainingMs(1000, 1500, 120)).toBe(0)
    expect(markRemainingMs(1000, 900, 120)).toBe(120) // a clock read before the mark never extends it
    expect(markRemainingMs(1000, 1000, 0)).toBe(0) // unreadable token: no mark rather than a stuck one
  })

  it('keeps a still cube at each hit and no flash in reduced or off motion, remote shoot-downs included', () => {
    fx.explode('peer/sim-1', new THREE.Vector3(), 1)
    fx.flash('sim-1', 80)
    expect([fx.bursts.map((b) => [b.kind, b.still ?? false]), fx.flashes.length]).toEqual([[['explosion', false]], 1])
    // v0.5: the same aircraft reported down twice explodes once
    expect(fx.explode('peer/sim-1', new THREE.Vector3(), 2)).toBe(false)
    for (const tier of ['reduced', 'off']) {
      fx.clear()
      initMotionTier(`?motion=${tier}`)
      fx.explode('peer/sim-1', new THREE.Vector3(), 1)
      fx.burst(new THREE.Vector3(), 'hit', 2)
      fx.burst(new THREE.Vector3(), 'spark', 3)
      fx.flash('sim-1', 80)
      expect([fx.bursts.map((b) => [b.kind, b.still]), fx.flashes.length]).toEqual([[['explosion', true], ['hit', true], ['spark', true]], 0])
    }
  })
})
