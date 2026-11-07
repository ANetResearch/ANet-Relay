// Records workspace (02 §13, R-50..R-52): record list by day (320) | single-record replay. `focus` comes from a click
// on the relay timeline and opens the record that covers that moment with the cursor on it.
// Contract: CONTRACTS.md#recordsworkspace.
import * as React from 'react'
import { api, RelayApiError } from '@/api'
import { useLang } from '@/i18n'
import { clock, courseTime, duration } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Input } from '@/ui/components/ui/input'
import { ScrollArea } from '@/ui/components/ui/scroll-area'
import { Icon } from '@/ui/icons'
import { EmptyState, SimTag } from '@/ui/relay'
import { useT8 } from '@/fleet/copy'
import { usePoll } from '@/inspector/usePoll'
import { useLength } from '@/settings/prefs'
import { dayKey, groupByDay, recordFor, type RecordSummary } from './model'
import { useRecordsCtl } from './ctl'
import { Replay } from './Replay'
import './records.css'

export interface RecordsWorkspaceProps {
  focus: { vehicle: string; t: number } | null
}

function dayLabel(day: string, t: ReturnType<typeof useT8>, lang: 'en' | 'zh'): string {
  const now = Date.now() / 1000
  if (day === dayKey(now)) return t('records.today')
  if (day === dayKey(now - 86400)) return t('records.yesterday')
  const [y, m, d] = day.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString(lang === 'zh' ? 'zh-CN' : 'en-GB', { year: 'numeric', month: 'short', day: 'numeric' })
}

function RecordRow({ r, selected, onSelect }: { r: RecordSummary; selected: boolean; onSelect(): void }) {
  const t = useT8()
  const len = useLength()
  return (
    <button type="button" className="record-row" data-selected={selected ? '' : undefined} data-record={r.id} onClick={onSelect}>
      <div className="flex min-w-0 items-center gap-2">
        <span className="num w-10 shrink-0 text-[11px] text-[var(--g400)]">{clock(r.started_at)?.slice(0, 5)}</span>
        {r.kind === 'sim' ? <SimTag /> : null}
        <span className="truncate text-hud-title font-[560] text-[var(--g100)]">{r.label}</span>
        <span className="flex-1" />
        {r.live ? <span className="rec-live shrink-0">{t('records.recording')}</span> : null}
      </div>
      <div className="flex items-center gap-3 pl-12 text-[11px] text-[var(--g400)]">
        <span className="num">{duration(r.duration_s)}</span>
        <span className="num">{r.max_alt_m !== null && r.max_alt_m > 0 ? `↑ ${len(r.max_alt_m)}` : ''}</span>
        <span className="flex-1" />
        {r.course_best_s !== null ? (
          <span className="flex items-center gap-1 text-[var(--g200)]" title={t('records.course')}>
            <Icon icon="course" className="size-3" />
            <span className="num">{courseTime(r.course_best_s)}</span>
          </span>
        ) : null}
        {r.alerts > 0 ? (
          <span className="flex items-center gap-1 text-[var(--g200)]" title={t('records.alerts', { n: r.alerts })}>
            <Icon icon="alert" className="size-3" />
            <span className="num">{r.alerts}</span>
          </span>
        ) : null}
      </div>
    </button>
  )
}

export function RecordsWorkspace({ focus }: RecordsWorkspaceProps) {
  const t = useT8()
  const lang = useLang()
  const list = usePoll(() => api.historySessions() as Promise<{ sessions: RecordSummary[] }>, 5000, 'records')
  const records = list.data?.sessions ?? null
  const [q, setQ] = React.useState('')
  const [selectedId, setSelectedId] = React.useState<number | null>(null)
  const [focusT, setFocusT] = React.useState<number | null>(null)

  // a timeline click selects the record covering that moment and places the cursor there
  const [appliedFocus, setAppliedFocus] = React.useState<RecordsWorkspaceProps['focus']>(null)
  if (focus && records && appliedFocus !== focus) {
    setAppliedFocus(focus)
    const r = recordFor(records, focus.vehicle, focus.t)
    if (r) {
      setSelectedId(r.id)
      setFocusT(focus.t)
    }
  }

  // a record opened by application control (04 §3.2 open_record)
  const openReq = useRecordsCtl((s) => s.open)
  const [appliedOpen, setAppliedOpen] = React.useState<typeof openReq>(null)
  if (openReq && appliedOpen !== openReq) {
    setAppliedOpen(openReq)
    setSelectedId(openReq.id)
    setFocusT(null)
  }

  const filtered = React.useMemo(() => {
    if (!records) return []
    const n = q.trim().toLowerCase()
    if (!n) return records
    return records.filter((r) => [r.label, r.vehicle_id, r.autopilot ?? '', String(r.id), dayKey(r.started_at)].some((s) => s.toLowerCase().includes(n)))
  }, [records, q])
  const groups = React.useMemo(() => groupByDay(filtered), [filtered])
  const selected = records?.find((r) => r.id === selectedId) ?? null

  const listError = list.error && !records ? (list.error instanceof RelayApiError ? list.error.text(lang) : String(list.error)) : null

  return (
    <div className="flex h-full min-w-0 gap-2" data-workspace="records">
      <section className="l1 flex w-[320px] shrink-0 flex-col overflow-hidden rounded-[var(--radius)]" aria-label={t('records.title')}>
        <header className="flex h-10 shrink-0 items-center gap-2 px-3">
          <h2 className="text-hud-title font-[560] text-[var(--g100)]">{t('records.title')}</h2>
          {records?.length ? <span className="num text-hud-sub text-[var(--g500)]">{records.length}</span> : null}
        </header>
        <div className="relative shrink-0 px-3 pb-2">
          <Icon icon="cmd.search" className="pointer-events-none absolute top-1/2 left-5 size-3.5 -translate-y-[calc(50%+4px)] text-[var(--g500)]" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('records.search')} aria-label={t('records.search')} className="h-7 pl-7 text-[12px]" />
        </div>
        {listError ? (
          <p className="px-3 py-3 text-hud-sub text-[var(--g50)]">{t('records.listFailed', { reason: listError })}</p>
        ) : records && records.length === 0 ? (
          <EmptyState title={t('records.empty')} body={t('records.emptyBody')} />
        ) : records && filtered.length === 0 ? (
          <p className="px-3 py-3 text-hud-sub text-[var(--g500)]">{t('records.noMatch', { q })}</p>
        ) : (
          <ScrollArea className="min-h-0 flex-1">
            <div className="flex flex-col gap-1 px-2 pb-2">
              {groups.map((g) => (
                <React.Fragment key={g.day}>
                  <div className="flex h-6 items-end gap-2 px-1 pb-1">
                    <span className="cap">{dayLabel(g.day, t, lang)}</span>
                    <span className="num text-hud-cap text-[var(--g500)]">{g.items.length}</span>
                  </div>
                  {g.items.map((r) => (
                    <RecordRow
                      key={r.id}
                      r={r}
                      selected={r.id === selectedId}
                      onSelect={() => {
                        setSelectedId(r.id)
                        setFocusT(null)
                      }}
                    />
                  ))}
                </React.Fragment>
              ))}
            </div>
          </ScrollArea>
        )}
      </section>
      <section className={cn('l1 flex min-w-0 flex-1 flex-col overflow-hidden rounded-[var(--radius)]')} aria-live="polite">
        {selected ? (
          <Replay key={selected.id} record={selected} focusT={focusT} />
        ) : (
          <EmptyState title={t('records.title')} body={t('records.select')} width={260} />
        )}
      </section>
    </div>
  )
}
