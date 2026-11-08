// Leaderboard model (07 §4, §5): tolerant readers of GET /arena/leaderboard and GET /scenarios/dogfight, the order
// of each tab and the text of its two metric columns. Pure: no React, no three.js (leaderboard.test.ts).
//
// Tabs and their columns:
//   downs     primary: downs            secondary: lost (deaths)      order: downs, hits, fewer deaths
//   survival  primary: best time alive  secondary: alive now          order: best, now, fewer deaths
//   accuracy  primary: hits / shots     secondary: "hits / shots"     order: accuracy (no shots last), shots
//   rams      primary: rams             secondary: crashes            order: rams, downs, fewer crashes
// Ties fall back to the row key, so the order never flickers between two refetches of an unchanged board.
import { duration } from '@/lib/format'

export const BOARD_TABS = ['downs', 'survival', 'accuracy', 'rams'] as const
export type BoardBy = (typeof BOARD_TABS)[number]

export type ControllerKind = 'program' | 'agent' | 'human' | 'none'

export interface LeaderboardRow {
  /** the backend's row key: "sim-n" for this terminal's aircraft, "<peer instance>/sim-n" for a peer's */
  key: string
  /** the aircraft's label ("SIM-n") */
  aircraft: string
  callsign: string
  local: boolean
  controller: { kind: ControllerKind; name: string }
  downs: number
  hits: number
  shots: number
  /** 0..1, null without shots */
  accuracy: number | null
  rams: number
  deaths: number
  crashes: number
  /** seconds alive since the last spawn, 0 while down */
  alive_s: number
  best_alive_s: number
  damage: number
}

export interface BoardMatch {
  id: string
  /** backend wall clock, seconds */
  started_at: number | null
  ends_at: number | null
  running: boolean
}

export interface Leaderboard {
  by: BoardBy
  match: BoardMatch | null
  rows: LeaderboardRow[]
}

export interface DogfightAircraft {
  vid: string
  role: string
  program: string | null
  team: string | null
}

export interface DogfightStatus {
  running: boolean
  started_at: number | null
  ends_at: number | null
  config: Record<string, unknown> | null
  aircraft: DogfightAircraft[]
}

type Obj = Record<string, unknown>
const obj = (x: unknown): Obj => (x && typeof x === 'object' && !Array.isArray(x) ? (x as Obj) : {})
const finite = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x)
const num = (x: unknown): number => (finite(x) && x > 0 ? x : 0)
const text = (x: unknown, max = 64): string => (typeof x === 'string' ? x.slice(0, max) : '')
const KINDS: readonly ControllerKind[] = ['program', 'agent', 'human', 'none']

export const isBoardBy = (x: unknown): x is BoardBy => typeof x === 'string' && (BOARD_TABS as readonly string[]).includes(x)

export function normMatch(raw: unknown): BoardMatch | null {
  const m = obj(raw)
  if (!Object.keys(m).length) return null
  return {
    id: typeof m.id === 'string' ? m.id : finite(m.id) ? String(m.id) : '',
    started_at: finite(m.started_at) ? m.started_at : null,
    ends_at: finite(m.ends_at) ? m.ends_at : null,
    running: m.running === true,
  }
}

export function normRow(raw: unknown): LeaderboardRow | null {
  const r = obj(raw)
  const key = text(r.key, 96)
  if (!key) return null
  const c = obj(r.controller)
  const kind = KINDS.includes(c.kind as ControllerKind) ? (c.kind as ControllerKind) : 'none'
  const shots = num(r.shots)
  const hits = num(r.hits)
  // the backend's figure first; derived from hits / shots otherwise; null without shots
  const accuracy = shots > 0 ? (finite(r.accuracy) ? Math.min(1, Math.max(0, r.accuracy > 1 ? r.accuracy / 100 : r.accuracy)) : Math.min(1, hits / shots)) : null
  return {
    key,
    aircraft: text(r.aircraft) || key,
    callsign: text(r.callsign, 32),
    local: r.local === true,
    // controller names come from peers: plain text, at most 32 characters (07 §4)
    controller: { kind, name: text(c.name, 32) },
    downs: num(r.downs),
    hits,
    shots,
    accuracy,
    rams: num(r.rams),
    deaths: num(r.deaths),
    crashes: num(r.crashes),
    alive_s: num(r.alive_s),
    best_alive_s: Math.max(num(r.best_alive_s), num(r.alive_s)),
    damage: num(r.damage),
  }
}

export function normBoard(raw: unknown, by: BoardBy): Leaderboard {
  const o = obj(raw)
  const rows = (Array.isArray(o.rows) ? o.rows : []).map(normRow).filter((r): r is LeaderboardRow => r !== null)
  return { by: isBoardBy(o.by) ? o.by : by, match: normMatch(o.match), rows }
}

export function normDogfight(raw: unknown): DogfightStatus {
  const o = obj(raw)
  const s = Object.keys(obj(o.scenario)).length ? obj(o.scenario) : o
  const aircraft = (Array.isArray(s.aircraft) ? s.aircraft : []).flatMap((a): DogfightAircraft[] => {
    const x = obj(a)
    const vid = text(x.vid)
    return vid ? [{ vid, role: text(x.role) || 'gunner', program: text(x.program) || null, team: x.team === null || x.team === undefined ? null : String(x.team) }] : []
  })
  return {
    running: s.running === true,
    started_at: finite(s.started_at) ? s.started_at : null,
    ends_at: finite(s.ends_at) ? s.ends_at : null,
    config: Object.keys(obj(s.config)).length ? obj(s.config) : null,
    aircraft,
  }
}

type Cmp = (a: LeaderboardRow, b: LeaderboardRow) => number
const desc = (f: (r: LeaderboardRow) => number): Cmp => (a, b) => f(b) - f(a)
const asc = (f: (r: LeaderboardRow) => number): Cmp => (a, b) => f(a) - f(b)

const ORDER: Record<BoardBy, Cmp[]> = {
  downs: [desc((r) => r.downs), desc((r) => r.hits), asc((r) => r.deaths)],
  survival: [desc((r) => r.best_alive_s), desc((r) => r.alive_s), asc((r) => r.deaths)],
  accuracy: [desc((r) => (r.accuracy === null ? -1 : r.accuracy)), desc((r) => r.shots), desc((r) => r.hits)],
  rams: [desc((r) => r.rams), desc((r) => r.downs), asc((r) => r.crashes)],
}

/** Rows in the order of a tab (a new array; the input is not touched). */
/** The name a row is shown with (07 §4): "SIM-n" for this terminal's aircraft, "callsign / SIM-n" for a peer's. */
export function rowLabel(r: Pick<LeaderboardRow, 'aircraft' | 'callsign' | 'local'>): string {
  if (r.local || !r.callsign || r.aircraft.includes(' / ')) return r.aircraft
  return `${r.callsign} / ${r.aircraft}`
}

export function sortRows(rows: readonly LeaderboardRow[], by: BoardBy): LeaderboardRow[] {
  const cmps = ORDER[by]
  return [...rows].sort((a, b) => {
    for (const c of cmps) {
      const d = c(a, b)
      if (d) return d
    }
    return a.key < b.key ? -1 : a.key > b.key ? 1 : 0
  })
}

/** Ranks with shared places for equal rows (1, 2, 2, 4), in the order given. */
export function ranks(rows: readonly LeaderboardRow[], by: BoardBy): number[] {
  const cmps = ORDER[by]
  return rows.map((r, i) => {
    let k = i
    while (k > 0 && cmps.every((c) => c(rows[k - 1], r) === 0)) k--
    return k + 1
  })
}

/** "54 %", or null without shots. */
export function accuracyText(r: Pick<LeaderboardRow, 'accuracy'>): string | null {
  return r.accuracy === null ? null : `${Math.round(r.accuracy * 100)} %`
}

/** The two metric cells of a row for a tab ("—" stands for null at the call site). */
export function metricCells(r: LeaderboardRow, by: BoardBy): [string | null, string | null] {
  switch (by) {
    case 'downs':
      return [String(r.downs), String(r.deaths)]
    case 'survival':
      return [duration(r.best_alive_s), r.alive_s > 0 ? duration(r.alive_s) : null]
    case 'accuracy':
      return [accuracyText(r), `${r.hits} / ${r.shots}`]
    case 'rams':
      return [String(r.rams), String(r.crashes)]
  }
}

/** Seconds left in a match at backend time `now` (null without an end; never negative). */
export function matchLeft(m: BoardMatch | null, now: number): number | null {
  if (!m || !m.running || m.ends_at === null) return null
  return Math.max(0, m.ends_at - now)
}
