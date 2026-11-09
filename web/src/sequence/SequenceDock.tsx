// Timeline dock (10 §2): an .ars stick sequence as four tracks of blocks under the stage. Blocks are dragged (up and
// down for the stick, the right edge for how long, the left edge for when it starts) or nudged with the keys; the
// trim is added to every frame. While the sequence flies the playhead runs and every committed edit goes to the
// aircraft at once (PATCH /sequence): the blocks not yet flown change, the time line keeps its place.
import * as React from 'react'
import { api, RelayApiError, type Vehicle } from '@/api'
import { useLang } from '@/i18n'
import { cn } from '@/lib/utils'
import { registerDismisser } from '@/shell/keyboard'
import { notify, notifyError } from '@/shell/notify'
import { useSelectedVehicle } from '@/store/selectors'
import { Button } from '@/ui/components/ui/button'
import { Input } from '@/ui/components/ui/input'
import { NativeSelect, NativeSelectOption } from '@/ui/components/ui/native-select'
import { Slider } from '@/ui/components/ui/slider'
import { Icon } from '@/ui/icons'
import { TypeToConfirm } from '@/ui/relay'
import {
  ARS_SUFFIX,
  ArsError,
  blockAt,
  blocks,
  fileName,
  insertAfter,
  moveStart,
  parseArs,
  range,
  removeBlock,
  sampleTrack,
  serialiseArs,
  setDocDuration,
  setDuration,
  setEase,
  setLabel,
  setTrim,
  keyTimes,
  setSticksAt,
  setValue,
  splitAt,
  sticksAt,
  templateHover,
  TRACKS,
  TRIM_LIMITS,
  zeroTrim,
  isManualMode,
  SEQ_MODES,
  armWaitThrottle,
  type ArsDoc,
  type Block,
  type SeqEnd,
  type SeqMode,
  type TrackName,
} from './model'
import { useSequence, type Selection } from './store'
import { useST, type SeqKey } from './strings'
import { usePrefs } from '@/settings/prefs'
import './sequence.css'

const ROW = 46
const RULER = 22
const STRIP = 34 // stick pairs above the ruler
const MINI = 13
const LABEL_W = 112
const PAD = 6
const EDGE = 6

const fmtS = (x: number) => `${x.toFixed(x < 10 ? 2 : 1)} s`
const fmtV = (x: number) => (x >= 0 ? `+${x.toFixed(2)}` : `−${Math.abs(x).toFixed(2)}`)
const fmtStick = (track: TrackName, x: number) => (track === 'throttle' ? x.toFixed(2) : fmtV(x))
const SCALES = [0.1, 0.25, 0.5, 1]
/** Vertical scale of a track: throttle 0..1; a direction track zooms to the smallest of ±0.1/0.25/0.5/1 that fits. */
function trackRange(doc: ArsDoc, track: TrackName): [number, number] {
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

type Flight = { flying: boolean; progress: number | null; phase: string | null; state: string | null; detail: string | null }

function useFlight(v: Vehicle | null): Flight {
  const skill = v?.skill && v.skill.name === 'sequence' ? v.skill : null
  if (!v || !skill) return { flying: false, progress: null, phase: null, state: null, detail: null }
  const holding = skill.state === 'done' && v.armed
  const flying = skill.state === 'running' || holding
  return {
    flying,
    progress: flying ? (skill.progress_s ?? null) : null,
    phase: skill.phase ?? null,
    state: skill.state,
    detail: skill.detail,
  }
}

function download(doc: ArsDoc) {
  const blob = new Blob([serialiseArs(doc)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = fileName(doc)
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}

export function SequenceDock() {
  const st = useST()
  const lang = useLang()
  const v = useSelectedVehicle()
  const { doc, sel, zoom, past, future } = useSequence()
  const store = useSequence.getState
  const flight = useFlight(v)
  const human = v?.controller === 'human'
  const [cursor, setCursor] = React.useState(0)
  const [busy, setBusy] = React.useState<string | null>(null)
  const [confirming, setConfirming] = React.useState<null | { start: number }>(null)
  const [confirmError, setConfirmError] = React.useState<string | null>(null)
  const [recordingSince, setRecordingSince] = React.useState<number | null>(null)
  const fileRef = React.useRef<HTMLInputElement | null>(null)
  const assistOff = v?.kind === 'sim' && v.sim && !v.sim.hover_assist

  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key)
    try {
      await fn()
      return true
    } catch (e) {
      notifyError(e)
      return false
    } finally {
      setBusy(null)
    }
  }

  // Esc first clears the block selection (a layer, 02 §3); with nothing selected it stops the aircraft
  React.useEffect(
    () =>
      registerDismisser(() => {
        if (!useSequence.getState().sel) return false
        useSequence.getState().select(null)
        return true
      }),
    [],
  )

  // a committed edit (undo and redo too): kept for undo, and sent to the aircraft when the sequence is flying there
  const push = React.useRef<number | null>(null)
  const pushDoc = () => {
    if (!(flight.flying && v && human)) return
    if (push.current) window.clearTimeout(push.current)
    push.current = window.setTimeout(() => {
      push.current = null
      void api.editSequence(v.id, { sequence: useSequence.getState().doc }).catch(notifyError)
    }, 80)
  }
  const commit = (next: ArsDoc, nextSel?: Selection | null) => {
    store().edit(next, nextSel === undefined ? undefined : { sel: nextSel })
    pushDoc()
  }
  const undo = () => {
    store().undo()
    pushDoc()
  }
  const redo = () => {
    store().redo()
    pushDoc()
  }
  const pushTrim = (trim: Record<TrackName, number>) => {
    if (flight.flying && v && human) void api.editSequence(v.id, { trim }).catch(notifyError)
  }

  const start = async (startAt: number, confirm?: string) => {
    if (!v) return
    await api.runSequence(v.id, doc, { ...(confirm ? { confirm } : {}), ...(startAt > 0 ? { start_at: startAt } : {}) })
  }
  const onRun = (startAt: number) => {
    if (!v) return
    if (v.kind === 'real' && !v.armed) {
      setConfirmError(null)
      setConfirming({ start: startAt })
      return
    }
    void run('run', () => start(startAt))
  }

  const onImport = async (file: File) => {
    try {
      const next = parseArs(await file.text())
      store().load(next)
      notify(st('seq.imported', { name: next.name || file.name }))
    } catch (e) {
      notify(st('seq.importFailed', { reason: e instanceof ArsError ? `${e.field}: ${e.reason}` : String(e) }), { level: 'warn' })
    }
  }

  const toggleRecord = async () => {
    if (!v) return
    if (recordingSince === null) {
      if (await run('record', () => api.recordSequence(v.id, 'start', doc.name))) setRecordingSince(Date.now())
      return
    }
    await run('record', async () => {
      try {
        const out = (await api.recordSequence(v.id, 'stop')) as { sequence?: unknown }
        const next = parseArs(JSON.stringify(out.sequence))
        store().load(next)
        notify(st('seq.recorded', { t: fmtS(next.duration_s) }))
      } finally {
        setRecordingSince(null)
      }
    })
  }

  const selBlock = sel ? blocks(doc, sel.track)[sel.index] ?? null : null

  // keyboard: nudges on the selected block, split, remove, undo / redo
  const onKeyDown = (e: React.KeyboardEvent) => {
    const tag = (e.target as HTMLElement).tagName
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return
    const mod = e.metaKey || e.ctrlKey
    if (mod && e.key.toLowerCase() === 'z') {
      e.preventDefault()
      if (e.shiftKey) redo()
      else undo()
      return
    }
    if (!sel || !selBlock) return
    const big = e.shiftKey
    let next: ArsDoc | null = null
    let nextSel: Selection | null | undefined
    if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
      const d = (big ? 0.05 : 0.01) * (e.key === 'ArrowUp' ? 1 : -1)
      next = setValue(doc, sel.track, sel.index, selBlock.v + d)
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      const d = (big ? 0.5 : 0.1) * (e.key === 'ArrowRight' ? 1 : -1)
      next = setDuration(doc, sel.track, sel.index, selBlock.end - selBlock.start + d)
    } else if (!mod && !e.altKey && (e.key === 's' || e.key === 'S')) {
      const r = splitAt(doc, sel.track, cursor)
      next = r.doc
      nextSel = { track: sel.track, index: r.index }
    } else if (e.key === 'Delete' || e.key === 'Backspace') {
      next = removeBlock(doc, sel.track, sel.index)
      nextSel = null
    }
    if (next) {
      e.preventDefault()
      commit(next, nextSel)
    }
  }

  const total = doc.duration_s
  const now = flight.progress
  let status: string | null = null
  if (flight.flying) {
    if (flight.phase === 'mode') status = st('seq.state.mode')
    // 11 §9: the throttle held while arming (ArduPilot 0; PX4 mid stick in the hold modes)
    else if (flight.phase === 'arm') status = st('seq.state.armThrottle', { pct: Math.round(armWaitThrottle(doc.mode, v?.autopilot) * 100) })
    else if (flight.phase === 'end') status = st('seq.state.end')
    else if (flight.state === 'done') status = st('seq.state.hold')
    else status = st('seq.state.play', { t: fmtS(now ?? 0), total: fmtS(total) })
  } else if (flight.state === 'done') status = st('seq.state.done', { t: fmtS(total) })
  else if (flight.state === 'failed' && flight.detail) status = st('seq.state.failed', { detail: flight.detail })

  return (
    <section className="seq-dock l1" tabIndex={-1} data-sequence-dock="" data-own-keys="" onKeyDown={onKeyDown} aria-label={st('seq.title')}>
      {/* header: document */}
      <div className="seq-head">
        <span className="seq-title">
          <Icon icon="seq.timeline" className="size-3.5" />
          {st('seq.title')}
        </span>
        <Input
          value={doc.name}
          onChange={(e) => store().edit({ ...doc, name: e.target.value.slice(0, 80) }, { transient: true })}
          onBlur={() => store().edit(useSequence.getState().doc)}
          placeholder={st('seq.name')}
          aria-label={st('seq.name')}
          className="h-6 w-44 px-1.5 text-[11px]"
          data-seq-name=""
        />
        <NativeSelect
          size="sm"
          value={doc.mode}
          disabled={flight.flying}
          onChange={(e) => commit({ ...doc, mode: e.target.value as SeqMode })}
          className="h-6 w-[104px] text-[11px]"
          aria-label={st('seq.mode')}
          data-seq-mode=""
        >
          {SEQ_MODES.map((m) => (
            <NativeSelectOption key={m} value={m}>
              {st(`seq.mode.${m}` as SeqKey)}
            </NativeSelectOption>
          ))}
        </NativeSelect>
        <NativeSelect
          size="sm"
          value={doc.end}
          onChange={(e) => commit({ ...doc, end: e.target.value as SeqEnd })}
          className="h-6 w-[156px] text-[11px]"
          aria-label={st('seq.end')}
          data-seq-end=""
        >
          {(['disarm', 'hold'] as const).map((m) => (
            <NativeSelectOption key={m} value={m}>
              {st(`seq.end.${m}` as SeqKey)}
            </NativeSelectOption>
          ))}
        </NativeSelect>
        <LengthField doc={doc} onCommit={(n) => commit(setDocDuration(doc, n))} />
        <span className="flex-1" />
        <Button variant="ghost" size="icon-sm" disabled={!past.length} onClick={undo} title={st('seq.undo')} aria-label={st('seq.undo')}>
          <Icon icon="seq.undo" />
        </Button>
        <Button variant="ghost" size="icon-sm" disabled={!future.length} onClick={redo} title={st('seq.redo')} aria-label={st('seq.redo')}>
          <Icon icon="seq.redo" />
        </Button>
        <Button variant="ghost" size="icon-sm" onClick={() => store().setZoom(zoom / 1.4)} title={st('seq.zoomOut')} aria-label={st('seq.zoomOut')}>
          <Icon icon="seq.zoomOut" />
        </Button>
        <Button variant="ghost" size="icon-sm" onClick={() => store().setZoom(zoom * 1.4)} title={st('seq.zoomIn')} aria-label={st('seq.zoomIn')}>
          <Icon icon="seq.zoomIn" />
        </Button>
        <Button variant="ghost" size="sm" disabled={flight.flying} onClick={() => store().load(templateHover())} data-seq-template="">
          <Icon icon="reset" />
          {st('seq.template')}
        </Button>
        <Button variant="ghost" size="sm" disabled={flight.flying} onClick={() => fileRef.current?.click()} data-seq-import="">
          <Icon icon="seq.import" />
          {st('seq.import')}
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept={`${ARS_SUFFIX},application/json`}
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0]
            e.target.value = ''
            if (f) void onImport(f)
          }}
        />
        <Button variant="ghost" size="sm" onClick={() => download(doc)} data-seq-export="">
          <Icon icon="seq.export" />
          {st('seq.export')}
        </Button>
        <Button variant="ghost" size="icon-sm" onClick={() => store().setOpen(false)} title={st('seq.close')} aria-label={st('seq.close')}>
          <Icon icon="close" />
        </Button>
      </div>

      <div className="seq-body">
        <Tracks doc={doc} sel={sel} zoom={zoom} cursor={cursor} playhead={now} onCursor={setCursor} commit={commit} />
        <aside className="seq-side">
          <StickPads doc={doc} at={now ?? cursor} commit={commit} />
          {/* trim next: it is what changes most while the sequence flies */}
          <TrimPanel
            doc={doc}
            onChange={(next, done) => {
              if (!done) store().edit(next, { transient: true })
              else {
                store().edit(next)
                pushTrim(next.trim)
              }
            }}
          />
          <BlockPanel doc={doc} sel={sel} block={selBlock} cursor={cursor} commit={commit} />
        </aside>
      </div>

      {/* run bar: the selected aircraft */}
      <div className="seq-run">
        {!v ? (
          <span className="text-hud-sub text-[var(--g500)]">{st('seq.noAircraft')}</span>
        ) : (
          <>
            <span className="text-hud-sub text-[var(--g300)]">{v.label}</span>
            {flight.flying ? (
              <Button variant="outline" size="sm" disabled={!human || busy !== null} onClick={() => void run('stop', () => api.stopSequence(v.id))} data-seq-stop="">
                <Icon icon="stop" />
                {st('seq.stop')}
              </Button>
            ) : (
              <>
                <Button variant="outline" size="sm" disabled={!human || busy !== null || Boolean(assistOff && !isManualMode(doc.mode))} onClick={() => onRun(0)} data-seq-run="">
                  <Icon icon="tl.play" />
                  {st('seq.run')}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!human || busy !== null || cursor <= 0 || cursor >= total || Boolean(assistOff && !isManualMode(doc.mode))}
                  onClick={() => onRun(cursor)}
                  data-seq-run-cursor=""
                >
                  {st('seq.runFromCursor')}
                </Button>
              </>
            )}
            <Button
              variant={recordingSince !== null ? 'destructive' : 'ghost'}
              size="sm"
              disabled={!human || (busy !== null && busy !== 'record')}
              onClick={() => void toggleRecord()}
              title={st('seq.recordHint')}
              data-seq-record={recordingSince !== null ? 'on' : 'off'}
            >
              <Icon icon={recordingSince !== null ? 'square' : 'seq.record'} />
              {recordingSince !== null ? st('seq.recordStop') : st('seq.record')}
            </Button>
            {recordingSince !== null ? <RecordClock since={recordingSince} /> : null}
            {status ? (
              <span className="num text-hud-sub text-[var(--g300)]" data-seq-status={flight.flying ? (flight.phase ?? 'play') : (flight.state ?? '')}>
                {status}
              </span>
            ) : null}
            {flight.flying ? <span className="text-hud-sub text-[var(--g500)]">{st('seq.live')}</span> : null}
            {!human ? <span className="text-hud-sub text-[var(--g500)]">{st('seq.possessFirst')}</span> : null}
            {assistOff && !isManualMode(doc.mode) ? <span className="text-hud-sub text-[var(--g400)]">{st('seq.assistOff')}</span> : null}
            <span className="flex-1" />
            <LiveSticks v={v} doc={doc} at={now ?? cursor} />
          </>
        )}
      </div>
      {confirming && v && human && !v.armed ? (
        <div className="px-3 pb-2">
          <TypeToConfirm
            word="ARM"
            prompt={st('seq.confirm', { aircraft: v.label })}
            actionLabel={st('seq.run')}
            onConfirm={async () => {
              try {
                await start(confirming.start, 'ARM')
                setConfirming(null)
              } catch (e) {
                setConfirmError(e instanceof RelayApiError ? e.text(lang) : String(e))
              }
            }}
            onCancel={() => setConfirming(null)}
            error={confirmError}
          />
        </div>
      ) : null}
    </section>
  )
}

function RecordClock({ since }: { since: number }) {
  const st = useST()
  const [elapsed, setElapsed] = React.useState(0)
  React.useEffect(() => {
    const id = window.setInterval(() => setElapsed((Date.now() - since) / 1000), 200)
    return () => window.clearInterval(id)
  }, [since])
  return <span className="num text-hud-sub text-[var(--r400)]">{st('seq.recording', { t: fmtS(elapsed) })}</span>
}

function LengthField({ doc, onCommit }: { doc: ArsDoc; onCommit: (n: number) => void }) {
  const st = useST()
  return (
    <label className="flex items-center gap-1 text-hud-sub text-[var(--g400)]">
      {st('seq.length')}
      <NumberInput value={doc.duration_s} step={0.5} min={0.1} max={600} onCommit={onCommit} className="w-16" testId="seq-length" />
      <span>{st('seq.unit.s')}</span>
    </label>
  )
}

/** A number box that commits on Enter or blur (not on every keystroke, so a half-typed value never flies). */
function NumberInput({ value, step, min, max, onCommit, className, testId }: {
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

// ---------------------------------------------------------------- tracks

type Drag = { kind: 'value' | 'end' | 'start'; track: TrackName; index: number; x0: number; y0: number; base: ArsDoc; v0: number; len0: number; start0: number }

function Tracks({ doc, sel, zoom, cursor, playhead, onCursor, commit }: {
  doc: ArsDoc
  sel: Selection | null
  zoom: number
  cursor: number
  playhead: number | null
  onCursor: (t: number) => void
  commit: (doc: ArsDoc, sel?: Selection | null) => void
}) {
  const st = useST()
  const scroll = React.useRef<HTMLDivElement | null>(null)
  const drag = React.useRef<Drag | null>(null)
  const width = Math.max(320, doc.duration_s * zoom + 48)
  const TOP = STRIP + RULER
  const height = TOP + TRACKS.length * ROW
  const mode = usePrefs((p) => p.stickMode)
  const x = (t: number) => t * zoom
  const tAt = (clientX: number) => {
    const el = scroll.current
    if (!el) return 0
    const r = el.getBoundingClientRect()
    return Math.min(doc.duration_s, Math.max(0, (clientX - r.left + el.scrollLeft) / zoom))
  }

  // keep the playhead in view while flying
  React.useEffect(() => {
    const el = scroll.current
    if (!el || playhead == null) return
    const px = x(playhead)
    if (px < el.scrollLeft + 24 || px > el.scrollLeft + el.clientWidth - 48) el.scrollLeft = Math.max(0, px - el.clientWidth / 3)
  })

  const onDown = (e: React.PointerEvent, b: Block) => {
    e.stopPropagation()
    const el = e.currentTarget as SVGElement
    const r = el.getBoundingClientRect()
    const local = e.clientX - r.left
    const kind: Drag['kind'] = local >= r.width - EDGE ? 'end' : local <= EDGE && b.index > 0 ? 'start' : 'value'
    drag.current = { kind, track: b.track, index: b.index, x0: e.clientX, y0: e.clientY, base: doc, v0: b.v, len0: b.end - b.start, start0: b.start }
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
    useSequence.getState().select({ track: b.track, index: b.index })
    onCursor(tAt(e.clientX))
    ;(scroll.current?.closest('[data-sequence-dock]') as HTMLElement | null)?.focus({ preventScroll: true })
  }
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d) return
    const fine = e.shiftKey ? 0.2 : 1
    let next = d.base
    if (d.kind === 'value') {
      const [lo, hi] = trackRange(d.base, d.track)
      const span = ROW - 2 * PAD
      const perPx = ((hi - lo) / span) * fine
      next = setValue(d.base, d.track, d.index, Math.round((d.v0 - (e.clientY - d.y0) * perPx) * 100) / 100)
    } else if (d.kind === 'end') {
      next = setDuration(d.base, d.track, d.index, Math.round((d.len0 + ((e.clientX - d.x0) / zoom) * fine) * 20) / 20)
    } else {
      next = moveStart(d.base, d.track, d.index, Math.round((d.start0 + ((e.clientX - d.x0) / zoom) * fine) * 20) / 20)
    }
    useSequence.getState().edit(next, { transient: true })
  }
  const onUp = () => {
    const d = drag.current
    drag.current = null
    if (!d) return
    const now = useSequence.getState().doc
    if (now !== d.base) commit(now)
  }

  const ticks: number[] = []
  const stepS = zoom >= 80 ? 0.5 : zoom >= 30 ? 1 : zoom >= 14 ? 2 : 5
  for (let t = 0; t <= doc.duration_s + 1e-9; t += stepS) ticks.push(Math.round(t * 100) / 100)

  return (
    <div className="seq-tracks">
      <div className="seq-labels" style={{ width: LABEL_W }}>
        <div className="seq-label" style={{ height: STRIP, borderTop: 0 }} title={st('seq.strip.hint')}>
          <span className="text-[11px] font-[560] text-[var(--g100)]">{st('seq.strip')}</span>
        </div>
        <div style={{ height: RULER }} />
        {TRACKS.map((name) => (
          <div key={name} className="seq-label" style={{ height: ROW }}>
            <span className="flex items-baseline justify-between text-[11px] font-[560] text-[var(--g100)]">
              {st(`seq.track.${name}` as SeqKey)}
              {name !== 'throttle' ? <span className="num text-[9px] font-normal text-[var(--g500)]" data-seq-scale={name}>±{trackRange(doc, name)[1]}</span> : null}
            </span>
            <span className="text-[10px] text-[var(--g500)]">{st(`seq.hint.${name}` as SeqKey)}</span>
          </div>
        ))}
      </div>
      <div className="seq-scroll" ref={scroll}>
        <svg
          width={width}
          height={height}
          className="block select-none"
          onPointerMove={onMove}
          onPointerUp={onUp}
          onPointerCancel={onUp}
          onPointerDown={(e) => {
            onCursor(tAt(e.clientX))
            useSequence.getState().select(null)
          }}
          data-seq-svg=""
        >
          {/* ruler */}
          <StickStrip doc={doc} x={x} mode={mode} cursor={cursor} onPick={onCursor} />
          <rect x={0} y={STRIP} width={width} height={RULER} className="seq-ruler" />
          {ticks.map((t) => (
            <g key={t}>
              <line x1={x(t)} x2={x(t)} y1={TOP - 6} y2={height} className={Number.isInteger(t) ? 'seq-grid' : 'seq-grid-minor'} />
              {Number.isInteger(t / (stepS >= 1 ? stepS : 1)) ? (
                <text x={x(t) + 3} y={STRIP + 13} className="seq-tick">
                  {t}
                </text>
              ) : null}
            </g>
          ))}
          {TRACKS.map((name, row) => {
            const y0 = TOP + row * ROW
            const bipolar = name !== 'throttle'
            const [lo, hi] = trackRange(doc, name)
            const yOf = (val: number) => {
              const f = Math.min(1, Math.max(0, (val - lo) / (hi - lo)))
              return y0 + PAD + (1 - f) * (ROW - 2 * PAD)
            }
            const base = bipolar ? yOf(0) : yOf(0)
            return (
              <g key={name} data-seq-track={name}>
                <rect x={0} y={y0} width={width} height={ROW} className={row % 2 ? 'seq-row-alt' : 'seq-row'} />
                {bipolar ? <line x1={0} x2={width} y1={base} y2={base} className="seq-zero" /> : null}
                {!bipolar && !isManualMode(doc.mode) ? <line x1={0} x2={width} y1={yOf(0.5)} y2={yOf(0.5)} className="seq-zero" /> : null}
                {blocks(doc, name).map((b) => {
                  const bx = x(b.start)
                  const bw = Math.max(2, x(b.end) - bx)
                  const selected = sel?.track === name && sel.index === b.index
                  const ya = yOf(b.v)
                  const yb = yOf(b.to)
                  const shape = `M${bx},${base} L${bx},${ya} L${bx + bw},${yb} L${bx + bw},${base} Z`
                  return (
                    <g
                      key={b.index}
                      className={cn('seq-block', selected && 'is-selected')}
                      onPointerDown={(e) => onDown(e, b)}
                      onDoubleClick={(e) => {
                        e.stopPropagation()
                        const r = splitAt(doc, name, tAt(e.clientX))
                        commit(r.doc, { track: name, index: r.index })
                      }}
                      data-seq-block={`${name}:${b.index}`}
                      data-value={b.v}
                    >
                      <rect x={bx} y={y0 + 1} width={bw} height={ROW - 2} className="seq-block-hit" />
                      <path d={shape} className="seq-block-fill" />
                      <line x1={bx} x2={bx + bw} y1={ya} y2={yb} className="seq-block-line" />
                      <line x1={bx} x2={bx} y1={y0 + 3} y2={y0 + ROW - 3} className="seq-block-edge" />
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
          {/* cursor and playhead */}
          <line x1={x(cursor)} x2={x(cursor)} y1={0} y2={height} className="seq-cursor" data-seq-cursor={cursor.toFixed(2)} />
          {playhead != null ? <line x1={x(playhead)} x2={x(playhead)} y1={0} y2={height} className="seq-playhead" data-seq-playhead={playhead.toFixed(2)} /> : null}
        </svg>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- side panels

function BlockPanel({ doc, sel, block, cursor, commit }: {
  doc: ArsDoc
  sel: Selection | null
  block: Block | null
  cursor: number
  commit: (doc: ArsDoc, sel?: Selection | null) => void
}) {
  const st = useST()
  if (!sel || !block) {
    return (
      <div className="seq-panel" data-seq-block-panel="none">
        <span className="cap text-[10px] text-[var(--g400)]">{st('seq.block')}</span>
        <p className="text-hud-sub text-[var(--g500)]">{st('seq.block.none')}</p>
        <p className="text-hud-sub text-[var(--g600)]">{st('seq.keys')}</p>
      </div>
    )
  }
  const [lo, hi] = range(sel.track)
  return (
    <div className="seq-panel" data-seq-block-panel={`${sel.track}:${sel.index}`}>
      <div className="flex items-center gap-2">
        <span className="cap text-[10px] text-[var(--g400)]">
          {st(`seq.track.${sel.track}` as SeqKey)} · {st('seq.block')} {sel.index + 1}
        </span>
        <span className="flex-1" />
        <span className="num text-[10px] text-[var(--g500)]">{st('seq.block.starts', { t: fmtS(block.start) })}</span>
      </div>
      <label className="grid grid-cols-[64px_1fr] items-center gap-2 text-hud-sub text-[var(--g400)]">
        {st('seq.block.label')}
        <LabelInput value={block.label ?? ''} onCommit={(text) => commit(setLabel(doc, sel.track, sel.index, text))} />
      </label>
      <div className="grid grid-cols-[64px_1fr_56px] items-center gap-2 text-hud-sub">
        <span className="text-[var(--g400)]">{st('seq.block.value')}</span>
        <Slider
          value={[block.v]}
          min={lo}
          max={hi}
          step={0.01}
          onValueChange={(x) => useSequence.getState().edit(setValue(doc, sel.track, sel.index, Array.isArray(x) ? x[0] : (x as number)), { transient: true })}
          onValueCommitted={() => commit(useSequence.getState().doc)}
          aria-label={st('seq.block.value')}
          data-seq-value=""
        />
        <NumberInput value={block.v} step={0.01} min={lo} max={hi} onCommit={(n) => commit(setValue(doc, sel.track, sel.index, n))} testId="seq-value" />
      </div>
      <div className="grid grid-cols-[64px_1fr] items-center gap-2 text-hud-sub">
        <span className="text-[var(--g400)]">{st('seq.block.seconds')}</span>
        <span className="flex items-center gap-1">
          <NumberInput
            value={Math.round((block.end - block.start) * 100) / 100}
            step={0.1}
            min={0.1}
            max={600}
            onCommit={(n) => commit(setDuration(doc, sel.track, sel.index, n))}
            className="w-16"
            testId="seq-seconds"
          />
          <span className="text-[var(--g400)]">{st('seq.unit.s')}</span>
          <span className="flex-1" />
          <NativeSelect
            size="sm"
            value={block.ease}
            onChange={(e) => commit(setEase(doc, sel.track, sel.index, e.target.value as 'hold' | 'ramp'))}
            className="h-6 w-[112px] text-[11px]"
            data-seq-ease=""
          >
            <NativeSelectOption value="hold">{st('seq.ease.hold')}</NativeSelectOption>
            <NativeSelectOption value="ramp">{st('seq.ease.ramp')}</NativeSelectOption>
          </NativeSelect>
        </span>
      </div>
      <div className="flex flex-wrap gap-1">
        <Button
          variant="ghost"
          size="sm"
          disabled={cursor <= block.start || cursor >= block.end}
          onClick={() => {
            const r = splitAt(doc, sel.track, cursor)
            commit(r.doc, { track: sel.track, index: r.index })
          }}
          data-seq-split=""
        >
          <Icon icon="seq.split" />
          {st('seq.split')}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            const r = insertAfter(doc, sel.track, sel.index, 1)
            commit(r.doc, { track: sel.track, index: r.index })
          }}
          data-seq-insert=""
        >
          <Icon icon="seq.insert" />
          {st('seq.insert')}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          disabled={(doc.tracks[sel.track]?.length ?? 0) <= 1}
          onClick={() => commit(removeBlock(doc, sel.track, sel.index), null)}
          data-seq-remove=""
        >
          <Icon icon="trash" />
          {st('seq.remove')}
        </Button>
      </div>
    </div>
  )
}

function LabelInput({ value, onCommit }: { value: string; onCommit: (s: string) => void }) {
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

function TrimPanel({ doc, onChange }: { doc: ArsDoc; onChange: (doc: ArsDoc, done: boolean) => void }) {
  const st = useST()
  const clear = TRACKS.every((n) => doc.trim[n] === 0)
  return (
    <div className="seq-panel" data-seq-trim="">
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
      <p className="text-hud-sub text-[var(--g500)]">{st('seq.trim.hint')}</p>
    </div>
  )
}

/** What the time line sends at the playhead (or the cursor) and, while armed, what the aircraft does. */
function LiveSticks({ v, doc, at }: { v: Vehicle; doc: ArsDoc; at: number }) {
  const st = useST()
  return (
    <span className="num flex items-center gap-3 text-hud-sub text-[var(--g400)]" data-seq-live="">
      {TRACKS.map((n) => (
        <span key={n}>
          {st(`seq.track.${n}` as SeqKey)} <span className="text-[var(--g100)]">{fmtStick(n, sampleTrack(doc, n, at))}</span>
        </span>
      ))}
      {v.armed && v.altitude_m != null ? <span className="text-[var(--g100)]">{v.altitude_m.toFixed(2)} m</span> : null}
    </span>
  )
}

// ---------------------------------------------------------------- the two sticks

type Axes = { lx: TrackName; ly: TrackName; rx: TrackName; ry: TrackName }
/** Which track each stick axis moves: mode 2 has the throttle on the left stick, mode 1 on the right. */
const axesFor = (mode: 1 | 2): Axes => (mode === 1 ? { lx: 'yaw', ly: 'pitch', rx: 'roll', ry: 'throttle' } : { lx: 'yaw', ly: 'throttle', rx: 'roll', ry: 'pitch' })
/** A stick value as -1..1 on its pad axis (throttle 0..1 runs bottom to top). */
const toPad = (track: TrackName, v: number) => (track === 'throttle' ? v * 2 - 1 : v)
const fromPad = (track: TrackName, p: number) => (track === 'throttle' ? (p + 1) / 2 : p)

/** The moments whose pairs fit side by side (a later one closer than `gap` pixels is left out). */
function spaced(times: number[], x: (t: number) => number, gap: number): number[] {
  const out: number[] = []
  for (const t of times) if (!out.length || x(t) - x(out[out.length - 1]) >= gap) out.push(t)
  return out
}

/** Stick pairs above the ruler at every moment a stick changes; a click puts the cursor there. */
function StickStrip({ doc, x, mode, cursor, onPick }: { doc: ArsDoc; x: (t: number) => number; mode: 1 | 2; cursor: number; onPick: (t: number) => void }) {
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
              onPick(t)
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

/** Both sticks at the cursor (or the playhead while flying): drag to set every axis from that moment on. */
function StickPads({ doc, at, commit }: { doc: ArsDoc; at: number; commit: (doc: ArsDoc, sel?: Selection | null) => void }) {
  const st = useST()
  const mode = usePrefs((p) => p.stickMode)
  const ax = axesFor(mode)
  const s = sticksAt(doc, at + 0.0005)
  return (
    <div className="seq-panel" data-seq-pads="">
      <div className="flex items-center gap-2">
        <span className="cap text-[10px] text-[var(--g400)]">{st('seq.pads', { t: fmtS(at) })}</span>
        <span className="flex-1" />
        <span className="text-[10px] text-[var(--g500)]">{st(mode === 1 ? 'seq.pads.mode1' : 'seq.pads.mode2')}</span>
      </div>
      <div className="flex justify-center gap-4">
        <Pad side="left" x={ax.lx} y={ax.ly} values={s} doc={doc} t={at} commit={commit} />
        <Pad side="right" x={ax.rx} y={ax.ry} values={s} doc={doc} t={at} commit={commit} />
      </div>
      <p className="text-hud-sub text-[var(--g500)]">{st('seq.pads.hint')}</p>
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
