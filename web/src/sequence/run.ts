// Running a stick timeline from anywhere in the Timeline workspace (0.6.1): the tracks, the code view and the
// command palette call the same functions, so "change a number, run, watch, stop" never needs a view change.
import { create } from 'zustand'
import { api, type Vehicle } from '@/api'
import { notifyError } from '@/shell/notify'
import { getVehicle } from '@/store/selectors'
import { useUi } from '@/store/ui'
import { isManualMode, type ArsDoc, type TrackName } from './model'
import { useSequence, type Selection } from './store'

export interface Flight {
  flying: boolean
  progress: number | null
  phase: string | null
  state: string | null
  detail: string | null
}

export function flightOf(v: Vehicle | null): Flight {
  const skill = v?.skill && v.skill.name === 'sequence' ? v.skill : null
  if (!v || !skill) return { flying: false, progress: null, phase: null, state: null, detail: null }
  const holding = skill.state === 'done' && v.armed
  const flying = skill.state === 'running' || holding
  return { flying, progress: flying ? (skill.progress_s ?? null) : null, phase: skill.phase ?? null, state: skill.state, detail: skill.detail }
}

export const target = (): Vehicle | null => getVehicle(useUi.getState().selectedId)
const mine = (v: Vehicle | null) => v?.controller === 'human'

// ---------------------------------------------------------------- edits reach the aircraft while it flies

let pushTimer: number | null = null
/** A committed edit: kept for undo, and sent to the aircraft when it is flying this sequence (PATCH /sequence). */
export function commitDoc(next: ArsDoc, sel?: Selection | null): void {
  useSequence.getState().edit(next, sel === undefined ? undefined : { sel })
  pushLive()
}
export function pushLive(delay = 80): void {
  const v = target()
  if (!v || !mine(v) || !flightOf(v).flying) return
  if (pushTimer) window.clearTimeout(pushTimer)
  pushTimer = window.setTimeout(() => {
    pushTimer = null
    const s = useSequence.getState()
    if (s.problems.length) return
    api.editSequence(v.id, { sequence: s.doc }).then(() => useRun.getState().markSynced(), notifyError)
  }, delay)
}
export function pushTrim(trim: Record<TrackName, number>): void {
  const v = target()
  if (v && mine(v) && flightOf(v).flying) api.editSequence(v.id, { trim }).then(() => useRun.getState().markSynced(), notifyError)
}
export function undo(): void {
  useSequence.getState().undo()
  pushLive()
}
export function redo(): void {
  useSequence.getState().redo()
  pushLive()
}

// ---------------------------------------------------------------- run state shared by the transport and the keys

export interface TracePoint {
  t: number
  alt: number
}
/** What the transport must ask before a run can start. */
export type Ask = null | { kind: 'confirm' | 'possess' | 'takeover'; startAt: number }

interface RunState {
  busy: boolean
  ask: Ask
  error: string | null
  syncedAt: number
  /** altitude against sequence time of the current run and of the one before it */
  trace: TracePoint[]
  ghost: TracePoint[]
  setAsk(ask: Ask): void
  markSynced(): void
  sample(t: number, alt: number): void
}
export const useRun = create<RunState>((set, get) => ({
  busy: false,
  ask: null,
  error: null,
  syncedAt: 0,
  trace: [],
  ghost: [],
  setAsk: (ask) => set({ ask, error: null }),
  markSynced: () => set({ syncedAt: Date.now() }),
  sample: (t, alt) => {
    const trace = get().trace
    const last = trace[trace.length - 1]
    if (last && t - last.t < 0.09) return
    set({ trace: [...trace, { t, alt }] })
  },
}))

const sleep = (ms: number) => new Promise((r) => window.setTimeout(r, ms))
async function until(cond: () => boolean, ms: number): Promise<boolean> {
  const end = Date.now() + ms
  while (Date.now() < end) {
    if (cond()) return true
    await sleep(60)
  }
  return cond()
}

/** Can this aircraft fly the document at all (a simulator without hover assist flies stick modes only)? */
export function blocked(v: Vehicle | null, doc: ArsDoc): boolean {
  return Boolean(v?.kind === 'sim' && v.sim && !v.sim.hover_assist && !isManualMode(doc.mode))
}

/**
 * Run the working copy on the selected aircraft. Does what stands between the operator and the run by itself where
 * that is safe (possess an idle simulator, put a disarmed or crashed simulator back on its spawn point) and asks in
 * the transport bar where it is not (typed ARM for a real aircraft on the ground, possession of a real aircraft,
 * taking an aircraft from an agent).
 */
export async function runSequence(startAt = 0, opts: { confirm?: string; takeover?: boolean } = {}): Promise<void> {
  const run = useRun
  const seq = useSequence.getState()
  const v = target()
  if (!v || run.getState().busy) return
  if (seq.problems.length || blocked(v, seq.doc)) return
  if (flightOf(v).flying && startAt === 0 && !opts.confirm) return // already flying: Stop first
  const id = v.id
  run.setState({ busy: true, error: null })
  try {
    if (!mine(v)) {
      if (v.controller === 'agent' && !opts.takeover) return run.setState({ ask: { kind: 'takeover', startAt } })
      if (v.kind === 'real' && !opts.takeover) return run.setState({ ask: { kind: 'possess', startAt } })
    }
    if (v.kind === 'real' && !v.armed && opts.confirm !== 'ARM') return run.setState({ ask: { kind: 'confirm', startAt } })
    // the reset comes first: it ends whatever controlled the simulator, and the autopilot it restarts needs a
    // moment to report its mode again (a stale mode would abort the sequence as "mode changed")
    if (v.kind === 'sim' && seq.prefs.reset && startAt === 0 && v.sim?.engine !== 'sitl' && (v.crashed || !v.armed)) {
      await api.resetSim(id)
      await sleep(600)
    }
    if (!mine(getVehicle(id))) {
      await api.possess(id, { input: 'keyboard' })
      await until(() => mine(getVehicle(id)), 1500)
    }
    const doc = useSequence.getState().doc
    const speed = seq.prefs.speed
    await api.runSequence(id, doc, { ...(opts.confirm ? { confirm: opts.confirm } : {}), ...(speed !== 1 ? { speed } : {}), ...(startAt > 0 ? { start_at: startAt } : {}) })
    useSequence.getState().setLastRun(doc)
    const prev = run.getState().trace
    run.setState({ ask: null, ghost: prev.length ? prev : run.getState().ghost, trace: [] })
  } catch (e) {
    if (run.getState().ask) run.setState({ error: e instanceof Error ? e.message : String(e) })
    notifyError(e)
  } finally {
    run.setState({ busy: false })
  }
}

export function stopSequence(): void {
  const v = target()
  if (v && flightOf(v).flying) api.stopSequence(v.id).catch(notifyError)
}
