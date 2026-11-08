// Replay state of the stage (02 §13). The Records workspace writes it while a record is open in its replay viewport;
// the stage (WP6) reads it in its frame loop. See CONTRACTS.md §5.
import { create } from 'zustand'

export interface ReplayFrame {
  t: number
  armed: boolean
  mode: string | null
  /** rad */
  roll: number | null
  pitch: number | null
  yaw: number | null
  /** ENU m in the aircraft's own frame (sim or local) */
  x: number | null
  y: number | null
  z: number | null
  alt: number | null
  /** 0..100 */
  throttle: number | null
  controller: 'none' | 'human' | 'agent' | 'program' | null
}

/** WP-3: one aircraft of a multi-aircraft replay (a Lab evaluation trace, 06 §3.4 / §7). */
export interface ReplayCraft {
  /** stage id (lab/trace.ts traceStageId), never a live aircraft id */
  id: string
  label: string
  sim: boolean
  frame: ReplayFrame
  /** program name and state for the PROGRAM · name · state label */
  program: string | null
  state: string | null
  hp: number | null
}

export interface ReplayState {
  active: boolean
  recordId: number | null
  vehicleId: string | null
  label: string
  kind: 'real' | 'sim'
  /** unix seconds at the cursor */
  t: number | null
  frame: ReplayFrame | null
  playing: boolean
  /** WP-3: several aircraft at once (an evaluation trace); null while one record replays */
  crafts: ReplayCraft[] | null
}

const IDLE: ReplayState = { active: false, recordId: null, vehicleId: null, label: '', kind: 'real', t: null, frame: null, playing: false, crafts: null }

export const useReplay = create<ReplayState>()(() => ({ ...IDLE }))

export function setReplay(patch: Partial<ReplayState>): void {
  useReplay.setState(patch)
}

export function exitReplay(): void {
  useReplay.setState({ ...IDLE })
}

export const isReplaying = () => useReplay.getState().active
