// Inspector Overview (02 §11): preflight summary (opens Control), key values in two columns, flight health (03 §5.9),
// video group (WP7), the last three status texts, capabilities.
import * as React from 'react'
import type { Vehicle } from '@/api'
import { duration } from '@/lib/format'
import { cn } from '@/lib/utils'
import { criticalTone } from '@/store/red'
import { useRedOwner, useVehicle } from '@/store/selectors'
import { Icon } from '@/ui/icons'
import { BatteryReadout, LinkStatus, Readout, useAgo, Value } from '@/ui/relay'
import { VideoSourceGroup } from '@/video/VideoPip'
import { useT8, type AnyKey } from '@/fleet/copy'
import { useLength } from '@/settings/prefs'
import { HealthBlock } from './HealthBlock'
import { Block } from './parts'
import { useInspectorTab } from './tabs'

function PreflightLine({ v }: { v: Vehicle }) {
  const t = useT8()
  const setTab = useInspectorTab((s) => s.setTab)
  const n = v.preflight.blocking.length
  return (
    <div className="px-3">
      <button
        type="button"
        onClick={() => setTab('control')}
        className="l2 flex h-9 w-full items-center gap-2.5 rounded-md px-3 text-left outline-none hover:border-[var(--g600)] focus-visible:ring-1 focus-visible:ring-ring"
        aria-label={t('insp.preflightOpen')}
        data-preflight={n ? 'blocking' : 'ready'}
      >
        <Icon icon={n ? 'alert' : 'check.circle'} className={cn('size-4', n ? 'text-[var(--g50)]' : 'text-[var(--g300)]')} />
        <span className={cn('text-ed-value', n ? 'font-[560] text-[var(--g50)]' : 'text-[var(--g200)]')}>
          {n ? t('insp.preflightBlocking', { n }) : t('insp.preflightReady')}
        </span>
        <span className="flex-1" />
        <Icon icon="chevron.right" className="size-3.5 text-[var(--g500)]" />
      </button>
    </div>
  )
}

export function gpsText(gps: Vehicle['gps'], t: (k: AnyKey, p?: Record<string, string | number>) => string): string | null {
  if (!gps) return null
  const fix = Math.max(0, Math.min(6, Math.round(gps.fix)))
  const word = t(`gps.fix.${fix}` as AnyKey)
  return gps.sats === null ? word : `${word} · ${t('gps.sats', { n: gps.sats })}`
}

function StatusTextRow({ s }: { s: Vehicle['statustext'][number] }) {
  const t = useT8()
  const owner = useRedOwner()
  const ago = useAgo(s.t)
  const sev = Math.max(0, Math.min(7, s.severity))
  const hot = sev <= 2
  return (
    <li className="flex items-start gap-2 py-1">
      <span
        className={cn('num w-12 shrink-0 text-[10px] leading-4 tracking-[0.04em]', hot ? (criticalTone(owner) === 'red' ? 'text-[var(--r500)]' : 'font-bold text-[var(--g50)]') : sev <= 4 ? 'text-[var(--g200)]' : 'text-[var(--g500)]')}
      >
        {t(`sev.${sev}` as AnyKey)}
      </span>
      <span className="min-w-0 flex-1 text-ed-value break-words text-[var(--g100)]" data-selectable="">
        {s.text}
      </span>
      <span className="num shrink-0 text-[11px] leading-4 text-[var(--g500)]">{ago}</span>
    </li>
  )
}

function Capabilities({ caps }: { caps: Vehicle['capabilities'] }) {
  const t = useT8()
  const list: { key: keyof Vehicle['capabilities']; label: AnyKey }[] = [
    { key: 'manual', label: 'cap.manual' },
    { key: 'althold', label: 'cap.althold' },
    { key: 'position', label: 'cap.position' },
  ]
  return (
    <div className="flex flex-wrap gap-1.5">
      {list.map((c) => {
        const on = caps[c.key]
        return (
          <span
            key={c.key}
            className={cn('inline-flex h-6 items-center rounded-full border px-2.5 text-[11px]', on ? 'border-[var(--g600)] text-[var(--g100)]' : 'border-dashed border-[var(--g700)] text-[var(--g600)] line-through')}
            aria-label={on ? t(c.label) : t('insp.capMissing', { cap: t(c.label) })}
            data-cap={c.key}
            data-on={on ? '' : undefined}
          >
            {t(c.label)}
          </span>
        )
      })}
    </div>
  )
}

export function OverviewTab({ vehicleId }: { vehicleId: string }) {
  const t = useT8()
  const v = useVehicle(vehicleId)
  const len = useLength()
  if (!v) return null
  const notLive = v.link.state !== 'ok' && v.link.state !== 'delayed'
  const texts = [...v.statustext].sort((a, b) => b.t - a.t).slice(0, 3)
  return (
    <div className="flex flex-col gap-4" data-inspector-overview="">
      <PreflightLine v={v} />
      <div className="grid grid-cols-2 gap-x-3 gap-y-3 px-3">
        <Readout label={t('insp.field.battery')}>
          <BatteryReadout battery={v.battery} />
        </Readout>
        <Readout label={t('insp.field.link')}>
          <LinkStatus link={v.link} />
        </Readout>
        <Readout label={t('insp.field.mode')}>
          <Value value={v.mode} reason={t('fleet.reason.noMode')} className="text-[12px]" />
        </Readout>
        <Readout label={t('insp.field.landed')}>
          <span>{v.crashed ? t('status.crashed') : t(`landed.${v.landed}` as AnyKey)}</span>
        </Readout>
        <Readout label={t('insp.field.altitude')}>
          <Value value={len(v.altitude_m)} reason={notLive ? t('value.reason.notLive') : t('value.reason.notSent', { msg: 'VFR_HUD' })} />
        </Readout>
        <Readout label={t('insp.field.flightTime')}>
          <Value value={duration(v.flight_time_s)} reason={t('value.reason.noData')} />
        </Readout>
        <Readout label={t('insp.field.gps')}>
          <Value value={gpsText(v.gps, t)} reason={v.kind === 'sim' ? t('value.reason.unknown') : t('value.reason.notSent', { msg: 'GPS_RAW_INT' })} className="text-[12px]" />
        </Readout>
        <Readout label={t('insp.field.position')}>
          <span className={v.capabilities.position ? 'text-[var(--g100)]' : 'text-[var(--g500)]'}>
            {v.capabilities.position ? t('insp.position.local') : t('insp.position.none')}
          </span>
        </Readout>
      </div>
      <HealthBlock health={v.health} />
      <Block title={t('video.group')}>
        <VideoSourceGroup vehicleId={vehicleId} />
      </Block>
      <Block title={t('insp.statustext')}>
        {texts.length ? (
          <ul className="flex flex-col">
            {texts.map((s) => (
              <StatusTextRow key={`${s.t}-${s.text}`} s={s} />
            ))}
          </ul>
        ) : (
          <p className="text-hud-sub text-[var(--g500)]">{t('insp.noStatustext')}</p>
        )}
      </Block>
      <Block title={t('insp.capabilities')}>
        <Capabilities caps={v.capabilities} />
      </Block>
    </div>
  )
}

export const OverviewTabMemo = React.memo(OverviewTab)
