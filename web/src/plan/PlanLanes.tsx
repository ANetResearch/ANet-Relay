// The time strip of a flight plan (14 §2): the steps as blocks on a time axis that always fills the width, with the
// planned height under them and, once flown, the height the aircraft really had (and that of the run before as a
// dashed line). The seconds are an estimate from the speed limits; a finished step's trace is fitted to its block.
// Also the top-down path preview of the monitor.
import * as React from 'react'
import { describe, type Estimate, type Leg, type PlanDoc, type Volume } from './model'
import { useRun, type TracePoint } from './run'
import { usePlan } from './store'
import { usePT, type PlanKey } from './strings'

const PAD = 12
const RULER = 18
const BLOCKS = 40

function useWidth(ref: React.RefObject<HTMLElement | null>): { w: number; h: number } {
  const [size, setSize] = React.useState({ w: 800, h: 160 })
  React.useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const read = () => setSize({ w: el.clientWidth, h: el.clientHeight })
    read()
    const ro = new ResizeObserver(read)
    ro.observe(el)
    return () => ro.disconnect()
  }, [ref])
  return size
}

/** Seconds on the plan's axis of a trace point: its step's block, filled in proportion to how long the step took. */
function planTime(p: TracePoint, legs: Leg[], took: Record<number, number>): number | null {
  const leg = legs[p.i]
  if (!leg) return null
  const whole = took[p.i] ?? Math.max(leg.seconds, p.s)
  return leg.t0 + Math.min(1, whole > 0 ? p.s / whole : 0) * leg.seconds
}

function tickStep(seconds: number, width: number): number {
  const want = (seconds / Math.max(1, width)) * 70
  return [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300].find((s) => s >= want) ?? 600
}

export function fmtT(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`
  return `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, '0')}`
}

export function PlanLanes({ doc, est, flying, readOnly, marks }: { doc: PlanDoc; est: Estimate; flying: number | null; readOnly: boolean; marks: ('ok' | 'bad' | null)[] }) {
  const pt = usePT()
  const root = React.useRef<HTMLDivElement | null>(null)
  const { w, h } = useWidth(root)
  const sel = usePlan((s) => s.sel)
  const more = usePlan((s) => s.more)
  const { trace, took, ghost, indexAt } = useRun()
  const [now, setNow] = React.useState(0)
  React.useEffect(() => {
    if (flying === null) return
    const id = window.setInterval(() => setNow(Date.now()), 150)
    return () => window.clearInterval(id)
  }, [flying])
  const total = Math.max(1, est.seconds)
  const scale = Math.max(0.01, (w - 2 * PAD) / total)
  const X = (t: number) => PAD + t * scale
  const altH = Math.max(40, h - RULER - BLOCKS - 6)
  const top = Math.max(1, est.height, ...trace.map((p) => p.alt), ...ghost.trace.map((p) => p.alt)) * 1.15
  const Y = (alt: number) => altH - 4 - (Math.max(0, alt) / top) * (altH - 12)
  const step = tickStep(total, w)
  const ticks = Array.from({ length: Math.floor(total / step) + 1 }, (_, i) => i * step)
  const planned = est.legs.flatMap((l) => [`${X(l.t0).toFixed(1)},${Y(l.from[2]).toFixed(1)}`, `${X(l.t0 + l.seconds).toFixed(1)},${Y(l.to[2]).toFixed(1)}`]).join(' ')
  const line = (points: TracePoint[], tk: Record<number, number>) =>
    points
      .map((p) => {
        const t = planTime(p, est.legs, tk)
        return t === null ? null : `${X(t).toFixed(1)},${Y(p.alt).toFixed(1)}`
      })
      .filter(Boolean)
      .join(' ')
  const leg = flying !== null ? est.legs[flying] : null
  const head = leg ? X(leg.t0 + Math.min(leg.seconds, Math.max(0, (now || indexAt) - indexAt) / 1000)) : null
  return (
    <div ref={root} className="plan-lanes" data-plan-lanes="" data-plan-scale={scale.toFixed(2)}>
      <div className="plan-ruler" style={{ height: RULER }}>
        {ticks.map((t) => (
          <span key={t} style={{ left: X(t) }}>
            {t < 60 ? t : fmtT(t)}
          </span>
        ))}
      </div>
      <div className="plan-blocks" style={{ height: BLOCKS }}>
        {est.legs.map((l) => {
          const s = doc.steps[l.index]
          if (!s) return null
          const on = sel === l.index
          return (
            <button
              key={l.index}
              type="button"
              className="plan-block"
              style={{ left: X(l.t0), width: Math.max(3, l.seconds * scale - 2) }}
              data-on={on ? '' : undefined}
              data-more={more.includes(l.index) ? '' : undefined}
              data-flying={flying === l.index ? '' : undefined}
              data-flown={flying !== null && l.index < flying ? '' : undefined}
              data-mark={marks[l.index] ?? undefined}
              data-plan-block={l.index}
              disabled={readOnly}
              tabIndex={-1}
              title={`${l.index + 1}. ${describe(s)} · ${fmtT(l.seconds)}`}
              onClick={(e) => usePlan.getState().select(l.index, e.shiftKey ? 'range' : e.metaKey || e.ctrlKey ? 'toggle' : 'one')}
            >
              <b>{pt(`plan.action.${s.action}` as PlanKey)}</b>
              <span>{describe(s).slice(s.action.length + 1)}</span>
            </button>
          )
        })}
      </div>
      <svg className="plan-alt" width={w} height={altH} data-plan-alt="">
        <text x={PAD} y={11} className="plan-alt-label">
          {pt('plan.alt')} · {top > 0 ? `${(top / 1.15).toFixed(1)} m` : ''}
        </text>
        <line x1={PAD} x2={w - PAD} y1={Y(0)} y2={Y(0)} className="plan-alt-ground" />
        {est.legs.map((l) => (
          <line key={l.index} x1={X(l.t0)} x2={X(l.t0)} y1={0} y2={altH} className="plan-alt-grid" />
        ))}
        <polyline points={planned} className="plan-alt-plan" />
        {ghost.trace.length ? <polyline points={line(ghost.trace, ghost.took)} className="plan-alt-ghost" data-plan-ghost="" /> : null}
        {trace.length ? <polyline points={line(trace, took)} className="plan-alt-trace" data-plan-trace={trace.length} /> : null}
        {head !== null ? <line x1={head} x2={head} y1={0} y2={altH} className="plan-head" /> : null}
      </svg>
      {head !== null ? <i className="plan-head-top" style={{ left: head, top: RULER, height: BLOCKS }} /> : null}
    </div>
  )
}

/** The plan from above: forward is up. The dashed circle is what the safety volume allows around the start. */
export function PathPreview({ est, volume, live }: { est: Estimate; volume: Volume | null; live: { f: number; l: number } | null }) {
  const pt = usePT()
  const sel = usePlan((s) => s.sel)
  const { trace, ghost } = useRun()
  const pts = est.legs.flatMap((l) => [l.from, l.to])
  const actual = trace.filter((p) => p.f !== null) as (TracePoint & { f: number; l: number })[]
  const before = ghost.trace.filter((p) => p.f !== null) as (TracePoint & { f: number; l: number })[]
  const safe = volume ? Math.min(volume.x, volume.y) / 2 : 0
  const reach = Math.max(1, est.radius, ...actual.map((p) => Math.hypot(p.f, p.l)), ...pts.map((p) => Math.hypot(p[0], p[1])))
  const span = Math.max(reach * 1.25, Math.min(safe, reach * 2.2))
  const S = 100 / (2 * span)
  // screen: forward up, left to the left
  const x = (l: number) => 50 - l * S
  const y = (f: number) => 50 - f * S
  const poly = (points: { f: number; l: number }[]) => points.map((p) => `${x(p.l).toFixed(2)},${y(p.f).toFixed(2)}`).join(' ')
  const unknown = est.legs.some((l) => l.unknown)
  return (
    <div className="plan-preview" data-plan-preview="">
      <span className="cap flex justify-between text-[10px] text-[var(--g400)]">
        {pt('plan.preview')}
        <span className="num">± {span.toFixed(span < 10 ? 1 : 0)} m</span>
      </span>
      <svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
        <line x1="50" y1="2" x2="50" y2="98" className="plan-pv-axis" />
        <line x1="2" y1="50" x2="98" y2="50" className="plan-pv-axis" />
        {safe > 0 ? <circle cx="50" cy="50" r={Math.min(200, safe * S)} className="plan-pv-safe" data-outside={est.radius + 0.5 > safe ? '' : undefined} /> : null}
        <text x="52" y="7" className="plan-pv-text">
          {pt('plan.preview.forward')}
        </text>
        {est.legs.map((l) =>
          Math.hypot(l.to[0] - l.from[0], l.to[1] - l.from[1]) > 1e-3 ? (
            <line key={l.index} x1={x(l.from[1])} y1={y(l.from[0])} x2={x(l.to[1])} y2={y(l.to[0])} className="plan-pv-leg" data-on={sel === l.index ? '' : undefined} />
          ) : null,
        )}
        {before.length > 1 ? <polyline points={poly(before)} className="plan-pv-ghost" /> : null}
        {actual.length > 1 ? <polyline points={poly(actual)} className="plan-pv-trace" /> : null}
        {est.legs.map((l) => (
          <circle key={l.index} cx={x(l.to[1])} cy={y(l.to[0])} r={sel === l.index ? 2.2 : 1.3} className="plan-pv-corner" data-on={sel === l.index ? '' : undefined} />
        ))}
        <rect x="48.2" y="48.2" width="3.6" height="3.6" className="plan-pv-start" />
        {live ? <circle cx={x(live.l)} cy={y(live.f)} r="2" className="plan-pv-live" /> : null}
      </svg>
      {unknown ? <p className="text-[10px] leading-tight text-[var(--g500)]">{pt('plan.preview.unknown')}</p> : null}
    </div>
  )
}
