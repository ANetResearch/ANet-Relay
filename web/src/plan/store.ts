// Flight-plan side of the Timeline workspace (0.6.2): the plan being edited (kept per viewer as text), the
// selected steps and the parameter the arrow keys change, undo, the view preferences, the saved plans (kept by
// the backend, so agents can fly them too) and the last runs of this viewer.
import { create } from 'zustand'
import { api, type SavedPlan } from '@/api'
import { parseCode, toCode, type CodeProblem } from './code'
import { PARAMS, primary, templateSquare, toApi, type ParamKey, type PlanDoc } from './model'

const KEY = 'relay.plan.doc'
const VIEW_KEY = 'relay.plan.view'
const RUNS_KEY = 'relay.plan.runs'
const KIND_KEY = 'relay.timeline.kind'
const UNDO_LIMIT = 80
const RUN_LIMIT = 12

export type PlanViewMode = 'steps' | 'split' | 'code'
export type TimelineKind = 'plan' | 'sticks'
export interface PlanPrefs {
  view: PlanViewMode
  /** a disarmed or crashed simulator goes back to its spawn point before each run */
  reset: boolean
  band: number
  split: number
}
/** One flight of a plan from this viewer: what was flown and how each step ended. */
export interface RunRecord {
  at: number
  name: string
  aircraft: string
  real: boolean
  text: string
  from: number
  state: 'done' | 'failed' | 'cancelled'
  detail: string | null
  /** per step of the document: the result text, null while not flown */
  results: (string | null)[]
  /** seconds each flown step took */
  seconds: (number | null)[]
}

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw ? { ...fallback, ...JSON.parse(raw) } : fallback
  } catch {
    return fallback
  }
}
function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, typeof value === 'string' ? value : JSON.stringify(value))
  } catch {
    // storage may be unavailable: the plan still works for this session
  }
}
function firstDoc(): PlanDoc {
  try {
    const raw = localStorage.getItem(KEY)
    const out = raw ? parseCode(raw) : null
    if (out?.ok) return out.doc
  } catch {
    // fall through
  }
  return { ...templateSquare(1, 1.5), name: 'Square' }
}
function firstRuns(): RunRecord[] {
  try {
    const raw = JSON.parse(localStorage.getItem(RUNS_KEY) ?? '[]')
    return Array.isArray(raw) ? raw.slice(0, RUN_LIMIT) : []
  } catch {
    return []
  }
}
function firstKind(): TimelineKind {
  try {
    return localStorage.getItem(KIND_KEY) === 'sticks' ? 'sticks' : 'plan'
  } catch {
    return 'plan'
  }
}

interface PlanState {
  kind: TimelineKind
  /** true for one tick while the kind changes: the two workspaces never swap inside one render */
  swapping: boolean
  doc: PlanDoc
  /** primary selected step and the others of a multi-selection */
  sel: number | null
  more: number[]
  /** the parameter of the primary step the keys change */
  param: ParamKey | null
  past: PlanDoc[]
  future: PlanDoc[]
  /** the document before a burst of transient edits (typing, dragging): the burst is one undo step */
  burst: PlanDoc | null
  prefs: PlanPrefs
  problems: CodeProblem[]
  saved: SavedPlan[]
  /** text of the plan as last saved or loaded: the dirty mark compares against it */
  cleanText: string
  runs: RunRecord[]
  setKind(kind: TimelineKind): void
  edit(doc: PlanDoc, opts?: { transient?: boolean; sel?: number | null }): void
  load(doc: PlanDoc): void
  select(sel: number | null, how?: 'one' | 'toggle' | 'range'): void
  setParam(param: ParamKey | null): void
  setPrefs(p: Partial<PlanPrefs>): void
  setProblems(p: CodeProblem[]): void
  undo(): void
  redo(): void
  refreshSaved(): Promise<void>
  save(id: string): Promise<SavedPlan>
  removeSaved(id: string): Promise<void>
  addRun(run: RunRecord): void
}

const keep = (doc: PlanDoc) => write(KEY, toCode(doc).text)
const validParam = (doc: PlanDoc, sel: number | null, param: ParamKey | null): ParamKey | null => {
  const step = sel === null ? null : doc.steps[sel]
  if (!step) return null
  return param && PARAMS[step.action].includes(param) ? param : primary(step)
}

export const usePlan = create<PlanState>((set, get) => {
  const doc = firstDoc()
  return {
    kind: firstKind(),
    swapping: false,
    doc,
    sel: null,
    more: [],
    param: null,
    past: [],
    future: [],
    burst: null,
    prefs: read<PlanPrefs>(VIEW_KEY, { view: 'steps', reset: true, band: 260, split: 0.42 }),
    problems: [],
    saved: [],
    cleanText: toCode(doc).text,
    runs: firstRuns(),
    setKind: (kind) => {
      if (kind === get().kind) return
      // whatever has the focus commits (and lets go) before its workspace is taken down
      if (typeof document !== 'undefined') (document.activeElement as HTMLElement | null)?.blur?.()
      write(KIND_KEY, kind)
      set({ kind, swapping: true })
      window.setTimeout(() => set({ swapping: false }), 0)
    },
    edit: (next, opts) => {
      const s = get()
      const sel = opts?.sel === undefined ? (s.sel !== null && s.sel < next.steps.length ? s.sel : null) : opts.sel
      const more = opts?.sel === undefined ? s.more.filter((i) => i < next.steps.length) : []
      const base = { doc: next, sel, more, param: validParam(next, sel, s.param) }
      if (opts?.transient) return set({ ...base, burst: s.burst ?? s.doc })
      keep(next)
      const origin = s.burst ?? s.doc
      if (next === origin) return set({ ...base, burst: null })
      set({ ...base, burst: null, past: [...s.past.slice(-UNDO_LIMIT + 1), origin], future: [] })
    },
    load: (next) => {
      keep(next)
      const s = get()
      set({ doc: next, sel: null, more: [], param: null, burst: null, past: [...s.past.slice(-UNDO_LIMIT + 1), s.burst ?? s.doc], future: [], problems: [], cleanText: toCode(next).text })
    },
    select: (sel, how = 'one') => {
      const s = get()
      if (sel === null) return set({ sel: null, more: [], param: null })
      if (how === 'toggle' && s.sel !== null) {
        if (sel === s.sel) {
          const [next, ...rest] = [...s.more].reverse()
          return set({ sel: next ?? null, more: rest.reverse(), param: validParam(s.doc, next ?? null, s.param) })
        }
        if (s.more.includes(sel)) return set({ more: s.more.filter((i) => i !== sel) })
        return set({ sel, more: [...s.more, s.sel], param: validParam(s.doc, sel, s.param) })
      }
      if (how === 'range' && s.sel !== null) {
        const anchor = s.more[0] ?? s.sel
        const lo = Math.min(anchor, sel)
        const hi = Math.max(anchor, sel)
        const all = Array.from({ length: hi - lo + 1 }, (_, i) => lo + i).filter((i) => i !== sel)
        return set({ sel, more: [anchor, ...all.filter((i) => i !== anchor)].filter((i) => i !== sel), param: validParam(s.doc, sel, s.param) })
      }
      set({ sel, more: [], param: validParam(s.doc, sel, sel === s.sel ? s.param : null) })
    },
    setParam: (param) => set({ param: validParam(get().doc, get().sel, param) }),
    setPrefs: (p) => {
      const prefs = { ...get().prefs, ...p }
      write(VIEW_KEY, prefs)
      set({ prefs })
    },
    setProblems: (problems) => set({ problems }),
    undo: () => {
      const s = get()
      const prev = s.past[s.past.length - 1]
      if (!prev) return
      keep(prev)
      const sel = s.sel !== null && s.sel < prev.steps.length ? s.sel : null
      set({ doc: prev, past: s.past.slice(0, -1), future: [s.doc, ...s.future], sel, more: [], param: validParam(prev, sel, s.param), problems: [] })
    },
    redo: () => {
      const s = get()
      const next = s.future[0]
      if (!next) return
      keep(next)
      const sel = s.sel !== null && s.sel < next.steps.length ? s.sel : null
      set({ doc: next, past: [...s.past, s.doc], future: s.future.slice(1), sel, more: [], param: validParam(next, sel, s.param), problems: [] })
    },
    refreshSaved: async () => {
      const out = await api.plansSaved()
      set({ saved: Array.isArray(out?.plans) ? out.plans : [] })
    },
    save: async (id) => {
      const doc = get().doc
      const text = toCode(doc).text
      const out = await api.savePlan(id, { name: doc.name, steps: toApi(doc), source: text })
      set({ cleanText: text })
      await get().refreshSaved().catch(() => undefined)
      return out.plan
    },
    removeSaved: async (id) => {
      await api.deleteSavedPlan(id)
      set({ saved: get().saved.filter((p) => p.id !== id) })
    },
    addRun: (run) => {
      const runs = [run, ...get().runs].slice(0, RUN_LIMIT)
      write(RUNS_KEY, runs)
      set({ runs })
    },
  }
})

export const isDirty = (s: PlanState): boolean => toCode(s.doc).text !== s.cleanText
/** A saved plan's id from its name (the backend's rule: lower case, digits, - and _). */
export function planId(name: string): string {
  const slug = name.trim().toLowerCase().replace(/[^a-z0-9_]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 48).replace(/-+$/, '')
  return /^[a-z0-9][a-z0-9_-]*$/.test(slug) ? slug : 'plan'
}
