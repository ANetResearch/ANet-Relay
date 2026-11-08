// Imperative effect queue of the live stage (06 §11, 07 §5), shared by the remote fleet (shoot-downs from the arena
// view), the hit marks and the event effects (./effects). Bursts are drawn by ArenaLayer's EffectsView. Under reduced
// motion every burst becomes a still white cube for the life of its kind, the shell flash is refused and the camera
// does not shake. An explosion is refused for an aircraft that exploded less than one explosion life ago, so one
// destruction reported twice (a ram as collision and crashed, a remote shoot-down as arena_down and the view's down
// flag) explodes once.
import * as THREE from 'three'
import { live } from '@/stage/live'
import { enuToScene, sceneToEnu } from '@/stage/math'
import { motionReduced } from '@/ui/motion/tier'
import { FxDedupe, type EventEffects, type Vec3 } from './effects'
import { addShake } from './shake'
import type { ArenaMark } from './store'
import { ARENA_TOKENS, tokenMs } from './timing'
import { seedOf, type Burst, type BurstKind } from './voxelFx'

const MAX_BURSTS = 64
const MAX_FLASHES = 8
/** used when the explosion token cannot be read (tests, first frame) */
const EXPLOSION_FALLBACK_S = 1.2

export interface FlashState {
  vehicle: string
  born: number
  life: number
}

type Pos = THREE.Vector3 | readonly [number, number, number]

/** Seconds on the clock the live bursts use. */
export const fxNow = () => performance.now() / 1000

const xyz = (p: Pos): [number, number, number] => (Array.isArray(p) ? [p[0], p[1], p[2]] : [(p as THREE.Vector3).x, (p as THREE.Vector3).y, (p as THREE.Vector3).z])

/** Life of an explosion in seconds (the dedupe window). */
export function explosionLifeS(): number {
  const ms = tokenMs(ARENA_TOKENS.explosion)
  return ms > 0 ? ms / 1000 : EXPLOSION_FALLBACK_S
}

export const fx = {
  bursts: [] as Burst[],
  flashes: [] as FlashState[],
  dedupe: new FxDedupe(),
  /** A burst at a scene position. Explosions and sparks also shake a chase camera close by. */
  burst(at: Pos, kind: BurstKind, seed: number): void {
    if (this.bursts.length >= MAX_BURSTS) this.bursts.shift()
    const p = xyz(at)
    const still = motionReduced()
    this.bursts.push(still ? { p, t0: fxNow(), seed, kind, still: true } : { p, t0: fxNow(), seed, kind })
    if (kind === 'explosion') addShake(p, 1, (seed % 997) / 997)
    else if (kind === 'spark') addShake(p, 0.5, (seed % 997) / 997)
  },
  /** An explosion for aircraft `key` (null: no dedupe); false when that aircraft just exploded. */
  explode(key: string | null, at: Pos, seed: number): boolean {
    if (!this.dedupe.allow(key, fxNow(), explosionLifeS())) return false
    this.burst(at, 'explosion', seed)
    return true
  },
  flash(vehicle: string, lifeMs: number): void {
    if (lifeMs <= 0 || motionReduced()) return
    if (this.flashes.length >= MAX_FLASHES) this.flashes.shift()
    this.flashes.push({ vehicle, born: performance.now(), life: lifeMs })
  },
  /** Tests and fixture resets. */
  clear(): void {
    this.bursts = []
    this.flashes = []
    this.dedupe.clear()
  },
}

export type FxApi = Pick<typeof fx, 'burst' | 'flash' | 'explode'>

function scene(p: readonly number[]): THREE.Vector3 {
  const [x, y, z] = enuToScene(p[0], p[1], p[2])
  return new THREE.Vector3(x, y, z)
}

/** Effects for a batch of hit marks: a burst at every hit point, a flash on our aircraft, an explosion on a down. */
export function applyMarks(marks: readonly ArenaMark[], api: FxApi, at = (vid: string | null) => (vid ? live.state.get(vid)?.pos ?? null : null)): void {
  for (const m of marks) {
    const where = m.point ? scene(m.point) : at(m.vehicle)?.clone() ?? null
    if (m.kind === 'taken' && m.vehicle) api.flash(m.vehicle, tokenMs(ARENA_TOKENS.flash))
    if (where) api.burst(where, 'hit', seedOf(`${m.seq}:${m.kind}`))
    // the shoot-down belongs to the aircraft that went down, so only a hit taken carries one here
    const body = m.kind === 'taken' && m.down ? at(m.vehicle) : null
    if (body) api.explode(m.vehicle, body.clone(), seedOf(`${m.seq}:down`))
  }
}

/** Puts the effects of a batch of events on the queue (ENU points converted to the scene). */
export function applyEventEffects(effects: EventEffects, api: FxApi, ringTouch: (vehicle: string, ring: number) => void): void {
  for (const r of effects.fx) {
    const p = scene(r.p)
    const seed = seedOf(r.seed)
    if (r.kind === 'explosion') api.explode(r.key, p, seed)
    else api.burst(p, r.kind, seed)
  }
  for (const t of effects.rings) ringTouch(t.vehicle, t.ring)
}

/** Local ENU position of a local aircraft from its smoothed stage state (null when it is not placed). */
export function localEnu(id: string): Vec3 | null {
  const st = live.state.get(id)
  if (!st || !st.ready || st.kind === 'slot') return null
  return sceneToEnu(st.pos.x, st.pos.y, st.pos.z)
}
