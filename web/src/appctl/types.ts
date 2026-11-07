// Renderer side of the application control channel (04 §3.1). The generated API types are regenerated after the
// backend slices merge; until then these local definitions are the renderer's view of the same messages.
import type { UiCommandMessage } from '@/api'

export type UiLang = 'en' | 'zh'
export type UiWorkspace = 'fly' | 'agents' | 'records'
export type UiView = 'overview' | 'chase' | 'top'
/** The one dialog or overlay in front, or null. */
export type UiDialog = 'settings' | 'connect' | 'palette' | 'alerts' | 'pads' | 'inspector_sheet' | null

export interface UiGamepad {
  /** 1..4 in connection order */
  index: number
  id: string
  name: string
  /** layout the input engine recognised: dualsense | standard | unknown */
  mapping: string
  connected: boolean
  vehicle: string | null
  input: 'gamepad'
}

export interface UiPossession {
  vehicle: string
  input: 'keyboard' | 'screen' | 'gamepad'
  pad: number | null
}

export interface UiState {
  lang: UiLang
  workspace: UiWorkspace
  selected: string | null
  view: UiView
  inspector: { open: boolean; tab: string }
  dialog: UiDialog
  settings_section: string
  copilot_open: boolean
  prefs: { units: string; startView: string; chaseOnCourse: boolean; padDeadzone: number; padExpo: number }
  gamepads: UiGamepad[]
  possession: UiPossession[]
  window: { focused: boolean; width: number; height: number; fullscreen: boolean }
  record: { session_id: number; playing: boolean; t: number | null } | null
  version: string
}

export type UiCommand = UiCommandMessage

export interface UiStateMessage {
  type: 'ui_state'
  data: UiState
}

export interface UiResultMessage {
  type: 'ui_result'
  id: string
  ok: boolean
  error?: string
  data?: Record<string, unknown>
}

/** Outcome of one interface action. Errors carry a stable code and an English hint for the agent. */
export type UiActionResult = { ok: true; data: Record<string, unknown> } | { ok: false; error: string; message: string }

export const UI_ACTION_NAMES = [
  'get_state',
  'set_language',
  'set_workspace',
  'select_vehicle',
  'set_view',
  'open_inspector',
  'close_inspector',
  'open_settings',
  'open_connect',
  'close_dialogs',
  'set_pref',
  'notify',
  'bind_gamepad',
  'unbind_gamepad',
  'possess',
  'release',
  'open_record',
  'replay',
  'toggle_copilot',
  'window',
  'screenshot',
] as const

export type UiActionName = (typeof UI_ACTION_NAMES)[number]
