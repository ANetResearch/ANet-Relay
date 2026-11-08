// Params tab (06 §2.1, §2.6, §7): the program's PARAMS with their ranges (PARAM_RANGES, or the backend's default
// of 10 x the default value). While the program runs on an aircraft the controls tune it live through
// PATCH /vehicles/{id}/program/params (the slow agent's channel into the fast loop); otherwise they edit the stored
// overrides that are saved with the program.
import * as React from 'react'
import type { ProgramRunView, Vehicle } from '@/api'
import { notifyError } from '@/shell/notify'
import { useRelay } from '@/store/relay'
import { Button } from '@/ui/components/ui/button'
import { Input } from '@/ui/components/ui/input'
import { Slider } from '@/ui/components/ui/slider'
import { Switch } from '@/ui/components/ui/switch'
import { Icon } from '@/ui/icons'
import { useShallow } from 'zustand/react/shallow'
import { labApi } from './api'
import { useTL } from './copy'
import { useLab } from './store'

type Value = number | boolean | string

/** Range of a numeric parameter (06 §2.6): PARAM_RANGES, else |value| <= max(10 x |default|, 1) (10 for integers). */
export function paramRange(def: number, bounds: number[] | undefined, integer: boolean): [number, number] {
  if (bounds && bounds.length === 2 && bounds.every(Number.isFinite) && bounds[0] < bounds[1]) return [bounds[0], bounds[1]]
  const span = Math.max(10 * Math.abs(def), integer ? 10 : 1)
  return [-span, span]
}

/** A slider step of about 1/200 of the range, rounded to 1, 2 or 5 x 10^n; 1 for integers. */
export function paramStep(lo: number, hi: number, integer: boolean): number {
  if (integer) return 1
  const raw = (hi - lo) / 200
  const p = 10 ** Math.floor(Math.log10(raw))
  const m = raw / p
  return (m < 1.5 ? 1 : m < 3.5 ? 2 : 5) * p
}

/**
 * Whether PARAMS gives `name` as an integer literal. JSON cannot tell 1.0 from 1, so the source decides: the
 * backend keeps integers integral and floats floating (store.coerce_param).
 */
export function intLiteral(source: string, name: string): boolean {
  const esc = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return new RegExp(`["']${esc}["']\\s*:\\s*-?\\d+\\s*(?:[,}#\\n]|$)`).test(source)
}

const fmt = (v: number, step: number) => {
  const d = Math.max(0, Math.min(6, -Math.floor(Math.log10(step))))
  return v.toFixed(d)
}

/** The live deployment of this program, preferring the aircraft chosen on the Run tab. */
export function useLiveRun(programId: string | null): { vehicle: Pick<Vehicle, 'id' | 'label'>; run: ProgramRunView } | null {
  const runVehicle = useLab((s) => s.runVehicle)
  const key = useRelay(
    useShallow((s) => {
      if (!programId) return [] as string[]
      return (s.fleet?.vehicles ?? [])
        .filter((v) => v.program && v.program.id === programId && (v.program.status === 'running' || v.program.status === 'paused'))
        .map((v) => v.id)
    }),
  )
  const id = key.includes(runVehicle ?? '') ? runVehicle : (key[0] ?? null)
  return useRelay(
    useShallow((s) => {
      const v = id ? s.fleet?.vehicles.find((x) => x.id === id) : undefined
      return v && v.program ? { vehicle: { id: v.id, label: v.label }, run: v.program } : null
    }),
  )
}

function ParamRow({ name, def, value, bounds, integer, onChange }: { name: string; def: Value; value: Value; bounds: number[] | undefined; integer: boolean; onChange(v: Value): void }) {
  const tl = useTL()
  const [drag, setDrag] = React.useState<number | null>(null)
  const [text, setText] = React.useState<string | null>(null)
  if (typeof def === 'boolean') {
    return (
      <div className="flex h-8 items-center gap-2 px-3" data-lab-param={name}>
        <span className="num min-w-0 flex-1 truncate text-[12px] text-[var(--g200)]">{name}</span>
        <Switch checked={value === true} onCheckedChange={(c) => onChange(Boolean(c))} aria-label={name} />
      </div>
    )
  }
  if (typeof def === 'string') {
    return (
      <div className="flex h-8 items-center gap-2 px-3" data-lab-param={name}>
        <span className="num min-w-0 flex-1 truncate text-[12px] text-[var(--g200)]">{name}</span>
        <Input
          value={text ?? String(value)}
          maxLength={32}
          onChange={(e) => setText(e.target.value)}
          onBlur={() => {
            if (text !== null && text !== value) onChange(text)
            setText(null)
          }}
          onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
          className="num h-7 w-36"
          aria-label={name}
        />
      </div>
    )
  }
  const [lo, hi] = paramRange(def, bounds, integer)
  const step = paramStep(lo, hi, integer)
  const num = typeof value === 'number' ? value : def
  const shown = drag ?? num
  const commitText = () => {
    if (text === null) return
    const n = Number(text)
    setText(null)
    if (Number.isFinite(n) && n >= lo && n <= hi && (!integer || Number.isInteger(n)) && n !== num) onChange(n)
  }
  return (
    <div className="flex flex-col gap-1 px-3 py-1.5" data-lab-param={name}>
      <div className="flex items-center gap-2">
        <span className="num min-w-0 flex-1 truncate text-[12px] text-[var(--g100)]">{name}</span>
        <span className="num shrink-0 text-[11px] text-[var(--g500)]">{tl('lab.params.default', { value: fmt(def, step) })}</span>
        <Input
          value={text ?? fmt(shown, step)}
          inputMode="decimal"
          onChange={(e) => setText(e.target.value)}
          onBlur={commitText}
          onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
          className="num h-6 w-20 px-1.5 text-right text-[11px]"
          aria-label={name}
        />
      </div>
      <div className="flex items-center gap-2">
        <span className="num w-10 shrink-0 text-[10px] text-[var(--g600)]">{fmt(lo, step)}</span>
        <Slider
          value={[Math.min(hi, Math.max(lo, shown))]}
          min={lo}
          max={hi}
          step={step}
          onValueChange={(v) => setDrag(Array.isArray(v) ? v[0] : (v as number))}
          onValueCommitted={(v) => {
            setDrag(null)
            const next = Number((Array.isArray(v) ? v[0] : (v as number)).toFixed(6))
            if (next !== num) onChange(integer ? Math.round(next) : next)
          }}
          aria-label={tl('lab.params.range', { min: fmt(lo, step), max: fmt(hi, step) })}
        />
        <span className="num w-10 shrink-0 text-right text-[10px] text-[var(--g600)]">{fmt(hi, step)}</span>
      </div>
    </div>
  )
}

export function ParamsTab() {
  const tl = useTL()
  const program = useLab((s) => s.program)
  const stored = useLab((s) => s.params)
  const validation = useLab((s) => s.validation)
  const source = useLab((s) => s.validatedSource ?? s.source)
  const live = useLiveRun(program?.id ?? null)
  const [pending, setPending] = React.useState<Record<string, Value>>({})
  if (!program) return null
  const defaults = (validation?.params ?? {}) as Record<string, Value>
  const names = Object.keys(defaults)
  const readOnly = program.builtin && !live
  if (!validation) return null
  if (!validation.ok && !names.length) return <p className="px-3 text-hud-sub text-[var(--g400)]">{tl('lab.params.invalid')}</p>
  if (!names.length) return <p className="px-3 text-hud-sub text-[var(--g500)]">{tl('lab.params.none')}</p>
  const liveParams = (live?.run.params ?? {}) as Record<string, Value>
  const valueOf = (k: string): Value => (k in pending ? pending[k] : live ? (liveParams[k] ?? defaults[k]) : ((stored[k] as Value | undefined) ?? defaults[k]))
  const change = async (k: string, v: Value) => {
    if (live) {
      setPending((p) => ({ ...p, [k]: v }))
      try {
        await labApi.params(live.vehicle.id, { [k]: v })
      } catch (e) {
        notifyError(e)
      } finally {
        setPending((p) => {
          const { [k]: _, ...rest } = p
          return rest
        })
      }
    } else if (!readOnly) {
      const next = { ...useLab.getState().params, [k]: v }
      if (v === defaults[k]) delete next[k]
      useLab.getState().setParams(next)
    }
  }
  const overridden = Object.keys(stored).length > 0
  return (
    <div className="flex flex-col gap-2" data-lab-params="">
      <p className="px-3 text-hud-sub text-[var(--g400)]" data-lab-params-mode={live ? 'live' : 'stored'}>
        {live ? tl('lab.params.live', { vehicle: live.vehicle.label }) : program.builtin ? tl('lab.readOnly') : tl('lab.params.stored')}
      </p>
      <div className={readOnly ? 'pointer-events-none opacity-60' : undefined}>
        {names.map((k) => (
          <ParamRow key={k} name={k} def={defaults[k]} value={valueOf(k)} bounds={validation.param_ranges[k]} integer={intLiteral(source, k)} onChange={(v) => void change(k, v)} />
        ))}
      </div>
      <div className="flex items-center gap-2 px-3">
        {live && !program.builtin ? (
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5"
            onClick={() => {
              const next: Record<string, unknown> = {}
              for (const k of names) if (liveParams[k] !== undefined && liveParams[k] !== defaults[k]) next[k] = liveParams[k]
              useLab.getState().setParams(next)
            }}
          >
            <Icon icon="save" />
            {tl('lab.params.keep')}
          </Button>
        ) : null}
        {!live && !program.builtin && overridden ? (
          <Button variant="ghost" size="sm" className="gap-1.5 text-[var(--g300)]" onClick={() => useLab.getState().setParams({})}>
            <Icon icon="reset" />
            {tl('lab.params.reset')}
          </Button>
        ) : null}
      </div>
    </div>
  )
}
