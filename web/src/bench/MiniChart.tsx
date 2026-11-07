// Live mini charts of the running step (05 §4.5): one lieflat sparkline per series the backend streams at 5 Hz in
// step.live (S7 vibration axes; S9 tilt, throttle, vibration; S10 stick step and response). No animation: the data
// itself moves, so reduced motion needs no special case.
import * as React from 'react'
import type { ProcedureStep } from '@/api'
import { formatNumber, sparkline } from './model'
import { enB, useTB, type KeyB } from './copy'

const UNITS: Record<string, string> = {
  vibration_x: 'm/s²',
  vibration_y: 'm/s²',
  vibration_z: 'm/s²',
  vibration: 'm/s²',
  tilt: '°',
  throttle: '%',
  command: '',
  roll: '°',
  pitch: '°',
  yaw_rate: '°/s',
}

const W = 120
const H = 28

export function Sparkline({ name, values }: { name: string; values: readonly (number | null)[] }) {
  const t = useTB()
  const key = `bench.series.${name}` as KeyB
  const label = key in enB ? t(key) : name
  const s = React.useMemo(() => sparkline(values, W, H), [values])
  const zeroY = s.min < 0 && s.max > 0 ? 2 + (1 - (0 - s.min) / (s.max - s.min)) * (H - 4) : null
  return (
    <div className="flex items-center gap-2" data-bench-series={name}>
      <span className="w-20 flex-none truncate text-hud-sub text-[var(--g400)]">{label}</span>
      <svg className="lf-svg bench-chart min-w-0 flex-1" width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={label}>
        <line className="lf-floor" x1={0} y1={H - 0.5} x2={W} y2={H - 0.5} />
        {zeroY !== null ? <line className="zero" x1={0} y1={zeroY} x2={W} y2={zeroY} /> : null}
        {s.points ? <polyline className="lf-line" points={s.points} vectorEffect="non-scaling-stroke" /> : null}
      </svg>
      <span className="num w-16 flex-none text-right text-hud-sub text-[var(--g100)]">
        {formatNumber(s.last)}
        {s.last !== null && UNITS[name] ? ` ${UNITS[name]}` : ''}
      </span>
    </div>
  )
}

export function LiveCharts({ step }: { step: ProcedureStep }) {
  const t = useTB()
  const live = step.live
  if (!live) return null
  const series = Object.entries(live).filter((e): e is [string, (number | null)[]] => Array.isArray(e[1]) && e[1].length > 1)
  if (series.length === 0) return null
  return (
    <div className="flex flex-col gap-1" data-bench-live={step.id}>
      <span className="cap">{t('bench.live')}</span>
      {series.map(([name, values]) => (
        <Sparkline key={name} name={name} values={values} />
      ))}
    </div>
  )
}
