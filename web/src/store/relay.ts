// Backend-derived state (03 §10.2): connection, hello, the latest fleet snapshot (structurally shared), a ring of
// recent events, and renderer-local facts other modules publish (input devices). Writers: the connection layer
// (app/connection.ts), the dev fixture and the InputLayer engine. Components read through ./selectors.
import { create } from 'zustand'
import type { Fleet, Hello, RelayEvent } from '@/api'
import type { StreamState } from '@/api/ws'
import { share } from './share'

export const EVENT_RING = 2000

export interface PadInfo {
  /** 1..4 in connection order (02 §7.3) */
  index: number
  id: string
  model: 'dualsense' | 'standard' | 'unknown'
  connected: boolean
  /** 0..1 when readable */
  battery: number | null
  /** vehicle id this pad possesses, if any */
  vehicle: string | null
}

export interface ConnInfo {
  state: StreamState
  attempt: number
  nextRetryMs: number | null
  everOpen: boolean
  /** performance.now() when the stream last stopped being open; null while open */
  downSince: number | null
  /** performance.now() of the next reconnect attempt, if scheduled */
  retryAt: number | null
}

export interface RelayState {
  conn: ConnInfo
  hello: Hello | null
  fleet: Fleet | null
  /** performance.now() when the last snapshot arrived; with fleet.t it gives the backend clock */
  fleetAt: number
  events: RelayEvent[]
  lastSeq: number
  /** alerts acknowledged in this window but not yet reflected by the backend */
  localAcks: ReadonlySet<string>
  /** written by control/InputLayer: first-interaction gate (02 §7.1) and connected pads (02 §7.4) */
  inputs: { gate: 'pending' | 'open'; pads: PadInfo[] }

  setConn(state: StreamState, info: { attempt: number; nextRetryMs: number | null }): void
  setHello(h: Hello): void
  applyFleet(f: Fleet): void
  applyEvents(e: readonly RelayEvent[]): void
  ackLocal(id: string): void
  setInputs(p: Partial<RelayState['inputs']>): void
  reset(): void
}

function initial() {
  return {
    conn: { state: 'idle', attempt: 0, nextRetryMs: null, everOpen: false, downSince: null, retryAt: null } as ConnInfo,
    hello: null,
    fleet: null,
    fleetAt: 0,
    events: [] as RelayEvent[],
    lastSeq: 0,
    localAcks: new Set<string>() as ReadonlySet<string>,
    inputs: { gate: 'pending' as 'pending' | 'open', pads: [] as PadInfo[] },
  }
}

export const useRelay = create<RelayState>()((set, get) => ({
  ...initial(),
  setConn: (state, info) =>
    set((s) => {
      const now = performance.now()
      return {
        conn: {
          state,
          attempt: info.attempt,
          nextRetryMs: info.nextRetryMs,
          everOpen: s.conn.everOpen || state === 'open',
          downSince: state === 'open' ? null : (s.conn.downSince ?? now),
          retryAt: info.nextRetryMs === null ? null : now + info.nextRetryMs,
        },
      }
    }),
  setHello: (hello) => set({ hello }),
  applyFleet: (f) => {
    const fleet = share(get().fleet, f)
    let localAcks = get().localAcks
    if (localAcks.size) {
      const still = new Set([...localAcks].filter((id) => fleet.alerts.some((a) => a.id === id && !a.acked)))
      if (still.size !== localAcks.size) localAcks = still
    }
    set({ fleet, fleetAt: performance.now(), localAcks })
  },
  applyEvents: (incoming) => {
    if (!incoming.length) return
    const { events, lastSeq } = get()
    const fresh = incoming.filter((e) => e.seq > lastSeq).sort((a, b) => a.seq - b.seq)
    if (!fresh.length) return
    const merged = events.concat(fresh)
    set({ events: merged.length > EVENT_RING ? merged.slice(merged.length - EVENT_RING) : merged, lastSeq: fresh[fresh.length - 1].seq })
  },
  ackLocal: (id) => set((s) => ({ localAcks: new Set([...s.localAcks, id]) })),
  setInputs: (p) => set((s) => ({ inputs: { ...s.inputs, ...p } })),
  reset: () => set(initial()),
}))

/** Backend clock now (seconds), extrapolated from the last snapshot. Safe to call in frame loops. */
export function backendNow(): number {
  const { fleet, fleetAt } = useRelay.getState()
  if (!fleet) return Date.now() / 1000
  return fleet.t + (performance.now() - fleetAt) / 1000
}
