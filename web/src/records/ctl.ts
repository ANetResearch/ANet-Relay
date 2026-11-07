// Requests from outside the Records workspace (application control, 04 §3.2 open_record / replay): which record to
// open and what the replay cursor should do. The workspace and the replay viewport consume them; `seq` makes each
// request distinct so repeating one still fires.
import { create } from 'zustand'

export type ReplayCommand = { action: 'play' } | { action: 'pause' } | { action: 'seek'; t: number }

interface RecordsCtl {
  open: { id: number; seq: number } | null
  replay: { cmd: ReplayCommand; seq: number } | null
  openRecord(id: number): void
  replayCommand(cmd: ReplayCommand): void
}

export const useRecordsCtl = create<RecordsCtl>()((set) => ({
  open: null,
  replay: null,
  openRecord: (id) => set((s) => ({ open: { id, seq: (s.open?.seq ?? 0) + 1 } })),
  replayCommand: (cmd) => set((s) => ({ replay: { cmd, seq: (s.replay?.seq ?? 0) + 1 } })),
}))
