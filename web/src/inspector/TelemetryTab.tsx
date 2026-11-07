// Inspector Telemetry (02 §11, R-11, R-12): grouped values (attitude, position & velocity, battery, GPS, sensors,
// RC & servo outputs, link) and the Raw MAVLink sub-page (last frame and rate per message, fields on selection,
// pausable). Every unknown value is "—" with its reason.
import * as React from 'react'
import { api, type Vehicle } from '@/api'
import { useLang } from '@/i18n'
import { amps, angle, climb, hertz, heading, millis, percent, seconds1, speed, volts, yaw, radToDeg, signed } from '@/lib/format'
import { cn } from '@/lib/utils'
import { useVehicle } from '@/store/selectors'
import { Button } from '@/ui/components/ui/button'
import { ToggleGroup, ToggleGroupItem } from '@/ui/components/ui/toggle-group'
import { StateIcon } from '@/ui/icons'
import { Value } from '@/ui/relay'
import { useT8, type AnyKey } from '@/fleet/copy'
import { useLength } from '@/settings/prefs'
import { Block, Kv, KvList } from './parts'
import { gpsText } from './OverviewTab'
import { usePoll } from './usePoll'

type T = ReturnType<typeof useT8>

const rate = (r: number | null | undefined) => {
  const s = signed(typeof r === 'number' ? radToDeg(r) : null, 3, 1)
  return s === null ? null : `${s}°/s`
}

function Channels({ label, values, reason }: { label: string; values: number[] | null; reason: string }) {
  const t = useT8()
  const list = (values ?? []).slice(0, 8)
  return (
    <div className="flex flex-col gap-1">
      <span className="text-hud-sub text-[var(--g400)]">{label}</span>
      {list.length === 0 ? (
        <Value value={null} reason={reason} className="text-[12px]" />
      ) : (
        <ul className="flex flex-col gap-1">
          {list.map((pwm, i) => {
            const valid = pwm > 0 && pwm < 65535
            const frac = valid ? Math.min(1, Math.max(0, (pwm - 1000) / 1000)) : 0
            return (
              <li key={i} className="grid grid-cols-[36px_1fr_40px] items-center gap-2">
                <span className="num text-[10px] text-[var(--g500)]">{t('tele.channel', { n: i + 1 })}</span>
                <span className="ch-bar" aria-hidden="true">
                  <i style={{ transform: `scaleX(${frac})` }} />
                  <b />
                </span>
                <span className="num text-right text-[11px] text-[var(--g200)]">{valid ? pwm : '—'}</span>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

function SensorsTable({ sensors }: { sensors: Vehicle['sensors'] }) {
  const t = useT8()
  if (!sensors.length) return <p className="text-hud-sub text-[var(--g500)]">{t('tele.noSensors')}</p>
  const yn = (b: boolean, strong = false) => (
    <span className={cn(b ? 'text-[var(--g200)]' : strong ? 'font-[560] text-[var(--g50)]' : 'text-[var(--g500)]')}>{b ? t('tele.yes') : t('tele.no')}</span>
  )
  return (
    <table className="w-full text-[11px]" data-lf-table="">
      <thead>
        <tr className="border-b border-[var(--foreground)] text-left">
          <th className="cap py-1 font-[560]">{t('tele.sensor.name')}</th>
          <th className="cap py-1 text-right font-[560]">{t('tele.sensor.present')}</th>
          <th className="cap py-1 text-right font-[560]">{t('tele.sensor.enabled')}</th>
          <th className="cap py-1 text-right font-[560]">{t('tele.sensor.healthy')}</th>
        </tr>
      </thead>
      <tbody>
        {sensors.map((s) => (
          <tr key={s.name} className="border-b border-dotted border-[var(--border)]">
            <td className="num py-1 text-[var(--g200)]">{s.name}</td>
            <td className="py-1 text-right">{yn(s.present)}</td>
            <td className="py-1 text-right">{yn(s.enabled)}</td>
            <td className="py-1 text-right">{s.present && s.enabled ? yn(s.healthy, true) : <span className="text-[var(--g600)]">—</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Values({ v, t }: { v: Vehicle; t: T }) {
  const len = useLength()
  const notLive = v.link.state !== 'ok' && v.link.state !== 'delayed'
  const r = (msg: string) => (notLive ? t('value.reason.notLive') : t('value.reason.notSent', { msg }))
  const p = v.position
  const noPos = t('value.reason.noPosition')
  const b = v.battery
  const perCell = typeof b.voltage === 'number' && typeof b.cells === 'number' && b.cells > 0 ? `${(b.voltage / b.cells).toFixed(2)} V` : null
  const src = v.kind === 'sim' ? t('tele.frame.sim') : [v.host, v.bridge?.version ? `mavesp8266 ${v.bridge.version}` : null, v.bridge?.source?.toUpperCase()].filter(Boolean).join(' · ')
  return (
    <div className="flex flex-col gap-4">
      <Block title={t('tele.attitude')}>
        <KvList>
          <Kv label={t('axis.roll')} value={angle(v.attitude?.roll)} reason={r('ATTITUDE')} />
          <Kv label={t('axis.pitch')} value={angle(v.attitude?.pitch)} reason={r('ATTITUDE')} />
          <Kv label={t('axis.yaw')} value={yaw(v.attitude?.yaw)} reason={r('ATTITUDE')} />
          <Kv label={`${t('tele.rates')} · ${t('axis.roll')}`} value={rate(v.rates?.roll)} reason={r('ATTITUDE')} />
          <Kv label={`${t('tele.rates')} · ${t('axis.pitch')}`} value={rate(v.rates?.pitch)} reason={r('ATTITUDE')} />
          <Kv label={`${t('tele.rates')} · ${t('axis.yaw')}`} value={rate(v.rates?.yaw)} reason={r('ATTITUDE')} />
        </KvList>
      </Block>
      <Block title={t('tele.positionVelocity')}>
        <KvList>
          <Kv label={t('tele.frame')} value={p ? t(p.frame === 'sim' ? 'tele.frame.sim' : 'tele.frame.local') : null} reason={noPos} />
          <Kv label={t('tele.east')} value={len(p?.x)} reason={noPos} />
          <Kv label={t('tele.north')} value={len(p?.y)} reason={noPos} />
          <Kv label={t('tele.up')} value={len(p?.z)} reason={noPos} />
          <Kv label={t('tele.vEast')} value={speed(p?.vx)} reason={noPos} />
          <Kv label={t('tele.vNorth')} value={speed(p?.vy)} reason={noPos} />
          <Kv label={t('tele.vUp')} value={climb(p?.vz)} reason={noPos} />
          <Kv label={t('tele.age')} value={seconds1(p?.age_s)} reason={noPos} />
          <Kv label={t('tele.altitude')} value={len(v.altitude_m)} reason={r('VFR_HUD')} />
          <Kv label={t('tele.climb')} value={climb(v.climb_ms)} reason={r('VFR_HUD')} />
          <Kv label={t('tele.groundspeed')} value={speed(v.groundspeed_ms)} reason={r('VFR_HUD')} />
          <Kv label={t('tele.heading')} value={heading(v.heading_deg)} reason={r('VFR_HUD')} />
          <Kv label={t('tele.throttle')} value={percent(v.throttle_pct)} reason={r('VFR_HUD')} />
          <Kv label={t('tele.lat')} value={v.global ? v.global.lat.toFixed(7) : null} reason={t('value.reason.noGps')} />
          <Kv label={t('tele.lon')} value={v.global ? v.global.lon.toFixed(7) : null} reason={t('value.reason.noGps')} />
          <Kv label={t('tele.altAmsl')} value={len(v.global?.alt, 1)} reason={t('value.reason.noGps')} />
        </KvList>
      </Block>
      <Block title={t('tele.battery')}>
        <KvList>
          <Kv label={t('tele.voltage')} value={volts(b.voltage)} reason={r('SYS_STATUS')} />
          <Kv label={t('tele.current')} value={amps(b.current)} reason={r('SYS_STATUS')} />
          <Kv label={t('tele.remaining')} value={percent(b.remaining_pct)} reason={r('BATTERY_STATUS')} />
          <Kv label={t('tele.cells')} value={b.cells !== null ? String(b.cells) : null} reason={r('BATTERY_STATUS')} />
          <Kv label={t('tele.perCell')} value={perCell} reason={r('BATTERY_STATUS')} />
          <Kv label={t('tele.level')} value={t(`tele.level.${b.level}` as AnyKey)} />
        </KvList>
      </Block>
      <Block title={t('tele.gps')}>
        <KvList>
          <Kv label={t('tele.fix')} value={gpsText(v.gps, t)} reason={v.kind === 'sim' ? t('value.reason.unknown') : r('GPS_RAW_INT')} />
          <Kv label={t('tele.sats')} value={v.gps?.sats !== null && v.gps?.sats !== undefined ? String(v.gps.sats) : null} reason={t('value.reason.noGps')} />
          <Kv label={t('tele.hdop')} value={typeof v.gps?.hdop === 'number' ? v.gps.hdop.toFixed(2) : null} reason={t('value.reason.noGps')} />
        </KvList>
      </Block>
      <Block title={t('tele.sensors')}>
        <SensorsTable sensors={v.sensors} />
      </Block>
      <Block title={t('tele.rcServo')}>
        <div className="grid grid-cols-1 gap-3">
          <Channels label={t('tele.rc')} values={v.rc} reason={r('RC_CHANNELS')} />
          <Channels label={t('tele.servo')} values={v.servo} reason={r('SERVO_OUTPUT_RAW')} />
        </div>
      </Block>
      <Block title={t('tele.link')}>
        <KvList>
          <Kv label={t('tele.link.rx')} value={hertz(v.link.rx_hz)} reason={t('value.reason.noData')} />
          <Kv label={t('tele.link.tx')} value={hertz(v.link.tx_hz)} reason={t('value.reason.noData')} />
          <Kv label={t('tele.link.loss')} value={typeof v.link.loss_pct === 'number' ? `${v.link.loss_pct.toFixed(1)} %` : null} reason={t('value.reason.noData')} />
          <Kv label={t('tele.link.heartbeat')} value={seconds1(v.link.heartbeat_age_s)} reason={t('value.reason.noData')} />
          <Kv label={t('tele.link.rtt')} value={millis(v.link.rtt_ms)} reason={t('value.reason.notSent', { msg: 'TIMESYNC' })} />
          <Kv label={t('tele.link.source')} value={src || null} reason={t('value.reason.unknown')} />
        </KvList>
      </Block>
    </div>
  )
}

// ------------------------------------------------------------------------------------------------ raw MAVLink

interface RawMessage {
  fields: Record<string, unknown>
  t: number
  age_s: number
  hz: number
  count: number
  src?: [number, number]
}

function fieldText(v: unknown): string {
  if (Array.isArray(v)) return `[${v.join(', ')}]`
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : String(Number(v.toPrecision(7)))
  if (v === null || v === undefined) return '—'
  return String(v)
}

function RawMavlink({ id }: { id: string }) {
  const t = useT8()
  const lang = useLang()
  const [paused, setPaused] = React.useState(false)
  const [sel, setSel] = React.useState<string | null>(null)
  const poll = usePoll(() => api.messages(id) as Promise<{ messages: Record<string, RawMessage> }>, 1000, id, !paused)
  const msgs = poll.data?.messages ?? {}
  const names = Object.keys(msgs).sort()
  const cur = sel ? msgs[sel] : undefined
  const err = poll.error as { text?: (l: 'en' | 'zh') => string; message?: string } | null
  return (
    <div className="flex flex-col gap-3 px-3" data-raw-mavlink="">
      <div className="flex items-center gap-2">
        <span className="num text-hud-sub text-[var(--g500)]">{names.length ? `${names.length}` : ''}</span>
        {paused ? <span className="cap">{t('tele.raw.paused')}</span> : null}
        <span className="flex-1" />
        <Button variant="outline" size="sm" onClick={() => setPaused((p) => !p)} className="gap-1.5" aria-pressed={paused}>
          <StateIcon icon="tl.play" alt={!paused} />
          {paused ? t('tele.resume') : t('tele.pause')}
        </Button>
      </div>
      {poll.error && !names.length ? (
        <p className="text-hud-sub text-[var(--g50)]">{t('tele.raw.loadFailed', { reason: err?.text?.(lang) ?? err?.message ?? '' })}</p>
      ) : names.length === 0 ? (
        <p className="text-hud-sub text-[var(--g500)]">{poll.loading ? '…' : t('tele.raw.empty')}</p>
      ) : (
        <table className="w-full text-[11px]" data-lf-table="">
          <thead>
            <tr className="border-b border-[var(--foreground)] text-left">
              <th className="cap py-1 font-[560]">{t('tele.raw.message')}</th>
              <th className="cap py-1 text-right font-[560]">{t('tele.raw.rate')}</th>
              <th className="cap py-1 text-right font-[560]">{t('tele.raw.last')}</th>
            </tr>
          </thead>
          <tbody>
            {names.map((n) => {
              const m = msgs[n]
              return (
                <tr
                  key={n}
                  className={cn('raw-row cursor-default border-b border-dotted border-[var(--border)]', sel === n && 'bg-[var(--g850)]')}
                  data-selected={sel === n ? '' : undefined}
                  onClick={() => setSel(sel === n ? null : n)}
                >
                  <td className="num py-1 text-[var(--g200)]">{n}</td>
                  <td className="num py-1 text-right text-[var(--g300)]">{m.hz > 0 ? hertz(m.hz) : '—'}</td>
                  <td className="num py-1 text-right text-[var(--g500)]">{seconds1(m.age_s)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
      {cur && sel ? (
        <div className="flex flex-col gap-1.5" data-raw-fields={sel}>
          <div className="flex items-center gap-2">
            <span className="num text-[11px] font-[560] text-[var(--g100)]">{sel}</span>
            <span className="num text-[10px] text-[var(--g500)]">
              {t('tele.raw.count')} {cur.count}
              {cur.src ? ` · ${cur.src.join('/')}` : ''}
            </span>
          </div>
          <table className="w-full text-[11px]" data-lf-table="">
            <tbody>
              {Object.entries(cur.fields).map(([k, val]) => (
                <tr key={k} className="border-b border-dotted border-[var(--border)]">
                  <td className="num py-0.5 pr-2 align-top text-[var(--g400)]">{k}</td>
                  <td className="num py-0.5 text-right break-all text-[var(--g200)]" data-selectable="">
                    {fieldText(val)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : names.length ? (
        <p className="text-hud-sub text-[var(--g500)]">{t('tele.raw.select')}</p>
      ) : null}
    </div>
  )
}

export function TelemetryTab({ vehicleId }: { vehicleId: string }) {
  const t = useT8()
  const v = useVehicle(vehicleId)
  const [view, setView] = React.useState<'values' | 'raw'>('values')
  if (!v) return null
  return (
    <div className="flex flex-col gap-3" data-inspector-telemetry={view}>
      <div className="px-3">
        <ToggleGroup value={[view]} onValueChange={(x: unknown[]) => x[0] && setView(x[0] as 'values' | 'raw')} spacing={0} className="w-full rounded-md bg-[var(--g950)] p-0.5 ring-1 ring-[var(--border)]">
          <ToggleGroupItem value="values" className="h-6 flex-1 rounded-[5px] text-[11px] text-[var(--g400)] hover:bg-transparent data-[pressed]:text-[var(--g50)]">
            {t('tele.view.values')}
          </ToggleGroupItem>
          <ToggleGroupItem value="raw" className="h-6 flex-1 rounded-[5px] text-[11px] text-[var(--g400)] hover:bg-transparent data-[pressed]:text-[var(--g50)]">
            {t('tele.raw')}
          </ToggleGroupItem>
        </ToggleGroup>
      </div>
      {view === 'values' ? <Values v={v} t={t} /> : <RawMavlink id={vehicleId} />}
    </div>
  )
}
