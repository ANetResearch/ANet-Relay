// Boot sequence controller (02 §15, 05 §2). Owns the #boot-mask node written by index.html: drives the progress
// hairline from three stages (backend spawned -> health check passed -> first WebSocket frame), reveals the shell no
// earlier than the opening allows (app/bootFilm.ts: the director's cut, the short cut, the 1200 ms Minimal opening,
// <= 400 ms in reduced motion; a key, click or controller button skips the films), and holds the failure states:
// "Starting services…" after 8 s, "The backend did not start." after 20 s, ports 8765-8775 in use. The failure
// clock runs from startBoot() whatever the opening does.
import { create } from 'zustand'
import { motionReduced } from '@/ui/motion/tier'
import { minBootMs } from './bootFilm'
import { startBootRun, type BootRun } from './bootCinema'

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
/** Minimal opening (the v0.2 one) and reduced motion; the films' minimum is their timeline (bootFilm.ts). */
export const MIN_BOOT_MS = minBootMs('minimal')
export const MIN_BOOT_REDUCED_MS = minBootMs('reduced')

export const useBoot = create<BootState>()((set, get) => ({
  phase: 'booting',
  stage: 0,
  revealed: false,
  ports: [8765, 8775],
  setStage: (s) => {
    if (s <= get().stage) return
    set({ stage: s })
    paintProgress(s)
    if (s === 3) {
      // the backend is up: the slow and failure clocks are over even while the opening still plays
      clearTimers()
      scheduleReveal()
    }
  },
  fail: (reason, ports) => {
    if (get().phase === 'ready') return
    set({ phase: reason === 'ports' ? 'ports' : 'failed', ports: ports ?? get().ports })
  },
  retry: () => {
    set({ phase: 'booting', stage: 0 })
    paintProgress(0)
    armTimers()
    // the film is not replayed: the opening goes to its waiting frame (05 §2.3)
    run?.retry()
  },
}))

function mask(): HTMLElement | null {
  return typeof document === 'undefined' ? null : document.getElementById('boot-mask')
}

function paintProgress(stage: number): void {
  const bar = mask()?.querySelector<HTMLElement>('.boot-bar > i')
  if (bar) bar.style.setProperty('--boot-p', String(stage / 3))
  run?.stage(stage)
}

/** This launch's opening (null before startBoot, in tests and in the Copilot window). */
let run: BootRun | null = null
let revealPending = false

let slowTimer: ReturnType<typeof setTimeout> | null = null
let failTimer: ReturnType<typeof setTimeout> | null = null

function clearTimers(): void {
  if (slowTimer) clearTimeout(slowTimer)
  if (failTimer) clearTimeout(failTimer)
  slowTimer = null
  failTimer = null
}

function armTimers(): void {
  clearTimers()
  slowTimer = setTimeout(() => {
    if (useBoot.getState().phase === 'booting') useBoot.setState({ phase: 'slow' })
  }, SLOW_AFTER_MS)
  failTimer = setTimeout(() => {
    const p = useBoot.getState().phase
    if (p === 'booting' || p === 'slow') useBoot.setState({ phase: 'failed' })
  }, FAIL_AFTER_MS)
}

function scheduleReveal(): void {
  // the opening decides when (the end of its film, a skip, or its minimum); it calls reveal() then
  if (run) {
    run.ready()
    return
  }
  const min = motionReduced() ? MIN_BOOT_REDUCED_MS : MIN_BOOT_MS
  // measured from navigation start: the CSS animation in index.html started there
  setTimeout(reveal, Math.max(0, min - performance.now()))
}

function reveal(): void {
  if (useBoot.getState().revealed || revealPending) return
  revealPending = true
  clearTimers()
  useBoot.setState({ phase: 'ready' })
  paintProgress(3)
  const m = mask()
  if (!m) {
    useBoot.setState({ revealed: true })
    return
  }
  if (run?.painted) {
    // films: act 5 flips the frame away in voxels while the panels enter underneath; the mask goes when it is done
    m.dataset.state = 'revealed'
    useBoot.setState({ revealed: true })
    run.dissolve(() => m.remove())
    return
  }
  // let the full hairline be seen for a beat, then fade the mask (fast 250 ms) and let the panels enter
  const hold = motionReduced() ? 0 : (run?.tl.tokens.minimalHold ?? 160)
  setTimeout(() => {
    m.dataset.state = 'revealed'
    useBoot.setState({ revealed: true })
    const done = () => m.remove()
    if (motionReduced()) done()
    else setTimeout(done, 320)
  }, hold)
}

/** Starts the watchdog timers, the opening, and mirrors the phase onto the mask (index.html styles read data-phase). */
export function startBoot(): void {
  armTimers()
  try {
    run = startBootRun({ onReveal: reveal }, motionReduced())
  } catch (err) {
    // the opening is decoration: without it the boot falls back to the Minimal timing
    console.error('[relay] opening failed', err)
    document.documentElement.dataset.boot = 'minimal'
    run = null
  }
  useBoot.subscribe((s, prev) => {
    const m = mask()
    if (m && m.dataset.phase !== s.phase) m.dataset.phase = s.phase
    // failure stops the film at once (05 §2.1); Retry is handled in retry()
    if ((s.phase === 'failed' || s.phase === 'ports') && prev.phase !== s.phase) run?.fail()
  })
}
