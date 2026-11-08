import { describe, expect, it } from 'vitest'
import { TRAIL_JUMP_M, trailJump } from './trail'

describe('trail restarts (v0.5 respawns)', () => {
  it('keeps a trail through flight and restarts it on a respawn jump', () => {
    const arr = new Float32Array([0, 0, 0, 1, 1.5, 0.2])
    expect(trailJump(arr, 3, { x: 1.9, y: 1.5, z: 0.4 })).toBe(false) // 9 m/s for 100 ms
    expect(trailJump(arr, 3, { x: 1 + TRAIL_JUMP_M + 0.1, y: 1.5, z: 0.2 })).toBe(true)
    expect(trailJump(arr, 0, { x: -2, y: 2, z: 2 })).toBe(true)
  })
})
