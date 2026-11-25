import { describe, expect, it } from 'vitest'
import { nudgeNumber, parseCode, toCode } from './code'
import { check, duplicateStep, estimate, fits, fromApi, insertStep, moveStep, newStep, nudge, removeSteps, setParam, templateSquare, toApi, volumeFor, type PlanDoc } from './model'

const square10: PlanDoc = {
  name: '10 m square',
  speed: 2.5,
  settle: null,
  frame: null,
  steps: [
    { action: 'takeoff', p: { alt: 10 } },
    { action: 'move', p: { forward: 10 } },
    { action: 'move', p: { left: 10 }, note: 'along the fence' },
    { action: 'move', p: { forward: -10 } },
    { action: 'move', p: { left: -10, speed: 1 } },
    { action: 'hover', p: { seconds: 5 } },
    { action: 'land', p: {} },
  ],
}

describe('plan code', () => {
  it('writes aligned columns and reads them back', () => {
    const { text, lines, cells } = toCode(square10)
    expect(text).toBe(
      ['plan    10 m square', 'speed   2.5', '', 'takeoff  alt 10', 'move     forward 10', 'move     left 10      # along the fence', 'move     forward -10', 'move     left -10     speed 1', 'hover    5', 'land', ''].join('\n'),
    )
    expect(lines).toEqual([4, 5, 6, 7, 8, 9, 10])
    const speed = cells.find((c) => c.step === 4 && c.key === 'speed')!
    expect(text.split('\n')[speed.line - 1].slice(speed.from, speed.to)).toBe('1')
    const back = parseCode(text)
    expect(back.ok && toCode(back.doc).text).toBe(text)
  })

  it('reads hand-written text: shorthand, aliases, comments', () => {
    const out = parseCode('# outdoor test\nplan Fence run\nspeed 2\n\ntakeoff 3\n# out\nmove back 2 right 1.5 settle 1\nturn 90\nwait 4\nreturn\n# done\n')
    expect(out.ok).toBe(true)
    if (!out.ok) return
    expect(out.doc.name).toBe('Fence run')
    expect(out.doc.steps.map((s) => [s.action, s.p])).toEqual([
      ['takeoff', { alt: 3 }],
      ['move', { forward: -2, left: -1.5, settle: 1 }],
      ['yaw', { deg: 90 }],
      ['hover', { seconds: 4 }],
      ['rtl', {}],
    ])
    expect(out.doc.steps[1].before).toEqual(['# out'])
    expect(out.doc.tail).toEqual(['# done'])
    expect(out.doc.head).toEqual(['# outdoor test'])
    expect(toCode(out.doc).text.startsWith('# outdoor test\nplan    Fence run\n')).toBe(true)
    // formatting keeps the comments where they were
    expect(toCode(out.doc).text).toContain('# out\nmove')
  })

  it('names the line and the rule of a problem', () => {
    const out = parseCode('takeoff alt 50\nmove sideways 2\nhover\nfly 3\nmove forward\n')
    expect(out.ok).toBe(false)
    if (out.ok) return
    expect(out.problems.map((p) => [p.line, p.en.split(':')[0]])).toEqual([
      [1, 'alt must be 0.2 to 30'],
      [2, 'move does not take "sideways"'],
      [2, 'move needs a distance'],
      [3, 'hover needs seconds, e.g. "hover 5"'],
      [4, 'Unknown step "fly"'],
      [5, 'forward needs a number'],
      [5, 'move needs a distance'],
    ])
    expect(parseCode('# nothing\n').ok).toBe(false)
  })

  it('nudges the number under the caret and keeps the columns', () => {
    expect(nudgeNumber('move     forward 10    speed 1', 17, 1, false)).toEqual({ line: 'move     forward 11    speed 1', col: 19 })
    expect(nudgeNumber('takeoff  alt 1.5', 15, -1, false)?.line).toBe('takeoff  alt 1.4')
    expect(nudgeNumber('move  forward 10  speed 1', 15, -1, false)?.line).toBe('move  forward 9   speed 1')
    expect(nudgeNumber('hover    5  # 5 s', 14, 1, false)).toBeNull()
    expect(nudgeNumber('land', 2, 1, false)).toBeNull()
  })
})

describe('plan model', () => {
  it('sends the defaults with the steps that take them', () => {
    expect(toApi(square10)).toEqual([
      { action: 'takeoff', alt: 10, speed: 2.5 },
      { action: 'move', forward: 10, left: 0, up: 0, speed: 2.5 },
      { action: 'move', forward: 0, left: 10, up: 0, speed: 2.5 },
      { action: 'move', forward: -10, left: 0, up: 0, speed: 2.5 },
      { action: 'move', forward: 0, left: -10, up: 0, speed: 1 },
      { action: 'hover', seconds: 5 },
      { action: 'land' },
    ])
    expect(toApi({ ...square10, speed: null, settle: 3 })[0]).toEqual({ action: 'takeoff', alt: 10 }) // settle needs a speed
    expect(toApi(square10, 5)).toEqual([{ action: 'hover', seconds: 5 }, { action: 'land' }])
  })

  it('reads a built-in plan and lifts the shared speed', () => {
    const doc = fromApi('du7_box', [
      { action: 'takeoff', alt: 2, speed: 0.5 },
      { action: 'move', forward: 1, speed: 0.5 },
      { action: 'move', left: -1, speed: 0.5 },
      { action: 'move', right: 2, down: 0.5, speed: 0.5 },
      { action: 'land' },
    ])
    expect(doc.speed).toBe(0.5)
    expect(doc.steps.map((s) => s.p)).toEqual([{ alt: 2 }, { forward: 1 }, { left: -1 }, { left: -2, up: -0.5 }, {}])
  })

  it('estimates the path, the time and the safety volume', () => {
    const est = estimate(square10)
    expect(est.legs.map((l) => l.to.map((n) => Math.round(n)))).toEqual([[0, 0, 10], [10, 0, 10], [10, 10, 10], [0, 10, 10], [0, 0, 10], [0, 0, 10], [0, 0, 0]])
    expect(est.radius).toBeCloseTo(14.142, 2)
    expect(est.legs[0].seconds).toBeGreaterThan(20) // 10 m at 0.5 m/s
    expect(est.legs[1].t0).toBeCloseTo(est.legs[0].seconds, 3)
    expect(volumeFor(est)).toEqual({ x: 30, y: 30, z: 10 })
    expect(fits(volumeFor(est), { x: 3, y: 3, z: 2 })).toBe(false)
    expect(fits(volumeFor(est), { x: 30, y: 30, z: 12 })).toBe(true)
    // a turn changes where "forward" leads
    const turned = estimate({ ...square10, steps: [{ action: 'takeoff', p: { alt: 2 } }, { action: 'yaw', p: { deg: 90 } }, { action: 'move', p: { forward: 3 } }] })
    expect(turned.legs[2].to.map((n) => Math.round(n))).toEqual([0, 3, 2])
  })

  it('edits steps', () => {
    let doc = templateSquare(2, 2)
    doc = nudge(doc, 1, 'forward', 1, false)
    expect(doc.steps[1].p.forward).toBe(2.1)
    doc = nudge(doc, 1, 'speed', 1, false) // from the plan's default
    expect(doc.steps[1].p.speed).toBe(0.6)
    expect(setParam(doc, 1, 'speed', null).steps[1].p.speed).toBeUndefined()
    expect(setParam(doc, 0, 'alt', null)).toBe(doc) // a required value stays
    expect(setParam(doc, 0, 'alt', 99).steps[0].p.alt).toBe(30)
    const ins = insertStep(doc, 0, newStep('hover'))
    expect(ins.index).toBe(1)
    expect(ins.doc.steps[1].action).toBe('hover')
    expect(moveStep(ins.doc, 1, 1).doc.steps[2].action).toBe('hover')
    expect(duplicateStep(doc, 1).doc.steps[2].p).toEqual(doc.steps[1].p)
    expect(removeSteps(doc, [1, 2]).steps).toHaveLength(4)
    expect(check(doc)).toEqual([])
    expect(check({ ...doc, steps: [{ action: 'move', p: {} }] })[0].en).toBe('move needs a distance')
  })
})

describe('plan strings', () => {
  it('has the same keys in both languages', async () => {
    const { PLAN_STRINGS } = await import('./strings')
    expect(Object.keys(PLAN_STRINGS.zh).sort()).toEqual(Object.keys(PLAN_STRINGS.en).sort())
  })
})
