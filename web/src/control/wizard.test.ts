import { describe, expect, it } from 'vitest'
import { readMapped } from './devices'
import { advance, beginBind, buildProfile, canAdvance, clearBind, feed, retreat, startWizard, type Snapshot } from './wizard'

const snap = (axes: number[], buttons: boolean[] = [false, false]): Snapshot => ({ axes, buttons })
// a transmitter with a scrambled layout: roll on 3 (inverted), pitch on 0, throttle on 1 (rests low), yaw on 2
const REST = [0, -1, 0, 0, -1, -1]

function stickSteps() {
  let s = startWizard('transmitter')
  s = feed(s, snap(REST))
  s = feed(s, snap([0, -1, 0, -0.9, -1, -1])) // roll right = axis 3 going negative
  expect(s.step).toBe('pitch')
  s = feed(s, snap(REST))
  s = feed(s, snap([0.95, -1, 0, 0, -1, -1]))
  expect(s.step).toBe('throttle')
  s = feed(s, snap(REST))
  s = feed(s, snap([0, 1, 0, 0, -1, -1]))
  expect(s.step).toBe('yaw')
  s = feed(s, snap(REST))
  s = feed(s, snap([0, -1, 0.9, 0, -1, -1]))
  expect(s.step).toBe('range')
  return s
}

describe('mapping wizard (05 §6.3)', () => {
  it('detects each axis and its direction', () => {
    const s = stickSteps()
    expect(s.found).toEqual({
      roll: { index: 3, invert: true },
      pitch: { index: 0, invert: false },
      throttle: { index: 1, invert: false },
      yaw: { index: 2, invert: false },
    })
  })
  it('ignores small movement and axes already assigned', () => {
    let s = startWizard('generic')
    s = feed(s, snap([0, 0, 0, 0]))
    s = feed(s, snap([0.3, 0, 0, 0]))
    expect(s.step).toBe('roll')
    s = feed(s, snap([0.8, 0, 0, 0]))
    expect(s.step).toBe('pitch')
    s = feed(s, snap([0, 0, 0, 0]))
    s = feed(s, snap([-1, 0, 0, 0])) // roll axis again: not a pitch candidate
    expect(s.step).toBe('pitch')
  })
  it('needs the full range, then the centre, before switches', () => {
    let s = stickSteps()
    expect(canAdvance(s)).toBe(false)
    for (const a of [[-1, 1, -1, 1, -1, -1], [1, -1, 1, -1, -1, -1]]) s = feed(s, snap(a))
    expect(canAdvance(s)).toBe(true)
    s = advance(s)
    expect(s.step).toBe('centre')
    expect(canAdvance(s)).toBe(false)
    s = feed(s, snap([0.02, -1, -0.01, 0.03, -1, -1]))
    s = advance(s)
    expect(s.step).toBe('switches')
  })
  it('binds switches on axes and buttons and builds a working profile', () => {
    let s = stickSteps()
    for (const a of [[-1, 1, -1, 1, -1, -1], [1, -1, 1, -1, -1, -1]]) s = feed(s, snap(a))
    s = advance(s)
    s = feed(s, snap([0.02, -1, -0.01, 0.03, -1, -1]))
    s = advance(s)
    s = beginBind(s, 'assist', snap([0, -1, 0, 0, -1, -1]))
    s = feed(s, snap([0, -1, 0, 0, 1, -1]))
    expect(s.binding).toBeNull()
    s = beginBind(s, 'fire', snap([0, -1, 0, 0, 1, -1]))
    s = feed(s, snap([0, -1, 0, 0, 1, -1], [false, true]))
    s = beginBind(s, 'stop', snap([0, -1, 0, 0, 1, -1]))
    s = clearBind(s, 'stop')
    expect(s.switches).toEqual([
      { action: 'assist', source: { type: 'axis', index: 4, above: true } },
      { action: 'fire', source: { type: 'button', index: 1 } },
    ])
    const p = buildProfile(s, 'radio', 42)!
    expect(p.updatedAt).toBe(42)
    expect(p.axes.roll.center).toBeCloseTo(0.03, 9)
    expect(p.axes.throttle.center).toBe(0) // non-centring throttle: mid of its range
    // roll pushed right reads +1, throttle low reads 0 %, switches follow
    const r = readMapped({ axes: [0, -1, 0, -1, 1, -1], buttons: [{ pressed: false, value: 0 }, { pressed: true, value: 1 }] }, p)
    expect(r.logical.roll).toBe(1)
    expect(r.throttle01).toBe(0)
    expect(r.switches.assist).toBe(true)
    expect(r.switches.fire).toBe(true)
  })
  it('goes back and forgets the axis of the step it returns to', () => {
    let s = stickSteps()
    s = retreat(s)
    expect(s.step).toBe('yaw')
    expect(s.found.yaw).toBeUndefined()
    expect(buildProfile(s, 'x', 0)).toBeNull()
  })
})
