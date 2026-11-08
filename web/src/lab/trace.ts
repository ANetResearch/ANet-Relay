// Evaluation traces (06 §3.4): the 10 Hz downsampled flight of every aircraft in one judged run or duel round, plus
// the rounds fired, as GET /traces/{id} returns it. Pure: no React, no three.js (trace.test.ts).
//
// Wire shape (WP-2b writes it; this parser is the reference reader):
//
//   {
//     "id": "tr-…", "kind": "challenge" | "duel" | "run", "title": "duel_easy · seed 3",
//     "hz": 10, "duration_s": 42.3,
//     "arena": {"radius": 8, "floor": 0.3, "ceiling": 4} | null,
//     "aircraft": [{
//       "id": "a", "name": "Hunter (easy)", "program": "hunter_easy", "rev": 1, "team": "a",
//       "samples": {                      // columns of equal length, or rows [[t, x, y, z, roll, pitch, yaw,
//         "t": [0, 0.1, …],               //   throttle, hp, state, armed], …] in the same order
//         "x": […], "y": […], "z": […],    // ENU metres in the arena frame (origin at the arena centre)
//         "roll": […], "pitch": […], "yaw": […],   // degrees; yaw clockwise from north, 0-360
//         "throttle": […],                 // 0..1
//         "hp": […] | null, "state": […] | null, "armed": […] | null
//       }
//     }],
//     "shots": [{"id": "s1", "owner": "a", "t0": 3.2, "p": [x, y, z], "v": [vx, vy, vz], "t_end": 3.6 | null}],
//     "events": [{"t": 3.6, "kind": "hit" | "down" | "finish", "vehicle": "b", "by": "a"}]
//   }
//
// Times are seconds from the start of the run. Shots fly the arena integrator (arena/ballistics.ts, 1/240 s, drag
// and gravity) from (p, v) at t0 until t_end (a hit) or the 2.5 s lifetime, so the replay draws the same paths the
// evaluator flew without storing every bullet sample.

export interface TraceArena {
  radius: number
  floor: number
  ceiling: number
}

export interface TraceCraft {
  id: string
  name: string
  program: string | null
  rev: number | null
  team: string | null
  t: number[]
  x: number[]
  y: number[]
  z: number[]
  /** radians */
  roll: number[]
  pitch: number[]
  yaw: number[]
  /** 0..1 */
  throttle: number[]
  hp: (number | null)[]
  state: (string | null)[]
  armed: boolean[]
}

export interface TraceShot {
  id: string
  owner: string
  t0: number
  p: [number, number, number]
  v: [number, number, number]
  t_end: number | null
}

export interface TraceEvent {
  t: number
  kind: string
  vehicle: string | null
  by: string | null
}

export interface Trace {
  id: string
  kind: 'challenge' | 'duel' | 'run'
  title: string
  hz: number
  duration_s: number
  arena: TraceArena | null
  aircraft: TraceCraft[]
  shots: TraceShot[]
  events: TraceEvent[]
}

/** One aircraft at a moment of the trace. */
export interface TraceSample {
  x: number
  y: number
  z: number
  /** radians */
  roll: number
  pitch: number
  yaw: number
  throttle: number
  hp: number | null
  state: string | null
  armed: boolean
}

type Obj = Record<string, unknown>
const obj = (x: unknown): Obj => (x && typeof x === 'object' && !Array.isArray(x) ? (x as Obj) : {})
const finite = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x)
const DEG = Math.PI / 180
const COLUMNS = ['t', 'x', 'y', 'z', 'roll', 'pitch', 'yaw', 'throttle', 'hp', 'state', 'armed'] as const

function vec3(x: unknown): [number, number, number] | null {
  return Array.isArray(x) && x.length === 3 && x.every(finite) ? [x[0], x[1], x[2]] : null
}

function columnsOf(samples: unknown): Record<string, unknown[]> | null {
  if (Array.isArray(samples)) {
    const out: Record<string, unknown[]> = Object.fromEntries(COLUMNS.map((c) => [c, []]))
    for (const row of samples) {
      if (!Array.isArray(row)) return null
      COLUMNS.forEach((c, i) => out[c].push(row[i] ?? null))
    }
    return out
  }
  const o = obj(samples)
  if (!Array.isArray(o.t)) return null
  return Object.fromEntries(COLUMNS.map((c) => [c, Array.isArray(o[c]) ? (o[c] as unknown[]) : []]))
}

function parseCraft(raw: unknown, index: number): TraceCraft | null {
  const o = obj(raw)
  const id = typeof o.id === 'string' && o.id ? o.id : `craft-${index + 1}`
  const cols = columnsOf(o.samples)
  if (!cols) return null
  const n = cols.t.length
  const t: number[] = []
  const keep: number[] = []
  // keep samples with a finite, non-decreasing time and a full position
  for (let i = 0; i < n; i++) {
    const ti = cols.t[i]
    if (!finite(ti) || (t.length && ti < t[t.length - 1])) continue
    if (!finite(cols.x[i]) || !finite(cols.y[i]) || !finite(cols.z[i])) continue
    t.push(ti)
    keep.push(i)
  }
  if (!t.length) return null
  const numAt = (c: string, i: number, d: number) => (finite(cols[c][i]) ? (cols[c][i] as number) : d)
  return {
    id,
    name: typeof o.name === 'string' && o.name ? o.name : typeof o.program === 'string' ? o.program : id,
    program: typeof o.program === 'string' ? o.program : null,
    rev: finite(o.rev) ? o.rev : null,
    team: typeof o.team === 'string' ? o.team : null,
    t,
    x: keep.map((i) => cols.x[i] as number),
    y: keep.map((i) => cols.y[i] as number),
    z: keep.map((i) => cols.z[i] as number),
    roll: keep.map((i) => numAt('roll', i, 0) * DEG),
    pitch: keep.map((i) => numAt('pitch', i, 0) * DEG),
    yaw: keep.map((i) => numAt('yaw', i, 0) * DEG),
    throttle: keep.map((i) => Math.min(1, Math.max(0, numAt('throttle', i, 0)))),
    hp: keep.map((i) => (finite(cols.hp[i]) ? (cols.hp[i] as number) : null)),
    state: keep.map((i) => (typeof cols.state[i] === 'string' ? (cols.state[i] as string) : null)),
    armed: keep.map((i) => (typeof cols.armed[i] === 'boolean' ? (cols.armed[i] as boolean) : true)),
  }
}

/** Reads a trace body; null when it holds no aircraft with usable samples. */
export function parseTrace(raw: unknown): Trace | null {
  const top = obj(raw)
  const o = top.trace && typeof top.trace === 'object' ? obj(top.trace) : top
  const aircraft = (Array.isArray(o.aircraft) ? o.aircraft : []).map(parseCraft).filter((c): c is TraceCraft => c !== null)
  if (!aircraft.length) return null
  const shots: TraceShot[] = []
  for (const [i, s] of (Array.isArray(o.shots) ? o.shots : []).entries()) {
    const x = obj(s)
    const p = vec3(x.p)
    const v = vec3(x.v)
    if (!p || !v || !finite(x.t0)) continue
    shots.push({ id: typeof x.id === 'string' ? x.id : `s${i + 1}`, owner: typeof x.owner === 'string' ? x.owner : '', t0: x.t0, p, v, t_end: finite(x.t_end) ? x.t_end : null })
  }
  shots.sort((a, b) => a.t0 - b.t0)
  const events: TraceEvent[] = []
  for (const e of Array.isArray(o.events) ? o.events : []) {
    const x = obj(e)
    if (!finite(x.t) || typeof x.kind !== 'string') continue
    events.push({ t: x.t, kind: x.kind, vehicle: typeof x.vehicle === 'string' ? x.vehicle : null, by: typeof x.by === 'string' ? x.by : null })
  }
  events.sort((a, b) => a.t - b.t)
  const last = Math.max(...aircraft.map((c) => c.t[c.t.length - 1]))
  const ar = obj(o.arena)
  const kind = o.kind === 'duel' || o.kind === 'run' ? o.kind : 'challenge'
  return {
    id: typeof o.id === 'string' ? o.id : '',
    kind,
    title: typeof o.title === 'string' ? o.title : '',
    hz: finite(o.hz) && o.hz > 0 ? o.hz : 10,
    duration_s: finite(o.duration_s) && o.duration_s >= last ? o.duration_s : last,
    arena: finite(ar.radius) && ar.radius > 0 ? { radius: ar.radius, floor: finite(ar.floor) ? ar.floor : 0, ceiling: finite(ar.ceiling) ? ar.ceiling : 4 } : null,
    aircraft,
    shots,
    events,
  }
}

/** Index of the last sample at or before t (-1 before the first). */
export function sampleIndex(ts: readonly number[], t: number): number {
  let lo = 0
  let hi = ts.length - 1
  if (hi < 0 || t < ts[0]) return -1
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (ts[mid] <= t) lo = mid
    else hi = mid - 1
  }
  return lo
}

function lerpAngle(a: number, b: number, k: number): number {
  let d = (b - a) % (2 * Math.PI)
  if (d > Math.PI) d -= 2 * Math.PI
  if (d < -Math.PI) d += 2 * Math.PI
  return a + d * k
}

/**
 * The aircraft at time t: positions and attitude interpolated between samples (yaw the short way round), discrete
 * fields from the sample at or before t. Before the first sample it holds the first; after the last, the last.
 */
export function sampleAt(c: TraceCraft, t: number): TraceSample {
  const i = Math.max(0, sampleIndex(c.t, t))
  const j = Math.min(c.t.length - 1, i + 1)
  const span = c.t[j] - c.t[i]
  const k = j === i || span <= 0 ? 0 : Math.min(1, Math.max(0, (t - c.t[i]) / span))
  const lin = (a: number[]) => a[i] + (a[j] - a[i]) * k
  return {
    x: lin(c.x),
    y: lin(c.y),
    z: lin(c.z),
    roll: lerpAngle(c.roll[i], c.roll[j], k),
    pitch: lerpAngle(c.pitch[i], c.pitch[j], k),
    yaw: lerpAngle(c.yaw[i], c.yaw[j], k),
    throttle: lin(c.throttle),
    hp: c.hp[i],
    state: c.state[i],
    armed: c.armed[i],
  }
}

/** Shots in the air at t: fired at or before t, not yet ended by a hit and within the 2.5 s lifetime. */
export function shotsAt(shots: readonly TraceShot[], t: number, lifetime = 2.5): TraceShot[] {
  const out: TraceShot[] = []
  for (const s of shots) {
    if (s.t0 > t) break
    if (t - s.t0 > lifetime) continue
    if (s.t_end !== null && t >= s.t_end) continue
    out.push(s)
  }
  return out
}

/** The newest event at or before t within `window` seconds (for the replay bar caption). */
export function recentEvent(events: readonly TraceEvent[], t: number, window = 1.5): TraceEvent | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i]
    if (e.t > t) continue
    return t - e.t <= window ? e : null
  }
  return null
}

/** Stage ids of trace aircraft: prefixed so they never collide with a live aircraft of the same name. */
export const traceStageId = (craftId: string) => `trace:${craftId}`
