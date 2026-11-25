// Flight plans in the Timeline workspace (0.6.2, design 14): a plan is an ordered list of steps (take off, move,
// go to, hover, turn, land, return) with their settings. This is what the Timeline edits by default; the stick
// timeline of 0.6.1 stays behind a switch. The functions here are pure: the document, its edits, the estimate
// that lays the steps out on a time axis, and the safety volume the plan needs.
import type { PlanStep } from '@/api'

export const ACTIONS = ['takeoff', 'move', 'goto', 'hover', 'yaw', 'land', 'rtl'] as const
export type Action = (typeof ACTIONS)[number]
export type ParamKey = 'alt' | 'forward' | 'left' | 'up' | 'x' | 'y' | 'z' | 'heading' | 'seconds' | 'deg' | 'speed' | 'settle'

export interface Step {
  action: Action
  /** only the keys the step carries; a missing key is the default (0 for an offset) */
  p: Partial<Record<ParamKey, number>>
  note?: string
  /** comment and blank lines above the step in the code view */
  before?: string[]
}

export interface PlanDoc {
  name: string
  /** defaults of takeoff / move / goto; null leaves them to the aircraft (the ANetDU7 flies 0.5 m/s, 2 s) */
  speed: number | null
  settle: number | null
  frame: 'flu' | 'frd' | null
  steps: Step[]
  /** comment lines above the header */
  head?: string[]
  /** comment lines after the last step */
  tail?: string[]
}

/** The parameters a step can carry, in the order they are shown and written. */
export const PARAMS: Record<Action, readonly ParamKey[]> = {
  takeoff: ['alt', 'speed', 'settle'],
  move: ['forward', 'left', 'up', 'speed', 'settle'],
  goto: ['x', 'y', 'z', 'heading', 'speed', 'settle'],
  hover: ['seconds'],
  yaw: ['deg'],
  land: [],
  rtl: [],
}
/** [min, max, step, big step, unit] */
export const RANGE: Record<ParamKey, readonly [number, number, number, number, string]> = {
  alt: [0.2, 30, 0.1, 1, 'm'],
  forward: [-30, 30, 0.1, 1, 'm'],
  left: [-30, 30, 0.1, 1, 'm'],
  up: [-2, 2, 0.1, 0.5, 'm'],
  x: [-10000, 10000, 0.1, 1, 'm'],
  y: [-10000, 10000, 0.1, 1, 'm'],
  z: [-1000, 1000, 0.1, 1, 'm'],
  heading: [-360, 360, 5, 45, '°'],
  seconds: [0, 120, 0.5, 5, 's'],
  deg: [-360, 360, 5, 45, '°'],
  speed: [0.1, 5, 0.1, 0.5, 'm/s'],
  settle: [0, 10, 0.5, 1, 's'],
}
export const MAX_STEPS = 24
const REQUIRED: Partial<Record<Action, readonly ParamKey[]>> = { takeoff: ['alt'], goto: ['x', 'y', 'z'], hover: ['seconds'], yaw: ['deg'] }
const round = (n: number) => Math.round(n * 1000) / 1000 + 0

export function newStep(action: Action): Step {
  if (action === 'takeoff') return { action, p: { alt: 1.5 } }
  if (action === 'move') return { action, p: { forward: 1 } }
  if (action === 'goto') return { action, p: { x: 0, y: 0, z: 1.5 } }
  if (action === 'hover') return { action, p: { seconds: 3 } }
  if (action === 'yaw') return { action, p: { deg: 90 } }
  return { action, p: {} }
}

/** The parameter the arrow keys change when none was picked: the first one the step carries. */
export function primary(step: Step): ParamKey | null {
  const keys = PARAMS[step.action]
  return keys.find((k) => step.p[k] !== undefined && step.p[k] !== 0) ?? keys.find((k) => step.p[k] !== undefined) ?? keys[0] ?? null
}

export const clampParam = (key: ParamKey, n: number): number => round(Math.min(RANGE[key][1], Math.max(RANGE[key][0], n)))

// ---------------------------------------------------------------- edits (each returns a new document)

const withSteps = (doc: PlanDoc, steps: Step[]): PlanDoc => ({ ...doc, steps })

export function setParam(doc: PlanDoc, index: number, key: ParamKey, value: number | null): PlanDoc {
  const step = doc.steps[index]
  if (!step || !PARAMS[step.action].includes(key)) return doc
  const p = { ...step.p }
  if (value === null || !Number.isFinite(value)) {
    if (REQUIRED[step.action]?.includes(key)) return doc
    delete p[key]
  } else p[key] = clampParam(key, value)
  return withSteps(doc, doc.steps.map((s, i) => (i === index ? { ...s, p } : s)))
}
export function nudge(doc: PlanDoc, index: number, key: ParamKey, dir: 1 | -1, big: boolean): PlanDoc {
  const step = doc.steps[index]
  if (!step) return doc
  const r = RANGE[key]
  const base = step.p[key] ?? (key === 'speed' ? (doc.speed ?? 0.5) : key === 'settle' ? (doc.settle ?? 2) : 0)
  return setParam(doc, index, key, base + dir * (big ? r[3] : r[2]))
}
export function setAction(doc: PlanDoc, index: number, action: Action): PlanDoc {
  const step = doc.steps[index]
  if (!step || step.action === action) return doc
  const fresh = newStep(action)
  const kept = Object.fromEntries(Object.entries(step.p).filter(([k]) => PARAMS[action].includes(k as ParamKey)))
  return withSteps(doc, doc.steps.map((s, i) => (i === index ? { ...s, action, p: { ...fresh.p, ...kept } } : s)))
}
export function setNote(doc: PlanDoc, index: number, note: string): PlanDoc {
  const text = note.replace(/\s+/g, ' ').trim().slice(0, 60)
  return withSteps(doc, doc.steps.map((s, i) => (i === index ? { ...s, note: text || undefined } : s)))
}
/** Insert after `index` (-1: at the start). Returns the document and the new step's index. */
export function insertStep(doc: PlanDoc, index: number, step: Step): { doc: PlanDoc; index: number } {
  if (doc.steps.length >= MAX_STEPS) return { doc, index }
  const at = Math.min(doc.steps.length, Math.max(0, index + 1))
  return { doc: withSteps(doc, [...doc.steps.slice(0, at), step, ...doc.steps.slice(at)]), index: at }
}
export function removeSteps(doc: PlanDoc, indexes: number[]): PlanDoc {
  const gone = new Set(indexes)
  const left = doc.steps.filter((_, i) => !gone.has(i))
  return left.length ? withSteps(doc, left) : doc
}
export function moveStep(doc: PlanDoc, index: number, dir: 1 | -1): { doc: PlanDoc; index: number } {
  const to = index + dir
  if (!doc.steps[index] || to < 0 || to >= doc.steps.length) return { doc, index }
  const steps = [...doc.steps]
  ;[steps[index], steps[to]] = [steps[to], steps[index]]
  return { doc: withSteps(doc, steps), index: to }
}
export function duplicateStep(doc: PlanDoc, index: number): { doc: PlanDoc; index: number } {
  const step = doc.steps[index]
  return step ? insertStep(doc, index, { ...step, p: { ...step.p }, before: undefined }) : { doc, index }
}

// ---------------------------------------------------------------- what is sent to the aircraft

/** Steps of POST /vehicles/{id}/plan from `from` on, with the plan's defaults filled in. */
export function toApi(doc: PlanDoc, from = 0): PlanStep[] {
  return doc.steps.slice(from).map((s) => {
    const out: Record<string, unknown> = { action: s.action }
    const tracked = s.action === 'takeoff' || s.action === 'move' || s.action === 'goto'
    for (const key of PARAMS[s.action]) {
      const v = s.p[key]
      if (v === undefined) continue
      if (key === 'settle') out.settle_s = v
      else if (key === 'heading') out.yaw = v
      else out[key] = v
    }
    if (s.action === 'move') for (const k of ['forward', 'left', 'up'] as const) out[k] ??= 0
    if (tracked && out.speed === undefined && doc.speed !== null) out.speed = doc.speed
    if (tracked && out.settle_s === undefined && doc.settle !== null && out.speed !== undefined) out.settle_s = doc.settle
    if (doc.frame && (s.action === 'move' || s.action === 'goto' || s.action === 'yaw')) out.frame = doc.frame
    return out as unknown as PlanStep
  })
}

/** A plan of the API (a built-in or saved one) as a document. */
export function fromApi(name: string, steps: readonly Record<string, unknown>[]): PlanDoc {
  const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : undefined)
  const out: Step[] = []
  for (const raw of steps) {
    const action = (raw.action === 'hold' ? 'hover' : raw.action) as Action
    if (!ACTIONS.includes(action)) continue
    const p: Step['p'] = {}
    for (const key of PARAMS[action]) {
      const v = key === 'settle' ? num(raw.settle_s) : key === 'heading' ? num(raw.yaw) : num(raw[key])
      if (v !== undefined) p[key] = v
    }
    if (action === 'move') {
      if (num(raw.right) !== undefined && p.left === undefined) p.left = -num(raw.right)!
      if (num(raw.down) !== undefined && p.up === undefined) p.up = -num(raw.down)!
      for (const k of ['forward', 'left', 'up'] as const) if (p[k] === 0 && Object.keys(p).length > 1) delete p[k]
    }
    out.push({ ...newStep(action), p: { ...(Object.keys(p).length ? {} : newStep(action).p), ...p } })
  }
  // a speed every tracked step shares becomes the plan's default
  const speeds = out.filter((s) => PARAMS[s.action].includes('speed')).map((s) => s.p.speed)
  const shared = speeds.length && speeds.every((v) => v !== undefined && v === speeds[0]) ? (speeds[0] as number) : null
  if (shared !== null) for (const s of out) delete s.p.speed
  return { name, speed: shared, settle: null, frame: null, steps: out.length ? out : [newStep('takeoff'), newStep('land')] }
}

// ---------------------------------------------------------------- problems

export interface StepProblem {
  index: number
  key?: ParamKey
  en: string
  zh: string
}
export function check(doc: PlanDoc): StepProblem[] {
  const out: StepProblem[] = []
  if (doc.steps.length > MAX_STEPS) out.push({ index: MAX_STEPS, en: `A plan has at most ${MAX_STEPS} steps`, zh: `一个计划最多 ${MAX_STEPS} 步` })
  doc.steps.forEach((s, index) => {
    for (const key of PARAMS[s.action]) {
      const v = s.p[key]
      if (v === undefined) {
        if (REQUIRED[s.action]?.includes(key)) out.push({ index, key, en: `${s.action} needs ${key}`, zh: `${s.action} 需要 ${key}` })
        continue
      }
      const [lo, hi] = RANGE[key]
      if (!Number.isFinite(v) || v < lo || v > hi) out.push({ index, key, en: `${key} must be ${lo} to ${hi}`, zh: `${key} 须在 ${lo} 到 ${hi} 之间` })
    }
    if (s.action === 'move' && !(s.p.forward || s.p.left || s.p.up)) out.push({ index, en: 'move needs a distance', zh: 'move 需要一个距离' })
  })
  return out
}

// ---------------------------------------------------------------- estimate: where each step leads and how long it takes

const ACCEL = 0.5
const CLIMB = 0.5
const TAIL_S = 1.5 // the last decimetres on the position gain

export interface Leg {
  index: number
  t0: number
  seconds: number
  /** forward, left, up from the take-off point, in the heading the aircraft had there (metres) */
  from: [number, number, number]
  to: [number, number, number]
  /** turned since take-off, degrees counter-clockwise */
  heading: number
  /** position is not known before the flight (a goto without an aircraft to read the frame from) */
  unknown?: boolean
}
export interface Estimate {
  legs: Leg[]
  seconds: number
  radius: number
  height: number
}
export interface Origin {
  /** local ENU of the aircraft now and its compass heading in degrees */
  x: number
  y: number
  z: number
  headingDeg: number
}

function travel(distance: number, climb: number, cap: number): number {
  const level = distance <= 1e-6 ? 0 : (distance < (cap * cap) / ACCEL ? 2 * Math.sqrt(distance / ACCEL) : distance / cap + cap / ACCEL) + TAIL_S
  const vcap = Math.min(cap, CLIMB)
  const vertical = Math.abs(climb) <= 1e-6 ? 0 : Math.abs(climb) / vcap + vcap / ACCEL + TAIL_S
  return Math.max(level, vertical)
}

/** Walk the plan: positions relative to the take-off point and seconds per step (an estimate, not a promise). */
export function estimate(doc: PlanDoc, opts: { speed?: number; settle?: number; origin?: Origin | null; airborneAt?: number } = {}): Estimate {
  const legs: Leg[] = []
  let t = 0
  let pos: [number, number, number] = [0, 0, opts.airborneAt ?? 0]
  let heading = 0
  let radius = 0
  let height = pos[2]
  doc.steps.forEach((s, index) => {
    const cap = s.p.speed ?? doc.speed ?? opts.speed ?? 0.5
    const settle = s.p.settle ?? doc.settle ?? opts.settle ?? 2
    const from: [number, number, number] = [...pos]
    let seconds = 0
    let unknown = false
    if (s.action === 'takeoff') {
      const alt = s.p.alt ?? 1
      seconds = 4 + travel(0, alt - pos[2], cap) + settle
      pos = [pos[0], pos[1], Math.max(pos[2], alt)]
    } else if (s.action === 'move') {
      // the named directions mean the same in every control frame
      const f = s.p.forward ?? 0
      const l = s.p.left ?? 0
      const u = s.p.up ?? 0
      const h = (heading * Math.PI) / 180
      const df = f * Math.cos(h) - l * Math.sin(h)
      const dl = f * Math.sin(h) + l * Math.cos(h)
      seconds = travel(Math.hypot(f, l), u, cap) + settle
      pos = [pos[0] + df, pos[1] + dl, pos[2] + u]
    } else if (s.action === 'goto') {
      const o = opts.origin
      if (o) {
        // local ENU of the target against the aircraft's start, turned into its start heading
        const [gx, gy, gz] = doc.frame === 'frd' ? [s.p.y ?? 0, s.p.x ?? 0, -(s.p.z ?? 0)] : [s.p.x ?? 0, s.p.y ?? 0, s.p.z ?? 0]
        const de = gx - o.x
        const dn = gy - o.y
        const hr = (o.headingDeg * Math.PI) / 180
        const to: [number, number, number] = [de * Math.sin(hr) + dn * Math.cos(hr), -(de * Math.cos(hr) - dn * Math.sin(hr)), gz - o.z + (opts.airborneAt ?? 0)]
        seconds = travel(Math.hypot(to[0] - pos[0], to[1] - pos[1]), to[2] - pos[2], cap) + settle
        pos = to
      } else {
        unknown = true
        seconds = 8 + settle
      }
    } else if (s.action === 'hover') seconds = s.p.seconds ?? 0
    else if (s.action === 'yaw') {
      const deg = s.p.deg ?? 0
      heading += doc.frame === 'frd' ? -deg : deg
      seconds = Math.abs(deg) / 30 + 1.5
    } else if (s.action === 'land') {
      seconds = pos[2] / 0.7 + 4
      pos = [pos[0], pos[1], 0]
    } else if (s.action === 'rtl') {
      seconds = Math.hypot(pos[0], pos[1]) / 1.0 + pos[2] / 0.7 + 6
      pos = [0, 0, 0]
    }
    seconds = Math.max(0.5, round(seconds))
    legs.push({ index, t0: round(t), seconds, from, to: [...pos], heading, ...(unknown ? { unknown } : {}) })
    t += seconds
    radius = Math.max(radius, Math.hypot(pos[0], pos[1]))
    height = Math.max(height, pos[2])
  })
  return { legs, seconds: round(t), radius: round(radius), height: round(height) }
}

export interface Volume {
  x: number
  y: number
  z: number
}
/** The safety volume the plan needs: a box around the take-off point, whatever the heading (0.5 m to spare
 * level; the height is the backend's own rule: no higher than the volume). */
export function volumeFor(est: Estimate): Volume {
  const side = Math.ceil((est.radius + 0.5) * 2)
  return { x: Math.max(1, side), y: Math.max(1, side), z: Math.max(1, Math.ceil(est.height * 2 - 1e-9) / 2) }
}
export const fits = (need: Volume, have: Volume | null): boolean => !have || (need.x <= have.x + 1e-6 && need.y <= have.y + 1e-6 && need.z <= have.z + 1e-6)

// ---------------------------------------------------------------- templates

export function templateHop(): PlanDoc {
  return { name: 'Take off, hover, land', speed: null, settle: null, frame: null, steps: [{ action: 'takeoff', p: { alt: 1.5 } }, { action: 'hover', p: { seconds: 5 } }, { action: 'land', p: {} }] }
}
export function templateSquare(side = 2, alt = 2): PlanDoc {
  return {
    name: `${side} m square`,
    speed: 0.5,
    settle: null,
    frame: null,
    steps: [
      { action: 'takeoff', p: { alt } },
      { action: 'move', p: { forward: side } },
      { action: 'move', p: { left: side } },
      { action: 'move', p: { forward: -side } },
      { action: 'move', p: { left: -side } },
      { action: 'land', p: {} },
    ],
  }
}
export function templateOutAndBack(): PlanDoc {
  return {
    name: 'Out and back',
    speed: 0.5,
    settle: null,
    frame: null,
    steps: [{ action: 'takeoff', p: { alt: 2 } }, { action: 'move', p: { forward: 3 } }, { action: 'yaw', p: { deg: 180 } }, { action: 'move', p: { forward: 3 } }, { action: 'yaw', p: { deg: 180 } }, { action: 'land', p: {} }],
  }
}

/** One line of text for a step: "move forward 10 · left 2". */
export function describe(step: Step): string {
  const parts = PARAMS[step.action].filter((k) => step.p[k] !== undefined && k !== 'speed' && k !== 'settle').map((k) => (PARAMS[step.action].length === 1 || k === 'alt' ? `${step.p[k]}` : `${k} ${step.p[k]}`))
  return [step.action, parts.join(' · ')].filter(Boolean).join(' ')
}
