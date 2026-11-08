// "Start dogfight…" dialog (07 §3, §5), opened from the top-bar Arena menu: the scenario fields of POST
// /scenarios/dogfight (aircraft, teams, gunner and rammer counts and programs, arena radius and ceiling, airframe,
// hover assist, duration, respawn, seed). Local checks (./dogfight) keep an obviously wrong request in the window;
// a refusal by the backend (03 §10 error body, e.g. a real aircraft, no operator rights) is shown in the dialog in
// the window language and the dialog stays open. Programs come from GET /programs, the built-in hunters and rammer
// first. The scenario flies every aircraft through onboard programs with stick-level control only (06 §5).
import * as React from 'react'
import { create } from 'zustand'
import { RelayApiError, type ProgramSummary } from '@/api'
import { useLang } from '@/i18n'
import { labApi } from '@/lab/api'
import { notify } from '@/shell/notify'
import { useUi } from '@/store/ui'
import { Button } from '@/ui/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/ui/components/ui/dialog'
import { Input } from '@/ui/components/ui/input'
import { NativeSelect, NativeSelectOption } from '@/ui/components/ui/native-select'
import { Spinner } from '@/ui/components/ui/spinner'
import { Switch } from '@/ui/components/ui/switch'
import { arenaApi } from './api'
import { refetchBoard, refetchScenario } from './board'
import { useTA, type ArenaKey } from './copy'
import { defaultForm, dogfightBody, validateDogfight, withAircraft, type DogfightField, type DogfightForm } from './dogfight'

export const useDogfightDialog = create<{ open: boolean; setOpen(open: boolean): void }>()((set) => ({
  open: false,
  setOpen: (open) => set({ open }),
}))

/** Opens the dialog on the Fly workspace. */
export function openDogfightDialog(): void {
  useUi.getState().setWorkspace('fly')
  useDogfightDialog.getState().setOpen(true)
}

const FALLBACK_PROGRAMS = ['hunter_easy', 'hunter_medium', 'hunter_hard', 'rammer']

function usePrograms(open: boolean): { id: string; name: string }[] {
  const lang = useLang()
  const [list, setList] = React.useState<ProgramSummary[] | null>(null)
  React.useEffect(() => {
    if (!open) return
    let live = true
    labApi
      .programs()
      .then((r) => live && setList(r.programs ?? []))
      .catch(() => live && setList([]))
    return () => {
      live = false
    }
  }, [open])
  return React.useMemo(() => {
    const seen = new Map<string, string>()
    for (const p of list ?? []) seen.set(p.id, (lang === 'zh' && p.name_zh) || p.name || p.id)
    for (const id of FALLBACK_PROGRAMS) if (!seen.has(id)) seen.set(id, id)
    const rank = (id: string) => (id.startsWith('hunter') ? 0 : id === 'rammer' ? 1 : 2)
    return [...seen].map(([id, name]) => ({ id, name })).sort((a, b) => rank(a.id) - rank(b.id) || a.id.localeCompare(b.id))
  }, [list, lang])
}

function FieldRow({ label, hint, error, children, htmlFor }: { label: string; hint?: string; error?: string | null; htmlFor?: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <label htmlFor={htmlFor} className="text-[11px] leading-4 text-[var(--g300)]">
        {label}
      </label>
      {children}
      {error ? (
        <p className="text-hud-sub text-[var(--g50)]" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className="text-hud-sub text-[var(--g500)]">{hint}</p>
      ) : null}
    </div>
  )
}

function NumberInput({ id, value, onChange, invalid, step }: { id: string; value: string; onChange(v: string): void; invalid?: boolean; step?: string }) {
  return (
    <Input
      id={id}
      value={value}
      inputMode="decimal"
      type="number"
      step={step}
      aria-invalid={invalid || undefined}
      onChange={(e) => onChange(e.target.value)}
      className="num h-7 text-right text-[12px]"
    />
  )
}

export function DogfightDialog() {
  const t = useTA()
  const lang = useLang()
  const open = useDogfightDialog((s) => s.open)
  const setOpen = useDogfightDialog((s) => s.setOpen)
  const [form, setForm] = React.useState<DogfightForm>(defaultForm)
  const [tried, setTried] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const [failure, setFailure] = React.useState<string | null>(null)
  const programs = usePrograms(open)
  const errors = validateDogfight(form)
  const err = (f: DogfightField): string | null => {
    const e = tried ? errors[f] : undefined
    return e ? t(e.key, e.params) : null
  }
  const set = <K extends keyof DogfightForm>(k: K, v: DogfightForm[K]) => setForm((f) => ({ ...f, [k]: v }))

  const submit = () => {
    setTried(true)
    setFailure(null)
    if (Object.keys(validateDogfight(form)).length) return
    setBusy(true)
    const body = dogfightBody(form)
    void arenaApi
      .dogfightStart(body)
      .then(() => {
        notify(t('df.started', { n: body.aircraft }))
        setOpen(false)
        setTried(false)
        void refetchScenario()
        void refetchBoard()
      })
      .catch((e: unknown) => setFailure(e instanceof RelayApiError ? e.text(lang) : e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false))
  }

  const select = (id: string, value: string, onChange: (v: string) => void, options: { value: string; label: string }[]) => (
    <NativeSelect id={id} value={value} onChange={(e) => onChange(e.target.value)} className="w-full">
      {options.map((o) => (
        <NativeSelectOption key={o.value} value={o.value}>
          {o.label}
        </NativeSelectOption>
      ))}
    </NativeSelect>
  )
  const programOptions = programs.map((p) => ({ value: p.id, label: p.name === p.id ? p.id : `${p.name} · ${p.id}` }))
  const airframes: { value: DogfightForm['airframe']; key: ArenaKey }[] = [
    { value: 'ideal', key: 'df.airframe.ideal' },
    { value: 'typical', key: 'df.airframe.typical' },
    { value: 'rough', key: 'df.airframe.rough' },
  ]

  return (
    <Dialog
      open={open}
      onOpenChange={(o: boolean) => {
        setOpen(o)
        if (!o) {
          setFailure(null)
          setTried(false)
        }
      }}
    >
      <DialogContent className="l3 flex max-h-[calc(100vh-48px)] flex-col gap-0 overflow-hidden p-0 sm:max-w-[560px]" data-dogfight-dialog="">
        <DialogHeader className="shrink-0 gap-1 px-5 pt-5 pb-3">
          <DialogTitle className="text-[15px] font-[560] text-[var(--g50)]">{t('df.title')}</DialogTitle>
          <DialogDescription className="text-hud-sub text-[var(--g400)]">{t('df.description')}</DialogDescription>
        </DialogHeader>
        <form
          className="min-h-0 flex-1 overflow-y-auto px-5 pb-4"
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
        >
          <div className="grid grid-cols-2 gap-x-4 gap-y-3">
            <FieldRow label={t('df.aircraft')} hint={t('df.aircraftHint')} error={err('aircraft')} htmlFor="df-aircraft">
              <NumberInput id="df-aircraft" value={form.aircraft} invalid={Boolean(err('aircraft'))} onChange={(v) => setForm((f) => withAircraft(f, v))} />
            </FieldRow>
            <FieldRow label={t('df.teams')} htmlFor="df-teams">
              {select('df-teams', form.teams, (v) => set('teams', v as DogfightForm['teams']), [
                { value: 'ffa', label: t('df.teams.ffa') },
                { value: '2', label: t('df.teams.2') },
                { value: '3', label: t('df.teams.3') },
              ])}
            </FieldRow>
            <FieldRow label={t('df.gunners')} htmlFor="df-gunners" error={err('roles')} hint={t('df.rolesHint')}>
              <NumberInput id="df-gunners" value={form.gunners} invalid={Boolean(err('roles'))} onChange={(v) => set('gunners', v)} />
            </FieldRow>
            <FieldRow label={t('df.rammers')} htmlFor="df-rammers">
              <NumberInput id="df-rammers" value={form.rammers} invalid={Boolean(err('roles'))} onChange={(v) => set('rammers', v)} />
            </FieldRow>
            <FieldRow label={t('df.gunnerProgram')} htmlFor="df-gunner-program">
              {select('df-gunner-program', form.gunnerProgram, (v) => set('gunnerProgram', v), programOptions)}
            </FieldRow>
            <FieldRow label={t('df.rammerProgram')} htmlFor="df-rammer-program">
              {select('df-rammer-program', form.rammerProgram, (v) => set('rammerProgram', v), programOptions)}
            </FieldRow>
            <FieldRow label={t('df.radius')} htmlFor="df-radius" error={err('arena')}>
              <NumberInput id="df-radius" value={form.radius} step="0.5" invalid={Boolean(err('arena'))} onChange={(v) => set('radius', v)} />
            </FieldRow>
            <FieldRow label={t('df.ceiling')} htmlFor="df-ceiling">
              <NumberInput id="df-ceiling" value={form.ceiling} step="0.5" invalid={Boolean(err('arena'))} onChange={(v) => set('ceiling', v)} />
            </FieldRow>
            <FieldRow label={t('df.airframe')} htmlFor="df-airframe">
              {select('df-airframe', form.airframe, (v) => set('airframe', v as DogfightForm['airframe']), airframes.map((a) => ({ value: a.value, label: t(a.key) })))}
            </FieldRow>
            <FieldRow label={t('df.duration')} hint={t('df.durationHint')} error={err('duration')} htmlFor="df-duration">
              <NumberInput id="df-duration" value={form.duration} invalid={Boolean(err('duration'))} onChange={(v) => set('duration', v)} />
            </FieldRow>
            <FieldRow label={t('df.seed')} error={err('seed')} htmlFor="df-seed">
              <NumberInput id="df-seed" value={form.seed} invalid={Boolean(err('seed'))} onChange={(v) => set('seed', v)} />
            </FieldRow>
            <div className="flex flex-col justify-end gap-2">
              <label className="flex items-center justify-between gap-3 text-[12px] text-[var(--g100)]">
                <span className="flex flex-col">
                  <span>{t('df.hoverAssist')}</span>
                  <span className="text-hud-sub text-[var(--g500)]">{t('df.hoverAssistHint')}</span>
                </span>
                <Switch checked={form.hoverAssist} onCheckedChange={(v: boolean) => set('hoverAssist', v)} aria-label={t('df.hoverAssist')} />
              </label>
              <label className="flex items-center justify-between gap-3 text-[12px] text-[var(--g100)]">
                <span>{t('df.respawn')}</span>
                <Switch checked={form.respawn} onCheckedChange={(v: boolean) => set('respawn', v)} aria-label={t('df.respawn')} />
              </label>
            </div>
          </div>
          {failure ? (
            <p className="text-ed-value mt-4 text-[var(--g50)]" role="alert" data-dogfight-error="">
              {failure}
            </p>
          ) : null}
          {/* Enter in a field submits */}
          <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
        </form>
        <DialogFooter className="shrink-0 border-t border-[var(--border)] px-5 py-3">
          <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
            {t('df.cancel')}
          </Button>
          <Button variant="outline" size="sm" disabled={busy} onClick={submit} data-dogfight-start="">
            {busy ? <Spinner /> : null}
            {t('df.start')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
