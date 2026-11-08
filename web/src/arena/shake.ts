// Chase camera shake (07 §5): an explosion or a collision within SHAKE_RANGE of the camera jolts it once, a decaying
// jitter of at most SHAKE_MAX metres over --duration-arena-shake, weaker with distance. Off under reduced motion.
// shakeOffset is pure (shake.test via effects.test.ts); the queue below is fed by the arena effects and read by the
// camera rig in the chase view only.
import { motionReduced } from '@/ui/motion/tier'
import { ARENA_TOKENS, tokenMs } from './timing'

export const SHAKE_RANGE = 5
export const SHAKE_MAX = 0.03
const MAX_SOURCES = 8

export interface ShakeSource {
  /** scene metres */
  p: readonly [number, number, number]
  /** seconds, the caller's clock */
  t0: number
  /** 1 for an explosion, less for a contact */
  strength: number
  /** phase seed, 0..1 */
  phase: number
}

/**
 * Offset of a camera at `cam` (scene metres) at `now`: the sum of every live source, each `SHAKE_MAX * strength`
 * scaled down linearly with distance to zero at SHAKE_RANGE and quadratically with age to zero at `spanS`, clamped
 * to SHAKE_MAX in length. Deterministic in its inputs.
 */
export function shakeOffset(sources: readonly ShakeSource[], now: number, cam: readonly [number, number, number], spanS: number, out: [number, number, number] = [0, 0, 0]): [number, number, number] {
  out[0] = 0
  out[1] = 0
  out[2] = 0
  if (!(spanS > 0)) return out
  for (const s of sources) {
    const age = now - s.t0
    if (age < 0 || age > spanS) continue
    const d = Math.hypot(cam[0] - s.p[0], cam[1] - s.p[1], cam[2] - s.p[2])
    if (d >= SHAKE_RANGE) continue
    const k = 1 - age / spanS
    const amp = SHAKE_MAX * Math.min(1, Math.max(0, s.strength)) * (1 - d / SHAKE_RANGE) * k * k
    const w = 2 * Math.PI * age
    const ph = s.phase * 2 * Math.PI
    out[0] += amp * Math.sin(w * 31 + ph)
    out[1] += amp * 0.7 * Math.sin(w * 23 + ph * 1.7)
    out[2] += amp * Math.sin(w * 37 + ph * 2.3)
  }
  const len = Math.hypot(out[0], out[1], out[2])
  if (len > SHAKE_MAX) {
    const f = SHAKE_MAX / len
    out[0] *= f
    out[1] *= f
    out[2] *= f
  }
  return out
}

const sources: ShakeSource[] = []
const tmp: [number, number, number] = [0, 0, 0]

const shakeNow = () => performance.now() / 1000

/** Queues a shake at a scene position (no-op under reduced motion). */
export function addShake(p: readonly [number, number, number], strength: number, phase: number): void {
  if (motionReduced()) return
  if (sources.length >= MAX_SOURCES) sources.shift()
  sources.push({ p: [p[0], p[1], p[2]], t0: shakeNow(), strength, phase })
}

/** The offset to add to the chase camera at `cam` this frame (scene metres; zeros when nothing shakes). */
export function cameraShake(cam: { x: number; y: number; z: number }): readonly [number, number, number] {
  tmp[0] = 0
  tmp[1] = 0
  tmp[2] = 0
  if (!sources.length || motionReduced()) return tmp
  const span = tokenMs(ARENA_TOKENS.shake) / 1000
  const now = shakeNow()
  while (sources.length && now - sources[0].t0 > span) sources.shift()
  return shakeOffset(sources, now, [cam.x, cam.y, cam.z], span, tmp)
}

/** Tests only. */
export function clearShakes(): void {
  sources.length = 0
}
