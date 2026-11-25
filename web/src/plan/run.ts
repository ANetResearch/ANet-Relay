// Running a flight plan from anywhere in the Timeline workspace (0.6.2): the step list, the code view and the
// command palette call the same functions. Run does what stands between the operator and the flight where that is
// safe (possess an idle simulator, put a disarmed simulator back on its spawn point) and asks in the transport bar
// where it is not: typed ARM for a real aircraft, possession of a real aircraft, taking an aircraft from an agent,
// and a safety volume that is too small for the plan.
import { create } from 'zustand'
import { api, type Vehicle } from '@/api'
import { notifyError } from '@/shell/notify'
import { useRelay } from '@/store/relay'
import { getVehicle } from '@/store/selectors'
import { useUi } from '@/store/ui'
import { toCode } from './code'
import { estimate, fits, toApi, volumeFor, type Volume } from './model'
import { usePlan, type RunRecord } from './store'

export const target = (): Vehicle | null => getVehicle(useUi.getState().selectedId)
const mine = (v: Vehicle | null) => v?.controller === 'human'

export interface TracePoint {
  /** step of the document and seconds into it */
  i: number
  s: number
  alt: number
  /** forward and left of the start point, in the start heading; null without a position */
  f: number | null
  l: number | null
}
export type Ask = null | { kind: 'confirm' | 'possess' | 'takeover' | 'volume'; from: number; need?: Volume; unlimited?: boolean }

/** The run this viewer started: which backend plan it is and how its steps map onto the document. */
interface Active {
  vehicle: string
  planId: number
  from: number
  startedAt: number
  text: string
  name: string
  real: boolean
  /** start position (local ENU) and compass heading in radians, for the path trace */
  origin: { x: number; y: number; z: number; heading: number } | null
  stepAt: number[]
}

interface RunState {
  busy: boolean
  ask: Ask
  error: string | null
  active: Active | null
  /** step of the document being flown and when it began (ms) */
  index: number | null
  indexAt: number
  trace: TracePoint[]
  /** seconds each finished step of this run took */
  took: Record<number, number>
  ghost: { trace: TracePoint[]; took: Record<number, number> }
  setAsk(ask: Ask): void
}
export const useRun = create<RunState>((set) => ({
  busy: false,
  ask: null,
  error: null,
  active: null,
  index: null,
  indexAt: 0,
  trace: [],
  took: {},
  ghost: { trace: [], took: {} },
  setAsk: (ask) => set({ ask, error: null }),
}))

export interface PlanFlight {
  /** this aircraft is flying a plan (any plan) */
  running: boolean
  /** it is the plan this viewer started from the Timeline */
  ours: boolean
  /** step of the document in flight */
  index: number | null
  count: number
  state: string | null
  detail: string | null
  name: string | null
}
export function flightOf(v: Vehicle | null, active: Active | null): PlanFlight {
  const p = v?.plan ?? null
  if (!v || !p) return { running: false, ours: false, index: null, count: 0, state: null, detail: null, name: null }
  const ours = Boolean(active && active.vehicle === v.id && active.planId === p.id)
  const running = p.state === 'running'
  return { running, ours, index: running ? (ours ? active!.from : 0) + p.index : null, count: p.count, state: p.state, detail: p.detail, name: p.name }
}

const sleep = (ms: number) => new Promise((r) => window.setTimeout(r, ms))
async function until(cond: () => boolean, ms: number): Promise<boolean> {
  const end = Date.now() + ms
  while (Date.now() < end) {
    if (cond()) return true
    await sleep(60)
  }
  return cond()
}

/** Why the plan cannot fly on this aircraft at all, or null. */
export function blocked(v: Vehicle | null): 'noSkills' | null {
  return v && !v.capabilities.althold ? 'noSkills' : null
}

/** `unlimited`: the operator's separate trigger (14 §6): the plan flies without the safety volume. */
export async function runPlan(from = 0, opts: { confirm?: string; takeover?: boolean; volume?: boolean; unlimited?: boolean } = {}): Promise<void> {
  const run = useRun
  const plan = usePlan.getState()
  const v = target()
  if (!v || run.getState().busy || plan.problems.length || blocked(v)) return
  if (v.plan?.state === 'running') return // Stop first
  const doc = plan.doc
  if (!doc.steps[from]) return
  const id = v.id
  run.setState({ busy: true, error: null })
  try {
    if (!mine(v)) {
      if (v.controller === 'agent' && !opts.takeover) return run.setState({ ask: { kind: 'takeover', from, unlimited: opts.unlimited } })
      if (v.kind === 'real' && !opts.takeover) return run.setState({ ask: { kind: 'possess', from, unlimited: opts.unlimited } })
    }
    // the safety volume: a plan that leaves it is refused step by step in flight, so ask before the take-off
    const have = useRelay.getState().fleet?.safety_volume ?? null
    const need = volumeFor(estimate(doc))
    if (!opts.unlimited && !fits(need, have)) {
      if (!opts.volume) return run.setState({ ask: { kind: 'volume', from, need } })
      const next = { x: Math.max(need.x, have?.x ?? 0), y: Math.max(need.y, have?.y ?? 0), z: Math.max(need.z, have?.z ?? 0) }
      await api.putSettings({ safety_volume: next })
      await until(() => fits(need, useRelay.getState().fleet?.safety_volume ?? null), 1500)
    }
    const first = doc.steps[from].action
    if (v.kind === 'real' && !v.armed && first === 'takeoff' && opts.confirm !== 'ARM') return run.setState({ ask: { kind: 'confirm', from, unlimited: opts.unlimited } })
    if (v.kind === 'sim' && plan.prefs.reset && from === 0 && first === 'takeoff' && v.sim?.engine !== 'sitl' && (v.crashed || !v.armed)) {
      await api.resetSim(id)
      await sleep(600)
    }
    if (!mine(getVehicle(id))) {
      await api.possess(id, { input: 'keyboard' })
      await until(() => mine(getVehicle(id)), 1500)
    }
    const now = getVehicle(id)
    const out = (await api.plan(id, toApi(doc, from), { name: doc.name.slice(0, 80) || 'plan', ...(opts.confirm ? { confirm: opts.confirm } : {}), ...(opts.unlimited ? { unlimited: true } : {}) })) as { plan?: { id?: number } }
    const pos = now?.position ?? null
    const prev = run.getState().trace
    run.setState({
      ask: null,
      active: {
        vehicle: id,
        planId: Number(out.plan?.id ?? -1),
        from,
        startedAt: Date.now(),
        text: toCode(doc).text,
        name: doc.name,
        real: v.kind === 'real',
        origin: pos ? { x: pos.x, y: pos.y, z: pos.z, heading: ((now?.heading_deg ?? 0) * Math.PI) / 180 } : null,
        stepAt: [],
      },
      index: from,
      indexAt: Date.now(),
      ghost: prev.length ? { trace: prev, took: run.getState().took } : run.getState().ghost,
      trace: [],
      took: {},
    })
  } catch (e) {
    if (run.getState().ask) run.setState({ error: e instanceof Error ? e.message : String(e) })
    notifyError(e)
  } finally {
    run.setState({ busy: false })
  }
}

/** Cancel the plan: an airborne aircraft holds where it is. */
export function stopPlan(): void {
  const v = target()
  if (v?.plan?.state === 'running') api.cancelPlan(v.id).catch(notifyError)
}

/** Follow the run from the fleet stream: the step in flight, the trace, and the record once it ends. */
export function watchRuns(): () => void {
  // another plan was opened: the traces of the old one say nothing about it
  const offPlan = usePlan.subscribe((now, before) => {
    if (now.cleanText !== before.cleanText && now.doc !== before.doc && !useRun.getState().active) useRun.setState({ trace: [], took: {}, ghost: { trace: [], took: {} } })
  })
  const offFleet = useRelay.subscribe(() => {
    const s = useRun.getState()
    const a = s.active
    if (!a) return
    const v = getVehicle(a.vehicle)
    const p = v?.plan ?? null
    if (!v || !p || p.id !== a.planId) {
      if (Date.now() - a.startedAt > 3000) useRun.setState({ active: null, index: null })
      return
    }
    const now = Date.now()
    const index = a.from + p.index
    if (p.state === 'running') {
      let since = s.indexAt
      if (index !== s.index) {
        a.stepAt[index] = now
        useRun.setState({ index, indexAt: now, took: s.index === null ? s.took : { ...s.took, [s.index]: (now - s.indexAt) / 1000 } })
        since = now
      }
      const sec = (now - since) / 1000
      const last = s.trace[s.trace.length - 1]
      if (v.altitude_m != null && (!last || last.i !== index || sec - last.s >= 0.2)) {
        let f: number | null = null
        let l: number | null = null
        if (a.origin && v.position) {
          const de = v.position.x - a.origin.x
          const dn = v.position.y - a.origin.y
          const h = a.origin.heading
          f = de * Math.sin(h) + dn * Math.cos(h)
          l = -(de * Math.cos(h) - dn * Math.sin(h))
        }
        useRun.setState({ trace: [...useRun.getState().trace, { i: index, s: sec, alt: v.altitude_m, f, l }] })
      }
      return
    }
    // the plan ended: keep what happened
    const doc = usePlan.getState().doc
    const results: (string | null)[] = doc.steps.map(() => null)
    const seconds: (number | null)[] = doc.steps.map(() => null)
    p.results.forEach((r, i) => {
      results[a.from + i] = r ?? 'done'
      const t0 = i === 0 ? a.startedAt : a.stepAt[a.from + i]
      const t1 = a.stepAt[a.from + i + 1] ?? now
      if (t0) seconds[a.from + i] = Math.round((t1 - t0) / 100) / 10
    })
    if (p.state !== 'done' && results[a.from + p.index] === null && a.from + p.index < results.length) results[a.from + p.index] = `✗ ${p.detail ?? p.state}`
    const record: RunRecord = { at: a.startedAt, name: a.name, aircraft: v.label, real: a.real, text: a.text, from: a.from, state: p.state as RunRecord['state'], detail: p.detail, results, seconds }
    usePlan.getState().addRun(record)
    useRun.setState({ active: null, index: null, took: s.index === null ? s.took : { ...s.took, [s.index]: (now - s.indexAt) / 1000 } })
  })
  return () => {
    offPlan()
    offFleet()
  }
}
