// Keyboard layer (02 §3, 03 §12). One capture-phase keydown listener on window; feature modules add their keys
// with registerHotkey(). ⌘. / Ctrl+. (stop all) is matched before anything else and works inside every dialog,
// wizard and text field. Esc closes the top overlay (left to the overlay itself); with no overlay it stops the
// keyboard-possessed or selected aircraft; inside a text field it only leaves the field.
import type { MenuAction, MenuEvent } from '@/api'
import { setLang, t } from '@/i18n'
import { useUi, type SettingsSection } from '@/store/ui'
import { moveSelection, stopAll, stopFocused } from './actions'
import { notify } from './notify'

export interface Hotkey {
  /** e.g. "mod+k", "alt+mod+b", "1", "up", "p", "space"; mod = ⌘ on macOS, Ctrl elsewhere */
  combo: string
  handler(e: KeyboardEvent): void | false
  /** only while this workspace is shown */
  workspace?: 'fly' | 'agents' | 'records' | 'lab'
  /** also fire while a text field has focus (default false) */
  inText?: boolean
  /** also fire while a dialog, sheet, popover or menu is open (default false) */
  inOverlay?: boolean
  /** fire on auto-repeat (default false) */
  repeat?: boolean
}

const hotkeys = new Set<Hotkey>()

export function registerHotkey(h: Hotkey): () => void {
  hotkeys.add(h)
  return () => hotkeys.delete(h)
}

const isMac = typeof navigator !== 'undefined' && /mac/i.test(navigator.platform || navigator.userAgent)

const CODE_NAMES: Record<string, string> = {
  Period: '.', Comma: ',', Slash: '/', Space: 'space', Escape: 'escape', Enter: 'enter', NumpadEnter: 'enter',
  ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right', Backspace: 'backspace', Tab: 'tab',
}

/** Layout-independent combo of a keyboard event: letters and digits by physical key (Option on macOS changes e.key). */
export function comboOf(e: Pick<KeyboardEvent, 'code' | 'key' | 'metaKey' | 'ctrlKey' | 'altKey' | 'shiftKey'>, mac = isMac): string {
  let k: string
  if (/^Key[A-Z]$/.test(e.code)) k = e.code.slice(3).toLowerCase()
  else if (/^Digit\d$/.test(e.code)) k = e.code.slice(5)
  else if (CODE_NAMES[e.code]) k = CODE_NAMES[e.code]
  else k = e.key.length === 1 ? e.key.toLowerCase() : e.key.toLowerCase()
  const mod = mac ? e.metaKey : e.ctrlKey
  const parts: string[] = []
  if (e.altKey) parts.push('alt')
  if (mod) parts.push('mod')
  if (e.shiftKey && k.length > 1) parts.push('shift')
  parts.push(k)
  return parts.join('+')
}

const SETTINGS_SECTIONS: readonly SettingsSection[] = ['general', 'safety', 'controllers', 'copilot', 'network', 'data', 'about']

export const OVERLAY_SELECTOR = [
  'dialog-content', 'alert-dialog-content', 'sheet-content', 'popover-content', 'dropdown-menu-content',
  'context-menu-content', 'select-content', 'combobox-content', 'hover-card-content', 'menubar-content',
].map((s) => `[data-slot="${s}"]`).join(',')

/** Is an overlay open? Base UI keeps some popups (select) mounted under a [hidden] ancestor after they close. */
export function overlayOpen(): boolean {
  if (typeof document === 'undefined') return false
  for (const el of document.querySelectorAll(OVERLAY_SELECTOR)) {
    if (el.closest('[hidden]') || el.hasAttribute('data-closed') || el.hasAttribute('data-ending-style')) continue
    return true
  }
  return false
}

export function inTextField(
  el: Element | null = typeof document !== 'undefined' ? document.activeElement : null,
  ownKeys = true,
): boolean {
  if (!el || !(el instanceof HTMLElement)) return false
  if (el.isContentEditable) return true
  // an editor that handles its own keys (the timeline dock, 10 §2): flight and shell hotkeys step aside
  if (ownKeys && el.closest('[data-own-keys]')) return true
  const tag = el.tagName
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (tag === 'INPUT') {
    const type = (el as HTMLInputElement).type
    return !['checkbox', 'radio', 'button', 'range', 'submit', 'reset'].includes(type)
  }
  // a select trigger (role combobox on a button) is not a text field: flight keys must keep working after a mode pick
  return false
}

/**
 * Non-modal floating layers that Esc closes before it means "stop" (02 §3: Esc closes the topmost layer; with none
 * open it stops the selected aircraft). The latest registration is the topmost. A dismisser returns true when it
 * closed something.
 */
const dismissers: (() => boolean)[] = []
export function registerDismisser(fn: () => boolean): () => void {
  dismissers.push(fn)
  return () => {
    const i = dismissers.lastIndexOf(fn)
    if (i >= 0) dismissers.splice(i, 1)
  }
}

/** Is `combo` the stop-all chord? */
export const isStopAll = (combo: string) => combo === 'mod+.'

function onKeyDown(e: KeyboardEvent): void {
  const combo = comboOf(e)
  if (isStopAll(combo)) {
    e.preventDefault()
    e.stopPropagation()
    if (!e.repeat) stopAll()
    return
  }
  const text = inTextField()
  const overlay = overlayOpen()
  if (combo === 'escape') {
    if (overlay) return
    // Esc keeps its stop meaning inside an editor with its own keys; only a real text field takes it
    if (inTextField(undefined, false)) {
      ;(document.activeElement as HTMLElement | null)?.blur()
      return
    }
    for (let i = dismissers.length - 1; i >= 0; i--) {
      if (dismissers[i]()) {
        e.preventDefault()
        return
      }
    }
    if (!e.repeat) {
      e.preventDefault()
      stopFocused()
    }
    return
  }
  const ws = useUi.getState().workspace
  for (const h of hotkeys) {
    if (h.combo !== combo) continue
    if (h.workspace && h.workspace !== ws) continue
    if (text && !h.inText) continue
    if (overlay && !h.inOverlay) continue
    if (e.repeat && !h.repeat) continue
    if (h.handler(e) === false) continue
    e.preventDefault()
    return
  }
}

function builtins(): Hotkey[] {
  const ui = () => useUi.getState()
  return [
    { combo: 'mod+k', inText: true, inOverlay: true, handler: () => ui().setCommandOpen(!ui().commandOpen) },
    { combo: 'mod+,', inText: true, handler: () => ui().openSettings() },
    { combo: 'mod+1', inText: true, inOverlay: true, handler: () => setLang('zh') },
    { combo: 'mod+2', inText: true, inOverlay: true, handler: () => setLang('en') },
    { combo: 'alt+mod+b', handler: () => ui().toggleFleet() },
    { combo: 'alt+mod+i', handler: () => ui().toggleInspector() },
    { combo: 'alt+mod+t', handler: () => ui().toggleTimeline() },
    { combo: '1', handler: () => ui().setWorkspace('fly') },
    { combo: '2', handler: () => ui().setWorkspace('agents') },
    { combo: '3', handler: () => ui().setWorkspace('records') },
    // WP-3: Lab (06 §7) on 4 like the other workspaces, and on mod+4 from anywhere, the editor included
    { combo: '4', handler: () => ui().setWorkspace('lab') },
    { combo: 'mod+4', inText: true, handler: () => ui().setWorkspace('lab') },
    { combo: 'up', workspace: 'fly', repeat: true, handler: () => moveSelection(-1) },
    { combo: 'down', workspace: 'fly', repeat: true, handler: () => moveSelection(1) },
    {
      combo: 'enter',
      workspace: 'fly',
      handler: () => {
        const s = ui()
        if (!s.selectedId) return false
        if (window.innerWidth < 1200) s.setInspectorSheet(true)
        else s.toggleInspector(false)
      },
    },
  ]
}

/** Menu actions owned by feature modules (Fly > Possess is the input layer's P). */
export const menuHandlers: Partial<Record<MenuAction, () => void>> = {}

/** Menu actions forwarded by the Electron main process (accelerators fire there, not here). */
export function runMenuAction(e: MenuEvent): void {
  const ui = useUi.getState()
  const action = e?.action
  if (action === 'stop-all') {
    // the shell sends POST /stop-all itself so it works even when this page is busy; only confirm it here
    if (e.shellIssued) notify(t('toast.stopAllSent'))
    else stopAll()
  } else if (action === 'possess') menuHandlers.possess?.()
  else if (action === 'toggle-fleet') ui.toggleFleet()
  else if (action === 'toggle-inspector') ui.toggleInspector()
  else if (action === 'toggle-timeline') ui.toggleTimeline()
  else if (action === 'settings') {
    const section = SETTINGS_SECTIONS.find((s) => s === e.section)
    ui.openSettings(section)
  }
}

export function installKeyboard(): () => void {
  const offs = builtins().map(registerHotkey)
  window.addEventListener('keydown', onKeyDown, { capture: true })
  const offMenu = window.relay?.onMenu?.(runMenuAction)
  return () => {
    offs.forEach((f) => f())
    window.removeEventListener('keydown', onKeyDown, { capture: true })
    offMenu?.()
  }
}
