// Hardware in the loop step dialog (11 §7.2, §11): type HITL to confirm the propellers are off → back up → write
// and reboot → run → restore and verify. Every step's state and detail come live from GET /vehicles/{id}/hitl
// (500 ms while a session is active, 2 s otherwise); Stop restores at any point, and a mismatch after the restore
// is listed with the backup file kept. Starting needs the operator's possession (the backend checks it).
import * as React from 'react'
import { create } from 'zustand'
import { api, type HitlStatus, type HitlStep } from '@/api'
import { duration } from '@/lib/format'
import { cn } from '@/lib/utils'
import { useLang } from '@/i18n'
import { notify } from '@/shell/notify'
import { useVehicle, useVehicleField } from '@/store/selectors'
import { possess } from '@/shell/actions'
import { useT8 } from '@/fleet/copy'
import { Button } from '@/ui/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/ui/components/ui/dialog'
import { Spinner } from '@/ui/components/ui/spinner'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/ui/components/ui/table'
import { Icon } from '@/ui/icons'
import { TypeToConfirm } from '@/ui/relay'
import { usePoll } from '@/inspector/usePoll'
import { changedRows, hitlActive, hitlBusy, hitlCanStart, hitlCanStop, hitlPollMs, hitlSteps, paramValue, twinErrorView, type TwinErrorView } from './model'
import { TwinFailure } from './parts'
import { useTT } from './strings'

export const useHitlDialog = create<{ vehicle: string | null; open(id: string): void; close(): void }>()((set) => ({
  vehicle: null,
  open: (vehicle) => set({ vehicle }),
  close: () => set({ vehicle: null }),
}))

export const openHitlDialog = (id: string) => useHitlDialog.getState().open(id)

function StepIcon({ state }: { state: HitlStep['state'] }) {
  if (state === 'active') return <Spinner className="size-3.5 text-[var(--g100)]" />
  if (state === 'done') return <Icon icon="check.circle" className="size-3.5 text-[var(--g300)]" />
  if (state === 'failed') return <Icon icon="alert.critical" className="size-3.5 text-[var(--g50)]" />
  if (state === 'skipped') return <Icon icon="close" className="size-3.5 text-[var(--g600)]" />
  return <Icon icon="pending" className="size-3.5 text-[var(--g600)]" />
}

/** The step list; pure so it is rendered in tests. */
export function HitlSteps({ status }: { status: HitlStatus | null }) {
  const t = useTT()
  const lang = useLang()
  return (
    <ol className="flex flex-col" data-hitl-steps="">
      {hitlSteps(status).map((s) => {
        const detail = lang === 'zh' ? (s.detail_zh || s.detail) : s.detail
        return (
          <li key={s.key} className="flex min-h-9 items-start gap-2.5 border-b border-dotted border-[var(--border)] py-2 last:border-b-0" data-hitl-step={s.key} data-state={s.state}>
            <span className="flex h-4 w-4 shrink-0 items-center justify-center">
              <StepIcon state={s.state} />
            </span>
            <span className="flex min-w-0 flex-1 flex-col gap-0.5">
              <span className={cn('text-[12px] leading-4', s.state === 'active' || s.state === 'failed' ? 'font-[560] text-[var(--g50)]' : s.state === 'pending' || s.state === 'skipped' ? 'text-[var(--g400)]' : 'text-[var(--g100)]')}>
                {t(`hitl.step.${s.key}`)}
              </span>
              {detail ? <span className="text-hud-sub break-words text-[var(--g400)]">{detail}</span> : null}
            </span>
            <span className="shrink-0 text-[11px] leading-4 text-[var(--g500)]">{t(`hitl.stepState.${s.state}`)}</span>
          </li>
        )
      })}
    </ol>
  )
}

/** Changed parameters and restore mismatches (lieflat log tables). */
export function HitlTables({ status }: { status: HitlStatus }) {
  const t = useTT()
  const changed = changedRows(status.changed)
  return (
    <>
      {changed.length ? (
        <div className="flex flex-col gap-1" data-hitl-changed="">
          <span className="cap">{t('hitl.changed')}</span>
          <Table data-lf-table="" className="text-[12px]">
            <TableHeader>
              <TableRow>
                <TableHead className="h-7 text-hud-cap">{t('hitl.col.name')}</TableHead>
                <TableHead className="h-7 text-hud-cap" data-num="">
                  {t('hitl.col.from')}
                </TableHead>
                <TableHead className="h-7 text-hud-cap" data-num="">
                  {t('hitl.col.to')}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {changed.map((c) => (
                <TableRow key={c.name} className="h-7">
                  <TableCell className="font-mono text-[11px]">{c.name}</TableCell>
                  <TableCell className="num" data-num="">
                    {paramValue(c.from)}
                  </TableCell>
                  <TableCell className="num" data-num="">
                    {paramValue(c.to)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : null}
      {status.mismatches.length ? (
        <div className="flex flex-col gap-1" data-hitl-mismatches="">
          <span className="cap">{t('hitl.mismatches')}</span>
          <p className="text-hud-sub text-[var(--g200)]">{t('hitl.mismatchNote')}</p>
          <Table data-lf-table="" className="text-[12px]">
            <TableHeader>
              <TableRow>
                <TableHead className="h-7 text-hud-cap">{t('hitl.col.name')}</TableHead>
                <TableHead className="h-7 text-hud-cap" data-num="">
                  {t('hitl.col.expected')}
                </TableHead>
                <TableHead className="h-7 text-hud-cap" data-num="">
                  {t('hitl.col.actual')}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {status.mismatches.map((m) => (
                <TableRow key={m.name} className="h-7">
                  <TableCell className="font-mono text-[11px]">{m.name}</TableCell>
                  <TableCell className="num" data-num="">
                    {paramValue(m.expected)}
                  </TableCell>
                  <TableCell className="num" data-num="" data-hot="">
                    {paramValue(m.actual)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : null}
      {status.volatile.length && (status.state === 'restored' || status.state === 'failed') ? (
        <p className="text-hud-sub text-[var(--g400)]" data-hitl-volatile="">
          {t('hitl.volatile', { names: status.volatile.join(', ') })}
        </p>
      ) : null}
    </>
  )
}

function HitlBody({ id, label }: { id: string; label: string }) {
  const t = useTT()
  const t8 = useT8()
  const possessed = useVehicleField(id, (v) => v.controller === 'human' && v.possession !== null) ?? false
  const lang = useLang()
  const [own, setOwn] = React.useState<HitlStatus | null>(null)
  const [busy, setBusy] = React.useState(false)
  const [failure, setFailure] = React.useState<TwinErrorView | null>(null)
  const [every, setEvery] = React.useState(500)
  const poll = usePoll(() => api.hitl(id), every, id)
  const status = poll.data ?? own
  const want = hitlPollMs(status?.state ?? null)
  if (want !== every) setEvery(want)

  const act = (action: 'start' | 'stop') => {
    setBusy(true)
    setFailure(null)
    api
      .hitlAction(id, action, action === 'start' ? 'HITL' : undefined)
      .then((s) => {
        setOwn(s)
        notify(t(action === 'start' ? 'hitl.started' : 'hitl.stopping', { aircraft: label }))
        poll.reload()
      })
      .catch((e: unknown) => setFailure(twinErrorView(e, lang)))
      .finally(() => setBusy(false))
  }

  if (!status) {
    return <p className="px-5 pb-4 text-hud-sub text-[var(--g500)]">{poll.error ? twinErrorView(poll.error, lang).message : t('hitl.loading')}</p>
  }
  const reason = lang === 'zh' ? (status.reason_zh ?? status.reason) : status.reason
  const run = status.run
  const err = status.error
  return (
    <>
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 pb-4" data-hitl-state={status.state}>
        {!status.available && !hitlActive(status.state) ? (
          <div className="flex items-start gap-2 rounded-md border border-[var(--g600)] px-3 py-2.5" data-hitl-unavailable="">
            <Icon icon="alert" className="mt-0.5 size-3.5 shrink-0 text-[var(--g100)]" />
            <span className="flex flex-col gap-0.5">
              <span className="text-[12px] font-[560] text-[var(--g50)]">{t('hitl.unavailable')}</span>
              {reason ? <span className="text-ed-value text-[var(--g200)]">{reason}</span> : null}
              {/* the operator starts HITL from the window while possessing the aircraft */}
              {status.cause === 'possession' && !possessed ? (
                <span className="pt-1.5">
                  <Button variant="outline" size="sm" className="gap-1.5" onClick={() => possess(id)} data-hitl-possess="">
                    <Icon icon="possess" className="size-3.5" />
                    {t8('action.possess')}
                  </Button>
                </span>
              ) : null}
            </span>
          </div>
        ) : null}
        {status.pending_restore && !hitlBusy(status.state) ? <p className="text-ed-value text-[var(--g100)]">{t('hitl.pendingNote')}</p> : null}
        <HitlSteps status={status} />
        {hitlCanStart(status) ? (
          <TypeToConfirm word="HITL" prompt={t('hitl.confirmPrompt')} actionLabel={t('hitl.start')} busy={busy} onConfirm={() => act('start')} autoFocus={false} />
        ) : null}
        {run ? (
          <div className="flex flex-col gap-0.5" data-hitl-run="">
            <span className="cap">{t('hitl.run')}</span>
            <span className="num text-[11px] text-[var(--g100)]">{t('hitl.runValue', { sensor: Math.round(run.sensor_hz), gps: Math.round(run.gps_hz), frames: run.actuator_frames })}</span>
            {/* the session runs at 250 physics steps per second of real time (11 §7.2) */}
            {status.state === 'running' ? <span className="num text-[11px] text-[var(--g400)]">{t('hitl.since', { t: duration(run.steps / 250) ?? '—' })}</span> : null}
          </div>
        ) : null}
        {status.backup ? (
          <div className="flex flex-col gap-0.5" data-hitl-backup="">
            <span className="cap">{t('hitl.backup')}</span>
            <span className="truncate font-mono text-[11px] text-[var(--g300)]" title={status.backup.path ?? status.backup.file} data-selectable="">
              {t('hitl.backupValue', { count: status.backup.count, file: status.backup.file })}
            </span>
          </div>
        ) : null}
        <HitlTables status={status} />
        {err ? <TwinFailure failure={{ code: err.code, title: 'err.generic', message: lang === 'zh' ? err.message_zh || err.message : err.message, lines: [], added: [] }} /> : null}
        {failure ? <TwinFailure failure={failure} /> : null}
      </div>
      <DialogFooter className="shrink-0 border-t border-[var(--border)] px-5 py-3">
        <Button variant="ghost" size="sm" onClick={() => useHitlDialog.getState().close()}>
          {t('hitl.close')}
        </Button>
        {hitlCanStop(status) ? (
          <Button variant="outline" size="sm" disabled={busy} onClick={() => act('stop')} data-hitl-stop="">
            {busy ? <Spinner /> : <Icon icon="stop" />}
            {status.state === 'idle' || status.state === 'restored' || status.state === 'failed' ? t('hitl.restorePending') : t('hitl.stop')}
          </Button>
        ) : null}
      </DialogFooter>
    </>
  )
}

export function HitlDialog() {
  const t = useTT()
  const id = useHitlDialog((s) => s.vehicle)
  const v = useVehicle(id)
  const label = v?.label ?? id ?? ''
  return (
    <Dialog open={id !== null} onOpenChange={(o: boolean) => !o && useHitlDialog.getState().close()}>
      <DialogContent className="l3 flex max-h-[calc(100vh-48px)] flex-col gap-0 overflow-hidden p-0 sm:max-w-[520px]" data-hitl-dialog="">
        <DialogHeader className="shrink-0 gap-1 px-5 pt-5 pb-3">
          <DialogTitle className="text-[15px] font-[560] text-[var(--g50)]">{t('hitl.title', { aircraft: label })}</DialogTitle>
          <DialogDescription className="text-hud-sub text-[var(--g400)]">{t('hitl.description')}</DialogDescription>
        </DialogHeader>
        {id ? <HitlBody key={id} id={id} label={label} /> : null}
      </DialogContent>
    </Dialog>
  )
}
