// Shared pieces of the Timeline workspace (0.6.1): number and label fields, the two stick pads at the cursor, the
// trim sliders. Moved here from the 0.5.3 dock.
import * as React from 'react'
import { cn } from '@/lib/utils'
import { Button } from '@/ui/components/ui/button'
import { Input } from '@/ui/components/ui/input'
import { Slider } from '@/ui/components/ui/slider'
import { usePrefs } from '@/settings/prefs'
import { blockAt, blocks, setSticksAt, setTrim, sticksAt, TRACKS, TRIM_LIMITS, zeroTrim, type ArsDoc, type TrackName } from './model'
import { useSequence, type Selection } from './store'
import { useST, type SeqKey } from './strings'

export const fmtS = (x: number) => `${x.toFixed(x < 10 ? 2 : 1)} s`
export const fmtV = (x: number) => (x >= 0 ? `+${x.toFixed(2)}` : `−${Math.abs(x).toFixed(2)}`)
export const fmtStick = (track: TrackName, x: number) => (track === 'throttle' ? x.toFixed(2) : fmtV(x))

/** A number box that commits on Enter or blur (not on every keystroke, so a half-typed value never flies). */
export function NumberInput({ value, step, min, max, onCommit, className, testId }: {
  value: number
  step: number
  min: number
  max: number
  onCommit: (n: number) => void
  className?: string
  testId?: string
}) {
  const [text, setText] = React.useState(String(value))
  const [shown, setShown] = React.useState(value)
  if (value !== shown) {
    setShown(value)
    setText(String(value))
  }
  const done = () => {
    const n = Number(text)
    if (text !== '' && Number.isFinite(n)) onCommit(Math.min(max, Math.max(min, n)))
    else setText(String(value))
  }
  return (
    <Input
      type="number"
      inputMode="decimal"
      step={step}
      min={min}
      max={max}
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={done}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault()
          done()
        }
      }}
      className={cn('num h-6 px-1.5 font-mono text-[11px]', className)}
      data-testid={testId}
    />
  )
}

export function LabelInput({ value, onCommit }: { value: string; onCommit: (s: string) => void }) {
  const [text, setText] = React.useState(value)
  const [shown, setShown] = React.useState(value)
  if (value !== shown) {
    setShown(value)
    setText(value)
  }
  return (
    <Input
      value={text}
      maxLength={40}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => text !== value && onCommit(text)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault()
          onCommit(text)
        }
      }}
      className="h-6 px-1.5 text-[11px]"
      data-seq-label=""
    />
  )
}

export function TrimPanel({ doc, onChange }: { doc: ArsDoc; onChange: (doc: ArsDoc, done: boolean) => void }) {
  const st = useST()
  const clear = TRACKS.every((n) => doc.trim[n] === 0)
  return (
    <div className="seq-panel" data-seq-trim="" title={st('seq.trim.hint')}>
      <div className="flex items-center gap-2">
        <span className="cap text-[10px] text-[var(--g400)]">{st('seq.trim')}</span>
        <span className="flex-1" />
        <Button variant="ghost" size="xs" disabled={clear} onClick={() => onChange({ ...doc, trim: zeroTrim() }, true)} data-seq-trim-reset="">
          {st('seq.trim.reset')}
        </Button>
      </div>
      {(['pitch', 'roll', 'yaw', 'throttle'] as const).map((n) => (
        <div key={n} className="grid grid-cols-[64px_1fr_48px] items-center gap-2 text-hud-sub" data-seq-trim-axis={n}>
          <span className="text-[var(--g400)]">{st(`seq.track.${n}` as SeqKey)}</span>
          <Slider
            value={[doc.trim[n]]}
            min={-TRIM_LIMITS[n]}
            max={TRIM_LIMITS[n]}
            step={0.005}
            onValueChange={(x) => onChange(setTrim(doc, n, Array.isArray(x) ? x[0] : (x as number)), false)}
            onValueCommitted={() => onChange(useSequence.getState().doc, true)}
            aria-label={`${st('seq.trim')} ${st(`seq.track.${n}` as SeqKey)}`}
          />
          <span className="num text-right text-[var(--g100)]">{fmtV(doc.trim[n])}</span>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------- the two sticks

export type Axes = { lx: TrackName; ly: TrackName; rx: TrackName; ry: TrackName }
/** Which track each stick axis moves: mode 2 has the throttle on the left stick, mode 1 on the right. */
export const axesFor = (mode: 1 | 2): Axes => (mode === 1 ? { lx: 'yaw', ly: 'pitch', rx: 'roll', ry: 'throttle' } : { lx: 'yaw', ly: 'throttle', rx: 'roll', ry: 'pitch' })
/** A stick value as -1..1 on its pad axis (throttle 0..1 runs bottom to top). */
export const toPad = (track: TrackName, v: number) => (track === 'throttle' ? v * 2 - 1 : v)
const fromPad = (track: TrackName, p: number) => (track === 'throttle' ? (p + 1) / 2 : p)

/** Both sticks at the cursor (or the playhead while flying): drag to set every axis from that moment on. */
export function StickPads({ doc, at, commit }: { doc: ArsDoc; at: number; commit: (doc: ArsDoc, sel?: Selection | null) => void }) {
  const st = useST()
  const mode = usePrefs((p) => p.stickMode)
  const ax = axesFor(mode)
  const s = sticksAt(doc, at + 0.0005)
  return (
    <div className="seq-panel" data-seq-pads="" title={st('seq.pads.hint')}>
      <div className="flex items-center gap-2">
        <span className="cap text-[10px] text-[var(--g400)]">{st('seq.pads', { t: fmtS(at) })}</span>
        <span className="flex-1" />
        <span className="text-[10px] text-[var(--g500)]">{st(mode === 1 ? 'seq.pads.mode1' : 'seq.pads.mode2')}</span>
      </div>
      <div className="flex justify-center gap-4">
        <Pad side="left" x={ax.lx} y={ax.ly} values={s} doc={doc} t={at} commit={commit} />
        <Pad side="right" x={ax.rx} y={ax.ry} values={s} doc={doc} t={at} commit={commit} />
      </div>
    </div>
  )
}

const PAD_PX = 88

function Pad({ side, x, y, values, doc, t, commit }: {
  side: 'left' | 'right'
  x: TrackName
  y: TrackName
  values: Record<TrackName, number>
  doc: ArsDoc
  t: number
  commit: (doc: ArsDoc, sel?: Selection | null) => void
}) {
  const st = useST()
  const drag = React.useRef<{ x0: number; y0: number; vx: number; vy: number; base: ArsDoc } | null>(null)
  const R = PAD_PX / 2 - 6
  const dx = toPad(x, values[x]) * R
  const dy = -toPad(y, values[y]) * R
  const move = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d) return
    const k = (e.shiftKey ? 0.2 : 1) / R
    const px = Math.max(-1, Math.min(1, d.vx + (e.clientX - d.x0) * k))
    const py = Math.max(-1, Math.min(1, d.vy - (e.clientY - d.y0) * k))
    // only the axes the drag moved get a block here
    const patch: Partial<Record<TrackName, number>> = {}
    if (Math.abs(px - d.vx) > 0.004) patch[x] = Math.round(fromPad(x, px) * 100) / 100
    if (Math.abs(py - d.vy) > 0.004) patch[y] = Math.round(fromPad(y, py) * 100) / 100
    const next = Object.keys(patch).length ? setSticksAt(d.base, t, patch) : d.base
    useSequence.getState().edit(next, { transient: true })
  }
  const up = () => {
    const d = drag.current
    drag.current = null
    if (!d) return
    const { doc: now, sel } = useSequence.getState()
    if (now === d.base) return
    // a split under the cursor shifts block indices: keep the same block selected by its start time
    const start = sel ? blocks(d.base, sel.track)[sel.index]?.start : undefined
    commit(now, sel && start !== undefined ? { track: sel.track, index: blockAt(now, sel.track, start + 0.0005) } : sel)
  }
  return (
    <div className="flex flex-col items-center gap-1" data-seq-pad={side}>
      <div
        className="seq-pad"
        style={{ width: PAD_PX, height: PAD_PX }}
        onPointerDown={(e) => {
          drag.current = { x0: e.clientX, y0: e.clientY, vx: toPad(x, values[x]), vy: toPad(y, values[y]), base: doc }
          ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
        }}
        onPointerMove={move}
        onPointerUp={up}
        onPointerCancel={up}
      >
        <span className="seq-pad-cross" />
        <span className="seq-pad-dot" style={{ transform: `translate(${dx.toFixed(1)}px, ${dy.toFixed(1)}px)` }} />
      </div>
      <span className="num text-[10px] text-[var(--g400)]">
        {st(`seq.track.${y}` as SeqKey)} {fmtStick(y, values[y])} · {st(`seq.track.${x}` as SeqKey)} {fmtStick(x, values[x])}
      </span>
    </div>
  )
}
