// Boot sequence controller (02 §15). Owns the #boot-mask node written by index.html: drives the progress hairline
// from three stages (backend spawned -> health check passed -> first WebSocket frame), reveals the shell no earlier
// than 1200 ms after navigation start (<= 400 ms in reduced motion), and holds the failure states: "Starting
// services…" after 8 s, "The backend did not start." after 20 s, ports 8765-8775 in use.
import { create } from 'zustand'
import { motionReduced } from '@/ui/motion/tier'

export type BootPhase = 'booting' | 'slow' | 'failed' | 'ports' | 'ready'

export interface BootState {
  phase: BootPhase
  /** 0..3 completed stages */
  stage: 0 | 1 | 2 | 3
  /** the mask has faded out and the panels have entered */
  revealed: boolean
  ports: [number, number]
  setStage(s: 1 | 2 | 3): void
  fail(reason: 'timeout' | 'ports', ports?: [number, number]): void
  retry(): void
}

export const SLOW_AFTER_MS = 8000
export const FAIL_AFTER_MS = 20000
export const MIN_BOOT_MS = 1200
export const MIN_BOOT_REDUCED_MS = 300

export const useBoot = create<BootState>()((set, get) => ({
  phase: 'booting',
  stage: 0,
  revealed: false,
  ports: [8765, 8775],
  setStage: (s) => {
    if (s <= get().stage) return
    set({ stage: s })
    paintProgress(s)
    if (s === 3) scheduleReveal()
  },
  fail: (reason, ports) => {
    if (get().phase === 'ready') return
    set({ phase: reason === 'ports' ? 'ports' : 'failed', ports: ports ?? get().ports })
  },
  retry: () => {
    set({ phase: 'booting', stage: 0 })
    paintProgress(0)
    armTimers()
  },
}))

function mask(): HTMLElement | null {
  return typeof document === 'undefined' ? null : document.getElementById('boot-mask')
}

function paintProgress(stage: number): void {
  const bar = mask()?.querySelector<HTMLElement>('.boot-bar > i')
  if (bar) bar.style.setProperty('--boot-p', String(stage / 3))
}

let slowTimer: ReturnType<typeof setTimeout> | null = null
let failTimer: ReturnType<typeof setTimeout> | null = null

function armTimers(): void {
  if (slowTimer) clearTimeout(slowTimer)
  if (failTimer) clearTimeout(failTimer)
  slowTimer = setTimeout(() => {
    if (useBoot.getState().phase === 'booting') useBoot.setState({ phase: 'slow' })
  }, SLOW_AFTER_MS)
  failTimer = setTimeout(() => {
    const p = useBoot.getState().phase
    if (p === 'booting' || p === 'slow') useBoot.setState({ phase: 'failed' })
  }, FAIL_AFTER_MS)
}

function scheduleReveal(): void {
  const min = motionReduced() ? MIN_BOOT_REDUCED_MS : MIN_BOOT_MS
  // measured from navigation start: the CSS animation in index.html started there
  setTimeout(reveal, Math.max(0, min - performance.now()))
}

function reveal(): void {
  if (slowTimer) clearTimeout(slowTimer)
  if (failTimer) clearTimeout(failTimer)
  useBoot.setState({ phase: 'ready' })
  paintProgress(3)
  const m = mask()
  if (!m) {
    useBoot.setState({ revealed: true })
    return
  }
  // let the full hairline be seen for a beat, then fade the mask (fast 250 ms) and let the panels enter
  const hold = motionReduced() ? 0 : 160
  setTimeout(() => {
    m.dataset.state = 'revealed'
    useBoot.setState({ revealed: true })
    const done = () => m.remove()
    if (motionReduced()) done()
    else setTimeout(done, 320)
  }, hold)
}

/** Starts the watchdog timers and mirrors the phase onto the mask (index.html styles read data-phase). */
export function startBoot(): void {
  armTimers()
  useBoot.subscribe((s) => {
    const m = mask()
    if (m && m.dataset.phase !== s.phase) m.dataset.phase = s.phase
  })
}
