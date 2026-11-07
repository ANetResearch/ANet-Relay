import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { normalizeApiBase, streamUrlFor } from './env'
import { RelayApiError, toApiError } from './rest'
import { backoffDelay, parseMessage, RelayStream, STICKS_MIN_INTERVAL_MS, type SocketLike } from './ws'

class FakeSocket implements SocketLike {
  readyState = 0
  sent: string[] = []
  onopen: ((ev: unknown) => void) | null = null
  onclose: ((ev: unknown) => void) | null = null
  onerror: ((ev: unknown) => void) | null = null
  onmessage: ((ev: { data: unknown }) => void) | null = null
  constructor(readonly url: string) {}
  send(d: string) {
    this.sent.push(d)
  }
  close() {
    this.readyState = 3
    this.onclose?.({})
  }
  open() {
    this.readyState = 1
    this.onopen?.({})
  }
}

describe('env', () => {
  it('normalises the API base', () => {
    expect(normalizeApiBase('http://127.0.0.1:8765')).toBe('http://127.0.0.1:8765/api/v1')
    expect(normalizeApiBase('http://127.0.0.1:8765/')).toBe('http://127.0.0.1:8765/api/v1')
    expect(normalizeApiBase('http://127.0.0.1:8765/api/v1/')).toBe('http://127.0.0.1:8765/api/v1')
    expect(streamUrlFor('http://127.0.0.1:8765/api/v1')).toBe('ws://127.0.0.1:8765/api/v1/stream')
  })
})

describe('REST errors (03 §10.1)', () => {
  it('parses the uniform error body', () => {
    const e = toApiError(409, { error: { code: 'possessed_by_human', message_en: 'Possessed by a human.', message_zh: '已被人工附身。', detail: { pad: 1 } } })
    expect(e).toBeInstanceOf(RelayApiError)
    expect(e.code).toBe('possessed_by_human')
    expect(e.status).toBe(409)
    expect(e.text('zh')).toBe('已被人工附身。')
    expect(e.detail).toEqual({ pad: 1 })
  })
  it('degrades for non-conforming bodies and network failures', () => {
    expect(toApiError(422, { detail: [{ loc: ['body'] }] }).code).toBe('validation_error')
    expect(toApiError(500, null, 'Internal Server Error').messageEn).toBe('Internal Server Error')
    expect(toApiError(0, null).code).toBe('network_error')
  })
})

describe('stream messages (03 §10.2)', () => {
  it('accepts the envelope variants', () => {
    expect(parseMessage('{"type":"hello","version":"0.1.0","operator":true,"session":null}')).toEqual({
      type: 'hello',
      hello: { type: 'hello', version: '0.1.0', operator: true, session: null },
    })
    expect(parseMessage({ type: 'fleet', data: { t: 1, vehicles: [] } }).type).toBe('fleet')
    expect(parseMessage({ type: 'fleet', fleet: { t: 1, vehicles: [] } }).type).toBe('fleet')
    expect(parseMessage({ type: 'event', data: { seq: 3, kind: 'stop' } }).type).toBe('event')
    expect(parseMessage('not json').type).toBe('other')
    expect(parseMessage({ type: 'fleet', data: {} }).type).toBe('other')
  })
  it('backs off exponentially to a 5 s cap', () => {
    expect(backoffDelay(0, 0.5)).toBe(250)
    expect(backoffDelay(1, 0.5)).toBe(500)
    expect(backoffDelay(3, 0.5)).toBe(2000)
    expect(backoffDelay(10, 0.5)).toBe(5000)
    expect(backoffDelay(0, 0)).toBe(200)
    expect(backoffDelay(0, 0.999)).toBeLessThanOrEqual(300)
  })
})

describe('RelayStream', () => {
  let now = 0
  let sockets: FakeSocket[] = []
  beforeEach(() => {
    vi.useFakeTimers()
    now = 0
    sockets = []
  })
  afterEach(() => vi.useRealTimers())
  const make = (h = {}) =>
    new RelayStream(
      () => 'ws://x/stream',
      h,
      (u) => {
        const s = new FakeSocket(u)
        sockets.push(s)
        return s
      },
      () => now,
    )

  it('sends sticks at most once per 20 ms per vehicle, latest frame wins', () => {
    const s = make()
    s.start()
    sockets[0].open()
    const f = (r: number) => ({ roll: r, pitch: 0, yaw: 0, throttle: 0.5 })
    expect(s.sendSticks('sim-1', f(0.1))).toBe(true)
    now = 5
    s.sendSticks('sim-1', f(0.2))
    now = 10
    s.sendSticks('sim-1', f(0.3))
    s.sendSticks('sim-2', f(0.9))
    const sticks = () => sockets[0].sent.map((m) => JSON.parse(m)).filter((m) => m.type === 'sticks')
    expect(sticks().map((m) => [m.vehicle, m.roll])).toEqual([['sim-1', 0.1], ['sim-2', 0.9]])
    now = STICKS_MIN_INTERVAL_MS
    vi.advanceTimersByTime(STICKS_MIN_INTERVAL_MS)
    expect(sticks().map((m) => [m.vehicle, m.roll])).toEqual([['sim-1', 0.1], ['sim-2', 0.9], ['sim-1', 0.3]])
    s.stop()
  })

  it('never queues sticks across a disconnect and reconnects with backoff', () => {
    const states: string[] = []
    const s = make({ onState: (st: string) => states.push(st) })
    s.start()
    sockets[0].open()
    now = 100
    s.sendSticks('sim-1', { roll: 0, pitch: 0, yaw: 0, throttle: 0 })
    now = 105
    s.sendSticks('sim-1', { roll: 1, pitch: 0, yaw: 0, throttle: 0 })
    sockets[0].close()
    expect(s.sendSticks('sim-1', { roll: 1, pitch: 0, yaw: 0, throttle: 0 })).toBe(false)
    vi.advanceTimersByTime(400)
    expect(sockets.length).toBe(2)
    sockets[1].open()
    expect(sockets[1].sent.filter((m) => m.includes('sticks'))).toEqual([])
    expect(states).toContain('closed')
    expect(states[states.length - 1]).toBe('open')
    s.stop()
  })

  it('treats a silent socket as stale and reconnects', () => {
    const s = make()
    s.start()
    sockets[0].open()
    now = 3500
    vi.advanceTimersByTime(1000)
    expect(sockets[0].readyState).toBe(3)
    s.stop()
  })
})
