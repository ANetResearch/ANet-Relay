// The floating Copilot window (04 §6.1): the Copilot conversation filling a frameless, always-on-top Electron window.
// The header is the drag region (buttons opt out); closing only hides the window. The main window and every other
// application stay clickable: this is not a modal.
import * as React from 'react'
import { useLangStore } from '@/i18n'
import { Button } from '@/ui/components/ui/button'
import { Icon } from '@/ui/icons'
import { CopilotBody, setCopilotWindow, useCopilotEvents } from './CopilotPanel'
import { useCopilot } from './store'
import { useCPT } from './strings'
import './copilot.css'

const FLASH_MS = 1600
const LANG_KEY = 'relay.lang'

/** The main window owns the language; follow it through the shared storage of the app origin. */
function useFollowLanguage(): void {
  React.useEffect(() => {
    const on = (e: StorageEvent) => {
      if (e.key !== LANG_KEY) return
      if ((e.newValue === 'zh' || e.newValue === 'en') && e.newValue !== useLangStore.getState().lang) useLangStore.getState().setLang(e.newValue)
    }
    window.addEventListener('storage', on)
    return () => window.removeEventListener('storage', on)
  }, [])
}

/** One flash of the frame when an approval arrives (the shell also flashes the OS frame). */
function useFlash(ref: React.RefObject<HTMLDivElement | null>): void {
  const seq = useCopilot((s) => s.flash)
  React.useEffect(() => {
    const el = ref.current
    if (!seq || !el || typeof el.animate !== 'function') return
    const calm = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    const anim = el.animate(
      [{ borderColor: 'var(--l3-border)', boxShadow: 'inset 0 0 0 0 var(--g50)' }, { borderColor: 'var(--g50)', boxShadow: 'inset 0 0 0 1px var(--g50)' }, { borderColor: 'var(--l3-border)', boxShadow: 'inset 0 0 0 0 var(--g50)' }],
      { duration: FLASH_MS / 2, iterations: calm ? 1 : 2 },
    )
    return () => anim.cancel()
  }, [seq, ref])
}

export function CopilotWindow() {
  const t = useCPT()
  const model = useCopilot((s) => s.config?.model)
  const autoApprove = useCopilot((s) => s.config?.autoApprove === true)
  const busy = useCopilot((s) => s.busy)
  const frame = React.useRef<HTMLDivElement>(null)
  useFlash(frame)
  useCopilotEvents()
  useFollowLanguage()
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mac = /mac/i.test(navigator.platform || navigator.userAgent)
      if ((mac ? e.metaKey : e.ctrlKey) && e.code === 'KeyJ') {
        e.preventDefault()
        void setCopilotWindow('hide')
      }
    }
    window.addEventListener('keydown', onKey, { capture: true })
    return () => window.removeEventListener('keydown', onKey, { capture: true })
  }, [])
  return (
    <div ref={frame} className="cp-window" data-copilot-panel="" aria-label={t('cp.title')} role="region">
      <header className="drag flex h-10 shrink-0 items-center gap-2 border-b border-[var(--border)] pr-1.5 pl-3" data-copilot-drag="">
        <Icon icon="copilot" className={busy ? 'size-4 cp-pulse text-[var(--g200)]' : 'size-4 text-[var(--g200)]'} />
        <span className="text-[13px] font-[560] text-[var(--g50)]">{t('cp.title')}</span>
        {model ? <span className="num truncate text-[11px] text-[var(--g500)]">{model}</span> : null}
        <span className="flex-1" />
        {autoApprove ? (
          <button
            type="button"
            className="cp-auto-badge no-drag"
            title={t('cp.auto.off')}
            onClick={async () => {
              const b = window.relay?.copilot
              if (b) useCopilot.getState().setConfig(await b.saveConfig({ autoApprove: false }))
            }}
            data-copilot-auto=""
          >
            {t('cp.auto.badge')}
            <Icon icon="close" className="size-3" />
          </button>
        ) : null}
        <Button
          variant="ghost"
          size="icon-sm"
          title={t('cp.newChat')}
          aria-label={t('cp.newChat')}
          onClick={() => {
            useCopilot.getState().clear()
            void window.relay?.copilot?.reset()
          }}
        >
          <Icon icon="plus" />
        </Button>
        <Button variant="ghost" size="icon-sm" title={t('cp.settings')} aria-label={t('cp.settings')} onClick={() => void window.relay?.copilot?.openSettings()}>
          <Icon icon="settings" />
        </Button>
        <Button variant="ghost" size="icon-sm" title={t('cp.close')} aria-label={t('cp.close')} onClick={() => void setCopilotWindow('hide')} data-copilot-hide="">
          <Icon icon="close" />
        </Button>
      </header>
      <CopilotBody />
    </div>
  )
}
