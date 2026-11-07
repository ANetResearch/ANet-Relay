// Copilot conversation state in the renderer (03 §16). The agent runs in the main process; this store only
// folds its events into a transcript and forwards the operator's messages and approvals.
import { create } from 'zustand'
import type { CopilotConfig, CopilotEvent } from '@/api'

export type ToolState = 'running' | 'ok' | 'error'
export type ApprovalState = 'pending' | 'approved' | 'declined' | 'expired'

export type CopilotItem =
  | { kind: 'user'; key: number; text: string }
  | { kind: 'assistant'; key: number; text: string; error: string | null; aborted: boolean; streaming: boolean }
  | { kind: 'tool'; key: number; id: string; tool: string; args: Record<string, unknown>; state: ToolState; progress: string; text: string }
  | { kind: 'approval'; key: number; id: string; tool: string; vehicle: string; label: string; request: Record<string, unknown>; state: ApprovalState }
  | { kind: 'notice'; key: number; text: string; tone: 'info' | 'error' }

interface CopilotState {
  /** the floating window is visible (main window: mirrored from the shell) */
  open: boolean
  /** bumped when an approval arrives, so the floating window flashes its frame once */
  flash: number
  busy: boolean
  items: CopilotItem[]
  config: CopilotConfig | null
  seq: number
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
  setOpen: (open) => set({ open }),
  toggle: () => set((s) => ({ open: !s.open })),
  setConfig: (config) => set({ config }),
  pushUser: (text) => set((s) => ({ seq: s.seq + 1, items: [...s.items, { kind: 'user', key: s.seq + 1, text }] })),
  pushNotice: (text, tone = 'info') => set((s) => ({ seq: s.seq + 1, items: [...s.items, { kind: 'notice', key: s.seq + 1, text, tone }] })),
  setApproval: (id, state) =>
    set((s) => ({ items: s.items.map((it) => (it.kind === 'approval' && it.id === id && it.state === 'pending' ? { ...it, state } : it)) })),
  clear: () => set({ items: [], busy: false }),
  apply: (e) =>
    set((s) => {
      const items = s.items.slice()
      const key = s.seq + 1
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
    }),
}))
