// Interface actions of the application control channel (04 §3.2). Every action runs through the same stores and
// REST calls the operator's own clicks use, so an agent and a person see identical behaviour. The executor never
// throws: a failure is a result with a stable error code (unknown_action, invalid_args, vehicle_not_found, ...).
import { api, RelayApiError, sendJson, type UiCommandMessage } from '@/api'
import { setLang, t, useLangStore } from '@/i18n'
import { useCopilot } from '@/copilot/store'
import { INSPECTOR_TABS, useInspectorTab, type InspectorTab } from '@/inspector/tabs'
import { useRecordsCtl } from '@/records/ctl'
import { useReplay } from '@/records/replayState'
import { DEADZONE_RANGE, EXPO_RANGE, setPrefs, usePrefs, type Prefs } from '@/settings/prefs'
import { notify } from '@/shell/notify'
import { useStage } from '@/stage/store'
import { useRelay } from '@/store/relay'
import { useUi, type ConnectTab, type SettingsSection, type Workspace } from '@/store/ui'
import { useAppNotices } from './noticeStore'
import { collectGamepads, collectUiState } from './state'
import { apt } from './strings'
import { UI_ACTION_NAMES, type UiActionName, type UiActionResult, type UiResultMessage } from './types'

type Args = Record<string, unknown>
type Data = Record<string, unknown>

/** A failure with a stable code; thrown inside handlers, turned into a result by runUiAction. */
export class ActionError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message)
  }
}

const fail = (code: string, message: string): never => {
  throw new ActionError(code, message)
}

const WORKSPACES: readonly Workspace[] = ['fly', 'agents', 'records']
const VIEWS = ['overview', 'chase', 'top'] as const
const SETTINGS_SECTIONS: readonly SettingsSection[] = ['general', 'safety', 'controllers', 'copilot', 'network', 'data', 'about']
const CONNECT_TABS: readonly ConnectTab[] = ['scan', 'address', 'bridge']
const WINDOW_ACTIONS = ['focus', 'minimize', 'maximize', 'restore', 'fullscreen'] as const
const INPUTS = ['keyboard', 'screen', 'gamepad'] as const
const SCREENSHOT_DEFAULT = 1280
const SCREENSHOT_MIN = 160
const SCREENSHOT_MAX = 1920
const OPEN_RECORD_WAIT_MS = 4000

function oneOf<T extends string>(args: Args, key: string, allowed: readonly T[], fallback?: T): T {
  const v = args[key]
  if (v === undefined || v === null) {
    if (fallback !== undefined) return fallback
    return fail('invalid_args', `${key} is required: one of ${allowed.join(', ')}`)
  }
  if (typeof v !== 'string' || !(allowed as readonly string[]).includes(v)) return fail('invalid_args', `${key} must be one of ${allowed.join(', ')}`)
  return v as T
}

function int(args: Args, key: string, min: number, max: number, required = true): number | undefined {
  const v = args[key]
  if (v === undefined || v === null) return required ? fail('invalid_args', `${key} is required (${min}..${max})`) : undefined
  if (typeof v !== 'number' || !Number.isInteger(v) || v < min || v > max) return fail('invalid_args', `${key} must be an integer ${min}..${max}`)
  return v
}

function vehicleId(args: Args, key = 'vehicle'): string {
  const v = args[key]
  if (typeof v !== 'string' || !v) return fail('invalid_args', `${key} is required: an aircraft id from the fleet`)
  const known = useRelay.getState().fleet?.vehicles
  if (known && !known.some((x) => x.id === v)) return fail('vehicle_not_found', `No aircraft with id ${v}`)
  return v
}

/** Maps a REST failure to an action failure that keeps the backend's own code. */
async function viaApi<T>(p: Promise<T>): Promise<T> {
  try {
    return await p
  } catch (e) {
    if (e instanceof RelayApiError) return fail(e.code, e.messageEn)
    return fail('request_failed', String(e instanceof Error ? e.message : e))
  }
}

const bridge = () => (typeof window !== 'undefined' ? window.relay : undefined)

function padFor(index: number) {
  const pad = collectGamepads().find((p) => p.index === index)
  if (!pad || !pad.connected) return fail('gamepad_not_found', `Controller ${index} is not connected`)
  return pad
}

const PREF_KEYS: Record<string, keyof Prefs> = {
  units: 'units',
  startView: 'startView',
  start_view: 'startView',
  chaseOnCourse: 'chaseOnCourse',
  chase_on_course: 'chaseOnCourse',
  padDeadzone: 'padDeadzone',
  pad_deadzone: 'padDeadzone',
  padExpo: 'padExpo',
  pad_expo: 'padExpo',
}

function setPref(args: Args): Data {
  const key = typeof args.key === 'string' ? PREF_KEYS[args.key] : undefined
  if (!key) return fail('invalid_args', `key must be one of ${[...new Set(Object.values(PREF_KEYS))].join(', ')}`)
  const v = args.value
  let value: Prefs[typeof key]
  if (key === 'units') {
    if (v !== 'm' && v !== 'ft') return fail('invalid_args', 'units must be "m" or "ft"')
    value = v
  } else if (key === 'startView') {
    if (v !== 'overview' && v !== 'chase' && v !== 'top') return fail('invalid_args', 'startView must be overview, chase or top')
    value = v
  } else if (key === 'chaseOnCourse') {
    if (typeof v !== 'boolean') return fail('invalid_args', 'chaseOnCourse must be true or false')
    value = v
  } else {
    const range = key === 'padDeadzone' ? DEADZONE_RANGE : EXPO_RANGE
    if (typeof v !== 'number' || !Number.isFinite(v) || v < range[0] || v > range[1]) return fail('invalid_args', `${key} must be a number ${range[0]}..${range[1]}`)
    value = v
  }
  setPrefs({ [key]: value } as Partial<Prefs>)
  return { key, prefs: { ...usePrefs.getState() } }
}

async function waitFor(pred: () => boolean, ms: number): Promise<boolean> {
  const end = Date.now() + ms
  while (Date.now() < end) {
    if (pred()) return true
    await new Promise((r) => setTimeout(r, 50))
  }
  return pred()
}

async function openRecord(args: Args): Promise<Data> {
  const id = int(args, 'session_id', 1, Number.MAX_SAFE_INTEGER) as number
  try {
    await api.historySession(id)
  } catch (e) {
    if (e instanceof RelayApiError && e.status === 404) return fail('record_not_found', `No record with session_id ${id}`)
    return fail('request_failed', String(e instanceof Error ? e.message : e))
  }
  useUi.getState().setWorkspace('records')
  useRecordsCtl.getState().openRecord(id)
  const shown = await waitFor(() => useReplay.getState().recordId === id && useReplay.getState().active, OPEN_RECORD_WAIT_MS)
  // the workspace has taken the request; a later mount must not open the record again
  useRecordsCtl.setState({ open: null })
  return { session_id: id, shown }
}

async function replay(args: Args): Promise<Data> {
  const action = oneOf(args, 'action', ['play', 'pause', 'seek'] as const)
  const rp = useReplay.getState()
  if (!rp.active || rp.recordId === null) return fail('no_record_open', 'Open a record first with open_record')
  if (action === 'seek') {
    const tt = args.t
    if (typeof tt !== 'number' || !Number.isFinite(tt) || tt < 0) return fail('invalid_args', 't is required: seconds from the start of the record, or unix seconds')
    useRecordsCtl.getState().replayCommand({ action: 'seek', t: tt })
  } else useRecordsCtl.getState().replayCommand({ action })
  await new Promise((r) => setTimeout(r, 120))
  const now = useReplay.getState()
  return { session_id: now.recordId, playing: now.playing, t: now.t }
}

async function toggleCopilot(args: Args): Promise<Data> {
  const open = args.open
  if (open !== undefined && open !== null && typeof open !== 'boolean') return fail('invalid_args', 'open must be true or false')
  const b = bridge()?.copilot
  if (b?.window) {
    const s = await b.window(open === undefined || open === null ? 'toggle' : open ? 'show' : 'hide')
    return { open: s.visible }
  }
  useCopilot.getState().setOpen(typeof open === 'boolean' ? open : !useCopilot.getState().open)
  return { open: useCopilot.getState().open }
}

async function windowAction(args: Args): Promise<Data> {
  const action = oneOf(args, 'action', WINDOW_ACTIONS)
  const b = bridge()?.app
  if (!b) return fail('unavailable', 'Window control needs the desktop app')
  const state = await b.window(action)
  return { action, state: { ...state } }
}

async function screenshot(args: Args): Promise<Data> {
  const w = args.max_width
  if (w !== undefined && w !== null && (typeof w !== 'number' || !Number.isFinite(w) || w < SCREENSHOT_MIN || w > SCREENSHOT_MAX)) {
    return fail('invalid_args', `max_width must be ${SCREENSHOT_MIN}..${SCREENSHOT_MAX}`)
  }
  const b = bridge()?.app
  if (!b) return fail('unavailable', 'Screenshots need the desktop app')
  const shot = await b.screenshot(typeof w === 'number' ? Math.round(w) : SCREENSHOT_DEFAULT)
  if (!shot) return fail('capture_failed', 'The window could not be captured (hidden or minimised)')
  return { png_base64: shot.png_base64, width: shot.width, height: shot.height }
}

function openInspector(args: Args): Data {
  const ui = useUi.getState()
  const tab = args.tab === undefined || args.tab === null ? undefined : oneOf(args, 'tab', INSPECTOR_TABS)
  if (tab) useInspectorTab.getState().setTab(tab as InspectorTab)
  ui.setWorkspace('fly')
  if (typeof window !== 'undefined' && window.innerWidth < 1200) ui.setInspectorSheet(true)
  else ui.toggleInspector(false)
  return { tab: useInspectorTab.getState().tab, selected: useUi.getState().selectedId }
}

function closeDialogs(): Data {
  const ui = useUi.getState()
  const open = ui.commandOpen || ui.settings.open || ui.connect.open || ui.alertCenterOpen || ui.padsOpen || ui.inspectorSheetOpen
  ui.setCommandOpen(false)
  ui.closeSettings()
  ui.closeConnect()
  ui.setAlertCenterOpen(false)
  ui.setPadsOpen(false)
  ui.setInspectorSheet(false)
  return { closed: open }
}

const HANDLERS: Record<UiActionName, (args: Args) => Promise<Data> | Data> = {
  get_state: () => collectUiState() as unknown as Data,
  set_language: (a) => {
    const lang = oneOf(a, 'lang', ['en', 'zh'] as const)
    setLang(lang)
    return { lang: useLangStore.getState().lang }
  },
  set_workspace: (a) => {
    const workspace = oneOf(a, 'workspace', WORKSPACES)
    useUi.getState().setWorkspace(workspace)
    return { workspace }
  },
  select_vehicle: (a) => {
    if (a.vehicle === undefined || a.vehicle === null) {
      useUi.getState().select(null)
      return { selected: null }
    }
    const id = vehicleId(a)
    useUi.getState().select(id)
    return { selected: id }
  },
  set_view: (a) => {
    const view = oneOf(a, 'view', VIEWS)
    if (view === 'chase' && !useUi.getState().selectedId) return fail('view_unavailable', 'The chase view needs a selected aircraft')
    useStage.getState().setView(view)
    return { view: useStage.getState().view }
  },
  open_inspector: openInspector,
  close_inspector: () => {
    const ui = useUi.getState()
    ui.toggleInspector(true)
    ui.setInspectorSheet(false)
    return { open: false }
  },
  open_settings: (a) => {
    const section = oneOf(a, 'section', SETTINGS_SECTIONS, 'general')
    useUi.getState().openSettings(section)
    return { section }
  },
  open_connect: (a) => {
    const tab = oneOf(a, 'tab', CONNECT_TABS, 'scan')
    useUi.getState().openConnect(tab)
    return { tab }
  },
  close_dialogs: closeDialogs,
  set_pref: setPref,
  notify: (a) => {
    const text = typeof a.text === 'string' ? a.text.trim() : ''
    if (!text || text.length > 500) return fail('invalid_args', 'text is required (1..500 characters)')
    const level = oneOf(a, 'level', ['info', 'warn'] as const, 'info')
    notify(text, { level })
    return { shown: true }
  },
  bind_gamepad: async (a) => {
    const index = int(a, 'index', 1, 4) as number
    padFor(index)
    const vehicle = a.vehicle === undefined || a.vehicle === null ? useUi.getState().selectedId : vehicleId(a)
    if (!vehicle) return fail('invalid_args', 'vehicle is required when no aircraft is selected')
    await viaApi(api.possess(vehicle, { input: 'gamepad', pad: index }))
    return { index, vehicle }
  },
  unbind_gamepad: async (a) => {
    const index = int(a, 'index', 1, 4) as number
    const pad = collectGamepads().find((p) => p.index === index)
    if (!pad) return fail('gamepad_not_found', `Controller ${index} is not known`)
    const vehicle = a.vehicle === undefined || a.vehicle === null ? pad.vehicle : vehicleId(a)
    if (!vehicle) return { index, vehicle: null, released: false }
    await viaApi(api.release(vehicle))
    return { index, vehicle, released: true }
  },
  possess: async (a) => {
    const vehicle = vehicleId(a)
    const input = oneOf(a, 'input', INPUTS, 'keyboard')
    if (input === 'gamepad') {
      const pad = int(a, 'pad', 1, 4) as number
      padFor(pad)
      await viaApi(api.possess(vehicle, { input, pad }))
      return { vehicle, input, pad }
    }
    await viaApi(api.possess(vehicle, { input }))
    return { vehicle, input }
  },
  release: async (a) => {
    const vehicle = vehicleId(a)
    await viaApi(api.release(vehicle))
    return { vehicle }
  },
  open_record: openRecord,
  replay,
  toggle_copilot: toggleCopilot,
  window: windowAction,
  screenshot,
}

/** Runs one action. Never throws. */
export async function runUiAction(action: string, args: Args = {}): Promise<UiActionResult> {
  if (!Object.hasOwn(HANDLERS, action)) return { ok: false, error: 'unknown_action', message: `Unknown action ${action}. Known: ${UI_ACTION_NAMES.join(', ')}` }
  try {
    const data = await HANDLERS[action as UiActionName](args && typeof args === 'object' ? args : {})
    return { ok: true, data }
  } catch (e) {
    if (e instanceof ActionError) return { ok: false, error: e.code, message: e.message }
    return { ok: false, error: 'action_failed', message: String(e instanceof Error ? e.message : e) }
  }
}

/** The line shown for an agent's action, or null for reads. */
export function noticeText(action: string, args: Args, by: string, data: Data): string | null {
  const aircraft = (id: unknown) => (typeof id === 'string' ? (useRelay.getState().fleet?.vehicles.find((v) => v.id === id)?.label ?? id) : '')
  switch (action) {
    case 'get_state':
    case 'notify':
      return null
    case 'set_language':
      return apt('app.n.set_language', { by, lang: apt(data.lang === 'zh' ? 'app.lang.zh' : 'app.lang.en') })
    case 'set_workspace':
      return apt('app.n.set_workspace', { by, workspace: t(`ws.${String(data.workspace)}` as 'ws.fly') })
    case 'select_vehicle':
      return data.selected ? apt('app.n.select_vehicle', { by, aircraft: aircraft(data.selected) }) : apt('app.n.deselect_vehicle', { by })
    case 'set_view':
      return apt('app.n.set_view', { by, view: t(`stage.view.${String(data.view)}` as 'stage.view.top') })
    case 'open_inspector':
    case 'close_inspector':
    case 'open_settings':
    case 'open_connect':
    case 'close_dialogs':
    case 'open_record':
    case 'toggle_copilot':
    case 'replay':
    case 'screenshot':
      return apt(`app.n.${action}` as 'app.n.replay', { by, id: String(data.session_id ?? '') })
    case 'set_pref':
      return apt('app.n.set_pref', { by, key: String(data.key) })
    case 'bind_gamepad':
      return apt('app.n.bind_gamepad', { by, index: String(data.index), aircraft: aircraft(data.vehicle) })
    case 'unbind_gamepad':
      return data.released ? apt('app.n.unbind_gamepad', { by, index: String(data.index) }) : null
    case 'possess':
    case 'release':
      return apt(`app.n.${action}` as 'app.n.release', { by, aircraft: aircraft(data.vehicle) })
    case 'window':
      return apt('app.n.window', { by, action: String(args.action) })
    default:
      return null
  }
}

/** Runs a ui_command from the backend, shows the agent notice and replies with ui_result. */
export async function handleUiCommand(cmd: UiCommandMessage, reply: (m: UiResultMessage) => unknown = sendJson): Promise<void> {
  const res = await runUiAction(cmd.action, cmd.args)
  const by = cmd.by || 'agent'
  const announce = !cmd.operator
  if (res.ok) {
    if (announce) {
      const text = noticeText(cmd.action, cmd.args, by, res.data)
      if (text) useAppNotices.getState().push(text)
    }
    reply({ type: 'ui_result', id: cmd.id, ok: true, data: res.data })
  } else {
    if (announce && res.error !== 'unknown_action') useAppNotices.getState().push(apt('app.n.failed', { by, action: cmd.action }), 'warn')
    reply({ type: 'ui_result', id: cmd.id, ok: false, error: res.error, data: { message: res.message } })
  }
}
