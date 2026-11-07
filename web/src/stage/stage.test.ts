import { describe, expect, it } from 'vitest'
import { en } from '@/i18n/en'
import { zh } from '@/i18n/zh'
import { chasePose, nextView, overviewDistance, overviewPose, topEquivalentHeight, topPose } from './camera'
import { en6, translate6, zh6 } from './copy'
import { angleDelta, attitudeEuler, cubicBezier, easeSmoothOut, enuToScene, expAlpha, sceneToEnu } from './math'
import { addPoint, emptyBounds, fallbackSpawn, framed, frameOffsets, framesUsed, place, slotPosition, slotRow, SLOT_SPACING } from './placement'

const veh = (id: string, over: Record<string, unknown> = {}) => ({
  id, kind: 'real' as const, position: null, global: null, gps: null, altitude_m: null, ...over,
})

describe('frames', () => {
  it('maps ENU to the Y-up scene with north at -Z and back', () => {
    expect(enuToScene(1, 2, 3)).toEqual([1, 3, -2])
    expect(sceneToEnu(...enuToScene(1.5, -2, 0.7))).toEqual([1.5, -2, 0.7])
  })
  it('turns yaw clockwise and roll right-down', () => {
    const [x, y, z] = attitudeEuler(0.1, 0.2, 0.3)
    expect([x, y, z]).toEqual([0.2, -0.3, -0.1])
  })
  it('smooths with a time constant and takes the short way round', () => {
    expect(expAlpha(0.12, 0.12)).toBeCloseTo(1 - Math.exp(-1))
    expect(expAlpha(1, 0)).toBe(1)
    expect(angleDelta(3.1, -3.1)).toBeCloseTo(2 * Math.PI - 6.2)
  })
  it('samples cubic-bezier easing', () => {
    expect(easeSmoothOut(0)).toBe(0)
    expect(easeSmoothOut(1)).toBe(1)
    expect(easeSmoothOut(0.5)).toBeGreaterThan(0.8)
    expect(cubicBezier(0, 0, 1, 1)(0.3)).toBeCloseTo(0.3, 3)
  })
})

describe('placement (02 §6.3)', () => {
  it('keeps the pad row south of spawns and the safety volume', () => {
    const row = slotRow([[0, 0], [2, 0], [0, -2]], { x: 3, y: 3 })
    expect(row.y).toBeCloseTo(-2 - 1.5 - 0.6)
    expect(row.cx).toBe(1)
    expect(slotRow([], { x: 3, y: 3 })).toEqual({ y: -2.1, cx: 0 })
  })
  it('spaces slots 0.6 m west to east in fleet order', () => {
    const row = { y: -2, cx: 0 }
    expect(slotPosition(row, 0, 3)).toEqual([-SLOT_SPACING, -2])
    expect(slotPosition(row, 2, 3)).toEqual([SLOT_SPACING, -2])
  })
  it('places by position, GPS projection, else slot', () => {
    const row = { y: -2, cx: 0 }
    const p = place(
      [
        veh('a', { position: { frame: 'local', x: 1, y: 2, z: 0.5, vx: 0, vy: 0, vz: 0, age_s: 0 } }),
        veh('b'),
        veh('g1', { global: { lat: 47.6, lon: -122.3, alt: 30 }, gps: { fix: 3, sats: 12, hdop: 0.8 }, altitude_m: 1 }),
        veh('g2', { global: { lat: 47.60001, lon: -122.3, alt: 30 }, gps: { fix: 3, sats: 12, hdop: 0.8 } }),
        veh('c', { global: { lat: 47.6, lon: -122.3, alt: 30 }, gps: { fix: 2, sats: 5, hdop: 3 } }),
      ],
      row,
    )
    expect(p.map((x) => x.kind)).toEqual(['position', 'slot', 'gps', 'gps', 'slot'])
    expect(p[0]).toMatchObject({ x: 1, y: 2, z: 0.5 })
    expect(p[2]).toMatchObject({ x: 0, y: 0, z: 1 })
    expect(p[3].y).toBeCloseTo(1.1054, 3)
    expect([p[1].x, p[4].x]).toEqual([-0.3, 0.3])
  })
  it('draws a real local frame beside the simulator grid, never on a spawn point', () => {
    const pos = { frame: 'local' as const, x: 0, y: 0, z: 0, vx: 0, vy: 0, vz: 0, age_s: 0 }
    const fleet = [veh('sim-1', { kind: 'sim', position: { ...pos, frame: 'sim' } }), veh('q1', { position: pos })]
    const used = framesUsed(fleet)
    expect(used).toEqual({ sim: true, local: true, gps: false })
    const spawns: [number, number][] = [[0, 0], [2, 0], [-2, 0]]
    const off = frameOffsets(spawns, { x: 3, y: 3 }, used)
    const p = place(fleet, { y: -2.1, cx: 0 }, off)
    expect(p[0]).toMatchObject({ x: 0, y: 0 })
    expect(p[1].y).toBeGreaterThanOrEqual(3)
    expect(off.gps[1]).toBeGreaterThan(off.local[1])
    // a real-only fleet keeps its own origin at the scene origin
    expect(frameOffsets([], { x: 3, y: 3 }, { sim: false, local: true, gps: false }).local).toEqual([0, 0])
  })
  it('knows the simulator spawn grid', () => {
    expect(fallbackSpawn('sim-1')).toEqual([0, 0])
    expect(fallbackSpawn('sim-8')).toEqual([4, -2])
    expect(fallbackSpawn('sim-9')).toBeNull()
    expect(fallbackSpawn('q01')).toBeNull()
  })
  it('frames at least 4 x 4 m, or 0.8 m around a lone aircraft', () => {
    const one = framed(addPoint(emptyBounds(), 1, 1), 1)
    expect(one.maxX - one.minX).toBeCloseTo(0.8)
    const two = framed(addPoint(addPoint(emptyBounds(), 0, 0), 1, 0), 2)
    expect(two.maxX - two.minX).toBe(4)
    expect(two.maxY - two.minY).toBe(4)
    const none = framed(emptyBounds(), 0)
    expect(none.maxX - none.minX).toBe(4)
  })
})

describe('camera (02 §6.5)', () => {
  const b16 = framed(addPoint(addPoint(emptyBounds(), -2, -2), 4, 0), 16)
  it('looks from the south-east at 32 degrees', () => {
    const p = overviewPose(framed(emptyBounds(), 0), 1.4)
    const dx = p.position[0] - p.target[0]
    const dz = p.position[2] - p.target[2]
    const dy = p.position[1] - p.target[1]
    expect(dx).toBeGreaterThan(0)
    expect(dz).toBeGreaterThan(0)
    expect((Math.atan2(dy, Math.hypot(dx, dz)) * 180) / Math.PI).toBeCloseTo(32)
  })
  it('backs off for wider fleets and narrower viewports', () => {
    expect(overviewDistance(b16, 1.4)).toBeGreaterThan(overviewDistance(framed(emptyBounds(), 0), 1.4))
    expect(overviewDistance(b16, 0.8)).toBeGreaterThan(overviewDistance(b16, 1.6))
  })
  it('a lone aircraft takes roughly a tenth of the width', () => {
    const b = framed(addPoint(emptyBounds(), 0, 0), 1)
    const aspect = 1.1
    const d = overviewDistance(b, aspect)
    const visible = 2 * d * Math.tan((16 * Math.PI) / 180) * aspect
    expect(0.133 / visible).toBeGreaterThan(0.05)
    expect(0.133 / visible).toBeLessThan(0.12)
  })
  it('chases 1.2 m behind and 0.45 m above, looking 2 m ahead', () => {
    const p = chasePose([0, 0, 1], 0)
    expect(p.position).toEqual(enuToScene(0, -1.2, 1.45))
    expect(p.target).toEqual(enuToScene(0, 2, 1))
    const e = chasePose([0, 0, 1], Math.PI / 2)
    expect(e.position[0]).toBeCloseTo(-1.2)
  })
  it('top view fits the bounds and matches a perspective height', () => {
    const t = topPose(b16, 1.5)
    expect(t.halfHeight * 2 * 1.5 * 0.6).toBeGreaterThanOrEqual(b16.maxX - b16.minX - 1e-9)
    expect(topEquivalentHeight(1)).toBeCloseTo(1 / Math.tan((16 * Math.PI) / 180))
  })
  it('cycles views and skips chase without a target', () => {
    expect(nextView('overview', true)).toBe('chase')
    expect(nextView('overview', false)).toBe('top')
    expect(nextView('top', false)).toBe('overview')
  })
})

describe('WP6 copy', () => {
  it('has identical key sets and placeholders in both languages', () => {
    expect(Object.keys(zh6).sort()).toEqual(Object.keys(en6).sort())
    const ph = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()
    for (const k of Object.keys(en6) as (keyof typeof en6)[]) expect(ph(zh6[k]), k).toEqual(ph(en6[k]))
  })
  it('does not overlap the shared dictionaries and has no exclamation marks', () => {
    const shared = new Set([...Object.keys(en), ...Object.keys(zh)])
    expect(Object.keys(en6).filter((k) => shared.has(k))).toEqual([])
    for (const s of [...Object.values(en6), ...Object.values(zh6)]) expect(s).not.toMatch(/[!！]/)
  })
  it('falls back to the shared dictionaries', () => {
    expect(translate6('en', 'stage.view.chase')).toBe('Chase')
    expect(translate6('zh', 'course.ringN', { n: 3 })).toBe('第 3 环')
  })
})
