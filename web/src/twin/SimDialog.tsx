// Add simulated aircraft (11 §11): the engine (fast simulator or firmware twin); for a twin a profile or a pack,
// the recommended one marked with its source ("Recommended: exact match with Quad 01"); the frame preset with mass
// and wheelbase for both engines; an optional home for a twin. A refusal stays in the dialog with the backend's
// bilingual message, and for twin_start_failed the firmware's last output lines (11 §6.3).
import * as React from 'react'
import { api, type AutopilotPack, type TwinProfileSummary, type VehicleFirmware } from '@/api'
import { useLang } from '@/i18n'
import { notify } from '@/shell/notify'
import { useRelay } from '@/store/relay'
import { useUi } from '@/store/ui'
import { Segmented } from '@/control/ControllerSettings'
import { Button } from '@/ui/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/ui/components/ui/dialog'
import { Input } from '@/ui/components/ui/input'
import { NativeSelect, NativeSelectOptGroup, NativeSelectOption } from '@/ui/components/ui/native-select'
import { Spinner } from '@/ui/components/ui/spinner'
import { Switch } from '@/ui/components/ui/switch'
import { Icon } from '@/ui/icons'
import { useSimDialog } from './actions'
import {
  defaultSimForm, defaultSource, FRAME_PRESETS, recommendSource, simBody, sourceOptions, twinErrorView, validateSimForm, withFrame, withSource,
  type Engine, type Recommendation, type SimField, type SimForm, type TwinErrorView,
} from './model'
import { useTT } from './strings'
import { TwinFailure } from './parts'

function FieldRow({ label, hint, error, htmlFor, children, className }: { label: string; hint?: React.ReactNode; error?: string | null; htmlFor?: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={`flex min-w-0 flex-col gap-1 ${className ?? ''}`}>
      <label htmlFor={htmlFor} className="text-[11px] leading-4 text-[var(--g300)]">
        {label}
      </label>
      {children}
      {error ? (
        <p className="text-hud-sub text-[var(--g50)]" role="alert">
          {error}
        </p>
      ) : hint ? (
        <div className="text-hud-sub text-[var(--g500)]">{hint}</div>
      ) : null}
    </div>
  )
}

/** Connected real aircraft, the selected one first; a primitive key keeps the selector stable. */
function useConnected(): { id: string; label: string }[] {
  const key = useRelay((s) =>
    JSON.stringify(
      (s.fleet?.vehicles ?? [])
        .filter((v) => v.kind === 'real' && (v.link.state === 'ok' || v.link.state === 'delayed') && v.firmware_info?.family)
        .map((v) => ({ id: v.id, label: v.label })),
    ),
  )
  return React.useMemo(() => JSON.parse(key) as { id: string; label: string }[], [key])
}

interface Sources {
  profiles: TwinProfileSummary[]
  packs: AutopilotPack[]
  firmware: { vehicle: { id: string; label: string }; match: VehicleFirmware['match'] } | null
  loaded: boolean
}

/** Profiles, packs and the identity match of the selected (else the first connected) real aircraft. */
function useSources(open: boolean, connected: { id: string; label: string }[], selectedId: string | null): Sources {
  const [s, setS] = React.useState<Sources>({ profiles: [], packs: [], firmware: null, loaded: false })
  const target = connected.find((c) => c.id === selectedId) ?? connected[0] ?? null
  const targetId = target?.id ?? null
  const targetLabel = target?.label ?? ''
  React.useEffect(() => {
    if (!open) return
    let alive = true
    void Promise.allSettled([api.twinProfiles(), api.autopilots(), targetId ? api.vehicleFirmware(targetId) : Promise.resolve(null)]).then(([p, a, f]) => {
      if (!alive) return
      setS({
        profiles: p.status === 'fulfilled' ? p.value.profiles : [],
        packs: a.status === 'fulfilled' ? a.value.packs : [],
        firmware: f.status === 'fulfilled' && f.value && targetId ? { vehicle: { id: targetId, label: targetLabel }, match: f.value.match } : null,
        loaded: true,
      })
    })
    return () => {
      alive = false
    }
  }, [open, targetId, targetLabel])
  return s
}

function NumberInput({ id, value, onChange, invalid, step }: { id: string; value: string; onChange(v: string): void; invalid?: boolean; step?: string }) {
  return (
    <Input
      id={id}
      value={value}
      inputMode="decimal"
      step={step}
      aria-invalid={invalid || undefined}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => e.stopPropagation()}
      className="num h-7 text-right text-[12px]"
    />
  )
}

export function SimDialog() {
  const t = useTT()
  const lang = useLang()
  const open = useSimDialog((s) => s.open)
  const preset = useSimDialog((s) => s.preset)
  const setOpen = useSimDialog((s) => s.setOpen)
  const selectedId = useUi((s) => s.selectedId)
  const connected = useConnected()
  const sources = useSources(open, connected, selectedId)
  const [form, setForm] = React.useState<SimForm>(() => defaultSimForm())
  const [tried, setTried] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const [failure, setFailure] = React.useState<TwinErrorView | null>(null)

  const rec: Recommendation | null = React.useMemo(() => recommendSource(sources.profiles, connected, selectedId, sources.firmware), [sources, connected, selectedId])
  const options = React.useMemo(() => sourceOptions(sources.profiles, sources.packs, rec), [sources, rec])

  // a fresh form per opening; the source settles once the lists arrive (the preset's, else the recommended)
  const [seen, setSeen] = React.useState({ open, loaded: sources.loaded })
  if (seen.open !== open || seen.loaded !== sources.loaded) {
    setSeen({ open, loaded: sources.loaded })
    if (open && seen.open !== open) {
      setForm(defaultSimForm(preset.engine ?? 'relay'))
      setTried(false)
      setFailure(null)
    }
    if (open && sources.loaded) {
      const source = preset.source && options.some((o) => o.value === preset.source) ? preset.source : defaultSource(options, rec)
      // a fast simulator keeps its frame; the twin source applies its frame when the engine switches
      setForm((f) => (f.source ? f : f.engine === 'sitl' ? withSource(f, source) : { ...f, source }))
    }
  }

  const errors = validateSimForm(form)
  const err = (k: SimField) => {
    const e = tried ? errors[k] : undefined
    return e ? t(e.key, e.params) : null
  }
  const twin = form.engine === 'sitl'
  const chosen = options.find((o) => o.value === form.source) ?? null
  const chosenProfile = chosen?.kind === 'profile' ? sources.profiles.find((p) => `profile:${p.id}` === chosen.value) ?? null : null
  const chosenMatch = chosenProfile?.match ?? (rec && rec.value === form.source ? rec.match : null)
  const weakMatch = twin && chosenMatch !== null && chosenMatch !== 'exact' && chosenMatch !== 'version'

  const submit = () => {
    setTried(true)
    setFailure(null)
    if (Object.keys(validateSimForm(form)).length || busy) return
    setBusy(true)
    api
      .addSim(simBody(form))
      .then((r) => {
        const added = Array.isArray((r as { added?: unknown }).added) ? ((r as { added: unknown[] }).added.filter((x) => typeof x === 'string') as string[]) : []
        notify(t('sim.added', { sims: added.join(', ') || '—' }))
        if (added[0]) useUi.getState().select(added[0])
        setOpen(false)
      })
      .catch((e: unknown) => setFailure(twinErrorView(e, lang)))
      .finally(() => setBusy(false))
  }

  const profileOpts = options.filter((o) => o.kind === 'profile')
  const packOpts = options.filter((o) => o.kind === 'pack')
  const optionLabel = (o: (typeof options)[number]) => {
    const recText = rec && rec.value === o.value ? ` · ${t('sim.recommended', { match: t(`match.${rec.match}`), aircraft: rec.vehicle })}` : ''
    const problem = o.problem ? ` · ${lang === 'zh' ? o.problem.zh : o.problem.en}` : ''
    return `${o.label}${recText}${problem}`
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o: boolean) => {
        if (!busy || o) setOpen(o)
      }}
    >
      <DialogContent className="l3 flex max-h-[calc(100vh-48px)] flex-col gap-0 overflow-hidden p-0 sm:max-w-[560px]" data-sim-dialog="">
        <DialogHeader className="shrink-0 gap-1 px-5 pt-5 pb-3">
          <DialogTitle className="text-[15px] font-[560] text-[var(--g50)]">{t('sim.title')}</DialogTitle>
          <DialogDescription className="text-hud-sub text-[var(--g400)]">{t('sim.description')}</DialogDescription>
        </DialogHeader>
        <form
          className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 pb-4"
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
        >
          <FieldRow label={t('sim.engine')} hint={t(twin ? 'sim.engine.sitlHint' : 'sim.engine.relayHint')}>
            <div className="flex" data-sim-engine={form.engine}>
              <Segmented<Engine>
                value={form.engine}
                onChange={(engine) => setForm((f) => (engine === f.engine ? f : engine === 'sitl' ? withSource({ ...f, engine }, f.source) : withFrame({ ...f, engine }, f.frame === 'profile' ? 'micro-120' : f.frame)))}
                label={t('sim.engine')}
                options={[
                  { value: 'relay', label: t('eng.relay') },
                  { value: 'sitl', label: t('eng.twin') },
                ]}
              />
            </div>
          </FieldRow>

          <div className="grid grid-cols-[96px_minmax(0,1fr)] gap-x-4 gap-y-3">
            <FieldRow label={t('sim.count')} error={err('count')} htmlFor="sim-count">
              <NumberInput id="sim-count" value={form.count} invalid={Boolean(err('count'))} onChange={(count) => setForm((f) => ({ ...f, count }))} />
            </FieldRow>
            {twin ? (
              <FieldRow
                label={t('sim.source')}
                htmlFor="sim-source"
                error={err('source')}
                hint={
                  options.length === 0 && sources.loaded ? (
                    t('sim.noSources')
                  ) : rec && rec.value === form.source ? (
                    <span className="flex items-center gap-1.5" data-sim-recommended={rec.value}>
                      <Icon icon="check.circle" className="size-3 text-[var(--g300)]" />
                      {t('sim.recommended', { match: t(`match.${rec.match}`), aircraft: rec.vehicle })}
                    </span>
                  ) : undefined
                }
              >
                <NativeSelect id="sim-source" value={form.source} onChange={(e) => setForm((f) => withSource(f, e.target.value))} className="w-full" disabled={!sources.loaded} data-sim-source="">
                  {!form.source ? <NativeSelectOption value="">{sources.loaded ? t('sim.err.source') : '…'}</NativeSelectOption> : null}
                  {profileOpts.length ? (
                    <NativeSelectOptGroup label={t('sim.group.profiles')}>
                      {profileOpts.map((o) => (
                        <NativeSelectOption key={o.value} value={o.value} disabled={o.disabled}>
                          {optionLabel(o)}
                        </NativeSelectOption>
                      ))}
                    </NativeSelectOptGroup>
                  ) : null}
                  {packOpts.length ? (
                    <NativeSelectOptGroup label={t('sim.group.packs')}>
                      {packOpts.map((o) => (
                        <NativeSelectOption key={o.value} value={o.value} disabled={o.disabled}>
                          {optionLabel(o)}
                        </NativeSelectOption>
                      ))}
                    </NativeSelectOptGroup>
                  ) : null}
                </NativeSelect>
              </FieldRow>
            ) : (
              <FieldRow label={t('sim.autopilot')} htmlFor="sim-autopilot">
                <NativeSelect id="sim-autopilot" value={form.autopilot} onChange={(e) => setForm((f) => ({ ...f, autopilot: e.target.value === 'px4' ? 'px4' : 'ardupilot' }))} className="w-full">
                  <NativeSelectOption value="ardupilot">ArduPilot</NativeSelectOption>
                  <NativeSelectOption value="px4">PX4</NativeSelectOption>
                </NativeSelect>
              </FieldRow>
            )}
          </div>
          {weakMatch ? (
            <p className="flex items-start gap-1.5 text-hud-sub text-[var(--g200)]" data-sim-match-warning="">
              <Icon icon="alert" className="mt-0.5 size-3 shrink-0" />
              {t('match.warning')}
            </p>
          ) : null}

          <div className="grid grid-cols-3 gap-x-4 gap-y-3">
            <FieldRow label={t('frame.title')} htmlFor="sim-frame">
              <NativeSelect id="sim-frame" value={form.frame} onChange={(e) => setForm((f) => withFrame(f, e.target.value as SimForm['frame']))} className="w-full" data-sim-frame="">
                {twin && chosenProfile ? <NativeSelectOption value="profile">{t('frame.preset.profile')}</NativeSelectOption> : null}
                {FRAME_PRESETS.map((p) => (
                  <NativeSelectOption key={p} value={p}>
                    {t(`frame.preset.${p}`)}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </FieldRow>
            {twin && form.frame === 'profile' && chosenProfile ? (
              <p className="col-span-2 self-end pb-1.5 text-hud-sub text-[var(--g400)]">
                {`${t(`frame.preset.${chosenProfile.frame.preset}`)} · ${chosenProfile.frame.mass_kg} kg · ${chosenProfile.frame.wheelbase_m} m`}
              </p>
            ) : (
              <>
                <FieldRow label={t('frame.mass_kg')} error={err('mass')} htmlFor="sim-mass">
                  <NumberInput id="sim-mass" value={form.mass} step="0.01" invalid={Boolean(err('mass'))} onChange={(mass) => setForm((f) => ({ ...f, mass }))} />
                </FieldRow>
                <FieldRow label={t('frame.wheelbase_m')} error={err('wheelbase')} htmlFor="sim-wheelbase">
                  <NumberInput id="sim-wheelbase" value={form.wheelbase} step="0.01" invalid={Boolean(err('wheelbase'))} onChange={(wheelbase) => setForm((f) => ({ ...f, wheelbase }))} />
                </FieldRow>
              </>
            )}
          </div>

          {twin ? (
            <div className="flex flex-col gap-2">
              <label className="flex items-center justify-between gap-3 text-[12px] text-[var(--g100)]">
                <span className="flex flex-col">
                  <span>{t('sim.home')}</span>
                  <span className="text-hud-sub text-[var(--g500)]">{t('sim.homeHint')}</span>
                </span>
                <Switch checked={form.homeOn} onCheckedChange={(homeOn: boolean) => setForm((f) => ({ ...f, homeOn }))} aria-label={t('sim.home')} />
              </label>
              {form.homeOn ? (
                <div className="grid grid-cols-3 gap-x-4">
                  <FieldRow label={t('sim.lat')} htmlFor="sim-lat">
                    <NumberInput id="sim-lat" value={form.lat} invalid={Boolean(err('home'))} onChange={(lat) => setForm((f) => ({ ...f, lat }))} />
                  </FieldRow>
                  <FieldRow label={t('sim.lon')} htmlFor="sim-lon">
                    <NumberInput id="sim-lon" value={form.lon} invalid={Boolean(err('home'))} onChange={(lon) => setForm((f) => ({ ...f, lon }))} />
                  </FieldRow>
                  <FieldRow label={t('sim.alt')} htmlFor="sim-alt">
                    <NumberInput id="sim-alt" value={form.alt} invalid={Boolean(err('home'))} onChange={(alt) => setForm((f) => ({ ...f, alt }))} />
                  </FieldRow>
                  {err('home') ? (
                    <p className="col-span-3 pt-1 text-hud-sub text-[var(--g50)]" role="alert">
                      {err('home')}
                    </p>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : null}

          {failure ? <TwinFailure failure={failure} /> : null}
          <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
        </form>
        <DialogFooter className="shrink-0 items-center border-t border-[var(--border)] px-5 py-3">
          {busy && twin ? <span className="mr-auto text-hud-sub text-[var(--g400)]">{t('sim.starting')}</span> : null}
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => setOpen(false)}>
            {t('sim.cancel')}
          </Button>
          <Button variant="outline" size="sm" disabled={busy || (twin && !sources.loaded)} onClick={submit} data-sim-add="">
            {busy ? <Spinner /> : <Icon icon="sim" />}
            {t('sim.add')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
