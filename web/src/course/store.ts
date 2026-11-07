// Ring course window state: course definitions (GET /courses, scaled by the backend to the safety volume), the
// course chosen for Start / G, best times seen in this window, and the completion card. A store subscription
// (outside React) watches every aircraft's run for the transitions the card and the pass animation need.
import { create } from 'zustand'
import { api } from '@/api'
import type { RelayEvent } from '@/api'
import { useRelay } from '@/store/relay'
import { isActive, parseCourses, type CourseDef, type CourseView } from './geometry'

export interface FinishInfo {
  vehicleId: string
  label: string
  courseId: string
  nameEn: string
  nameZh: string
  total: number
  splits: number[]
  newBest: boolean
  /** performance.now() when the card opened (keys the entry animation) */
  at: number
}

export interface RingPass {
  /** 0-based index of the ring just passed */
  ring: number
  split: number | null
  /** performance.now() when the pass was observed */
  at: number
}

/** How long the HUD keeps "Aborted · reason" (02 §10.6). */
export const ABORT_SHOW_MS = 2000

interface CourseState {
  defs: CourseDef[] | null
  loading: boolean
  /** course id for Start and the G key */
  chosen: string
  /** best total per course id seen in this window */
  best: Record<string, number>
  finish: FinishInfo | null
  /** vehicles whose abort the HUD shows right now (for 2 s after it was observed) */
  aborted: Record<string, true>
  /** latest ring pass per vehicle, for the pass flash and the split pop-in */
  passes: Record<string, RingPass>

  load(force?: boolean): Promise<void>
  choose(id: string): void
  closeFinish(): void
}

const KEY = 'relay.course'

function loadChosen(): string {
  try {
    return localStorage.getItem(KEY) || 'cage-loop'
  } catch {
    return 'cage-loop'
  }
}

export const useCourse = create<CourseState>()((set, get) => ({
  defs: null,
  loading: false,
  chosen: typeof localStorage === 'undefined' ? 'cage-loop' : loadChosen(),
  best: {},
  finish: null,
  aborted: {},
  passes: {},

  load: async (force = false) => {
    if (get().loading || (get().defs && !force)) return
    set({ loading: true })
    try {
      const defs = parseCourses(await api.courses())
      set({ defs, loading: false })
    } catch {
      set({ loading: false, defs: get().defs ?? [] })
    }
  },
  choose: (chosen) => {
    set({ chosen })
    try {
      localStorage.setItem(KEY, chosen)
    } catch {
      /* storage unavailable */
    }
  },
  closeFinish: () => set({ finish: null }),
}))

/** Display name of a run or definition in a language. */
export function courseName(lang: 'en' | 'zh', id: string, view?: Pick<CourseView, 'name_en' | 'name_zh'> | null): string {
  const def = useCourse.getState().defs?.find((d) => d.id === id)
  const en = view?.name_en ?? def?.name_en ?? id
  const zh = view?.name_zh ?? def?.name_zh ?? en
  return lang === 'zh' ? zh : en
}

function finishedEvent(vehicle: string): RelayEvent | null {
  const evs = useRelay.getState().events
  for (let i = evs.length - 1; i >= 0 && i >= evs.length - 200; i--) {
    const e = evs[i]
    if (e.kind === 'course_finished' && e.vehicle === vehicle) return e
  }
  return null
}

let watching = 0
let stopWatch: (() => void) | null = null

/**
 * Starts the run watcher (reference counted): opens the completion card when a run that no agent flies finishes
 * (02 §10.5: agent runs only toast, which the shell does), remembers best times, stamps aborts for the HUD, and
 * reloads the course definitions when the safety volume changes.
 */
export function watchCourses(): () => void {
  watching++
  if (!stopWatch) {
    const prev = new Map<string, string>()
    const prevNext = new Map<string, string>()
    let lastFleet = useRelay.getState().fleet
    let volumeKey = ''
    const step = () => {
      const fleet = useRelay.getState().fleet
      if (!fleet) return
      const vk = `${fleet.safety_volume.x}x${fleet.safety_volume.y}x${fleet.safety_volume.z}`
      if (vk !== volumeKey) {
        const first = volumeKey === ''
        volumeKey = vk
        void useCourse.getState().load(!first)
      }
      const st = useCourse.getState()
      let best = st.best
      for (const v of fleet.vehicles) {
        const c = v.course as CourseView | null
        const was = prev.get(v.id) ?? ''
        const now = c ? `${c.course_id}|${c.state}` : ''
        prev.set(v.id, now)
        if (!c) continue
        if (c.best_s !== null && (best[c.course_id] === undefined || c.best_s < best[c.course_id])) {
          best = { ...best, [c.course_id]: c.best_s }
        }
        // ring passes: next went up by one within the same run
        const runKey = `${c.course_id}|${c.origin.join(',')}`
        const pn = prevNext.get(v.id)
        prevNext.set(v.id, `${runKey}|${c.next}`)
        if (pn && pn.startsWith(`${runKey}|`) && Number(pn.slice(runKey.length + 1)) === c.next - 1) {
          const pass: RingPass = { ring: c.next - 1, split: c.splits[c.next - 1] ?? null, at: performance.now() }
          useCourse.setState((s) => ({ passes: { ...s.passes, [v.id]: pass } }))
        }
        if (now === was) continue
        if (c.state === 'aborted' && was !== '') {
          const id = v.id
          useCourse.setState((s) => ({ aborted: { ...s.aborted, [id]: true } }))
          setTimeout(() => {
            useCourse.setState((s) => {
              const rest = { ...s.aborted }
              delete rest[id]
              return { aborted: rest }
            })
          }, ABORT_SHOW_MS)
        }
        // a fresh start clears the card of that aircraft
        if (isActive(c) && st.finish?.vehicleId === v.id) useCourse.setState({ finish: null })
        if (c.state === 'finished' && was !== '' && v.controller !== 'agent') {
          const ev = finishedEvent(v.id)
          const d = (ev?.data ?? {}) as { total_s?: number; splits?: number[]; new_best?: boolean }
          const total = typeof d.total_s === 'number' ? d.total_s : c.elapsed_s
          const previousBest = st.best[c.course_id]
          useCourse.setState({
            finish: {
              vehicleId: v.id,
              label: v.label,
              courseId: c.course_id,
              nameEn: courseName('en', c.course_id, c),
              nameZh: courseName('zh', c.course_id, c),
              total,
              splits: Array.isArray(d.splits) ? d.splits : c.splits,
              newBest: typeof d.new_best === 'boolean' ? d.new_best : previousBest === undefined || total <= previousBest,
              at: performance.now(),
            },
          })
        }
      }
      for (const id of prev.keys()) {
        if (fleet.vehicles.some((v) => v.id === id)) continue
        prev.delete(id)
        prevNext.delete(id)
      }
      if (best !== st.best) useCourse.setState({ best })
    }
    step()
    const unsub = useRelay.subscribe((s) => {
      if (s.fleet === lastFleet) return
      lastFleet = s.fleet
      step()
    })
    stopWatch = unsub
  }
  return () => {
    watching--
    if (watching === 0 && stopWatch) {
      stopWatch()
      stopWatch = null
    }
  }
}
