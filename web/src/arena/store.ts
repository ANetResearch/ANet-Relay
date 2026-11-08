// LAN arena state of this window (05 §7.5): the latest `arena` stream message (20 Hz, apart from the 10 Hz fleet
// snapshot), the hit marks derived from arena events, and the actions of the top-bar toggle. Peers live here and
// never in the relay store's fleet, so they never reach the fleet panel, records or camera framing. Writers: the
// connection layer (applyArena) and the dev fixture. The frame loop reads useArena.getState() directly.
import * as React from 'react'
import { create } from 'zustand'
import { api, sendJson, type ArenaView, type RelayEvent } from '@/api'
import { useRelay } from '@/store/relay'

export type { ArenaView }
export type ArenaPeer = ArenaView['peers'][number]
export type ArenaRemote = ArenaPeer['vehicles'][number]
export type ArenaShot = ArenaView['shots'][number]

/** A hit this window shows: `hit` = one of our rounds struck, `taken` = one of our aircraft was struck. */
export interface ArenaMark {
  seq: number
  kind: 'hit' | 'taken'
  /** our aircraft involved (the shooter for `hit`, the target for `taken`) */
  vehicle: string | null
  /** performance.now() when the event arrived */
  at: number
  /** hit point in the local ENU frame, when known */
  point: [number, number, number] | null
  /** direction of the round's travel (local ENU), for the edge indicator */
  dir: [number, number, number] | null
  down: boolean
}

const MARK_KEEP_MS = 2000

export interface ArenaState {
  view: ArenaView | null
  /** performance.now() when the view arrived */
  at: number
  marks: ArenaMark[]
  applyArena(v: ArenaView): void
  addMarks(m: ArenaMark[]): void
  reset(): void
}

export const useArena = create<ArenaState>()((set, get) => ({
  view: null,
  at: 0,
  marks: [],
  applyArena: (view) => set({ view, at: performance.now() }),
  addMarks: (m) => {
    if (!m.length) return
    const now = performance.now()
    set({ marks: [...get().marks.filter((x) => now - x.at < MARK_KEEP_MS), ...m] })
  },
  reset: () => set({ view: null, at: 0, marks: [] }),
}))

const vec3 = (x: unknown): [number, number, number] | null =>
  Array.isArray(x) && x.length === 3 && x.every((n) => typeof n === 'number' && Number.isFinite(n)) ? [x[0], x[1], x[2]] : null

/** Hit marks of a batch of events (pure; arena_hit only). */
export function marksOf(events: readonly RelayEvent[], at: number): ArenaMark[] {
  const out: ArenaMark[] = []
  for (const e of events) {
    if (e.kind !== 'arena_hit') continue
    const d = e.data
    const kind = d.side === 'hit' ? 'hit' : d.side === 'taken' ? 'taken' : null
    if (!kind) continue
    // a round of ours that struck one of our own simulators counts as both
    if (kind === 'taken' && d.own_shot === true) out.push({ seq: e.seq, kind: 'hit', vehicle: typeof d.shooter === 'string' ? d.shooter : null, at, point: vec3(d.point), dir: vec3(d.dir), down: d.down === true })
    out.push({ seq: e.seq, kind, vehicle: e.vehicle, at, point: vec3(d.point), dir: vec3(d.dir), down: d.down === true })
  }
  return out
}

let watchers = 0
let unwatch: (() => void) | null = null

/** Turns arena events into marks while a consumer is mounted (reference counted). */
export function watchArenaEvents(): () => void {
  if (watchers++ === 0) {
    let last = useRelay.getState().lastSeq
    unwatch = useRelay.subscribe((s) => {
      if (s.lastSeq === last) return
      const fresh = s.events.filter((e) => e.seq > last)
      last = s.lastSeq
      useArena.getState().addMarks(marksOf(fresh, performance.now()))
    })
  }
  return () => {
    if (--watchers === 0) {
      unwatch?.()
      unwatch = null
    }
  }
}

// ---------------------------------------------------------------- v0.5 view fields (WP-C)

/**
 * v0.5 additions to the arena stream view (07 §4, WP-B): the running match and `board_rev`, an integer that changes
 * whenever the leaderboard changes, so the window refetches GET /arena/leaderboard only then. Read tolerantly: an
 * older backend sends neither, and the generated ArenaView type gains them only once the schema is regenerated.
 */
export interface ArenaViewV5 {
  match?: { id?: unknown; started_at?: unknown; ends_at?: unknown; running?: unknown } | null
  board_rev?: number | null
}

const v5 = (v: ArenaView | null): ArenaViewV5 => (v ?? {}) as ArenaViewV5

/** The board revision of the view (null when the backend sends none). */
export function boardRevOf(v: ArenaView | null): number | null {
  const r = v5(v).board_rev
  return typeof r === 'number' && Number.isFinite(r) ? r : null
}

/** Re-renders when the board revision or the match changes (the leaderboard panel's refetch trigger). */
export function useBoardSignal(): string {
  return useArena((s) => {
    const v = s.view
    if (!v) return ''
    const m = v5(v).match
    return `${v.enabled ? 1 : 0}|${boardRevOf(v) ?? ''}|${m ? `${String(m.id ?? '')}:${m.running === true ? 1 : 0}:${String(m.ends_at ?? '')}` : ''}`
  })
}

// ---------------------------------------------------------------- selectors

export const useArenaEnabled = () => useArena((s) => Boolean(s.view?.enabled))

/** Peers in the arena (re-renders only when the count changes). */
export const useArenaPeerCount = () => useArena((s) => (s.view?.enabled ? s.view.peers.length : 0))

export interface ArenaLocal {
  hp: number
  maxHp: number
  down: boolean
  ammo: { n: number; max: number; reloading: boolean; progress: number }
}

/** Hit points and magazine of one local simulator while the arena is on; null otherwise (DrivingHud slot). */
export function useArenaLocal(vehicleId: string | null): ArenaLocal | null {
  const key = useArena((s) => {
    const v = s.view
    if (!v?.enabled || !vehicleId || !(vehicleId in v.local.hp)) return ''
    const l = v.local
    const reload = l.reload[vehicleId] ?? 0
    return [l.hp[vehicleId], l.max_hp, l.down[vehicleId] ? 1 : 0, l.ammo[vehicleId] ?? 0, l.max_ammo, l.reloading[vehicleId] ? 1 : 0, reload].join('|')
  })
  return React.useMemo(() => {
    if (!key) return null
    const [hp, maxHp, down, n, max, reloading, progress] = key.split('|').map(Number)
    return { hp, maxHp, down: down === 1, ammo: { n, max, reloading: reloading === 1, progress } }
  }, [key])
}

// ---------------------------------------------------------------- actions

const DISCLOSED = 'relay.arena.disclosed'

/** True once the operator has read the first-use disclosure in this window profile. */
export function arenaDisclosed(): boolean {
  try {
    return localStorage.getItem(DISCLOSED) === '1'
  } catch {
    return false
  }
}

export function markArenaDisclosed(): void {
  try {
    localStorage.setItem(DISCLOSED, '1')
  } catch {
    /* storage unavailable: the disclosure shows again next time */
  }
}

/** Join or leave the arena (backend setting peer_enabled). */
export function setArenaEnabled(on: boolean): Promise<unknown> {
  return api.putSettings({ peer_enabled: on })
}

/**
 * Fire input of the controller (05 §7.4): hold-type, refreshed every 100 ms while held; the backend releases the
 * trigger 300 ms after the last refresh. The EngineActions.fire hook of control/engine.ts calls this.
 */
export function sendFire(vehicle: string, on: boolean): boolean {
  return sendJson({ type: 'fire', vehicle, on })
}
