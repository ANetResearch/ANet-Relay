// Inspector Control tab: flight actions and the sequence editor (03 §5.7, §5.8). Actions start one skill each;
// a sequence is a plan the backend flies step by step. Both need possession, like every operator command; moving a
// stick by more than the pilot-override threshold takes over and stops the running skill or sequence.
import * as React from 'react'
import { api, RelayApiError, type PlanAction, type PlanStep, type SkillName, type Vehicle } from '@/api'
import { useLang } from '@/i18n'
import { cn } from '@/lib/utils'
import { notifyError } from '@/shell/notify'
import { Button } from '@/ui/components/ui/button'
import { Input } from '@/ui/components/ui/input'
import { NativeSelect, NativeSelectOption } from '@/ui/components/ui/native-select'
import { Icon, type IconKey } from '@/ui/icons'
import { Group, TypeToConfirm } from '@/ui/relay'
import { useCT, type ControlKey } from './strings'

const PLAN_KEY = 'relay.plan.steps'

/** The owner's bench routine: take off, hover, forward 1 m, back 1 m, turn 45°, land. */
export const EXAMPLE_PLAN: PlanStep[] = [
  { action: 'takeoff', alt: 1.0 },
  { action: 'hover', seconds: 3 },
  { action: 'move', forward: 1 },
  { action: 'move', forward: -1 },
  { action: 'yaw', deg: 45 },
  { action: 'land' },
]

const EDITABLE: PlanAction[] = ['takeoff', 'hover', 'move', 'yaw', 'land']

function loadPlan(): PlanStep[] {
  try {
    const raw = localStorage.getItem(PLAN_KEY)
    const parsed = raw ? (JSON.parse(raw) as PlanStep[]) : null
    if (Array.isArray(parsed) && parsed.every((s) => s && EDITABLE.includes(s.action))) return parsed
  } catch {
    /* storage unavailable or malformed: fall back to the example */
  }
  return EXAMPLE_PLAN
}

function savePlan(steps: PlanStep[]): void {
  try {
    localStorage.setItem(PLAN_KEY, JSON.stringify(steps))
  } catch {
    /* per-viewer convenience only */
  }
}

function useRun() {
  const [busy, setBusy] = React.useState<string | null>(null)
  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key)
    try {
      await fn()
      return true
    } catch (e) {
      notifyError(e)
      return false
    } finally {
      setBusy(null)
    }
  }
  return { busy, run }
}

function NumberField({ label, unit, value, onChange, step = 0.1, min, max, className }: {
  label: string
  unit: string
  value: number
  onChange: (n: number) => void
  step?: number
  min?: number
  max?: number
  className?: string
}) {
  const [text, setText] = React.useState(String(value))
  const [shown, setShown] = React.useState(value)
  if (value !== shown) {
    // the value changed from outside (example loaded, step replaced): show it
    setShown(value)
    setText(String(value))
  }
  return (
    <label className={cn('flex items-center gap-1 text-hud-sub text-[var(--g400)]', className)}>
      <span className="whitespace-nowrap">{label}</span>
      <Input
        type="number"
        inputMode="decimal"
        step={step}
        min={min}
        max={max}
        value={text}
        onChange={(e) => {
          setText(e.target.value)
          const n = Number(e.target.value)
          if (e.target.value !== '' && Number.isFinite(n)) {
            setShown(n)
            onChange(n)
          }
        }}
        className="num h-6 w-14 px-1.5 font-mono text-[11px]"
      />
      <span>{unit}</span>
    </label>
  )
}

function ActionButton({ icon, label, onClick, disabled, testId }: { icon: IconKey; label: string; onClick: () => void; disabled: boolean; testId: string }) {
  return (
    <Button variant="outline" size="sm" disabled={disabled} onClick={onClick} data-action={testId} title={label}>
      <Icon icon={icon} />
      {label}
    </Button>
  )
}

export function ActionsBlock({ v }: { v: Vehicle }) {
  const ct = useCT()
  const lang = useLang()
  const { busy, run } = useRun()
  const [alt, setAlt] = React.useState(1.0)
  const [stepM, setStepM] = React.useState(1.0)
  const [turn, setTurn] = React.useState(45)
  const [confirming, setConfirming] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const human = v.controller === 'human'
  const althold = v.capabilities.althold
  const airborne = v.armed && (v.landed === 'air' || v.landed === 'takeoff' || v.landed === 'landing')
  const off = !human || busy !== null

  const skill = (name: SkillName, args?: Record<string, number | string>) => run(name, () => api.skill(v.id, name, args))
  const takeoff = () => {
    if (v.kind === 'real' && !v.armed) {
      setError(null)
      setConfirming(true)
      return
    }
    void skill('takeoff', { alt })
  }
  const confirmTakeoff = async () => {
    try {
      await api.skill(v.id, 'takeoff', { alt, confirm: 'ARM' })
      setConfirming(false)
    } catch (e) {
      setError(e instanceof RelayApiError ? e.text(lang) : String(e))
    }
  }
  const move = (forward: number, right: number, up = 0) => void skill('move', { forward, right, up })

  return (
    <Group label={ct('act.title')}>
      <div className="flex flex-col gap-2 px-3" data-actions-block="">
        <div className="flex flex-wrap items-center gap-1.5">
          <ActionButton icon="takeoff" label={ct('act.takeoff')} onClick={takeoff} disabled={off || !althold || airborne} testId="takeoff" />
          <NumberField label={ct('act.alt')} unit={ct('plan.unit.m')} value={alt} onChange={setAlt} min={0.2} max={3} />
          <span className="flex-1" />
          <ActionButton icon="hover" label={ct('act.hover')} onClick={() => void skill('hold')} disabled={off || !althold || !airborne} testId="hover" />
          <ActionButton icon="land" label={ct('act.land')} onClick={() => void skill('land')} disabled={off || !v.armed} testId="land" />
        </div>
        {confirming && !v.armed && human ? (
          <TypeToConfirm
            word="ARM"
            prompt={ct('act.confirmTakeoff', { aircraft: v.label })}
            actionLabel={ct('act.takeoff')}
            onConfirm={confirmTakeoff}
            onCancel={() => setConfirming(false)}
            error={error}
          />
        ) : null}
        <div className="grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-1.5">
          <div className="grid grid-cols-3 gap-1">
            <span />
            <Button variant="outline" size="icon-sm" disabled={off || !airborne} onClick={() => move(stepM, 0)} title={ct('act.forward')} data-action="forward">
              <Icon icon="move.forward" />
            </Button>
            <Button variant="outline" size="icon-sm" disabled={off || !airborne} onClick={() => move(0, 0, 0.3)} title={ct('act.up')} data-action="up">
              <Icon icon="move.up" />
            </Button>
            <Button variant="outline" size="icon-sm" disabled={off || !airborne} onClick={() => move(0, -stepM)} title={ct('act.left')} data-action="left">
              <Icon icon="move.left" />
            </Button>
            <Button variant="outline" size="icon-sm" disabled={off || !airborne} onClick={() => move(-stepM, 0)} title={ct('act.back')} data-action="back">
              <Icon icon="move.back" />
            </Button>
            <Button variant="outline" size="icon-sm" disabled={off || !airborne} onClick={() => move(0, stepM)} title={ct('act.right')} data-action="right">
              <Icon icon="move.right" />
            </Button>
            <span />
            <span />
            <Button variant="outline" size="icon-sm" disabled={off || !airborne} onClick={() => move(0, 0, -0.3)} title={ct('act.down')} data-action="down">
              <Icon icon="move.down" />
            </Button>
          </div>
          <div className="flex flex-col gap-1.5">
            <NumberField label={ct('act.step')} unit={ct('plan.unit.m')} value={stepM} onChange={setStepM} min={0.1} max={2} />
            <div className="flex items-center gap-1.5">
              <Button variant="outline" size="icon-sm" disabled={off || !airborne} onClick={() => void skill('yaw', { deg: -turn })} title={ct('act.yawCcw')} data-action="yaw-ccw">
                <Icon icon="yaw.ccw" />
              </Button>
              <Button variant="outline" size="icon-sm" disabled={off || !airborne} onClick={() => void skill('yaw', { deg: turn })} title={ct('act.yawCw')} data-action="yaw-cw">
                <Icon icon="yaw.cw" />
              </Button>
              <NumberField label={ct('act.turn')} unit={ct('plan.unit.deg')} value={turn} onChange={setTurn} step={5} min={1} max={180} />
            </div>
          </div>
        </div>
        <p className="text-hud-sub text-[var(--g500)]">
          {!airborne ? ct('act.needsAirborne') : v.capabilities.position ? ct('act.closedLoop') : ct('act.openLoop')}
        </p>
      </div>
    </Group>
  )
}

const ACTION_ICON: Record<PlanAction, IconKey> = {
  takeoff: 'takeoff',
  hover: 'hover',
  hold: 'hover',
  move: 'move.forward',
  yaw: 'yaw.cw',
  land: 'land',
  goto: 'target',
  rtl: 'land',
}

function StepFields({ step, onChange }: { step: PlanStep; onChange: (s: PlanStep) => void }) {
  const ct = useCT()
  const m = ct('plan.unit.m')
  switch (step.action) {
    case 'takeoff':
      return <NumberField label="" unit={m} value={step.alt ?? 1} onChange={(alt) => onChange({ ...step, alt })} min={0.2} max={3} />
    case 'hover':
    case 'hold':
      return <NumberField label="" unit={ct('plan.unit.s')} value={step.seconds ?? 3} onChange={(seconds) => onChange({ ...step, seconds })} step={1} min={0} max={120} />
    case 'move':
      return (
        <span className="flex flex-wrap items-center gap-1.5">
          <NumberField label={ct('plan.field.forward')} unit={m} value={step.forward ?? 0} onChange={(forward) => onChange({ ...step, forward })} />
          <NumberField label={ct('plan.field.right')} unit={m} value={step.right ?? 0} onChange={(right) => onChange({ ...step, right })} />
        </span>
      )
    case 'yaw':
      return <NumberField label="" unit={ct('plan.unit.deg')} value={step.deg ?? 45} onChange={(deg) => onChange({ ...step, deg })} step={5} min={-360} max={360} />
    default:
      return null
  }
}

function defaultsFor(action: PlanAction): PlanStep {
  switch (action) {
    case 'takeoff':
      return { action, alt: 1 }
    case 'hover':
      return { action, seconds: 3 }
    case 'move':
      return { action, forward: 1 }
    case 'yaw':
      return { action, deg: 45 }
    default:
      return { action }
  }
}

export function PlanBlock({ v }: { v: Vehicle }) {
  const ct = useCT()
  const lang = useLang()
  const { busy, run } = useRun()
  const [steps, setSteps] = React.useState<PlanStep[]>(loadPlan)
  const [confirming, setConfirming] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const human = v.controller === 'human'
  const plan = v.plan
  const running = plan?.state === 'running'
  const shown = running ? (plan.steps as PlanStep[]) : steps

  const update = (next: PlanStep[]) => {
    setSteps(next)
    savePlan(next)
  }
  const start = async (confirm?: string) => {
    await api.plan(v.id, steps, { name: 'operator', ...(confirm ? { confirm } : {}) })
  }
  const onRun = () => {
    if (v.kind === 'real' && !v.armed && steps[0]?.action === 'takeoff') {
      setError(null)
      setConfirming(true)
      return
    }
    void run('plan', start)
  }

  let status: string | null = null
  if (plan?.state === 'running') status = ct('plan.running', { n: plan.index + 1, total: plan.count })
  else if (plan?.state === 'done') status = ct('plan.done')
  else if (plan?.state === 'failed') status = ct('plan.failed', { n: plan.index + 1, detail: plan.detail ?? '' })
  else if (plan?.state === 'cancelled') status = ct('plan.cancelled')

  return (
    <Group label={ct('plan.title')} count={shown.length} right={status ? <span className="text-hud-sub text-[var(--g400)]" data-plan-status={plan?.state}>{status}</span> : null}>
      <div className="flex flex-col gap-2 px-3" data-plan-block="">
        {shown.length === 0 ? <p className="text-hud-sub text-[var(--g500)]">{ct('plan.empty')}</p> : null}
        <ol className="flex flex-col gap-1">
          {shown.map((step, i) => {
            const state = !plan || (!running && plan.state !== 'done' && plan.state !== 'failed') ? 'idle' : i < plan.index ? 'done' : i === plan.index && running ? 'current' : 'idle'
            const result = plan && running ? plan.results[i] : null
            return (
              <li
                key={i}
                className={cn(
                  'flex min-h-7 items-center gap-2 rounded-md px-1.5 text-[12px]',
                  state === 'current' ? 'bg-[var(--g800)] text-[var(--g50)]' : state === 'done' ? 'text-[var(--g400)]' : 'text-[var(--g200)]',
                )}
                data-plan-step={i}
                data-state={state}
              >
                <span className="num w-4 text-right text-[11px] text-[var(--g500)]">{i + 1}</span>
                <Icon icon={state === 'done' ? 'check' : ACTION_ICON[step.action]} className="size-3.5 flex-none" />
                {running ? (
                  <span className="flex-1 truncate">
                    {ct(`plan.action.${step.action}` as ControlKey)} {describe(step, ct)}
                    {result ? <span className="ml-1.5 text-[var(--g500)]">{result}</span> : null}
                  </span>
                ) : (
                  <>
                    <NativeSelect
                      size="sm"
                      value={step.action}
                      onChange={(e) => update(steps.map((s, j) => (j === i ? defaultsFor(e.target.value as PlanAction) : s)))}
                      className="h-6 w-[84px] text-[11px]"
                      aria-label={ct('plan.title')}
                    >
                      {EDITABLE.map((a) => (
                        <NativeSelectOption key={a} value={a}>
                          {ct(`plan.action.${a}` as ControlKey)}
                        </NativeSelectOption>
                      ))}
                    </NativeSelect>
                    <span className="min-w-0 flex-1">
                      <StepFields step={step} onChange={(s) => update(steps.map((x, j) => (j === i ? s : x)))} />
                    </span>
                    <Button variant="ghost" size="icon-xs" onClick={() => update(steps.filter((_, j) => j !== i))} title={ct('plan.remove')} aria-label={ct('plan.remove')}>
                      <Icon icon="close" />
                    </Button>
                  </>
                )}
              </li>
            )
          })}
        </ol>
        {confirming && !v.armed && human && !running ? (
          <TypeToConfirm
            word="ARM"
            prompt={ct('plan.confirm', { aircraft: v.label })}
            actionLabel={ct('plan.run')}
            onConfirm={async () => {
              try {
                await start('ARM')
                setConfirming(false)
              } catch (e) {
                setError(e instanceof RelayApiError ? e.text(lang) : String(e))
              }
            }}
            onCancel={() => setConfirming(false)}
            error={error}
          />
        ) : null}
        <div className="flex flex-wrap items-center gap-1.5">
          {running ? (
            <Button variant="outline" size="sm" disabled={!human || busy !== null} onClick={() => void run('cancel', () => api.cancelPlan(v.id))} data-plan-cancel="">
              <Icon icon="stop" />
              {ct('plan.cancel')}
            </Button>
          ) : (
            <>
              <Button variant="outline" size="sm" disabled={!human || busy !== null || steps.length === 0 || !v.capabilities.althold} onClick={onRun} data-plan-run="">
                <Icon icon="tl.play" />
                {ct('plan.run')}
              </Button>
              <Button variant="ghost" size="sm" disabled={steps.length >= 24} onClick={() => update([...steps, defaultsFor('hover')])}>
                <Icon icon="plus" />
                {ct('plan.add')}
              </Button>
              <Button variant="ghost" size="sm" onClick={() => update(EXAMPLE_PLAN)} data-plan-example="">
                <Icon icon="reset" />
                {ct('plan.reset')}
              </Button>
            </>
          )}
        </div>
        <p className="text-hud-sub text-[var(--g500)]">{ct('plan.overrideHint')}</p>
      </div>
    </Group>
  )
}

function describe(step: PlanStep, ct: (k: ControlKey) => string): string {
  const m = ct('plan.unit.m')
  switch (step.action) {
    case 'takeoff':
      return `${step.alt ?? 1} ${m}`
    case 'hover':
    case 'hold':
      return `${step.seconds ?? 0} ${ct('plan.unit.s')}`
    case 'move': {
      const parts = []
      if (step.forward) parts.push(`${ct('plan.field.forward')} ${step.forward} ${m}`)
      if (step.right) parts.push(`${ct('plan.field.right')} ${step.right} ${m}`)
      if (step.up) parts.push(`${ct('plan.field.up')} ${step.up} ${m}`)
      return parts.join(' · ')
    }
    case 'yaw':
      return `${step.deg ?? 0}${ct('plan.unit.deg')}`
    default:
      return ''
  }
}
