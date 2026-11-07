// Stage window state (02 §4.4, §6.5): view mode, layer visibility, camera requests and one-shot visual pulses.
// Discrete only; the frame loop reads it with useStage.getState(). View and layers persist per viewer.
import { create } from 'zustand'
import { nextView, type ViewMode } from './camera'

export type { ViewMode }
/** auto: shown while an aircraft has a position (02 §6.1); on / off: forced from the layer menu */
export type SafetyLayer = 'auto' | 'on' | 'off'

export interface StageLayers {
  grid: boolean
  safety: SafetyLayer
  course: boolean
  labels: boolean
  trails: boolean
  /** LAN arena: remote aircraft, bullets and the scoreboard (05 §7.5, WP-D) */
  arena: boolean
}

export interface StageState {
  view: ViewMode
  layers: StageLayers
  /** bumped to drop the user's orbit and return to automatic overview framing */
  recenter: number
  /** a pending smooth fly-to (double click on an aircraft) */
  flyTo: { id: string; seq: number } | null
  /** one-shot ring ripples, e.g. a controller binding to an aircraft (02 §7.2) */
  pulses: { id: string; at: number }[]
  /** set by CourseHud while it is shown, so the stage moves its top overlays out of the way on narrow stages */
  courseHudShown: boolean

  setView(v: ViewMode): void
  cycleView(chaseAvailable: boolean): void
  setLayer<K extends keyof StageLayers>(k: K, v: StageLayers[K]): void
  requestRecenter(): void
  requestFlyTo(id: string): void
  pulse(id: string): void
  setCourseHudShown(shown: boolean): void
}

const KEY = 'relay.stage'
const DEFAULT_LAYERS: StageLayers = { grid: true, safety: 'auto', course: true, labels: true, trails: true, arena: true }

function load(): { view?: ViewMode; layers?: Partial<StageLayers> } {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? (JSON.parse(raw) as { view?: ViewMode; layers?: Partial<StageLayers> }) : {}
  } catch {
    return {}
  }
}

function save(s: Pick<StageState, 'view' | 'layers'>): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ view: s.view, layers: s.layers }))
  } catch {
    /* storage unavailable: preferences stay in memory */
  }
}

const saved = typeof localStorage !== 'undefined' ? load() : {}
const validView = (v: unknown): v is ViewMode => v === 'overview' || v === 'chase' || v === 'top'

export const useStage = create<StageState>()((set, get) => ({
  // chase needs a target that may not exist at launch, so a remembered chase starts as overview
  view: validView(saved.view) && saved.view !== 'chase' ? saved.view : 'overview',
  layers: { ...DEFAULT_LAYERS, ...(saved.layers ?? {}) },
  recenter: 0,
  flyTo: null,
  pulses: [],
  courseHudShown: false,

  setView: (view) => {
    if (view === 'overview' && get().view === 'overview') set((s) => ({ recenter: s.recenter + 1 }))
    set({ view })
    save(get())
  },
  cycleView: (chaseAvailable) => get().setView(nextView(get().view, chaseAvailable)),
  setLayer: (k, v) => {
    set((s) => ({ layers: { ...s.layers, [k]: v } }))
    save(get())
  },
  requestRecenter: () => set((s) => ({ recenter: s.recenter + 1 })),
  requestFlyTo: (id) => set((s) => ({ flyTo: { id, seq: (s.flyTo?.seq ?? 0) + 1 }, view: 'overview' })),
  pulse: (id) => set((s) => ({ pulses: [...s.pulses.filter((p) => performance.now() - p.at < 2000), { id, at: performance.now() }] })),
  setCourseHudShown: (courseHudShown) => {
    if (get().courseHudShown !== courseHudShown) set({ courseHudShown })
  },
}))

/** Ripples the selection ring of an aircraft once (02 §7.2: a controller bound to it). Safe to call from anywhere. */
export function stagePulse(vehicleId: string): void {
  useStage.getState().pulse(vehicleId)
}
