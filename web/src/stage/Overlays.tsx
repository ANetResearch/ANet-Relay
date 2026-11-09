// Stage HUD overlays of 02 §4.4: selected identity (top left), view switch and layer menu (top right), the
// selected aircraft's HUD strip (bottom centre, at most 640 x 64), and while possessed the stick visualisation with
// keyboard hints (bottom left). Values that change with every snapshot are written to the DOM from a store
// subscription; React re-renders only when a value appears or disappears.
import * as React from 'react'
import type { Vehicle } from '@/api'
import { angle, climb, metres, volts, yaw as yawText } from '@/lib/format'
import { useRelay } from '@/store/relay'
import { findVehicle, useSelectedId, useVehicleField } from '@/store/selectors'
import { Button } from '@/ui/components/ui/button'
import {
  DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuGroup, DropdownMenuLabel, DropdownMenuTrigger,
} from '@/ui/components/ui/dropdown-menu'
import { Kbd, KbdGroup } from '@/ui/components/ui/kbd'
import { ToggleGroup, ToggleGroupItem } from '@/ui/components/ui/toggle-group'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/ui/components/ui/tooltip'
import { Icon, type IconKey } from '@/ui/icons'
import { ControllerPill, SimTag, Value } from '@/ui/relay'
import { cn } from '@/lib/utils'
import { chaseTargetId } from './scene/CameraRig'
import { useT6, type AnyKey6 } from './copy'
import { live } from './live'
import { useStage, type StageLayers, type ViewMode } from './store'
import { EngineBadge } from '@/twin/parts'
import { engineBadge } from '@/twin/model'
import { CrashedReset } from '@/twin/CrashedReset'

// ---------------------------------------------------------------- identity

export function IdentityBlock({ top }: { top: number }) {
  const id = useSelectedId()
  const head = useRelay((s) => {
    const v = findVehicle(s.fleet?.vehicles, id)
    if (!v) return null
    return JSON.stringify([v.label, v.kind, v.host ?? (v.kind === 'sim' ? v.id : null), v.autopilot, v.mode])
  })
  const v = useRelay((s) => findVehicle(s.fleet?.vehicles, id))
  if (!head || !v) return null
  const [label, kind, host, autopilot, mode] = JSON.parse(head) as [string, string, string | null, string | null, string | null]
  const sub = [host, autopilot, mode].filter(Boolean).join(' · ')
  return (
    <div className="stage-hud stage-identity" style={{ top }} data-stage-identity="">
      <div className="stage-identity-name">
        {kind === 'sim' ? <SimTag /> : null}
        <span>{label}</span>
      </div>
      {sub ? <div className="stage-identity-sub">{sub}</div> : null}
      {/* 11 §8.5: the engine of a twin or a HITL aircraft */}
      {engineBadge(v) ? (
        <div className="flex min-w-0">
          <EngineBadge vehicle={v} />
        </div>
      ) : null}
      <div className="flex">
        <ControllerPill vehicle={v} showWaiting />
      </div>
      {/* v0.5.4: a crashed simulator offers the reset next to it */}
      {v.kind === 'sim' && v.crashed ? <CrashedReset id={v.id} /> : null}
    </div>
  )
}

// ---------------------------------------------------------------- view switch and layers

const VIEWS: { id: ViewMode; icon: IconKey; key: AnyKey6 }[] = [
  { id: 'overview', icon: 'scan.frame', key: 'stage.view.overview' },
  { id: 'chase', icon: 'cam.follow', key: 'stage.view.chase' },
  { id: 'top', icon: 'grid', key: 'stage.view.top' },
]

/** Chase needs a placed aircraft to follow; re-evaluated per snapshot, re-renders only when the answer flips. */
function useChaseAvailable(): boolean {
  const sel = useSelectedId()
  return useRelay((s) => {
    void s.fleet
    const tg = sel ? live.targets.get(sel) : undefined
    return (tg !== undefined && tg.kind !== 'slot') || chaseTargetId() !== null
  })
}

export function ViewTools({ top }: { top: number }) {
  const t = useT6()
  const view = useStage((s) => s.view)
  const setView = useStage((s) => s.setView)
  const layers = useStage((s) => s.layers)
  const setLayer = useStage((s) => s.setLayer)
  const chaseOk = useChaseAvailable()
  const toggles: { k: Exclude<keyof StageLayers, 'safety' | 'grid'>; key: AnyKey6 }[] = [
    { k: 'course', key: 'stage.layer.course' },
    { k: 'labels', key: 'stage.layer.labels' },
    { k: 'trails', key: 'stage.layer.trails' },
    { k: 'arena', key: 'stage.layer.arena' },
    { k: 'volumes', key: 'stage.layer.volumes' }, // WP-C v0.5 (07 §5)
  ]
  const safetyShown = layers.safety === 'on' || (layers.safety === 'auto' && [...live.targets.values()].some((x) => x.kind !== 'slot'))
  return (
    <div className="stage-hud stage-tools" style={{ top }} data-stage-tools="">
      <ToggleGroup
        value={[view]}
        onValueChange={(v: unknown[]) => v[0] && setView(v[0] as ViewMode)}
        spacing={0}
        aria-label={t('stage.viewSwitch')}
        className="rounded-md p-0"
      >
        {VIEWS.map((it) => {
          const disabled = it.id === 'chase' && !chaseOk
          return (
            <Tooltip key={it.id}>
              <TooltipTrigger
                render={
                  <ToggleGroupItem
                    value={it.id}
                    disabled={disabled}
                    aria-label={t(it.key)}
                    className="size-7 rounded-[5px] px-0 text-[var(--g400)] hover:bg-transparent hover:text-[var(--g100)] data-[pressed]:text-[var(--g50)]"
                  />
                }
              >
                <Icon icon={it.icon} className="size-4" />
              </TooltipTrigger>
              <TooltipContent>{disabled ? t('stage.chaseUnavailable') : t('stage.viewHint', { view: t(it.key) })}</TooltipContent>
            </Tooltip>
          )
        })}
      </ToggleGroup>
      <span className="h-4 w-px bg-[var(--g700)]" aria-hidden="true" />
      <DropdownMenu>
        <Tooltip>
          <TooltipTrigger
            render={
              <DropdownMenuTrigger
                render={<Button variant="ghost" size="icon" aria-label={t('stage.layers')} className="text-[var(--g400)] hover:text-[var(--g100)]" />}
              />
            }
          >
            <Icon icon="layers" className="size-4" />
          </TooltipTrigger>
          <TooltipContent>{t('stage.layers')}</TooltipContent>
        </Tooltip>
        <DropdownMenuContent align="end" className="l3 w-52">
          <DropdownMenuGroup>
            <DropdownMenuLabel>{t('stage.layers')}</DropdownMenuLabel>
            <DropdownMenuCheckboxItem checked={layers.grid} onCheckedChange={(c: boolean) => setLayer('grid', c)}>
              {t('stage.layer.grid')}
            </DropdownMenuCheckboxItem>
            <DropdownMenuCheckboxItem checked={safetyShown} onCheckedChange={(c: boolean) => setLayer('safety', c ? 'on' : 'off')}>
              <span className="flex flex-col">
                <span>{t('stage.layer.safety')}</span>
                {layers.safety === 'auto' ? <span className="text-hud-sub text-[var(--g500)]">{t('stage.layerSafetyAuto')}</span> : null}
              </span>
            </DropdownMenuCheckboxItem>
            {toggles.map((it) => (
              <DropdownMenuCheckboxItem key={it.k} checked={layers[it.k]} onCheckedChange={(c: boolean) => setLayer(it.k, c)}>
                {t(it.key)}
              </DropdownMenuCheckboxItem>
            ))}
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}

// ---------------------------------------------------------------- HUD strip

type Reader = (v: Vehicle) => { text: string | null; reason: string }

function useReaders(): { key: AnyKey6; read: Reader }[] {
  const t = useT6()
  return React.useMemo(() => {
    const live = (v: Vehicle) => v.link.state === 'ok' || v.link.state === 'delayed'
    const gone = (v: Vehicle) => (v.link.state === 'waiting' ? t('value.reason.noData') : t('value.reason.notLive'))
    const att = (pick: (a: NonNullable<Vehicle['attitude']>) => string | null): Reader => (v) => {
      if (!live(v)) return { text: null, reason: gone(v) }
      return { text: v.attitude ? pick(v.attitude) : null, reason: t('value.reason.notSent', { msg: 'ATTITUDE' }) }
    }
    return [
      { key: 'stage.hud.roll', read: att((a) => angle(a.roll)) },
      { key: 'stage.hud.pitch', read: att((a) => angle(a.pitch)) },
      { key: 'stage.hud.yaw', read: att((a) => yawText(a.yaw)) },
      {
        key: 'stage.hud.alt',
        read: (v) => (live(v) ? { text: metres(v.altitude_m), reason: t('value.reason.notSent', { msg: 'VFR_HUD' }) } : { text: null, reason: gone(v) }),
      },
      {
        key: 'stage.hud.vs',
        read: (v) => (live(v) ? { text: climb(v.climb_ms), reason: t('value.reason.notSent', { msg: 'VFR_HUD' }) } : { text: null, reason: gone(v) }),
      },
      {
        key: 'stage.hud.bat',
        read: (v) =>
          live(v) ? { text: volts(v.battery.voltage), reason: t('value.reason.notSent', { msg: 'BATTERY_STATUS' }) } : { text: null, reason: gone(v) },
      },
    ]
  }, [t])
}

export function HudStrip({ width, bottom }: { width: number; bottom: number }) {
  const t = useT6()
  const id = useSelectedId()
  const readers = useReaders()
  const refs = React.useRef<(HTMLSpanElement | null)[]>([])
  // blank reasons as one string; changes only when a value appears or disappears
  const blanks = useRelay((s) => {
    const v = findVehicle(s.fleet?.vehicles, id)
    if (!v) return null
    return JSON.stringify(readers.map((r) => {
      const x = r.read(v)
      return x.text === null ? x.reason : null
    }))
  })

  React.useLayoutEffect(() => {
    const write = () => {
      const v = findVehicle(useRelay.getState().fleet?.vehicles, id)
      if (!v) return
      readers.forEach((r, i) => {
        const el = refs.current[i]
        const x = r.read(v)
        if (el && x.text !== null && el.textContent !== x.text) el.textContent = x.text
      })
    }
    write()
    let last = useRelay.getState().fleet
    return useRelay.subscribe((s) => {
      if (s.fleet === last) return
      last = s.fleet
      write()
    })
  }, [id, readers, blanks])

  if (!id || blanks === null) return null
  const reasons = JSON.parse(blanks) as (string | null)[]
  const w = Math.min(640, width - 32)
  return (
    <div className="stage-hud stage-strip" style={{ width: w, bottom }} data-compact={w < 560 ? '' : undefined} data-stage-strip="">
      {readers.map((r, i) => (
        <div key={r.key} className="stage-strip-item">
          <span className="cap">{t(r.key)}</span>
          {reasons[i] !== null ? (
            <Value value={null} reason={reasons[i] as string} className="stage-strip-value text-[var(--g500)]" />
          ) : (
            <span className="stage-strip-value" ref={(el) => { refs.current[i] = el }} />
          )}
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------- sticks while possessed

/** The aircraft whose sticks are shown: the selected one when a human flies it, else the keyboard-possessed one. */
/** Is the stage stick panel (bottom left, about 116 high) on screen? */
export function useStickPanelShown(): boolean {
  return usePossessedId() !== null
}

function usePossessedId(): string | null {
  const sel = useSelectedId()
  return useRelay((s) => {
    const vs = s.fleet?.vehicles ?? []
    const v = findVehicle(vs, sel)
    if (v && v.controller === 'human') return v.id
    return vs.find((x) => x.possession?.input === 'keyboard')?.id ?? null
  })
}

/** Throttle 0..1 back to the stick position -1..1 around the hover point (inverse of 03 §5.2). */
function throttleToStick(th: number, hover: number): number {
  if (th <= hover) return hover > 0 ? th / hover - 1 : 0
  return hover < 1 ? (th - hover) / (1 - hover) : 0
}

export function StickPanel({ bottom }: { bottom: number }) {
  const t = useT6()
  const id = usePossessedId()
  const input = useVehicleField(id, (v) => v.possession?.input ?? null)
  const hover = useVehicleField(id, (v) => v.calibration.hover) ?? 0.5
  const left = React.useRef<HTMLSpanElement>(null)
  const right = React.useRef<HTMLSpanElement>(null)
  const fill = React.useRef<HTMLSpanElement>(null)

  React.useLayoutEffect(() => {
    if (!id) return
    const write = () => {
      const v = findVehicle(useRelay.getState().fleet?.vehicles, id)
      const s = v?.sticks
      const roll = s?.roll ?? 0
      const pitch = s?.pitch ?? 0
      const yw = s?.yaw ?? 0
      const th = s?.throttle ?? (v?.throttle_pct ?? 0) / 100
      const ly = throttleToStick(th, v?.calibration.hover ?? hover)
      // 72 px boxes, 8 px dot: travel +-32 px
      if (left.current) left.current.style.transform = `translate(${(yw * 32).toFixed(1)}px, ${(-ly * 32).toFixed(1)}px)`
      if (right.current) right.current.style.transform = `translate(${(roll * 32).toFixed(1)}px, ${(-pitch * 32).toFixed(1)}px)`
      if (fill.current) fill.current.style.transform = `scaleY(${Math.min(1, Math.max(0, th)).toFixed(3)})`
    }
    write()
    let last = useRelay.getState().fleet
    return useRelay.subscribe((s) => {
      if (s.fleet === last) return
      last = s.fleet
      write()
    })
  }, [id, hover])

  if (!id) return null
  return (
    <div className="stage-hud stage-sticks" style={{ bottom }} data-stage-sticks="" aria-hidden="true">
      <div className="stage-sticks-row">
        <span className="stage-stick">
          <span ref={left} className="stage-stick-dot" />
        </span>
        <span className="stage-throttle">
          <span ref={fill} className="stage-throttle-fill" />
          <span className="stage-throttle-hover" style={{ bottom: `${(hover * 100).toFixed(1)}%` }} />
        </span>
        <span className="stage-stick">
          <span ref={right} className="stage-stick-dot" />
        </span>
      </div>
      {input === 'keyboard' ? (
        <div className={cn('flex items-center gap-2')} title={t('stage.keysTitle')}>
          <KbdGroup>
            {['W', 'A', 'S', 'D'].map((k) => <Kbd key={k}>{k}</Kbd>)}
          </KbdGroup>
          <KbdGroup>
            <Kbd>Q</Kbd>
            <Kbd>E</Kbd>
          </KbdGroup>
          <KbdGroup>
            <Kbd>R</Kbd>
            <Kbd>F</Kbd>
          </KbdGroup>
          <Kbd>Space</Kbd>
          <Kbd>Esc</Kbd>
        </div>
      ) : null}
    </div>
  )
}
