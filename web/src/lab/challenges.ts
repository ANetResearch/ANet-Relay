// Challenge list of the Lab (06 §3.2): loaded once per workspace mount and after every finished submission. A 404
// or 405 means the backend has no judge yet (WP-2b not merged); the Lab then says so instead of failing.
import { create } from 'zustand'
import { RelayApiError } from '@/api'
import { labApi, type ChallengeView } from './api'

export interface ChallengesState {
  list: ChallengeView[] | null
  unavailable: boolean
  error: unknown
  load(): Promise<void>
}

export const isUnavailable = (e: unknown) => e instanceof RelayApiError && (e.status === 404 || e.status === 405 || e.status === 501)

export const useChallenges = create<ChallengesState>()((set) => ({
  list: null,
  unavailable: false,
  error: null,
  load: async () => {
    try {
      set({ list: await labApi.challenges(), unavailable: false, error: null })
    } catch (e) {
      set((s) => ({ unavailable: isUnavailable(e), error: e, list: s.list ?? [] }))
    }
  },
}))

export type ChallengeStatus = 'passed' | 'failed' | 'new'

export function challengeStatus(c: ChallengeView): ChallengeStatus {
  if (c.best?.passed) return 'passed'
  return c.best || c.submissions > 0 ? 'failed' : 'new'
}
