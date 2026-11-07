// Ring course data helpers (03 §8, 02 §10). Pure: the snapshot's course view in, ring placements and states out.
import type { Vehicle } from '@/api'

/** One ring in ENU metres: centre, compass heading of the normal (0 = north, 90 = east), inner radius. */
export interface Ring {
  c: [number, number, number]
  yaw_deg: number
  r: number
}

/** The snapshot's course view; `rings` are already placed in the vehicle's position frame. */
export type CourseView = NonNullable<Vehicle['course']>

export interface CourseDef {
  id: string
  name_en: string
  name_zh: string
  rings: Ring[]
  preset?: boolean
}

export type RingState = 'next' | 'later' | 'passed'

/** Places course-relative rings at an origin [x, y, z, heading_deg]; mirrors relay/course.py transform(). */
export function transformRings(rings: readonly Ring[], origin: readonly number[]): Ring[] {
  const [ox, oy, oz, heading] = [origin[0] ?? 0, origin[1] ?? 0, origin[2] ?? 0, origin[3] ?? 0]
  const h = (heading * Math.PI) / 180
  const ch = Math.cos(h)
  const sh = Math.sin(h)
  return rings.map((ring) => {
    const [x, y, z] = ring.c
    return {
      c: [ox + x * ch + y * sh, oy - x * sh + y * ch, oz + z],
      yaw_deg: (((ring.yaw_deg + heading) % 360) + 360) % 360,
      r: ring.r,
    }
  })
}

/** Rings of a run: the backend's placed rings, or the course definition placed at the run's origin. */
export function runRings(view: CourseView, defs: readonly CourseDef[]): Ring[] {
  if (view.rings && view.rings.length) return view.rings
  const def = defs.find((d) => d.id === view.course_id)
  return def ? transformRings(def.rings, view.origin) : []
}

/** Unit normal of a ring in ENU. */
export function ringNormal(yawDeg: number): [number, number, number] {
  const y = (yawDeg * Math.PI) / 180
  return [Math.sin(y), Math.cos(y), 0]
}

/** State of ring i (0-based) during a run. */
export function ringState(i: number, view: Pick<CourseView, 'next' | 'state' | 'total'>): RingState {
  if (view.state === 'finished') return 'passed'
  if (i < view.next) return 'passed'
  if (i === view.next) return view.state === 'aborted' ? 'later' : 'next'
  return 'later'
}

export const isActive = (view: Pick<CourseView, 'state'> | null | undefined) =>
  !!view && (view.state === 'waiting_takeoff' || view.state === 'running')

/** Elapsed seconds of a run now: the snapshot value, advanced on the backend clock while running. */
export function liveElapsed(view: Pick<CourseView, 'state' | 'elapsed_s'>, snapshotT: number, nowT: number): number {
  if (view.state !== 'running') return view.elapsed_s
  return view.elapsed_s + Math.max(0, nowT - snapshotT)
}

/** i18n key of a preset name with ring count ("Cage loop · 4 rings"), or null for custom courses. */
export function presetKey(id: string): 'course.cageLoop' | 'course.slalom' | 'course.climb' | null {
  if (id === 'cage-loop') return 'course.cageLoop'
  if (id === 'slalom') return 'course.slalom'
  if (id === 'climb') return 'course.climb'
  return null
}

/** Parses GET /courses ({courses: [...]}, or a bare list) into definitions; malformed entries are dropped. */
export function parseCourses(body: unknown): CourseDef[] {
  const list = Array.isArray(body) ? body : body && typeof body === 'object' ? (body as { courses?: unknown }).courses : null
  if (!Array.isArray(list)) return []
  const out: CourseDef[] = []
  for (const c of list) {
    if (!c || typeof c !== 'object') continue
    const o = c as Record<string, unknown>
    if (typeof o.id !== 'string' || !Array.isArray(o.rings)) continue
    const rings = (o.rings as unknown[]).filter(isRing)
    out.push({
      id: o.id,
      name_en: typeof o.name_en === 'string' ? o.name_en : o.id,
      name_zh: typeof o.name_zh === 'string' ? o.name_zh : typeof o.name_en === 'string' ? o.name_en : o.id,
      rings,
      preset: o.preset === true,
    })
  }
  return out
}

function isRing(r: unknown): r is Ring {
  if (!r || typeof r !== 'object') return false
  const o = r as Record<string, unknown>
  return Array.isArray(o.c) && o.c.length === 3 && o.c.every((n) => typeof n === 'number') && typeof o.yaw_deg === 'number' && typeof o.r === 'number'
}

/** Bounds of rings in ENU (for camera framing). */
export function ringsExtent(rings: readonly Ring[]): { minX: number; maxX: number; minY: number; maxY: number; maxZ: number } | null {
  if (!rings.length) return null
  let minX = Infinity
  let maxX = -Infinity
  let minY = Infinity
  let maxY = -Infinity
  let maxZ = 0
  for (const r of rings) {
    minX = Math.min(minX, r.c[0] - r.r)
    maxX = Math.max(maxX, r.c[0] + r.r)
    minY = Math.min(minY, r.c[1] - r.r)
    maxY = Math.max(maxY, r.c[1] + r.r)
    maxZ = Math.max(maxZ, r.c[2] + r.r)
  }
  return { minX, maxX, minY, maxY, maxZ }
}
