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
  controller: 'none' | 'human' | 'agent' | null
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
}

const IDLE: ReplayState = { active: false, recordId: null, vehicleId: null, label: '', kind: 'real', t: null, frame: null, playing: false }

export const useReplay = create<ReplayState>()(() => ({ ...IDLE }))

export function setReplay(patch: Partial<ReplayState>): void {
  useReplay.setState(patch)
}

export function exitReplay(): void {
  useReplay.setState({ ...IDLE })
}

export const isReplaying = () => useReplay.getState().active
