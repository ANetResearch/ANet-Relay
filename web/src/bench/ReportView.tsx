// Bench report (05 §4.4, §4.5): verdict and counts, one lieflat table of every step's metrics with their limits, the
// findings, the suggested parameter changes (never written automatically) and the Markdown / JSON export. Used at
// the end of a run in the Bench tab and for saved runs in Records.
import * as React from 'react'
import { api, RelayApiError, type ProcedureRun, type ProcedureStep } from '@/api'
import { pickText, useLang } from '@/i18n'
import { clock, duration } from '@/lib/format'
import { cn } from '@/lib/utils'
import { notify } from '@/shell/notify'
import { Button } from '@/ui/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/ui/components/ui/table'
import { Icon } from '@/ui/icons'
import { SimTag } from '@/ui/relay'
import { VoxelLoader } from '@/ui/voxel/VoxelLoader'
import { counts, elapsed, formatNumber, formatValue, levelTone, reportFile, stateTone } from './model'
import { useTB, type BenchKey, type KeyB } from './copy'

async function download(runId: number, fmt: 'md' | 'json', t: (k: BenchKey, p?: Record<string, string | number>) => string, lang: 'en' | 'zh') {
  try {
    const res = await fetch(api.testRunExportUrl(runId, fmt))
    if (!res.ok) throw new RelayApiError(res.status, `http_${res.status}`, res.statusText, res.statusText)
    const blob = await res.blob()
    const file = reportFile(runId, fmt)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = file
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 2000)
    notify(t('bench.report.exported', { file }))
  } catch (err) {
    notify(t('bench.report.exportFailed'), { level: 'warn', description: err instanceof RelayApiError ? err.text(lang) : String(err) })
  }
}

export function VerdictWord({ step }: { step: Pick<ProcedureStep, 'state' | 'verdict'> }) {
  const t = useTB()
  const tone = step.verdict === 'fail' ? 'bad' : step.verdict === 'warn' ? 'warn' : step.verdict === 'pass' ? 'ok' : 'muted'
  const key: KeyB = step.verdict ? (`bench.verdict.${step.verdict}` as KeyB) : (`bench.stepState.${step.state}` as KeyB)
  return <span className={cn('text-hud-sub', `bench-tone-${tone}`)}>{t(key)}</span>
}

function StepRows({ step, limitsColumn }: { step: ProcedureStep; limitsColumn: boolean }) {
  const t = useTB()
  const lang = useLang()
  const title = `${step.id} ${pickText(lang, step.title_en, step.title_zh)}`
  if (step.metrics.length === 0) {
    return (
      <TableRow className="h-7" data-report-step={step.id}>
        <TableCell className="py-1 text-[var(--g100)]">{title}</TableCell>
        <TableCell className="py-1">
          <VerdictWord step={step} />
        </TableCell>
        <TableCell className="py-1 text-[var(--g500)]">—</TableCell>
        <TableCell data-num="" className="py-1 text-[var(--g500)]">
          —
        </TableCell>
        {limitsColumn ? <TableCell className="py-1 text-[var(--g500)]">—</TableCell> : null}
      </TableRow>
    )
  }
  return (
    <>
      {step.metrics.map((m, i) => {
        const tone = levelTone(m.level)
        const peak = m.peak !== null && m.mean !== null ? t('bench.report.peak', { v: formatNumber(m.peak) }) : null
        const limits = m.warn !== null || m.bad !== null ? t('bench.report.limitsValue', { warn: formatNumber(m.warn), bad: formatNumber(m.bad) }) : null
        return (
          <TableRow key={m.key} className="h-7" data-report-step={i === 0 ? step.id : undefined} data-metric={m.key}>
            <TableCell className="py-1 align-top text-[var(--g100)]">{i === 0 ? title : ''}</TableCell>
            <TableCell className="py-1 align-top">{i === 0 ? <VerdictWord step={step} /> : null}</TableCell>
            <TableCell className="py-1 align-top text-[var(--g300)]" title={limits ?? undefined}>
              {pickText(lang, m.label_en, m.label_zh)}
            </TableCell>
            <TableCell data-num="" data-hot={tone === 'bad' ? '' : undefined} className={cn('num py-1 align-top', tone === 'warn' ? 'font-[560] text-[var(--g50)]' : 'text-[var(--g200)]')}>
              {formatValue(m.value, m.unit)}
              {peak ? <span className="block text-[10px] text-[var(--g500)]">{peak}</span> : null}
            </TableCell>
            {limitsColumn ? <TableCell className="num py-1 align-top text-[var(--g400)]">{limits ?? '—'}</TableCell> : null}
          </TableRow>
        )
      })}
    </>
  )
}

export function ReportView({ run, className, compact = false }: { run: ProcedureRun; className?: string; compact?: boolean }) {
  const t = useTB()
  const lang = useLang()
  const c = counts(run)
  const tone = stateTone(run.state)
  const findings = run.steps.flatMap((s) => s.findings.map((f) => ({ ...f, step: s.id })))
  const suggestions = run.steps.flatMap((s) => s.suggestions.map((g) => ({ ...g, step: s.id })))
  const took = elapsed(run.started, run.ended)
  return (
    <div className={cn('flex flex-col gap-4', className)} data-bench-report={run.run_id}>
      <section className="flex flex-col gap-2 px-3">
        <div className="flex min-w-0 items-center gap-2">
          {run.simulated ? <SimTag /> : null}
          <h3 className="min-w-0 truncate text-hud-title font-[560] text-[var(--g100)]">{t('bench.report.title', { id: run.run_id })}</h3>
          <span className="flex-1" />
          <span className={cn('text-hud-sub', `bench-tone-${tone}`)} data-run-state={run.state}>
            {t(`bench.state.${run.state}` as KeyB)}
          </span>
        </div>
        <p className="text-hud-sub text-[var(--g400)]">
          {pickText(lang, run.title_en, run.title_zh)} · {run.label ?? run.vehicle}
        </p>
        <dl className="grid grid-cols-[repeat(auto-fill,minmax(112px,1fr))] gap-x-4 gap-y-1.5">
          <div className="flex flex-col">
            <dt className="cap">{t('bench.report.verdict')}</dt>
            <dd className="num text-ed-value text-[var(--g100)]">{t('bench.report.counts', { pass: c.pass, warn: c.warn, fail: c.fail })}</dd>
          </div>
          <div className="flex flex-col">
            <dt className="cap">{t('bench.report.started')}</dt>
            <dd className="num text-ed-value text-[var(--g100)]">{clock(run.started) ?? '—'}</dd>
          </div>
          <div className="flex flex-col">
            <dt className="cap">{t('bench.report.duration')}</dt>
            <dd className="num text-ed-value text-[var(--g100)]">{duration(took) ?? '—'}</dd>
          </div>
          {run.reason_en ? (
            <div className="flex flex-col">
              <dt className="cap">{t('bench.report.reason')}</dt>
              <dd className="text-ed-value text-[var(--g50)]">{pickText(lang, run.reason_en, run.reason_zh ?? '')}</dd>
            </div>
          ) : null}
        </dl>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={() => void download(run.run_id, 'md', t, lang)} data-export="md">
            <Icon icon="external" />
            {t('bench.report.exportMd')}
          </Button>
          <Button size="sm" variant="outline" onClick={() => void download(run.run_id, 'json', t, lang)} data-export="json">
            <Icon icon="external" />
            {t('bench.report.exportJson')}
          </Button>
        </div>
      </section>

      <section className="flex flex-col gap-1.5 px-3">
        <span className="cap">{t('bench.steps')}</span>
        <Table data-lf-table="" className="text-[11px]">
          <TableHeader>
            <TableRow>
              <TableHead className="cap h-7">{t('bench.steps')}</TableHead>
              <TableHead className="cap h-7">{t('bench.report.verdict')}</TableHead>
              <TableHead className="cap h-7">{t('bench.report.metric')}</TableHead>
              <TableHead className="cap h-7 text-right">{t('bench.report.value')}</TableHead>
              {compact ? null : <TableHead className="cap h-7">{t('bench.report.limits')}</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {run.steps.map((s) => (
              <StepRows key={s.id} step={s} limitsColumn={!compact} />
            ))}
          </TableBody>
        </Table>
      </section>

      <section className="flex flex-col gap-1.5 px-3">
        <span className="cap">{t('bench.report.findings')}</span>
        {findings.length === 0 ? (
          <p className="text-hud-sub text-[var(--g500)]">{t('bench.report.noFindings')}</p>
        ) : (
          <ul className="flex flex-col gap-1" data-report-findings="">
            {findings.map((f, i) => (
              <li key={`${f.step}-${f.code}-${i}`} className="flex items-start gap-2 text-hud-sub">
                <Icon icon={f.level === 'info' ? 'info' : 'alert'} className={cn('mt-px size-3.5 flex-none', f.level === 'fail' ? 'text-[var(--r400)]' : f.level === 'warn' ? 'text-[var(--g50)]' : 'text-[var(--g500)]')} />
                <span className="num w-7 flex-none text-[var(--g500)]">{f.step}</span>
                <span className={f.level === 'info' ? 'text-[var(--g400)]' : 'text-[var(--g100)]'}>{pickText(lang, f.text_en, f.text_zh)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="flex flex-col gap-1.5 px-3">
        <span className="cap">{t('bench.report.suggestions')}</span>
        {suggestions.length === 0 ? (
          <p className="text-hud-sub text-[var(--g500)]">{t('bench.report.noSuggestions')}</p>
        ) : (
          <>
            <Table data-lf-table="" className="text-[11px]" data-report-suggestions="">
              <TableHeader>
                <TableRow>
                  <TableHead className="cap h-7">{t('bench.report.param')}</TableHead>
                  <TableHead className="cap h-7 text-right">{t('bench.report.current')}</TableHead>
                  <TableHead className="cap h-7 text-right">{t('bench.report.suggested')}</TableHead>
                  {compact ? null : <TableHead className="cap h-7">{t('bench.report.status')}</TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {suggestions.map((g, i) => (
                  <TableRow key={`${g.step}-${g.name}-${i}`} className="h-7" title={pickText(lang, g.reason_en, g.reason_zh)}>
                    <TableCell className="num py-1 font-mono text-[var(--g100)]">{g.name}</TableCell>
                    <TableCell data-num="" className="num py-1 text-[var(--g300)]">
                      {formatNumber(g.current)}
                    </TableCell>
                    <TableCell data-num="" className="num py-1 text-[var(--g50)]">
                      {formatNumber(g.suggested)}
                      {g.change_pct !== null && g.direction !== 'set' ? <span className="ml-1 text-[var(--g500)]">{`${g.change_pct > 0 ? '+' : ''}${g.change_pct} %`}</span> : null}
                    </TableCell>
                    {compact ? null : <TableCell className="py-1 text-[var(--g300)]">{t(`bench.sugg.${g.state}` as KeyB)}</TableCell>}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <p className="text-hud-sub text-[var(--g500)]">{t('bench.report.suggestionsNote')}</p>
          </>
        )}
      </section>
    </div>
  )
}

/** A saved run loaded by id (Records). */
export function SavedReport({ runId }: { runId: number }) {
  const t = useTB()
  const lang = useLang()
  const [state, setState] = React.useState<{ id: number; run: ProcedureRun | null; error: string | null }>({ id: runId, run: null, error: null })
  React.useEffect(() => {
    let alive = true
    api.testRun(runId).then(
      (run) => alive && setState({ id: runId, run, error: null }),
      (e) => alive && setState({ id: runId, run: null, error: e instanceof RelayApiError ? e.text(lang) : String(e) }),
    )
    return () => {
      alive = false
    }
  }, [runId, lang])
  if (state.id !== runId || (!state.run && !state.error)) {
    return (
      <div className="px-3 py-3" data-bench-report-loading="">
        <VoxelLoader size={24} label={t('bench.records.loading')} />
      </div>
    )
  }
  if (!state.run) return <p className="px-3 py-3 text-hud-sub text-[var(--g50)]">{t('bench.records.failed', { reason: state.error ?? '' })}</p>
  return <ReportView run={state.run} className="py-3" />
}
