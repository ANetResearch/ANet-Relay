// Live events -> voxel effects (07 §5), pure: no three.js, no React (effects.test.ts). The arena layer resolves
// aircraft positions and feeds the result to its effect queue (./fx).
//
//   arena_down {side, target, point}             explosion at the aircraft that went down (target), else at point
//   collision {a, b, point, crashed: [ids], kind} sparks at the contact point; an explosion for every crashed id
//   crashed {reason_code, point, speed?}          'ground' (or none, older backends): dust ring and grey chunks;
//                                                 a ground crash at >= HARD_CRASH_V explodes; 'collision' explodes
//                                                 (the collision event usually came first and wins the dedupe);
//                                                 'shot_down' raises dust where the falling wreck lands
//   ring_touch {vehicle, ring, point}             the ring flashes white (course layer)
//
// Every explosion carries the key of the aircraft it belongs to (a local id such as "sim-2" or a remote key
// "<peer>/sim-N"), so the queue can drop the second report of one destruction: a ram arrives as a collision and a
// crashed event, a remote shoot-down as arena_down and as the arena view's down flag.
import type { RelayEvent } from '@/api'

export type Vec3 = [number, number, number]

/** A ground crash at or above this speed (m/s, when the backend reports one) explodes instead of raising dust. */
export const HARD_CRASH_V = 6

export type FxKind = 'explosion' | 'spark' | 'dust'

export interface FxRequest {
  kind: FxKind
  /** local ENU metres */
  p: Vec3
  /** the aircraft the effect belongs to (dedupe key), null for a contact point */
  key: string | null
  /** stable per effect: the same event always looks the same */
  seed: string
}

export interface RingTouch {
  vehicle: string
  ring: number
}

export interface EventEffects {
  fx: FxRequest[]
  rings: RingTouch[]
}

/** Where an aircraft is now (local ENU): a local id or a remote key; null when unknown. */
export type Locate = (key: string) => Vec3 | null

export const vec3 = (x: unknown): Vec3 | null =>
  Array.isArray(x) && x.length === 3 && x.every((n) => typeof n === 'number' && Number.isFinite(n)) ? [x[0], x[1], x[2]] : null

const str = (x: unknown): string | null => (typeof x === 'string' && x ? x : null)

/** Event kinds are compared as strings: collision and ring_touch are v0.5 kinds the generated union may lack. */
const kindOf = (e: Pick<RelayEvent, 'kind'>): string => e.kind as string

/** The effects of a batch of events, in order. */
export function effectsOf(events: readonly RelayEvent[], locate: Locate): EventEffects {
  const out: EventEffects = { fx: [], rings: [] }
  for (const e of events) {
    const d = e.data ?? {}
    const point = vec3(d.point)
    switch (kindOf(e)) {
      case 'arena_down': {
        const key = str(d.target) ?? e.vehicle
        const p = (key ? locate(key) : null) ?? point
        if (p) out.fx.push({ kind: 'explosion', p, key, seed: `${e.seq}:down` })
        break
      }
      case 'collision': {
        if (point) out.fx.push({ kind: 'spark', p: point, key: null, seed: `${e.seq}:spark` })
        const crashed = Array.isArray(d.crashed) ? d.crashed.filter((x): x is string => typeof x === 'string' && x.length > 0) : []
        for (const id of crashed) {
          const p = locate(id) ?? point
          if (p) out.fx.push({ kind: 'explosion', p, key: id, seed: `${e.seq}:${id}` })
        }
        break
      }
      case 'crashed': {
        const id = e.vehicle
        const p = point ?? (id ? locate(id) : null)
        if (!p) break
        const reason = str(d.reason_code) ?? 'ground'
        const speed = typeof d.speed === 'number' ? d.speed : null
        const hard = reason === 'collision' || (reason === 'ground' && speed !== null && speed >= HARD_CRASH_V)
        // the wreck sits on the ground: dust and explosions of a crash start there
        const ground: Vec3 = [p[0], p[1], reason === 'collision' ? p[2] : Math.max(0, Math.min(p[2], 0.05))]
        out.fx.push({ kind: hard ? 'explosion' : 'dust', p: ground, key: hard ? id : null, seed: `${e.seq}:crash` })
        break
      }
      case 'ring_touch': {
        const vehicle = str(d.vehicle) ?? e.vehicle
        if (vehicle && typeof d.ring === 'number' && Number.isInteger(d.ring) && d.ring >= 0) out.rings.push({ vehicle, ring: d.ring })
        break
      }
      default:
        break
    }
  }
  return out
}

/**
 * Drops the second report of one aircraft's destruction: an explosion for a key that exploded less than `windowS`
 * ago is refused. Contact effects (no key) always pass.
 */
export class FxDedupe {
  private readonly seen = new Map<string, number>()

  allow(key: string | null, now: number, windowS: number): boolean {
    if (key === null) return true
    for (const [k, t] of this.seen) if (now - t > windowS) this.seen.delete(k)
    const last = this.seen.get(key)
    if (last !== undefined && now - last <= windowS) return false
    this.seen.set(key, now)
    return true
  }

  clear(): void {
    this.seen.clear()
  }
}
