// Pure maths shared by the stage and the course layer: frame conversion, smoothing and easing. No three.js, no
// React, so it is unit-tested in node.
//
// Frames: the backend reports ENU metres (x east, y north, z up) and attitude in radians (roll right-positive,
// pitch nose-up-positive, yaw clockwise from north). The scene is three.js Y-up with north = -Z and east = +X, so
// ENU (x, y, z) maps to scene (x, z, -y). An airframe's nose is local -Z and its right side local +X.

export type Vec3 = [number, number, number]

/** ENU metres to scene coordinates. */
export function enuToScene(x: number, y: number, z: number): Vec3 {
  return [x, z, -y]
}

/** Scene coordinates back to ENU metres. */
export function sceneToEnu(sx: number, sy: number, sz: number): Vec3 {
  return [sx, -sz, sy]
}

/**
 * Euler angles for an airframe group (rotation order YXZ): yaw about scene +Y is negated because compass yaw is
 * clockwise seen from above, roll is negated because a positive roll lowers the right (+X) side.
 */
export function attitudeEuler(roll: number, pitch: number, yaw: number): Vec3 {
  return [pitch, -yaw, -roll]
}

/** Weight for exponential smoothing with time constant tau over a frame of dt seconds. */
export function expAlpha(dt: number, tau: number): number {
  if (tau <= 0) return 1
  return 1 - Math.exp(-Math.max(0, dt) / tau)
}

/** Shortest signed difference b - a between two angles in radians, in (-pi, pi]. */
export function angleDelta(a: number, b: number): number {
  let d = (b - a) % (2 * Math.PI)
  if (d > Math.PI) d -= 2 * Math.PI
  if (d <= -Math.PI) d += 2 * Math.PI
  return d
}

/** Exponentially smoothed angle that takes the short way round. */
export function smoothAngle(current: number, target: number, alpha: number): number {
  return current + angleDelta(current, target) * alpha
}

/** cubic-bezier(x1, y1, x2, y2) as a function of progress 0..1 (Newton iterations with bisection fallback). */
export function cubicBezier(x1: number, y1: number, x2: number, y2: number): (t: number) => number {
  const cx = 3 * x1
  const bx = 3 * (x2 - x1) - cx
  const ax = 1 - cx - bx
  const cy = 3 * y1
  const by = 3 * (y2 - y1) - cy
  const ay = 1 - cy - by
  const sx = (t: number) => ((ax * t + bx) * t + cx) * t
  const sy = (t: number) => ((ay * t + by) * t + cy) * t
  const dx = (t: number) => (3 * ax * t + 2 * bx) * t + cx
  return (x: number) => {
    if (x <= 0) return 0
    if (x >= 1) return 1
    let t = x
    for (let i = 0; i < 8; i++) {
      const e = sx(t) - x
      if (Math.abs(e) < 1e-6) return sy(t)
      const d = dx(t)
      if (Math.abs(d) < 1e-6) break
      t -= e / d
    }
    let lo = 0
    let hi = 1
    t = x
    for (let i = 0; i < 30; i++) {
      const v = sx(t)
      if (Math.abs(v - x) < 1e-6) break
      if (v < x) lo = t
      else hi = t
      t = (lo + hi) / 2
    }
    return sy(t)
  }
}

/** --ease-smooth-out of the motion tokens: cubic-bezier(0.22, 1, 0.36, 1). */
export const easeSmoothOut = cubicBezier(0.22, 1, 0.36, 1)

export const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))
export const lerp = (a: number, b: number, t: number) => a + (b - a) * t
