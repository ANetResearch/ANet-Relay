// Live feed of the stage. A store subscription (outside React) turns every snapshot into placement targets; the
// frame loop smooths towards them. While Records replays a flight (records/replayState.ts, CONTRACTS §5) the only
// target is the replayed aircraft at the cursor.
// Also holds the per-aircraft smoothed scene state other layers read (course guide line, camera framing).
import * as THREE from 'three'
import type { Fleet, Vehicle } from '@/api'
import { api } from '@/api'
import { useRelay } from '@/store/relay'
import { useReplay, type ReplayState } from '@/records/replayState'
import { create } from 'zustand'
import type { Quat } from './math'
import { fallbackSpawn, frameOffsets, framesUsed, NO_OFFSETS, place, slotPosition, slotRow, type FrameOffsets, type PlaceKind, type SlotRow } from './placement'

/** Frame offsets for React consumers that place things in an aircraft's frame (course rings). */
export const useFrameOffsets = create<FrameOffsets>(() => NO_OFFSETS)

export interface Target {
  id: string
  kind: PlaceKind
  sim: boolean
  /** ENU metres and m/s */
  x: number
  y: number
  z: number
  vx: number
  vy: number
  vz: number
  /** radians; null while the autopilot sends no attitude */
  roll: number | null
  pitch: number | null
  yaw: number | null
  /** 0..1 for the propeller spin */
  throttle: number
  armed: boolean
  /** performance.now() of the snapshot */
  at: number
}

/** Smoothed scene state of one aircraft, written by the frame loop. */
export interface AircraftState {
  /** scene coordinates of the body */
  pos: THREE.Vector3
  /** heading of the smoothed orientation (math.ts quatHeading), radians clockwise from north */
  yaw: number
  /** smoothed orientation in the scene frame (11 §8.5: slerp, so inverted flight and tumbling never jump) */
  q: Quat
  kind: PlaceKind
  ready: boolean
}

export const live = {
  targets: new Map<string, Target>(),
  row: { y: -2.1, cx: 0 } as SlotRow,
  /** simulator spawn points (ENU) by id */
  spawns: new Map<string, [number, number]>(),
  /** where each aircraft's safety volume is centred (ENU) */
  safetyOrigins: new Map<string, [number, number, number]>(),
  safety: { x: 3, y: 3, z: 2 },
  /** display offsets of the real local frame and the GPS plane (placement.ts frameOffsets) */
  offsets: NO_OFFSETS as FrameOffsets,
  state: new Map<string, AircraftState>(),
  /** true while the stage shows a replay instead of the live fleet */
  replaying: false,
  /** bumped whenever the target set is rebuilt */
  version: 0,
}

const fetched = new Set<string>()

/**
 * A simulator's level error (radians, roll and pitch) for this flight. ATTITUDE is the board's estimate and leaves
 * the level error out by design (10 §3): the board believes it is level. The stage draws the airframe itself, so it
 * adds the error back. The value comes from the operator-only airframe view (06 §4), never from the snapshot.
 */
const levelErrors = new Map<string, { key: string; at: number; rad: [number, number] }>()
const LEVEL_REFRESH_MS = 3000

function levelError(v: Vehicle, now: number): [number, number] {
  if (v.kind !== 'sim' || !v.sim) return [0, 0]
  const key = `${v.armed}:${v.sim.hover_assist}:${v.sim.airframe.preset}:${v.sim.airframe.seed}`
  const got = levelErrors.get(v.id)
  if (!got || got.key !== key || (v.armed && now - got.at > LEVEL_REFRESH_MS)) {
    levelErrors.set(v.id, { key, at: now, rad: got?.rad ?? [0, 0] })
    api
      .simAirframe(v.id)
      .then((af) => {
        const deg = af.active ? (af.level_now_deg ?? [0, 0]) : [0, 0]
        const rad: [number, number] = [((deg[0] ?? 0) * Math.PI) / 180, ((deg[1] ?? 0) * Math.PI) / 180]
        const cur = levelErrors.get(v.id)
        if (cur) cur.rad = rad
      })
      .catch(() => {
        /* not the operator: the estimate is all there is */
      })
  }
  return levelErrors.get(v.id)?.rad ?? [0, 0]
}

function throttleOf(v: Pick<Vehicle, 'armed' | 'throttle_pct' | 'sticks'>): number {
  if (!v.armed) return 0
  if (typeof v.throttle_pct === 'number') return Math.min(1, Math.max(0, v.throttle_pct / 100))
  if (v.sticks) return Math.min(1, Math.max(0, v.sticks.throttle))
  return 0.5
}

function spawnDetail(id: string): void {
  if (fetched.has(id)) return
  fetched.add(id)
  api
    .vehicle(id)
    .then((d) => {
      const o = d as unknown as { spawn?: unknown; safety_origin?: unknown }
      if (Array.isArray(o.spawn) && o.spawn.length >= 2 && o.spawn.every((n) => typeof n === 'number')) {
        live.spawns.set(id, [o.spawn[0] as number, o.spawn[1] as number])
      }
      if (Array.isArray(o.safety_origin) && o.safety_origin.length >= 3) {
        const [x, y, z] = o.safety_origin as number[]
        live.safetyOrigins.set(id, [x, y, z])
      }
      rebuild()
    })
    .catch(() => {
      /* keep the fallback grid */
    })
}

function replayTargets(r: ReplayState, now: number): Map<string, Target> {
  const out = new Map<string, Target>()
  // WP-3: a multi-aircraft trace (06 §3.4) places every aircraft by its recorded arena position
  if (r.crafts) {
    for (const c of r.crafts) {
      const f = c.frame
      out.set(c.id, {
        id: c.id, kind: 'position', sim: c.sim,
        x: f.x ?? 0, y: f.y ?? 0, z: Math.max(0, f.z ?? 0), vx: 0, vy: 0, vz: 0,
        roll: f.roll, pitch: f.pitch, yaw: f.yaw,
        throttle: f.armed ? Math.min(1, Math.max(0, (f.throttle ?? 50) / 100)) : 0,
        armed: f.armed, at: now,
      })
    }
    return out
  }
  const f = r.frame
  if (!r.vehicleId || !f) return out
  const has = f.x !== null && f.y !== null && f.z !== null
  const [sx, sy] = has ? [f.x as number, f.y as number] : slotPosition(live.row, 0, 1)
  out.set(r.vehicleId, {
    id: r.vehicleId,
    kind: has ? 'position' : 'slot',
    sim: r.kind === 'sim',
    x: sx,
    y: sy,
    z: has ? Math.max(0, f.z as number) : Math.max(0, f.alt ?? 0),
    vx: 0,
    vy: 0,
    vz: 0,
    roll: f.roll,
    pitch: f.pitch,
    yaw: f.yaw,
    throttle: f.armed ? Math.min(1, Math.max(0, (f.throttle ?? 50) / 100)) : 0,
    armed: f.armed,
    at: now,
  })
  return out
}

function rebuild(): void {
  const fleet: Fleet | null = useRelay.getState().fleet
  const vehicles: readonly Vehicle[] = fleet?.vehicles ?? []
  if (fleet) live.safety = fleet.safety_volume
  const now = performance.now()
  const replay = useReplay.getState()
  live.replaying = replay.active

  const ids = new Set(vehicles.map((v) => v.id))
  for (const id of live.spawns.keys()) if (!ids.has(id)) live.spawns.delete(id)
  for (const id of levelErrors.keys()) if (!ids.has(id)) levelErrors.delete(id)
  for (const v of vehicles) {
    if (v.kind !== 'sim') continue
    if (!live.spawns.has(v.id)) {
      const fb = fallbackSpawn(v.id)
      if (fb) live.spawns.set(v.id, fb)
    }
    spawnDetail(v.id)
  }
  for (const id of fetched) if (!ids.has(id)) fetched.delete(id)
  live.row = slotRow([...live.spawns.values()], live.safety)
  const offsets = frameOffsets([...live.spawns.values()], live.safety, framesUsed(vehicles))
  if (offsets.local.join() !== live.offsets.local.join() || offsets.gps.join() !== live.offsets.gps.join()) {
    live.offsets = offsets
    useFrameOffsets.setState(offsets, true)
  }

  let next: Map<string, Target>
  if (replay.active) {
    next = replayTargets(replay, now)
  } else {
    const placed = place(vehicles, live.row, live.offsets)
    next = new Map<string, Target>()
    for (let i = 0; i < vehicles.length; i++) {
      const v = vehicles[i]
      const p = placed[i]
      const att = v.attitude
      const [errRoll, errPitch] = levelError(v, now)
      next.set(v.id, {
        id: v.id,
        kind: p.kind,
        sim: v.kind === 'sim',
        x: p.x,
        y: p.y,
        z: p.z,
        vx: p.vx,
        vy: p.vy,
        vz: p.vz,
        roll: att ? att.roll + errRoll : null,
        pitch: att ? att.pitch + errPitch : null,
        yaw: att ? att.yaw : typeof v.heading_deg === 'number' ? (v.heading_deg * Math.PI) / 180 : null,
        throttle: throttleOf(v),
        armed: v.armed,
        at: now,
      })
    }
  }
  live.targets = next
  for (const id of live.state.keys()) if (!next.has(id)) live.state.delete(id)
  live.version++
}

let refs = 0
let unsub: (() => void) | null = null

/** Starts the snapshot subscription; returns a release function (reference counted). */
export function startLive(): () => void {
  refs++
  if (!unsub) {
    let last: Fleet | null = null
    const a = useRelay.subscribe((s) => {
      if (s.fleet === last) return
      last = s.fleet
      if (!useReplay.getState().active) rebuild()
    })
    let lastReplay: ReplayState = useReplay.getState()
    const b = useReplay.subscribe((r) => {
      if (r.frame === lastReplay.frame && r.active === lastReplay.active && r.vehicleId === lastReplay.vehicleId && r.crafts === lastReplay.crafts) return
      lastReplay = r
      rebuild()
    })
    unsub = () => {
      a()
      b()
    }
    rebuild()
  }
  return () => {
    refs--
    if (refs === 0 && unsub) {
      unsub()
      unsub = null
    }
  }
}

/** Safety-volume centre for the aircraft the stage focuses on: its sim spawn, else the shared origin. */
/** Display shift (ENU east, north) of an aircraft's own frame: zero for simulators and during a replay. */
export function frameShift(id: string | null): [number, number] {
  const t = id ? live.targets.get(id) : undefined
  if (!t || t.sim || live.replaying) return [0, 0]
  return t.kind === 'position' ? live.offsets.local : t.kind === 'gps' ? live.offsets.gps : [0, 0]
}

export function safetyOriginFor(id: string | null): [number, number, number] {
  if (id) {
    const o = live.safetyOrigins.get(id)
    if (o) {
      const [dx, dy] = frameShift(id)
      return [o[0] + dx, o[1] + dy, o[2]]
    }
    const s = live.spawns.get(id)
    if (s) return [s[0], s[1], 0]
  }
  return [0, 0, 0]
}

/** Whether any aircraft is placed by a position estimate (02 §6.1 safety volume rule). */
export function anyPositioned(): boolean {
  for (const t of live.targets.values()) if (t.kind !== 'slot') return true
  return false
}
