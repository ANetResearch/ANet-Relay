// WebSocket client for /api/v1/stream (03 §10.2). Server: hello -> fleet (10 Hz) -> event. Client: sticks, ping.
// Reconnects with capped exponential backoff and jitter. Sticks are never queued across a reconnect: a frame that
// cannot be sent now is dropped (the backend stops human sticks when the socket closes, 03 §10.3 / H2), and each
// vehicle is limited to one frame per 20 ms (50 Hz); within a window the latest frame wins.
import type { Fleet, Hello } from './types'
import type { RelayEvent } from './events'

export type StreamState = 'idle' | 'connecting' | 'open' | 'closed'

export interface StreamHandlers {
  onHello?(h: Hello): void
  onFleet?(f: Fleet): void
  onEvent?(e: RelayEvent): void
  onState?(s: StreamState, info: { attempt: number; nextRetryMs: number | null }): void
}

export interface SocketLike {
  readyState: number
  send(data: string): void
  close(code?: number, reason?: string): void
  onopen: ((ev: unknown) => void) | null
  onclose: ((ev: unknown) => void) | null
  onerror: ((ev: unknown) => void) | null
  onmessage: ((ev: { data: unknown }) => void) | null
}
export type SocketFactory = (url: string) => SocketLike

export const STICKS_MIN_INTERVAL_MS = 20
export const STALE_AFTER_MS = 3000
const PING_EVERY_MS = 5000
const OPEN = 1

/** attempt 0 -> 250 ms, doubling to a 5 s cap, +-20 % jitter (rnd in [0, 1)) */
export function backoffDelay(attempt: number, rnd: number = Math.random()): number {
  const base = Math.min(5000, 250 * 2 ** Math.max(0, attempt))
  return Math.round(base * (0.8 + 0.4 * rnd))
}

export type StreamMessage =
  | { type: 'hello'; hello: Hello }
  | { type: 'fleet'; fleet: Fleet }
  | { type: 'event'; event: RelayEvent }
  | { type: 'other' }

/** Tolerant envelope parser: {type|kind, data|fleet|event|...payload}. */
export function parseMessage(raw: unknown): StreamMessage {
  let m: Record<string, unknown>
  try {
    m = (typeof raw === 'string' ? JSON.parse(raw) : raw) as Record<string, unknown>
  } catch {
    return { type: 'other' }
  }
  if (!m || typeof m !== 'object') return { type: 'other' }
  const type = (m.type ?? m.kind) as string | undefined
  if (type === 'hello') {
    const h = (m.data ?? m.hello ?? m) as Hello
    return { type: 'hello', hello: { type: 'hello', version: String(h.version ?? ''), operator: Boolean(h.operator), session: (h.session as string | null) ?? null } }
  }
  if (type === 'fleet') {
    const f = (m.data ?? m.fleet ?? m) as Fleet
    if (f && Array.isArray(f.vehicles)) return { type: 'fleet', fleet: f }
    return { type: 'other' }
  }
  if (type === 'event') {
    const e = (m.data ?? m.event ?? m) as RelayEvent
    if (e && typeof e.seq === 'number') return { type: 'event', event: e }
  }
  return { type: 'other' }
}

export class RelayStream {
  private sock: SocketLike | null = null
  private attempt = 0
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private pingTimer: ReturnType<typeof setInterval> | null = null
  private lastMessageAt = 0
  private stopped = true
  private state: StreamState = 'idle'
  private lastStick = new Map<string, number>()
  private pending = new Map<string, { roll: number; pitch: number; yaw: number; throttle: number }>()
  private flushTimers = new Map<string, ReturnType<typeof setTimeout>>()

  constructor(
    private url: () => string,
    private handlers: StreamHandlers,
    private factory: SocketFactory = (u) => new WebSocket(u) as unknown as SocketLike,
    private now: () => number = () => performance.now(),
  ) {}

  get status(): StreamState {
    return this.state
  }

  start(): void {
    if (!this.stopped) return
    this.stopped = false
    this.open()
  }

  stop(): void {
    this.stopped = true
    this.clearTimers()
    const s = this.sock
    this.sock = null
    if (s) {
      s.onclose = s.onerror = s.onmessage = s.onopen = null
      s.close(1000, 'client stop')
    }
    this.setState('closed', null)
  }

  /** Reconnect now (Retry button); resets the backoff. */
  reconnect(): void {
    this.attempt = 0
    if (this.retryTimer) clearTimeout(this.retryTimer)
    this.retryTimer = null
    if (this.sock) this.sock.close(4000, 'reconnect')
    else if (!this.stopped) this.open()
  }

  /**
   * Sends one stick frame for `vehicle`. Returns false when the socket is not open (the frame is dropped).
   * At most one frame per vehicle per STICKS_MIN_INTERVAL_MS; frames inside a window coalesce to the latest.
   */
  sendSticks(vehicle: string, s: { roll: number; pitch: number; yaw: number; throttle: number }): boolean {
    if (!this.sock || this.sock.readyState !== OPEN) {
      this.pending.delete(vehicle)
      return false
    }
    const t = this.now()
    const last = this.lastStick.get(vehicle) ?? -Infinity
    if (t - last >= STICKS_MIN_INTERVAL_MS) {
      this.pending.delete(vehicle)
      this.writeSticks(vehicle, s, t)
      return true
    }
    this.pending.set(vehicle, s)
    if (!this.flushTimers.has(vehicle)) {
      this.flushTimers.set(
        vehicle,
        setTimeout(() => {
          this.flushTimers.delete(vehicle)
          const p = this.pending.get(vehicle)
          this.pending.delete(vehicle)
          if (p && this.sock && this.sock.readyState === OPEN) this.writeSticks(vehicle, p, this.now())
        }, Math.max(0, STICKS_MIN_INTERVAL_MS - (t - last))),
      )
    }
    return true
  }

  /** Drops any coalesced frame for `vehicle` (call when input stops: blur, release, link lost). */
  cancelSticks(vehicle?: string): void {
    const ids = vehicle ? [vehicle] : [...this.pending.keys(), ...this.flushTimers.keys()]
    for (const id of ids) {
      this.pending.delete(id)
      const tm = this.flushTimers.get(id)
      if (tm) clearTimeout(tm)
      this.flushTimers.delete(id)
    }
  }

  private writeSticks(vehicle: string, s: { roll: number; pitch: number; yaw: number; throttle: number }, t: number): void {
    this.lastStick.set(vehicle, t)
    this.sock!.send(JSON.stringify({ type: 'sticks', vehicle, roll: s.roll, pitch: s.pitch, yaw: s.yaw, throttle: s.throttle }))
  }

  private open(): void {
    this.setState('connecting', null)
    let sock: SocketLike
    try {
      sock = this.factory(this.url())
    } catch {
      this.scheduleRetry()
      return
    }
    this.sock = sock
    sock.onopen = () => {
      this.attempt = 0
      this.lastMessageAt = this.now()
      this.setState('open', null)
      this.pingTimer = setInterval(() => this.keepAlive(), 1000)
    }
    sock.onmessage = (ev) => {
      this.lastMessageAt = this.now()
      const m = parseMessage(ev.data)
      if (m.type === 'hello') this.handlers.onHello?.(m.hello)
      else if (m.type === 'fleet') this.handlers.onFleet?.(m.fleet)
      else if (m.type === 'event') this.handlers.onEvent?.(m.event)
    }
    sock.onerror = () => {
      /* onclose follows */
    }
    sock.onclose = () => {
      if (this.sock !== sock) return
      this.sock = null
      this.clearTimers()
      if (!this.stopped) this.scheduleRetry()
      else this.setState('closed', null)
    }
  }

  private keepAlive(): void {
    const s = this.sock
    if (!s || s.readyState !== OPEN) return
    const idle = this.now() - this.lastMessageAt
    if (idle > STALE_AFTER_MS) {
      // the backend stopped talking (hung or crashed without closing): force a reconnect
      s.close(4001, 'stale')
      return
    }
    if (Math.floor(this.now() / 1000) % (PING_EVERY_MS / 1000) === 0) s.send('{"type":"ping"}')
  }

  private scheduleRetry(): void {
    const delay = backoffDelay(this.attempt++)
    this.setState('closed', delay)
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null
      if (!this.stopped) this.open()
    }, delay)
  }

  private clearTimers(): void {
    if (this.retryTimer) clearTimeout(this.retryTimer)
    if (this.pingTimer) clearInterval(this.pingTimer)
    this.retryTimer = null
    this.pingTimer = null
    this.cancelSticks()
  }

  private setState(s: StreamState, nextRetryMs: number | null): void {
    this.state = s
    this.handlers.onState?.(s, { attempt: this.attempt, nextRetryMs })
  }
}
