// Preflight checklist (02 §11 Control, 03 §6.3): one row per check with icon, text and status; the manual items
// (stick signs, propellers) carry a Confirm button. Items come from GET /vehicles/{id}/preflight, refreshed every
// 2 s while shown and whenever the snapshot's preflight summary changes.
import * as React from 'react'
import { api } from '@/api'
import { pickText, useLang, useT, type MessageKey } from '@/i18n'
import { cn } from '@/lib/utils'
import { notifyError } from '@/shell/notify'
import { useVehicleField } from '@/store/selectors'
import { Button } from '@/ui/components/ui/button'
import { Icon, type IconKey } from '@/ui/icons'
import { Group } from '@/ui/relay'
import { VoxelLoader } from '@/ui/voxel/VoxelLoader'
import { useCT, type ControlKey } from './strings'

export interface PreflightItem {
  id: string
  level: 'block' | 'warn' | 'info'
  status: 'pass' | 'fail' | 'warn' | 'info' | 'waiting'
  text_en: string
  text_zh: string
  detail_en: string | null
  detail_zh: string | null
}

const POLL_MS = 2000
const MANUAL = new Set(['signs', 'props'])

/** Items of the preflight endpoint, polled while mounted. */
export function usePreflight(vehicleId: string): { items: PreflightItem[] | null; error: boolean; refresh(): void } {
  const summary = useVehicleField(vehicleId, (v) => `${v.preflight.ok}|${v.preflight.blocking.join(',')}|${v.confirmed.signs}|${v.confirmed.props}`)
  const [state, setState] = React.useState<{ id: string; items: PreflightItem[] | null; error: boolean }>({ id: vehicleId, items: null, error: false })
  const [nonce, refresh] = React.useReducer((n: number) => n + 1, 0)
  React.useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout> | null = null
    const load = async () => {
      try {
        const res = (await api.preflight(vehicleId)) as { items?: PreflightItem[] }
        if (alive) setState({ id: vehicleId, items: res.items ?? [], error: false })
      } catch {
        if (alive) setState((s) => ({ id: vehicleId, items: s.id === vehicleId ? s.items : null, error: true }))
      }
      if (alive) timer = setTimeout(load, POLL_MS)
    }
    void load()
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
    }
  }, [vehicleId, summary, nonce])
  return { items: state.id === vehicleId ? state.items : null, error: state.id === vehicleId && state.error, refresh }
}

const ICON: Record<PreflightItem['status'], IconKey> = { pass: 'check', fail: 'alert', warn: 'alert', info: 'info', waiting: 'pending' }

function Row({ item, vehicleId, onConfirmed, kind }: { item: PreflightItem; vehicleId: string; onConfirmed(): void; kind: 'real' | 'sim' }) {
  const t = useT()
  const ct = useCT()
  const lang = useLang()
  const [busy, setBusy] = React.useState(false)
  const failing = item.status === 'fail'
  const statusWord = ct(`pf.${item.status}` as ControlKey)
  const detail = pickText(lang, item.detail_en ?? '', item.detail_zh ?? '')
  const text = item.id in KEYS ? t(KEYS[item.id as keyof typeof KEYS]) : pickText(lang, item.text_en, item.text_zh)
  const confirm = async () => {
    setBusy(true)
    try {
      await api.confirm(vehicleId, item.id as 'signs' | 'props')
      onConfirmed()
    } catch (e) {
      notifyError(e)
    } finally {
      setBusy(false)
    }
  }
  return (
    <li className="flex min-h-7 items-start gap-2 px-3 py-1" data-preflight={item.id} data-status={item.status}>
      <Icon
        icon={ICON[item.status]}
        className={cn('mt-0.5 size-3.5 flex-none', item.status === 'pass' ? 'text-[var(--g300)]' : failing ? 'text-[var(--g50)]' : 'text-[var(--g500)]')}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <span className={cn('text-ed-value', failing ? 'text-[var(--g50)]' : 'text-[var(--g200)]')}>{text}</span>
        {detail && item.status !== 'pass' ? <span className="truncate text-hud-sub text-[var(--g500)]" title={detail}>{detail}</span> : null}
        {detail && item.status === 'pass' && kind === 'sim' ? <span className="text-hud-sub text-[var(--g500)]">{detail}</span> : null}
      </div>
      {MANUAL.has(item.id) && failing ? (
        <Button size="sm" variant="outline" disabled={busy} onClick={confirm} data-confirm={item.id}>
          {t('action.confirm')}
        </Button>
      ) : (
        <span className={cn('mt-0.5 flex-none text-hud-sub', failing ? 'font-[560] text-[var(--g50)]' : item.status === 'pass' ? 'text-[var(--g400)]' : 'text-[var(--g500)]')}>
          {item.status === 'waiting' && item.detail_en === 'Waiting for parameters' ? t('preflight.waitingParams') : statusWord}
        </span>
      )}
    </li>
  )
}

const KEYS = {
  link: 'preflight.link',
  autopilot: 'preflight.autopilot',
  battery: 'preflight.battery',
  prearm: 'preflight.prearm',
  gcs_input: 'preflight.gcs_input',
  signs: 'preflight.signs',
  props: 'preflight.props',
  mode: 'preflight.mode',
  failsafe: 'preflight.failsafe',
  position: 'preflight.position',
} as const satisfies Record<string, MessageKey>

export function PreflightList({ vehicleId, kind, blocking }: { vehicleId: string; kind: 'real' | 'sim'; blocking: number }) {
  const t = useT()
  const ct = useCT()
  const { items, error, refresh } = usePreflight(vehicleId)
  const summary = blocking > 0 ? ct('pf.summaryBlocked', { n: blocking }) : ct('pf.summaryOk')
  return (
    <Group
      label={t('preflight.title')}
      collapsible
      defaultOpen={kind === 'real'}
      right={<span className={cn('text-hud-sub', blocking > 0 ? 'font-[560] text-[var(--g50)]' : 'text-[var(--g400)]')}>{summary}</span>}
      key={`${vehicleId}-${kind}`}
    >
      {items === null ? (
        error ? (
          <p className="px-3 text-hud-sub text-[var(--g500)]">{ct('pf.error')}</p>
        ) : (
          <div className="px-3 py-1" data-preflight-loading="">
            <VoxelLoader size={24} label={ct('pf.loading')} />
          </div>
        )
      ) : (
        <ul className="flex flex-col" data-preflight-list="">
          {items.map((it) => (
            <Row key={it.id} item={it} vehicleId={vehicleId} onConfirmed={refresh} kind={kind} />
          ))}
        </ul>
      )}
    </Group>
  )
}
