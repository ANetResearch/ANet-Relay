// Inspector tab selection (02 §11), shared so other surfaces can jump to a tab (CONTRACTS.md §5).
import { create } from 'zustand'
import { useUi } from '@/store/ui'

// WP-B: 'bench' (bench procedures, 05 §4.5) sits between Control and Telemetry
export type InspectorTab = 'overview' | 'control' | 'bench' | 'telemetry' | 'parameters' | 'log'
export const INSPECTOR_TABS: readonly InspectorTab[] = ['overview', 'control', 'bench', 'telemetry', 'parameters', 'log']

interface TabState {
  tab: InspectorTab
  /** optional sub-section the target tab should bring into view, e.g. 'calibration' */
  focus: string | null
  setTab(tab: InspectorTab): void
  clearFocus(): void
}

export const useInspectorTab = create<TabState>()((set) => ({
  tab: 'overview',
  focus: null,
  setTab: (tab) => set({ tab, focus: null }),
  clearFocus: () => set({ focus: null }),
}))

/** Selects the aircraft, shows the inspector (panel or compact sheet) and switches to `tab`. */
export function openInspector(id: string, tab?: InspectorTab, focus: string | null = null): void {
  const ui = useUi.getState()
  ui.setWorkspace('fly')
  ui.select(id)
  if (typeof window !== 'undefined' && window.innerWidth < 1200) ui.setInspectorSheet(true)
  else ui.toggleInspector(false)
  if (tab) useInspectorTab.setState({ tab, focus })
}
