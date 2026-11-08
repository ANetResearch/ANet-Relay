import { describe, expect, it } from 'vitest'
import { normBoard, sortRows } from '@/arena/leaderboard'
import { BEATS, boardRows, dogfightAt, downSince, PILOTS, shotsBetween } from './dogfightFixture'

describe('dogfight dev fixture', () => {
  it('flies six aircraft, four of them ours, and brings the downed back after 5 s', () => {
    expect(PILOTS.filter((p) => p.local)).toHaveLength(4)
    expect(dogfightAt(1).every((c) => c.alive && c.p[2] > 0.5)).toBe(true)
    expect(downSince(PILOTS[4].key, 7.5)).toBe(6)
    expect(dogfightAt(10).find((c) => c.key === PILOTS[4].key)!.p[2]).toBe(0)
    expect(downSince(PILOTS[4].key, 11.1)).toBeNull()
  })

  it('brings the rammer onto its target at the ram and the bumper next to its neighbour', () => {
    const ram = BEATS.find((b) => b.kind === 'ram')!
    const at = dogfightAt(ram.t - 1e-3)
    const a = at.find((c) => c.key === ram.by)!.p
    const b = at.find((c) => c.key === ram.target)!.p
    expect(Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2])).toBeLessThan(0.1)
    const bump = BEATS.find((x) => x.kind === 'bump')!
    const near = dogfightAt(bump.t)
    const p = near.find((c) => c.key === bump.by)!.p
    const q = near.find((c) => c.key === bump.target)!.p
    expect(Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2])).toBeCloseTo(0.3, 1)
  })

  it('fires rounds every second of the loop and keeps a consistent board', () => {
    // at most one quiet second, while every remote aircraft is down
    let quiet = 0
    for (let t = 1; t <= 24; t += 1) if (!shotsBetween(t - 1, t).length) quiet++
    expect(quiet).toBeLessThanOrEqual(1)
    const board = normBoard({ rows: boardRows(1, 21) }, 'downs')
    expect(board.rows).toHaveLength(6)
    const top = sortRows(board.rows, 'downs')[0]
    expect(top.downs).toBeGreaterThan(0)
    expect(board.rows.filter((r) => r.local).map((r) => r.key)).toEqual(['SIM-1', 'SIM-2', 'SIM-3', 'SIM-4'])
    expect(board.rows.find((r) => r.key === 'SIM-3')!.rams).toBe(2)
  })
})
