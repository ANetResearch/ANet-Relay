// Run tab (06 §2.6, §7): deploy the saved revision to an aircraft as pilot or copilot, then watch it: status,
// state name, frame rate, faults, and the tail of its log (GET /vehicles/{id}/program/log?after=, polled every
// 500 ms while this tab shows it). Real aircraft take a program only after the operator allowed programs on that
// aircraft in the inspector; taking the sticks pauses the program and only Resume continues it.
import * as React from 'react'
import { useShallow } from 'zustand/react/shallow'
import type { ProgramLogLine, Vehicle } from '@/api'
import { addSim } from '@/shell/actions'
import { notify, notifyError } from '@/shell/notify'
import { useRelay } from '@/store/relay'
import { cn } from '@/lib/utils'
import { Button } from '@/ui/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/ui/components/ui/select'
import { ToggleGroup, ToggleGroupItem } from '@/ui/components/ui/toggle-group'
import { Icon } from '@/ui/icons'
import { SimTag } from '@/ui/relay'
import { labApi } from './api'
import { useTL } from './copy'
import { deployable, RunActions, RunReadout } from './parts'
import { isDirty, useLab } from './store'

const LOG_KEEP = 200
const LOG_POLL_MS = 500

type AircraftOption = Pick<Vehicle, 'id' | 'label' | 'kind' | 'programs_allowed'> & { ok: boolean }

function useAircraft(): AircraftOption[] {
  const key = useRelay(
    useShallow((s) => (s.fleet?.vehicles ?? []).map((v) => `${v.id}\u0000${v.label}\u0000${v.kind}\u0000${v.programs_allowed ? 1 : 0}\u0000${deployable(v) ? 1 : 0}`)),
  )
  return React.useMemo(
    () =>
      key
        .map((k) => {
          const [id, label, kind, allowed, ok] = k.split('\u0000')
          return { id, label, kind: kind as Vehicle['kind'], programs_allowed: allowed === '1', ok: ok === '1' }
        })
        .sort((a, b) => (a.kind === b.kind ? a.label.localeCompare(b.label) : a.kind === 'sim' ? -1 : 1)),
    [key],
  )
}

/** Log lines of the deployment on one aircraft, appended from `after` every 500 ms. */
export function useProgramLog(vehicleId: string | null, enabled: boolean): ProgramLogLine[] {
  const [lines, setLines] = React.useState<ProgramLogLine[]>([])
  const [seen, setSeen] = React.useState(vehicleId)
  if (seen !== vehicleId) {
    setSeen(vehicleId)
    setLines([])
  }
  React.useEffect(() => {
    if (!vehicleId || !enabled) return
    let alive = true
    let after = 0
    let timer: ReturnType<typeof setTimeout> | null = null
    const poll = async () => {
      try {
        const res = await labApi.log(vehicleId, after)
        if (!alive) return
        if (res.last_seq < after) after = 0 // a new deployment restarted the ring
        if (res.lines.length) {
          after = res.lines[res.lines.length - 1].seq
          setLines((l) => [...l, ...res.lines].slice(-LOG_KEEP))
        }
      } catch {
        /* no deployment yet, or the link is down: keep polling */
      }
      if (alive) timer = setTimeout(poll, LOG_POLL_MS)
    }
    void poll()
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
    }
  }, [vehicleId, enabled])
  return lines
}

function LogTail({ lines }: { lines: ProgramLogLine[] }) {
  const tl = useTL()
  const ref = React.useRef<HTMLDivElement>(null)
  React.useLayoutEffect(() => {
    const el = ref.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lines])
  return (
    <div className="flex flex-col gap-1 px-3">
      <span className="cap">{tl('lab.run.log')}</span>
      <div ref={ref} className="lab-log" data-lab-log="" data-selectable="">
        {lines.length === 0 ? (
          <span className="text-[var(--g500)]">{tl('lab.run.logEmpty')}</span>
        ) : (
          lines.map((l) => (
            <div key={l.seq} className={cn('lab-log-line', l.level === 'error' && 'lab-log-error', l.level === 'event' && 'lab-log-event')}>
              <span className="text-[var(--g600)]">{l.t.toFixed(2).padStart(7)}</span> {l.text}
            </div>
          ))
        )}
      </div>
    </div>
  )
}

export function RunTab() {
  const tl = useTL()
  const program = useLab((s) => s.program)
  const dirty = useLab((s) => isDirty(s))
  const chosen = useLab((s) => s.runVehicle)
  const aircraft = useAircraft()
  const [mode, setMode] = React.useState<'pilot' | 'copilot'>('pilot')
  const [busy, setBusy] = React.useState(false)
  const vid = chosen && aircraft.some((a) => a.id === chosen) ? chosen : (aircraft.find((a) => a.kind === 'sim')?.id ?? aircraft[0]?.id ?? null)
  const target = aircraft.find((a) => a.id === vid) ?? null
  const run = useRelay((s) => (vid ? (s.fleet?.vehicles.find((v) => v.id === vid)?.program ?? null) : null))
  const lines = useProgramLog(vid, Boolean(run))
  if (!program) return null
  if (!aircraft.length) {
    return (
      <div className="flex flex-col items-start gap-2 px-3">
        <p className="text-hud-sub text-[var(--g400)]">{tl('lab.run.noAircraft')}</p>
        <Button variant="outline" size="sm" className="gap-1.5" onClick={addSim}>
          <Icon icon="sim" />
          {tl('lab.run.addSim')}
        </Button>
      </div>
    )
  }
  const mine = run && run.id === program.id
  const active = run && (run.status === 'running' || run.status === 'paused')
  const blocked = target ? target.kind === 'real' && !target.programs_allowed : true
  const deploy = async () => {
    if (!vid || !target) return
    setBusy(true)
    try {
      await labApi.deploy(vid, { program: program.id, rev: program.rev, mode })
      notify(tl('lab.run.deployed', { vehicle: target.label }))
    } catch (e) {
      notifyError(e)
    } finally {
      setBusy(false)
    }
  }
  const items = aircraft.map((a) => ({ value: a.id, label: a.label }))
  return (
    <div className="flex flex-col gap-3" data-lab-run="">
      <div className="flex flex-col gap-1 px-3">
        <span className="cap">{tl('lab.run.aircraft')}</span>
        <Select items={items} value={vid} onValueChange={(x) => x && useLab.getState().setRunVehicle(x as string)}>
          <SelectTrigger size="sm" className="h-7 w-full" aria-label={tl('lab.run.pick')}>
            <SelectValue placeholder={tl('lab.run.pick')} />
          </SelectTrigger>
          <SelectContent>
            {aircraft.map((a) => (
              <SelectItem key={a.id} value={a.id}>
                <span className="flex items-center gap-1.5">
                  {a.kind === 'sim' ? <SimTag /> : null}
                  {a.label}
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="flex flex-col gap-1 px-3">
        <span className="cap">{tl('lab.run.mode')}</span>
        <ToggleGroup value={[mode]} onValueChange={(v: unknown[]) => v[0] && setMode(v[0] as 'pilot' | 'copilot')} spacing={0} className="w-full rounded-md bg-[var(--g950)] p-0.5 ring-1 ring-[var(--border)]">
          {(['pilot', 'copilot'] as const).map((m) => (
            <ToggleGroupItem key={m} value={m} className="h-6 flex-1 rounded-[5px] text-[12px] text-[var(--g400)] hover:bg-transparent data-[pressed]:text-[var(--g50)]">
              {tl(m === 'pilot' ? 'lab.run.pilot' : 'lab.run.copilot')}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <span className="text-hud-sub text-[var(--g500)]">{tl(mode === 'pilot' ? 'lab.run.pilotHint' : 'lab.run.copilotHint')}</span>
      </div>
      <div className="flex flex-col gap-1.5 px-3">
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" className="gap-1.5" disabled={busy || dirty || blocked} onClick={() => void deploy()} data-lab-deploy="">
            <Icon icon="code" />
            {tl('lab.run.deploy', { rev: program.rev })}
          </Button>
          <span className="flex-1" />
          {run && vid ? <RunActions vehicleId={vid} run={run} /> : null}
        </div>
        {dirty ? <p className="text-hud-sub text-[var(--g400)]">{tl('lab.run.saveFirst')}</p> : null}
        {blocked && target ? <p className="text-hud-sub text-[var(--g400)]">{tl('lab.run.realBlocked')}</p> : null}
      </div>
      <div className="flex flex-col gap-2 border-t border-[var(--border)] px-3 pt-3">
        {run ? (
          <>
            {!mine && active ? <p className="text-hud-sub text-[var(--g300)]">{tl('lab.run.other', { name: run.name })}</p> : null}
            <RunReadout run={run} />
            {active ? <p className="text-hud-sub text-[var(--g500)]">{tl('lab.run.possessHint')}</p> : null}
          </>
        ) : (
          <p className="text-hud-sub text-[var(--g500)]">{tl('lab.run.idle')}</p>
        )}
      </div>
      {run ? <LogTail lines={lines} /> : null}
    </div>
  )
}
