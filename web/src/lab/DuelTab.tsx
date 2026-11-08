// Duel tab (06 §3.4, §7): pick 2 to 8 programs, the number of rounds and a seed, run the duel as an evaluation job,
// then read the Elo ladder (lieflat table, the leader in bold g50; red is never used for a result) and every round:
// seed, winner, hits and survival per program, with the replay of the round on the Fly stage.
import * as React from 'react'
import { useLang } from '@/i18n'
import { cn } from '@/lib/utils'
import { notifyError } from '@/shell/notify'
import { Button } from '@/ui/components/ui/button'
import { Checkbox } from '@/ui/components/ui/checkbox'
import { Input } from '@/ui/components/ui/input'
import { Progress } from '@/ui/components/ui/progress'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/ui/components/ui/table'
import { Icon } from '@/ui/icons'
import { labApi, type DuelResult, type JobView, type LadderRow } from './api'
import { isUnavailable } from './challenges'
import { useTL } from './copy'
import { percent, useJobPoll } from './jobs'
import { ReplayButton } from './JudgeTab'
import { programName } from './parts'
import { useLab } from './store'

export const DUEL_MIN = 2
export const DUEL_MAX = 8
const ROUNDS_MAX = 50

function Ladder({ rows }: { rows: LadderRow[] }) {
  const tl = useTL()
  return (
    <Table data-lf-table="" className="text-[11px]" data-lab-ladder="">
      <TableHeader>
        <TableRow>
          <TableHead className="cap h-7 w-8">{tl('lab.duel.rank')}</TableHead>
          <TableHead className="cap h-7">{tl('lab.duel.program')}</TableHead>
          <TableHead className="cap h-7 text-right">{tl('lab.duel.elo')}</TableHead>
          <TableHead className="cap h-7 text-right">{tl('lab.duel.delta')}</TableHead>
          <TableHead className="cap h-7 text-right">{tl('lab.duel.wld')}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r, i) => (
          <TableRow key={r.program} className="h-7" data-lab-ladder-row={r.program}>
            <TableCell className="num py-1 text-[var(--g400)]">{i + 1}</TableCell>
            <TableCell className={cn('max-w-0 truncate py-1', i === 0 ? 'font-bold text-[var(--g50)]' : 'text-[var(--g100)]')}>{r.name}</TableCell>
            <TableCell data-num="" className={cn('num py-1 text-right', i === 0 ? 'font-bold text-[var(--g50)]' : 'text-[var(--g100)]')}>
              {Math.round(r.elo)}
            </TableCell>
            <TableCell data-num="" className="num py-1 text-right text-[var(--g400)]">
              {r.delta === null ? '—' : `${r.delta > 0 ? '+' : r.delta < 0 ? '−' : ''}${Math.abs(Math.round(r.delta))}`}
            </TableCell>
            <TableCell data-num="" className="num py-1 text-right text-[var(--g300)]">
              {r.wins}–{r.losses}–{r.draws}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function Rounds({ result }: { result: DuelResult }) {
  const tl = useTL()
  const name = (id: string | null) => {
    if (!id) return tl('lab.duel.draw')
    for (const r of result.rounds) for (const e of r.entries) if (e.program === id) return e.name
    return id
  }
  return (
    <Table data-lf-table="" className="text-[11px]" data-lab-rounds="">
      <TableHeader>
        <TableRow>
          <TableHead className="cap h-7 w-12">{tl('lab.duel.round')}</TableHead>
          <TableHead className="cap h-7">
            {tl('lab.duel.winner')} · {tl('lab.duel.hits')}
          </TableHead>
          <TableHead className="cap h-7 w-20 text-right">{tl('lab.judge.replay')}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {result.rounds.map((r) => (
          <TableRow key={r.round} className="h-auto align-top" data-lab-round={r.round}>
            <TableCell className="num py-1.5 text-[var(--g400)]">
              {r.round}
              {r.seed !== null ? <div className="text-[10px] text-[var(--g600)]">{tl('lab.duel.seed')} {r.seed}</div> : null}
            </TableCell>
            <TableCell className="max-w-0 py-1.5">
              <div className="flex min-w-0 flex-col gap-0.5">
                <span className={cn('truncate', r.winner ? 'font-bold text-[var(--g50)]' : 'text-[var(--g300)]')}>{name(r.winner)}</span>
                {r.entries.map((e) => (
                  <span key={e.program} className="num truncate text-[10px] text-[var(--g400)]">
                    <span className="text-[var(--g200)]">{e.name}</span> · {e.hits ?? '—'}
                    {e.alive_s !== null ? ` · ${e.alive_s.toFixed(1)} s` : ''}
                    {e.survived ? '' : ` · ${tl('lab.duel.down')}`}
                  </span>
                ))}
              </div>
            </TableCell>
            <TableCell className="py-1 text-right">{r.trace_id ? <ReplayButton traceId={r.trace_id} label={tl('lab.judge.replay')} /> : <span className="text-[var(--g600)]">—</span>}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

export function DuelTab() {
  const tl = useTL()
  const lang = useLang()
  const programs = useLab((s) => s.programs)
  const current = useLab((s) => s.program)
  const job = useLab((s) => s.duelJob)
  const [picked, setPicked] = React.useState<string[] | null>(null)
  const [rounds, setRounds] = React.useState('5')
  const [seed, setSeed] = React.useState('1')
  const [busy, setBusy] = React.useState(false)
  const [ladder, setLadder] = React.useState<LadderRow[] | null>(null)
  const [unavailable, setUnavailable] = React.useState(false)
  const setJob = React.useCallback((j: JobView) => useLab.getState().setDuelJob(j), [])
  const loadLadder = React.useCallback(() => {
    labApi.ladder().then(
      (rows) => {
        setLadder(rows)
        setUnavailable(false)
      },
      (e) => {
        setLadder([])
        setUnavailable(isUnavailable(e))
      },
    )
  }, [])
  React.useEffect(loadLadder, [loadLadder])
  useJobPoll(job, setJob, loadLadder)
  const list = programs ?? []
  const chosen = picked ?? [current?.id, list.find((p) => p.builtin && p.id === 'hunter_easy')?.id].filter((x, i, a): x is string => !!x && a.indexOf(x) === i)
  const toggle = (id: string, on: boolean) => {
    const next = on ? [...chosen, id] : chosen.filter((x) => x !== id)
    if (next.length <= DUEL_MAX) setPicked(next)
  }
  const nRounds = Number(rounds)
  const nSeed = Number(seed)
  const valid = chosen.length >= DUEL_MIN && chosen.length <= DUEL_MAX && Number.isInteger(nRounds) && nRounds >= 1 && nRounds <= ROUNDS_MAX && Number.isInteger(nSeed) && nSeed >= 0
  const running = job !== null && (job.state === 'queued' || job.state === 'running')
  const run = async () => {
    setBusy(true)
    try {
      useLab.getState().setDuelJob(await labApi.duel({ programs: chosen, rounds: nRounds, seed: nSeed }))
    } catch (e) {
      if (isUnavailable(e)) setUnavailable(true)
      notifyError(e)
    } finally {
      setBusy(false)
    }
  }
  const result = job?.state === 'done' && job.result?.kind === 'duel' ? job.result : null
  const rows = result?.ladder.length ? result.ladder : (ladder ?? [])
  return (
    <div className="flex flex-col gap-3 px-3" data-lab-duel="">
      {unavailable ? <p className="text-hud-sub text-[var(--g500)]">{tl('lab.duel.unavailable')}</p> : null}
      <div className="flex flex-col gap-1">
        <span className="cap">{tl('lab.duel.programs', { n: chosen.length })}</span>
        <div className="lab-picklist flex flex-col">
          {list.map((p) => {
            const on = chosen.includes(p.id)
            return (
              <label key={p.id} className="flex h-7 items-center gap-2 rounded-md px-1.5 hover:bg-[var(--g850)]" data-lab-duel-pick={p.id}>
                <Checkbox checked={on} disabled={!on && chosen.length >= DUEL_MAX} onCheckedChange={(c) => toggle(p.id, Boolean(c))} />
                <span className={cn('min-w-0 flex-1 truncate text-[12px]', on ? 'text-[var(--g50)]' : 'text-[var(--g300)]')}>{programName(p, lang)}</span>
                <span className="num shrink-0 text-[10px] text-[var(--g600)]">{p.builtin ? tl('lab.example') : tl('lab.rev', { rev: p.rev })}</span>
              </label>
            )
          })}
        </div>
      </div>
      <div className="flex items-end gap-2">
        <label className="flex w-20 flex-col gap-1">
          <span className="cap">{tl('lab.duel.rounds')}</span>
          <Input value={rounds} onChange={(e) => setRounds(e.target.value)} inputMode="numeric" className="num h-7" />
        </label>
        <label className="flex w-24 flex-col gap-1">
          <span className="cap">{tl('lab.duel.seed')}</span>
          <Input value={seed} onChange={(e) => setSeed(e.target.value)} inputMode="numeric" className="num h-7" />
        </label>
        <span className="flex-1" />
        <Button variant="outline" size="sm" className="h-7 gap-1.5" disabled={!valid || busy || running} onClick={() => void run()} data-lab-run-duel="">
          <Icon icon="duel" />
          {tl('lab.duel.run')}
        </Button>
      </div>
      {chosen.length < DUEL_MIN ? <p className="text-hud-sub text-[var(--g400)]">{tl('lab.duel.need')}</p> : null}
      {job && running ? (
        <div className="flex flex-col gap-1.5" data-lab-job={job.state}>
          <span className="num text-hud-sub text-[var(--g300)]">{job.state === 'queued' ? tl('lab.judge.queued') : tl('lab.duel.running', { pct: percent(job.progress) })}</span>
          <Progress value={Math.round(job.progress * 100)} aria-label={tl('lab.duel.run')} />
        </div>
      ) : null}
      {job?.state === 'failed' ? (
        <p className="text-hud-sub font-bold text-[var(--g50)]">{tl('lab.duel.failed', { reason: job.error ? (lang === 'zh' ? job.error.message_zh : job.error.message_en) : '—' })}</p>
      ) : null}
      <div className="flex flex-col gap-1">
        <span className="cap">{tl('lab.duel.ladder')}</span>
        {rows.length ? <Ladder rows={rows} /> : <p className="text-hud-sub text-[var(--g500)]">{tl('lab.duel.noLadder')}</p>}
      </div>
      {result?.rounds.length ? (
        <div className="flex flex-col gap-1">
          <span className="cap">{tl('lab.duel.results')}</span>
          <Rounds result={result} />
        </div>
      ) : null}
    </div>
  )
}
