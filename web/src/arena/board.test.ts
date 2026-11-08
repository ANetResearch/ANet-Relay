import { afterEach, describe, expect, it, vi } from 'vitest'
import { arenaApi } from './api'
import { BOARD_MIN_GAP_MS, refetchBoard, resetBoard, useBoard } from './board'
import type { Leaderboard } from './leaderboard'

const board = (n: number): Leaderboard => ({ by: 'downs', match: null, rows: [{ key: 'SIM-1', aircraft: 'SIM-1', callsign: '', local: true, controller: { kind: 'program', name: 'hunter' }, downs: n, hits: 0, shots: 0, accuracy: null, rams: 0, deaths: 0, crashes: 0, alive_s: 0, best_alive_s: 0, damage: 0 }] })

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  resetBoard()
})

describe('leaderboard refetch (07 §4)', () => {
  it('coalesces revisions during a fight instead of aborting, so the board keeps landing', async () => {
    vi.useFakeTimers()
    let calls = 0
    let release: (() => void) | null = null
    vi.spyOn(arenaApi, 'leaderboard').mockImplementation(() => {
      const n = ++calls
      return new Promise((resolve) => {
        release = () => resolve(board(n))
      })
    })
    void refetchBoard()
    // a burst of revisions while the first read is in flight: no new request, no abort
    for (let i = 0; i < 20; i++) void refetchBoard()
    expect(calls).toBe(1)
    release!()
    await vi.advanceTimersByTimeAsync(0)
    expect(useBoard.getState().board?.rows[0].downs).toBe(1)
    // exactly one follow-up read, spaced by the minimum gap
    await vi.advanceTimersByTimeAsync(BOARD_MIN_GAP_MS)
    expect(calls).toBe(2)
    release!()
    await vi.advanceTimersByTimeAsync(0)
    expect(useBoard.getState().board?.rows[0].downs).toBe(2)
    await vi.advanceTimersByTimeAsync(BOARD_MIN_GAP_MS * 4)
    expect(calls).toBe(2)
  })
})
