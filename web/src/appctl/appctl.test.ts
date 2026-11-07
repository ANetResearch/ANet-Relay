import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/shell/notify', () => ({ notify: vi.fn(), notifyError: vi.fn() }))

import { api, RelayApiError, parseMessage } from '@/api'
import { useLangStore } from '@/i18n'
import { useCopilot } from '@/copilot/store'
import { useInspectorTab } from '@/inspector/tabs'
import { useRecordsCtl } from '@/records/ctl'
import { useReplay } from '@/records/replayState'
import { usePrefs } from '@/settings/prefs'
import { notify } from '@/shell/notify'
import { useStage } from '@/stage/store'
import { useRelay } from '@/store/relay'
import { useUi } from '@/store/ui'
import { handleUiCommand, noticeText, runUiAction } from './actions'
import { useAppNotices } from './notices'
import { UiReporter, REPORT_MIN_MS } from './reporter'
import { collectUiState } from './state'
import { APPCTL_DICTS } from './strings'
import { UI_ACTION_NAMES, type UiState } from './types'

const fleet = (ids: string[]) =>
  ({ t: 1, vehicles: ids.map((id) => ({ id, label: id.toUpperCase(), kind: 'sim', possession: null })), alerts: [] }) as never

beforeEach(() => {
  useUi.setState({ workspace: 'fly', selectedId: null, inspectorCollapsed: true, inspectorSheetOpen: false, commandOpen: false, alertCenterOpen: false, padsOpen: false })
  useUi.getState().closeSettings()
  useUi.getState().closeConnect()
  useStage.setState({ view: 'overview' })
  useInspectorTab.setState({ tab: 'overview' })
  useLangStore.setState({ lang: 'en' })
  useRelay.setState({ fleet: fleet(['sim-1', 'sim-2']), inputs: { gate: 'open', pads: [{ index: 1, id: 'pad', model: 'dualsense', connected: true, battery: null, vehicle: null }] } })
  useAppNotices.setState({ items: [], seq: 0 })
  useReplay.setState({ active: false, recordId: null, playing: false, t: null, frame: null })
  useRecordsCtl.setState({ open: null, replay: null })
  useCopilot.setState({ open: false })
  vi.spyOn(api, 'putSettings').mockResolvedValue({} as never)
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('interface actions (04 §3.2)', () => {
  it('rejects unknown actions with a stable code', async () => {
    const r = await runUiAction('self_destruct', {})
    expect(r).toMatchObject({ ok: false, error: 'unknown_action' })
    const proto = await runUiAction('constructor', {})
    expect(proto).toMatchObject({ ok: false, error: 'unknown_action' })
  })

  it('covers every action of the catalogue', async () => {
    for (const name of UI_ACTION_NAMES) {
      const r = await runUiAction(name, {})
      if (!r.ok) expect(r.error).not.toBe('unknown_action')
    }
  })

  it('get_state returns the UiState', async () => {
    useUi.getState().select('sim-2')
    const r = await runUiAction('get_state')
    expect(r.ok).toBe(true)
    if (r.ok) expect(r.data).toMatchObject({ lang: 'en', workspace: 'fly', selected: 'sim-2', view: 'overview', inspector: { open: false, tab: 'overview' }, dialog: null, copilot_open: false, record: null })
  })

  it('set_language switches the language store', async () => {
    expect(await runUiAction('set_language', { lang: 'zh' })).toEqual({ ok: true, data: { lang: 'zh' } })
    expect(useLangStore.getState().lang).toBe('zh')
    expect(await runUiAction('set_language', { lang: 'fr' })).toMatchObject({ ok: false, error: 'invalid_args' })
    expect(useLangStore.getState().lang).toBe('zh')
  })

  it('set_workspace and select_vehicle change the ui store', async () => {
    await runUiAction('set_workspace', { workspace: 'records' })
    expect(useUi.getState().workspace).toBe('records')
    expect(await runUiAction('set_workspace', { workspace: 'bridge' })).toMatchObject({ ok: false, error: 'invalid_args' })
    expect(await runUiAction('select_vehicle', { vehicle: 'sim-1' })).toEqual({ ok: true, data: { selected: 'sim-1' } })
    expect(useUi.getState().selectedId).toBe('sim-1')
    expect(await runUiAction('select_vehicle', { vehicle: 'nope' })).toMatchObject({ ok: false, error: 'vehicle_not_found' })
    expect(useUi.getState().selectedId).toBe('sim-1')
    await runUiAction('select_vehicle', { vehicle: null })
    expect(useUi.getState().selectedId).toBeNull()
  })

  it('set_view changes the stage view; chase needs a selection', async () => {
    expect(await runUiAction('set_view', { view: 'top' })).toEqual({ ok: true, data: { view: 'top' } })
    expect(useStage.getState().view).toBe('top')
    expect(await runUiAction('set_view', { view: 'chase' })).toMatchObject({ ok: false, error: 'view_unavailable' })
    useUi.getState().select('sim-1')
    await runUiAction('set_view', { view: 'chase' })
    expect(useStage.getState().view).toBe('chase')
  })

  it('opens and closes the inspector on a tab', async () => {
    const r = await runUiAction('open_inspector', { tab: 'telemetry' })
    expect(r.ok).toBe(true)
    expect(useUi.getState().inspectorCollapsed).toBe(false)
    expect(useInspectorTab.getState().tab).toBe('telemetry')
    expect(await runUiAction('open_inspector', { tab: 'bogus' })).toMatchObject({ ok: false, error: 'invalid_args' })
    await runUiAction('close_inspector')
    expect(useUi.getState().inspectorCollapsed).toBe(true)
  })

  it('opens settings and connect dialogs, and close_dialogs closes them', async () => {
    await runUiAction('open_settings', { section: 'copilot' })
    expect(useUi.getState().settings).toEqual({ open: true, section: 'copilot' })
    expect(collectUiState().dialog).toBe('settings')
    expect(collectUiState().settings_section).toBe('copilot')
    await runUiAction('open_connect', { tab: 'bridge' })
    expect(useUi.getState().connect).toEqual({ open: true, tab: 'bridge' })
    expect(await runUiAction('open_settings', { section: 'nope' })).toMatchObject({ ok: false, error: 'invalid_args' })
    const r = await runUiAction('close_dialogs')
    expect(r).toEqual({ ok: true, data: { closed: true } })
    expect(useUi.getState().settings.open).toBe(false)
    expect(useUi.getState().connect.open).toBe(false)
    expect(collectUiState().dialog).toBeNull()
  })

  it('set_pref validates keys and ranges', async () => {
    expect((await runUiAction('set_pref', { key: 'units', value: 'ft' })).ok).toBe(true)
    expect(usePrefs.getState().units).toBe('ft')
    expect((await runUiAction('set_pref', { key: 'pad_expo', value: 0.5 })).ok).toBe(true)
    expect(usePrefs.getState().padExpo).toBe(0.5)
    expect((await runUiAction('set_pref', { key: 'chaseOnCourse', value: false })).ok).toBe(true)
    expect(usePrefs.getState().chaseOnCourse).toBe(false)
    expect(await runUiAction('set_pref', { key: 'padDeadzone', value: 0.9 })).toMatchObject({ ok: false, error: 'invalid_args' })
    expect(await runUiAction('set_pref', { key: 'units', value: 'yd' })).toMatchObject({ ok: false, error: 'invalid_args' })
    expect(await runUiAction('set_pref', { key: 'theme', value: 'x' })).toMatchObject({ ok: false, error: 'invalid_args' })
  })

  it('notify shows a notice', async () => {
    expect((await runUiAction('notify', { text: 'Land now', level: 'warn' })).ok).toBe(true)
    expect(notify).toHaveBeenCalledWith('Land now', { level: 'warn' })
    expect(await runUiAction('notify', { text: '' })).toMatchObject({ ok: false, error: 'invalid_args' })
  })

  it('bind_gamepad possesses with the pad; unbind releases', async () => {
    const possess = vi.spyOn(api, 'possess').mockResolvedValue({} as never)
    const release = vi.spyOn(api, 'release').mockResolvedValue({} as never)
    expect(await runUiAction('bind_gamepad', { index: 1, vehicle: 'sim-1' })).toEqual({ ok: true, data: { index: 1, vehicle: 'sim-1' } })
    expect(possess).toHaveBeenCalledWith('sim-1', { input: 'gamepad', pad: 1 })
    expect(await runUiAction('bind_gamepad', { index: 3, vehicle: 'sim-1' })).toMatchObject({ ok: false, error: 'gamepad_not_found' })
    useRelay.setState({ inputs: { gate: 'open', pads: [{ index: 1, id: 'pad', model: 'dualsense', connected: true, battery: null, vehicle: 'sim-1' }] } })
    expect(await runUiAction('unbind_gamepad', { index: 1 })).toEqual({ ok: true, data: { index: 1, vehicle: 'sim-1', released: true } })
    expect(release).toHaveBeenCalledWith('sim-1')
    const state = collectUiState()
    expect(state.gamepads).toEqual([{ index: 1, id: 'pad', name: 'DualSense', mapping: 'dualsense', connected: true, vehicle: 'sim-1', input: 'gamepad' }])
  })

  it('bind_gamepad defaults to the selected aircraft', async () => {
    const possess = vi.spyOn(api, 'possess').mockResolvedValue({} as never)
    expect(await runUiAction('bind_gamepad', { index: 1 })).toMatchObject({ ok: false, error: 'invalid_args' })
    useUi.getState().select('sim-2')
    await runUiAction('bind_gamepad', { index: 1 })
    expect(possess).toHaveBeenCalledWith('sim-2', { input: 'gamepad', pad: 1 })
  })

  it('possess and release go through the normal REST flow and keep backend error codes', async () => {
    const possess = vi.spyOn(api, 'possess').mockResolvedValue({} as never)
    vi.spyOn(api, 'release').mockRejectedValue(new RelayApiError(409, 'not_possessed', 'Not possessed', '未被附身'))
    expect((await runUiAction('possess', { vehicle: 'sim-1' })).ok).toBe(true)
    expect(possess).toHaveBeenCalledWith('sim-1', { input: 'keyboard' })
    expect((await runUiAction('possess', { vehicle: 'sim-1', input: 'gamepad', pad: 1 })).ok).toBe(true)
    expect(possess).toHaveBeenLastCalledWith('sim-1', { input: 'gamepad', pad: 1 })
    expect(await runUiAction('release', { vehicle: 'sim-1' })).toMatchObject({ ok: false, error: 'not_possessed' })
  })

  it('open_record opens the Records workspace on the record; replay needs an open record', async () => {
    vi.spyOn(api, 'historySession').mockResolvedValue({} as never)
    expect(await runUiAction('replay', { action: 'play' })).toMatchObject({ ok: false, error: 'no_record_open' })
    // the replay viewport reports itself once it has loaded the record
    const timer = setTimeout(() => useReplay.setState({ active: true, recordId: 7, playing: false, t: 100 }), 30)
    const r = await runUiAction('open_record', { session_id: 7 })
    clearTimeout(timer)
    expect(r).toEqual({ ok: true, data: { session_id: 7, shown: true } })
    expect(useUi.getState().workspace).toBe('records')
    const p = await runUiAction('replay', { action: 'seek', t: 12 })
    expect(p.ok).toBe(true)
    expect(useRecordsCtl.getState().replay?.cmd).toEqual({ action: 'seek', t: 12 })
    await runUiAction('replay', { action: 'play' })
    expect(useRecordsCtl.getState().replay?.cmd).toEqual({ action: 'play' })
    expect(collectUiState().record).toEqual({ session_id: 7, playing: false, t: 100 })
    expect(await runUiAction('replay', { action: 'seek' })).toMatchObject({ ok: false, error: 'invalid_args' })
  })

  it('open_record reports a missing record', async () => {
    vi.spyOn(api, 'historySession').mockRejectedValue(new RelayApiError(404, 'not_found', 'No such record', '没有该记录'))
    expect(await runUiAction('open_record', { session_id: 99 })).toMatchObject({ ok: false, error: 'record_not_found' })
    expect(useUi.getState().workspace).toBe('fly')
  })

  it('toggle_copilot uses the shell bridge when present, the store otherwise', async () => {
    expect(await runUiAction('toggle_copilot', { open: true })).toEqual({ ok: true, data: { open: true } })
    expect(useCopilot.getState().open).toBe(true)
    const win = vi.fn().mockResolvedValue({ visible: false })
    vi.stubGlobal('window', { relay: { copilot: { window: win } }, innerWidth: 1440, innerHeight: 900 })
    expect(await runUiAction('toggle_copilot', { open: false })).toEqual({ ok: true, data: { open: false } })
    expect(win).toHaveBeenCalledWith('hide')
    await runUiAction('toggle_copilot', {})
    expect(win).toHaveBeenLastCalledWith('toggle')
  })

  it('window and screenshot go through the shell and are unavailable in a browser', async () => {
    expect(await runUiAction('window', { action: 'maximize' })).toMatchObject({ ok: false, error: 'unavailable' })
    expect(await runUiAction('screenshot', {})).toMatchObject({ ok: false, error: 'unavailable' })
    const state = { focused: true, minimized: false, maximized: true, fullscreen: false }
    const app = { window: vi.fn().mockResolvedValue(state), screenshot: vi.fn().mockResolvedValue({ png_base64: 'AAAA', width: 800, height: 500 }) }
    vi.stubGlobal('window', { relay: { app }, innerWidth: 1440, innerHeight: 900 })
    expect(await runUiAction('window', { action: 'maximize' })).toEqual({ ok: true, data: { action: 'maximize', state } })
    expect(await runUiAction('window', { action: 'close' })).toMatchObject({ ok: false, error: 'invalid_args' })
    expect(await runUiAction('screenshot', { max_width: 800 })).toEqual({ ok: true, data: { png_base64: 'AAAA', width: 800, height: 500 } })
    expect(app.screenshot).toHaveBeenCalledWith(800)
    await runUiAction('screenshot', {})
    expect(app.screenshot).toHaveBeenLastCalledWith(1280)
    expect(await runUiAction('screenshot', { max_width: 5000 })).toMatchObject({ ok: false, error: 'invalid_args' })
  })
})

describe('ui_command handling', () => {
  it('replies with ui_result and notifies for agents only', async () => {
    const reply = vi.fn()
    await handleUiCommand({ id: 'c1', action: 'set_language', args: { lang: 'zh' }, by: 'mcp-app', operator: false }, reply)
    expect(reply).toHaveBeenCalledWith({ type: 'ui_result', id: 'c1', ok: true, data: { lang: 'zh' } })
    expect(useAppNotices.getState().items.map((n) => n.text)).toEqual(['mcp-app 将语言切换为中文'])
    useAppNotices.setState({ items: [] })
    await handleUiCommand({ id: 'c2', action: 'set_language', args: { lang: 'en' }, by: 'operator', operator: true }, reply)
    expect(useAppNotices.getState().items).toEqual([])
    // a session that calls itself "operator" is still announced
    await handleUiCommand({ id: 'c2b', action: 'set_language', args: { lang: 'zh' }, by: 'operator', operator: false }, reply)
    expect(useAppNotices.getState().items).toHaveLength(1)
    useAppNotices.setState({ items: [] })
  })

  it('reports failures with the error code and a message', async () => {
    const reply = vi.fn()
    await handleUiCommand({ id: 'c3', action: 'nope', args: {}, by: 'copilot', operator: false }, reply)
    expect(reply).toHaveBeenCalledWith(expect.objectContaining({ type: 'ui_result', id: 'c3', ok: false, error: 'unknown_action' }))
    await handleUiCommand({ id: 'c4', action: 'select_vehicle', args: { vehicle: 'ghost' }, by: 'copilot', operator: false }, reply)
    expect(reply).toHaveBeenLastCalledWith(expect.objectContaining({ ok: false, error: 'vehicle_not_found', data: { message: expect.any(String) } }))
    expect(useAppNotices.getState().items.at(-1)?.level).toBe('warn')
  })

  it('writes notices in English and Chinese', () => {
    expect(noticeText('set_language', {}, 'copilot', { lang: 'zh' })).toBe('copilot switched the language to Chinese')
    useLangStore.setState({ lang: 'zh' })
    expect(noticeText('select_vehicle', {}, 'copilot', { selected: 'sim-1' })).toBe('copilot 选中了 SIM-1')
    expect(noticeText('get_state', {}, 'copilot', {})).toBeNull()
  })

  it('parses ui_command messages from the stream', () => {
    expect(parseMessage(JSON.stringify({ type: 'ui_command', id: 'u', action: 'get_state', args: { a: 1 }, by: 'x' }))).toEqual({
      type: 'ui_command',
      command: { id: 'u', action: 'get_state', args: { a: 1 }, by: 'x', operator: false },
    })
    expect(parseMessage(JSON.stringify({ type: 'ui_command', action: 'get_state' }))).toEqual({ type: 'other' })
  })
})

describe('ui_state reporter', () => {
  const state = (): UiState => collectUiState()

  it('sends changes at most every 200 ms and repeats unchanged state only as a heartbeat', async () => {
    vi.useFakeTimers()
    try {
      let clock = 1000
      const send = vi.fn().mockReturnValue(true)
      const r = new UiReporter({ send, collect: state, now: () => clock })
      expect(r.flush(false)).toBe(true)
      expect(send).toHaveBeenCalledTimes(1)
      expect(send.mock.calls[0][0]).toMatchObject({ type: 'ui_state', data: { lang: 'en', workspace: 'fly' } })
      expect(r.flush(false)).toBe(false)
      expect(r.flush(true)).toBe(true)
      useUi.getState().setWorkspace('agents')
      clock += 50
      r.poke()
      r.poke()
      vi.advanceTimersByTime(REPORT_MIN_MS - 51)
      expect(send).toHaveBeenCalledTimes(2)
      vi.advanceTimersByTime(60)
      expect(send).toHaveBeenCalledTimes(3)
      expect(send.mock.calls[2][0].data.workspace).toBe('agents')
      r.stop()
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps retrying while the socket is down', () => {
    const send = vi.fn().mockReturnValueOnce(false).mockReturnValue(true)
    const r = new UiReporter({ send, collect: state, now: () => 0 })
    expect(r.flush(false)).toBe(false)
    expect(r.flush(false)).toBe(true)
  })
})

describe('copy', () => {
  it('has identical keys in both languages', () => {
    expect(Object.keys(APPCTL_DICTS.zh).sort()).toEqual(Object.keys(APPCTL_DICTS.en).sort())
  })
})
