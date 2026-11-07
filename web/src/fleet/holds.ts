// Hold-to-confirm progress per aircraft (02 §7.5). Written by the InputLayer (WP7) on press and release; read by the
// fleet cards (bottom edge) and the stage labels. See CONTRACTS.md §5.
import { create } from 'zustand'

export interface Hold {
  /** what is being held: 'possess', 'arm', 'reset', ... (for diagnostics only) */
  kind: string
  durationMs: number
  /** performance.now() at press */
  startedAt: number
}

interface HoldState {
  holds: Readonly<Record<string, Hold>>
  setHold(vehicleId: string, hold: { kind: string; durationMs: number } | null): void
}

export const useHolds = create<HoldState>()((set) => ({
  holds: {},
  setHold: (vehicleId, hold) =>
    set((s) => {
      const next = { ...s.holds }
      if (hold) next[vehicleId] = { ...hold, startedAt: performance.now() }
      else delete next[vehicleId]
      return { holds: next }
    }),
}))

export const setHold = (vehicleId: string, hold: { kind: string; durationMs: number } | null) => useHolds.getState().setHold(vehicleId, hold)
export const useHold = (vehicleId: string): Hold | null => useHolds((s) => s.holds[vehicleId] ?? null)
