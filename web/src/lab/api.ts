// REST helpers of the Lab (06 §2, §3, §7). Program routes are WP-1's (backend/relay/api/routes_programs.py) and
// typed from the generated schema. Challenge, job, duel, ladder and trace routes belong to WP-2b; until they are in
// the exported schema their bodies are read through the tolerant normalisers below, which accept the shapes of
// 06 §3.2-§3.4 and the field names documented here. A missing field becomes null or empty, never an exception.
//
// Expected WP-2b shapes (integrator: keep these or adjust the normalisers):
//   GET  /challenges                 -> {challenges: [{id, name, name_zh, description, description_zh, pass_en?,
//                                         pass_zh?, best: {score, passed, program, rev, t} | null, submissions}]}
//   POST /challenges/{id}/submit     {program, rev?} -> {job: Job} | Job
//   GET  /jobs/{id}                  -> Job = {id, kind: challenge|duel, state: queued|running|done|failed|cancelled,
//                                         progress 0..1, error?, result}
//        challenge result            {challenge, program, rev, score, passed, metrics: {name: value},
//                                         seeds: [{seed, hidden, score, passed, metrics?, trace_id?}], trace_id}
//        duel result                 {rounds: [{round, seed, winner | null, entries: [{program, name?, hits, hp,
//                                         alive_s, survived}], trace_id}], ladder: LadderRow[]}
//   POST /duels                      {programs: [id x 2..8], rounds, seed, scenario?} -> {job: Job} | Job
//   GET  /ladder                     -> {ladder: [{program, name?, elo, delta?, played, wins, losses, draws}]}
//   GET  /traces/{id}                -> the trace of ./trace.ts
import { request, type Program, type ProgramLog, type ProgramRevision, type ProgramRunView, type ProgramSummary, type ProgramTestResults, type ProgramValidation } from '@/api'

const enc = encodeURIComponent
const op = <T>(method: Parameters<typeof request>[0], path: string, body?: unknown) => request<T>(method, path, body, 'operator')

/** A unit test (06 §3.1). */
export interface ProgramTestCase {
  name: string
  obs: Record<string, unknown>
  mem?: Record<string, unknown>
  state?: string
  params?: Record<string, unknown>
  expect: Record<string, unknown>
}

export interface ProgramBody {
  name?: string
  description?: string
  source?: string
  params?: Record<string, unknown>
  tests?: ProgramTestCase[]
  note?: string
  copy_of?: string
}

export interface DeployBody {
  program: string
  rev?: number
  params?: Record<string, unknown>
  mode: 'pilot' | 'copilot'
}

export interface SaveResult {
  program: Program
  validation: ProgramValidation
}

export const labApi = {
  programs: () => request<{ programs: ProgramSummary[] }>('GET', '/programs'),
  program: (id: string) => request<{ program: Program }>('GET', `/programs/${enc(id)}`),
  create: (body: ProgramBody) => op<SaveResult>('POST', '/programs', body),
  update: (id: string, body: ProgramBody) => op<SaveResult>('PUT', `/programs/${enc(id)}`, body),
  remove: (id: string) => op<unknown>('DELETE', `/programs/${enc(id)}`),
  revisions: (id: string) => request<{ id: string; revisions: ProgramRevision[] }>('GET', `/programs/${enc(id)}/revisions`),
  validate: (id: string, source: string) => op<ProgramValidation & { id: string }>('POST', `/programs/${enc(id)}/validate`, { source }),
  test: (id: string, body: { source?: string; tests?: ProgramTestCase[]; rev?: number; seed?: number }) =>
    op<ProgramTestResults>('POST', `/programs/${enc(id)}/test`, body),
  deploy: (vid: string, body: DeployBody) => op<{ id: string; program: ProgramRunView }>('POST', `/vehicles/${enc(vid)}/program`, body),
  stop: (vid: string) => op<{ id: string; program: ProgramRunView }>('POST', `/vehicles/${enc(vid)}/program/stop`),
  resume: (vid: string) => op<{ id: string; program: ProgramRunView }>('POST', `/vehicles/${enc(vid)}/program/resume`),
  params: (vid: string, patch: Record<string, unknown>) => op<{ id: string; params: Record<string, unknown> }>('PATCH', `/vehicles/${enc(vid)}/program/params`, patch),
  log: (vid: string, after: number) => request<ProgramLog>('GET', `/vehicles/${enc(vid)}/program/log?after=${after}`),
  allow: (vid: string, allow: boolean, confirm?: string) =>
    op<{ id: string; programs_allowed: boolean }>('POST', `/vehicles/${enc(vid)}/program/allow`, confirm === undefined ? { allow } : { allow, confirm }),
  // WP-2b routes (06 §3.2-§3.4)
  challenges: async () => normChallenges(await request<unknown>('GET', '/challenges')),
  submit: async (challenge: string, program: string, rev?: number) =>
    normJob(await op<unknown>('POST', `/challenges/${enc(challenge)}/submit`, rev === undefined ? { program } : { program, rev })),
  job: async (id: string) => normJob(await request<unknown>('GET', `/jobs/${enc(id)}`)),
  duel: async (body: { programs: string[]; rounds: number; seed: number; scenario?: string }) => normJob(await op<unknown>('POST', '/duels', body)),
  ladder: async () => normLadder(await request<unknown>('GET', '/ladder')),
  trace: (id: string) => request<unknown>('GET', `/traces/${enc(id)}`),
} as const

// ---------------------------------------------------------------- normalisers

type Obj = Record<string, unknown>
const obj = (x: unknown): Obj => (x && typeof x === 'object' && !Array.isArray(x) ? (x as Obj) : {})
const str = (x: unknown, d = ''): string => (typeof x === 'string' ? x : d)
const strOrNull = (x: unknown): string | null => (typeof x === 'string' && x ? x : null)
const num = (x: unknown): number | null => (typeof x === 'number' && Number.isFinite(x) ? x : null)
const arr = (x: unknown): unknown[] => (Array.isArray(x) ? x : [])

export interface ChallengeBest {
  score: number
  passed: boolean
  program: string | null
  rev: number | null
  t: number | null
}

export interface ChallengeView {
  id: string
  name: string
  name_zh: string
  description: string
  description_zh: string
  pass_en: string
  pass_zh: string
  best: ChallengeBest | null
  submissions: number
}

export function normChallenge(raw: unknown): ChallengeView | null {
  const o = obj(raw)
  const id = str(o.id)
  if (!id) return null
  const b = o.best && typeof o.best === 'object' ? obj(o.best) : null
  const score = b ? num(b.score) : null
  return {
    id,
    name: str(o.name, id),
    name_zh: str(o.name_zh, str(o.name, id)),
    description: str(o.description),
    description_zh: str(o.description_zh, str(o.description)),
    pass_en: str(o.pass_en),
    pass_zh: str(o.pass_zh, str(o.pass_en)),
    best: b && score !== null ? { score, passed: b.passed === true, program: strOrNull(b.program), rev: num(b.rev), t: num(b.t) } : null,
    submissions: num(o.submissions) ?? 0,
  }
}

export function normChallenges(raw: unknown): ChallengeView[] {
  const list = Array.isArray(raw) ? raw : arr(obj(raw).challenges)
  return list.map(normChallenge).filter((c): c is ChallengeView => c !== null)
}

export type JobState = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
const JOB_STATES: readonly JobState[] = ['queued', 'running', 'done', 'failed', 'cancelled']

export interface SeedResult {
  seed: number
  hidden: boolean
  score: number | null
  passed: boolean
  trace_id: string | null
}

export interface ChallengeResult {
  kind: 'challenge'
  challenge: string | null
  program: string | null
  rev: number | null
  score: number
  passed: boolean
  metrics: [string, string | number | boolean | null][]
  seeds: SeedResult[]
  trace_id: string | null
}

export interface DuelEntry {
  program: string
  name: string
  hits: number | null
  hp: number | null
  alive_s: number | null
  survived: boolean
}

export interface DuelRound {
  round: number
  seed: number | null
  /** program id of the winner; null for a draw */
  winner: string | null
  entries: DuelEntry[]
  trace_id: string | null
}

export interface LadderRow {
  program: string
  name: string
  elo: number
  delta: number | null
  played: number
  wins: number
  losses: number
  draws: number
}

export interface DuelResult {
  kind: 'duel'
  rounds: DuelRound[]
  ladder: LadderRow[]
}

export interface JobError {
  code: string
  message_en: string
  message_zh: string
}

export interface JobView {
  id: string
  kind: 'challenge' | 'duel'
  state: JobState
  /** 0..1 */
  progress: number
  error: JobError | null
  result: ChallengeResult | DuelResult | null
}

function metricValue(x: unknown): string | number | boolean | null {
  return typeof x === 'number' || typeof x === 'string' || typeof x === 'boolean' ? x : null
}

export function normLadderRow(raw: unknown): LadderRow | null {
  const o = obj(raw)
  const program = str(o.program, str(o.id))
  const elo = num(o.elo) ?? num(o.rating)
  if (!program || elo === null) return null
  return {
    program,
    name: str(o.name, program),
    elo,
    delta: num(o.delta),
    played: num(o.played) ?? (num(o.wins) ?? 0) + (num(o.losses) ?? 0) + (num(o.draws) ?? 0),
    wins: num(o.wins) ?? 0,
    losses: num(o.losses) ?? 0,
    draws: num(o.draws) ?? 0,
  }
}

export function normLadder(raw: unknown): LadderRow[] {
  const list = Array.isArray(raw) ? raw : arr(obj(raw).ladder ?? obj(raw).rows)
  return list
    .map(normLadderRow)
    .filter((r): r is LadderRow => r !== null)
    .sort((a, b) => b.elo - a.elo)
}

function normResult(raw: unknown, kind: 'challenge' | 'duel'): ChallengeResult | DuelResult | null {
  if (!raw || typeof raw !== 'object') return null
  const o = obj(raw)
  if (kind === 'duel' || Array.isArray(o.rounds)) {
    const rounds = arr(o.rounds).map((r, i): DuelRound => {
      const x = obj(r)
      return {
        round: num(x.round) ?? i + 1,
        seed: num(x.seed),
        winner: strOrNull(x.winner),
        entries: arr(x.entries ?? x.programs).map((e) => {
          const y = obj(e)
          const program = str(y.program, str(y.id))
          return { program, name: str(y.name, program), hits: num(y.hits), hp: num(y.hp), alive_s: num(y.alive_s), survived: y.survived === true }
        }),
        trace_id: strOrNull(x.trace_id),
      }
    })
    return { kind: 'duel', rounds, ladder: normLadder(o.ladder ?? []) }
  }
  const metrics = Object.entries(obj(o.metrics)).map(([k, v]) => [k, metricValue(v)] as [string, string | number | boolean | null])
  return {
    kind: 'challenge',
    challenge: strOrNull(o.challenge),
    program: strOrNull(o.program),
    rev: num(o.rev),
    score: num(o.score) ?? 0,
    passed: o.passed === true,
    metrics,
    seeds: arr(o.seeds).map((s) => {
      const y = obj(s)
      return { seed: num(y.seed) ?? 0, hidden: y.hidden === true, score: num(y.score), passed: y.passed === true, trace_id: strOrNull(y.trace_id) }
    }),
    trace_id: strOrNull(o.trace_id),
  }
}

export function normJob(raw: unknown): JobView {
  const top = obj(raw)
  const o = top.job && typeof top.job === 'object' ? obj(top.job) : top
  const state = JOB_STATES.find((s) => s === o.state) ?? 'queued'
  const kind = o.kind === 'duel' ? 'duel' : o.kind === 'challenge' ? 'challenge' : Array.isArray(obj(o.result).rounds) ? 'duel' : 'challenge'
  let progress = num(o.progress) ?? (state === 'done' ? 1 : 0)
  if (progress > 1) progress = progress / 100
  const e = o.error
  const error: JobError | null =
    typeof e === 'string'
      ? { code: e, message_en: e, message_zh: e }
      : e && typeof e === 'object'
        ? { code: str(obj(e).code, 'job_failed'), message_en: str(obj(e).message_en, str(obj(e).code)), message_zh: str(obj(e).message_zh, str(obj(e).message_en, str(obj(e).code))) }
        : null
  return { id: str(o.id, str(o.job_id)), kind, state, progress: Math.min(1, Math.max(0, progress)), error, result: normResult(o.result, kind) }
}

export const jobFinished = (j: JobView | null) => j !== null && (j.state === 'done' || j.state === 'failed' || j.state === 'cancelled')
