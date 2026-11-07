// Renderer-local state published by the input engine (control/engine.ts) for the surfaces of WP6, WP7 and WP8:
// hold progress per aircraft, controller disconnects, paused / awaiting-move notices, connection hint cards;
// nothing persistent (dead zone and expo live in settings/prefs.ts, WP8). Live stick values change at 50 Hz and are therefore not React
// state: frame loops read getLiveSticks(id) directly (CONTRACTS.md rule 6).
import { create } from 'zustand'
import type { PadModel, StickFrame } from './mapping'
import type { SourceKey } from './bindings'

export type HoldKind = 'options' | 'arm' | 'reset'

export interface HoldState {
  kind: HoldKind
  pad: number
  /** performance.now() when the hold started */
  startedAt: number
  durationMs: number
}

export type AwaitReason = 'focus' | 'link'

export interface HintCard {
  key: string
  pad: number
  model: PadModel
  /** line 2 of 02 §7.2 */
  state: 'flying' | 'holdOptions' | 'noFree'
  vehicle: string | null
  createdAt: number
}

export interface InputUiState {
  holds: Readonly<Record<string, HoldState>>
  /** vehicle -> pad number and performance.now() of the disconnect */
  padLost: Readonly<Record<string, { pad: number; since: number }>>
  /** the window lost focus: every human stick is stopped (02 §5.3) */
  paused: boolean
  /** vehicle -> why its sticks wait for a stick movement before resuming */
  awaiting: Readonly<Record<string, AwaitReason>>
  hints: readonly HintCard[]
  dismissHint(key: string): void
}

export const useInputUi = create<InputUiState>()((set) => ({
  holds: {},
  padLost: {},
  paused: false,
  awaiting: {},
  hints: [],
  dismissHint: (key) => set((s) => ({ hints: s.hints.filter((h) => h.key !== key) })),
}))

/** Hold in progress on this aircraft (Options / L1 + R1 / Circle), for HoldProgress on cards and stage labels. */
export function useHold(vehicleId: string | null): HoldState | null {
  return useInputUi((s) => (vehicleId ? (s.holds[vehicleId] ?? null) : null))
}

/** True while the controller possessing this aircraft is disconnected (ControllerPill `padDisconnected`). */
export function usePadDisconnected(vehicleId: string | null): boolean {
  return useInputUi((s) => (vehicleId ? vehicleId in s.padLost : false))
}

export function useAwaiting(vehicleId: string | null): AwaitReason | null {
  return useInputUi((s) => (vehicleId ? (s.awaiting[vehicleId] ?? null) : null))
}

export const useInputPaused = () => useInputUi((s) => s.paused)

// ------------------------------------------------------------------------------------------- live sticks

export interface LiveSticks {
  /** last frame computed for this aircraft (sent or not) */
  frame: StickFrame
  /** throttle stick centre in use (hover point or 0.5 in hold modes) */
  centre: number
  /** true while frames are actually going out */
  sending: boolean
  source: SourceKey
  /** performance.now() of the computation */
  at: number
}

const live = new Map<string, LiveSticks>()

/** Non-reactive: the current human sticks of an aircraft. Safe in frame loops; null when no human input drives it. */
export function getLiveSticks(vehicleId: string): LiveSticks | null {
  return live.get(vehicleId) ?? null
}

/** Live preview of every connected pad (raw axes, y up positive), for the pads popover. */
const padPreview = new Map<number, { lx: number; ly: number; rx: number; ry: number }>()
export function getPadPreview(pad: number): { lx: number; ly: number; rx: number; ry: number } | null {
  return padPreview.get(pad) ?? null
}

/** Engine-side writers (control/engine.ts only). */
export const liveWriter = {
  set(vehicle: string, s: LiveSticks): void {
    live.set(vehicle, s)
  },
  retain(vehicles: ReadonlySet<string>): void {
    for (const id of [...live.keys()]) if (!vehicles.has(id)) live.delete(id)
  },
  pad(index: number, raw: { lx: number; ly: number; rx: number; ry: number } | null): void {
    if (raw) padPreview.set(index, raw)
    else padPreview.delete(index)
  },
  clear(): void {
    live.clear()
    padPreview.clear()
  },
}
