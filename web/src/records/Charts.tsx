// Records charts (02 §13): lieflat line charts on a shared time axis (96 high each), the control and event tracks in
// the relay-timeline language, and the time axis. Every element scrubs the shared cursor.
import * as React from 'react'
import { pickText, useLang } from '@/i18n'
import { clock } from '@/lib/format'
import { armedTone } from '@/store/red'
import { useRedOwner } from '@/store/selectors'
import { useT8 } from '@/fleet/copy'
import { armedIntervals, extent, indexAt, linePath, timeTicks, type ControlSegment, type RecordEvent, type Samples } from './model'

export const GUTTER = 48
const RIGHT = 8

export interface Scale {
  t0: number
  t1: number
  width: number
  x(t: number): number
  t(x: number): number
}

export function makeScale(t0: number, t1: number, width: number): Scale {
  const w = Math.max(1, width - GUTTER - RIGHT)
  const span = Math.max(1e-6, t1 - t0)
  return {
    t0,
    t1,
    width,
    x: (t) => GUTTER + ((t - t0) / span) * w,
    t: (x) => t0 + (Math.min(Math.max(x - GUTTER, 0), w) / w) * span,
  }
}

export function useWidth<T extends HTMLElement>(): [(el: T | null) => void, number] {
  const [el, setEl] = React.useState<T | null>(null)
  const [w, setW] = React.useState(0)
  React.useLayoutEffect(() => {
    if (!el) return
    const ro = new ResizeObserver((entries) => setW(Math.round(entries[0].contentRect.width)))
    // the observer reports the initial size right after observe()
    ro.observe(el)
    return () => ro.disconnect()
  }, [el])
  return [setEl, w]
}

/** Pointer scrubbing: press and drag anywhere on the element moves the cursor. */
function useScrub(scale: Scale, onScrub: (t: number) => void) {
  const drag = React.useRef(false)
  const at = (e: React.PointerEvent<Element>) => {
    const r = (e.currentTarget as Element).getBoundingClientRect()
    onScrub(scale.t(e.clientX - r.left))
  }
  return {
    onPointerDown: (e: React.PointerEvent<Element>) => {
      drag.current = true
      ;(e.currentTarget as Element).setPointerCapture?.(e.pointerId)
      at(e)
    },
    onPointerMove: (e: React.PointerEvent<Element>) => {
      if (drag.current) at(e)
    },
    onPointerUp: () => {
      drag.current = false
    },
    onPointerCancel: () => {
      drag.current = false
    },
  }
}

export interface Series {
  values: readonly (number | null)[]
  /** 'primary' = lf-data 1.5 px, 'secondary' = lf-data2 dashed */
  tone: 'primary' | 'secondary'
  label?: string
}

export function LineChart({ title, series, ts, scale, cursor, onScrub, fmt, minSpan = 0, bounds, height = 96 }: {
  title: string
  series: Series[]
  ts: readonly number[]
  scale: Scale
  cursor: number | null
  onScrub(t: number): void
  fmt(v: number): string
  minSpan?: number
  /** physical limits the padded extent never crosses, e.g. [0, 100] for throttle */
  bounds?: [number, number]
  height?: number
}) {
  const scrub = useScrub(scale, onScrub)
  const all = series.flatMap((s) => s.values as (number | null)[])
  const raw = extent(all, 0.1, minSpan)
  const ext: [number, number] | null = raw && bounds ? [Math.max(bounds[0], raw[0]), Math.min(bounds[1], raw[1])] : raw
  const top = 6
  const bottom = height - 6
  const y = (v: number) => (ext ? bottom - ((v - ext[0]) / (ext[1] - ext[0])) * (bottom - top) : height / 2)
  const i = cursor === null ? -1 : indexAt(ts, cursor)
  const now = series.map((s) => (i >= 0 ? s.values[i] : null))
  const cx = cursor === null ? null : scale.x(cursor)
  const w = scale.width
  return (
    <figure className="flex flex-col gap-1" data-chart={title}>
      <figcaption className="flex h-5 items-center gap-3 pr-2" style={{ paddingLeft: GUTTER }}>
        <span className="cap">{title}</span>
        {series.length > 1
          ? series.map((s) => (
              <span key={s.label} className="flex items-center gap-1.5 text-[10px] text-[var(--g400)]">
                <svg width="14" height="4" aria-hidden="true">
                  <line x1="0" y1="2" x2="14" y2="2" className={s.tone === 'primary' ? 'lf-line' : 'rec-line2'} />
                </svg>
                {s.label}
              </span>
            ))
          : null}
        <span className="flex-1" />
        <span className="num text-[11px] text-[var(--g100)]">
          {now.map((v) => (typeof v === 'number' ? fmt(v) : '—')).join(' / ')}
        </span>
      </figcaption>
      {w > 0 ? (
        <svg className="lf-svg rec-svg" width={w} height={height} viewBox={`0 0 ${w} ${height}`} {...scrub} role="img" aria-label={title}>
          <line x1={GUTTER} x2={w - RIGHT} y1={bottom + 0.5} y2={bottom + 0.5} className="lf-floor" />
          <line x1={GUTTER} x2={w - RIGHT} y1={top + 0.5} y2={top + 0.5} className="lf-grid" />
          <line x1={GUTTER} x2={w - RIGHT} y1={(top + bottom) / 2} y2={(top + bottom) / 2} className="lf-grid" />
          {ext ? (
            <>
              <text x={GUTTER - 6} y={top + 4} textAnchor="end" className="lf-axis" fontSize="10">
                {fmt(ext[1])}
              </text>
              <text x={GUTTER - 6} y={bottom} textAnchor="end" className="lf-axis" fontSize="10">
                {fmt(ext[0])}
              </text>
            </>
          ) : null}
          {series.map((s) => (
            <path key={s.label ?? s.tone} d={linePath(ts, s.values, scale.x, y)} className={s.tone === 'primary' ? 'lf-line' : 'rec-line2'} />
          ))}
          {cx !== null ? <line x1={cx} x2={cx} y1={0} y2={height} className="rec-cursor" /> : null}
          {cx !== null
            ? now.map((v, k) => (typeof v === 'number' ? <circle key={k} cx={cx} cy={y(v)} r={2.5} className="lf-hollow" /> : null))
            : null}
        </svg>
      ) : (
        <div style={{ height }} />
      )}
    </figure>
  )
}

const LANE = 18

/** Control-authority lane: agent g500 with the session name, human g50, idle empty (relay-timeline language). */
export function ControlTrack({ segments, scale, end, cursor, onScrub }: { segments: ControlSegment[]; scale: Scale; end: number; cursor: number | null; onScrub(t: number): void }) {
  const t = useT8()
  const scrub = useScrub(scale, onScrub)
  const w = scale.width
  const cx = cursor === null ? null : scale.x(cursor)
  return (
    <div className="flex flex-col gap-1" data-track="control">
      <div className="flex h-4 items-center gap-3" style={{ paddingLeft: GUTTER }}>
        <span className="cap">{t('records.track.control')}</span>
        <span className="flex items-center gap-1.5 text-[10px] text-[var(--g400)]">
          <i className="inline-block h-2 w-3 rounded-[1px] bg-[var(--g500)]" aria-hidden="true" />
          {t('records.ctl.agent')}
        </span>
        <span className="flex items-center gap-1.5 text-[10px] text-[var(--g400)]">
          <i className="inline-block h-2 w-3 rounded-[1px] bg-[var(--g50)]" aria-hidden="true" />
          {t('records.ctl.human')}
        </span>
        <span className="flex items-center gap-1.5 text-[10px] text-[var(--g400)]">
          <i className="inline-block h-2 w-3 rounded-[1px] bg-[var(--g300)]" aria-hidden="true" />
          {t('records.ctl.program')}
        </span>
      </div>
      {w > 0 ? (
        <svg className="lf-svg rec-svg" width={w} height={LANE} viewBox={`0 0 ${w} ${LANE}`} {...scrub} role="img" aria-label={t('records.track.control')}>
          <rect x={GUTTER} y={0} width={Math.max(0, w - GUTTER - RIGHT)} height={LANE} className="lf-track" />
          {segments.map((s, i) => {
            if (s.controller !== 'agent' && s.controller !== 'human' && s.controller !== 'program') return null
            const x0 = scale.x(Math.max(s.start, scale.t0))
            const x1 = scale.x(Math.min(s.end ?? end, scale.t1))
            const width = Math.max(1, x1 - x0)
            const kind = s.controller === 'agent' ? 'agent' : s.controller === 'program' ? 'program' : 'human'
            return (
              <g key={i}>
                <rect x={x0} y={2} width={width} height={LANE - 4} rx={1} className={`rec-seg-${kind}`} />
                {s.who && width > 48 ? (
                  <text x={x0 + 4} y={LANE / 2 + 3} fontSize="9" className={`rec-seg-label-${kind}`}>
                    {s.who}
                  </text>
                ) : null}
              </g>
            )
          })}
          {cx !== null ? <line x1={cx} x2={cx} y1={0} y2={LANE} className="rec-cursor" /> : null}
        </svg>
      ) : null}
    </div>
  )
}

const MAJOR = new Set(['relay_handback', 'stop', 'stop_all', 'crashed', 'link_lost', 'possession_started', 'course_finished', 'course_aborted'])

/** Event lane: armed intervals as a bottom line (single-red rule), ticks for every event, marks for the major ones. */
export function EventTrack({ events, samples, scale, cursor, onScrub }: { events: RecordEvent[]; samples: Pick<Samples, 't' | 'armed'>; scale: Scale; cursor: number | null; onScrub(t: number): void }) {
  const t = useT8()
  const owner = useRedOwner()
  const scrub = useScrub(scale, onScrub)
  const w = scale.width
  const cx = cursor === null ? null : scale.x(cursor)
  const armed = armedIntervals(samples)
  const red = armedTone(owner) === 'red'
  return (
    <div className="flex flex-col gap-1" data-track="events">
      <div className="flex h-4 items-center gap-3" style={{ paddingLeft: GUTTER }}>
        <span className="cap">{t('records.track.events')}</span>
        <span className="num text-[10px] text-[var(--g500)]">{events.length}</span>
      </div>
      {w > 0 ? (
        <svg className="lf-svg rec-svg" width={w} height={LANE} viewBox={`0 0 ${w} ${LANE}`} {...scrub} role="img" aria-label={t('records.track.events')}>
          <rect x={GUTTER} y={0} width={Math.max(0, w - GUTTER - RIGHT)} height={LANE} className="lf-track" />
          {armed.map((a, i) => (
            <rect key={i} x={scale.x(a.start)} y={LANE - 1} width={Math.max(1, scale.x(a.end) - scale.x(a.start))} height={1} className={red ? 'rec-armed-red' : 'rec-armed'} />
          ))}
          {events.map((e) => {
            const x = Math.round(scale.x(e.t)) + 0.5
            if (!MAJOR.has(e.kind)) return <line key={e.seq} x1={x} x2={x} y1={6} y2={12} className="rec-tick" />
            if (e.kind === 'relay_handback') return <path key={e.seq} d={`M${x - 4} 13L${x} 5L${x + 4} 13Z`} className="rec-mark" />
            if (e.kind === 'crashed') return <path key={e.seq} d={`M${x - 4} 5L${x + 4} 13M${x + 4} 5L${x - 4} 13`} className="rec-mark-stroke" />
            if (e.kind === 'possession_started') return <rect key={e.seq} x={x - 2.5} y={6.5} width={5} height={5} className="rec-mark" />
            if (e.kind === 'course_finished' || e.kind === 'course_aborted') return <circle key={e.seq} cx={x} cy={9} r={3} className="rec-mark-stroke" />
            return <line key={e.seq} x1={x} x2={x} y1={2} y2={LANE - 3} className="rec-mark-stroke" />
          })}
          {cx !== null ? <line x1={cx} x2={cx} y1={0} y2={LANE} className="rec-cursor" /> : null}
        </svg>
      ) : null}
    </div>
  )
}

export function TimeAxis({ scale }: { scale: Scale }) {
  const w = scale.width
  if (w <= 0) return null
  const ticks = timeTicks(scale.t0, scale.t1, Math.max(2, Math.floor((w - GUTTER) / 110)))
  return (
    <svg className="lf-svg" width={w} height={16} viewBox={`0 0 ${w} 16`} aria-hidden="true">
      {ticks.map((tk) => (
        <g key={tk}>
          <line x1={scale.x(tk)} x2={scale.x(tk)} y1={0} y2={4} className="lf-floor" />
          <text x={scale.x(tk)} y={14} textAnchor="middle" fontSize="10" className="lf-axis">
            {clock(tk)}
          </text>
        </g>
      ))}
    </svg>
  )
}

/** Nearest event to t within `windowS` seconds, in the UI language. */
export function useNearestEvent(events: RecordEvent[], t: number | null, windowS: number): string | null {
  const lang = useLang()
  if (t === null) return null
  let best: RecordEvent | null = null
  for (const e of events) if (Math.abs(e.t - t) <= windowS && (!best || Math.abs(e.t - t) < Math.abs(best.t - t))) best = e
  return best ? pickText(lang, best.text_en, best.text_zh) : null
}
