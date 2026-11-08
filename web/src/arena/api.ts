// REST helpers of the v0.5 dogfight scenario, the LAN match and the leaderboard (07 §3, §4). Routes are WP-B's; the
// bodies are read through the tolerant normalisers of ./leaderboard so a missing field becomes null, zero or empty,
// never an exception (an older backend without these routes answers 404, which the panel treats as "no board").
//
// Backend shapes (07 §3, §4):
//   POST   /scenarios/dogfight   DogfightRequest -> DogfightStatus           (operator, or agents_app_control)
//   GET    /scenarios/dogfight   -> {running, started_at, ends_at, config, aircraft: [{vid, role, program, team}]}
//   DELETE /scenarios/dogfight   -> DogfightStatus (running false)
//   POST   /arena/match          {duration_s} -> {match}
//   DELETE /arena/match          -> {match: null}
//   GET    /arena/leaderboard?by=downs|survival|accuracy|rams
//          -> {by, match: {id, started_at, ends_at, running} | null, rows: [LeaderboardRow]}
import { request } from '@/api'
import { normBoard, normDogfight, type BoardBy, type DogfightStatus, type Leaderboard } from './leaderboard'

const op = <T>(method: Parameters<typeof request>[0], path: string, body?: unknown) => request<T>(method, path, body, 'operator')

export type TeamsSetting = 'ffa' | 2 | 3

/** POST /scenarios/dogfight (07 §3). Times in seconds, lengths in metres. */
export interface DogfightRequest {
  /** 2..12 simulated aircraft */
  aircraft: number
  teams: TeamsSetting
  /** sums to `aircraft` */
  roles: { gunner: number; rammer: number }
  /** program id per role */
  programs: { gunner: string; rammer: string }
  arena: { radius: number; ceiling: number }
  airframe: 'ideal' | 'typical' | 'rough'
  hover_assist: boolean
  /** 30..1800 */
  duration_s: number
  respawn: boolean
  seed: number
}

export const arenaApi = {
  leaderboard: async (by: BoardBy, signal?: AbortSignal): Promise<Leaderboard> => normBoard(await request<unknown>('GET', `/arena/leaderboard?by=${by}`, undefined, 'none', signal), by),
  matchStart: (durationS: number) => op<unknown>('POST', '/arena/match', { duration_s: durationS }),
  matchStop: () => op<unknown>('DELETE', '/arena/match'),
  dogfight: async (): Promise<DogfightStatus> => normDogfight(await request<unknown>('GET', '/scenarios/dogfight')),
  dogfightStart: async (body: DogfightRequest): Promise<DogfightStatus> => normDogfight(await op<unknown>('POST', '/scenarios/dogfight', body)),
  dogfightStop: async (): Promise<DogfightStatus> => normDogfight(await op<unknown>('DELETE', '/scenarios/dogfight')),
}
