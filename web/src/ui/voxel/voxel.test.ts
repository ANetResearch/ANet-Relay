// 05 §2.3, §2.5: the voxel language - diagonal wave, cell flips, the logo dot matrix and its landing order, the
// VoxelLoader cube geometry and the loop budget it draws from.
import { describe, expect, it } from 'vitest'
import { acquireLoop, holdsLoop, LOOP_SLOTS, releaseLoop } from '@/ui/motion/budget'
import { cubeModel, CUBE_N } from './cube'
import { waveTiming } from './paint'
import { cellKeys, cellProgress, diagKey, easeSpringSnappy, flightStart, flipScale, frontTone, grid, hash01, landingOrder, logoCells, scatterVectors } from './wave'

describe('diagonal wave', () => {
  it('runs from the bottom-left corner (0) to the top-right corner (1)', () => {
    expect(diagKey(0, 900, 1440, 900)).toBe(0)
    expect(diagKey(1440, 0, 1440, 900)).toBe(1)
    const mid = diagKey(720, 450, 1440, 900)
    expect(mid).toBeGreaterThan(0.4)
    expect(mid).toBeLessThan(0.6)
    // further right or further up is later
    expect(diagKey(800, 450, 1440, 900)).toBeGreaterThan(mid)
    expect(diagKey(720, 300, 1440, 900)).toBeGreaterThan(mid)
  })
  it('covers a box with whole cells and keys every centre', () => {
    const g = grid(100, 50, 24)
    expect(g).toEqual({ size: 24, cols: 5, rows: 3 })
    const keys = cellKeys(g, 100, 50)
    expect(keys).toHaveLength(15)
    for (const k of keys) expect(k).toBeGreaterThanOrEqual(0)
    expect(keys[(g.rows - 1) * g.cols]).toBeLessThan(keys[g.cols - 1])
  })
  it('flips each cell from cover through g800 and g50 to clear', () => {
    expect(cellProgress(0, 0.5, 0, 100, 20)).toBe(0)
    expect(cellProgress(60, 0.5, 0, 100, 20)).toBe(0.5)
    expect(cellProgress(80, 0.5, 0, 100, 20)).toBe(1)
    expect(cellProgress(5, 0, 0, 100, 0)).toBe(1)
    expect([0, 0.2, 0.5, 0.9, 1].map(frontTone)).toEqual(['cover', 'dark', 'light', 'clear', 'clear'])
    expect(flipScale(0)).toBe(1)
    expect(flipScale(1)).toBe(0)
    expect(flipScale(0.5)).toBeCloseTo(Math.SQRT1_2)
  })
  it('keeps the front 2-3 cells wide and ends on time', () => {
    const g = grid(1440, 900, 24)
    const { spread, cell } = waveTiming(g, 400)
    expect(spread + cell).toBeCloseTo(400)
    const front = (cell / (spread + cell)) * (g.cols * Math.cos(Math.PI / 10) + g.rows * Math.sin(Math.PI / 10) + 2.5)
    expect(front).toBeCloseTo(2.5)
  })
})

describe('logo dot matrix', () => {
  // 3 x 2 image: red, white, black / transparent, dark red, faint
  const px = [233, 48, 36, 255, 250, 250, 250, 255, 0, 0, 0, 255, 0, 0, 0, 0, 120, 30, 30, 200, 255, 255, 255, 40]
  it('classifies brand red, light and the dark badge body; skips transparent pixels', () => {
    expect(logoCells(px, 3, 2)).toEqual([
      { c: 0, r: 0, tone: 'red' },
      { c: 1, r: 0, tone: 'light' },
      { c: 2, r: 0, tone: 'dark' },
      { c: 1, r: 1, tone: 'red' },
    ])
  })
  it('lands voxels nearest the centre (diagonally) first, deterministically', () => {
    const cells = Array.from({ length: 20 }, (_, i) => ({ c: i % 10, r: Math.floor(i / 10), tone: 'dark' as const }))
    const order = landingOrder(cells, 10, 2)
    expect(order).toHaveLength(20)
    const first = order[0]
    expect(Math.abs(first.c + 0.5 - 5)).toBeLessThanOrEqual(1)
    const last = order[order.length - 1]
    expect(Math.abs(last.c + 0.5 - 5)).toBeGreaterThanOrEqual(4)
    expect(landingOrder(cells, 10, 2)).toEqual(order)
  })
  it('starts every flight outside the window', () => {
    const w = 1440
    const h = 900
    const centre = { x: 720, y: 426 }
    for (let i = 0; i < 40; i++) {
      const to = { x: 400 + i * 15, y: 380 + (i % 8) * 12 }
      const p = flightStart(to, centre, w, h, i + 1)
      expect(p.x < 0 || p.x > w || p.y < 0 || p.y > h).toBe(true)
    }
    const atCentre = flightStart(centre, centre, w, h, 3)
    expect(atCentre.x < 0 || atCentre.x > w || atCentre.y < 0 || atCentre.y > h).toBe(true)
  })
  it('uses a spring that overshoots and settles', () => {
    expect(easeSpringSnappy(0)).toBe(0)
    expect(easeSpringSnappy(1)).toBe(1)
    expect(Math.max(...[0.5, 0.6, 0.7, 0.8].map(easeSpringSnappy))).toBeGreaterThan(1)
  })
  it('hashes and scatters deterministically', () => {
    expect(hash01(7)).toBe(hash01(7))
    expect(hash01(7)).not.toBe(hash01(8))
    for (let i = 0; i < 100; i++) expect(hash01(i)).toBeGreaterThanOrEqual(0)
    const v = scatterVectors(24)
    expect(v).toHaveLength(24)
    for (const d of v) expect(Math.hypot(d.x, d.y)).toBeCloseTo(1)
  })
})

describe('VoxelLoader cube', () => {
  const m = cubeModel()
  it('has 27 voxels with a build order 0..26, bottom layer first', () => {
    expect(m.voxels).toHaveLength(CUBE_N ** 3)
    expect(m.voxels.map((v) => v.light).sort((a, b) => a - b)).toEqual(Array.from({ length: 27 }, (_, i) => i))
    const bottom = m.voxels.filter((v) => v.key.endsWith(',0')).map((v) => v.light)
    expect(Math.max(...bottom)).toBe(8)
  })
  it('paints back to front and frames every face', () => {
    const depth = (k: string) => k.split(',').map(Number).reduce((a, b) => a + b, 0)
    const d = m.voxels.map((v) => depth(v.key))
    expect([...d].sort((a, b) => a - b)).toEqual(d)
    const [x, y, w, h] = m.viewBox.split(' ').map(Number)
    for (const v of m.voxels) {
      for (const pt of `${v.top} ${v.left} ${v.right}`.split(' ')) {
        const [px, py] = pt.split(',').map(Number)
        expect(px).toBeGreaterThanOrEqual(x)
        expect(px).toBeLessThanOrEqual(x + w)
        expect(py).toBeGreaterThanOrEqual(y)
        expect(py).toBeLessThanOrEqual(y + h)
      }
      expect(Math.hypot(v.sx, v.sy)).toBeCloseTo(1)
    }
  })
})

describe('loop budget', () => {
  it('grants at most LOOP_SLOTS loops, in request order, and hands a freed slot on', () => {
    const ids = [acquireLoop(), acquireLoop(), acquireLoop()]
    expect(LOOP_SLOTS).toBe(2)
    expect(ids.map(holdsLoop)).toEqual([true, true, false])
    releaseLoop(ids[0])
    expect(holdsLoop(ids[0])).toBe(false)
    expect(holdsLoop(ids[2])).toBe(true)
    releaseLoop(ids[1])
    releaseLoop(ids[2])
    releaseLoop(ids[2])
    expect(ids.map(holdsLoop)).toEqual([false, false, false])
  })
})
