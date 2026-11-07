import { describe, expect, it } from 'vitest'
import { armedIntervals, extent, groupByDay, indexAt, linePath, recordFor, timeTicks, type RecordSummary } from './model'

const rec = (id: number, vehicle: string, started: number, ended: number | null): RecordSummary => ({
  id, vehicle_id: vehicle, kind: 'sim', label: vehicle, autopilot: null, started_at: started, ended_at: ended, live: ended === null,
  duration_s: 0, max_alt_m: null, samples: 0, armed_s: 0, course_best_s: null, course_finished: 0, alerts: 0,
})

describe('records model', () => {
  it('finds the sample at or before a time', () => {
    const ts = [10, 10.2, 10.4, 10.6]
    expect(indexAt(ts, 9)).toBe(0)
    expect(indexAt(ts, 10.3)).toBe(1)
    expect(indexAt(ts, 10.4)).toBe(2)
    expect(indexAt(ts, 99)).toBe(3)
    expect(indexAt([], 1)).toBe(-1)
  })
  it('pads extents and ignores nulls', () => {
    expect(extent([null, null])).toBeNull()
    const e = extent([1, null, 3], 0)!
    expect(e).toEqual([1, 3])
    const flat = extent([2, 2], 0, 1)!
    expect(flat).toEqual([1.5, 2.5])
  })
  it('breaks lines at nulls', () => {
    const d = linePath([0, 1, 2, 3], [1, null, 2, 3], (t) => t, (v) => v)
    expect(d).toBe('M0.0 1.0M2.0 2.0L3.0 3.0')
  })
  it('chooses readable time ticks', () => {
    const ticks = timeTicks(100, 160, 6)
    expect(ticks[0]).toBe(100)
    expect(ticks.length).toBeLessThanOrEqual(7)
    expect(ticks[1] - ticks[0]).toBe(10)
  })
  it('derives armed intervals', () => {
    expect(armedIntervals({ t: [0, 1, 2, 3, 4], armed: [0, 1, 1, 0, 1] })).toEqual([
      { start: 1, end: 3 },
      { start: 4, end: 4 },
    ])
  })
  it('groups by day, newest first', () => {
    const day = 86400
    const g = groupByDay([rec(1, 'a', 1000, 1100), rec(2, 'a', 1000 + day, null), rec(3, 'b', 1100 + day, null)])
    expect(g.length).toBe(2)
    expect(g[0].items.map((r) => r.id)).toEqual([3, 2])
  })
  it('finds the record covering a moment', () => {
    const list = [rec(1, 'a', 100, 200), rec(2, 'a', 300, null), rec(3, 'b', 100, 400)]
    expect(recordFor(list, 'a', 150)?.id).toBe(1)
    expect(recordFor(list, 'a', 350)?.id).toBe(2)
    expect(recordFor(list, 'a', 250)?.id).toBe(2)
    expect(recordFor(list, 'c', 250)).toBeNull()
  })
})
