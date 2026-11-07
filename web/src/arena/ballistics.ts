// Bullet flight (05 §7.4). The same integrator as backend/relay/peers/ballistics.py, checked against the shared
// vectors in docs/design/vectors/ballistics.json (ballistics.test.ts), so every terminal draws the same trajectory
// from the same spawn. Fixed step 1/240 s, semi-implicit Euler, quadratic drag k = 0.015 /m, gravity 9.81, 2.5 s
// (600 steps) or until z <= 0. Pure: no three.js, no React.
import type { Vec3 } from './frame'

export const DT = 1 / 240
export const G = 9.81
export const K = 0.015
export const MUZZLE = 35
export const TTL_STEPS = 600
export const HIT_RADIUS = 0.12

/** One integrator step, in the backend's order of operations. */
export function step(p: readonly number[], v: readonly number[]): { p: Vec3; v: Vec3 } {
  const speed = Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
  const vx = v[0] + -K * speed * v[0] * DT
  const vy = v[1] + -K * speed * v[1] * DT
  const vz = v[2] + (-K * speed * v[2] - G) * DT
  return { p: [p[0] + vx * DT, p[1] + vy * DT, p[2] + vz * DT], v: [vx, vy, vz] }
}

export function advance(p: readonly number[], v: readonly number[], steps: number): { p: Vec3; v: Vec3 } {
  let s = { p: [p[0], p[1], p[2]] as Vec3, v: [v[0], v[1], v[2]] as Vec3 }
  for (let i = 0; i < steps; i++) s = step(s.p, s.v)
  return s
}

/** Initial bullet velocity: the nose direction (with pitch) at 35 m/s plus the aircraft's velocity. */
export function muzzle(heading: number, pitch: number, aircraftV: readonly number[]): Vec3 {
  const c = Math.cos(pitch)
  return [MUZZLE * Math.sin(heading) * c + aircraftV[0], MUZZLE * Math.cos(heading) * c + aircraftV[1], MUZZLE * Math.sin(pitch) + aircraftV[2]]
}

/** First time in [0, 1] at which p0 -> p1 comes within r of a centre moving c0 -> c1, or null. */
export function sweep(p0: readonly number[], p1: readonly number[], c0: readonly number[], c1: readonly number[], r: number): number | null {
  const a = [p0[0] - c0[0], p0[1] - c0[1], p0[2] - c0[2]]
  const b = [p1[0] - c1[0], p1[1] - c1[1], p1[2] - c1[2]]
  const d = [b[0] - a[0], b[1] - a[1], b[2] - a[2]]
  const dd = d[0] * d[0] + d[1] * d[1] + d[2] * d[2]
  const ad = a[0] * d[0] + a[1] * d[1] + a[2] * d[2]
  const aa = a[0] * a[0] + a[1] * a[1] + a[2] * a[2] - r * r
  if (aa <= 0) return 0
  if (dd <= 1e-12) return null
  const disc = ad * ad - dd * aa
  if (disc < 0) return null
  const t = (-ad - Math.sqrt(disc)) / dd
  return t >= 0 && t <= 1 ? t : null
}

/**
 * Flight state of a shot at backend time `now` (seconds, the Fleet.t clock): integrates from (p, v) at t0 by
 * whole steps, so every frame of every terminal lands on the same 1/240 s lattice. Null once it has expired.
 * `cache` lets a frame loop continue from the last result instead of integrating from t0 each frame.
 */
export interface Flight {
  steps: number
  p: Vec3
  v: Vec3
  done: boolean
}

export function flightAt(shot: { t0: number; p: readonly number[]; v: readonly number[] }, now: number, cache?: Flight | null): Flight {
  const want = Math.max(0, Math.min(TTL_STEPS, Math.floor((now - shot.t0) / DT)))
  let f: Flight = cache && cache.steps <= want ? cache : { steps: 0, p: [shot.p[0], shot.p[1], shot.p[2]], v: [shot.v[0], shot.v[1], shot.v[2]], done: false }
  while (!f.done && f.steps < want) {
    const s = step(f.p, f.v)
    f = { steps: f.steps + 1, p: s.p, v: s.v, done: s.p[2] <= 0 || f.steps + 1 >= TTL_STEPS }
  }
  if (want >= TTL_STEPS) f = { ...f, done: true }
  return f
}
