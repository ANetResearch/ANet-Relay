// Lab state (06 §7): the program list, the program open in the editor with its unsaved draft (source, parameter
// overrides, unit tests), the latest validation, the right-hand tab and the judge / duel jobs. One store outside
// React so the editor, the tabs and the stage label read the same draft. The draft of the open program is also
// kept in localStorage (per window profile, a convenience only) so a reload does not lose typing.
import { create } from 'zustand'
import type { Program, ProgramSummary, ProgramValidation } from '@/api'
import { notify, notifyError } from '@/shell/notify'
import { labApi, type JobView, type ProgramTestCase } from './api'
import { tL } from './copy'

export type LabTab = 'params' | 'tests' | 'run' | 'judge' | 'duel' | 'history'
export const LAB_TABS: readonly LabTab[] = ['params', 'tests', 'run', 'judge', 'duel', 'history']

export interface LabState {
  programs: ProgramSummary[] | null
  listError: unknown
  selectedId: string | null
  /** the saved program open in the editor */
  program: Program | null
  loading: boolean
  /** editor text and the other unsaved fields */
  source: string
  params: Record<string, unknown>
  tests: ProgramTestCase[]
  /** latest validation of `source` (06 §2.4) and the text it belongs to */
  validation: ProgramValidation | null
  validatedSource: string | null
  saving: boolean
  tab: LabTab
  challengeId: string | null
  /** aircraft chosen on the Run tab */
  runVehicle: string | null
  judgeJob: JobView | null
  duelJob: JobView | null
  /** bumped when the editor must take `source` from the store (load, restore) */
  loadSeq: number

  refresh(): Promise<void>
  /** reloads the list without changing the selection */
  refreshList(): Promise<void>
  open(id: string): Promise<void>
  setSource(s: string): void
  setParams(p: Record<string, unknown>): void
  setTests(t: ProgramTestCase[]): void
  setValidation(source: string, v: ProgramValidation | null): void
  save(note?: string): Promise<boolean>
  revert(): void
  restore(source: string, params: Record<string, unknown>, rev: number): void
  createNew(): Promise<void>
  duplicate(): Promise<void>
  remove(): Promise<void>
  setTab(t: LabTab): void
  setChallenge(id: string | null): void
  setRunVehicle(id: string | null): void
  setJudgeJob(j: JobView | null): void
  setDuelJob(j: JobView | null): void
}

const DRAFT_KEY = 'relay.lab.draft'
const TAB_KEY = 'relay.lab.tab'

interface StoredDraft {
  id: string
  rev: number
  source: string
  params: Record<string, unknown>
  tests: ProgramTestCase[]
}

function readDraft(): StoredDraft | null {
  try {
    const raw = localStorage.getItem(DRAFT_KEY)
    const d = raw ? (JSON.parse(raw) as StoredDraft) : null
    return d && typeof d.id === 'string' && typeof d.source === 'string' ? d : null
  } catch {
    return null
  }
}

function writeDraft(d: StoredDraft | null): void {
  try {
    if (d) localStorage.setItem(DRAFT_KEY, JSON.stringify(d))
    else localStorage.removeItem(DRAFT_KEY)
  } catch {
    /* storage unavailable: the draft stays in memory */
  }
}

function readTab(): LabTab {
  try {
    const t = localStorage.getItem(TAB_KEY)
    return LAB_TABS.find((x) => x === t) ?? 'tests'
  } catch {
    return 'tests'
  }
}

/** Unit tests as stored (06 §3.1), with the optional fields only when present. */
export function testsOf(p: Program | null): ProgramTestCase[] {
  return ((p?.tests ?? []) as Record<string, unknown>[]).map((x, i) => ({
    name: typeof x.name === 'string' ? x.name : `test ${i + 1}`,
    obs: (x.obs as Record<string, unknown>) ?? {},
    expect: (x.expect as Record<string, unknown>) ?? {},
    ...(x.mem ? { mem: x.mem as Record<string, unknown> } : {}),
    ...(typeof x.state === 'string' ? { state: x.state } : {}),
    ...(x.params ? { params: x.params as Record<string, unknown> } : {}),
  }))
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

/** Unsaved changes of the open program. */
export function isDirty(s: Pick<LabState, 'program' | 'source' | 'params' | 'tests'>): boolean {
  const p = s.program
  if (!p || p.builtin) return false
  return s.source !== p.source || !same(s.params, p.params ?? {}) || !same(s.tests, testsOf(p))
}

/** Starting point of a new program (06 §2.1). */
export const NEW_SOURCE = `PARAMS = {"target_alt": 1.0, "hover": 0.5, "kp": 0.3, "kd": 0.15}


def init(ctx):
    ctx.state = "climb"


def step(obs, ctx):
    p = ctx.params
    if not obs.armed or obs.alt is None:
        ctx.state = "idle"
        return Sticks(throttle=0.0)
    error = p["target_alt"] - obs.alt
    ctx.state = "hold" if abs(error) < 0.1 else "climb"
    throttle = p["hover"] + p["kp"] * error - p["kd"] * obs.vz
    return Sticks(throttle=clamp(throttle, 0.0, 1.0))
`

let draftTimer: ReturnType<typeof setTimeout> | null = null

export const useLab = create<LabState>()((set, get) => {
  const persistDraft = () => {
    if (draftTimer) clearTimeout(draftTimer)
    draftTimer = setTimeout(() => {
      const s = get()
      if (s.program && isDirty(s)) writeDraft({ id: s.program.id, rev: s.program.rev, source: s.source, params: s.params, tests: s.tests })
      else if (s.program && readDraft()?.id === s.program.id) writeDraft(null)
    }, 300)
  }

  const load = (p: Program, restore = true) => {
    let source = p.source
    let params = { ...((p.params ?? {}) as Record<string, unknown>) }
    let tests = testsOf(p)
    const d = restore ? readDraft() : null
    if (d && d.id === p.id && d.rev === p.rev && !p.builtin && (d.source !== source || !same(d.params, params) || !same(d.tests, tests))) {
      source = d.source
      params = d.params ?? params
      tests = d.tests ?? tests
      notify(tL('lab.draftRestored'))
    }
    set((s) => ({ program: p, selectedId: p.id, source, params, tests, loading: false, loadSeq: s.loadSeq + 1 }))
  }

  return {
    programs: null,
    listError: null,
    selectedId: null,
    program: null,
    loading: false,
    source: '',
    params: {},
    tests: [],
    validation: null,
    validatedSource: null,
    saving: false,
    tab: readTab(),
    challengeId: null,
    runVehicle: null,
    judgeJob: null,
    duelJob: null,
    loadSeq: 0,

    refresh: async () => {
      try {
        const res = await labApi.programs()
        set({ programs: res.programs ?? [], listError: null })
        const s = get()
        const list = res.programs ?? []
        if (!s.selectedId || !list.some((p) => p.id === s.selectedId)) {
          const first = list.find((p) => !p.builtin) ?? list[0]
          if (first) await get().open(first.id)
        }
      } catch (e) {
        set({ listError: e })
      }
    },

    open: async (id) => {
      set({ selectedId: id, loading: true })
      try {
        const res = await labApi.program(id)
        if (get().selectedId !== id) return
        load(res.program)
      } catch (e) {
        // the list points back at the program the editor still holds
        if (get().selectedId === id) set({ selectedId: get().program?.id ?? null, loading: false })
        else set({ loading: false })
        notifyError(e)
      }
    },

    setSource: (source) => {
      set({ source })
      persistDraft()
    },
    setParams: (params) => {
      set({ params })
      persistDraft()
    },
    setTests: (tests) => {
      set({ tests })
      persistDraft()
    },
    setValidation: (source, validation) => set({ validation, validatedSource: source }),

    save: async (note) => {
      const s = get()
      const p = s.program
      if (!p || p.builtin || s.saving) return false
      set({ saving: true })
      try {
        const body: Parameters<typeof labApi.update>[1] = {}
        if (s.source !== p.source) body.source = s.source
        if (!same(s.params, p.params ?? {})) body.params = s.params
        if (!same(s.tests, testsOf(p))) body.tests = s.tests
        if (note) body.note = note
        if (!Object.keys(body).length) return true
        const res = await labApi.update(p.id, body)
        const now = get()
        if (now.program?.id !== p.id) {
          // another program was opened meanwhile: its editor stays as it is
        } else if (now.source === s.source && same(now.params, s.params) && same(now.tests, s.tests)) {
          writeDraft(null)
          set({ validation: res.validation, validatedSource: res.program.source })
          load(res.program, false)
        } else {
          // edited while the request was out: keep the newer draft, only the saved revision moves on
          set({ program: res.program, validation: res.validation, validatedSource: res.program.source })
          persistDraft()
        }
        notify(res.program.rev !== p.rev ? tL('lab.saved', { rev: res.program.rev }) : tL('lab.savedTests'))
        void get().refreshList()
        return true
      } catch (e) {
        notifyError(e)
        return false
      } finally {
        set({ saving: false })
      }
    },

    revert: () => {
      const p = get().program
      if (!p) return
      writeDraft(null)
      load(p, false)
    },

    restore: (source, params, rev) => {
      set((s) => ({ source, params: { ...params }, loadSeq: s.loadSeq + 1 }))
      persistDraft()
      notify(tL('lab.restored', { rev }))
    },

    createNew: async () => {
      try {
        const res = await labApi.create({ name: tL('lab.untitled'), source: NEW_SOURCE, note: tL('lab.noteCreated') })
        await get().refreshList()
        load(res.program, false)
      } catch (e) {
        notifyError(e)
      }
    },

    duplicate: async () => {
      const p = get().program
      if (!p) return
      try {
        const res = await labApi.create({ copy_of: p.id, name: tL('lab.copyName', { name: p.name }) })
        await get().refreshList()
        load(res.program, false)
      } catch (e) {
        notifyError(e)
      }
    },

    remove: async () => {
      const p = get().program
      if (!p || p.builtin) return
      try {
        await labApi.remove(p.id)
        if (readDraft()?.id === p.id) writeDraft(null)
        set({ program: null, selectedId: null })
        notify(tL('lab.deleted', { name: p.name }))
        await get().refresh()
      } catch (e) {
        notifyError(e)
      }
    },

    setTab: (tab) => {
      set({ tab })
      try {
        localStorage.setItem(TAB_KEY, tab)
      } catch {
        /* storage unavailable */
      }
    },
    setChallenge: (challengeId) => set({ challengeId }),
    setRunVehicle: (runVehicle) => set({ runVehicle }),
    setJudgeJob: (judgeJob) => set({ judgeJob }),
    setDuelJob: (duelJob) => set({ duelJob }),
    refreshList: async () => {
      try {
        const res = await labApi.programs()
        set({ programs: res.programs ?? [], listError: null })
      } catch (e) {
        set({ listError: e })
      }
    },
  }
})
