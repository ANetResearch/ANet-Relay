// Evaluation traces (06 §3.4): the 10 Hz downsampled flight of every aircraft in one judged run or duel round, plus
// the rounds fired, as GET /traces/{id} returns it. Pure: no React, no three.js (trace.test.ts); rounds fly
// arena/ballistics.
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
//     "events": [{"t": 3.6, "kind": "hit" | "down" | "finish", "vehicle": "b", "by": "a"},
//                {"t": 4.1, "kind": "collision", "vehicle": "a", "with": "b" | "ring:2", "point": [x, y, z],
//                 "v_rel": 2.4, "crashed": ["a", "b"] | true | false},           // v0.5 (07 §2, §5)
//                {"t": 4.1, "kind": "crash", "vehicle": "a", "point": [x, y, z], "reason": "ground" | "collision" | "shot_down"}]
//   }
//
// Times are seconds from the start of the run. Shots fly the arena integrator (arena/ballistics.ts, 1/240 s, drag
// and gravity) from (p, v) at t0 until t_end (a hit) or the 2.5 s lifetime, so the replay draws the same paths the
// evaluator flew without storing every bullet sample.
//
// The evaluator (backend relay.judge, trace version 1) writes a compact form that parseTrace converts to the above:
// a shared "t" column and a "fields" list, each aircraft's "frames" rows in the order of "fields" (angles in radians,
// "flags" bit 1 = armed), "states" as [t, name] changes, shots with "shooter" and no end time (the end is the hit
// event of that shooter whose point is nearest the round's path), and events with "shooter". See fromJudgeTrace.

import { flightAt } from '@/arena/ballistics'

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
  // v0.5 collision and crash events (07 §5); absent on older traces
  /** the other body of a collision: an aircraft id or "ring:<i>" */
  with?: string | null
  /** ENU metres in the arena frame */
  point?: [number, number, number] | null
  v_rel?: number | null
  /** aircraft a collision crashed */
  crashed?: string[]
  /** crash cause: 'ground' | 'collision' | 'shot_down' */
  reason?: string | null
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

const RAD = 180 / Math.PI

/**
 * Converts a version 1 evaluator trace (backend relay.judge: shared t column, frames rows in the order of fields,
 * radians) into the column form above (degrees). A body without "fields" and "t" is returned unchanged.
 */
export function fromJudgeTrace(o: Obj): Obj {
  if (!Array.isArray(o.fields) || !Array.isArray(o.t)) return o
  const fields = (o.fields as unknown[]).map(String)
  const col = (name: string) => fields.indexOf(name)
  const ts = o.t as unknown[]
  const angle = new Set(['roll', 'pitch', 'yaw'])
  const aircraft = (Array.isArray(o.aircraft) ? o.aircraft : []).map((a) => {
    const x = obj(a)
    const frames = Array.isArray(x.frames) ? (x.frames as unknown[]) : []
    const n = Math.min(ts.length, frames.length)
    const row = (i: number) => (Array.isArray(frames[i]) ? (frames[i] as unknown[]) : [])
    const column = (name: string): unknown[] => {
      const k = col(name)
      if (k < 0) return []
      return Array.from({ length: n }, (_, i) => {
        const v = row(i)[k]
        return angle.has(name) && finite(v) ? v * RAD : v
      })
    }
    const flags = col('flags')
    const armed = flags < 0 ? [] : Array.from({ length: n }, (_, i) => (finite(row(i)[flags]) ? ((row(i)[flags] as number) & 1) === 1 : null))
    // state names: [t, name] at every change, held until the next one
    const changes = (Array.isArray(x.states) ? x.states : []).filter((c): c is [number, string] => Array.isArray(c) && finite(c[0]) && typeof c[1] === 'string')
    let k = -1
    const state = Array.from({ length: n }, (_, i) => {
      const ti = ts[i]
      while (finite(ti) && k + 1 < changes.length && changes[k + 1][0] <= ti + 1e-6) k++
      return k >= 0 ? changes[k][1] : null
    })
    const samples: Record<string, unknown[]> = { t: ts.slice(0, n), state, armed }
    for (const c of ['x', 'y', 'z', 'roll', 'pitch', 'yaw', 'throttle', 'hp']) samples[c] = column(c)
    return { id: x.id, name: x.name ?? x.label, program: x.program, rev: x.rev, team: x.team, samples }
  })
  const events = (Array.isArray(o.events) ? o.events : []).map((e): Obj => {
    const x = obj(e)
    return { ...x, by: typeof x.by === 'string' ? x.by : x.shooter }
  })
  const shots = (Array.isArray(o.shots) ? o.shots : []).map((s): Obj => {
    const x = obj(s)
    return { ...x, id: x.id === undefined ? undefined : String(x.id), owner: typeof x.owner === 'string' ? x.owner : x.shooter, t_end: finite(x.t_end) ? x.t_end : null }
  })
  // a hit ends the round of that shooter in flight whose straight-line path passes nearest the hit point
  for (const e of events) {
    const te = e.t
    if (e.kind !== 'hit' || !finite(te)) continue
    const point = vec3(e.point)
    let best: Obj | null = null
    let bestD = Infinity
    for (const s of shots) {
      const p = vec3(s.p)
      const v = vec3(s.v)
      const t0 = s.t0
      if (!p || !v || !finite(t0) || s.owner !== e.by || s.t_end !== null || t0 > te || te - t0 > 2.5) continue
      const dt = te - t0
      const d = point ? Math.hypot(p[0] + v[0] * dt - point[0], p[1] + v[1] * dt - point[1], p[2] + v[2] * dt - point[2]) : dt
      if (d < bestD) {
        bestD = d
        best = s
      }
    }
    if (best) best.t_end = te
  }
  const label = typeof o.challenge === 'string' ? o.challenge : typeof o.scenario === 'string' ? o.scenario : ''
  const round = finite(o.round) ? ` · ${o.round}` : ''
  const seed = finite(o.seed) ? ` · #${o.seed}` : ''
  return { ...o, title: typeof o.title === 'string' ? o.title : `${label}${round}${seed}`, aircraft, shots, events }
}

/** Reads a trace body; null when it holds no aircraft with usable samples. */
export function parseTrace(raw: unknown): Trace | null {
  const top = obj(raw)
  const o = fromJudgeTrace(top.trace && typeof top.trace === 'object' ? obj(top.trace) : top)
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
    const ev: TraceEvent = { t: x.t, kind: x.kind, vehicle: typeof x.vehicle === 'string' ? x.vehicle : null, by: typeof x.by === 'string' ? x.by : null }
    if (x.kind === 'collision' || x.kind === 'crash') {
      // v0.5 (07 §5): `crashed` is a list of ids, or a flag meaning both bodies of the collision (rings never crash)
      const other = typeof x.with === 'string' ? x.with : null
      const both = [ev.vehicle, other && !other.startsWith('ring:') ? other : null].filter((id): id is string => id !== null)
      Object.assign(ev, {
        with: other,
        point: vec3(x.point),
        v_rel: finite(x.v_rel) ? x.v_rel : null,
        crashed: Array.isArray(x.crashed) ? x.crashed.filter((id): id is string => typeof id === 'string') : x.crashed === true ? both : [],
        reason: typeof x.reason === 'string' ? x.reason : null,
      })
    }
    events.push(ev)
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

/** Effect kinds of a trace replay (06 §11, 07 §5); the same voxel effects the live stage throws. */
export type TraceImpactKind = 'hit' | 'explosion' | 'spark' | 'dust'

/** Where something was hit, shot down, collided or crashed in a trace (ENU metres), for the replay's voxel effects. */
export interface TraceImpact {
  t: number
  kind: TraceImpactKind
  p: [number, number, number]
  /** stable per impact, so a burst looks the same every time the cursor passes it */
  key: string
}

/** A second explosion of one aircraft within this many seconds of the first is the same destruction reported twice. */
const EXPLOSION_DEDUPE_S = 1.2

const impactsOf = new WeakMap<Trace, TraceImpact[]>()

/**
 * Every impact of a trace, by time: the end of each round that ended on a hit (where the round was), a hit event
 * whose round is unknown (where the target was), an explosion for each shoot-down (where the aircraft went down) and
 * each aircraft a collision crashed, sparks at every collision point, and dust where an aircraft crashed on the
 * ground (or where a shot-down wreck landed). An aircraft explodes at most once per EXPLOSION_DEDUPE_S. Cached.
 */
export function traceImpacts(trace: Trace): TraceImpact[] {
  const hit = impactsOf.get(trace)
  if (hit) return hit
  const out: TraceImpact[] = []
  const ended = new Set<string>()
  for (const s of trace.shots) {
    if (s.t_end === null) continue
    const f = flightAt(s, s.t_end)
    out.push({ t: s.t_end, kind: 'hit', p: [f.p[0], f.p[1], Math.max(0, f.p[2])], key: `shot:${s.id}` })
    ended.add(`${s.owner}@${s.t_end}`)
  }
  const at = (id: string | null | undefined, t: number): [number, number, number] | null => {
    const craft = id ? trace.aircraft.find((c) => c.id === id) : undefined
    if (!craft || !craft.t.length) return null
    const s = sampleAt(craft, t)
    return [s.x, s.y, Math.max(0, s.z)]
  }
  const exploded = new Map<string, number>()
  const explode = (id: string, t: number, p: [number, number, number] | null, key: string) => {
    const last = exploded.get(id)
    if (!p || (last !== undefined && t - last <= EXPLOSION_DEDUPE_S)) return
    exploded.set(id, t)
    out.push({ t, kind: 'explosion', p, key })
  }
  trace.events.forEach((e, i) => {
    if (e.kind === 'hit') {
      if (ended.has(`${e.by}@${e.t}`)) return
      const p = at(e.vehicle, e.t)
      if (p) out.push({ t: e.t, kind: 'hit', p, key: `event:${i}` })
    } else if (e.kind === 'down') {
      if (e.vehicle) explode(e.vehicle, e.t, at(e.vehicle, e.t), `event:${i}`)
    } else if (e.kind === 'collision') {
      const p = e.point ?? at(e.vehicle, e.t)
      if (p) out.push({ t: e.t, kind: 'spark', p: [p[0], p[1], Math.max(0, p[2])], key: `event:${i}` })
      for (const id of e.crashed ?? []) explode(id, e.t, at(id, e.t) ?? p, `event:${i}:${id}`)
    } else if (e.kind === 'crash') {
      const p = e.point ?? at(e.vehicle, e.t)
      if (!p || !e.vehicle) return
      if (e.reason === 'collision') explode(e.vehicle, e.t, p, `event:${i}`)
      else out.push({ t: e.t, kind: 'dust', p: [p[0], p[1], Math.max(0, Math.min(p[2], 0.05))], key: `event:${i}` })
    }
  })
  out.sort((a, b) => a.t - b.t)
  impactsOf.set(trace, out)
  return out
}

/** Impacts whose effect is still showing at t (started at or before t, within `lifeS` of their kind). */
export function impactsAt(trace: Trace, t: number, lifeS: (kind: TraceImpactKind) => number): TraceImpact[] {
  return traceImpacts(trace).filter((m) => m.t <= t && t - m.t <= lifeS(m.kind))
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
