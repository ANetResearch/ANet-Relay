// Small pieces shared by the Lab tabs and the Fly inspector's Program group: the run readout of 06 §2.6 (status,
// state, frame rate, faults), the stop / resume actions, author labels and the program select.
import * as React from 'react'
import type { ProgramRunView, ProgramSummary, Vehicle } from '@/api'
import { pickText, useLang } from '@/i18n'
import { cn } from '@/lib/utils'
import { notifyError } from '@/shell/notify'
import { Button } from '@/ui/components/ui/button'
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from '@/ui/components/ui/select'
import { Readout } from '@/ui/relay'
import { labApi } from './api'
import { reasonKey, useTL, type LabKey } from './copy'

export const STATUS_KEYS: Record<ProgramRunView['status'], LabKey> = {
  running: 'lab.status.running',
  paused: 'lab.status.paused',
  faulted: 'lab.status.faulted',
  stopped: 'lab.status.stopped',
}

/** Display name of a program in the UI language (built-in examples carry a Chinese name). */
export function programName(p: Pick<ProgramSummary, 'name' | 'name_zh'>, lang: 'en' | 'zh'): string {
  return pickText(lang, p.name, p.name_zh || p.name)
}

/** "Operator", "Agent · harness-a" or "Built in" for a revision author. */
export function useAuthorLabel(): (author: string | null | undefined) => string {
  const tl = useTL()
  return React.useCallback(
    (author) => (!author || author === 'operator' ? tl('lab.author.operator') : author === 'relay' ? tl('lab.author.relay') : tl('lab.author.agent', { name: author })),
    [tl],
  )
}

/** Status, state, frame rate, faults, revision, reason and last error of a deployment. */
export function RunReadout({ run, compact = false }: { run: ProgramRunView; compact?: boolean }) {
  const tl = useTL()
  const reason = reasonKey(run.reason)
  const active = run.status === 'running' || run.status === 'paused'
  return (
    <div className="flex flex-col gap-2" data-program-run={run.status}>
      <div className={cn('grid gap-x-4 gap-y-2', compact ? 'grid-cols-3' : 'grid-cols-[repeat(auto-fill,minmax(88px,1fr))]')}>
        <Readout label={tl('lab.run.status')}>
          <span className={cn('text-[12px]', run.status === 'faulted' ? 'font-bold text-[var(--g50)]' : 'text-[var(--g100)]')}>{tl(STATUS_KEYS[run.status])}</span>
        </Readout>
        <Readout label={tl('lab.run.state')}>
          <span className="num truncate text-[12px] text-[var(--g100)]" data-program-state="">
            {run.state || '—'}
          </span>
        </Readout>
        <Readout label={tl('lab.run.fps')}>
          <span className="num text-[12px] text-[var(--g100)]">{active ? tl('lab.run.hz', { n: Math.round(run.fps) }) : '—'}</span>
        </Readout>
        <Readout label={tl('lab.run.faults')}>
          <span className={cn('num text-[12px]', run.faults > 0 ? 'font-bold text-[var(--g50)]' : 'text-[var(--g100)]')}>{run.faults}</span>
        </Readout>
        {!compact ? (
          <>
            <Readout label={tl('lab.run.overruns')}>
              <span className="num text-[12px] text-[var(--g100)]">{run.overruns}</span>
            </Readout>
            <Readout label={tl('lab.run.revision')}>
              <span className="num text-[12px] text-[var(--g100)]">{run.rev}</span>
            </Readout>
          </>
        ) : null}
      </div>
      {run.reason && run.status !== 'running' ? (
        <p className="text-hud-sub text-[var(--g300)]">
          {tl('lab.run.reason')}: {reason ? tl(reason) : run.reason}
        </p>
      ) : null}
      {run.last_error ? (
        <p className="addr break-words text-[var(--g200)]" data-program-error="">
          {tl('lab.run.lastError')}: {run.last_error}
        </p>
      ) : null}
    </div>
  )
}

/** Stop and Resume of a deployment; failures go to the shell's error toast. */
export function RunActions({ vehicleId, run }: { vehicleId: string; run: ProgramRunView }) {
  const tl = useTL()
  const [busy, setBusy] = React.useState(false)
  const call = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await fn()
    } catch (e) {
      notifyError(e)
    } finally {
      setBusy(false)
    }
  }
  const active = run.status === 'running' || run.status === 'paused'
  return (
    <div className="flex items-center gap-2">
      {run.status === 'paused' ? (
        <Button variant="outline" size="sm" disabled={busy} onClick={() => call(() => labApi.resume(vehicleId))} data-program-resume="">
          {tl('lab.run.resume')}
        </Button>
      ) : null}
      {active ? (
        <Button variant="outline" size="sm" disabled={busy} onClick={() => call(() => labApi.stop(vehicleId))} data-program-stop="">
          {tl('lab.run.stop')}
        </Button>
      ) : null}
    </div>
  )
}

/** Programs grouped as Examples and My programs. */
export function ProgramSelect({ programs, value, onChange, label, className }: { programs: readonly ProgramSummary[]; value: string | null; onChange(id: string): void; label: string; className?: string }) {
  const tl = useTL()
  const lang = useLang()
  const items = programs.map((p) => ({ value: p.id, label: programName(p, lang) }))
  const mine = programs.filter((p) => !p.builtin)
  const examples = programs.filter((p) => p.builtin)
  return (
    <Select items={items} value={value} onValueChange={(x) => x && onChange(x as string)}>
      <SelectTrigger size="sm" className={cn('h-7 min-w-0', className)} aria-label={label}>
        <SelectValue placeholder={label} />
      </SelectTrigger>
      <SelectContent>
        {mine.length ? (
          <SelectGroup>
            <SelectLabel>{tl('lab.mine')}</SelectLabel>
            {mine.map((p) => (
              <SelectItem key={p.id} value={p.id}>
                {programName(p, lang)}
              </SelectItem>
            ))}
          </SelectGroup>
        ) : null}
        <SelectGroup>
          <SelectLabel>{tl('lab.examples')}</SelectLabel>
          {examples.map((p) => (
            <SelectItem key={p.id} value={p.id}>
              {programName(p, lang)}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}

/** Aircraft a program can be deployed to: connected, not crashed; real ones only once programs are allowed. */
export function deployable(v: Vehicle): boolean {
  return v.link.state !== 'offline' && v.link.state !== 'lost' && (v.kind === 'sim' || v.programs_allowed)
}
