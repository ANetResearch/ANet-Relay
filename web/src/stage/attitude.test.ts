// 11 §8.5: the stage smooths attitude as a quaternion. Inverted flight, a full roll and a loop through pitch ±90°
// (where the reported Euler angles jump) must turn the drawn airframe continuously.
import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import { attitudeEuler, attitudeQuat, quatAngle, quatHeading, quatMul, quatRotate, quatSlerp, smoothAttitude, yawQuat, type Quat } from './math'

const close = (a: number[], b: number[], eps = 1e-9) => a.forEach((x, i) => expect(x).toBeCloseTo(b[i], -Math.log10(eps)))

/** The attitude a flight controller reports for a scene orientation: three.js Euler YXZ back to roll, pitch, yaw. */
function reported(q: Quat): { roll: number; pitch: number; yaw: number } {
  const e = new THREE.Euler().setFromQuaternion(new THREE.Quaternion(q[0], q[1], q[2], q[3]), 'YXZ')
  return { roll: -e.z, pitch: e.x, yaw: -e.y }
}

describe('attitude quaternions (11 §8.5)', () => {
  it('matches the Euler path of the airframe group', () => {
    for (const [r, p, y] of [[0.1, 0.2, 0.3], [-1.2, 0.7, 2.9], [3, -1.4, -2]]) {
      const [ex, ey, ez] = attitudeEuler(r, p, y)
      const t = new THREE.Quaternion().setFromEuler(new THREE.Euler(ex, ey, ez, 'YXZ'))
      const q = attitudeQuat(r, p, y)
      expect(quatAngle(q, [t.x, t.y, t.z, t.w])).toBeLessThan(1e-6)
    }
  })

  it('draws inverted flight upside down with the nose where it points', () => {
    const q = attitudeQuat(Math.PI, 0, Math.PI / 2) // inverted, heading east
    close(quatRotate(q, [0, 1, 0]), [0, -1, 0])
    close(quatRotate(q, [0, 0, -1]), [1, 0, 0])
    expect(quatHeading(q)).toBeCloseTo(Math.PI / 2)
    // a positive roll lowers the right (+X) side
    expect(quatRotate(attitudeQuat(0.3, 0, 0), [1, 0, 0])[1]).toBeLessThan(0)
  })

  it('stays continuous through a full roll as the reported roll wraps from +180° to −180°', () => {
    let cur = attitudeQuat(0, 0, 0.4)
    let heading = 0.4
    let maxStep = 0
    for (let k = 1; k <= 72; k++) {
      const roll = ((k * 5 + 180) % 360) - 180 // 5° steps, wraps at 180°
      const target = attitudeQuat((roll * Math.PI) / 180, 0, 0.4)
      const next = smoothAttitude(cur, target, 0.5, heading)
      maxStep = Math.max(maxStep, quatAngle(cur, next.q))
      cur = next.q
      heading = next.heading
      expect(heading).toBeCloseTo(0.4, 6)
    }
    expect(maxStep).toBeLessThan((6 * Math.PI) / 180)
    // the yaw group times the attitude group is the smoothed orientation exactly
    const s = smoothAttitude(cur, attitudeQuat(2, 0.3, 0.4), 0.3, heading)
    expect(quatAngle(quatMul(yawQuat(s.heading), s.local), s.q)).toBeLessThan(1e-6)
  })

  it('stays continuous through a loop where the reported pitch passes ±90° and roll and yaw flip', () => {
    const yaw0 = 1.1
    let cur = attitudeQuat(0, 0, yaw0)
    let heading = yaw0
    let maxStep = 0
    let flips = 0
    let last = reported(cur)
    for (let k = 1; k <= 120; k++) {
      // the true orientation pitches up 3° per frame about the body's right axis
      const truth = quatMul(attitudeQuat(0, 0, yaw0), [Math.sin((k * 1.5 * Math.PI) / 180), 0, 0, Math.cos((k * 1.5 * Math.PI) / 180)])
      const r = reported(truth)
      if (Math.abs(r.roll - last.roll) > 3) flips++
      last = r
      const next = smoothAttitude(cur, attitudeQuat(r.roll, r.pitch, r.yaw), 0.6, heading)
      maxStep = Math.max(maxStep, quatAngle(cur, next.q))
      cur = next.q
      heading = next.heading
    }
    expect(flips).toBeGreaterThan(0) // the Euler angles did jump on the way
    expect(maxStep).toBeLessThan((4 * Math.PI) / 180) // the drawn airframe did not
  })

  it('slerps along the shorter arc and reaches the target', () => {
    const a = attitudeQuat(0, 0, 0)
    const b: Quat = attitudeQuat(0, 0, 0.5).map((x) => -x) as Quat // same orientation, opposite sign
    expect(quatAngle(quatSlerp(a, b, 0.5), attitudeQuat(0, 0, 0.25))).toBeLessThan(1e-6)
    expect(quatAngle(quatSlerp(a, b, 1), b)).toBeLessThan(1e-6)
  })

  it('keeps a heading for a nose straight up or down from the belly direction', () => {
    expect(quatHeading(attitudeQuat(0, Math.PI / 2, 0.7))).toBeCloseTo(0.7)
    expect(quatHeading(attitudeQuat(0, -Math.PI / 2, -2))).toBeCloseTo(-2)
  })
})
