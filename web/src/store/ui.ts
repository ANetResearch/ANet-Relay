// Window-local UI state: workspace, selection, panel collapse, overlays. Persisted preferences (panel collapse,
// timeline window) go to localStorage under relay.ui; everything else resets per launch.
import { create } from 'zustand'

export type Workspace = 'fly' | 'agents' | 'records'
export type ConnectTab = 'scan' | 'address' | 'bridge'
export type SettingsSection = 'general' | 'safety' | 'controllers' | 'copilot' | 'network' | 'data' | 'about'
export type TimelineWindow = 1 | 5 | 15

export interface UiState {
  workspace: Workspace
  selectedId: string | null
  fleetCollapsed: boolean
  inspectorCollapsed: boolean
  timelineCollapsed: boolean
  timelineWindow: TimelineWindow
  /** compact layout (< 1200 px wide): the inspector opens as a sheet */
  inspectorSheetOpen: boolean
  commandOpen: boolean
  alertCenterOpen: boolean
  padsOpen: boolean
  connect: { open: boolean; tab: ConnectTab }
  settings: { open: boolean; section: SettingsSection }
  /** a click on the relay timeline asks Records to show this moment */
  recordsFocus: { vehicle: string; t: number } | null

  setWorkspace(w: Workspace): void
  select(id: string | null): void
  toggleFleet(force?: boolean): void
  toggleInspector(force?: boolean): void
  toggleTimeline(force?: boolean): void
  setTimelineWindow(w: TimelineWindow): void
  setInspectorSheet(open: boolean): void
  setCommandOpen(open: boolean): void
  setAlertCenterOpen(open: boolean): void
  setPadsOpen(open: boolean): void
  openConnect(tab?: ConnectTab): void
  closeConnect(): void
  openSettings(section?: SettingsSection): void
  closeSettings(): void
  focusRecords(vehicle: string, t: number): void
}

const KEY = 'relay.ui'
type Persisted = Pick<UiState, 'fleetCollapsed' | 'inspectorCollapsed' | 'timelineCollapsed' | 'timelineWindow'>

function load(): Partial<Persisted> {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? (JSON.parse(raw) as Partial<Persisted>) : {}
  } catch {
    return {}
  }
}
function save(s: UiState): void {
  try {
    const p: Persisted = {
      fleetCollapsed: s.fleetCollapsed,
      inspectorCollapsed: s.inspectorCollapsed,
      timelineCollapsed: s.timelineCollapsed,
      timelineWindow: s.timelineWindow,
    }
    localStorage.setItem(KEY, JSON.stringify(p))
  } catch {
    /* storage unavailable: preferences stay in memory */
  }
}

/** Layout defaults by window size (02 §4.1): 1280 x 800 folds the timeline; < 1200 px folds the inspector. */
export function layoutDefaults(w: number, h: number): Persisted {
  return { fleetCollapsed: false, inspectorCollapsed: w < 1200, timelineCollapsed: w < 1400 || h < 860, timelineWindow: 5 }
}

const vw = typeof window !== 'undefined' ? window.innerWidth : 1440
const vh = typeof window !== 'undefined' ? window.innerHeight : 900

export const useUi = create<UiState>()((set, get) => {
  const persist = (patch: Partial<UiState>) => {
    set(patch)
    save(get())
  }
  return {
    workspace: 'fly',
    selectedId: null,
    ...layoutDefaults(vw, vh),
    ...load(),
    inspectorSheetOpen: false,
    commandOpen: false,
    alertCenterOpen: false,
    padsOpen: false,
    connect: { open: false, tab: 'scan' },
    settings: { open: false, section: 'general' },
    recordsFocus: null,

    setWorkspace: (workspace) => set({ workspace }),
    select: (selectedId) => set({ selectedId }),
    toggleFleet: (force) => persist({ fleetCollapsed: force ?? !get().fleetCollapsed }),
    toggleInspector: (force) => persist({ inspectorCollapsed: force ?? !get().inspectorCollapsed }),
    toggleTimeline: (force) => persist({ timelineCollapsed: force ?? !get().timelineCollapsed }),
    setTimelineWindow: (timelineWindow) => persist({ timelineWindow }),
    setInspectorSheet: (inspectorSheetOpen) => set({ inspectorSheetOpen }),
    setCommandOpen: (commandOpen) => set({ commandOpen }),
    setAlertCenterOpen: (alertCenterOpen) => set({ alertCenterOpen }),
    setPadsOpen: (padsOpen) => set({ padsOpen }),
    openConnect: (tab = 'scan') => set({ connect: { open: true, tab } }),
    closeConnect: () => set((s) => ({ connect: { ...s.connect, open: false } })),
    openSettings: (section = 'general') => set({ settings: { open: true, section } }),
    closeSettings: () => set((s) => ({ settings: { ...s.settings, open: false } })),
    focusRecords: (vehicle, t) => set({ recordsFocus: { vehicle, t }, workspace: 'records' }),
  }
})
