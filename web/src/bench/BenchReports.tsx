// Records workspace "Bench reports" group (05 §4.5): saved bench runs, newest first, above the flight records. A row
// opens the report in the right pane (SavedReport); the list refreshes every 5 s like the record list.
import { api, RelayApiError, type TestRunSummary } from '@/api'
import { pickText, useLang } from '@/i18n'
import { clock } from '@/lib/format'
import { cn } from '@/lib/utils'
import { usePoll } from '@/inspector/usePoll'
import { Group, SimTag } from '@/ui/relay'
import { stateTone } from './model'
import { useTB, type KeyB } from './copy'
import './bench.css'

function Row({ r, selected, onSelect }: { r: TestRunSummary; selected: boolean; onSelect(): void }) {
  const t = useTB()
  const lang = useLang()
  const title = r.title_en ? pickText(lang, r.title_en, r.title_zh ?? '') : r.procedure
  return (
    <button type="button" className="bench-report-row" data-selected={selected ? '' : undefined} data-test-run={r.run_id} onClick={onSelect}>
      <div className="flex min-w-0 items-center gap-2">
        <span className="num w-10 shrink-0 text-[11px] text-[var(--g400)]">{clock(r.started)?.slice(0, 5)}</span>
        {r.kind === 'sim' ? <SimTag /> : null}
        <span className="truncate text-hud-title font-[560] text-[var(--g100)]">{r.label ?? r.vehicle}</span>
        <span className="flex-1" />
        <span className={cn('shrink-0 text-[11px]', `bench-tone-${stateTone(r.state)}`)}>{t(`bench.state.${r.state}` as KeyB)}</span>
      </div>
      <div className="flex items-center gap-3 pl-12 text-[11px] text-[var(--g400)]">
        <span className="truncate">{title}</span>
        <span className="flex-1" />
        <span className="num shrink-0">{t('bench.report.counts', { pass: r.counts.pass, warn: r.counts.warn, fail: r.counts.fail })}</span>
      </div>
    </button>
  )
}

export function BenchReportsGroup({ selectedId, onSelect, query = '' }: { selectedId: number | null; onSelect(id: number): void; query?: string }) {
  const t = useTB()
  const lang = useLang()
  const list = usePoll(() => api.testRuns(undefined, 50), 5000, 'test-runs')
  const runs = list.data?.runs ?? null
  const q = query.trim().toLowerCase()
  const shown = (runs ?? []).filter((r) => !q || [r.label ?? '', r.vehicle, r.procedure, r.title_en ?? '', r.title_zh ?? '', String(r.run_id)].some((s) => s.toLowerCase().includes(q)))
  if (runs !== null && runs.length === 0) return null
  return (
    <Group label={t('bench.records.title')} count={runs?.length} collapsible defaultOpen className="pb-1">
      {list.error && !runs ? (
        <p className="px-3 text-hud-sub text-[var(--g50)]">{t('bench.records.failed', { reason: list.error instanceof RelayApiError ? list.error.text(lang) : String(list.error) })}</p>
      ) : (
        <div className="flex flex-col gap-1" data-bench-reports="">
          {shown.map((r) => (
            <Row key={r.run_id} r={r} selected={r.run_id === selectedId} onSelect={() => onSelect(r.run_id)} />
          ))}
        </div>
      )}
    </Group>
  )
}
