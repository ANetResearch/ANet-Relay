// Leaderboard state of this window (07 §4, §5): the open tab, the last board read from GET /arena/leaderboard, the
// dogfight scenario status and the panel's collapsed flag (localStorage relay.arena.board). The panel refetches the
// board only when the arena view's board_rev or match changes, and when the tab changes; never per stream message.
import { create } from 'zustand'
import { RelayApiError } from '@/api'
import { arenaApi } from './api'
import { isBoardBy, type BoardBy, type DogfightStatus, type Leaderboard } from './leaderboard'

export type BoardStatus = 'idle' | 'loading' | 'ok' | 'unavailable' | 'error'

export interface BoardState {
  by: BoardBy
  board: Leaderboard | null
  /** performance.now() when `board` arrived (survival times run on from there) */
  at: number
  status: BoardStatus
  scenario: DogfightStatus | null
  collapsed: boolean
  setBy(by: BoardBy): void
  setCollapsed(c: boolean): void
}

const KEY = 'relay.arena.board'

function load(): { by?: unknown; collapsed?: unknown } {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? (JSON.parse(raw) as { by?: unknown; collapsed?: unknown }) : {}
  } catch {
    return {}
  }
}

function save(s: Pick<BoardState, 'by' | 'collapsed'>): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ by: s.by, collapsed: s.collapsed }))
  } catch {
    /* storage unavailable: the tab is remembered for this window only */
  }
}

const saved = typeof localStorage !== 'undefined' ? load() : {}

export const useBoard = create<BoardState>()((set, get) => ({
  by: isBoardBy(saved.by) ? saved.by : 'downs',
  board: null,
  at: 0,
  status: 'idle',
  scenario: null,
  collapsed: saved.collapsed === true,
  setBy: (by) => {
    if (by === get().by) return
    set({ by })
    save(get())
    void refetchBoard()
  },
  setCollapsed: (collapsed) => {
    set({ collapsed })
    save(get())
  },
}))

let inflight: AbortController | null = null

/** Reads the board of the open tab; a 404 (backend without a leaderboard) marks it unavailable. */
export async function refetchBoard(): Promise<void> {
  inflight?.abort()
  const ctl = new AbortController()
  inflight = ctl
  const by = useBoard.getState().by
  if (!useBoard.getState().board) useBoard.setState({ status: 'loading' })
  try {
    const board = await arenaApi.leaderboard(by, ctl.signal)
    if (ctl.signal.aborted) return
    useBoard.setState({ board, at: performance.now(), status: 'ok' })
  } catch (e) {
    if (ctl.signal.aborted || (e as { name?: string }).name === 'AbortError') return
    const missing = e instanceof RelayApiError && (e.status === 404 || e.status === 405)
    useBoard.setState({ status: missing ? 'unavailable' : 'error' })
  } finally {
    if (inflight === ctl) inflight = null
  }
}

/** Reads the dogfight scenario status (null on a backend without scenarios). */
export async function refetchScenario(): Promise<void> {
  try {
    useBoard.setState({ scenario: await arenaApi.dogfight() })
  } catch {
    useBoard.setState({ scenario: null })
  }
}

/** Tests only. */
export function resetBoard(): void {
  inflight?.abort()
  inflight = null
  useBoard.setState({ board: null, at: 0, status: 'idle', scenario: null })
}
