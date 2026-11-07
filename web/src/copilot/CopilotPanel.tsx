// Copilot panel (03 §16): a docked conversation with the built-in agent. The agent and its API key live in the
// main process; this panel shows the transcript, tool progress and approval requests for real aircraft.
import * as React from 'react'
import { env, type CopilotEvent } from '@/api'
import { cn } from '@/lib/utils'
import { registerHotkey } from '@/shell/keyboard'
import { useVehicle } from '@/store/selectors'
import { useUi } from '@/store/ui'
import { Button } from '@/ui/components/ui/button'
import { Textarea } from '@/ui/components/ui/textarea'
import { Icon } from '@/ui/icons'
import { useCT, type ControlKey } from '@/control/strings'
import { useCopilot, type CopilotItem } from './store'
import { cpt, useCPT, type CopilotKey } from './strings'
import './copilot.css'

const MOD = typeof navigator !== 'undefined' && /mac/i.test(navigator.platform || navigator.userAgent) ? '⌘' : 'Ctrl+'

const bridge = () => (typeof window !== 'undefined' ? window.relay?.copilot : undefined)

/** Subscribe once to the main-process event stream and load the endpoint settings. */
export function useCopilotBridge(): void {
  React.useEffect(() => {
    const b = bridge()
    if (!b) return
    const apply = useCopilot.getState().apply
    const off = b.onEvent((e: CopilotEvent) => apply(e))
    b.getConfig().then((cfg) => useCopilot.getState().setConfig(cfg), () => {})
    b.getState().then((s) => useCopilot.setState({ busy: s.busy }), () => {})
    const offKey = registerHotkey({ combo: 'mod+j', inText: true, handler: () => useCopilot.getState().toggle() })
    return () => {
      off()
      offKey()
    }
  }, [])
}

export async function sendToCopilot(text: string): Promise<void> {
  const b = bridge()
  const trimmed = text.trim()
  if (!b || !trimmed) return
  const s = useCopilot.getState()
  s.pushUser(trimmed)
  const vehicle = useUi.getState().selectedId
  const res = await b.prompt(trimmed, vehicle)
  if (!res.ok) {
    const code = res.error ?? 'generic'
    const key = `cp.err.${code}` as CopilotKey
    s.pushNotice(['not_configured', 'backend_unavailable', 'busy'].includes(code) ? cpt(key) : cpt('cp.err.generic', { error: code }), 'error')
  }
}

/** Minimal, injection-free rendering of the model's Markdown: paragraphs, list lines, **bold** and `code`. */
function RichText({ text }: { text: string }) {
  const lines = text.replace(/\r/g, '').split('\n')
  return (
    <div className="cp-rich">
      {lines.map((line, i) => {
        const list = /^\s*(?:[-*]|\d+\.)\s+/.exec(line)
        const body = list ? line.slice(list[0].length) : line
        if (!body.trim()) return <div key={i} className="h-1.5" />
        const parts = body.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((p, j) =>
          p.startsWith('**') && p.endsWith('**') ? (
            <strong key={j}>{p.slice(2, -2)}</strong>
          ) : p.startsWith('`') && p.endsWith('`') ? (
            <code key={j}>{p.slice(1, -1)}</code>
          ) : (
            <React.Fragment key={j}>{p}</React.Fragment>
          ),
        )
        return list ? (
          <div key={i} className="flex gap-1.5 pl-1">
            <span className="text-[var(--g500)]">{/\d/.test(list[0]) ? list[0].trim() : '·'}</span>
            <span className="min-w-0">{parts}</span>
          </div>
        ) : (
          <p key={i}>{parts}</p>
        )
      })}
    </div>
  )
}

function stepText(step: Record<string, unknown>, ct: (k: ControlKey) => string): string {
  const action = String(step.action)
  const m = ct('plan.unit.m')
  const n = (k: string) => (typeof step[k] === 'number' ? (step[k] as number) : 0)
  const name = ['takeoff', 'hover', 'move', 'yaw', 'land'].includes(action) ? ct(`plan.action.${action}` as ControlKey) : action
  switch (action) {
    case 'takeoff':
      return `${name} ${n('alt') || 1} ${m}`
    case 'hover':
      return `${name} ${n('seconds')} ${ct('plan.unit.s')}`
    case 'move': {
      const parts = []
      if (n('forward')) parts.push(`${ct('plan.field.forward')} ${n('forward')} ${m}`)
      if (n('right')) parts.push(`${ct('plan.field.right')} ${n('right')} ${m}`)
      if (n('up')) parts.push(`${ct('plan.field.up')} ${n('up')} ${m}`)
      return `${name} ${parts.join(' · ')}`
    }
    case 'yaw':
      return `${name} ${n('deg')}${ct('plan.unit.deg')}`
    default:
      return name
  }
}

const TOOL_KEYS = new Set(
  ['relay_fleet', 'relay_vehicle', 'relay_takeoff', 'relay_hover', 'relay_move', 'relay_yaw', 'relay_land', 'relay_plan', 'relay_cancel', 'relay_release', 'relay_stop'].map((n) => `cp.tool.${n}`),
)

function ToolCard({ item }: { item: Extract<CopilotItem, { kind: 'tool' }> }) {
  const t = useCPT()
  const ct = useCT()
  const [open, setOpen] = React.useState(false)
  const key = `cp.tool.${item.tool}` as CopilotKey
  const label = TOOL_KEYS.has(key) ? t(key) : item.tool
  const vehicle = typeof item.args.vehicle === 'string' ? item.args.vehicle : null
  const steps = Array.isArray(item.args.steps) ? (item.args.steps as Record<string, unknown>[]) : null
  return (
    <div className="cp-tool" data-state={item.state} data-tool={item.tool}>
      <button type="button" className="flex w-full items-center gap-2 text-left" onClick={() => setOpen((o) => !o)}>
        <Icon icon={item.state === 'ok' ? 'check' : item.state === 'error' ? 'alert' : 'pending'} className={cn('size-3.5 flex-none', item.state === 'running' && 'cp-spin')} />
        <span className="font-[560] text-[var(--g100)]">{label}</span>
        {vehicle ? <span className="num text-[var(--g400)]">{vehicle}</span> : null}
        <span className="flex-1" />
        <span className="text-[var(--g500)]">{item.state === 'running' ? item.progress || t('cp.tool.running') : item.state === 'error' ? t('cp.tool.failed') : t('cp.tool.done')}</span>
      </button>
      {steps ? (
        <ol className="mt-1 flex flex-col gap-0.5 pl-5 text-[var(--g300)]">
          {steps.map((s, i) => (
            <li key={i} className="num">
              {i + 1}. {stepText(s, ct)}
            </li>
          ))}
        </ol>
      ) : null}
      {open || item.state === 'error' ? <pre className="cp-pre">{item.text || JSON.stringify(item.args)}</pre> : null}
    </div>
  )
}

function ApprovalCard({ item }: { item: Extract<CopilotItem, { kind: 'approval' }> }) {
  const t = useCPT()
  const ct = useCT()
  const req = item.request
  const steps = Array.isArray(req.steps) ? (req.steps as Record<string, unknown>[]) : [req]
  const decide = (ok: boolean) => {
    useCopilot.getState().setApproval(item.id, ok ? 'approved' : 'declined')
    void bridge()?.approve(item.id, ok)
  }
  return (
    <div className="cp-approval" data-state={item.state} data-approval={item.id}>
      <div className="flex items-center gap-2">
        <Icon icon="alert" className="size-3.5 flex-none text-[var(--g50)]" />
        <span className="font-[560] text-[var(--g50)]">{t('cp.approval.title', { aircraft: item.label })}</span>
      </div>
      <ol className="flex flex-col gap-0.5 pl-5 text-[var(--g200)]">
        {steps.map((s, i) => (
          <li key={i} className="num">
            {steps.length > 1 ? `${i + 1}. ` : ''}
            {stepText(s, ct)}
          </li>
        ))}
      </ol>
      {item.state === 'pending' ? (
        <>
          <p className="text-[var(--g400)]">{t('cp.approval.body')}</p>
          <div className="flex gap-1.5">
            <button type="button" className="cp-approve" onClick={() => decide(true)} data-approve="">
              {t('cp.approval.approve')}
            </button>
            <Button variant="outline" size="sm" onClick={() => decide(false)} data-decline="">
              {t('cp.approval.decline')}
            </Button>
          </div>
        </>
      ) : (
        <p className="text-[var(--g400)]">{t(`cp.approval.${item.state}` as CopilotKey)}</p>
      )}
    </div>
  )
}

function Transcript() {
  const t = useCPT()
  const items = useCopilot((s) => s.items)
  const busy = useCopilot((s) => s.busy)
  const end = React.useRef<HTMLDivElement>(null)
  React.useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' })
  }, [items, busy])
  return (
    <div className="flex flex-col gap-2.5 px-3 py-3">
      {items.map((it) => {
        switch (it.kind) {
          case 'user':
            return (
              <div key={it.key} className="cp-user">
                {it.text}
              </div>
            )
          case 'assistant':
            return (
              <div key={it.key} className="cp-assistant">
                {it.text ? <RichText text={it.text} /> : null}
                {it.aborted ? <p className="text-[var(--g500)]">{t('cp.aborted')}</p> : null}
                {it.error ? <p className="text-[var(--g300)]">{t('cp.err.generic', { error: it.error })}</p> : null}
              </div>
            )
          case 'tool':
            return <ToolCard key={it.key} item={it} />
          case 'approval':
            return <ApprovalCard key={it.key} item={it} />
          default:
            return (
              <p key={it.key} className={cn('text-hud-sub', it.tone === 'error' ? 'text-[var(--g200)]' : 'text-[var(--g400)]')}>
                {it.text}
              </p>
            )
        }
      })}
      {busy && !items.some((it) => (it.kind === 'assistant' && it.streaming) || (it.kind === 'tool' && it.state === 'running')) ? (
        <p className="cp-thinking text-hud-sub text-[var(--g400)]">{t('cp.thinking')}</p>
      ) : null}
      <div ref={end} />
    </div>
  )
}

function Composer() {
  const t = useCPT()
  const busy = useCopilot((s) => s.busy)
  const selectedId = useUi((s) => s.selectedId)
  const selected = useVehicle(selectedId ?? '')
  const [text, setText] = React.useState('')
  const submit = () => {
    if (busy || !text.trim()) return
    void sendToCopilot(text)
    setText('')
  }
  return (
    <div className="flex flex-col gap-1.5 border-t border-[var(--border)] p-3">
      <Textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            submit()
          }
        }}
        placeholder={t('cp.placeholder')}
        rows={2}
        className="min-h-[52px] resize-none text-[12px]"
        data-copilot-input=""
      />
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-hud-sub text-[var(--g500)]">
          {selected ? t('cp.selected', { aircraft: selected.label }) : t('cp.noSelection')}
        </span>
        {busy ? (
          <Button variant="outline" size="sm" onClick={() => void bridge()?.abort()} data-copilot-stop="">
            <Icon icon="stop" />
            {t('cp.stop')}
          </Button>
        ) : (
          <Button variant="outline" size="sm" disabled={!text.trim()} onClick={submit} data-copilot-send="">
            <Icon icon="send" />
            {t('cp.send')}
          </Button>
        )}
      </div>
    </div>
  )
}

function Empty() {
  const t = useCPT()
  const busy = useCopilot((s) => s.busy)
  return (
    <div className="flex flex-col gap-3 px-3 py-4">
      <p className="text-hud-sub text-[var(--g400)]">{t('cp.empty')}</p>
      <div className="flex flex-col items-start gap-1.5">
        {(['cp.suggest.status', 'cp.suggest.routine', 'cp.suggest.battery'] as const).map((k) => (
          <button key={k} type="button" disabled={busy} className="cp-suggest" onClick={() => void sendToCopilot(t(k))}>
            {t(k)}
          </button>
        ))}
      </div>
    </div>
  )
}

function Setup() {
  const t = useCPT()
  return (
    <div className="flex flex-col items-start gap-2 px-3 py-4">
      <span className="text-hud-title font-[560] text-[var(--g100)]">{t('cp.setupTitle')}</span>
      <p className="text-hud-sub text-[var(--g400)]">{t('cp.setupBody')}</p>
      <Button variant="outline" size="sm" onClick={() => useUi.getState().openSettings('copilot')} data-copilot-setup="">
        <Icon icon="key" />
        {t('cp.setupAction')}
      </Button>
    </div>
  )
}

export function CopilotPanel() {
  const t = useCPT()
  const open = useCopilot((s) => s.open)
  const config = useCopilot((s) => s.config)
  const items = useCopilot((s) => s.items)
  useCopilotBridge()
  if (!open) return null
  const desktop = Boolean(bridge()) && env().electron
  return (
    <aside className="cp-panel l3" aria-label={t('cp.title')} data-copilot-panel="">
      <header className="flex h-10 shrink-0 items-center gap-2 border-b border-[var(--border)] pr-1.5 pl-3">
        <Icon icon="copilot" className="size-4 text-[var(--g200)]" />
        <span className="text-[13px] font-[560] text-[var(--g50)]">{t('cp.title')}</span>
        {config?.model ? <span className="num truncate text-[11px] text-[var(--g500)]">{config.model}</span> : null}
        <span className="flex-1" />
        <Button variant="ghost" size="icon-sm" title={t('cp.newChat')} aria-label={t('cp.newChat')} onClick={() => {
          useCopilot.getState().clear()
          void bridge()?.reset()
        }}>
          <Icon icon="plus" />
        </Button>
        <Button variant="ghost" size="icon-sm" title={t('cp.settings')} aria-label={t('cp.settings')} onClick={() => useUi.getState().openSettings('copilot')}>
          <Icon icon="settings" />
        </Button>
        <Button variant="ghost" size="icon-sm" title={t('cp.close')} aria-label={t('cp.close')} onClick={() => useCopilot.getState().setOpen(false)}>
          <Icon icon="close" />
        </Button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {!desktop ? (
          <p className="px-3 py-4 text-hud-sub text-[var(--g400)]">{t('cp.desktopOnly')}</p>
        ) : !config?.configured || !config.hasKey ? (
          <Setup />
        ) : items.length === 0 ? (
          <Empty />
        ) : (
          <Transcript />
        )}
      </div>
      {desktop && config?.configured ? <Composer /> : null}
    </aside>
  )
}

export function CopilotButton() {
  const t = useCPT()
  const open = useCopilot((s) => s.open)
  const busy = useCopilot((s) => s.busy)
  return (
    <button
      type="button"
      onClick={() => useCopilot.getState().toggle()}
      title={t('cp.hint', { mod: MOD })}
      aria-pressed={open}
      className={cn(
        'no-drag flex h-7 items-center gap-1.5 rounded-md px-2 text-[12px] outline-none transition-colors duration-quick focus-visible:ring-2 focus-visible:ring-ring/40',
        open ? 'bg-[var(--g800)] text-[var(--g50)]' : 'text-[var(--g300)] hover:text-[var(--g50)]',
      )}
      data-copilot-toggle=""
    >
      <Icon icon="copilot" className={cn('size-4', busy && 'cp-pulse')} />
      <span className="max-[1279px]:sr-only">{t('cp.open')}</span>
    </button>
  )
}
