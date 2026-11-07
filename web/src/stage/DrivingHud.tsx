// Driving state HUD (05 §6.4, WP-C): stage bottom left, above the stick panel, 220 wide, only while this window flies
// the aircraft. Row 1: aircraft · YOU · input. Row 2: mode, hover assist (simulated) or altitude hold (real), control
// feel; a cell that changes flashes for 120 ms in a voxel pattern. Row 3: the throttle actually sent with the hover
// point tick, plus the optional arena slot (hit points, ammunition); while sticks wait (focus, link, H5) row 3 is
// the waiting notice instead. Stage() places it (DRIVING_HUD_H) in its collision layout.
//
// Arena slot API: pass `hp` / `ammo` as props, or call setDrivingHudSlot(vehicleId, {hp, ammo}) from the arena layer
// (props win). With neither, row 3 shows the throttle only.
import * as React from 'react'
import { create } from 'zustand'
import type { Vehicle } from '@/api'
import { useLang, type Lang, type Params } from '@/i18n'
import { cn } from '@/lib/utils'
import { usePrefs } from '@/settings/prefs'
import { useRelay } from '@/store/relay'
import { findVehicle, useSelectedId } from '@/store/selectors'
import { isWindowSource, sourceOf, type SourceKey } from '@/control/bindings'
import { isHoldClass, type ControlFeel } from '@/control/shaping'
import { getLiveSticks, useAwaiting, type AwaitReason } from '@/control/store'
import { h5Key, modeLabel, translateControl, type ControlKey } from '@/control/strings'
import './driving-hud.css'

/** Height Stage() reserves for the HUD (three rows and padding). */
export const DRIVING_HUD_H = 76
export const DRIVING_HUD_W = 220

export interface HudAmmo {
  n: number
  max: number
  reloading: boolean
  /** 0..1 reload progress, shown as voxel steps while reloading */
  progress?: number
}

export interface HudSlot {
  /** 0..100 hit points */
  hp?: number
  ammo?: HudAmmo
}

const useSlots = create<{ slots: Record<string, HudSlot> }>()(() => ({ slots: {} }))

/** Arena feed of row 3 for one aircraft; null clears it. */
export function setDrivingHudSlot(vehicleId: string, slot: HudSlot | null): void {
  useSlots.setState((s) => {
    const slots = { ...s.slots }
    if (slot) slots[vehicleId] = slot
    else delete slots[vehicleId]
    return { slots }
  })
}

/** The aircraft this window flies and shows in the HUD: the selected one when a human flies it, else the keyboard one. */
export function useDrivenId(): string | null {
  const sel = useSelectedId()
  return useRelay((s) => {
    const vs = s.fleet?.vehicles ?? []
    const v = findVehicle(vs, sel)
    if (v && v.controller === 'human' && v.possession) return v.id
    return vs.find((x) => x.controller === 'human' && x.possession && isWindowSource(sourceOf(x.possession)))?.id ?? null
  })
}

/** Remounts its child (restarting the flash animation) whenever `value` changes after the first render. */
function Flash({ value, className, children, cell }: { value: string; className?: string; children: React.ReactNode; cell: string }) {
  const [state, setState] = React.useState({ value, n: 0 })
  if (state.value !== value) setState({ value, n: state.n + 1 })
  return (
    <span key={state.n} className={cn('dhud-cell', state.n > 0 && 'dhud-flash', className)} data-dhud-cell={cell}>
      {children}
    </span>
  )
}

export interface DrivingHudProps extends HudSlot {
  bottom: number
}

/** What the HUD shows for one aircraft (pure: hudModel builds it, DrivingHudView draws it). */
export interface HudModel {
  id: string
  label: string
  source: SourceKey | null
  mode: string | null
  modeClass: Vehicle['mode_class']
  /** hover assist of a simulated aircraft; null on a real aircraft */
  assist: boolean | null
  feel: ControlFeel
  awaiting: AwaitReason | null
  hp?: number
  ammo?: HudAmmo
}

export function hudModel(
  v: Pick<Vehicle, 'id' | 'label' | 'possession' | 'mode' | 'mode_class' | 'kind' | 'sim'>,
  feel: ControlFeel,
  awaiting: AwaitReason | null,
  slot?: HudSlot,
): HudModel {
  return {
    id: v.id,
    label: v.label,
    source: v.possession ? sourceOf(v.possession) : null,
    mode: v.mode,
    modeClass: v.mode_class,
    assist: v.kind === 'sim' && v.sim ? v.sim.hover_assist : null,
    feel,
    awaiting,
    hp: slot?.hp,
    ammo: slot?.ammo,
  }
}

export function DrivingHud({ bottom, hp, ammo }: DrivingHudProps) {
  const lang = useLang()
  const id = useDrivenId()
  const v = useRelay((s) => (id ? findVehicle(s.fleet?.vehicles, id) : null))
  const feel = usePrefs((p) => p.controlFeel)
  const awaiting = useAwaiting(id)
  const slot = useSlots((s) => (id ? s.slots[id] : undefined))
  if (!v) return null
  const model = hudModel(v, feel, awaiting, { hp: hp ?? slot?.hp, ammo: ammo ?? slot?.ammo })
  return <DrivingHudView model={model} lang={lang} bottom={bottom} />
}

export function DrivingHudView({ model, lang, bottom }: { model: HudModel; lang: Lang; bottom: number }) {
  const ct = (key: ControlKey, params?: Params) => translateControl(lang, key, params)
  const { id, source, mode, modeClass, assist, feel, awaiting } = model
  const fill = React.useRef<HTMLSpanElement>(null)
  const tick = React.useRef<HTMLSpanElement>(null)
  React.useEffect(() => {
    let raf = 0
    const loop = () => {
      const l = getLiveSticks(id)
      const th = l ? l.frame.throttle : 0
      const centre = l ? (l.hold ? 0.5 : l.centre) : 0.5
      if (fill.current) fill.current.style.transform = `scaleX(${Math.min(1, Math.max(0, th)).toFixed(3)})`
      if (tick.current) tick.current.style.left = `${(centre * 100).toFixed(1)}%`
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [id, awaiting])

  const sourceText = !source ? '' : source === 'keyboard' ? ct('hud.keys') : source === 'screen' ? ct('hud.screen') : ct('hud.pad', { n: Number(source.slice(4)) })
  const hold = isHoldClass(modeClass)
  const assistText = assist !== null ? ct(assist ? 'hud.assistOn' : 'hud.assistOff') : ct(hold ? 'hud.altHold' : 'hud.manualThr')
  const waiting = awaiting === 'throttle' ? ct(h5Key(modeClass)) : awaiting ? ct('ctl.moveToResume') : null

  return (
    <div className="stage-hud dhud" style={{ bottom }} data-driving-hud="" role="status" aria-label={ct('hud.label')}>
      <div className="dhud-row dhud-id">
        <span className="truncate">{model.label}</span>
        <span className="dhud-sep">·</span>
        <span>{ct('hud.you')}</span>
        {sourceText ? (
          <>
            <span className="dhud-sep">·</span>
            <span className="truncate">{sourceText}</span>
          </>
        ) : null}
      </div>
      <div className="dhud-row dhud-state">
        <Flash value={mode ?? ''} cell="mode">{modeLabel(lang, mode)}</Flash>
        <Flash value={assistText} cell="assist">{assistText}</Flash>
        <Flash value={feel} cell="feel">{ct(`hud.feel.${feel}` as ControlKey)}</Flash>
      </div>
      {waiting ? (
        <div className="dhud-row dhud-wait" data-dhud-wait={awaiting}>
          {waiting}
        </div>
      ) : (
        <div className="dhud-row dhud-bars">
          <span className="dhud-cap">{ct('hud.throttle')}</span>
          <span className="dhud-bar" title={ct('hud.hover')}>
            <span ref={fill} className="dhud-bar-fill" />
            <span ref={tick} className="dhud-bar-tick" />
          </span>
          {model.hp !== undefined ? (
            <>
              <span className="dhud-cap">{ct('hud.hp')}</span>
              <span className="dhud-bar dhud-bar-hp" data-dhud-hp={Math.round(model.hp)}>
                <span className="dhud-bar-fill" style={{ transform: `scaleX(${(Math.min(100, Math.max(0, model.hp)) / 100).toFixed(3)})` }} />
              </span>
              <span className="num dhud-num">{Math.round(model.hp)}</span>
            </>
          ) : null}
          {model.ammo ? <Ammo ammo={model.ammo} label={ct(model.ammo.reloading ? 'hud.reloading' : 'hud.ammo')} /> : null}
        </div>
      )}
    </div>
  )
}

const VOXELS = 6

function Ammo({ ammo, label }: { ammo: HudAmmo; label: string }) {
  if (ammo.reloading) {
    const lit = Math.round(Math.min(1, Math.max(0, ammo.progress ?? 0)) * VOXELS)
    return (
      <span className="dhud-reload" title={label} aria-label={label} data-dhud-ammo="reloading">
        {Array.from({ length: VOXELS }, (_, i) => (
          <i key={i} data-on={i < lit ? '' : undefined} />
        ))}
      </span>
    )
  }
  return (
    <span className="num dhud-num" title={label} data-dhud-ammo={ammo.n}>
      {ammo.n} / {ammo.max}
    </span>
  )
}
