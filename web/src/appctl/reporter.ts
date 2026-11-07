// UiState reporter (04 §3.1): sends {"type":"ui_state"} over the operator socket when the interface changes (at
// most 5 Hz), when the socket (re)opens and every 5 s as a heartbeat, so the backend always holds a fresh view of
// what the window shows and knows which connection to forward ui_command to.
import { sendJson } from '@/api'
import { STATE_SOURCES, collectUiState, setShellWindowState } from './state'
import type { UiState } from './types'

export const REPORT_MIN_MS = 200
export const REPORT_HEARTBEAT_MS = 5000

export interface ReporterDeps {
  send(message: unknown): boolean
  collect(): UiState
  now(): number
}

export class UiReporter {
  private last = ''
  private lastSentAt = -Infinity
  private timer: ReturnType<typeof setTimeout> | null = null
  private heartbeat: ReturnType<typeof setInterval> | null = null

  constructor(
    private deps: ReporterDeps = { send: sendJson, collect: collectUiState, now: () => performance.now() },
  ) {}

  /** Something may have changed: report within the rate limit if it did. */
  poke(): void {
    if (this.timer) return
    const wait = Math.max(0, REPORT_MIN_MS - (this.deps.now() - this.lastSentAt))
    this.timer = setTimeout(() => {
      this.timer = null
      this.flush(false)
    }, wait)
  }

  /** Sends now. Without `force` an unchanged state is not repeated. */
  flush(force: boolean): boolean {
    const data = this.deps.collect()
    const text = JSON.stringify(data)
    if (!force && text === this.last) return false
    if (!this.deps.send({ type: 'ui_state', data })) return false
    this.last = text
    this.lastSentAt = this.deps.now()
    return true
  }

  start(): void {
    this.heartbeat = setInterval(() => this.flush(true), REPORT_HEARTBEAT_MS)
    this.poke()
  }

  stop(): void {
    if (this.timer) clearTimeout(this.timer)
    if (this.heartbeat) clearInterval(this.heartbeat)
    this.timer = null
    this.heartbeat = null
  }
}

let currentReporter: UiReporter | null = null

/** The socket just opened: tell the backend straight away. */
export function reportNow(): void {
  currentReporter?.flush(true)
}

/** Starts reporting for the main window. Returns the stop function. */
export function startUiReporter(): () => void {
  const reporter = new UiReporter()
  currentReporter = reporter
  const offs: (() => void)[] = STATE_SOURCES.map((s) => (s as { subscribe(l: () => void): () => void }).subscribe(() => reporter.poke()))
  const poke = () => reporter.poke()
  window.addEventListener('resize', poke)
  window.addEventListener('focus', poke)
  window.addEventListener('blur', poke)
  const app = window.relay?.app
  if (app) {
    void app.getWindowState().then((s) => {
      setShellWindowState(s)
      poke()
    }, () => undefined)
    offs.push(
      app.onWindowState((s) => {
        setShellWindowState(s)
        reporter.flush(false)
      }),
    )
  }
  reporter.start()
  return () => {
    reporter.stop()
    if (currentReporter === reporter) currentReporter = null
    for (const off of offs) off()
    window.removeEventListener('resize', poke)
    window.removeEventListener('focus', poke)
    window.removeEventListener('blur', poke)
  }
}

