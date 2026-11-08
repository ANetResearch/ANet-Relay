// History tab (06 §2.5, §7): the program's revisions (newest first; author operator or an agent session, note,
// time) and a line diff of the selected revision against its predecessor or against the editor. A revision can be
// loaded into the editor; saving it then makes a new revision, so history is never rewritten.
import * as React from 'react'
import type { ProgramRevision } from '@/api'
import { cn } from '@/lib/utils'
import { Button } from '@/ui/components/ui/button'
import { ToggleGroup, ToggleGroupItem } from '@/ui/components/ui/toggle-group'
import { Icon } from '@/ui/icons'
import { usePoll } from '@/inspector/usePoll'
import { labApi } from './api'
import { useTL } from './copy'
import { diffStats, lineDiff, withContext } from './diff'
import { useAuthorLabel } from './parts'
import { useLab } from './store'

/** Today: 14:02:11; earlier: 2026-11-07 14:02. */
export function revisionTime(t: number, now = Date.now() / 1000): string {
  if (!t) return '—'
  const d = new Date(t * 1000)
  const n = new Date(now * 1000)
  const p = (x: number) => String(x).padStart(2, '0')
  const time = `${p(d.getHours())}:${p(d.getMinutes())}`
  if (d.toDateString() === n.toDateString()) return `${time}:${p(d.getSeconds())}`
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${time}`
}

function Diff({ from, to }: { from: string; to: string }) {
  const tl = useTL()
  const rows = React.useMemo(() => lineDiff(from, to), [from, to])
  const stats = diffStats(rows)
  const shown = React.useMemo(() => withContext(rows, 3), [rows])
  if (!stats.added && !stats.removed) return <p className="text-hud-sub text-[var(--g500)]">{tl('lab.hist.same')}</p>
  return (
    <div className="flex flex-col gap-1">
      <span className="num text-hud-sub text-[var(--g300)]" data-lab-diff-stats="">
        {tl('lab.hist.stats', stats)}
      </span>
      <div className="lab-diff" data-lab-diff="" data-selectable="">
        {shown.map((r, i) =>
          r === null ? (
            <div key={`gap-${i}`} className="lab-diff-gap">
              {tl('lab.hist.fold')}
            </div>
          ) : (
            <div key={i} className={cn('lab-diff-row', `lab-diff-${r.kind}`)} data-kind={r.kind}>
              <span className="lab-diff-no">{r.a ?? ''}</span>
              <span className="lab-diff-no">{r.b ?? ''}</span>
              <span className="lab-diff-mark">{r.kind === 'add' ? '+' : r.kind === 'del' ? '−' : ' '}</span>
              <span className="lab-diff-text">{r.text || ' '}</span>
            </div>
          ),
        )}
      </div>
    </div>
  )
}

export function HistoryTab() {
  const tl = useTL()
  const author = useAuthorLabel()
  const program = useLab((s) => s.program)
  const source = useLab((s) => s.source)
  const id = program?.id ?? null
  const poll = usePoll(() => labApi.revisions(id ?? ''), 0, `${id}:${program?.rev}`, Boolean(id))
  const revisions = React.useMemo(() => [...((poll.data?.revisions ?? []) as ProgramRevision[])].sort((a, b) => b.rev - a.rev), [poll.data])
  const [sel, setSel] = React.useState<number | null>(null)
  const [against, setAgainst] = React.useState<'previous' | 'editor'>('previous')
  if (!program) return null
  if (!revisions.length) return <p className="px-3 text-hud-sub text-[var(--g500)]">{poll.loading ? '' : tl('lab.hist.empty')}</p>
  const current = revisions.find((r) => r.rev === sel) ?? revisions[0]
  const prev = revisions.find((r) => r.rev < current.rev)
  const from = against === 'editor' ? current.source : (prev?.source ?? '')
  const to = against === 'editor' ? source : current.source
  return (
    <div className="flex flex-col gap-3" data-lab-history="">
      <div className="flex flex-col gap-1 px-1.5">
        <span className="cap px-1.5">{tl('lab.hist.title')}</span>
        {revisions.map((r) => (
          <button key={r.rev} type="button" className="lab-row" data-selected={r.rev === current.rev ? '' : undefined} onClick={() => setSel(r.rev)} data-lab-revision={r.rev}>
            <span className="flex min-w-0 items-center gap-2">
              <span className="num shrink-0 text-[12px] text-[var(--g50)]">{tl('lab.rev', { rev: r.rev })}</span>
              <span className={cn('min-w-0 flex-1 truncate text-[11px]', r.author === 'operator' ? 'text-[var(--g300)]' : 'text-[var(--g100)]')} data-author={r.author === 'operator' ? 'operator' : 'agent'}>
                {author(r.author)}
              </span>
              <span className="num shrink-0 text-[11px] text-[var(--g500)]">{revisionTime(r.t)}</span>
            </span>
            {r.note ? <span className="truncate text-[11px] text-[var(--g400)]">{r.note}</span> : null}
          </button>
        ))}
      </div>
      <div className="flex flex-col gap-2 border-t border-[var(--border)] px-3 pt-3">
        <div className="flex items-center gap-2">
          <span className="cap shrink-0">{tl('lab.hist.against')}</span>
          <ToggleGroup value={[against]} onValueChange={(v: unknown[]) => v[0] && setAgainst(v[0] as 'previous' | 'editor')} spacing={0} className="rounded-md bg-[var(--g950)] p-0.5 ring-1 ring-[var(--border)]">
            {(['previous', 'editor'] as const).map((k) => (
              <ToggleGroupItem key={k} value={k} className="h-6 rounded-[5px] px-2 text-[11px] text-[var(--g400)] hover:bg-transparent data-[pressed]:text-[var(--g50)]">
                {tl(k === 'previous' ? 'lab.hist.previous' : 'lab.hist.editor')}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>
        <div className="flex min-h-7 items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-hud-sub text-[var(--g300)]">
            {against === 'editor' ? tl('lab.hist.toEditor', { a: current.rev }) : prev ? tl('lab.hist.compare', { a: prev.rev, b: current.rev }) : tl('lab.hist.first')}
          </span>
          {!program.builtin ? (
            <Button variant="outline" size="sm" className="shrink-0 gap-1.5" onClick={() => useLab.getState().restore(current.source, current.params as Record<string, unknown>, current.rev)} data-lab-restore={current.rev}>
              <Icon icon="reset" />
              {tl('lab.hist.restore')}
            </Button>
          ) : null}
        </div>
        <Diff from={from} to={to} />
      </div>
    </div>
  )
}
