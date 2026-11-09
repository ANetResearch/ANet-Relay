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

// ---------------------------------------------------------------- attitude quaternions (11 §8.5)
// The stage smooths attitude as a quaternion (slerp) rather than as Euler angles: inverted flight, a full roll and
// a tumbling wreck pass through the Euler singularities (pitch ±90°, roll ±180°) where the angles jump although the
// airframe barely moves. Quaternions are [x, y, z, w] in the scene frame, the order three.js uses.

export type Quat = [number, number, number, number]

const axisQuat = (ax: 0 | 1 | 2, angle: number): Quat => {
  const s = Math.sin(angle / 2)
  const q: Quat = [0, 0, 0, Math.cos(angle / 2)]
  q[ax] = s
  return q
}

/** Hamilton product a * b (apply b, then a). */
export function quatMul(a: Quat, b: Quat): Quat {
  const [ax, ay, az, aw] = a
  const [bx, by, bz, bw] = b
  return [aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz]
}

export function quatNormalize(q: Quat): Quat {
  const n = Math.hypot(q[0], q[1], q[2], q[3]) || 1
  return [q[0] / n, q[1] / n, q[2] / n, q[3] / n]
}

export const quatConj = (q: Quat): Quat => [-q[0], -q[1], -q[2], q[3]]

/** The scene orientation of an attitude: the same rotation as attitudeEuler in order YXZ (yaw, then pitch, then roll). */
export function attitudeQuat(roll: number, pitch: number, yaw: number): Quat {
  return quatMul(quatMul(axisQuat(1, -yaw), axisQuat(0, pitch)), axisQuat(2, -roll))
}

/** Rotation about scene +Y that turns the nose to a compass yaw (the yaw group of an aircraft). */
export const yawQuat = (yaw: number): Quat => axisQuat(1, -yaw)

/** Spherical interpolation from a to b by t along the shorter arc (q and -q are the same orientation). */
export function quatSlerp(a: Quat, b: Quat, t: number): Quat {
  let [bx, by, bz, bw] = b
  let cos = a[0] * bx + a[1] * by + a[2] * bz + a[3] * bw
  if (cos < 0) {
    cos = -cos
    bx = -bx
    by = -by
    bz = -bz
    bw = -bw
  }
  if (cos > 0.9995) return quatNormalize([a[0] + (bx - a[0]) * t, a[1] + (by - a[1]) * t, a[2] + (bz - a[2]) * t, a[3] + (bw - a[3]) * t])
  const theta = Math.acos(Math.min(1, cos))
  const sin = Math.sin(theta)
  const wa = Math.sin((1 - t) * theta) / sin
  const wb = Math.sin(t * theta) / sin
  return [a[0] * wa + bx * wb, a[1] * wa + by * wb, a[2] * wa + bz * wb, a[3] * wa + bw * wb]
}

/** Angle in radians between two orientations (0..pi). */
export function quatAngle(a: Quat, b: Quat): number {
  const d = Math.abs(a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3])
  return 2 * Math.acos(Math.min(1, d))
}

/** v rotated by q. */
export function quatRotate(q: Quat, v: Vec3): Vec3 {
  const r = quatMul(quatMul(q, [v[0], v[1], v[2], 0]), quatConj(q))
  return [r[0], r[1], r[2]]
}

/**
 * Compass heading of an orientation: the nose (local -Z) projected on the ground; when the nose points almost
 * straight up or down, the belly or back direction instead. Continuous through inverted flight and loops.
 */
export function quatHeading(q: Quat, fallback = 0): number {
  let [x, , z] = quatRotate(q, [0, 0, -1])
  if (Math.hypot(x, z) < 0.2) {
    const up = quatRotate(q, [0, 1, 0])
    const nose = quatRotate(q, [0, 0, -1])
    // nose up: the belly faces the way the aircraft is heading; nose down: the back does
    const s = nose[1] > 0 ? -1 : 1
    x = s * up[0]
    z = s * up[2]
    if (Math.hypot(x, z) < 1e-6) return fallback
  }
  // scene north is -Z, east +X; compass yaw is clockwise from north
  return Math.atan2(x, -z)
}

/**
 * One frame of the stage's attitude smoothing: slerp towards the target with weight alpha. Returns the smoothed
 * orientation, its heading (for the yaw group, the chase camera and the ground marker) and the attitude group's
 * quaternion relative to that heading, so yaw group x attitude group = the smoothed orientation exactly.
 */
export function smoothAttitude(current: Quat, target: Quat, alpha: number, prevHeading: number): { q: Quat; heading: number; local: Quat } {
  const q = quatNormalize(quatSlerp(current, target, alpha))
  const heading = quatHeading(q, prevHeading)
  return { q, heading, local: quatMul(quatConj(yawQuat(heading)), q) }
}
