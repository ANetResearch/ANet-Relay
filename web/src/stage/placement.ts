// Placement rules of 02 §6.3. Pure: snapshot in, ENU placements out.
//   - aircraft with a shared frame (simulators, real aircraft reporting a local position) sit at their position;
//   - real aircraft without a local position but with a 3D GPS fix are projected onto a plane whose origin is the
//     first such aircraft in fleet order;
//   - everything else goes to a pad slot: one row south of the scene, 0.6 m apart, west to east in fleet order.
// The simulator grid, the real aircraft's local frame and the GPS plane are three unrelated frames. When more than
// one is in use, each later frame is drawn north of the previous one (frameOffsets) so a real aircraft whose local
// origin coincides with a simulator spawn point never sits inside that simulator.
import type { Vehicle } from '@/api'

export const SLOT_SPACING = 0.6
/** gap between the southern edge of the flying area and the pad row */
export const SLOT_MARGIN = 0.6

/**
 * Simulator spawn points of the backend (fleet.py SIM_SPAWNS, 2 m grid, 4 x 2), used when the vehicle detail call
 * has not answered yet or is unavailable (dev fixture).
 */
export const SIM_SPAWNS_FALLBACK: readonly [number, number][] = [
  [0, 0], [2, 0], [-2, 0], [4, 0], [0, -2], [2, -2], [-2, -2], [4, -2],
]

export type PlaceKind = 'position' | 'gps' | 'slot'

export interface Placement {
  id: string
  kind: PlaceKind
  /** ENU metres */
  x: number
  y: number
  z: number
  /** ENU m/s, zero for slots */
  vx: number
  vy: number
  vz: number
  /** index in the pad row for slots */
  slot: number | null
}

export interface SlotRow {
  /** ENU north coordinate of the row */
  y: number
  /** ENU east coordinate of the row centre */
  cx: number
}

/** Spawn of a simulator by id (sim-3 -> third grid point) when the backend detail is not known. */
export function fallbackSpawn(id: string): [number, number] | null {
  const m = /^sim-(\d+)$/.exec(id)
  if (!m) return null
  const n = Number(m[1])
  return SIM_SPAWNS_FALLBACK[n - 1] ?? null
}

/**
 * The pad row depends only on static things (spawn points and the safety volume), so slots never wander while
 * aircraft fly.
 */
export function slotRow(spawns: readonly [number, number][], safety: { x: number; y: number }): SlotRow {
  const ys = spawns.map((s) => s[1])
  const xs = spawns.map((s) => s[0])
  const south = Math.min(0, ...ys) - safety.y / 2 - SLOT_MARGIN
  const cx = xs.length ? (Math.min(...xs) + Math.max(...xs)) / 2 : 0
  return { y: south, cx }
}

/** Position of slot i of n in the row. */
export function slotPosition(row: SlotRow, i: number, n: number): [number, number] {
  return [row.cx + (i - (n - 1) / 2) * SLOT_SPACING, row.y]
}

/** gap between two frames drawn side by side (02 §6.3) */
export const FRAME_GAP = 1.2

/** Display offsets (ENU east, north) of the three frames; the simulator grid is never moved. */
export interface FrameOffsets {
  local: [number, number]
  gps: [number, number]
}

export const NO_OFFSETS: FrameOffsets = { local: [0, 0], gps: [0, 0] }

/**
 * Real local frames are drawn north of the simulator grid when simulators exist; the GPS plane north of whatever
 * is in use. Each frame gets one safety-volume depth plus a gap, so neighbouring safety volumes never overlap.
 */
export function frameOffsets(
  spawns: readonly [number, number][],
  safety: { x: number; y: number },
  used: { sim: boolean; local: boolean; gps: boolean },
): FrameOffsets {
  const xs = spawns.map((s) => s[0])
  const cx = used.sim && xs.length ? (Math.min(...xs) + Math.max(...xs)) / 2 : 0
  const step = safety.y + FRAME_GAP
  let north = used.sim && spawns.length ? Math.max(0, ...spawns.map((s) => s[1])) + step : 0
  const local: [number, number] = used.sim ? [cx, north] : [0, 0]
  if (used.local && used.sim) north += step
  else if (used.local) north = step
  const gps: [number, number] = used.sim || used.local ? [cx, north] : [0, 0]
  return { local, gps }
}

/** Which frames the fleet uses (by the same rules as place()). */
export function framesUsed(vehicles: readonly PlaceVehicle[]): { sim: boolean; local: boolean; gps: boolean } {
  const used = { sim: false, local: false, gps: false }
  for (const v of vehicles) {
    if (v.position) {
      if (v.kind === 'sim') used.sim = true
      else used.local = true
    } else if (v.kind === 'real' && v.global && v.gps && v.gps.fix >= 3) used.gps = true
  }
  return used
}

const EARTH_LAT_M = 110540
const EARTH_LON_M = 111320

type PlaceVehicle = Pick<Vehicle, 'id' | 'kind' | 'position' | 'global' | 'gps' | 'altitude_m'>

/** Applies 02 §6.3 to the vehicles in fleet order. */
export function place(vehicles: readonly PlaceVehicle[], row: SlotRow, offsets: FrameOffsets = NO_OFFSETS): Placement[] {
  const out: Placement[] = []
  const slotIds: string[] = []
  let gpsOrigin: { lat: number; lon: number } | null = null
  for (const v of vehicles) {
    const p = v.position
    if (p) {
      const [dx, dy] = v.kind === 'sim' ? [0, 0] : offsets.local
      out.push({ id: v.id, kind: 'position', x: p.x + dx, y: p.y + dy, z: p.z, vx: p.vx, vy: p.vy, vz: p.vz, slot: null })
      continue
    }
    const g = v.global
    if (v.kind === 'real' && g && v.gps && v.gps.fix >= 3) {
      gpsOrigin ??= { lat: g.lat, lon: g.lon }
      const x = (g.lon - gpsOrigin.lon) * EARTH_LON_M * Math.cos((gpsOrigin.lat * Math.PI) / 180) + offsets.gps[0]
      const y = (g.lat - gpsOrigin.lat) * EARTH_LAT_M + offsets.gps[1]
      out.push({ id: v.id, kind: 'gps', x, y, z: Math.max(0, v.altitude_m ?? 0), vx: 0, vy: 0, vz: 0, slot: null })
      continue
    }
    slotIds.push(v.id)
    out.push({ id: v.id, kind: 'slot', x: 0, y: 0, z: 0, vx: 0, vy: 0, vz: 0, slot: slotIds.length - 1 })
  }
  const n = slotIds.length
  for (const pl of out) {
    if (pl.kind !== 'slot' || pl.slot === null) continue
    const [x, y] = slotPosition(row, pl.slot, n)
    pl.x = x
    pl.y = y
  }
  return out
}

export interface Bounds {
  minX: number
  maxX: number
  minY: number
  maxY: number
  maxZ: number
  /** number of points that went in */
  count: number
}

export function emptyBounds(): Bounds {
  return { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity, maxZ: 0, count: 0 }
}

export function addPoint(b: Bounds, x: number, y: number, z = 0): Bounds {
  b.minX = Math.min(b.minX, x)
  b.maxX = Math.max(b.maxX, x)
  b.minY = Math.min(b.minY, y)
  b.maxY = Math.max(b.maxY, y)
  b.maxZ = Math.max(b.maxZ, z)
  b.count++
  return b
}

/**
 * Bounds grown to a minimum footprint around their centre. 02 §6.5 asks for at least 4 x 4 m; with a single
 * aircraft that would leave it at 2 % of the frame width while the same row asks for about a tenth, so one
 * aircraft (and nothing else to frame) uses a 0.8 m footprint instead.
 */
export function framed(b: Bounds, aircraft: number): Bounds {
  const min = aircraft === 1 && b.count <= 1 ? 0.8 : 4
  if (b.count === 0) return { minX: -min / 2, maxX: min / 2, minY: -min / 2, maxY: min / 2, maxZ: 0, count: 0 }
  const cx = (b.minX + b.maxX) / 2
  const cy = (b.minY + b.maxY) / 2
  const w = Math.max(min, b.maxX - b.minX)
  const d = Math.max(min, b.maxY - b.minY)
  return { minX: cx - w / 2, maxX: cx + w / 2, minY: cy - d / 2, maxY: cy + d / 2, maxZ: b.maxZ, count: b.count }
}
