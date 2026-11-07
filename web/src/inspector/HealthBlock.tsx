// Inspector → Overview → Flight health (03 §5.9): the backend's rolling diagnostics, one row per item, and for
// every finding the likely cause and what to change. Coarse by design; the flight log is the reference.
import type { FlightHealthView, HealthItem } from '@/api'
import { cn } from '@/lib/utils'
import { criticalTone } from '@/store/red'
import { useRedOwner } from '@/store/selectors'
import { Icon } from '@/ui/icons'
import { useHT, type HealthKey } from './health.strings'
import { Block } from './parts'

const UNITS: Record<string, string> = { 'deg/s': '°/s', deg: '°', us: 'µs', m: 'm', 'm/s': 'm/s', 'm/s2': 'm/s²', '%': '%', 'V/cell': 'V/cell', Hz: 'Hz' }

function num(x: unknown, digits = 1): string {
  return typeof x === 'number' && Number.isFinite(x) ? x.toFixed(digits) : '—'
}

function valueText(item: HealthItem): string {
  if (item.value === null || item.level === 'na') return '—'
  const digits = item.unit === '%' || item.unit === 'us' || item.unit === 'Hz' ? 0 : item.unit === 'V/cell' || item.unit === 'm/s' ? 2 : 1
  return `${num(item.value, digits)} ${UNITS[item.unit] ?? item.unit}`
}

/** The cause-and-fix note for a finding, or null when the item is fine or not judged. */
export function fixFor(item: HealthItem): { key: HealthKey; params: Record<string, string | number> } | null {
  if (item.level !== 'warn' && item.level !== 'bad') return null
  const d = (item.detail ?? {}) as Record<string, unknown>
  const v = item.value ?? 0
  switch (item.key) {
    case 'lean':
      return { key: 'fh.fix.lean', params: { roll: num(d.roll), pitch: num(d.pitch) } }
    case 'hover_throttle':
      return { key: d.low ? 'fh.fix.hover_throttle.low' : 'fh.fix.hover_throttle.high', params: { v: Math.round(v) } }
    case 'motors': {
      const sat = typeof d.saturated === 'number' ? d.saturated : 0
      if (sat >= 0.03) return { key: 'fh.fix.motors.saturated', params: { pct: Math.round(sat * 100) } }
      const means = Array.isArray(d.means) ? (d.means as number[]).join(' / ') : '—'
      return { key: 'fh.fix.motors', params: { n: typeof d.high === 'number' ? d.high : '—', means } }
    }
    case 'vibration':
      return typeof d.clipping === 'number' && d.clipping > 0 ? { key: 'fh.fix.vibration.clip', params: { n: d.clipping } } : { key: 'fh.fix.vibration', params: { v: num(v) } }
    case 'battery_sag':
      return { key: 'fh.fix.battery_sag', params: { v: num(v, 2) } }
    case 'link':
      return { key: 'fh.fix.link', params: { loss: num(item.value), rtt: num(d.rtt_ms, 0) } }
    case 'stick_stream':
      return { key: 'fh.fix.stick_stream', params: { hz: num(item.value, 0), gap: num(d.max_gap_s, 2) } }
    default:
      return { key: `fh.fix.${item.key}` as HealthKey, params: {} }
  }
}

function Row({ item }: { item: HealthItem }) {
  const t = useHT()
  const owner = useRedOwner()
  const fix = fixFor(item)
  const bad = item.level === 'bad'
  return (
    <li className="py-1" data-health-item={item.key} data-level={item.level}>
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-ed-value text-[var(--g200)]">{t(`fh.item.${item.key}` as HealthKey)}</span>
        <span className={cn('num text-[12px]', item.level === 'na' ? 'text-[var(--g600)]' : 'text-[var(--g100)]')}>{valueText(item)}</span>
        <span
          className={cn(
            'w-10 text-right text-[11px]',
            bad ? (criticalTone(owner) === 'red' ? 'font-[560] text-[var(--r500)]' : 'font-bold text-[var(--g50)]') : item.level === 'warn' ? 'font-[560] text-[var(--g50)]' : 'text-[var(--g500)]',
          )}
        >
          {t(`fh.level.${item.level}` as HealthKey)}
        </span>
      </div>
      {fix ? (
        <p className="mt-0.5 flex gap-1.5 text-hud-sub text-[var(--g300)]" data-selectable="">
          <Icon icon="alert" className="mt-0.5 size-3 shrink-0 text-[var(--g400)]" />
          <span>{t(fix.key, fix.params)}</span>
        </p>
      ) : null}
    </li>
  )
}

export function HealthBlock({ health }: { health: FlightHealthView }) {
  const t = useHT()
  const order = { bad: 0, warn: 1, ok: 2, na: 3 } as const
  const items = [...health.items].sort((a, b) => order[a.level] - order[b.level])
  return (
    <Block
      title={t('fh.title')}
      right={
        <span className={cn('text-[11px]', health.state === 'bad' || health.state === 'warn' ? 'font-[560] text-[var(--g50)]' : 'text-[var(--g400)]')} data-health={health.state}>
          {t(`fh.state.${health.state}` as HealthKey)}
        </span>
      }
    >
      {health.state === 'idle' ? (
        <p className="text-hud-sub text-[var(--g500)]">{t('fh.idle')}</p>
      ) : (
        <>
          <ul className="flex flex-col divide-y divide-[var(--border)]">
            {items.map((i) => (
              <Row key={i.key} item={i} />
            ))}
          </ul>
          <p className="text-hud-sub text-[var(--g500)]">{t('fh.footer', { s: health.window_s })}</p>
        </>
      )}
    </Block>
  )
}
