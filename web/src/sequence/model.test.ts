import { describe, expect, it } from 'vitest'
import {
  ArsError,
  blocks,
  fileName,
  insertAfter,
  moveStart,
  parseArs,
  removeBlock,
  sampleTrack,
  serialiseArs,
  setDocDuration,
  setDuration,
  setEase,
  setTrim,
  setSticksAt,
  setValue,
  splitAt,
  sticksAt,
  keyTimes,
  templateHover,
  armWaitThrottle,
  isManualMode,
  restValue,
  SEQ_MODES,
} from './model'
import { SEQ_STRINGS } from './strings'

describe('.ars timeline model', () => {
  it('reads blocks: hold keeps the stick, ramp moves to the next key, trim is added', () => {
    const doc = templateHover(5)
    const thr = blocks(doc, 'throttle')
    expect(thr.map((b) => [b.label, b.start, b.end, b.v])).toEqual([
      ['spool', 0, 0.5, 0.5],
      ['climb', 0.5, 3, 0.75],
      ['hover', 3, 8, 0.5],
      ['descend', 8, 17, 0.3],
      ['settle', 17, 19, 0],
    ])
    expect(sampleTrack(doc, 'throttle', 1)).toBe(0.75)
    const ramp = setEase(doc, 'throttle', 3, 'ramp')
    expect(sampleTrack(ramp, 'throttle', 12.5)).toBeCloseTo(0.15)
    const trimmed = setTrim(doc, 'pitch', -0.05)
    expect(sampleTrack(trimmed, 'pitch', 4)).toBeCloseTo(-0.05)
    expect(setTrim(doc, 'pitch', -0.9).trim.pitch).toBe(-0.3) // trim is clamped
  })

  it('changes the take-off throttle and the climb time in one edit each', () => {
    let doc = setValue(templateHover(), 'throttle', 1, 0.82)
    expect(sampleTrack(doc, 'throttle', 1)).toBe(0.82)
    doc = setDuration(doc, 'throttle', 1, 3.0) // climb 2.5 s -> 3 s: everything after moves later
    const thr = blocks(doc, 'throttle')
    expect(thr[2].start).toBe(3.5)
    expect(thr[4].start).toBe(17.5)
    expect(doc.duration_s).toBe(19.5)
    expect(setValue(doc, 'throttle', 1, 1.4).tracks.throttle![1].v).toBe(1) // stick range
  })

  it('splits, inserts, moves and removes blocks on a direction track', () => {
    let doc = templateHover()
    let r = splitAt(doc, 'pitch', 4)
    doc = setValue(r.doc, 'pitch', r.index, -0.08) // pull back from 4 s on
    r = splitAt(doc, 'pitch', 6)
    doc = setValue(r.doc, 'pitch', r.index, 0)
    expect(blocks(doc, 'pitch').map((b) => [b.start, b.end, b.v])).toEqual([
      [0, 4, 0],
      [4, 6, -0.08],
      [6, 19, 0],
    ])
    doc = moveStart(doc, 'pitch', 1, 3.5)
    expect(sampleTrack(doc, 'pitch', 3.75)).toBe(-0.08)
    const ins = insertAfter(doc, 'pitch', 1, 1)
    expect(blocks(ins.doc, 'pitch')[2]).toMatchObject({ start: 6, end: 7, v: -0.08 })
    expect(ins.doc.duration_s).toBe(20)
    doc = removeBlock(ins.doc, 'pitch', 1)
    expect(sampleTrack(doc, 'pitch', 4)).toBe(0)
  })

  it('sets both sticks at one moment and lists the moments sticks change', () => {
    let doc = templateHover()
    doc = setSticksAt(doc, 4, { pitch: -0.1, roll: 0.05, throttle: 0.55 })
    expect(sticksAt(doc, 4.5)).toEqual({ throttle: 0.55, pitch: -0.1, roll: 0.05, yaw: 0 })
    expect(sticksAt(doc, 3.5)).toEqual({ throttle: 0.5, pitch: 0, roll: 0, yaw: 0 })
    doc = setSticksAt(doc, 6, { pitch: 0, roll: 0 })
    expect(keyTimes(doc)).toEqual([0, 0.5, 3, 4, 6, 8, 17])
    // a moment that already starts a block: changed in place, no new key
    expect(keyTimes(setSticksAt(doc, 4, { pitch: -0.2 }))).toEqual(keyTimes(doc))
  })

  it('keeps every edit inside the 600 s limit', () => {
    const long = setDocDuration(templateHover(), 599.5)
    expect(insertAfter(long, 'throttle', 1, 2).doc).toBe(long) // no room: unchanged
    const stretched = setDuration(long, 'throttle', 1, 590)
    const keys = stretched.tracks.throttle!
    expect(keys[keys.length - 1].t).toBeLessThanOrEqual(600 - 0.1)
    expect(stretched.duration_s).toBeLessThanOrEqual(600)
    expect(() => parseArs(serialiseArs(stretched))).not.toThrow()
  })

  it('round-trips the file and rejects broken ones with the field named', () => {
    const doc = setTrim(templateHover(), 'roll', 0.02)
    expect(parseArs(serialiseArs(doc))).toEqual(doc)
    expect(fileName({ ...doc, name: 'Hover / test 1' })).toBe('Hover-test-1.ars')
    const bad = (patch: object) => () => parseArs(JSON.stringify({ ...doc, ...patch }))
    expect(bad({ format: 'relayseq' })).toThrow(ArsError)
    expect(bad({ version: 2 })).toThrow(/version/)
    expect(bad({ tracks: { throttle: [{ t: 0, v: 2 }] } })).toThrow(/tracks.throttle\[0\].v/)
    expect(bad({ tracks: { warp: [{ t: 0, v: 0 }] } })).toThrow(/unknown track/)
    expect(bad({ duration_s: 1 })).toThrow(/duration_s/)
    expect(() => parseArs('not json')).toThrow(/not JSON/)
  })
})

describe('timeline copy', () => {
  it('has the same keys in both languages', () => {
    expect(Object.keys(SEQ_STRINGS.zh).sort()).toEqual(Object.keys(SEQ_STRINGS.en).sort())
  })

  it('accepts acro (11 §9): rest throttle 0 like stabilize, arming throttle by firmware and mode', () => {
    expect(SEQ_MODES).toEqual(['stabilize', 'acro', 'althold', 'poshold'])
    const doc = parseArs(JSON.stringify({ format: 'ars', version: 1, mode: 'acro', tracks: { roll: [{ t: 0, v: 1 }, { t: 1.5, v: 0 }] }, duration_s: 3 }))
    expect(doc.mode).toBe('acro')
    expect(isManualMode('acro')).toBe(true)
    expect(isManualMode('althold')).toBe(false)
    expect(restValue('throttle', 'acro')).toBe(0)
    expect(restValue('throttle', 'stabilize')).toBe(0)
    expect(restValue('throttle', 'poshold')).toBe(0.5)
    expect(sampleTrack(doc, 'throttle', 1)).toBe(0)
    expect(sampleTrack(doc, 'roll', 1)).toBe(1)
    expect(JSON.parse(serialiseArs(doc)).mode).toBe('acro')
    expect(() => parseArs(JSON.stringify({ format: 'ars', version: 1, mode: 'sport', tracks: { roll: [{ t: 0, v: 0 }] } }))).toThrow(/acro/)
    // ArduPilot 4.6 and later arm only at zero throttle in every mode; PX4 holds mid stick in the hold modes
    for (const m of SEQ_MODES) expect(armWaitThrottle(m, 'ArduPilot')).toBe(0)
    expect(armWaitThrottle('althold', 'PX4')).toBe(0.5)
    expect(armWaitThrottle('poshold', 'px4')).toBe(0.5)
    expect(armWaitThrottle('acro', 'PX4')).toBe(0)
    expect(armWaitThrottle('stabilize', 'PX4')).toBe(0)
  })
})
