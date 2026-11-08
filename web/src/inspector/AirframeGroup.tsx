// Inspector → Overview → Airframe (06 §4, WP-2a): the assembly defects of a simulated aircraft. Preset, seed,
// one slider per value and Randomize. The values come from GET /sims/{vid}/airframe (operator or controller);
// the snapshot only names the preset and seed. They act only with hover assist off.
import * as React from 'react'
import { api, RelayApiError, type AirframeBody, type SimAirframe } from '@/api'
import { cn } from '@/lib/utils'
import { notifyError } from '@/shell/notify'
import { useVehicle } from '@/store/selectors'
import { Button } from '@/ui/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/ui/components/ui/collapsible'
import { Input } from '@/ui/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/ui/components/ui/select'
import { Slider } from '@/ui/components/ui/slider'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/ui/components/ui/tooltip'
import { Icon, StateIcon } from '@/ui/icons'
import { useAT, type AirframeKey } from './airframe.strings'
import { Block } from './parts'
import { usePoll } from './usePoll'

type Preset = SimAirframe['preset']
type Params = SimAirframe['params']
const PRESETS: readonly Preset[] = ['ideal', 'typical', 'rough', 'custom']
const SEED_MAX = 2 ** 31 - 1

/** One slider: which value it moves, its range key in `limits`, step and how the number reads. */
export interface AirframeField {
  id: string
  label: AirframeKey
  labelParams?: Record<string, string | number>
  limit: string
  step: number
  get(p: Params): number
  patch(p: Params, v: number): Partial<Params>
  text(v: number): string
}

const vec = (arr: readonly number[], i: number, v: number): number[] => arr.map((x, j) => (j === i ? v : x))
const signed = (digits: number, unit: string) => (v: number) => `${v > 0 ? '+' : ''}${v.toFixed(digits)} ${unit}`

export const AIRFRAME_GROUPS: { title: AirframeKey; fields: AirframeField[] }[] = [
  {
    title: 'af.group.weight',
    fields: [
      { id: 'mass_kg', label: 'af.p.mass_kg', limit: 'mass_kg', step: 0.005, get: (p) => p.mass_kg, patch: (_p, v) => ({ mass_kg: v }), text: (v) => `${Math.round(v * 1000)} g` },
      { id: 'payload_kg', label: 'af.p.payload_kg', limit: 'payload_kg', step: 0.005, get: (p) => p.payload_kg, patch: (_p, v) => ({ payload_kg: v }), text: (v) => `${Math.round(v * 1000)} g` },
    ],
  },
  {
    title: 'af.group.balance',
    fields: [
      { id: 'cg_x', label: 'af.p.cg_x', limit: 'cg_offset_cm', step: 0.05, get: (p) => p.cg_offset_cm[0] ?? 0, patch: (p, v) => ({ cg_offset_cm: vec(p.cg_offset_cm, 0, v) }), text: signed(2, 'cm') },
      { id: 'cg_y', label: 'af.p.cg_y', limit: 'cg_offset_cm', step: 0.05, get: (p) => p.cg_offset_cm[1] ?? 0, patch: (p, v) => ({ cg_offset_cm: vec(p.cg_offset_cm, 1, v) }), text: signed(2, 'cm') },
      { id: 'trim_roll', label: 'af.p.trim_roll', limit: 'level_trim_deg', step: 0.1, get: (p) => p.level_trim_deg[0] ?? 0, patch: (p, v) => ({ level_trim_deg: vec(p.level_trim_deg, 0, v) }), text: signed(1, '°') },
      { id: 'trim_pitch', label: 'af.p.trim_pitch', limit: 'level_trim_deg', step: 0.1, get: (p) => p.level_trim_deg[1] ?? 0, patch: (p, v) => ({ level_trim_deg: vec(p.level_trim_deg, 1, v) }), text: signed(1, '°') },
    ],
  },
  {
    title: 'af.group.motors',
    fields: [
      ...[0, 1, 2, 3].map(
        (i): AirframeField => ({
          id: `motor_${i + 1}`,
          label: 'af.p.motor',
          labelParams: { n: i + 1 },
          limit: 'motor_gain',
          step: 0.005,
          get: (p) => p.motor_gain[i] ?? 0,
          patch: (p, v) => ({ motor_gain: vec(p.motor_gain, i, v) }),
          text: (v) => signed(1, '%')(v * 100),
        }),
      ),
      { id: 'prop_damage', label: 'af.p.prop_damage', limit: 'prop_damage', step: 0.05, get: (p) => p.prop_damage, patch: (_p, v) => ({ prop_damage: v }), text: (v) => `${Math.round(v * 100)} %` },
    ],
  },
  {
    title: 'af.group.sensors',
    fields: [{ id: 'imu_noise', label: 'af.p.imu_noise', limit: 'imu_noise', step: 0.1, get: (p) => p.imu_noise, patch: (_p, v) => ({ imu_noise: v }), text: (v) => `× ${v.toFixed(1)}` }],
  },
]

/** The body Randomize sends: a fresh seed for a generated preset; ideal and custom roll a typical airframe. */
export function randomizeBody(preset: Preset, seed: number): AirframeBody {
  return { preset: preset === 'rough' ? 'rough' : 'typical', seed }
}

/** The body choosing a preset sends: the current seed, so switching back and forth is repeatable. */
export function presetBody(preset: Preset, seed: number): AirframeBody {
  return preset === 'custom' ? { preset } : { preset, seed }
}

function randomSeed(): number {
  const buf = new Uint32Array(1)
  globalThis.crypto.getRandomValues(buf)
  return buf[0] % (SEED_MAX + 1)
}

function SliderRow({ field, params, limits, disabled, onCommit }: { field: AirframeField; params: Params; limits: SimAirframe['limits']; disabled: boolean; onCommit(patch: Partial<Params>): void }) {
  const t = useAT()
  const [drag, setDrag] = React.useState<number | null>(null)
  const [min, max] = limits[field.limit] ?? [0, 1]
  const value = drag ?? field.get(params)
  const label = t(field.label, field.labelParams)
  return (
    <div className="flex h-7 items-center gap-2" data-airframe-field={field.id}>
      <span className="min-w-0 flex-1 truncate text-[12px] text-[var(--g400)]">{label}</span>
      <div className="w-28 shrink-0">
        <Slider
          value={[value]}
          min={min}
          max={max}
          step={field.step}
          disabled={disabled}
          onValueChange={(v) => setDrag(Array.isArray(v) ? v[0] : (v as number))}
          onValueCommitted={(v) => {
            setDrag(null)
            const next = Array.isArray(v) ? v[0] : (v as number)
            if (next !== field.get(params)) onCommit(field.patch(params, Number(next.toFixed(4))))
          }}
          aria-label={label}
        />
      </div>
      <span className="num w-16 shrink-0 text-right text-[11px] text-[var(--g100)]">{field.text(value)}</span>
    </div>
  )
}

function SeedField({ seed, disabled, onApply }: { seed: number; disabled: boolean; onApply(seed: number): void }) {
  const t = useAT()
  const [text, setText] = React.useState(String(seed)) // remounted per seed (key)
  const commit = () => {
    const n = Number(text)
    if (Number.isInteger(n) && n >= 0 && n <= SEED_MAX && n !== seed) onApply(n)
    else setText(String(seed))
  }
  return (
    <Input
      value={text}
      inputMode="numeric"
      disabled={disabled}
      aria-label={t('af.seed')}
      className="num h-7 w-28 text-right"
      onChange={(e) => setText(e.target.value.replace(/[^0-9]/g, '').slice(0, 10))}
      onKeyDown={(e) => {
        e.stopPropagation() // keep flight hotkeys out of the field
        if (e.key === 'Enter') commit()
        if (e.key === 'Escape') setText(String(seed))
      }}
      onBlur={commit}
      data-airframe-seed=""
    />
  )
}

function AirframeBodyView({ id, refKey, assist }: { id: string; refKey: string; assist: boolean }) {
  const t = useAT()
  const key = `${id}|${refKey}|${assist}`
  const poll = usePoll(() => api.simAirframe(id), 0, key)
  const [own, setOwn] = React.useState<{ key: string; data: SimAirframe } | null>(null)
  const [busy, setBusy] = React.useState(false)
  const data = own && own.key === key ? own.data : poll.data

  const send = (body: AirframeBody) => {
    setBusy(true)
    api
      .setSimAirframe(id, body)
      .then((r) => setOwn({ key, data: r }))
      .catch(notifyError)
      .finally(() => setBusy(false))
  }

  if (!data) {
    const err = poll.error
    const denied = err instanceof RelayApiError && (err.status === 403 || err.status === 409)
    return (
      <p className="text-hud-sub text-[var(--g500)]" data-airframe-state={err ? 'error' : 'loading'}>
        {err ? (denied ? t('af.denied') : t('af.error', { msg: err instanceof Error ? err.message : String(err) })) : t('af.loading')}
      </p>
    )
  }
  return <AirframeEditor data={data} busy={busy} send={send} />
}

/** The editor itself (pure: the values in, request bodies out). */
export function AirframeEditor({ data, busy, send, defaultOpen = false }: { data: SimAirframe; busy: boolean; send(body: AirframeBody): void; defaultOpen?: boolean }) {
  const t = useAT()
  const [open, setOpen] = React.useState(defaultOpen)
  const p = data.params
  return (
    <div className="flex flex-col gap-2" data-airframe={data.preset}>
      <p className="text-hud-sub text-[var(--g400)]">{t('af.note')}</p>
      <div className="flex h-8 items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-[12px] text-[var(--g400)]">{t('af.preset')}</span>
        <Select
          value={data.preset}
          onValueChange={(v: unknown) => typeof v === 'string' && v !== data.preset && send(presetBody(v as Preset, data.seed))}
          disabled={busy}
          items={PRESETS.map((k) => ({ value: k, label: t(`af.preset.${k}` as AirframeKey) }))}
        >
          <SelectTrigger className="h-7 w-28" aria-label={t('af.preset')} data-airframe-preset="">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PRESETS.map((k) => (
              <SelectItem key={k} value={k}>
                {t(`af.preset.${k}` as AirframeKey)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="flex h-8 items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-[12px] text-[var(--g400)]">{t('af.seed')}</span>
        <SeedField key={data.seed} seed={data.seed} disabled={busy} onApply={(seed) => send({ seed })} />
      </div>
      <div className="flex h-8 items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-[12px] text-[var(--g400)]">{t('af.hover')}</span>
        <span className={cn('num text-[12px]', data.hover_throttle === null ? 'font-[560] text-[var(--g50)]' : 'text-[var(--g100)]')} data-airframe-hover="">
          {data.hover_throttle === null ? t('af.cannotLift') : t('af.hoverValue', { v: Math.round(data.hover_throttle * 100) })}
        </span>
        <Tooltip>
          <TooltipTrigger
            render={
              <Button variant="outline" size="sm" className="gap-1.5" disabled={busy} onClick={() => send(randomizeBody(data.preset, randomSeed()))} data-airframe-randomize="" />
            }
          >
            <Icon icon="refresh" className="size-3.5" />
            {t('af.randomize')}
          </TooltipTrigger>
          <TooltipContent side="bottom">{t('af.randomizeHint')}</TooltipContent>
        </Tooltip>
      </div>
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger className="flex h-6 w-full items-center gap-2 rounded-sm text-left text-hud-sub text-[var(--g300)] outline-none hover:text-[var(--g100)] focus-visible:ring-1 focus-visible:ring-ring" data-airframe-values="">
          <Icon icon="sliders" className="size-3.5" />
          <span className="flex-1">{t('af.values')}</span>
          <StateIcon icon="chevron.down" alt={open} className="size-3.5 text-[var(--g400)]" />
        </CollapsibleTrigger>
        <CollapsibleContent className="flex flex-col gap-2 pt-1">
          {AIRFRAME_GROUPS.map((g) => (
            <div key={g.title} className="flex flex-col border-t border-[var(--border)] pt-1.5">
              <span className="cap pb-0.5">{t(g.title)}</span>
              {g.fields.map((f) => (
                <SliderRow key={f.id} field={f} params={p} limits={data.limits} disabled={busy} onCommit={(patch) => send({ params: patch })} />
              ))}
              {g.title === 'af.group.motors' ? (
                <div className="flex h-7 items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-[12px] text-[var(--g400)]">{t('af.p.damaged_prop')}</span>
                  <Select
                    value={String(p.damaged_prop)}
                    onValueChange={(v: unknown) => typeof v === 'string' && Number(v) !== p.damaged_prop && send({ params: { damaged_prop: Number(v) } })}
                    disabled={busy}
                    items={[1, 2, 3, 4].map((n) => ({ value: String(n), label: t('af.p.damaged_propValue', { n }) }))}
                  >
                    <SelectTrigger className="h-7 w-28" aria-label={t('af.p.damaged_prop')} data-airframe-prop="">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {[1, 2, 3, 4].map((n) => (
                        <SelectItem key={n} value={String(n)}>
                          {t('af.p.damaged_propValue', { n })}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              ) : null}
            </div>
          ))}
        </CollapsibleContent>
      </Collapsible>
    </div>
  )
}

/** Shown for simulators only; the body mounts per (aircraft, preset, seed, assist) so it refetches on change. */
export function AirframeGroup({ vehicleId }: { vehicleId: string }) {
  const t = useAT()
  const v = useVehicle(vehicleId)
  if (!v || v.kind !== 'sim' || !v.sim) return null
  const sim = v.sim
  const active = !sim.hover_assist
  return (
    <Block
      title={t('af.title')}
      right={
        <span className={cn('text-[11px]', active ? 'font-[560] text-[var(--g50)]' : 'text-[var(--g400)]')} data-airframe-active={active ? '' : undefined}>
          {active ? t('af.state.active') : t('af.state.waiting')}
        </span>
      }
    >
      <AirframeBodyView id={v.id} refKey={`${sim.airframe.preset}:${sim.airframe.seed}`} assist={sim.hover_assist} />
    </Block>
  )
}
