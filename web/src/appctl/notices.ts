// Small bottom-right notices for what an agent did to the interface (04 §1 item 3). Separate from the centred
// toasts of shell/notify.ts, which report the operator's own actions.
import { create } from 'zustand'

export interface AppNotice {
  id: number
  text: string
  level: 'info' | 'warn'
}

interface NoticeState {
  items: AppNotice[]
  seq: number
  push(text: string, level?: AppNotice['level']): void
  dismiss(id: number): void
}

export const NOTICE_MS = 4000
const MAX = 4

export const useAppNotices = create<NoticeState>()((set, get) => ({
  items: [],
  seq: 0,
  push: (text, level = 'info') => {
    const id = get().seq + 1
    set((s) => ({ seq: id, items: [...s.items, { id, text, level }].slice(-MAX) }))
    setTimeout(() => get().dismiss(id), NOTICE_MS)
  },
  dismiss: (id) => set((s) => ({ items: s.items.filter((n) => n.id !== id) })),
}))
