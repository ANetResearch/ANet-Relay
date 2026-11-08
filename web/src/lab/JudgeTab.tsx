// Judge tab (06 §3.2, §3.4, §7): submit the saved revision to a challenge, follow the evaluation job, and read the
// result: score (0-100), pass or not, the metrics, one row per seed (visible samples and hidden seeds) and the
// replay of each judged run on the Fly stage.
import * as React from 'react'
import { useLang } from '@/i18n'
import { cn } from '@/lib/utils'
import { notifyError } from '@/shell/notify'
import { Button } from '@/ui/components/ui/button'
import { Progress } from '@/ui/components/ui/progress'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/ui/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/ui/components/ui/table'
import { Icon } from '@/ui/icons'
import { labApi, type ChallengeResult, type JobView } from './api'
import { isUnavailable, useChallenges } from './challenges'
import { useTL } from './copy'
import { percent, useJobPoll } from './jobs'
import { programName } from './parts'
import { isDirty, useLab } from './store'
import { openTraceById, useTraceReplay } from './traceReplay'

const metricText = (v: string | number | boolean | null) => (v === null ? '—' : typeof v === 'number' ? String(Math.round(v * 1000) / 1000) : String(v))

export function ReplayButton({ traceId, label }: { traceId: string; label?: string }) {
  const tl = useTL()
  const loading = useTraceReplay((s) => s.loading === traceId)
  return (
    <Button variant="ghost" size="sm" className="h-6 gap-1 px-1.5 text-[var(--g300)]" disabled={loading} onClick={() => void openTraceById(traceId)} data-open-trace={traceId}>
      <Icon icon="tl.play" />
      {loading ? tl('lab.replay.loading') : (label ?? tl('lab.judge.openTrace'))}
    </Button>
  )
}

function JobProgress({ job }: { job: JobView }) {
  const tl = useTL()
  const lang = useLang()
  if (job.state === 'failed') {
    return <p className="text-hud-sub font-bold text-[var(--g50)]">{tl('lab.judge.jobFailed', { reason: job.error ? (lang === 'zh' ? job.error.message_zh : job.error.message_en) : '—' })}</p>
  }
  if (job.state === 'cancelled') return <p className="text-hud-sub text-[var(--g300)]">{tl('lab.judge.cancelled')}</p>
  if (job.state === 'done') return null
  return (
    <div className="flex flex-col gap-1.5" data-lab-job={job.state}>
      <span className="num text-hud-sub text-[var(--g300)]">{job.state === 'queued' ? tl('lab.judge.queued') : tl('lab.judge.running', { pct: percent(job.progress) })}</span>
      <Progress value={Math.round(job.progress * 100)} aria-label={tl('lab.judge.result')} />
    </div>
  )
}

function Result({ r }: { r: ChallengeResult }) {
  const tl = useTL()
  return (
    <div className="flex flex-col gap-3" data-lab-judge-result={r.passed ? 'passed' : 'failed'}>
      <div className="flex items-end gap-3">
        <div className="flex flex-col">
          <span className="cap">{tl('lab.judge.score')}</span>
          <span className="num text-hud-kpi font-[650] text-[var(--g50)]">{Math.round(r.score)}</span>
        </div>
        <span className={cn('pb-1 text-[12px]', r.passed ? 'font-bold text-[var(--g50)]' : 'text-[var(--g300)]')}>{r.passed ? tl('lab.judge.passed') : tl('lab.judge.failed')}</span>
        <span className="flex-1" />
        {r.trace_id ? <ReplayButton traceId={r.trace_id} /> : null}
      </div>
      {r.metrics.length ? (
        <div className="flex flex-col gap-1">
          <span className="cap">{tl('lab.judge.metrics')}</span>
          <Table data-lf-table="" className="text-[11px]">
            <TableBody>
              {r.metrics.map(([k, v]) => (
                <TableRow key={k} className="h-7">
                  <TableCell className="num py-1 text-[var(--g300)]">{k}</TableCell>
                  <TableCell data-num="" className="num py-1 text-right text-[var(--g100)]">
                    {metricText(v)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : null}
      {r.seeds.length ? (
        <div className="flex flex-col gap-1">
          <span className="cap">{tl('lab.judge.seeds')}</span>
          <Table data-lf-table="" className="text-[11px]">
            <TableHeader>
              <TableRow>
                <TableHead className="cap h-7">{tl('lab.judge.seed')}</TableHead>
                <TableHead className="cap h-7 text-right">{tl('lab.judge.score')}</TableHead>
                <TableHead className="cap h-7">{tl('lab.judge.result')}</TableHead>
                <TableHead className="cap h-7 text-right">{tl('lab.judge.replay')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {r.seeds.map((s, i) => (
                <TableRow key={`${s.seed}-${i}`} className="h-7" data-lab-seed={s.seed}>
                  <TableCell className="num py-1 text-[var(--g200)]">
                    {s.seed} <span className="text-[var(--g500)]">· {s.hidden ? tl('lab.judge.hidden') : tl('lab.judge.sample')}</span>
                  </TableCell>
                  <TableCell data-num="" className="num py-1 text-right text-[var(--g100)]">
                    {s.score === null ? '—' : Math.round(s.score)}
                  </TableCell>
                  <TableCell className={cn('py-1', s.passed ? 'text-[var(--g100)]' : 'font-bold text-[var(--g50)]')}>{s.passed ? tl('lab.judge.passed') : tl('lab.judge.failed')}</TableCell>
                  <TableCell className="py-0.5 text-right">{s.trace_id ? <ReplayButton traceId={s.trace_id} label={tl('lab.judge.replay')} /> : <span className="text-[var(--g600)]">—</span>}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : null}
    </div>
  )
}

export function JudgeTab() {
  const tl = useTL()
  const lang = useLang()
  const program = useLab((s) => s.program)
  const dirty = useLab((s) => isDirty(s))
  const challengeId = useLab((s) => s.challengeId)
  const job = useLab((s) => s.judgeJob)
  const list = useChallenges((s) => s.list)
  const unavailable = useChallenges((s) => s.unavailable)
  const [busy, setBusy] = React.useState(false)
  const setJob = React.useCallback((j: JobView) => useLab.getState().setJudgeJob(j), [])
  useJobPoll(job, setJob, () => void useChallenges.getState().load())
  const cid = challengeId && list?.some((c) => c.id === challengeId) ? challengeId : (list?.[0]?.id ?? null)
  const c = list?.find((x) => x.id === cid) ?? null
  if (!program) return null
  if (unavailable || (list && !list.length)) return <p className="px-3 text-hud-sub text-[var(--g500)]">{tl('lab.judge.unavailable')}</p>
  const running = job !== null && (job.state === 'queued' || job.state === 'running')
  const submit = async () => {
    if (!cid) return
    setBusy(true)
    try {
      useLab.getState().setJudgeJob(await labApi.submit(cid, program.id, program.rev))
    } catch (e) {
      if (isUnavailable(e)) void useChallenges.getState().load()
      notifyError(e)
    } finally {
      setBusy(false)
    }
  }
  const items = (list ?? []).map((x) => ({ value: x.id, label: lang === 'zh' ? x.name_zh : x.name }))
  const result = job?.state === 'done' && job.result?.kind === 'challenge' ? job.result : null
  return (
    <div className="flex flex-col gap-3 px-3" data-lab-judge="">
      <Select items={items} value={cid} onValueChange={(x) => x && useLab.getState().setChallenge(x as string)}>
        <SelectTrigger size="sm" className="h-7 w-full" aria-label={tl('lab.judge.pick')}>
          <SelectValue placeholder={tl('lab.judge.pick')} />
        </SelectTrigger>
        <SelectContent>
          {items.map((it) => (
            <SelectItem key={it.value} value={it.value}>
              {it.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {c ? (
        <div className="flex flex-col gap-1.5">
          {c.description ? <p className="text-[12px] leading-5 text-[var(--g200)]">{lang === 'zh' ? c.description_zh : c.description}</p> : null}
          {c.pass_en ? (
            <p className="text-hud-sub text-[var(--g400)]">
              <span className="text-[var(--g300)]">{tl('lab.judge.pass')}:</span> {lang === 'zh' ? c.pass_zh : c.pass_en}
            </p>
          ) : null}
          <p className="num text-hud-sub text-[var(--g500)]">{c.best ? tl('lab.judge.best', { score: Math.round(c.best.score), n: c.submissions }) : tl('lab.judge.noBest')}</p>
        </div>
      ) : null}
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" className="gap-1.5" disabled={busy || running || dirty || !cid} onClick={() => void submit()} data-lab-submit="">
            <Icon icon="judge" />
            {tl('lab.judge.submit', { rev: program.rev })}
          </Button>
          <span className="min-w-0 truncate text-hud-sub text-[var(--g500)]">{tl('lab.judge.of', { rev: program.rev, name: programName(program, lang) })}</span>
        </div>
        {dirty ? <p className="text-hud-sub text-[var(--g400)]">{tl('lab.judge.saveFirst')}</p> : null}
      </div>
      {job ? <JobProgress job={job} /> : null}
      {result ? <Result r={result} /> : null}
    </div>
  )
}

