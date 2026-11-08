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

let inflight: { ctl: AbortController; by: BoardBy } | null = null
let pending = false
let lastAt = -Infinity
let timer: ReturnType<typeof setTimeout> | null = null

/** At most this many board reads a second: board_rev moves with every round fired during a fight. */
export const BOARD_MIN_GAP_MS = 250

/**
 * Reads the board of the open tab; a 404 (backend without a leaderboard) marks it unavailable. Requests made while a
 * read is in flight are coalesced into one more read after it, no sooner than BOARD_MIN_GAP_MS after the last one:
 * aborting on every revision would starve the panel while rounds are flying. Only a tab change aborts the read.
 */
export async function refetchBoard(): Promise<void> {
  const by = useBoard.getState().by
  if (inflight && inflight.by === by) {
    pending = true
    return
  }
  inflight?.ctl.abort()
  inflight = null
  const wait = lastAt + BOARD_MIN_GAP_MS - performance.now()
  if (wait > 0) {
    pending = true
    timer ??= setTimeout(() => {
      timer = null
      if (pending) {
        pending = false
        void refetchBoard()
      }
    }, wait)
    return
  }
  pending = false
  const ctl = new AbortController()
  inflight = { ctl, by }
  lastAt = performance.now()
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
    if (inflight?.ctl === ctl) {
      inflight = null
      if (pending) {
        pending = false
        void refetchBoard()
      }
    }
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
  inflight?.ctl.abort()
  inflight = null
  pending = false
  lastAt = -Infinity
  if (timer) clearTimeout(timer)
  timer = null
  useBoard.setState({ board: null, at: 0, status: 'idle', scenario: null })
}
