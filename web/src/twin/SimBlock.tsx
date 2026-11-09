// Inspector → Overview → Simulator (11 §6, §8.1, §11; v0.5.4 reset request): the engine (fast simulator or the
// firmware twin badge "固件孪生 ArduCopter 4.6.3 · 精确匹配"), Reset simulator (disarmed or crashed; a twin restarts
// its firmware and the snapshot's twin state shows the progress), the frame editor (PUT /sims/{id}/frame, disarmed
// and on the ground only) and for a twin the details of GET /sims/{id}/twin: pack, process state with the exit
// lines, parameters applied and not applied (with the reasons), the values Relay forces, lockstep figures, the
// profile and Recapture parameters from the aircraft while its source aircraft is connected.
import * as React from 'react'
import { api, type SimTwin, type Vehicle } from '@/api'
import { useLang } from '@/i18n'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/shell/notify'
import { useRelay } from '@/store/relay'
import { useVehicle } from '@/store/selectors'
import { Button } from '@/ui/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/ui/components/ui/collapsible'
import { Input } from '@/ui/components/ui/input'
import { NativeSelect, NativeSelectOption } from '@/ui/components/ui/native-select'
import { Spinner } from '@/ui/components/ui/spinner'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/ui/components/ui/tooltip'
import { Icon, StateIcon } from '@/ui/icons'
import { Block, Kv, KvList } from '@/inspector/parts'
import { usePoll } from '@/inspector/usePoll'
import { resetSim } from './actions'
import {
  FRAME_FIELDS, FRAME_PRESETS, frameBlock, frameBody, frameErrors, frameForm, isPreset, paramValue, PRESET_VALUES, resetBlock, sourceConnected, twinErrorView, twinRestarting,
  type FrameForm, type FramePreset, type FrameValues,
} from './model'
import { EngineBadge, OutputLines } from './parts'
import { useTT } from './strings'

function Reason({ text, children }: { text: string | null; children: React.ReactElement }) {
  if (!text) return children
  return (
    <Tooltip>
      <TooltipTrigger render={<span className="inline-flex" />}>{children}</TooltipTrigger>
      <TooltipContent className="max-w-[260px]">{text}</TooltipContent>
    </Tooltip>
  )
}

/** Reset simulator: enabled for disarmed or crashed simulators; a twin shows "Restarting the firmware" until it runs. */
export function ResetSimButton({ v, size = 'sm', className }: { v: Vehicle; size?: 'sm' | 'xs'; className?: string }) {
  const t = useTT()
  const [sent, setSent] = React.useState(false)
  const block = resetBlock(v)
  const restarting = v.sim?.engine === 'sitl' && twinRestarting(v)
  const busy = sent || restarting
  return (
    <Reason text={block ? t(block.key) : null}>
      <Button
        variant="outline"
        size={size}
        className={cn('gap-1.5', className)}
        disabled={Boolean(block) || busy}
        onClick={() => {
          setSent(true)
          void resetSim(v.id).finally(() => setSent(false))
        }}
        data-reset-sim={v.id}
        data-crashed={v.crashed ? '' : undefined}
      >
        {busy ? <Spinner className="size-3.5" /> : <Icon icon="reset" className="size-3.5" />}
        {restarting ? t('reset.restarting') : t('reset.action')}
      </Button>
    </Reason>
  )
}

/** Frame values the editor starts from: the twin's view, our own last answer, else the preset's. */
function frameValuesOf(preset: string, twin: SimTwin | null, own: (FrameValues & { preset: string; thrust_to_weight?: number }) | null): FrameValues & { preset: FramePreset; thrust_to_weight: number | null } {
  const src = own ?? twin?.frame ?? null
  const p: FramePreset = isPreset(src?.preset ?? preset) ? ((src?.preset ?? preset) as FramePreset) : 'micro-120'
  const base = src ?? PRESET_VALUES[p]
  return {
    preset: p,
    mass_kg: base.mass_kg,
    wheelbase_m: base.wheelbase_m,
    prop_in: base.prop_in,
    cells: base.cells,
    capacity_mah: base.capacity_mah,
    thrust_to_weight: (src as { thrust_to_weight?: number } | null)?.thrust_to_weight ?? null,
  }
}

/** The frame editor; the values in, PUT bodies out. */
export function FrameEditor({ v, twin }: { v: Vehicle; twin: SimTwin | null }) {
  const t = useTT()
  const [own, setOwn] = React.useState<(FrameValues & { preset: string; thrust_to_weight?: number }) | null>(null)
  const cur = frameValuesOf(v.sim?.frame ?? 'micro-120', twin, own)
  const [form, setForm] = React.useState<FrameForm>(() => frameForm(cur.preset, cur))
  const [busy, setBusy] = React.useState(false)
  const [open, setOpen] = React.useState(false)
  const key = `${cur.preset}|${cur.mass_kg}|${cur.wheelbase_m}|${cur.prop_in}|${cur.cells}|${cur.capacity_mah}`
  const [seen, setSeen] = React.useState(key)
  if (seen !== key) {
    setSeen(key)
    setForm(frameForm(cur.preset, cur))
  }
  const block = frameBlock(v)
  const errors = frameErrors(form)
  const body = frameBody(form, cur.preset === form.preset ? cur : PRESET_VALUES[form.preset])
  const dirty = form.preset !== cur.preset || Object.keys(body).length > 1
  const apply = () => {
    if (block || Object.keys(errors).length || busy) return
    setBusy(true)
    api
      .setSimFrame(v.id, body)
      .then((r) => {
        setOwn(r.frame)
        notify(t('frame.applied', { aircraft: v.label, preset: r.frame.preset }))
      })
      .catch(notifyError)
      .finally(() => setBusy(false))
  }
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="flex h-6 w-full items-center gap-2 rounded-sm text-left text-hud-sub text-[var(--g300)] outline-none hover:text-[var(--g100)] focus-visible:ring-1 focus-visible:ring-ring" data-frame-toggle="">
        <Icon icon="sliders" className="size-3.5" />
        <span className="flex-1 truncate">
          {t('frame.title')} · {t(`frame.preset.${cur.preset}`)} · {cur.mass_kg} kg · {cur.wheelbase_m} m
          {cur.thrust_to_weight !== null ? ` · ${t('frame.twr')} ${cur.thrust_to_weight.toFixed(2)}` : ''}
        </span>
        <StateIcon icon="chevron.down" alt={open} className="size-3.5 text-[var(--g400)]" />
      </CollapsibleTrigger>
      <CollapsibleContent className="flex flex-col gap-1.5 pt-1.5" data-frame-editor="">
        <p className="text-hud-sub text-[var(--g500)]">{t('frame.hint')}</p>
        <div className="flex h-7 items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-[12px] text-[var(--g400)]">{t('frame.preset')}</span>
          <NativeSelect value={form.preset} disabled={busy} onChange={(e) => isPreset(e.target.value) && setForm(frameForm(e.target.value))} className="w-36" aria-label={t('frame.preset')} data-frame-preset="">
            {FRAME_PRESETS.map((p) => (
              <NativeSelectOption key={p} value={p}>
                {t(`frame.preset.${p}`)}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </div>
        {FRAME_FIELDS.map((k) => (
          <div key={k} className="flex min-h-7 items-center gap-2" data-frame-field={k}>
            <span className="min-w-0 flex-1 truncate text-[12px] text-[var(--g400)]">{t(`frame.${k}`)}</span>
            {errors[k] ? <span className="text-hud-sub text-[var(--g50)]">{t(errors[k]!.key, errors[k]!.params)}</span> : null}
            <Input
              value={form[k]}
              inputMode="decimal"
              disabled={busy}
              aria-label={t(`frame.${k}`)}
              aria-invalid={errors[k] ? true : undefined}
              onChange={(e) => setForm((f) => ({ ...f, [k]: e.target.value }))}
              onKeyDown={(e) => {
                e.stopPropagation()
                if (e.key === 'Enter') apply()
              }}
              className="num h-7 w-24 text-right text-[12px]"
            />
          </div>
        ))}
        <div className="flex items-center justify-end gap-2 pt-0.5">
          {block ? <span className="min-w-0 flex-1 text-hud-sub text-[var(--g400)]">{t(block.key)}</span> : null}
          <Button variant="outline" size="sm" className="gap-1.5" disabled={Boolean(block) || !dirty || busy || Object.keys(errors).length > 0} onClick={apply} data-frame-apply="">
            {busy ? <Spinner /> : <Icon icon="check" className="size-3.5" />}
            {t('frame.apply')}
          </Button>
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

/** GET /sims/{id}/twin in the inspector. */
export function TwinDetails({ v, twin }: { v: Vehicle; twin: SimTwin }) {
  const t = useTT()
  const lang = useLang()
  const [openEx, setOpenEx] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const sourceId = useProfileSource(twin.profile?.id ?? null)
  const source = useRelay((s) => sourceConnected(s.fleet?.vehicles ?? [], sourceId)?.label ?? null)
  const excluded = twin.params.excluded
  const forced = Object.entries(twin.params.forced).filter((e): e is [string, number] => typeof e[1] === 'number')
  const recapture = () => {
    if (!twin.profile || !sourceId) return
    setBusy(true)
    api
      .captureTwinProfile({ from_vehicle: sourceId, profile: twin.profile.id })
      .then((p) => notify(t('twin.recaptured', { name: p.name, aircraft: source ?? sourceId })))
      .catch((e: unknown) => notify(t(twinErrorView(e, lang).title), { level: 'warn', description: twinErrorView(e, lang).message }))
      .finally(() => setBusy(false))
  }
  const ls = twin.lockstep
  return (
    <div className="flex flex-col gap-2" data-twin-details={twin.state}>
      <KvList>
        <Kv label={t('twin.state')}>
          <span className={cn(twin.state === 'failed' || twin.state === 'exited' ? 'font-[560] text-[var(--g50)]' : undefined)}>{t(`twin.state.${twin.state}`)}</span>
        </Kv>
        <Kv label={t('twin.pack')} value={twin.pack.id} />
        <Kv label={t('twin.profile')} value={twin.profile ? (twin.profile.name ?? twin.profile.id) : t('twin.noProfile')} />
        <Kv label={t('twin.boot')} value={twin.boot_s !== null ? t('twin.seconds', { s: twin.boot_s.toFixed(1) }) : null} reason="—" />
        <Kv label={t('twin.restarts')} value={String(twin.restarts)} />
        <Kv label={t('twin.process')} value={twin.pid !== null ? t('twin.processValue', { pid: twin.pid, n: twin.instance ?? '—' }) : null} reason="—" />
        <Kv label={t('twin.lockstep')} value={t('twin.lockstepValue', { steps: ls.steps, stalls: ls.stalls, frames: ls.frames })} />
        <Kv label={t('twin.msPerStep')} value={ls.ms_per_step !== null ? t('twin.ms', { ms: ls.ms_per_step.toFixed(2) }) : null} reason="—" />
        <Kv label={t('twin.params')} value={t('twin.paramsValue', { applied: twin.params.applied, excluded: excluded.length })} />
      </KvList>
      {excluded.length ? (
        <Collapsible open={openEx} onOpenChange={setOpenEx}>
          <CollapsibleTrigger className="flex h-6 w-full items-center gap-2 rounded-sm text-left text-hud-sub text-[var(--g300)] outline-none hover:text-[var(--g100)] focus-visible:ring-1 focus-visible:ring-ring" data-twin-excluded-toggle="">
            <Icon icon="layers" className="size-3.5" />
            <span className="flex-1">{t('twin.excluded', { n: excluded.length })}</span>
            <StateIcon icon="chevron.down" alt={openEx} className="size-3.5 text-[var(--g400)]" />
          </CollapsibleTrigger>
          <CollapsibleContent>
            <ul className="flex max-h-48 flex-col overflow-y-auto" data-twin-excluded="">
              {excluded.map((p) => (
                <li key={p.name} className="flex items-baseline gap-2 border-b border-dotted border-[var(--border)] py-1 last:border-b-0">
                  <span className="w-32 shrink-0 truncate font-mono text-[11px] text-[var(--g100)]">{p.name}</span>
                  <span className="min-w-0 flex-1 text-hud-sub text-[var(--g400)]">{lang === 'zh' ? p.reason_zh || p.reason_en : p.reason_en}</span>
                </li>
              ))}
            </ul>
          </CollapsibleContent>
        </Collapsible>
      ) : null}
      {forced.length ? (
        <div className="flex flex-col gap-0.5" data-twin-forced="">
          <span className="text-hud-sub text-[var(--g300)]">{t('twin.forced')}</span>
          <span className="font-mono text-[11px] text-[var(--g100)]">{forced.map(([k, x]) => `${k} = ${paramValue(x)}`).join(' · ')}</span>
          <span className="text-hud-sub text-[var(--g500)]">{t('twin.forcedHint')}</span>
        </div>
      ) : null}
      {twin.exit ? (
        <div className="flex flex-col gap-1" data-twin-exit="">
          <span className="text-hud-sub text-[var(--g300)]">
            {t('twin.exit')}
            {twin.exit.code !== null ? ` · ${t('twin.exitCode', { code: twin.exit.code })}` : ''}
          </span>
          {twin.exit.lines.length ? <OutputLines lines={twin.exit.lines} /> : null}
        </div>
      ) : null}
      {source && twin.profile ? (
        <div className="flex flex-col gap-1">
          <span className="text-hud-sub text-[var(--g400)]">{t('twin.recaptureHint', { aircraft: source })}</span>
          <div>
            <Button variant="outline" size="sm" className="gap-1.5" disabled={busy || v.armed} onClick={recapture} data-twin-recapture="">
              {busy ? <Spinner /> : <Icon icon="refresh" className="size-3.5" />}
              {t('twin.recapture')}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  )
}

/** The source aircraft of a twin profile (GET /twins/profiles/{pid} summary fields). */
function useProfileSource(pid: string | null): string | null {
  const poll = usePoll(() => (pid ? api.twinProfile(pid) : Promise.resolve(null)), 0, pid, pid !== null)
  return poll.data?.source.vehicle ?? null
}

/** Simulators only: engine, reset, frame, and the twin details for a firmware twin. */
export function SimBlock({ vehicleId }: { vehicleId: string }) {
  const v = useVehicle(vehicleId)
  if (!v || v.kind !== 'sim') return null
  return <SimBody key={v.id} v={v} /> // keyed: the frame editor and twin state belong to one simulator
}

function SimBody({ v }: { v: Vehicle }) {
  const t = useTT()
  const lang = useLang()
  const twinEngine = v.sim?.engine === 'sitl'
  const state = v.sim?.firmware?.state ?? ''
  const poll = usePoll(() => api.simTwin(v.id), 2000, `${v.id}|${state}|${v.armed}`, twinEngine)
  const twin = twinEngine ? poll.data : null
  const block = resetBlock(v)
  return (
    <Block
      title={t('sim.block')}
      right={v.crashed ? <span className="text-[11px] font-[560] text-[var(--g50)]">{t('reset.crashed')}</span> : null}
      className="gap-2"
    >
      <div className="flex min-h-6 items-center gap-2" data-sim-block={v.id}>
        <span className="text-[12px] text-[var(--g400)]">{t('sim.block.engine')}</span>
        <span className="flex-1" />
        {twinEngine ? <EngineBadge vehicle={v} /> : <span className="text-[12px] text-[var(--g100)]">{t('eng.relay')}</span>}
      </div>
      <div className="flex items-center gap-2">
        <ResetSimButton v={v} />
        <span className="min-w-0 flex-1 text-hud-sub text-[var(--g500)]">{block ? t(block.key) : twinEngine ? t('reset.twinHint') : t('reset.hint')}</span>
      </div>
      <FrameEditor v={v} twin={twin} />
      {twinEngine ? (
        twin ? (
          <TwinDetails v={v} twin={twin} />
        ) : (
          <p className="text-hud-sub text-[var(--g500)]">{poll.error ? t('twin.error', { msg: twinErrorView(poll.error, lang).message }) : t('twin.loading')}</p>
        )
      ) : null}
    </Block>
  )
}
