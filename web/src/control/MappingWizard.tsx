// Mapping setup of a transmitter or generic controller (05 §6.3, WP-C), shown in the controller popover. While it is
// open the controller flies nothing (InputLayer setPadLearning); it refuses to start while the controller's aircraft
// is armed. The result is saved per device id in settings.ui.padProfiles (at most 8, settings/prefs.ts).
import * as React from 'react'
import { notify } from '@/shell/notify'
import { useVehicleField } from '@/store/selectors'
import { setPrefs, usePrefs } from '@/settings/prefs'
import { Button } from '@/ui/components/ui/button'
import { Progress } from '@/ui/components/ui/progress'
import { Icon } from '@/ui/icons'
import { dropProfile, putProfile, SWITCH_ACTIONS, type SwitchAction } from './devices'
import { setPadLearning } from './InputLayer'
import { useCT, type ControlKey } from './strings'
import {
  advance,
  beginBind,
  buildProfile,
  canAdvance,
  clearBind,
  feed,
  retreat,
  startWizard,
  WIZARD_STEPS,
  type Snapshot,
  type WizardState,
} from './wizard'

function readDevice(id: string): Snapshot | null {
  if (typeof navigator === 'undefined' || typeof navigator.getGamepads !== 'function') return null
  for (const p of navigator.getGamepads()) {
    if (p && p.connected && p.id === id) return { axes: [...p.axes], buttons: p.buttons.map((b) => b.pressed) }
  }
  return null
}

const STEP_TEXT: Record<string, ControlKey> = {
  roll: 'wiz.roll',
  pitch: 'wiz.pitch',
  throttle: 'wiz.throttle',
  yaw: 'wiz.yaw',
  range: 'wiz.range',
  centre: 'wiz.centre',
  switches: 'wiz.switches',
}

export interface MappingWizardProps {
  pad: number
  deviceId: string
  kind: 'transmitter' | 'generic'
  vehicle: string | null
  onClose(): void
}

export function MappingWizard({ pad, deviceId, kind, vehicle, onClose }: MappingWizardProps) {
  const ct = useCT()
  const armed = useVehicleField(vehicle, (v) => v.armed) ?? false
  const profiles = usePrefs((p) => p.padProfiles)
  const [state, setState] = React.useState<WizardState>(() => startWizard(kind))
  const live = React.useRef<Snapshot | null>(null)

  React.useEffect(() => {
    if (armed) return
    setPadLearning(pad, true)
    let raf = 0
    const loop = () => {
      const snap = readDevice(deviceId)
      live.current = snap
      if (snap) setState((s) => feed(s, snap))
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => {
      cancelAnimationFrame(raf)
      setPadLearning(pad, false)
    }
  }, [pad, deviceId, armed])

  if (armed) {
    return (
      <div className="flex flex-col gap-2" data-mapping-wizard="blocked">
        <p className="text-hud-sub text-[var(--g300)]">{ct('wiz.armedBlock')}</p>
        <Button variant="outline" size="sm" onClick={onClose}>
          {ct('wiz.cancel')}
        </Button>
      </div>
    )
  }

  const index = WIZARD_STEPS.indexOf(state.step)
  const stick = index < 4
  const save = () => {
    const profile = buildProfile(state, deviceId, Date.now())
    if (!profile) return
    setPrefs({ padProfiles: putProfile(profiles, profile) })
    notify(ct('wiz.saved', { n: pad }), { timeoutMs: 2000 })
    onClose()
  }
  const reset = () => {
    setPrefs({ padProfiles: dropProfile(profiles, deviceId) })
    onClose()
  }

  return (
    <div className="flex flex-col gap-3" data-mapping-wizard={state.step}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-ed-value font-[560] text-[var(--g100)]">{ct('wiz.title', { n: pad })}</span>
        <span className="num text-hud-sub text-[var(--g500)]">{ct('wiz.step', { n: index + 1, total: WIZARD_STEPS.length })}</span>
      </div>
      <Progress value={((index + 1) / WIZARD_STEPS.length) * 100} aria-label={ct('wiz.step', { n: index + 1, total: WIZARD_STEPS.length })} />
      <p className="text-hud-sub text-[var(--g200)]">{ct(STEP_TEXT[state.step])}</p>
      {stick ? (
        <p className="flex items-center gap-1.5 text-hud-sub text-[var(--g500)]">
          <Icon icon="pending" className="size-3.5" />
          {ct('wiz.waiting')}
        </p>
      ) : null}
      <DetectedAxes state={state} />
      {state.step === 'switches' ? <SwitchBinder state={state} setState={setState} live={live} /> : null}
      <p className="text-hud-sub text-[var(--g500)]">{ct('wiz.paused')}</p>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="ghost" size="sm" onClick={() => setState((s) => retreat(s))} disabled={index === 0}>
          {ct('wiz.back')}
        </Button>
        <span className="flex-1" />
        <Button variant="ghost" size="sm" onClick={onClose}>
          {ct('wiz.cancel')}
        </Button>
        {state.step === 'switches' ? (
          <Button size="sm" onClick={save} data-wizard-save="">
            {ct('wiz.save')}
          </Button>
        ) : (
          <Button size="sm" onClick={() => setState((s) => advance(s))} disabled={stick || !canAdvance(state)}>
            {ct('wiz.next')}
          </Button>
        )}
      </div>
      {profiles[deviceId] ? (
        <Button variant="outline" size="sm" onClick={reset}>
          {ct('wiz.reset')}
        </Button>
      ) : null}
    </div>
  )
}

function DetectedAxes({ state }: { state: WizardState }) {
  const ct = useCT()
  const rows = (['roll', 'pitch', 'throttle', 'yaw'] as const).filter((a) => state.found[a])
  if (!rows.length) return null
  return (
    <table data-lf-table="" className="w-full text-hud-sub">
      <tbody>
        {rows.map((a) => {
          const f = state.found[a]!
          return (
            <tr key={a} className="border-t border-[var(--border)] first:border-t-0">
              <th scope="row" className="py-1 pr-3 text-left font-[450] text-[var(--g200)]">{ct(`invert.${a}` as ControlKey)}</th>
              <td className="num py-1 text-[var(--g400)]">
                {ct('wiz.detected', { axis: f.index, dir: ct(f.invert ? 'wiz.dirInverted' : 'wiz.dirNormal') })}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function SwitchBinder({ state, setState, live }: { state: WizardState; setState: React.Dispatch<React.SetStateAction<WizardState>>; live: React.RefObject<Snapshot | null> }) {
  const ct = useCT()
  const label = (a: SwitchAction) => {
    const b = state.switches.find((x) => x.action === a)
    if (state.binding?.action === a) return ct('wiz.flip')
    if (!b) return ct('wiz.unbound')
    return b.source.type === 'axis' ? ct('wiz.axisN', { n: b.source.index }) : ct('wiz.buttonN', { n: b.source.index })
  }
  return (
    <table data-lf-table="" className="w-full text-hud-sub">
      <tbody>
        {SWITCH_ACTIONS.map((a) => (
          <tr key={a} className="border-t border-[var(--border)] first:border-t-0">
            <th scope="row" className="py-1 pr-2 text-left font-[450] text-[var(--g200)]">{ct(`sw.${a}` as ControlKey)}</th>
            <td className="num py-1 text-[var(--g400)]">{label(a)}</td>
            <td className="py-1 text-right whitespace-nowrap">
              <Button
                variant="ghost"
                size="sm"
                className="h-6 px-2"
                onClick={() => {
                  const snap = live.current
                  if (snap) setState((s) => beginBind(s, a, snap))
                }}
              >
                {ct('wiz.bind')}
              </Button>
              {state.switches.some((b) => b.action === a) ? (
                <Button variant="ghost" size="sm" className="h-6 px-2" onClick={() => setState((s) => clearBind(s, a))}>
                  {ct('wiz.clear')}
                </Button>
              ) : null}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
