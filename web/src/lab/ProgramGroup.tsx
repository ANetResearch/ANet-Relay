// Program group of the Fly inspector's Control tab (06 §2.6, §7): the deployment on this aircraft (status, state,
// frame rate, faults; Stop / Resume), a quick deploy of a saved program, Open in Lab, and for real aircraft the
// operator's "Allow programs on this aircraft" switch behind the confirm word PROGRAM (reset by the backend on
// every new connection).
import * as React from 'react'
import { RelayApiError } from '@/api'
import { useLang } from '@/i18n'
import { notify, notifyError } from '@/shell/notify'
import { useVehicle } from '@/store/selectors'
import { useUi } from '@/store/ui'
import { Button } from '@/ui/components/ui/button'
import { Icon } from '@/ui/icons'
import { Group, TypeToConfirm } from '@/ui/relay'
import { labApi } from './api'
import { useTL } from './copy'
import { ProgramSelect, RunActions, RunReadout, STATUS_KEYS } from './parts'
import { useLab } from './store'

const ALLOW_WORD = 'PROGRAM'

function AllowRow({ vehicleId, allowed, label }: { vehicleId: string; allowed: boolean; label: string }) {
  const tl = useTL()
  const lang = useLang()
  const [confirming, setConfirming] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const call = async (on: boolean) => {
    setBusy(true)
    setError(null)
    try {
      await labApi.allow(vehicleId, on, on ? ALLOW_WORD : undefined)
      setConfirming(false)
    } catch (e) {
      if (on) setError(e instanceof RelayApiError ? e.text(lang) : String(e))
      else notifyError(e)
    } finally {
      setBusy(false)
    }
  }
  if (allowed) {
    return (
      <div className="flex min-h-7 items-center gap-2" data-programs-allowed="">
        <span className="min-w-0 flex-1 text-hud-sub text-[var(--g200)]">{tl('lab.grp.allowed')}</span>
        <Button variant="outline" size="sm" disabled={busy} onClick={() => void call(false)}>
          {tl('lab.grp.disallow')}
        </Button>
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-2">
      {confirming ? (
        <TypeToConfirm
          word={ALLOW_WORD}
          prompt={tl('lab.grp.allowPrompt')}
          actionLabel={tl('lab.grp.allowAction')}
          onConfirm={() => call(true)}
          onCancel={() => setConfirming(false)}
          busy={busy}
          error={error}
        />
      ) : (
        <Button variant="outline" size="sm" className="self-start" onClick={() => setConfirming(true)} data-programs-allow="" aria-label={`${tl('lab.grp.allow')} · ${label}`}>
          {tl('lab.grp.allow')}
        </Button>
      )}
      <p className="text-hud-sub text-[var(--g500)]">{tl('lab.grp.allowHint')}</p>
    </div>
  )
}

export function ProgramGroup({ vehicleId }: { vehicleId: string }) {
  const tl = useTL()
  const v = useVehicle(vehicleId)
  const programs = useLab((s) => s.programs)
  const selectedId = useLab((s) => s.selectedId)
  const [choice, setChoice] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)
  React.useEffect(() => {
    if (useLab.getState().programs === null) void useLab.getState().refreshList()
  }, [])
  if (!v) return null
  const run = v.program
  const active = run && (run.status === 'running' || run.status === 'paused')
  const pick = choice ?? run?.id ?? selectedId ?? programs?.find((p) => !p.builtin)?.id ?? programs?.[0]?.id ?? null
  const blocked = v.kind === 'real' && !v.programs_allowed
  const deploy = async () => {
    if (!pick) return
    setBusy(true)
    try {
      await labApi.deploy(v.id, { program: pick, mode: 'pilot' })
      notify(tl('lab.run.deployed', { vehicle: v.label }))
    } catch (e) {
      notifyError(e)
    } finally {
      setBusy(false)
    }
  }
  const openLab = () => {
    const lab = useLab.getState()
    const id = run?.id ?? pick
    lab.setRunVehicle(v.id)
    lab.setTab('run')
    if (id && id !== lab.selectedId) void lab.open(id)
    useUi.getState().setWorkspace('lab')
  }
  return (
    <Group label={tl('lab.grp.title')} right={run ? <span className="text-hud-sub text-[var(--g400)]">{tl(STATUS_KEYS[run.status])}</span> : null}>
      <div className="flex flex-col gap-2.5 px-3" data-program-group={vehicleId}>
        {v.kind === 'real' ? <AllowRow vehicleId={v.id} allowed={v.programs_allowed} label={v.label} /> : null}
        {run ? (
          <>
            <div className="flex min-h-7 items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-[12px] text-[var(--g100)]">
                {run.name} <span className="num text-[var(--g500)]">· {tl('lab.rev', { rev: run.rev })}</span>
              </span>
              <RunActions vehicleId={v.id} run={run} />
            </div>
            <RunReadout run={run} compact />
          </>
        ) : (
          <p className="text-hud-sub text-[var(--g500)]">{tl('lab.grp.none')}</p>
        )}
        {!active ? (
          <div className="flex items-center gap-2">
            <ProgramSelect programs={programs ?? []} value={pick} onChange={setChoice} label={tl('lab.grp.select')} className="flex-1" />
            <Button variant="outline" size="sm" disabled={busy || !pick || blocked} onClick={() => void deploy()} data-program-deploy="">
              <Icon icon="code" />
              {tl('lab.grp.deploy')}
            </Button>
          </div>
        ) : null}
        <Button variant="ghost" size="sm" className="gap-1.5 self-start px-1.5 text-[var(--g300)]" onClick={openLab}>
          <Icon icon="ws.lab" />
          {tl('lab.grp.openLab')}
        </Button>
      </div>
    </Group>
  )
}
