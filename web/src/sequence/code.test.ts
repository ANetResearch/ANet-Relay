import { describe, expect, it } from 'vitest'
import { nudgeNumber, parseCode, toCode } from './code'
import { parseArs, rippleFrom, resize, templateBlank, templateDoublet, templateHover, templateStep, duplicateBlock, blocks, type ArsDoc } from './model'

const example: ArsDoc = parseArs(
  JSON.stringify({
    format: 'ars', version: 1, name: '起飞-悬停-降落', mode: 'althold', duration_s: 19,
    trim: { roll: 0, pitch: -0.04, yaw: 0, throttle: 0 },
    tracks: {
      throttle: [{ t: 0, v: 0.5, label: 'spool' }, { t: 0.5, v: 0.75, label: 'climb' }, { t: 3, v: 0.5, label: 'hover' }, { t: 8, v: 0.3, label: 'descend' }, { t: 17, v: 0, label: 'settle' }],
      pitch: [{ t: 0, v: 0 }, { t: 4, v: -0.08, ease: 'ramp' }, { t: 6, v: 0 }],
    },
    end: 'disarm',
  }),
)

describe('time table', () => {
  it('round-trips every template and the documented example, in both time styles', () => {
    for (const doc of [templateHover(), templateStep(), templateDoublet(), templateBlank(), example]) {
      for (const style of ['rel', 'abs'] as const) {
        const out = parseCode(toCode(doc, style).text, doc)
        expect(out.ok && out.doc).toEqual(doc)
      }
    }
  })

  it('writes aligned columns with intervals', () => {
    const { text, rows, cells } = toCode(example)
    const lines = text.split('\n')
    expect(lines[0]).toBe('name  起飞-悬停-降落')
    expect(lines[2]).toBe('trim  pitch -0.04  roll 0  yaw 0  throttle 0')
    expect(lines[4]).toBe('time | throttle     | pitch  | roll | yaw')
    expect(lines[5]).toBe('0    | 0.50 spool   |  0.00  |      |')
    expect(lines[6]).toBe('+0.5 | 0.75 climb   |        |      |')
    expect(lines[8]).toBe('+1   |              | -0.08~ |      |')
    expect(lines[lines.length - 2]).toBe('+2   | end disarm')
    expect(rows.map((r) => r.t)).toEqual([0, 0.5, 3, 4, 6, 8, 17, 19])
    const c = cells.find((x) => x.track === 'pitch' && x.index === 1)!
    expect(lines[c.line - 1].slice(c.from, c.to)).toBe('-0.08~')
  })

  it('reads hand-written text: absolute and relative rows, labels, comments, missing cells', () => {
    const out = parseCode(['mode stabilize', '# warm up', '0 | 0.1 idle', '+1|0.6~ go \\| fast | +.2', '3 | 0.5', '4.5 | end hold'].join('\n'))
    expect(out.ok).toBe(true)
    if (!out.ok) return
    expect(out.doc.mode).toBe('stabilize')
    expect(out.doc.tracks.throttle).toEqual([
      { t: 0, v: 0.1, ease: 'hold', label: 'idle' },
      { t: 1, v: 0.6, ease: 'ramp', label: 'go | fast' },
      { t: 3, v: 0.5, ease: 'hold' },
    ])
    expect(out.doc.tracks.pitch).toEqual([{ t: 1, v: 0.2, ease: 'hold' }])
    expect(out.doc.duration_s).toBe(4.5)
    expect(out.doc.end).toBe('hold')
    expect(out.doc.source).toEqual({ comments: [{ t: 0, text: 'warm up' }] })
    expect(parseCode(toCode(out.doc).text, out.doc)).toMatchObject({ ok: true, doc: out.doc })
  })

  it('names the row and the rule of every problem', () => {
    const p = (text: string) => {
      const out = parseCode(text)
      return out.ok ? [] : out.problems.map((x) => x.en)
    }
    expect(p('0 | 1.2\n1 | end disarm')[0]).toBe('Row 1: throttle: the stick must be within 0 to 1')
    expect(p('0 | 0.5\n0 | 0.6\n1 | end disarm')[0]).toMatch(/Row 2: the time must be after/)
    expect(p('0 | 0.5\n+0.05 | 0.6\n1 | end disarm')[0]).toMatch(/at least 0.1 s/)
    expect(p('0 | 0.5')[0]).toMatch(/the last row must be/)
    expect(p('mode hover\n0 | 0.5\n1 | end disarm')[0]).toMatch(/Row 1: mode must be/)
    expect(p('0 | abc\n1 | end disarm')[0]).toMatch(/a cell is a number/)
  })

  it('nudges the number under the caret', () => {
    expect(nudgeNumber('+0.5 | 0.75 climb | -0.08~', 8, 0.01, 0.1)).toEqual({ line: '+0.5 | 0.76 climb | -0.08~', col: 11 })
    expect(nudgeNumber('+0.5 | 0.75 climb | -0.08~', 22, 0.05, 0.1)?.line).toBe('+0.5 | 0.75 climb | -0.03~')
    expect(nudgeNumber('+0.5 | 0.75', 2, 0.01, 0.1)?.line).toBe('+0.6 | 0.75')
    expect(nudgeNumber('+0.5 | climb', 9, 0.01, 0.1)).toBeNull()
  })
})

describe('ripple', () => {
  it('moves the other tracks with a longer block', () => {
    const doc = resize(example, 'throttle', 2, 5.5, true) // hover 5 s -> 5.5 s
    expect(doc.tracks.throttle!.map((k) => k.t)).toEqual([0, 0.5, 3, 8.5, 17.5])
    expect(doc.tracks.pitch!.map((k) => k.t)).toEqual([0, 4, 6]) // before the edited boundary: untouched
    const early = resize(example, 'throttle', 1, 3, true) // climb 2.5 s -> 3 s
    expect(early.tracks.pitch!.map((k) => k.t)).toEqual([0, 4.5, 6.5])
    expect(early.duration_s).toBe(19.5)
    expect(resize(example, 'throttle', 1, 3, false).tracks.pitch!.map((k) => k.t)).toEqual([0, 4, 6])
    expect(rippleFrom(example, 4, -10, 'throttle').tracks.pitch!.map((k) => k.t)).toEqual([0, 0.1, 0.2])
  })

  it('duplicates a block after itself', () => {
    const r = duplicateBlock(example, 'throttle', 1, false)
    const b = blocks(r.doc, 'throttle')
    expect(r.index).toBe(2)
    expect([b[2].v, b[2].label, b[2].end - b[2].start]).toEqual([0.75, 'climb', 2.5])
  })
})
