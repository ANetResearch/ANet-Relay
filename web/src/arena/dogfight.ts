// Form model of the "Start dogfight" dialog (07 §3): defaults, the 2:1 gunner / rammer split, validation against the
// request limits and the request body. Pure (leaderboard.test.ts). The backend validates again and is authoritative;
// these checks only keep an obviously wrong request from leaving the window.
import type { ArenaKey } from './copy'
import type { DogfightRequest, TeamsSetting } from './api'

export interface DogfightForm {
  aircraft: string
  teams: 'ffa' | '2' | '3'
  gunners: string
  rammers: string
  gunnerProgram: string
  rammerProgram: string
  radius: string
  ceiling: string
  airframe: DogfightRequest['airframe']
  hoverAssist: boolean
  duration: string
  respawn: boolean
  seed: string
}

export type DogfightField = 'aircraft' | 'roles' | 'arena' | 'duration' | 'seed'

export const DOGFIGHT_LIMITS = { aircraft: [2, 12], duration: [30, 1800], radius: [3, 30], ceiling: [1.5, 10] } as const

/** Default split of `n` aircraft: two gunners for every rammer (07 §3), at least one gunner. */
export function defaultRoles(n: number): { gunner: number; rammer: number } {
  const total = Math.max(0, Math.floor(n))
  const rammer = Math.floor(total / 3)
  return { gunner: total - rammer, rammer }
}

export function defaultForm(): DogfightForm {
  const roles = defaultRoles(6)
  return {
    aircraft: '6', teams: 'ffa', gunners: String(roles.gunner), rammers: String(roles.rammer),
    gunnerProgram: 'hunter_medium', rammerProgram: 'rammer',
    radius: '8', ceiling: '4', airframe: 'typical', hoverAssist: false, duration: '180', respawn: true, seed: '7',
  }
}

const int = (s: string): number | null => (/^\s*\d+\s*$/.test(s) ? Number(s) : null)
const real = (s: string): number | null => {
  const n = Number(s.trim())
  return s.trim() !== '' && Number.isFinite(n) ? n : null
}
const within = (n: number | null, [lo, hi]: readonly [number, number]) => n !== null && n >= lo && n <= hi

/** Field errors as copy keys with their parameters; empty when the form can be sent. */
export function validateDogfight(f: DogfightForm): Partial<Record<DogfightField, { key: ArenaKey; params?: Record<string, number> }>> {
  const out: Partial<Record<DogfightField, { key: ArenaKey; params?: Record<string, number> }>> = {}
  const n = int(f.aircraft)
  if (!within(n, DOGFIGHT_LIMITS.aircraft)) out.aircraft = { key: 'df.err.aircraft' }
  const g = int(f.gunners)
  const r = int(f.rammers)
  if (n !== null && (g === null || r === null || g + r !== n)) out.roles = { key: 'df.err.roles', params: { n } }
  if (!within(real(f.radius), DOGFIGHT_LIMITS.radius) || !within(real(f.ceiling), DOGFIGHT_LIMITS.ceiling)) out.arena = { key: 'df.err.arena' }
  if (!within(int(f.duration), DOGFIGHT_LIMITS.duration)) out.duration = { key: 'df.err.duration' }
  if (int(f.seed) === null) out.seed = { key: 'df.err.seed' }
  return out
}

/** The POST /scenarios/dogfight body of a valid form. */
export function dogfightBody(f: DogfightForm): DogfightRequest {
  const teams: TeamsSetting = f.teams === 'ffa' ? 'ffa' : f.teams === '2' ? 2 : 3
  return {
    aircraft: Number(f.aircraft),
    teams,
    roles: { gunner: Number(f.gunners), rammer: Number(f.rammers) },
    programs: { gunner: f.gunnerProgram, rammer: f.rammerProgram },
    arena: { radius: Number(f.radius), ceiling: Number(f.ceiling) },
    airframe: f.airframe,
    hover_assist: f.hoverAssist,
    duration_s: Number(f.duration),
    respawn: f.respawn,
    seed: Number(f.seed),
  }
}

/** Changing the number of aircraft re-splits the roles 2:1 (a hand-set split is kept while it still adds up). */
export function withAircraft(f: DogfightForm, aircraft: string): DogfightForm {
  const n = int(aircraft)
  const g = int(f.gunners)
  const r = int(f.rammers)
  if (n === null || (g !== null && r !== null && g + r === n)) return { ...f, aircraft }
  const roles = defaultRoles(n)
  return { ...f, aircraft, gunners: String(roles.gunner), rammers: String(roles.rammer) }
}
