// Inspector Control tab (02 §5, §11 Control): possession (the app's one solid light button), sticks, Arm / Disarm
// with type-to-confirm for real aircraft, preflight checklist, mode, flight actions and sequences, ring course,
// calibration and motor test. Contract: web/CONTRACTS.md#controltab.
import * as React from 'react'
import { api, RelayApiError, type Vehicle } from '@/api'
import { useLang, useT } from '@/i18n'
import { duration } from '@/lib/format'
import { cn } from '@/lib/utils'
import { notifyError } from '@/shell/notify'
import { useRelay } from '@/store/relay'
import { useVehicle } from '@/store/selectors'
import { useInspectorTab } from '@/inspector/tabs'
import { Button } from '@/ui/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/ui/components/ui/select'
import { Icon, StateIcon } from '@/ui/icons'
import { ArmedWord, ControllerPill, Group, HoldProgress, TypeToConfirm } from '@/ui/relay'
import { possessWith, releaseVehicle, sourceLabel } from './actions'
import { isWindowSource, sourceOf } from './bindings'
import { Calibration } from './Calibration'
import { CourseGroup } from './CourseGroup'
import { ActionsBlock, PlanBlock } from './FlightActions'
import { SequenceEntry } from '@/sequence/SequenceEntry' // 10 §2
import { MotorTest } from './MotorTest'
import { PreflightList } from './PreflightList'
import { Sticks } from './Sticks'
import { BenchEntry } from '@/bench/BenchEntry' // WP-B
import { ProgramGroup } from '@/lab/ProgramGroup' // WP-3
import { useAwaiting, useHold, useInputPaused, usePadDisconnected } from './store'
import { useCT, h5Key } from './strings'
import './control.css'

export interface ControlTabProps {
  vehicleId: string
}

function useAirborneAlert(id: string): boolean {
  return useRelay((s) => (s.fleet?.alerts ?? []).some((a) => a.code === 'airborne_no_controller' && a.vehicle === id && !a.acked && !s.localAcks.has(a.id)))
}

function Notice({ children, tone = 'info' }: { children: React.ReactNode; tone?: 'info' | 'warn' }) {
  return (
    <p className={cn('flex items-start gap-2 text-hud-sub', tone === 'warn' ? 'text-[var(--g50)]' : 'text-[var(--g400)]')} role="status">
      <Icon icon={tone === 'warn' ? 'alert' : 'info'} className="mt-px size-3.5 flex-none" />
      <span>{children}</span>
    </p>
  )
}

function PossessBlock({ v }: { v: Vehicle }) {
  const t = useT()
  const ct = useCT()
  const hold = useHold(v.id)
  const padLost = usePadDisconnected(v.id)
  const paused = useInputPaused()
  const awaiting = useAwaiting(v.id)
  const airborne = useAirborneAlert(v.id)
  const [busy, setBusy] = React.useState(false)
  const human = v.controller === 'human' && v.possession
  const source = v.possession ? sourceOf(v.possession) : null
  const windowSource = source !== null && isWindowSource(source)
  const lost = v.link.state === 'lost' || v.link.state === 'offline'

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await fn()
    } catch {
      /* reported by the action */
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-2.5 px-3" data-possess-block="">
      {!human ? (
        <>
          {v.controller === 'agent' && v.claim ? <p className="text-hud-sub text-[var(--g300)]">{t('possess.agentFlying', { agent: v.claim.name })}</p> : null}
          <div className="flex flex-col gap-1">
            <button
              type="button"
              disabled={busy}
              onClick={() => act(() => possessWith(v.id, null))}
              title={ct('ctl.possessKeyboard')}
              className="flex h-9 w-full items-center justify-center gap-2 rounded-md bg-[var(--g50)] text-[13px] font-[560] text-[var(--g950)] outline-none transition-colors duration-quick hover:bg-[var(--g100)] focus-visible:ring-2 focus-visible:ring-ring/40 active:bg-[var(--g200)] disabled:opacity-60"
              data-possess=""
            >
              <StateIcon icon="possess" className="size-4" />
              {t('action.possess')}
              <kbd className="num ml-1 rounded-[3px] border border-[var(--g400)] px-1 text-[10px] leading-[14px] text-[var(--g700)]">P</kbd>
            </button>
            <HoldProgress active={Boolean(hold && hold.kind === 'options')} durationMs={hold?.durationMs ?? 1000} />
          </div>
        </>
      ) : (
        <>
          <div className="flex items-center gap-2">
            <ControllerPill vehicle={v} showWaiting padDisconnected={padLost} className="min-w-0 flex-1" />
            <Button variant="outline" disabled={busy} onClick={() => act(() => releaseVehicle(v.id))} data-release="">
              <StateIcon icon="possess" alt className="size-3.5" />
              {t('action.release')}
            </Button>
          </div>
          <HoldProgress active={Boolean(hold && hold.kind === 'options')} durationMs={hold?.durationMs ?? 1000} />
          <Sticks vehicleId={v.id} interactive={windowSource} />
          <p className="text-hud-sub text-[var(--g500)]">
            {windowSource ? (
              ct('ctl.screenHint')
            ) : (
              <>
                <span className="block text-[var(--g400)]">{ct('ctl.flownBy', { input: sourceLabel(source!) })}</span>
                <span className="block">{ct('ctl.padHint')}</span>
              </>
            )}
          </p>
          {paused ? <Notice>{t('stage.inputPaused')}</Notice> : null}
          {!paused && awaiting === 'focus' ? <Notice>{ct('ctl.moveToResume')}</Notice> : null}
          {!paused && awaiting === 'link' ? <Notice>{t('stage.linkRestored')}</Notice> : null}
          {!paused && awaiting === 'throttle' ? <Notice>{ct(h5Key(v.mode_class))}</Notice> : null}
          {lost ? <Notice tone="warn">{t('stage.linkLost', { aircraft: v.label })}</Notice> : null}
          {padLost && v.possession?.pad ? <Notice tone="warn">{t('stage.padDisconnected', { n: v.possession.pad, aircraft: v.label })}</Notice> : null}
        </>
      )}
      {airborne && !human ? <Notice tone="warn">{t('possess.airborneNoController', { aircraft: v.label })}</Notice> : null}
    </div>
  )
}

function ArmBlock({ v }: { v: Vehicle }) {
  const t = useT()
  const ct = useCT()
  const lang = useLang()
  const hold = useHold(v.id)
  const [confirming, setConfirming] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const human = v.controller === 'human'
  const blocked = v.kind === 'real' && !v.preflight.ok
  // the confirm block only makes sense while disarmed and possessed
  const showConfirm = confirming && !v.armed && human

  const call = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      setConfirming(false)
    } catch (e) {
      const msg = e instanceof RelayApiError ? e.text(lang) : String(e)
      if (showConfirm) setError(msg)
      else notifyError(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-2 px-3" data-arm-block="">
      <div className="flex min-h-7 items-center gap-2">
        <StateIcon icon="lock" alt={v.armed} className={cn('size-4', v.armed ? 'text-[var(--g100)]' : 'text-[var(--g500)]')} />
        <ArmedWord armed={v.armed} />
        {v.armed ? <span className="num text-hud-sub text-[var(--g400)]">{duration(v.armed_time_s)}</span> : null}
        <span className="flex-1" />
        {v.armed ? (
          <Button variant="outline" disabled={busy} onClick={() => call(() => api.disarm(v.id))} data-disarm="">
            {t('action.disarm')}
          </Button>
        ) : (
          <Button
            variant="outline"
            disabled={busy || !human || blocked || showConfirm}
            onClick={() => (v.kind === 'real' ? setConfirming(true) : call(() => api.arm(v.id)))}
            data-arm=""
          >
            {busy && !showConfirm ? ct('ctl.armPending') : t('action.arm')}
          </Button>
        )}
      </div>
      {hold?.kind === 'arm' ? <HoldProgress active durationMs={hold.durationMs} /> : null}
      {showConfirm ? (
        <TypeToConfirm
          word="ARM"
          prompt={t('arm.typePrompt', { aircraft: v.label })}
          actionLabel={t('action.arm')}
          onConfirm={() => call(() => api.arm(v.id))}
          onCancel={() => setConfirming(false)}
          busy={busy}
          error={error}
        />
      ) : null}
      {!v.armed && !human ? <p className="text-hud-sub text-[var(--g500)]">{ct('ctl.possessFirst')}</p> : null}
      {!v.armed && human && blocked ? <p className="text-hud-sub text-[var(--g400)]">{ct('ctl.preflightBlocksArm')}</p> : null}
      {!v.armed && human && v.kind === 'sim' ? <p className="text-hud-sub text-[var(--g500)]">{t('arm.simDirect')}</p> : null}
    </div>
  )
}

function ModeBlock({ v }: { v: Vehicle }) {
  const t = useT()
  const ct = useCT()
  const [busy, setBusy] = React.useState(false)
  const human = v.controller === 'human'
  const items = v.modes.map((m) => ({ value: m, label: m }))
  if (v.mode && !v.modes.includes(v.mode)) items.unshift({ value: v.mode, label: v.mode })
  const change = async (name: string | null) => {
    if (!name || name === v.mode) return
    setBusy(true)
    try {
      await api.mode(v.id, name)
    } catch (e) {
      notifyError(e)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Group label={t('ctl.mode')} right={busy ? <span className="text-hud-sub text-[var(--g400)]">{ct('ctl.modePending')}</span> : null}>
      <div className="px-3">
        <Select items={items} value={v.mode ?? null} onValueChange={(x) => void change(x as string | null)} disabled={!human || busy || items.length === 0}>
          <SelectTrigger size="sm" className="num h-7 w-full font-mono text-[11px]" aria-label={t('ctl.mode')} title={human ? ct('ctl.modeHint') : ct('ctl.possessFirst')}>
            <SelectValue placeholder="—" />
          </SelectTrigger>
          <SelectContent>
            {items.map((it) => (
              <SelectItem key={it.value} value={it.value} className="font-mono text-[11px]">
                {it.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </Group>
  )
}

export function ControlTab({ vehicleId }: ControlTabProps) {
  const v = useVehicle(vehicleId)
  const calibFocus = useInspectorTab((s) => s.focus === 'calibration')
  const clearFocus = useInspectorTab((s) => s.clearFocus)
  if (!v) return null
  return (
    <div className="flex flex-col gap-4" data-control-tab={vehicleId}>
      <PossessBlock v={v} />
      <ArmBlock v={v} />
      <PreflightList vehicleId={v.id} kind={v.kind} blocking={v.preflight.blocking.length} />
      <BenchEntry vehicleId={v.id} />
      <ModeBlock v={v} />
      <ActionsBlock v={v} />
      <PlanBlock v={v} />
      <SequenceEntry />
      <CourseGroup vehicleId={v.id} />
      <ProgramGroup vehicleId={v.id} /> {/* WP-3: onboard program (06 §2.6, §7) */}
      <Calibration vehicle={v} focus={calibFocus} onFocused={clearFocus} />
      {v.kind === 'real' ? <MotorTest vehicle={v} /> : null}
    </div>
  )
}
