// Inspector Parameters (02 §11, R-14, 03 §6.1 H12): search, refresh with progress, a virtual table (name Mono /
// value / type); a value is edited in its row, written only after typing the parameter name, and confirmed by
// reading it back (copy -> check morph).
import * as React from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { api, RelayApiError } from '@/api'
import { useLang } from '@/i18n'
import { cn } from '@/lib/utils'
import { notifyError } from '@/shell/notify'
import { useVehicleField } from '@/store/selectors'
import { Button } from '@/ui/components/ui/button'
import { Input } from '@/ui/components/ui/input'
import { Icon, StateIcon } from '@/ui/icons'
import { TypeToConfirm } from '@/ui/relay'
import { useT8 } from '@/fleet/copy'
import { usePoll } from './usePoll'
import { VoxelLoader } from '@/ui/voxel/VoxelLoader'

export interface Param {
  name: string
  value: number
  type: string
  index?: number
}

interface ParamsResponse {
  id: string
  received: number
  total: number
  complete: boolean
  fetching: boolean
  params: Param[]
}

const ROW = 28
const CONFIRM = 92

export const isIntegerType = (type: string) => /^u?int/i.test(type)

export function paramText(p: Pick<Param, 'value' | 'type'>): string {
  if (isIntegerType(p.type)) return String(Math.round(p.value))
  return String(Number(p.value.toPrecision(7)))
}

/** Parses a typed value for a parameter type; returns an error key when invalid. */
export function parseParamValue(text: string, type: string): { value: number } | { error: 'param.invalid' | 'param.integer' } {
  const s = text.trim()
  if (!/^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i.test(s)) return { error: 'param.invalid' }
  const n = Number(s)
  if (!Number.isFinite(n)) return { error: 'param.invalid' }
  if (isIntegerType(type) && !Number.isInteger(n)) return { error: 'param.integer' }
  return { value: n }
}

function errText(err: unknown, lang: 'en' | 'zh'): string {
  return err instanceof RelayApiError ? err.text(lang) : err instanceof Error ? err.message : String(err)
}

function VerifiedMark() {
  const [on, setOn] = React.useState(false)
  React.useEffect(() => {
    const tm = setTimeout(() => setOn(true), 30)
    return () => clearTimeout(tm)
  }, [])
  return <StateIcon icon="copy" alt={on} spring="smooth" className="size-3.5 text-[var(--g50)]" />
}

export function ParametersTab({ vehicleId }: { vehicleId: string }) {
  const t = useT8()
  const lang = useLang()
  const linkState = useVehicleField(vehicleId, (v) => v.link.state)
  const live = linkState === 'ok' || linkState === 'delayed'
  const [fast, setFast] = React.useState(true)
  const poll = usePoll(() => api.params(vehicleId) as Promise<ParamsResponse>, fast ? 1000 : 15000, vehicleId)
  const data = poll.data
  React.useEffect(() => {
    if (data) setFast(!data.complete || data.fetching)
  }, [data])

  const [q, setQ] = React.useState('')
  const [edit, setEdit] = React.useState<{ name: string; text: string; error: string | null; confirming: boolean } | null>(null)
  const [busy, setBusy] = React.useState(false)
  const [verified, setVerified] = React.useState<string | null>(null)
  const [writeError, setWriteError] = React.useState<string | null>(null)

  const rows = React.useMemo(() => {
    const list = [...(data?.params ?? [])].sort((a, b) => a.name.localeCompare(b.name))
    const needle = q.trim().toUpperCase()
    return needle ? list.filter((p) => p.name.toUpperCase().includes(needle)) : list
  }, [data, q])

  const scrollRef = React.useRef<HTMLDivElement>(null)
  // TanStack Virtual is not React-Compiler-safe; this component is not memoised by the compiler, which is fine here
  // oxlint-disable-next-line react/incompatible-library
  const virt = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (i) => (rows[i]?.name !== edit?.name ? ROW : ROW + (edit?.confirming ? CONFIRM : 0) + (edit?.error ? 18 : 0)),
    overscan: 12,
  })
  React.useEffect(() => {
    virt.measure()
  }, [edit?.confirming, edit?.name, edit?.error, virt])

  const refresh = () => {
    api.refreshParams(vehicleId).then(() => {
      setFast(true)
      poll.reload()
    }, notifyError)
  }

  const startEdit = (p: Param) => {
    if (!live || busy) return
    setWriteError(null)
    setVerified(null)
    setEdit({ name: p.name, text: paramText(p), error: null, confirming: false })
  }

  const review = (p: Param) => {
    if (!edit) return
    const parsed = parseParamValue(edit.text, p.type)
    if ('error' in parsed) {
      setEdit({ ...edit, error: t(parsed.error, { type: p.type }) })
      return
    }
    if (parsed.value === p.value) {
      setEdit(null)
      return
    }
    setEdit({ ...edit, error: null, confirming: true })
  }

  const write = async (p: Param) => {
    if (!edit) return
    const parsed = parseParamValue(edit.text, p.type)
    if ('error' in parsed) return
    setBusy(true)
    setWriteError(null)
    try {
      await api.setParam(vehicleId, p.name, parsed.value)
      setEdit(null)
      setVerified(p.name)
      poll.reload()
    } catch (err) {
      setWriteError(errText(err, lang))
    } finally {
      setBusy(false)
    }
  }

  const loadErr = poll.error && !data ? errText(poll.error, lang) : null

  return (
    <div className="flex h-full min-h-0 flex-col" data-inspector-params="">
      <div className="flex shrink-0 flex-col gap-2 px-3 pt-3 pb-2">
        <div className="flex items-center gap-2">
          <div className="relative min-w-0 flex-1">
            <Icon icon="cmd.search" className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-[var(--g500)]" />
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={t('param.search')}
              aria-label={t('param.search')}
              spellCheck={false}
              className="h-7 pl-7 font-mono text-[11px] uppercase placeholder:normal-case"
            />
          </div>
          <Button variant="outline" size="default" onClick={refresh} disabled={!live} className="gap-1.5">
            <Icon icon="refresh" />
            {t('action.refresh')}
          </Button>
        </div>
        <div className="flex items-center gap-2">
          <span className="num text-[11px] text-[var(--g400)]" data-param-progress="">
            {data ? t('param.received', { n: data.received, total: data.total || '—' }) : '—'}
          </span>
          {data && (data.fetching || !data.complete) ? <span className="text-hud-sub text-[var(--g500)]">{t('param.fetching')}</span> : null}
          <span className="flex-1" />
          <span className="text-hud-sub text-[var(--g500)]">{live ? t('param.editHint') : t('param.offline')}</span>
        </div>
        {data && data.total > 0 && !data.complete ? (
          <span className="scan-line" aria-hidden="true">
            <i style={{ transform: `scaleX(${Math.min(1, data.received / data.total)})` }} />
          </span>
        ) : null}
      </div>
      <div className="param-head grid shrink-0 grid-cols-[1fr_96px_52px] gap-2 border-b border-[var(--foreground)] px-3 pb-1">
        <span className="cap">{t('param.name')}</span>
        <span className="cap text-right">{t('param.value')}</span>
        <span className="cap text-right">{t('param.type')}</span>
      </div>
      {loadErr ? (
        <p className="px-3 py-3 text-hud-sub text-[var(--g50)]">{t('param.loadFailed', { reason: loadErr })}</p>
      ) : rows.length === 0 ? (
        q || data ? (
          <p className="px-3 py-3 text-hud-sub text-[var(--g500)]">{q ? t('param.noMatch', { q }) : t('param.empty')}</p>
        ) : (
          <div className="px-3 py-3" data-param-loading="">
            <VoxelLoader size={24} />
          </div>
        )
      ) : (
        <div ref={scrollRef} className="param-scroll min-h-0 flex-1 overflow-y-auto" role="table" aria-rowcount={rows.length}>
          <div style={{ height: virt.getTotalSize(), position: 'relative' }}>
            {virt.getVirtualItems().map((it) => {
              const p = rows[it.index]
              const editing = edit?.name === p.name
              return (
                <div
                  key={p.name}
                  role="row"
                  data-param={p.name}
                  className={cn('param-row absolute inset-x-0', editing && 'bg-[var(--g850)]')}
                  style={{ top: it.start, height: it.size }}
                >
                  <div className="grid h-7 grid-cols-[1fr_96px_52px] items-center gap-2 px-3">
                    <span className="num truncate text-[11px] text-[var(--g200)]" data-selectable="">
                      {p.name}
                    </span>
                    {editing && !edit.confirming ? (
                      <input
                        autoFocus
                        value={edit.text}
                        aria-label={p.name}
                        aria-invalid={edit.error ? true : undefined}
                        onChange={(e) => setEdit({ ...edit, text: e.target.value, error: null })}
                        onKeyDown={(e) => {
                          e.stopPropagation()
                          if (e.key === 'Enter') review(p)
                          else if (e.key === 'Escape') setEdit(null)
                        }}
                        onBlur={() => setEdit((cur) => (cur && cur.name === p.name && !cur.confirming ? null : cur))}
                        className="num h-6 w-full rounded-sm border border-[var(--g500)] bg-[var(--g950)] px-1.5 text-right text-[11px] text-[var(--g50)] outline-none aria-invalid:border-[var(--g50)]"
                      />
                    ) : (
                      <button
                        type="button"
                        disabled={!live}
                        onClick={() => startEdit(p)}
                        className={cn('num flex h-6 items-center justify-end gap-1.5 rounded-sm px-1.5 text-right text-[11px] text-[var(--g100)] outline-none enabled:hover:bg-[var(--g800)] focus-visible:ring-1 focus-visible:ring-ring', editing && 'text-[var(--g50)]')}
                      >
                        {verified === p.name ? <VerifiedMark /> : null}
                        {editing ? edit.text : paramText(p)}
                      </button>
                    )}
                    <span className="num text-right text-[10px] text-[var(--g500)]">{p.type}</span>
                  </div>
                  {editing && edit.error ? <p className="px-3 pb-1 text-right text-hud-sub text-[var(--g50)]">{edit.error}</p> : null}
                  {editing && edit.confirming ? (
                    <div className="px-3 pb-2">
                      <TypeToConfirm
                        word={p.name}
                        prompt={t('param.typeName', { name: p.name })}
                        actionLabel={busy ? t('param.writing') : t('action.write')}
                        busy={busy}
                        error={writeError}
                        onConfirm={() => write(p)}
                        onCancel={() => setEdit(null)}
                        className="min-h-0"
                      />
                    </div>
                  ) : null}
                </div>
              )
            })}
          </div>
        </div>
      )}
      {verified ? (
        <p className="shrink-0 border-t border-[var(--border)] px-3 py-1.5 text-hud-sub text-[var(--g300)]" role="status">
          {verified} · {t('param.verified')}
        </p>
      ) : null}
    </div>
  )
}
