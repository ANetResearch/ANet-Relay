// Copilot conversation state in the renderer (03 §16). The agent runs in the main process; this store only
// folds its events into a transcript and forwards the operator's messages and approvals.
import { create } from 'zustand'
import type { CopilotConfig, CopilotEvent, CopilotRunOutcome, CopilotUsage } from '@/api'

export type ToolState = 'running' | 'ok' | 'error'

// ---------------------------------------------------------------- run trace (08 §3, §4)

/** What a run is doing now; the outcomes once it ended. */
export type RunPhase = 'waiting' | 'thinking' | 'writing' | 'drafting' | 'tool' | 'approval' | CopilotRunOutcome

export interface TurnStep {
  kind: 'turn'
  turn: number
  startedAt: number
  endedAt: number | null
  thinking: string
  text: string
  toolCalls: { id: string; name: string; args: string }[]
  stopReason: string | null
  usage: CopilotUsage | null
  error: string | null
}

export interface ToolStep {
  kind: 'tool'
  id: string
  tool: string
  args: Record<string, unknown>
  startedAt: number
  endedAt: number | null
  state: ToolState
  result: string
}

export type RunStep = TurnStep | ToolStep

export interface CopilotRun {
  id: number
  /** key of the user message this run answers (null when it started without one) */
  userKey: number | null
  model: string
  thinking: string
  startedAt: number
  endedAt: number | null
  phase: RunPhase
  /** the tool whose arguments the model is writing, or that runs (drafting, tool, approval) */
  tool: string | null
  draftChars: number
  thinkingChars: number
  steps: RunStep[]
  usage: CopilotUsage | null
  turns: number
  tools: number
}

/** Runs kept for the trace; older ones lose their chip. */
export const RUNS_KEPT = 30
/** Thinking text kept per turn (characters). */
export const THINKING_KEPT = 200_000

const ENDED: readonly RunPhase[] = ['done', 'error', 'aborted', 'empty', 'length']
export const runEnded = (r: CopilotRun) => ENDED.includes(r.phase)

/** The run with this id (or the newest one) after `fn` changed a copy of it; other runs are kept as they are. */
function withRun(runs: CopilotRun[], id: number | undefined, fn: (r: CopilotRun) => void): CopilotRun[] {
  let i = id === undefined ? runs.length - 1 : runs.findIndex((r) => r.id === id)
  if (i < 0) i = runs.length - 1
  if (i < 0) return runs
  const r: CopilotRun = { ...runs[i], steps: runs[i].steps.slice() }
  fn(r)
  const out = runs.slice()
  out[i] = r
  return out
}

/** Replace the last turn of `r` with a changed copy. */
function editTurn(r: CopilotRun, fn: (t: TurnStep) => void): void {
  for (let i = r.steps.length - 1; i >= 0; i--) {
    const st = r.steps[i]
    if (st.kind !== 'turn') continue
    const copy = { ...st }
    fn(copy)
    r.steps[i] = copy
    return
  }
}

/** Fold one main-process event into the runs (pure; also used by tests). */
export function applyRunEvent(runs: CopilotRun[], e: CopilotEvent, userKey: number | null, now: number): CopilotRun[] {
  switch (e.type) {
    case 'run_start': {
      const run: CopilotRun = {
        id: e.run,
        userKey,
        model: e.model,
        thinking: e.thinking,
        startedAt: e.at,
        endedAt: null,
        phase: 'waiting',
        tool: null,
        draftChars: 0,
        thinkingChars: 0,
        steps: [],
        usage: null,
        turns: 0,
        tools: 0,
      }
      return [...runs, run].slice(-RUNS_KEPT)
    }
    case 'turn_start':
      return withRun(runs, e.run, (r) => {
        r.phase = 'waiting'
        r.tool = null
        r.turns = e.turn
        r.steps.push({ kind: 'turn', turn: e.turn, startedAt: e.at, endedAt: null, thinking: '', text: '', toolCalls: [], stopReason: null, usage: null, error: null })
      })
    case 'thinking':
      return withRun(runs, e.run, (r) => {
        r.phase = 'thinking'
        r.thinkingChars += e.delta.length
        editTurn(r, (t) => {
          if (t.thinking.length < THINKING_KEPT) t.thinking = (t.thinking + e.delta).slice(0, THINKING_KEPT)
        })
      })
    case 'text':
      if (!runs.length) return runs
      return withRun(runs, e.run, (r) => {
        r.phase = 'writing'
        editTurn(r, (t) => {
          t.text += e.delta
        })
      })
    case 'draft':
      return withRun(runs, e.run, (r) => {
        r.phase = 'drafting'
        r.tool = e.tool || r.tool
        r.draftChars = e.chars
      })
    case 'assistant_end':
      if (!runs.length) return runs
      return withRun(runs, e.run, (r) => {
        r.phase = 'waiting'
        r.draftChars = 0
        editTurn(r, (t) => {
          t.endedAt = e.ms != null ? t.startedAt + e.ms : now
          t.stopReason = e.stopReason ?? null
          t.usage = e.usage ?? null
          t.toolCalls = e.toolCalls ?? []
          t.error = e.error
        })
      })
    case 'tool_start':
      if (!runs.length) return runs
      return withRun(runs, e.run, (r) => {
        r.phase = 'tool'
        r.tool = e.tool
        r.tools += 1
        r.steps.push({ kind: 'tool', id: e.id, tool: e.tool, args: e.args ?? {}, startedAt: e.at ?? now, endedAt: null, state: 'running', result: '' })
      })
    case 'tool_end':
      if (!runs.length) return runs
      return withRun(runs, undefined, (r) => {
        const i = r.steps.findIndex((st) => st.kind === 'tool' && st.id === e.id)
        if (i >= 0) {
          const st = r.steps[i] as ToolStep
          r.steps[i] = { ...st, endedAt: e.ms != null ? st.startedAt + e.ms : now, state: e.error ? 'error' : 'ok', result: e.full ?? e.text }
        }
        if (!runEnded(r)) r.phase = 'waiting'
      })
    case 'approval':
      if (!runs.length) return runs
      return withRun(runs, undefined, (r) => {
        if (!runEnded(r)) {
          r.phase = 'approval'
          r.tool = e.tool
        }
      })
    case 'run_end':
      return withRun(runs, e.run, (r) => {
        r.phase = e.outcome
        r.endedAt = e.at
        r.usage = e.usage
        r.turns = e.turns
        r.tools = e.tools
        r.tool = null
      })
    case 'busy':
      // a run the agent never closed (the main process failed): end it as an error
      if (e.busy || !runs.length) return runs
      return withRun(runs, undefined, (r) => {
        if (!runEnded(r)) {
          r.phase = 'error'
          r.endedAt = now
        }
      })
    default:
      return runs
  }
}

/** The run a turn belongs to has ended in `length` or said nothing: the notice the transcript shows (08 §3). */
export function turnNotice(e: Extract<CopilotEvent, { type: 'assistant_end' }>): 'length' | 'empty' | null {
  if (e.error || e.aborted) return null
  if (e.stopReason === 'length') return 'length'
  if (e.empty) return 'empty'
  return null
}
export type ApprovalState = 'pending' | 'approved' | 'declined' | 'expired' | 'auto'

export type CopilotItem =
  | { kind: 'user'; key: number; text: string }
  | { kind: 'assistant'; key: number; text: string; error: string | null; aborted: boolean; streaming: boolean }
  | { kind: 'tool'; key: number; id: string; tool: string; args: Record<string, unknown>; state: ToolState; progress: string; text: string }
  | { kind: 'approval'; key: number; id: string; tool: string; vehicle: string; label: string; request: Record<string, unknown>; state: ApprovalState }
  | { kind: 'notice'; key: number; text: string; tone: 'info' | 'error'; code?: 'length' | 'empty' }

interface CopilotState {
  /** the floating window is visible (main window: mirrored from the shell) */
  open: boolean
  /** bumped when an approval arrives, so the floating window flashes its frame once */
  flash: number
  busy: boolean
  items: CopilotItem[]
  config: CopilotConfig | null
  seq: number
  /** 08 §3: the run trace of each prompt */
  runs: CopilotRun[]
  /** the run whose trace is open, or null */
  openRun: number | null
  toggleRun(id: number): void
  setOpen(open: boolean): void
  toggle(): void
  setConfig(cfg: CopilotConfig | null): void
  pushUser(text: string): void
  pushNotice(text: string, tone?: 'info' | 'error'): void
  setApproval(id: string, state: ApprovalState): void
  clear(): void
  apply(e: CopilotEvent): void
}

function lastIndex<T>(items: T[], pred: (x: T) => boolean): number {
  for (let i = items.length - 1; i >= 0; i--) if (pred(items[i])) return i
  return -1
}

export const useCopilot = create<CopilotState>((set) => ({
  open: false,
  flash: 0,
  busy: false,
  items: [],
  config: null,
  seq: 0,
  runs: [],
  openRun: null,
  toggleRun: (id) => set((s) => ({ openRun: s.openRun === id ? null : id })),
  setOpen: (open) => set({ open }),
  toggle: () => set((s) => ({ open: !s.open })),
  setConfig: (config) => set({ config }),
  pushUser: (text) => set((s) => ({ seq: s.seq + 1, items: [...s.items, { kind: 'user', key: s.seq + 1, text }] })),
  pushNotice: (text, tone = 'info') => set((s) => ({ seq: s.seq + 1, items: [...s.items, { kind: 'notice', key: s.seq + 1, text, tone }] })),
  setApproval: (id, state) =>
    set((s) => ({ items: s.items.map((it) => (it.kind === 'approval' && it.id === id && it.state === 'pending' ? { ...it, state } : it)) })),
  clear: () => set({ items: [], busy: false, runs: [], openRun: null }),
  apply: (e) =>
    set((s) => {
      const items = s.items.slice()
      const key = s.seq + 1
      // the user message a new run answers: the newest one no run has claimed yet
      const userKey = e.type === 'run_start' ? (lastUnclaimed(s.items, s.runs) ?? null) : null
      const runs = applyRunEvent(s.runs, e, userKey, Date.now())
      const out = applyItem(s, items, key, e)
      return runs === s.runs ? out : { ...out, runs }
    }),
}))

function lastUnclaimed(items: CopilotItem[], runs: CopilotRun[]): number | undefined {
  const claimed = new Set(runs.map((r) => r.userKey))
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i]
    if (it.kind === 'user') return claimed.has(it.key) ? undefined : it.key
  }
  return undefined
}

function applyItem(s: CopilotState, items: CopilotItem[], key: number, e: CopilotEvent): Partial<CopilotState> {
  switch (e.type) {
    case 'busy':
      return { busy: e.busy, items: e.busy ? items : items.map((it) => (it.kind === 'assistant' && it.streaming ? { ...it, streaming: false } : it)) }
    case 'assistant_start':
      items.push({ kind: 'assistant', key, text: '', error: null, aborted: false, streaming: true })
      return { items, seq: key }
    case 'text': {
      const i = lastIndex(items, (it) => it.kind === 'assistant')
      if (i < 0) return {}
      const it = items[i] as Extract<CopilotItem, { kind: 'assistant' }>
      items[i] = { ...it, text: it.text + e.delta }
      return { items }
    }
    case 'assistant_end': {
      const i = lastIndex(items, (it) => it.kind === 'assistant')
      if (i < 0) return {}
      const it = items[i] as Extract<CopilotItem, { kind: 'assistant' }>
      if (!it.text.trim() && !e.error && !e.aborted) items.splice(i, 1)
      else items[i] = { ...it, error: e.error, aborted: e.aborted, streaming: false }
      // 08 §3: a turn that ran out of output or said nothing leaves a notice instead of vanishing
      const code = turnNotice(e)
      if (code) {
        items.push({ kind: 'notice', key, text: '', tone: 'error', code })
        return { items, seq: key }
      }
      return { items }
    }
    case 'tool_start':
      items.push({ kind: 'tool', key, id: e.id, tool: e.tool, args: e.args ?? {}, state: 'running', progress: '', text: '' })
      return { items, seq: key }
    case 'tool_update': {
      const i = lastIndex(items, (it) => it.kind === 'tool' && it.id === e.id)
      if (i >= 0) items[i] = { ...(items[i] as Extract<CopilotItem, { kind: 'tool' }>), progress: e.text }
      return { items }
    }
    case 'tool_end': {
      const i = lastIndex(items, (it) => it.kind === 'tool' && it.id === e.id)
      if (i >= 0) items[i] = { ...(items[i] as Extract<CopilotItem, { kind: 'tool' }>), state: e.error ? 'error' : 'ok', text: e.text }
      return { items }
    }
    case 'approval':
      if (e.auto) {
        // 09 §4: approved under "Allow all"; recorded, no flash
        items.push({ kind: 'approval', key, id: e.id, tool: e.tool, vehicle: e.vehicle, label: e.label, request: e.request, state: 'auto' })
        return { items, seq: key }
      }
      items.push({ kind: 'approval', key, id: e.id, tool: e.tool, vehicle: e.vehicle, label: e.label, request: e.request, state: 'pending' })
      return { items, seq: key, flash: s.flash + 1 }
    case 'approval_closed':
      return {
        items: items.map((it) =>
          it.kind === 'approval' && it.id === e.id && it.state === 'pending' ? { ...it, state: e.approved ? 'approved' : e.reason === 'timeout' ? 'expired' : 'declined' } : it,
        ),
      }
    case 'error':
      items.push({ kind: 'notice', key, text: e.error, tone: 'error' })
      return { items, seq: key }
    default:
      return {}
  }
}
