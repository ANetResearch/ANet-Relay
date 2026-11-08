// Dev fixture of a six-aircraft dogfight (?fixture=dogfight, 07 §3–§5): four simulators of this terminal (Alpha:
// SIM-1, SIM-2 and SIM-4 fire, SIM-3 rams) and two of a peer (Bravo), flying a 24 s loop with bursts of fire, hits,
// shoot-downs, a ram that crashes both aircraft, a light bump, a crash into the ground and respawns after 5 s. It
// drives the fleet snapshot of the local simulators, the arena view (remote aircraft, rounds in flight, match,
// board_rev) and the events the stage turns into voxel effects, and it answers the leaderboard, match and dogfight
// scenario routes. `&t0=<s>` starts the loop at that second (screenshots of a given moment). The choreography is a
// pure function of the loop time (dogfightAt; dogfightFixture.test.ts). Nothing here exists in production builds.
import { RelayApiError, type ArenaView, type Vehicle } from '@/api'
import { useArena } from '@/arena/store'
import type { BoardBy } from '@/arena/leaderboard'

type Vec3 = [number, number, number]
type Obj = Record<string, unknown>

export const DF_PERIOD = 24
const PEER = '00000000000000bb'
const RESPAWN_S = 5
const MUZZLE = 35
const FIRE_STEP = 0.1
const MATCH_S = 180
const B1 = `${PEER}/sim-1`
const B2 = `${PEER}/sim-2`

interface Pilot {
  key: string
  local: boolean
  label: string
  role: 'gunner' | 'rammer'
  R: number
  w: number
  phase: number
  h: number
  controller: { kind: 'program' | 'agent' | 'human'; name: string }
}

export const PILOTS: Pilot[] = [
  { key: 'sim-1', local: true, label: 'SIM-1', role: 'gunner', R: 3.2, w: 0.42, phase: 0, h: 1.6, controller: { kind: 'program', name: 'hunter_medium' } },
  { key: 'sim-2', local: true, label: 'SIM-2', role: 'gunner', R: 4.2, w: -0.36, phase: 1.4, h: 2.1, controller: { kind: 'program', name: 'hunter_medium' } },
  { key: 'sim-3', local: true, label: 'SIM-3', role: 'rammer', R: 3.6, w: 0.5, phase: 2.6, h: 1.3, controller: { kind: 'program', name: 'rammer' } },
  { key: 'sim-4', local: true, label: 'SIM-4', role: 'gunner', R: 2.6, w: -0.55, phase: 4.0, h: 1.9, controller: { kind: 'agent', name: 'harness-a' } },
  { key: B1, local: false, label: 'SIM-1', role: 'gunner', R: 4.6, w: 0.33, phase: 3.3, h: 2.4, controller: { kind: 'program', name: 'Duelist' } },
  { key: B2, local: false, label: 'SIM-2', role: 'rammer', R: 3.0, w: -0.47, phase: 5.2, h: 1.4, controller: { kind: 'human', name: 'PAD 1' } },
]

type BeatKind = 'hit' | 'down' | 'ram' | 'bump' | 'ground'
interface Beat {
  t: number
  kind: BeatKind
  by: string | null
  target: string
}

/** The loop's events, in order. */
export const BEATS: Beat[] = [
  { t: 3.0, kind: 'hit', by: 'sim-1', target: B1 },
  { t: 4.2, kind: 'hit', by: B1, target: 'sim-2' },
  { t: 5.5, kind: 'hit', by: 'sim-1', target: B1 },
  { t: 6.0, kind: 'down', by: 'sim-1', target: B1 },
  { t: 10.0, kind: 'ram', by: 'sim-3', target: B2 },
  { t: 12.0, kind: 'bump', by: 'sim-4', target: 'sim-1' },
  { t: 14.5, kind: 'hit', by: B1, target: 'sim-4' },
  { t: 16.0, kind: 'ground', by: null, target: 'sim-2' },
  { t: 18.0, kind: 'down', by: 'sim-4', target: B2 },
  { t: 20.5, kind: 'down', by: B1, target: 'sim-1' },
]

const pilot = (key: string) => PILOTS.find((p) => p.key === key)!
const smooth = (a: number, b: number, t: number) => {
  const k = Math.min(1, Math.max(0, (t - a) / (b - a)))
  return k * k * (3 - 2 * k)
}
const lerp3 = (a: Vec3, b: Vec3, k: number): Vec3 => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k]

function circle(p: Pilot, t: number): Vec3 {
  const a = p.phase + p.w * t
  return [p.R * Math.cos(a), p.R * Math.sin(a), p.h + 0.3 * Math.sin(t * 0.8 + p.phase)]
}

/** When `key` went down or crashed (loop seconds) if it is down at `t`. */
export function downSince(key: string, t: number): number | null {
  for (const b of BEATS) {
    const dies = b.target === key && b.kind !== 'hit' && b.kind !== 'bump'
    const rams = b.kind === 'ram' && b.by === key
    if ((dies || rams) && t >= b.t && t < b.t + RESPAWN_S) return b.t
  }
  return null
}

/** Where `key` flies at `t` while alive: its circle, bent towards a target before a ram, a bump or a ground crash. */
function flying(key: string, t: number): Vec3 {
  const p = pilot(key)
  let pos = circle(p, t)
  if (key === 'sim-3') pos = lerp3(pos, circle(pilot(B2), t), smooth(7.5, 10, t))
  if (key === 'sim-4') {
    const target = circle(pilot('sim-1'), t)
    const near: Vec3 = [target[0] + 0.3, target[1], target[2]]
    pos = lerp3(pos, near, smooth(10.8, 12, t) * (1 - smooth(12, 13.2, t)))
  }
  if (key === 'sim-2') pos = [pos[0], pos[1], pos[2] * (1 - smooth(14.6, 16, t))]
  return pos
}

export interface CraftState {
  key: string
  p: Vec3
  v: Vec3
  alive: boolean
  /** compass heading, rad */
  yaw: number
  roll: number
}

/** Every aircraft at loop time `t`. Pure. */
export function dogfightAt(t: number): CraftState[] {
  return PILOTS.map((p) => {
    const since = downSince(p.key, t)
    if (since !== null) {
      // the wreck falls from where it went down and stays on the ground until the respawn
      const p0 = flying(p.key, since)
      const dt = t - since
      const z = Math.max(0, p0[2] - 0.5 * 9.81 * 0.7 * dt * dt)
      return { key: p.key, p: [p0[0], p0[1], z], v: [0, 0, z > 0 ? -9.81 * 0.7 * dt : 0], alive: false, yaw: p.phase, roll: 0.6 }
    }
    const a = flying(p.key, t)
    const b = flying(p.key, t + 0.05)
    const v: Vec3 = [(b[0] - a[0]) / 0.05, (b[1] - a[1]) / 0.05, (b[2] - a[2]) / 0.05]
    return { key: p.key, p: a, v, alive: true, yaw: Math.atan2(v[0], v[1]), roll: Math.sign(p.w) * 0.22 }
  })
}

export interface FixtureShot {
  id: string
  owner: string
  t: number
  p: Vec3
  v: Vec3
}

/** Rounds fired at loop time in (from, to]: the bursts before every hit and shoot-down, and a short burst of every
 * gunner every 2 s at the nearest enemy (aimed a little wide, so they miss). */
export function shotsBetween(from: number, to: number): FixtureShot[] {
  const out: FixtureShot[] = []
  const fire = (owner: string, target: string, t: number, wide: number, id: string) => {
    if (!(t > from && t <= to) || downSince(owner, t) !== null) return
    const me = dogfightAt(t).find((c) => c.key === owner)!
    const them = dogfightAt(t).find((c) => c.key === target)!
    const d = Math.hypot(them.p[0] - me.p[0], them.p[1] - me.p[1], them.p[2] - me.p[2]) || 1
    const lead = d / MUZZLE
    const aim: Vec3 = [them.p[0] + them.v[0] * lead + wide, them.p[1] + them.v[1] * lead - wide * 0.5, them.p[2] + them.v[2] * lead + 0.1]
    const a = Math.hypot(aim[0] - me.p[0], aim[1] - me.p[1], aim[2] - me.p[2]) || 1
    out.push({ id, owner, t, p: me.p, v: [((aim[0] - me.p[0]) / a) * MUZZLE, ((aim[1] - me.p[1]) / a) * MUZZLE, ((aim[2] - me.p[2]) / a) * MUZZLE + 0.4] })
  }
  BEATS.forEach((b, i) => {
    if ((b.kind !== 'hit' && b.kind !== 'down') || !b.by) return
    for (let k = 0; k < 7; k++) fire(b.by, b.target, b.t - 0.15 - k * FIRE_STEP, 0, `b${i}:${k}`)
  })
  PILOTS.forEach((p, i) => {
    if (p.role !== 'gunner') return
    for (let s = 0; s < DF_PERIOD; s += 2) {
      const t0 = s + 0.3 * i
      const me = dogfightAt(t0).find((c) => c.key === p.key)!
      const enemies = dogfightAt(t0).filter((c) => c.alive && pilot(c.key).local !== p.local)
      if (!enemies.length) continue
      const near = enemies.reduce((a, c) => (Math.hypot(c.p[0] - me.p[0], c.p[1] - me.p[1]) < Math.hypot(a.p[0] - me.p[0], a.p[1] - me.p[1]) ? c : a))
      for (let k = 0; k < 5; k++) fire(p.key, near.key, t0 + k * FIRE_STEP, 0.7, `a${i}:${s}:${k}`)
    }
  })
  return out.sort((a, b) => a.t - b.t)
}

// ---------------------------------------------------------------- leaderboard

interface Tally {
  downs: number
  hits: number
  shots: number
  rams: number
  deaths: number
  crashes: number
  damage: number
}

/** Board rows after `loops` whole loops and `t` seconds of the current one. */
export function boardRows(loops: number, t: number): Obj[] {
  const tally = new Map<string, Tally>(PILOTS.map((p) => [p.key, { downs: 0, hits: 0, shots: 0, rams: 0, deaths: 0, crashes: 0, damage: 0 }]))
  const add = (upto: number, times: number) => {
    for (const b of BEATS) {
      if (b.t > upto) break
      const by = b.by ? tally.get(b.by)! : null
      const target = tally.get(b.target)!
      if ((b.kind === 'hit' || b.kind === 'down') && by) {
        by.hits += times
        by.damage += 25 * times
      }
      if (b.kind === 'down' && by) by.downs += times
      if (b.kind === 'down') target.deaths += times
      if (b.kind === 'ram' && by) {
        by.rams += times
        by.crashes += times
        target.crashes += times
      }
      if (b.kind === 'ground') target.crashes += times
    }
    for (const s of shotsBetween(-1, upto)) tally.get(s.owner)!.shots += times
  }
  if (loops > 0) add(DF_PERIOD, loops)
  add(t, 1)
  return PILOTS.map((p) => {
    const x = tally.get(p.key)!
    const since = downSince(p.key, t)
    // alive since the last respawn of this loop (or the loop start)
    const last = BEATS.filter((b) => (b.target === p.key || (b.kind === 'ram' && b.by === p.key)) && b.kind !== 'hit' && b.kind !== 'bump' && b.t + RESPAWN_S <= t).map((b) => b.t + RESPAWN_S)
    const alive = since !== null ? 0 : t - (last.length ? Math.max(...last) : 0) + (last.length ? 0 : loops * 4)
    const callsign = p.local ? 'Alpha' : 'Bravo'
    return {
      key: p.local ? p.label : `${callsign} / ${p.label}`, aircraft: p.label, callsign, local: p.local, controller: p.controller,
      downs: x.downs, hits: x.hits, shots: x.shots, accuracy: x.shots ? x.hits / x.shots : null, rams: x.rams, deaths: x.deaths,
      crashes: x.crashes, alive_s: alive, best_alive_s: Math.max(alive, 9 + PILOTS.indexOf(p) * 2.5 + loops * 3), damage: x.damage,
    }
  })
}

// ---------------------------------------------------------------- the stand-in

export interface DogfightHost {
  base(over: Partial<Vehicle> & Pick<Vehicle, 'id' | 'label' | 'kind'>): Vehicle
  setVehicles(vs: Vehicle[]): void
  emit(kind: string, vehicle: string | null, data: Obj): void
}

function localVehicle(host: DogfightHost, c: CraftState, now: number, startedAt: number): Vehicle {
  const p = pilot(c.key)
  const n = Number(c.key.slice(4))
  return host.base({
    id: c.key, label: p.label, kind: 'sim', sysid: 10 + n, armed: c.alive, mode: 'STABILIZE', mode_class: 'manual', landed: c.alive ? 'air' : 'ground', crashed: !c.alive,
    position: { frame: 'sim', x: c.p[0], y: c.p[1], z: c.p[2], vx: c.v[0], vy: c.v[1], vz: c.v[2], age_s: 0.02 },
    attitude: { roll: c.roll, pitch: -0.12, yaw: c.yaw }, altitude_m: c.p[2], groundspeed_ms: Math.hypot(c.v[0], c.v[1]), heading_deg: ((c.yaw * 180) / Math.PI + 360) % 360,
    throttle_pct: c.alive ? 56 : 0, flight_time_s: now - startedAt, armed_time_s: now - startedAt,
    controller: p.controller.kind === 'agent' ? 'agent' : 'program',
    claim: p.controller.kind === 'agent' ? { session_id: 's-7f3a', name: p.controller.name, since: startedAt, suspended: false } : null,
    sim: { hover_assist: false, turbulence: 0.3, airframe: { preset: 'typical', seed: 7 } },
    program: p.controller.kind === 'program'
      ? {
          id: p.controller.name, name: p.controller.name, rev: 1, state: c.alive ? (p.role === 'rammer' ? 'pursue' : 'fire') : 'recover', status: 'running', reason: null, mode: 'pilot',
          fps: 50, frames: 0, faults: 0, overruns: 0, last_error: null, params: {}, seed: 7, rates_ok: true, sm: null, log_seq: 0,
          owner: { kind: 'operator', name: 'operator' }, started_at: startedAt,
        }
      : null,
  })
}

function boardBody(by: BoardBy, rows: Obj[], match: Obj | null): Obj {
  return { by, match, rows }
}

/** Starts the dogfight at 20 Hz. Returns its REST handler (undefined: not a dogfight route) and a stop function. */
export function startDogfightFixture(host: DogfightHost): { vehicles(now: number): Vehicle[]; handle(method: string, path: string, body: unknown): unknown; stop(): void } {
  const q = new URLSearchParams(globalThis.location?.search ?? '')
  const offset = Math.min(DF_PERIOD - 0.01, Math.max(0, Number(q.get('t0') ?? 0) || 0))
  const t0 = Date.now() / 1000 - offset
  let running = true
  let matchStart = t0
  let rev = 0
  let lastT = offset - 1e-6
  let lastLoop = 0
  const loopAt = (now: number) => {
    const e = now - t0
    return { loop: Math.floor(e / DF_PERIOD), t: e - Math.floor(e / DF_PERIOD) * DF_PERIOD }
  }
  const match = (now: number): Obj => ({ id: `m-${Math.round(matchStart)}`, started_at: matchStart, ends_at: matchStart + MATCH_S, running: running && now < matchStart + MATCH_S })
  const hp = new Map<string, number>()

  const vehicles = (now: number) => {
    const { t } = loopAt(now)
    return dogfightAt(t).filter((c) => pilot(c.key).local).map((c) => localVehicle(host, c, now, t0))
  }

  const emitBeat = (b: Beat, t: number) => {
    const all = dogfightAt(t)
    const at = (k: string) => all.find((c) => c.key === k)!.p.map((x) => Math.round(x * 1000) / 1000) as Vec3
    const local = (k: string | null) => (k ? pilot(k).local : false)
    const label = (k: string) => (pilot(k).local ? pilot(k).label : `Bravo / ${pilot(k).label}`)
    const point = at(b.target)
    if (b.kind === 'hit' || b.kind === 'down') {
      const down = b.kind === 'down'
      const left = down ? 0 : Math.max(25, (hp.get(b.target) ?? 100) - 25)
      hp.set(b.target, left)
      const from = b.by ? at(b.by) : point
      const d = Math.hypot(point[0] - from[0], point[1] - from[1], point[2] - from[2]) || 1
      const dir = [((point[0] - from[0]) / d) * MUZZLE, ((point[1] - from[1]) / d) * MUZZLE, ((point[2] - from[2]) / d) * MUZZLE]
      if (local(b.target)) {
        const data = { side: 'taken', target: b.target, shooter: b.by, own_shot: local(b.by), point, dir, down, hp: left, detail_en: `was hit by ${b.by ? label(b.by) : ''}`, detail_zh: '被命中' }
        host.emit('arena_hit', b.target, data)
        if (down) host.emit('arena_down', b.target, { side: 'taken', target: b.target, shooter: b.by, point })
      } else {
        host.emit('arena_hit', b.by, { side: 'hit', target: b.target, shooter: b.by, point, down })
        if (down) host.emit('arena_down', b.by, { side: 'hit', target: b.target, shooter: b.by, point })
      }
    } else if (b.kind === 'ram' || b.kind === 'bump') {
      const a = at(b.by!)
      const mid: Vec3 = [(a[0] + point[0]) / 2, (a[1] + point[1]) / 2, (a[2] + point[2]) / 2]
      const crashed = b.kind === 'ram' ? [b.by!, b.target] : []
      host.emit('collision', b.by, { a: b.by, b: b.target, b_label: label(b.target), point: mid, v_rel: b.kind === 'ram' ? 3.6 : 1.1, crashed, kind: 'craft' })
      if (b.kind === 'ram') host.emit('crashed', b.by, { reason_code: 'collision', point: a, text: 'collision' })
    } else if (b.kind === 'ground') {
      host.emit('crashed', b.target, { reason_code: 'ground', point: [point[0], point[1], 0], text: 'ground' })
    }
    rev++
  }

  const push = () => {
    const now = Date.now() / 1000
    const { loop, t } = loopAt(now)
    if (loop !== lastLoop) {
      lastLoop = loop
      lastT = -1e-6
      hp.clear()
    }
    for (const b of BEATS) if (b.t > lastT && b.t <= t) emitBeat(b, b.t)
    // respawns refill the hit points
    for (const p of PILOTS) if (downSince(p.key, t) === null && downSince(p.key, lastT) !== null) hp.set(p.key, 100)
    const loopStart = now - t
    const shots = shotsBetween(t - 1.2, t).map((s) => ({ id: `${s.owner}:${loop}:${s.id}`, owner: pilot(s.owner).local ? '00000000000000aa' : PEER, mine: pilot(s.owner).local, t0: loopStart + s.t, p: s.p, v: s.v }))
    lastT = t
    host.setVehicles(vehicles(now))
    const crafts = dogfightAt(t)
    const remote = crafts.filter((c) => !pilot(c.key).local).map((c) => ({
      key: c.key, label: pilot(c.key).label, x: c.p[0], y: c.p[1], z: c.p[2], vx: c.v[0], vy: c.v[1], vz: c.v[2],
      roll: c.roll, pitch: -0.1, yaw: c.yaw, throttle: c.alive ? 0.55 : 0, armed: c.alive, down: !c.alive, hp: c.alive ? hp.get(c.key) ?? 100 : 0,
    }))
    const localKeys = PILOTS.filter((p) => p.local).map((p) => p.key)
    const view: ArenaView & Obj = {
      t: now, enabled: true, instance: '00000000000000aa', seat: 0, callsign: 'Alpha', error: null,
      peers: [{ id: PEER, callsign: 'Bravo', seat: 6, rtt_ms: 2.1, stale: false, age_s: 0.04, score: { hits: 4, downs: 2, deaths: 3 }, vehicles: remote }],
      shots,
      local: {
        ammo: Object.fromEntries(localKeys.map((k, i) => [k, 24 - ((Math.floor(t * 4) + i * 5) % 25)])),
        hp: Object.fromEntries(localKeys.map((k) => [k, downSince(k, t) === null ? hp.get(k) ?? 100 : 0])),
        reloading: {}, reload: {}, down: Object.fromEntries(localKeys.map((k) => [k, downSince(k, t) !== null])),
        max_ammo: 24, max_hp: 100, score: { hits: 6, downs: 3, deaths: 2 },
      },
      match: match(now),
      board_rev: rev,
    }
    useArena.getState().applyArena(view)
  }
  push()
  const timer = setInterval(push, 50)

  return {
    vehicles,
    handle(method, path, body) {
      const now = Date.now() / 1000
      const [route, query = ''] = path.split('?')
      if (route === '/arena/leaderboard' && method === 'GET') {
        const by = (new URLSearchParams(query).get('by') ?? 'downs') as BoardBy
        const { loop, t } = loopAt(now)
        return boardBody(by, boardRows(loop, t), match(now))
      }
      if (route === '/arena/match') {
        if (method === 'POST') {
          matchStart = now
          running = true
        } else if (method === 'DELETE') running = false
        rev++
        return { match: method === 'DELETE' ? null : match(now) }
      }
      if (route === '/scenarios/dogfight') {
        if (method === 'POST') {
          const b = (body ?? {}) as Obj
          const n = typeof b.aircraft === 'number' ? b.aircraft : 6
          if (n < 2 || n > 12) throw new RelayApiError(400, 'invalid_scenario', 'A dogfight needs 2 to 12 simulated aircraft.', '狗斗需要 2 至 12 架模拟机。')
          running = true
          matchStart = now
          rev++
        } else if (method === 'DELETE') {
          running = false
          rev++
        }
        return {
          running, started_at: matchStart, ends_at: matchStart + MATCH_S,
          config: { aircraft: 6, teams: 'ffa', roles: { gunner: 4, rammer: 2 }, duration_s: MATCH_S, seed: 7 },
          aircraft: PILOTS.filter((p) => p.local).map((p) => ({ vid: p.key, role: p.role, program: p.controller.kind === 'program' ? p.controller.name : null, team: null })),
        }
      }
      return undefined
    },
    stop: () => clearInterval(timer),
  }
}

/** The leaderboard of the busy fixture's arena (?fixture=busy&arena=1): its two peers' aircraft and ours. */
export function busyBoard(by: BoardBy): Obj {
  const ctl = (kind: string, name: string) => ({ kind, name })
  return boardBody(by, [
    { key: 'SIM-1', aircraft: 'SIM-1', callsign: 'Alpha', local: true, controller: ctl('human', 'PAD 2'), downs: 1, hits: 3, shots: 22, rams: 0, deaths: 1, crashes: 0, alive_s: 41, best_alive_s: 63, damage: 75 },
    { key: 'SIM-2', aircraft: 'SIM-2', callsign: 'Alpha', local: true, controller: ctl('agent', 'harness-a'), downs: 0, hits: 1, shots: 9, rams: 0, deaths: 0, crashes: 0, alive_s: 58, best_alive_s: 58, damage: 25 },
    { key: 'Bravo / SIM-1', aircraft: 'SIM-1', callsign: 'Bravo', local: false, controller: ctl('program', 'Duelist'), downs: 1, hits: 3, shots: 30, rams: 0, deaths: 0, crashes: 0, alive_s: 77, best_alive_s: 77, damage: 75 },
    { key: 'Bravo / SIM-2', aircraft: 'SIM-2', callsign: 'Bravo', local: false, controller: ctl('none', ''), downs: 0, hits: 0, shots: 0, rams: 0, deaths: 1, crashes: 1, alive_s: 12, best_alive_s: 40, damage: 0 },
  ], null)
}
