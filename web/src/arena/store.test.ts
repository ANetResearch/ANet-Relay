import { afterEach, describe, expect, it } from 'vitest'
import { installStream, parseMessage, type ArenaView, type RelayEvent } from '@/api'
import { useRelay } from '@/store/relay'
import { marksOf, sendFire, useArena, watchArenaEvents } from './store'

export function arenaView(over: Partial<ArenaView> = {}): ArenaView {
  return {
    t: 1000,
    enabled: true,
    instance: '00000000000000aa',
    seat: 0,
    callsign: 'Alpha',
    error: null,
    peers: [],
    shots: [],
    local: { ammo: { 'sim-1': 20 }, hp: { 'sim-1': 80 }, reloading: { 'sim-1': false }, down: { 'sim-1': false }, max_ammo: 24, max_hp: 100, score: { hits: 1, downs: 0, deaths: 0 } },
    ...over,
  }
}

function ev(seq: number, kind: RelayEvent['kind'], vehicle: string | null, data: Record<string, unknown>): RelayEvent {
  return { seq, t: 0, kind, vehicle, session: null, text_en: '', text_zh: '', data }
}

afterEach(() => {
  useArena.getState().reset()
  useRelay.getState().reset()
  installStream(null)
})

describe('arena store', () => {
  it('parses the arena stream message and ignores malformed ones', () => {
    const v = arenaView()
    expect(parseMessage(JSON.stringify({ type: 'arena', data: v }))).toEqual({ type: 'arena', arena: v })
    expect(parseMessage({ type: 'arena', data: { enabled: true } }).type).toBe('other')
    expect(parseMessage({ type: 'arena' }).type).toBe('other')
  })

  it('turns arena hit events into marks', () => {
    const marks = marksOf(
      [
        ev(1, 'arena_hit', 'sim-1', { side: 'hit', point: [1, 2, 3], down: true }),
        ev(2, 'arena_hit', 'sim-2', { side: 'taken', dir: [0, 35, 0], point: [0, 0, 1], own_shot: false }),
        ev(3, 'arena_hit', 'sim-2', { side: 'taken', shooter: 'sim-1', own_shot: true }),
        ev(4, 'crashed', 'sim-2', {}),
        ev(5, 'arena_hit', 'sim-3', { side: 'bogus' }),
      ],
      42,
    )
    expect(marks.map((m) => [m.seq, m.kind, m.vehicle])).toEqual([
      [1, 'hit', 'sim-1'],
      [2, 'taken', 'sim-2'],
      [3, 'hit', 'sim-1'],
      [3, 'taken', 'sim-2'],
    ])
    expect(marks[0]).toMatchObject({ point: [1, 2, 3], down: true, at: 42 })
    expect(marks[1].dir).toEqual([0, 35, 0])
  })

  it('watches the event ring only while someone listens', () => {
    const stop = watchArenaEvents()
    useRelay.getState().applyEvents([ev(10, 'arena_hit', 'sim-1', { side: 'hit' })])
    expect(useArena.getState().marks.map((m) => m.seq)).toEqual([10])
    stop()
    useRelay.getState().applyEvents([ev(11, 'arena_hit', 'sim-1', { side: 'hit' })])
    expect(useArena.getState().marks.map((m) => m.seq)).toEqual([10])
  })

  it('sends fire as a stream message and never queues it', () => {
    expect(sendFire('sim-1', true)).toBe(false)
    const sent: unknown[] = []
    installStream({ sendSticks: () => true, cancelSticks: () => {}, reconnect: () => {}, send: (m) => (sent.push(m), true) })
    expect(sendFire('sim-1', true)).toBe(true)
    expect(sent).toEqual([{ type: 'fire', vehicle: 'sim-1', on: true }])
  })

  it('keeps the arena view apart from the fleet snapshot', () => {
    useArena.getState().applyArena(arenaView({ peers: [{ id: 'b', callsign: 'Bravo', seat: 6, rtt_ms: 3, stale: false, age_s: 0, score: { hits: 0, downs: 0, deaths: 0 }, vehicles: [] }] }))
    expect(useRelay.getState().fleet).toBeNull()
    expect(useArena.getState().view?.peers[0].callsign).toBe('Bravo')
  })
})
