// UiState collector (04 §3.1): one read of every store that describes what the window shows, in the shape the
// backend publishes at GET /app/ui. Pure reads, no side effects; the reporter decides when to send.
import { useLangStore } from '@/i18n'
import { useCopilot } from '@/copilot/store'
import { useInspectorTab } from '@/inspector/tabs'
import { useReplay } from '@/records/replayState'
import { usePrefs } from '@/settings/prefs'
import { useStage } from '@/stage/store'
import { useRelay } from '@/store/relay'
import { useUi } from '@/store/ui'
import type { AppWindowState } from '@/api'
import type { UiDialog, UiGamepad, UiPossession, UiState } from './types'

/** What the shell last reported about the main window; the DOM fills the gaps in a plain browser. */
let shellWindow: AppWindowState | null = null

export function setShellWindowState(s: AppWindowState | null): void {
  shellWindow = s
}

export function frontDialog(ui = useUi.getState()): UiDialog {
  if (ui.commandOpen) return 'palette'
  if (ui.settings.open) return 'settings'
  if (ui.connect.open) return 'connect'
  if (ui.alertCenterOpen) return 'alerts'
  if (ui.padsOpen) return 'pads'
  if (ui.inspectorSheetOpen) return 'inspector_sheet'
  return null
}

const MAPPING_NAME: Record<string, string> = { dualsense: 'DualSense', standard: 'Standard gamepad', unknown: 'Unrecognised gamepad' }

export function collectGamepads(): UiGamepad[] {
  return useRelay.getState().inputs.pads.map((p) => ({
    index: p.index,
    id: p.id,
    name: MAPPING_NAME[p.model] ?? p.model,
    mapping: p.model,
    connected: p.connected,
    vehicle: p.vehicle,
    input: 'gamepad' as const,
  }))
}

export function collectPossession(): UiPossession[] {
  const out: UiPossession[] = []
  for (const v of useRelay.getState().fleet?.vehicles ?? []) {
    if (!v.possession) continue
    out.push({ vehicle: v.id, input: v.possession.input as UiPossession['input'], pad: v.possession.pad ?? null })
  }
  return out
}

function windowInfo(): UiState['window'] {
  const w = typeof window !== 'undefined' ? window : undefined
  const doc = typeof document !== 'undefined' ? document : undefined
  return {
    focused: shellWindow ? shellWindow.focused : Boolean(doc?.hasFocus?.()),
    width: w?.innerWidth ?? 0,
    height: w?.innerHeight ?? 0,
    fullscreen: shellWindow ? shellWindow.fullscreen : Boolean(doc?.fullscreenElement),
  }
}

export function collectUiState(): UiState {
  const ui = useUi.getState()
  const prefs = usePrefs.getState()
  const replay = useReplay.getState()
  return {
    lang: useLangStore.getState().lang,
    workspace: ui.workspace,
    selected: ui.selectedId,
    view: useStage.getState().view,
    inspector: { open: !ui.inspectorCollapsed || ui.inspectorSheetOpen, tab: useInspectorTab.getState().tab },
    dialog: frontDialog(ui),
    settings_section: ui.settings.section,
    copilot_open: useCopilot.getState().open,
    prefs: { units: prefs.units, startView: prefs.startView, chaseOnCourse: prefs.chaseOnCourse, padDeadzone: prefs.padDeadzone, padExpo: prefs.padExpo },
    gamepads: collectGamepads(),
    possession: collectPossession(),
    window: windowInfo(),
    record: replay.active && replay.recordId !== null ? { session_id: replay.recordId, playing: replay.playing, t: replay.t } : null,
    version: (typeof window !== 'undefined' ? window.relay?.version : undefined) ?? 'dev',
  }
}

/** Stores whose changes can alter the state; the reporter subscribes to each. */
export const STATE_SOURCES = [useUi, useStage, useInspectorTab, usePrefs, useLangStore, useCopilot, useRelay, useReplay] as const
