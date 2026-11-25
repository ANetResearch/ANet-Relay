// The tracks of the Timeline workspace (0.6.1): four lanes of blocks that always fill the width (fit is a state, so
// resizing the window or opening the code re-fits; zooming in leaves it), lanes that stretch with the height, block
// selection with Ctrl/⌘ and Shift, and the response lane: the altitude of this run against the one before it.
import * as React from 'react'
import { cn } from '@/lib/utils'
import { usePrefs } from '@/settings/prefs'
import { blocks, isManualMode, keyTimes, moveStart, resize, setValue, splitAt, sticksAt, TRACKS, type ArsDoc, type Block, type TrackName } from './model'
import { axesFor, fmtS, fmtStick, toPad } from './parts'
import { commitDoc, useRun, type TracePoint } from './run'
import { sameSel, useSequence, type Selection } from './store'
import { useST, type SeqKey } from './strings'

const RULER = 22
const STRIP = 34 // stick pairs above the ruler
const MINI = 13
const LABEL_W = 104
const PAD = 6
const EDGE = 6
const RESP = 48
const SIDE = 12 // free pixels left and right of the time axis when fitted
const STEPS = [0.1, 0.5, 1, 2, 5, 10, 30, 60]
const SCALES = [0.1, 0.25, 0.5, 1]

/** Vertical scale of a track: throttle 0..1; a direction track zooms to the smallest of ±0.1/0.25/0.5/1 that fits. */
export function trackRange(doc: ArsDoc, track: TrackName): [number, number] {
  if (track === 'throttle') return [0, 1]
  const peak = Math.max(0, ...(doc.tracks[track] ?? []).map((k) => Math.abs(k.v)))
  const s = SCALES.find((x) => peak <= x + 1e-9) ?? 1
  return [-s, s]
}
/** Text cut to what fits in `px` (about 5.6 px per character at 10 px). */
function fit(text: string, px: number): string {
  const n = Math.floor((px - 8) / 5.6)
  if (n <= 0) return ''
  return text.length <= n ? text : `${text.slice(0, Math.max(1, n - 1))}…`
}

/** Pixels per second that show the whole sequence in `width`. */
export const fitZoom = (width: number, duration: number) => Math.max(1, (width - 2 * SIDE) / Math.max(0.1, duration))

/** The blocks a Shift+click takes: from the primary to `to`, across the tracks between them. */
export function rangeSelection(doc: ArsDoc, from: Selection, to: Selection): Selection[] {
  const a = blocks(doc, from.track)[from.index]
  const b = blocks(doc, to.track)[to.index]
  if (!a || !b) return [to]
  const t0 = Math.min(a.start, b.start)
  const t1 = Math.max(a.end, b.end)
  const [r0, r1] = [TRACKS.indexOf(from.track), TRACKS.indexOf(to.track)].sort((x, y) => x - y)
  const out: Selection[] = []
  for (let r = r0; r <= r1; r++) for (const k of blocks(doc, TRACKS[r])) if (k.start < t1 - 1e-6 && k.end > t0 + 1e-6) out.push({ track: k.track, index: k.index })
  return out
}

type Drag = { kind: 'value' | 'end' | 'start' | 'scrub'; track: TrackName; index: number; x0: number; y0: number; base: ArsDoc; v0: number; len0: number; start0: number; set: Selection[] }

export interface TracksProps {
  doc: ArsDoc
  /** the code has problems: the last valid document, dimmed, not editable */
  readOnly: boolean
  playhead: number | null
  /** only the stick strip and the ruler (above the editor in the code view) */
  mini?: boolean
  rootRef?: React.Ref<HTMLDivElement>
}

export function Tracks({ doc, readOnly, playhead, mini = false, rootRef }: TracksProps) {
  const st = useST()
  const sel = useSequence((s) => s.sel)
  const more = useSequence((s) => s.more)
  const cursor = useSequence((s) => s.cursor)
  const prefs = useSequence((s) => s.prefs)
  const trace = useRun((s) => s.trace)
  const ghost = useRun((s) => s.ghost)
  const scroll = React.useRef<HTMLDivElement | null>(null)
  const drag = React.useRef<Drag | null>(null)
  const [size, setSize] = React.useState({ w: 800, h: 320 })
  const mode = usePrefs((p) => p.stickMode)

  React.useLayoutEffect(() => {
    const el = scroll.current
    if (!el) return
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight })
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const fitted = fitZoom(size.w, doc.duration_s)
  const zoom = prefs.fit ? fitted : Math.max(fitted, prefs.zoom)
  const isFit = prefs.fit || zoom <= fitted + 1e-6
  const width = isFit ? size.w : Math.max(size.w, doc.duration_s * zoom + 2 * SIDE)
  const TOP = STRIP + RULER
  // a short window drops the response lane before it squeezes the four tracks
  const resp = !mini && size.h >= TOP + TRACKS.length * 40 + RESP
  const ROW = mini ? 0 : Math.max(30, Math.min(88, Math.floor((size.h - TOP - (resp ? RESP : 0) - 2) / TRACKS.length)))
  const lanes = mini ? 0 : TRACKS.length * ROW
  const height = TOP + lanes + (resp ? RESP : 0)
  const x = (t: number) => SIDE + t * zoom
  const tAt = (clientX: number) => {
    const el = scroll.current
    if (!el) return 0
    const r = el.getBoundingClientRect()
    return Math.min(doc.duration_s, Math.max(0, (clientX - r.left + el.scrollLeft - SIDE) / zoom))
  }

  // Ctrl/⌘ + wheel zooms around the pointer; a plain wheel scrolls time when zoomed in
  React.useEffect(() => {
    const el = scroll.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      const s = useSequence.getState()
      const fz = fitZoom(el.clientWidth, s.doc.duration_s)
      const cur = s.prefs.fit ? fz : Math.max(fz, s.prefs.zoom)
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault()
        const next = cur * (e.deltaY < 0 ? 1.2 : 1 / 1.2)
        const r = el.getBoundingClientRect()
        const t = (e.clientX - r.left + el.scrollLeft - SIDE) / cur
        if (next <= fz) s.setPrefs({ fit: true })
        else {
          s.setPrefs({ fit: false, zoom: next })
          requestAnimationFrame(() => (el.scrollLeft = SIDE + t * Math.min(400, next) - (e.clientX - r.left)))
        }
      } else if (!s.prefs.fit && Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
        e.preventDefault()
        el.scrollLeft += e.deltaY
      }
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  // zoomed in: page the view when the playhead (flying) or the cursor (keys) leaves it
  const follow = playhead ?? cursor
  React.useEffect(() => {
    const el = scroll.current
    if (!el || isFit || drag.current) return
    const px = x(follow)
    if (px < el.scrollLeft + 24 || px > el.scrollLeft + el.clientWidth * 0.85) el.scrollLeft = Math.max(0, px - el.clientWidth / 3)
  })

  const focusRoot = () => (scroll.current?.closest('[data-seq-tracks]') as HTMLElement | null)?.focus({ preventScroll: true })
  const isSelected = (track: TrackName, index: number) => sameSel(sel, { track, index }) || more.some((m) => m.track === track && m.index === index)

  const onDown = (e: React.PointerEvent, b: Block) => {
    e.stopPropagation()
    focusRoot()
    const store = useSequence.getState()
    const me = { track: b.track, index: b.index }
    if (e.metaKey || e.ctrlKey) return store.select(me, 'toggle')
    if (e.shiftKey && store.sel) return store.selectMany(rangeSelection(doc, store.sel, me), me)
    const inSet = isSelected(b.track, b.index) && more.length > 0
    if (!inSet) store.select(me)
    else store.select(me, 'add')
    store.setCursor(tAt(e.clientX))
    if (readOnly) return
    const el = e.currentTarget as SVGElement
    const r = el.getBoundingClientRect()
    const local = e.clientX - r.left
    const kind: Drag['kind'] = local >= r.width - EDGE ? 'end' : local <= EDGE && b.index > 0 ? 'start' : 'value'
    const now = useSequence.getState()
    const set = kind === 'value' ? [...now.more, me] : [me]
    drag.current = { kind, track: b.track, index: b.index, x0: e.clientX, y0: e.clientY, base: doc, v0: b.v, len0: b.end - b.start, start0: b.start, set }
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
  }
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d) return
    if (d.kind === 'scrub') return useSequence.getState().setCursor(tAt(e.clientX))
    if (Math.abs(e.clientX - d.x0) < 2 && Math.abs(e.clientY - d.y0) < 2) return
    const fine = e.shiftKey ? 0.2 : 1
    let next = d.base
    if (d.kind === 'value') {
      const [lo, hi] = trackRange(d.base, d.track)
      const perPx = ((hi - lo) / (ROW - 2 * PAD)) * fine
      const dv = Math.round(-(e.clientY - d.y0) * perPx * 100) / 100
      for (const s of d.set) {
        const k = d.base.tracks[s.track]?.[s.index]
        if (k) next = setValue(next, s.track, s.index, k.v + dv)
      }
    } else if (d.kind === 'end') {
      next = resize(d.base, d.track, d.index, Math.round((d.len0 + ((e.clientX - d.x0) / zoom) * fine) * 20) / 20, useSequence.getState().prefs.ripple)
    } else {
      next = moveStart(d.base, d.track, d.index, Math.round((d.start0 + ((e.clientX - d.x0) / zoom) * fine) * 20) / 20)
    }
    useSequence.getState().edit(next, { transient: true })
  }
  const onUp = () => {
    const d = drag.current
    drag.current = null
    if (!d || d.kind === 'scrub') return
    const now = useSequence.getState().doc
    if (now !== d.base) commitDoc(now)
  }

  const stepS = STEPS.find((s) => s * zoom >= 56) ?? 60
  const ticks: number[] = []
  for (let i = 0; i * stepS <= doc.duration_s + 1e-9; i++) ticks.push(Math.round(i * stepS * 100) / 100)
  const minor = stepS >= 1 && (stepS / 2) * zoom >= 14 ? stepS / 2 : null
  const at = sticksAt(doc, (playhead ?? cursor) + 0.0005)
  const peak = (pts: TracePoint[]) => Math.max(0, ...pts.map((p) => p.alt))
  const top = Math.max(1, peak(trace), peak(ghost)) * 1.1
  const respY0 = TOP + lanes
  const line = (pts: TracePoint[]) => pts.map((p) => `${x(p.t).toFixed(1)},${(respY0 + RESP - 4 - (Math.max(0, p.alt) / top) * (RESP - 10)).toFixed(1)}`).join(' ')

  return (
    <div className={cn('seq-tracks', readOnly && 'is-stale')} data-seq-tracks="" data-seq-fit={isFit ? '' : undefined} tabIndex={-1} ref={rootRef}>
      <div className="seq-labels" style={{ width: LABEL_W }}>
        <div className="seq-label" style={{ height: STRIP, borderTop: 0 }} title={st('seq.strip.hint')}>
          <span className="text-[11px] font-[560] text-[var(--g100)]">{st('seq.strip')}</span>
        </div>
        <div className="num flex items-center px-[10px] text-[10px] text-[var(--g400)]" style={{ height: RULER }} data-seq-cursor-time="">
          {fmtS(playhead ?? cursor)}
        </div>
        {mini
          ? null
          : TRACKS.map((name) => (
              <div key={name} className="seq-label" style={{ height: ROW }} title={st(`seq.hint.${name}` as SeqKey)}>
                <span className="flex items-baseline justify-between text-[11px] font-[560] text-[var(--g100)]">
                  {st(`seq.track.${name}` as SeqKey)}
                  <span className="num text-[11px] font-normal text-[var(--g200)]" data-seq-at={name}>{fmtStick(name, at[name])}</span>
                </span>
                {ROW >= 44 ? (
                  <span className="num text-[9px] text-[var(--g500)]" data-seq-scale={name}>
                    {name === 'throttle' ? '0 – 1' : `±${trackRange(doc, name)[1]}`}
                  </span>
                ) : null}
              </div>
            ))}
        {!resp ? null : (
          <div className="seq-label" style={{ height: RESP }} data-seq-response-label="">
            <span className="text-[11px] font-[560] text-[var(--g100)]">{st('seq.response')}</span>
            {trace.length || ghost.length ? (
              <span className="num text-[9px] leading-[11px] text-[var(--g400)]">
                {trace.length ? st('seq.response.peak', { now: peak(trace).toFixed(2) }) : ''}
                {trace.length && ghost.length ? ' · ' : ''}
                {ghost.length ? st('seq.response.prev', { prev: peak(ghost).toFixed(2) }) : ''}
              </span>
            ) : null}
          </div>
        )}
      </div>
      <div className="seq-scroll" ref={scroll} style={{ overflowX: isFit ? 'hidden' : 'auto' }}>
        <svg
          width={width}
          height={height}
          className="block select-none"
          onPointerMove={onMove}
          onPointerUp={onUp}
          onPointerCancel={onUp}
          onPointerDown={(e) => {
            focusRoot()
            const s = useSequence.getState()
            s.setCursor(tAt(e.clientX))
            const r = (e.currentTarget as SVGElement).getBoundingClientRect()
            if (e.clientY - r.top < TOP) {
              // the ruler and the stick strip scrub the cursor; the selection stays
              drag.current = { kind: 'scrub', track: 'throttle', index: 0, x0: e.clientX, y0: e.clientY, base: doc, v0: 0, len0: 0, start0: 0, set: [] }
              ;(e.currentTarget as Element).setPointerCapture?.(e.pointerId)
            } else s.select(null)
          }}
          data-seq-svg=""
          data-seq-zoom={zoom.toFixed(2)}
        >
          <StickStrip doc={doc} x={x} mode={mode} cursor={cursor} />
          <rect x={0} y={STRIP} width={width} height={RULER} className="seq-ruler" />
          {minor ? ticks.map((t) => (t + minor <= doc.duration_s ? <line key={`m${t}`} x1={x(t + minor)} x2={x(t + minor)} y1={TOP - 3} y2={TOP + lanes} className="seq-grid-minor" /> : null)) : null}
          {ticks.map((t) => (
            <g key={t}>
              <line x1={x(t)} x2={x(t)} y1={TOP - 6} y2={height} className="seq-grid" />
              <text x={x(t) + 3} y={STRIP + 13} className="seq-tick">
                {t}
              </text>
            </g>
          ))}
          {mini
            ? null
            : TRACKS.map((name, row) => {
                const y0 = TOP + row * ROW
                const [lo, hi] = trackRange(doc, name)
                const yOf = (val: number) => y0 + PAD + (1 - Math.min(1, Math.max(0, (val - lo) / (hi - lo)))) * (ROW - 2 * PAD)
                const base = yOf(0)
                return (
                  <g key={name} data-seq-track={name}>
                    <rect x={0} y={y0} width={width} height={ROW} className={row % 2 ? 'seq-row-alt' : 'seq-row'} />
                    {name !== 'throttle' ? <line x1={0} x2={width} y1={base} y2={base} className="seq-zero" /> : null}
                    {name === 'throttle' && !isManualMode(doc.mode) ? <line x1={0} x2={width} y1={yOf(0.5)} y2={yOf(0.5)} className="seq-zero" /> : null}
                    {blocks(doc, name).map((b) => {
                      const bx = x(b.start)
                      const bw = Math.max(2, x(b.end) - bx)
                      const selected = isSelected(name, b.index)
                      const primary = sameSel(sel, { track: name, index: b.index })
                      const flown = playhead != null && b.end <= playhead
                      const ya = yOf(b.v)
                      const yb = yOf(b.to)
                      return (
                        <g
                          key={b.index}
                          className={cn('seq-block', selected && 'is-selected', primary && 'is-primary', flown && 'is-flown')}
                          onPointerDown={(e) => onDown(e, b)}
                          onDoubleClick={(e) => {
                            e.stopPropagation()
                            if (readOnly) return
                            const r = splitAt(doc, name, tAt(e.clientX))
                            commitDoc(r.doc, { track: name, index: r.index })
                          }}
                          data-seq-block={`${name}:${b.index}`}
                          data-value={b.v}
                          data-selected={selected ? '' : undefined}
                        >
                          <rect x={bx} y={y0 + 1} width={bw} height={ROW - 2} className="seq-block-hit" />
                          <path d={`M${bx},${base} L${bx},${ya} L${bx + bw},${yb} L${bx + bw},${base} Z`} className="seq-block-fill" />
                          <line x1={bx} x2={bx + bw} y1={ya} y2={yb} className="seq-block-line" />
                          <line x1={bx} x2={bx} y1={y0 + 3} y2={y0 + ROW - 3} className="seq-block-edge" />
                          {primary ? <rect x={bx + 0.5} y={y0 + 1.5} width={Math.max(1, bw - 1)} height={ROW - 3} className="seq-block-outline" /> : null}
                          {bw > 20 ? (
                            <text x={bx + 4} y={y0 + 12} className="seq-block-text">
                              {fit(`${b.label ? `${b.label} · ` : ''}${fmtStick(name, b.v)}${b.ease === 'ramp' ? ` → ${fmtStick(name, b.to)}` : ''}`, bw)}
                            </text>
                          ) : null}
                          {bw > 44 ? (
                            <text x={bx + bw - 4} y={y0 + ROW - 5} textAnchor="end" className="seq-block-dur">
                              {fit(fmtS(b.end - b.start), bw)}
                            </text>
                          ) : null}
                          <rect x={bx + bw - EDGE} y={y0 + 1} width={EDGE} height={ROW - 2} className="seq-block-grip" />
                        </g>
                      )
                    })}
                  </g>
                )
              })}
          {!resp ? null : (
            <g data-seq-response="">
              <rect x={0} y={respY0} width={width} height={RESP} className="seq-resp" />
              <line x1={0} x2={width} y1={respY0 + RESP - 4} y2={respY0 + RESP - 4} className="seq-zero" />
              {ghost.length > 1 ? <polyline points={line(ghost)} className="seq-resp-ghost" /> : null}
              {trace.length > 1 ? <polyline points={line(trace)} className="seq-resp-line" data-seq-trace={trace.length} /> : null}
            </g>
          )}
          <line x1={x(cursor)} x2={x(cursor)} y1={0} y2={height} className="seq-cursor" data-seq-cursor={cursor.toFixed(2)} />
          {playhead != null ? <line x1={x(playhead)} x2={x(playhead)} y1={0} y2={height} className="seq-playhead" data-seq-playhead={playhead.toFixed(2)} /> : null}
        </svg>
      </div>
    </div>
  )
}

/** The moments whose pairs fit side by side (a later one closer than `gap` pixels is left out). */
function spaced(times: number[], x: (t: number) => number, gap: number): number[] {
  const out: number[] = []
  for (const t of times) if (!out.length || x(t) - x(out[out.length - 1]) >= gap) out.push(t)
  return out
}

/** Stick pairs above the ruler at every moment a stick changes; a click puts the cursor there. */
function StickStrip({ doc, x, mode, cursor }: { doc: ArsDoc; x: (t: number) => number; mode: 1 | 2; cursor: number }) {
  const ax = axesFor(mode)
  const pairW = MINI * 2 + 4
  const shown = spaced(keyTimes(doc), x, pairW + 4)
  const dot = (track: TrackName, v: number) => (toPad(track, v) * (MINI / 2 - 2))
  return (
    <g data-seq-strip="">
      {shown.map((t) => {
        const s = sticksAt(doc, t + 0.0005)
        const px = x(t) + 2
        const y = 6
        const at = Math.abs(cursor - t) < 0.01
        return (
          <g
            key={t}
            className={cn('seq-pair', at && 'is-at')}
            onPointerDown={(e) => {
              e.stopPropagation()
              useSequence.getState().setCursor(t)
            }}
            data-seq-pair={t}
          >
            <line x1={x(t)} x2={x(t)} y1={y + MINI + 2} y2={STRIP} className="seq-pair-tick" />
            {[0, 1].map((i) => {
              const cx = px + i * (MINI + 4) + MINI / 2
              const cy = y + MINI / 2
              const [tx, ty] = i === 0 ? [ax.lx, ax.ly] : [ax.rx, ax.ry]
              return (
                <g key={i}>
                  <rect x={cx - MINI / 2} y={y} width={MINI} height={MINI} rx={2} className="seq-pair-box" />
                  <circle cx={cx + dot(tx, s[tx])} cy={cy - dot(ty, s[ty])} r={1.8} className="seq-pair-dot" />
                </g>
              )
            })}
          </g>
        )
      })}
    </g>
  )
}

