// Records data model (03 §11, 02 §13): record list grouping, sample lookup at the cursor, chart scales and paths.
// Pure and framework-free; unit-tested in model.test.ts.

export interface RecordSummary {
  id: number
  vehicle_id: string
  kind: 'real' | 'sim'
  label: string
  autopilot: string | null
  started_at: number
  ended_at: number | null
  live: boolean
  duration_s: number
  max_alt_m: number | null
  samples: number
  armed_s: number
  course_best_s: number | null
  course_finished: number
  alerts: number
}

export interface Samples {
  t: number[]
  armed: (number | null)[]
  mode: (string | null)[]
  roll: (number | null)[]
  pitch: (number | null)[]
  yaw: (number | null)[]
  x: (number | null)[]
  y: (number | null)[]
  z: (number | null)[]
  alt: (number | null)[]
  climb: (number | null)[]
  voltage: (number | null)[]
  current: (number | null)[]
  remaining: (number | null)[]
  throttle: (number | null)[]
  controller: (string | null)[]
}

export interface RecordEvent {
  seq: number
  t: number
  kind: string
  data: Record<string, unknown>
  text_en: string
  text_zh: string
}

export interface ControlSegment {
  start: number
  end: number | null
  controller: 'human' | 'agent' | 'none' | string
  who: string | null
}

export interface CourseRun {
  id: number
  course_id: string
  started_at: number
  finished_at: number | null
  state: string
  total_s: number | null
  splits: number[]
  controller: string | null
}

export interface RecordDetail {
  session: Omit<RecordSummary, 'live' | 'duration_s' | 'max_alt_m' | 'samples' | 'armed_s' | 'course_best_s' | 'course_finished' | 'alerts'>
  summary: {
    duration_s: number
    armed_s: number
    max_alt_m: number | null
    voltage_start: number | null
    voltage_end: number | null
    possessions: number
    handbacks: number
    course_best_s: number | null
  }
  samples: Samples
  events: RecordEvent[]
  control_segments: ControlSegment[]
  course_runs: CourseRun[]
}

/** Local calendar day "2026-11-06" of a unix time. */
export function dayKey(t: number): string {
  const d = new Date(t * 1000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** Newest day first, newest record first inside a day. */
export function groupByDay<T extends { started_at: number }>(list: readonly T[]): { day: string; items: T[] }[] {
  const map = new Map<string, T[]>()
  for (const r of [...list].sort((a, b) => b.started_at - a.started_at)) {
    const k = dayKey(r.started_at)
    const arr = map.get(k)
    if (arr) arr.push(r)
    else map.set(k, [r])
  }
  return [...map.entries()].map(([day, items]) => ({ day, items }))
}

/** Index of the last sample at or before t (0 when t precedes all samples); -1 for no samples. */
export function indexAt(ts: readonly number[], t: number): number {
  if (!ts.length) return -1
  let lo = 0
  let hi = ts.length - 1
  if (t <= ts[0]) return 0
  if (t >= ts[hi]) return hi
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (ts[mid] <= t) lo = mid
    else hi = mid - 1
  }
  return lo
}

/** [min, max] of the finite values, padded so a flat series still has height; null when there are none. */
export function extent(values: readonly (number | null)[], pad = 0.08, minSpan = 0): [number, number] | null {
  let lo = Infinity
  let hi = -Infinity
  for (const v of values) {
    if (typeof v !== 'number' || !Number.isFinite(v)) continue
    if (v < lo) lo = v
    if (v > hi) hi = v
  }
  if (lo === Infinity) return null
  let span = hi - lo
  if (span < minSpan) {
    const mid = (hi + lo) / 2
    lo = mid - minSpan / 2
    hi = mid + minSpan / 2
    span = minSpan
  }
  if (span === 0) span = Math.abs(hi) || 1
  return [lo - span * pad, hi + span * pad]
}

/** SVG path through (t, v) pairs; nulls break the line. */
export function linePath(ts: readonly number[], vs: readonly (number | null)[], x: (t: number) => number, y: (v: number) => number): string {
  let d = ''
  let pen = false
  for (let i = 0; i < ts.length; i++) {
    const v = vs[i]
    if (typeof v !== 'number' || !Number.isFinite(v)) {
      pen = false
      continue
    }
    d += `${pen ? 'L' : 'M'}${x(ts[i]).toFixed(1)} ${y(v).toFixed(1)}`
    pen = true
  }
  return d
}

const STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200]

/** Time ticks (unix seconds) at a step that gives roughly `target` ticks across [t0, t1]. */
export function timeTicks(t0: number, t1: number, target = 6): number[] {
  const span = Math.max(1, t1 - t0)
  const step = STEPS.find((s) => span / s <= target) ?? STEPS[STEPS.length - 1]
  const out: number[] = []
  for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) out.push(t)
  return out
}

/** Intervals where the armed flag is set. */
export function armedIntervals(s: Pick<Samples, 't' | 'armed'>): { start: number; end: number }[] {
  const out: { start: number; end: number }[] = []
  let start: number | null = null
  for (let i = 0; i < s.t.length; i++) {
    const on = !!s.armed[i]
    if (on && start === null) start = s.t[i]
    if (!on && start !== null) {
      out.push({ start, end: s.t[i] })
      start = null
    }
  }
  if (start !== null) out.push({ start, end: s.t[s.t.length - 1] })
  return out
}

/** Domain of a record: first to last sample, else start to end (or now for a live record). */
export function recordDomain(d: Pick<RecordDetail, 'session' | 'samples'>, now: number): [number, number] {
  const ts = d.samples.t
  const t0 = ts.length ? Math.min(ts[0], d.session.started_at) : d.session.started_at
  const t1 = ts.length ? ts[ts.length - 1] : (d.session.ended_at ?? now)
  return [t0, Math.max(t1, t0 + 1)]
}

/** The record of `vehicle` that covers time t; else the vehicle's latest record. */
export function recordFor(list: readonly RecordSummary[], vehicle: string, t: number): RecordSummary | null {
  const own = list.filter((r) => r.vehicle_id === vehicle).sort((a, b) => b.started_at - a.started_at)
  return own.find((r) => r.started_at <= t && (r.ended_at === null || t <= r.ended_at)) ?? own[0] ?? null
}
