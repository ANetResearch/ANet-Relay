// The window's own flight requests keep their pre-0.6 meaning under any control_frame (12 §3.1): every parameter
// that depends on the frame is sent with frame "relay"; named directions and frame-free skills are left alone.
import { describe, expect, it } from 'vitest'
import { framedSkill, relayPlan, relaySkillArgs } from './frames'

describe('control frames of the window', () => {
  it('pins yaw, goto, velocity and x / y / z moves to Relay axes', () => {
    expect(relaySkillArgs('yaw', { deg: 90 })).toEqual({ deg: 90, frame: 'relay' })
    expect(relaySkillArgs('goto', { x: 1, y: 2, z: 1.5 })).toEqual({ x: 1, y: 2, z: 1.5, frame: 'relay' })
    expect(relaySkillArgs('velocity', { vx: 0.5, vy: 0, vz: 0, yaw_rate_dps: 10 })).toEqual({ vx: 0.5, vy: 0, vz: 0, yaw_rate_dps: 10, frame: 'relay' })
    expect(relaySkillArgs('move', { x: 1 })).toEqual({ x: 1, frame: 'relay' })
  })

  it('leaves named directions, frame-free skills and an explicit frame alone', () => {
    expect(relaySkillArgs('move', { forward: 1, speed: 0.5 })).toEqual({ forward: 1, speed: 0.5 })
    expect(relaySkillArgs('takeoff', { alt: 1 })).toEqual({ alt: 1 })
    expect(relaySkillArgs('land')).toEqual({})
    expect(relaySkillArgs('yaw', { deg: 90, frame: 'frd' })).toEqual({ deg: 90, frame: 'frd' })
    expect(framedSkill('hold')).toBe(false)
  })

  it('pins plan steps that need a frame', () => {
    const steps = relayPlan([
      { action: 'takeoff', alt: 1 },
      { action: 'move', forward: 1 },
      { action: 'move', x: 1, y: 0, z: 0 },
      { action: 'yaw', deg: -45 },
      { action: 'goto', x: 0, y: 0, z: 1, frame: 'flu' },
      { action: 'land' },
    ])
    expect(steps.map((s) => s.frame ?? null)).toEqual([null, null, 'relay', 'relay', 'flu', null])
    expect(steps[1]).toEqual({ action: 'move', forward: 1 })
  })
})
