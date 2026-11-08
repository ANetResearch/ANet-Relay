import { describe, expect, it } from 'vitest'
import type { RelayEvent, Vehicle } from '@/api'
import { HoldTimer } from '@/ui/relay/HoldProgress'
import { matchesWord } from '@/ui/relay/TypeToConfirm'
import { comboOf, isStopAll } from './keyboard'
import { layoutModeFor } from './layout'
import { TimelineModel } from './timeline/model'

const key = (code: string, mods: Partial<Record<'metaKey' | 'ctrlKey' | 'altKey' | 'shiftKey', boolean>> = {}, k = '') => ({
  code, key: k || code, metaKey: false, ctrlKey: false, altKey: false, shiftKey: false, ...mods,
})

describe('keyboard layer', () => {
  it('maps physical keys to combos on both platforms', () => {
    expect(comboOf(key('Period', { metaKey: true }, '.'), true)).toBe('mod+.')
    expect(comboOf(key('Period', { ctrlKey: true }, '.'), false)).toBe('mod+.')
    expect(isStopAll(comboOf(key('Period', { metaKey: true }, '.'), true))).toBe(true)
    // Option changes e.key on macOS; the physical key still wins
    expect(comboOf(key('KeyB', { metaKey: true, altKey: true }, '∫'), true)).toBe('alt+mod+b')
    expect(comboOf(key('KeyK', { metaKey: true }, 'k'), true)).toBe('mod+k')
    expect(comboOf(key('Digit1', {}, '1'), true)).toBe('1')
    expect(comboOf(key('ArrowUp', {}, 'ArrowUp'), true)).toBe('up')
    expect(comboOf(key('Escape', {}, 'Escape'), true)).toBe('escape')
  })
})

describe('layout modes (02 §4.1)', () => {
  it('matches the three reference sizes', () => {
    expect(layoutModeFor(1440)).toBe('wide')
    expect(layoutModeFor(1280)).toBe('medium')
    expect(layoutModeFor(1100)).toBe('compact')
  })
})

describe('confirmations', () => {
  it('type-to-confirm needs the exact word', () => {
    expect(matchesWord('ARM', 'ARM')).toBe(true)
    expect(matchesWord('arm', 'ARM')).toBe(false)
    expect(matchesWord('ARM ', 'ARM')).toBe(false)
    expect(matchesWord('ANGLE_MAX', 'ANGLE_MAX')).toBe(true)
  })
  it('hold timer fires once per press and resets on release', () => {
    const h = new HoldTimer(1000)
    expect(h.poll(0)).toBe(false)
    h.press(0)
    expect(h.progress(500)).toBe(0.5)
    expect(h.poll(999)).toBe(false)
    expect(h.poll(1000)).toBe(true)
    expect(h.poll(1500)).toBe(false)
    h.release()
    expect(h.progress(1600)).toBe(0)
    h.press(2000)
    expect(h.poll(3000)).toBe(true)
  })
})

function vehicle(id: string, over: Partial<Vehicle>): Vehicle {
  return {
    id, kind: 'real', label: id, link: { state: 'ok' }, armed: false, controller: 'none', claim: null, possession: null, ...over,
  } as Vehicle
}
let seq = 0
const ev = (t: number, kind: RelayEvent['kind'], vehicle: string | null, data: Record<string, unknown> = {}): RelayEvent => ({
  seq: ++seq, t, kind, vehicle, session: null, text_en: '', text_zh: '', data,
})

describe('relay timeline model (02 §4.5)', () => {
  it('builds agent, human and waiting segments with a handback tick', () => {
    const m = new TimelineModel()
    m.applyEvents([
      ev(10, 'claim_started', 'q1', { name: 'harness-a' }),
      ev(20, 'possession_started', 'q1', { input: 'gamepad', pad: 1 }),
      ev(30, 'relay_handback', 'q1', { name: 'harness-a' }),
    ])
    const l = m.lanes.get('q1')!
    expect(l.segs.map((s) => [s.kind, s.label, s.waiting, s.start, s.end])).toEqual([
      ['agent', 'harness-a', null, 10, 20],
      ['human', 'pad:1', 'harness-a', 20, 30],
      ['agent', 'harness-a', null, 30, null],
    ])
    expect(l.ticks).toEqual([{ t: 30, kind: 'handback', label: 'harness-a' }])
  })

  it('reconciles with snapshots and tracks armed and lost intervals', () => {
    const m = new TimelineModel()
    m.applySnapshot({ t: 100, vehicles: [vehicle('q1', { armed: true })] })
    m.applySnapshot({
      t: 105,
      vehicles: [vehicle('q1', { armed: true, controller: 'human', possession: { input: 'keyboard', pad: null, since: 105 }, link: { state: 'lost' } as Vehicle['link'] })],
    })
    m.applySnapshot({ t: 110, vehicles: [vehicle('q1', { armed: false })] })
    const l = m.lanes.get('q1')!
    expect(l.segs.map((s) => [s.kind, s.start, s.end])).toEqual([
      ['none', 100, 105],
      ['human', 105, 110],
      ['none', 110, null],
    ])
    expect(l.armed).toEqual([{ start: 100, end: 110 }])
    expect(l.lost).toEqual([{ start: 105, end: 110 }])
    expect(m.segmentAt('q1', 107)?.kind).toBe('human')
  })

  it('moves a boundary back when the event arrives after the snapshot', () => {
    const m = new TimelineModel()
    m.applySnapshot({ t: 100, vehicles: [vehicle('q1', {})] })
    m.applySnapshot({ t: 100.1, vehicles: [vehicle('q1', { controller: 'human', possession: { input: 'keyboard', pad: null, since: 100 } })] })
    m.applyEvents([ev(100.02, 'possession_started', 'q1', { input: 'keyboard' })])
    const l = m.lanes.get('q1')!
    expect(l.segs.map((s) => [s.kind, s.start])).toEqual([
      ['none', 100],
      ['human', 100.02],
    ])
  })

  it('stops all lanes on stop_all and drops lanes of removed aircraft', () => {
    const m = new TimelineModel()
    m.applySnapshot({ t: 1, vehicles: [vehicle('a', {}), vehicle('b', {})] })
    m.applyEvents([ev(2, 'stop_all', null)])
    expect(m.lanes.get('a')!.ticks[0].kind).toBe('stop')
    expect(m.lanes.get('b')!.ticks[0].kind).toBe('stop')
    m.applySnapshot({ t: 3, vehicles: [vehicle('a', {})] })
    expect(m.lanes.has('b')).toBe(false)
  })
  // WP-1 (06 §2.6): an onboard program is its own segment, labelled with the program's name
  it('shows program segments from events and snapshots', () => {
    const m = new TimelineModel()
    m.applyEvents([
      ev(10, 'claim_started', 'q1', { name: 'harness-a' }),
      ev(12, 'program_started', 'q1', { program: 'hover_trim', name: 'Hover trim', rev: 1 }),
      ev(20, 'possession_started', 'q1', { input: 'keyboard' }),
      ev(25, 'possession_released', 'q1'),
      ev(26, 'program_resumed', 'q1', { name: 'Hover trim' }),
      ev(30, 'program_stopped', 'q1', { name: 'Hover trim', reason: 'stopped' }),
    ])
    const l = m.lanes.get('q1')!
    expect(l.segs.map((s) => [s.kind, s.label, s.start])).toEqual([
      ['agent', 'harness-a', 10],
      ['program', 'Hover trim', 12],
      ['human', 'keyboard', 20],
      ['none', null, 25],
      ['program', 'Hover trim', 26],
      ['agent', 'harness-a', 30],
    ])
    const snap = new TimelineModel()
    const program = { name: 'Patrol' } as Vehicle['program']
    snap.applySnapshot({ t: 1, vehicles: [vehicle('q2', { controller: 'program', program })] })
    expect(snap.segmentAt('q2', 1)).toMatchObject({ kind: 'program', label: 'Patrol' })
  })
})
