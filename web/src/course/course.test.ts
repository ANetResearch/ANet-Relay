import { describe, expect, it } from 'vitest'
import { isActive, liveElapsed, parseCourses, presetKey, ringNormal, ringsExtent, ringState, runRings, transformRings, type CourseView } from './geometry'

const cage = [
  { c: [0, 1, 1] as [number, number, number], yaw_deg: 90, r: 0.45 },
  { c: [1, 0, 1] as [number, number, number], yaw_deg: 180, r: 0.45 },
]

const view = (over: Partial<CourseView> = {}): CourseView => ({
  course_id: 'cage-loop', name_en: null, name_zh: null, rings: [], state: 'running', next: 1, total: 4, started_at: 0,
  elapsed_s: 3, splits: [3], best_s: null, reason: null, origin: [2, 0, 0, 0], ...over,
})

describe('ring geometry (03 §8)', () => {
  it('places rings at the origin like the backend', () => {
    const r = transformRings(cage, [2, -2, 0, 0])
    expect(r[0].c).toEqual([2, -1, 1])
    const turned = transformRings(cage, [0, 0, 0, 90])
    expect(turned[0].c[0]).toBeCloseTo(1)
    expect(turned[0].c[1]).toBeCloseTo(0)
    expect(turned[0].yaw_deg).toBe(180)
  })
  it('prefers the rings in the snapshot, else the definition at the origin', () => {
    const placed = [{ c: [9, 9, 1] as [number, number, number], yaw_deg: 0, r: 0.4 }]
    expect(runRings(view({ rings: placed }), [])).toBe(placed)
    expect(runRings(view(), [{ id: 'cage-loop', name_en: 'Cage loop', name_zh: '围网环线', rings: cage }])[0].c).toEqual([2, 1, 1])
    expect(runRings(view({ course_id: 'none' }), [])).toEqual([])
  })
  it('normal follows the compass heading', () => {
    const [e, n] = ringNormal(90)
    expect(e).toBeCloseTo(1)
    expect(n).toBeCloseTo(0)
  })
  it('gives next / later / passed states', () => {
    const v = view({ next: 1 })
    expect([0, 1, 2].map((i) => ringState(i, v))).toEqual(['passed', 'next', 'later'])
    expect(ringState(3, view({ state: 'finished', next: 4 }))).toBe('passed')
    expect(ringState(1, view({ state: 'aborted' }))).toBe('later')
    expect(ringState(0, view({ state: 'waiting_takeoff', next: 0 }))).toBe('next')
  })
  it('advances the clock only while running', () => {
    expect(liveElapsed(view({ elapsed_s: 3 }), 100, 100.5)).toBeCloseTo(3.5)
    expect(liveElapsed(view({ state: 'finished', elapsed_s: 9 }), 100, 104)).toBe(9)
    expect(isActive(view())).toBe(true)
    expect(isActive(view({ state: 'aborted' }))).toBe(false)
    expect(isActive(null)).toBe(false)
  })
  it('parses GET /courses defensively', () => {
    const defs = parseCourses({ courses: [{ id: 'cage-loop', name_en: 'Cage loop', name_zh: '围网环线', rings: [...cage, { c: [1] }] }, { id: 3 }] })
    expect(defs).toHaveLength(1)
    expect(defs[0].rings).toHaveLength(2)
    expect(parseCourses(null)).toEqual([])
    expect(presetKey('slalom')).toBe('course.slalom')
    expect(presetKey('mine')).toBeNull()
  })
  it('measures the extent of a course', () => {
    const e = ringsExtent(cage)!
    expect(e.minX).toBeCloseTo(-0.45)
    expect(e.maxX).toBeCloseTo(1.45)
    expect(e.maxZ).toBeCloseTo(1.45)
    expect(ringsExtent([])).toBeNull()
  })
})

describe('voxel ring body (07 §2, §5)', () => {
  it('chains cubes of twice the tube along the ring centre line, closed and evenly spaced', async () => {
    const { ringVoxels } = await import('./voxelRing')
    const { edge, cubes } = ringVoxels(0.5, 0.03)
    expect(edge).toBeCloseTo(0.06)
    expect(cubes.length).toBe(Math.ceil((2 * Math.PI * 0.5) / 0.06))
    for (const c of cubes) expect(Math.hypot(c.x, c.y)).toBeCloseTo(0.5, 9)
    const gap = Math.hypot(cubes[1].x - cubes[0].x, cubes[1].y - cubes[0].y)
    expect(gap).toBeLessThanOrEqual(edge + 1e-9)
    expect(ringVoxels(0.05, 0.03).cubes).toHaveLength(12)
  })
})
