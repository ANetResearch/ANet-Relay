// Timeline workspace (0.6.1): the stick timeline as its own tab, built for the loop "change a number, run, watch,
// stop, change again". Top to bottom: document bar, the stage with the live monitor, the transport (never scrolls
// away), the block strip, then the tracks and the code side by side. One document sits behind both views; Run
// works from either (Ctrl/⌘+Enter anywhere in the workspace). Replaces the 0.5.3 dock under the Fly stage.
import * as React from 'react'
import { api, type Vehicle } from '@/api'
import { useLangStore } from '@/i18n'
import { cn } from '@/lib/utils'
import { registerDismisser, registerHotkey } from '@/shell/keyboard'
import { notify, notifyError } from '@/shell/notify'
import { addSim, possess } from '@/shell/actions'
import { useRelay } from '@/store/relay'
import { useSelectedVehicle } from '@/store/selectors'
import { useUi } from '@/store/ui'
import { Button } from '@/ui/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/ui/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from '@/ui/components/ui/dropdown-menu'
import { Input } from '@/ui/components/ui/input'
import { NativeSelect, NativeSelectOption } from '@/ui/components/ui/native-select'
import { Icon } from '@/ui/icons'
import { ControllerPill } from '@/ui/relay'
import { Stage } from '@/stage/Stage'
import {
  ARS_SUFFIX, ArsError, armWaitThrottle, blockAt, blocks, duplicateBlock, fileName, insertAfter, keyTimes, moveStart, parseArs, range, removeBlock, resize,
  rippleFrom, sampleTrack, SEQ_MODES, serialiseArs, setDocDuration, setEase, setLabel, setTrim, setValue, splitAt, templateBlank, templateDoublet,
  templateHover, templateStep, TRACKS, type ArsDoc, type Block, type SeqEnd, type SeqMode, type TrackName,
} from './model'
import { fmtS, fmtStick, LabelInput, NumberInput, StickPads, TrimPanel } from './parts'
import { blocked, commitDoc, flightOf, pushTrim, redo, runSequence, stopSequence, target, undo, useRun, type Flight } from './run'
import { SequenceCode } from './SequenceCode'
import { isDirty, useSequence, type Selection, type SeqView } from './store'
import { translateSeq, useST, type SeqKey } from './strings'
import { fitZoom, Tracks } from './Tracks'
import './sequence.css'

const MAC = typeof navigator !== 'undefined' && /mac/i.test(navigator.platform || navigator.userAgent)
const MOD = MAC ? '⌘' : 'Ctrl'
const RUN_KEY = `${MOD}+↵`
const seq = () => useSequence.getState()

function download(doc: ArsDoc) {
  const url = URL.createObjectURL(new Blob([serialiseArs(doc)], { type: 'application/json' }))
  const a = document.createElement('a')
  a.href = url
  a.download = fileName(doc)
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}

// ---------------------------------------------------------------- block edits shared by the keys and the strip

const selection = (): Selection[] => {
  const s = seq()
  return s.sel ? [...s.more, s.sel] : []
}
function primaryBlock(): Block | null {
  const s = seq()
  return s.sel ? (blocks(s.doc, s.sel.track)[s.sel.index] ?? null) : null
}
/** Select a block from the keys: the cursor goes to its start, so "run from cursor" runs from this block. */
function go(sel: Selection, extend = false): void {
  const s = seq()
  const b = blocks(s.doc, sel.track)[sel.index]
  if (!b) return
  s.select(sel, extend ? 'add' : 'one')
  s.setCursor(b.start)
}
function nudgeValue(d: number): void {
  let next = seq().doc
  for (const x of selection()) {
    const k = next.tracks[x.track]?.[x.index]
    if (k) next = setValue(next, x.track, x.index, k.v + d)
  }
  commitDoc(next)
}
function setValues(text: string): void {
  const all = selection()
  const n = Number(text.replace('−', '-'))
  if (!all.length || text.trim() === '' || !Number.isFinite(n)) return
  const relative = all.length > 1 && /^[+\-−]/.test(text.trim())
  let next = seq().doc
  for (const x of all) {
    const k = next.tracks[x.track]?.[x.index]
    if (k) next = setValue(next, x.track, x.index, relative ? k.v + n : n)
  }
  commitDoc(next)
}
function removeSelected(): void {
  let next = seq().doc
  const all = selection().sort((a, b) => b.index - a.index)
  for (const x of all) next = removeBlock(next, x.track, x.index)
  commitDoc(next, null)
}
function splitHere(allTracks: boolean): void {
  const s = seq()
  const b = primaryBlock()
  if (!b && !allTracks) return
  let next = s.doc
  let sel: Selection | null = s.sel
  for (const name of allTracks ? TRACKS : [b!.track]) {
    const r = splitAt(next, name, s.cursor)
    next = r.doc
    if (s.sel?.track === name) sel = { track: name, index: r.index }
  }
  commitDoc(next, sel)
}
function resizePrimary(d: number): void {
  const s = seq()
  const b = primaryBlock()
  if (b) commitDoc(resize(s.doc, b.track, b.index, b.end - b.start + d, s.prefs.ripple))
}
function insertBlock(): void {
  const s = seq()
  const b = primaryBlock()
  if (!b) return
  const r = insertAfter(s.doc, b.track, b.index, 1)
  if (r.index === b.index) return
  commitDoc(s.prefs.ripple ? rippleFrom(r.doc, b.end + 1e-4, 1, b.track) : r.doc, { track: b.track, index: r.index })
  seq().setCursor(b.end)
}
function duplicate(): void {
  const s = seq()
  const b = primaryBlock()
  if (!b) return
  const r = duplicateBlock(s.doc, b.track, b.index, s.prefs.ripple)
  commitDoc(r.doc, { track: b.track, index: r.index })
}
function toggleEase(): void {
  let next = seq().doc
  const b = primaryBlock()
  if (!b) return
  const ease = b.ease === 'ramp' ? 'hold' : 'ramp'
  for (const x of selection()) next = setEase(next, x.track, x.index, ease)
  commitDoc(next)
}
function zoomBy(f: number): void {
  const s = seq()
  const w = (document.querySelector('[data-seq-tracks] .seq-scroll') as HTMLElement | null)?.clientWidth ?? 800
  const fz = fitZoom(w, s.doc.duration_s)
  const next = (s.prefs.fit ? fz : Math.max(fz, s.prefs.zoom)) * f
  s.setPrefs(next <= fz ? { fit: true } : { fit: false, zoom: next })
}
function zoomToSelection(): void {
  const s = seq()
  const all = selection().map((x) => blocks(s.doc, x.track)[x.index]).filter(Boolean)
  const el = document.querySelector('[data-seq-tracks] .seq-scroll') as HTMLElement | null
  if (!all.length || !el) return
  const t0 = Math.min(...all.map((b) => b.start))
  const t1 = Math.max(...all.map((b) => b.end))
  const span = (t1 - t0) * 1.2
  const zoom = Math.min(400, (el.clientWidth - 24) / Math.max(0.2, span))
  s.setPrefs({ fit: false, zoom })
  requestAnimationFrame(() => (el.scrollLeft = Math.max(0, (t0 - (t1 - t0) * 0.1) * zoom)))
}
function trimBy(track: TrackName, d: number): void {
  const next = setTrim(seq().doc, track, seq().doc.trim[track] + d)
  seq().edit(next)
  pushTrim(next.trim)
}
function save(): void {
  seq().saveToLibrary()
  notify(translateSeq(useLangStore.getState().lang, 'seq.saved', { name: seq().doc.name }))
}

// ---------------------------------------------------------------- the workspace

export function SequenceWorkspace() {
  const st = useST()
  const v = useSelectedVehicle()
  const doc = useSequence((s) => s.doc)
  const prefs = useSequence((s) => s.prefs)
  const problems = useSequence((s) => s.problems)
  const flight = flightOf(v)
  const [sheet, setSheet] = React.useState(false)
  const [wide, setWide] = React.useState(true)
  const root = React.useRef<HTMLElement | null>(null)
  const tracksRef = React.useRef<HTMLDivElement | null>(null)
  const focusCode = React.useRef<(() => void) | null>(null)
  const strip = React.useRef<StripHandle | null>(null)
  const fileRef = React.useRef<HTMLInputElement | null>(null)
  const view: SeqView = !wide && prefs.view === 'split' ? 'tracks' : prefs.view
  const focusTracks = () => tracksRef.current?.focus({ preventScroll: true })

  // an aircraft to fly on: the selected one, else the first of the fleet
  const vehicles = useRelay((s) => s.fleet?.vehicles)
  React.useEffect(() => {
    if (!useUi.getState().selectedId && vehicles?.length) useUi.getState().select((vehicles.find((x) => x.kind === 'sim') ?? vehicles[0]).id)
  }, [vehicles])

  React.useEffect(() => {
    focusTracks()
    const el = root.current
    if (!el) return
    const ro = new ResizeObserver(() => setWide(el.clientWidth >= 1200))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // the altitude of the run against sequence time (the response lane)
  React.useEffect(
    () =>
      useRelay.subscribe(() => {
        const cur = target()
        const f = flightOf(cur)
        if (cur && f.flying && f.progress != null && f.state === 'running' && cur.altitude_m != null) useRun.getState().sample(f.progress, cur.altitude_m)
      }),
    [],
  )

  // Esc with nothing flying clears the block selection before it means "stop the selected aircraft"
  React.useEffect(
    () =>
      registerDismisser(() => {
        if (!seq().sel) return false
        seq().select(null)
        return true
      }),
    [],
  )

  // keys that work everywhere in the workspace, the code editor and the fields included
  React.useEffect(() => {
    const own = (fn: (e: KeyboardEvent) => void) => (e: KeyboardEvent) => {
      e.stopPropagation() // the code editor must not also take the key
      ;(document.activeElement as HTMLElement | null)?.blur?.() // a field commits first
      fn(e)
      if (!root.current?.contains(document.activeElement)) focusTracks()
    }
    const keep = (fn: (e: KeyboardEvent) => void) => (e: KeyboardEvent) => {
      e.stopPropagation()
      fn(e)
    }
    const offs = [
      registerHotkey({ combo: 'mod+enter', workspace: 'sequence', inText: true, handler: keep(() => void runSequence(0)) }),
      registerHotkey({ combo: 'mod+shift+enter', workspace: 'sequence', inText: true, handler: keep(() => void runSequence(seq().cursor)) }),
      registerHotkey({ combo: 'mod+s', workspace: 'sequence', inText: true, handler: keep((e) => (e.shiftKey ? download(seq().doc) : save())) }),
      registerHotkey({ combo: 'mod+o', workspace: 'sequence', inText: true, handler: keep(() => (document.querySelector('[data-seq-menu]') as HTMLElement | null)?.click()) }),
      registerHotkey({ combo: 'alt+t', workspace: 'sequence', inText: true, handler: own(() => seq().setPrefs({ view: 'tracks' })) }),
      registerHotkey({ combo: 'alt+s', workspace: 'sequence', inText: true, handler: own(() => seq().setPrefs({ view: 'split' })) }),
      registerHotkey({ combo: 'alt+c', workspace: 'sequence', inText: true, handler: keep(() => { seq().setPrefs({ view: 'code' }); window.setTimeout(() => focusCode.current?.(), 30) }) }),
      registerHotkey({
        combo: 'mod+e',
        workspace: 'sequence',
        inText: true,
        handler: keep(() => {
          if ((document.activeElement as HTMLElement | null)?.closest('.cm-editor')) focusTracks()
          else {
            if (seq().prefs.view === 'tracks') seq().setPrefs({ view: 'split' })
            window.setTimeout(() => focusCode.current?.(), 30)
          }
        }),
      }),
    ]
    return () => offs.forEach((f) => f())
  }, [])

  const onKeyDown = (e: React.KeyboardEvent) => {
    const el = e.target as HTMLElement
    const tag = el.tagName
    if (el.closest('.cm-editor') || tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return
    if (tag === 'BUTTON' && (e.key === ' ' || e.key === 'Enter')) return
    const mod = e.metaKey || e.ctrlKey
    const big = e.shiftKey
    const s = seq()
    const key = e.key
    const done = () => e.preventDefault()
    if (mod && key.toLowerCase() === 'z') return done(), big ? redo() : undo()
    if (key === ' ') return done(), stopSequence()
    if (key === '?') return done(), setSheet(true)
    if (!mod && !e.altKey && key === '\\') return done(), s.setPrefs({ fit: true })
    if (e.altKey && (e.code === 'Equal' || e.code === 'Minus')) return done(), zoomBy(e.code === 'Equal' ? 1.4 : 1 / 1.4)
    if (!mod && !e.altKey && key.toLowerCase() === 'g') return done(), s.setPrefs({ ripple: !s.prefs.ripple })
    if (!mod && !e.altKey && (key === '[' || key === ']')) {
      const times = [...keyTimes(s.doc), s.doc.duration_s]
      const next = key === ']' ? times.find((t) => t > s.cursor + 1e-6) : [...times].reverse().find((t) => t < s.cursor - 1e-6)
      if (next !== undefined) s.setCursor(next)
      return done()
    }
    if (e.altKey && e.shiftKey && key.startsWith('Arrow')) {
      done()
      if (key === 'ArrowUp' || key === 'ArrowDown') trimBy('pitch', key === 'ArrowUp' ? 0.005 : -0.005)
      else trimBy('roll', key === 'ArrowRight' ? 0.005 : -0.005)
      return
    }
    if (problems.length) return // the code has problems: the tracks are read-only
    if (mod && key.toLowerCase() === 'a') {
      done()
      const names = big ? TRACKS : [s.sel?.track ?? 'throttle']
      const all = names.flatMap((name) => blocks(s.doc, name).map((b) => ({ track: name, index: b.index })))
      return s.selectMany(all, s.sel ?? all[0] ?? null)
    }
    if (big && key.toLowerCase() === 's' && !mod && !e.altKey) return done(), splitHere(true)
    const b = primaryBlock()
    if (!b || !s.sel) {
      // nothing selected: the arrows pick the block under the cursor
      if (key.startsWith('Arrow') || key === 'Tab') {
        done()
        go({ track: 'throttle', index: blockAt(s.doc, 'throttle', s.cursor) })
      }
      return
    }
    const count = (s.doc.tracks[b.track] ?? []).length
    const step = big ? 0.5 : 0.1
    if (key === 'ArrowLeft' || key === 'ArrowRight') {
      done()
      const dir = key === 'ArrowRight' ? 1 : -1
      if (e.altKey) return resizePrimary(dir * step)
      if (mod) return commitDoc(moveStart(s.doc, b.track, b.index, b.start + dir * step))
      const i = b.index + dir
      if (i >= 0 && i < count) go({ track: b.track, index: i }, big)
      return
    }
    if (key === 'ArrowUp' || key === 'ArrowDown') return done(), nudgeValue((big ? 0.05 : 0.01) * (key === 'ArrowUp' ? 1 : -1))
    if (key === 'Tab') {
      done()
      const row = TRACKS.indexOf(b.track) + (big ? -1 : 1)
      if (row >= 0 && row < TRACKS.length) go({ track: TRACKS[row], index: blockAt(s.doc, TRACKS[row], b.start + 0.0005) })
      return
    }
    if (key === 'Home') return done(), go({ track: b.track, index: 0 })
    if (key === 'End') return done(), go({ track: b.track, index: count - 1 })
    if (key === 'Delete' || key === 'Backspace') return done(), removeSelected()
    if (mod && key.toLowerCase() === 'd') return done(), duplicate()
    if (mod || e.altKey) return
    if (key === 'Enter') return done(), strip.current?.typeValue()
    if (key === 'F2' || key.toLowerCase() === 'l') return done(), strip.current?.editLabel()
    if (/^[0-9.+-]$/.test(key)) return done(), strip.current?.typeValue(key)
    if (key.toLowerCase() === 'r') return done(), toggleEase()
    if (key.toLowerCase() === 's') return done(), splitHere(false)
    if (key.toLowerCase() === 'i') return done(), insertBlock()
    if (key.toLowerCase() === 'z') return done(), zoomToSelection()
  }

  const onImport = async (file: File) => {
    try {
      const next = parseArs(await file.text())
      seq().load(next)
      notify(st('seq.imported', { name: next.name || file.name }))
    } catch (e) {
      notify(st('seq.importFailed', { reason: e instanceof ArsError ? `${e.field}: ${e.reason}` : String(e) }), { level: 'warn' })
    }
  }

  const playhead = flight.progress
  return (
    <section
      ref={root}
      className="seqws l1"
      data-sequence-workspace=""
      data-own-keys=""
      onKeyDown={onKeyDown}
      aria-label={st('seq.title')}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault()
        const f = e.dataTransfer.files?.[0]
        if (f) void onImport(f)
      }}
    >
      <DocBar doc={doc} flying={flight.flying} view={view} wide={wide} onImport={() => fileRef.current?.click()} onKeys={() => setSheet(true)} />
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
      <Band v={v} doc={doc} at={playhead ?? undefined} />
      <Transport v={v} doc={doc} flight={flight} />
      <BlockStrip ref={strip} doc={doc} readOnly={problems.length > 0} onDone={focusTracks} />
      {problems.length && view !== 'code' ? <div className="seq-banner" data-seq-banner="">{st('seq.codeErrors', { n: problems.length })}</div> : null}
      <div className="seqws-main" data-seq-view={view}>
        {view === 'code' ? (
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <div className="shrink-0 border-b border-[var(--border)]" style={{ height: 57 }}>
              <Tracks doc={doc} readOnly={problems.length > 0} playhead={playhead} mini />
            </div>
            <SequenceCode playhead={playhead} focusRef={focusCode} />
          </div>
        ) : (
          <>
            <Tracks doc={doc} readOnly={problems.length > 0} playhead={playhead} rootRef={tracksRef} />
            {view === 'split' ? (
              <>
                <SplitGutter />
                <div className="flex min-h-0 min-w-[320px] flex-col border-l border-[var(--border)]" style={{ width: `${Math.round(prefs.split * 100)}%` }}>
                  <SequenceCode playhead={playhead} focusRef={focusCode} />
                </div>
              </>
            ) : null}
          </>
        )}
      </div>
      <KeySheet open={sheet} onOpenChange={setSheet} />
    </section>
  )
}

function SplitGutter() {
  const drag = React.useRef<{ x0: number; s0: number; w: number } | null>(null)
  return (
    <div
      className="seq-gutter-x"
      onPointerDown={(e) => {
        const w = (e.currentTarget.parentElement as HTMLElement).clientWidth
        drag.current = { x0: e.clientX, s0: seq().prefs.split, w }
        e.currentTarget.setPointerCapture(e.pointerId)
      }}
      onPointerMove={(e) => {
        const d = drag.current
        if (d) seq().setPrefs({ split: Math.min(0.7, Math.max(0.25, d.s0 - (e.clientX - d.x0) / d.w)) })
      }}
      onPointerUp={() => (drag.current = null)}
      onDoubleClick={() => seq().setPrefs({ split: 0.42 })}
    />
  )
}

// ---------------------------------------------------------------- A: document bar

function DocBar({ doc, flying, view, wide, onImport, onKeys }: { doc: ArsDoc; flying: boolean; view: SeqView; wide: boolean; onImport(): void; onKeys(): void }) {
  const st = useST()
  const past = useSequence((s) => s.past.length)
  const future = useSequence((s) => s.future.length)
  const prefs = useSequence((s) => s.prefs)
  const library = useSequence((s) => s.library)
  const dirty = useSequence(isDirty)
  const templates: [SeqKey, () => ArsDoc][] = [
    ['seq.tpl.hover', templateHover],
    ['seq.tpl.step', templateStep],
    ['seq.tpl.doublet', templateDoublet],
    ['seq.tpl.blank', templateBlank],
  ]
  return (
    <div className="seqws-bar" data-seq-docbar="">
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button variant="outline" size="sm" disabled={flying} data-seq-menu="" />}>
          <Icon icon="folder" />
          {st('seq.menu')}
          <Icon icon="chevron.down" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="l3 w-72">
          <DropdownMenuGroup>
            <DropdownMenuLabel>{st('seq.menu.library')}</DropdownMenuLabel>
            {library.length ? (
              library.map((e) => (
                <DropdownMenuItem key={e.name} onClick={() => seq().load(e.doc)} data-seq-lib={e.name} className="gap-2">
                  <span className="min-w-0 flex-1 truncate">{e.name}</span>
                  <span className="num text-[10px] text-[var(--g500)]">{e.doc.duration_s.toFixed(1)} s · {st(`seq.mode.${e.doc.mode}` as SeqKey)}</span>
                  <button
                    type="button"
                    className="rounded-sm px-1 text-[var(--g500)] hover:text-[var(--g50)]"
                    title={st('seq.delete')}
                    aria-label={`${st('seq.delete')} ${e.name}`}
                    onClick={(ev) => {
                      ev.stopPropagation()
                      seq().removeFromLibrary(e.name)
                    }}
                  >
                    <Icon icon="trash" className="size-3" />
                  </button>
                </DropdownMenuItem>
              ))
            ) : (
              <p className="px-2 py-1 text-hud-sub text-[var(--g500)]">{st('seq.menu.empty', { key: `${MOD}+S` })}</p>
            )}
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuLabel>{st('seq.menu.templates')}</DropdownMenuLabel>
            {templates.map(([key, make]) => (
              <DropdownMenuItem key={key} onClick={() => seq().load({ ...make(), name: st(key) })} data-seq-template={key}>
                {st(key)}
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuLabel>{st('seq.menu.files')}</DropdownMenuLabel>
            <DropdownMenuItem onClick={onImport} data-seq-import="">
              <Icon icon="seq.import" />
              {st('seq.importFile')}
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => download(doc)} data-seq-export="">
              <Icon icon="seq.export" />
              {st('seq.exportFile')}
            </DropdownMenuItem>
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
      <Input
        value={doc.name}
        onChange={(e) => seq().edit({ ...doc, name: e.target.value.slice(0, 80) }, { transient: true })}
        onBlur={() => seq().edit(seq().doc)}
        placeholder={st('seq.name')}
        aria-label={st('seq.name')}
        className="h-6 w-48 px-1.5 text-[12px]"
        data-seq-name=""
      />
      <button type="button" className="seq-dirty" data-dirty={dirty ? '' : undefined} title={dirty ? `${st('seq.unsaved')} · ${st('seq.save')} ${MOD}+S` : st('seq.save')} onClick={save} aria-label={st('seq.save')} data-seq-save="">
        <Icon icon="save" className="size-3.5" />
      </button>
      <NativeSelect size="sm" value={doc.mode} disabled={flying} onChange={(e) => commitDoc({ ...doc, mode: e.target.value as SeqMode })} className="h-6 w-[104px] text-[11px]" aria-label={st('seq.mode')} data-seq-mode="">
        {SEQ_MODES.map((m) => (
          <NativeSelectOption key={m} value={m}>
            {st(`seq.mode.${m}` as SeqKey)}
          </NativeSelectOption>
        ))}
      </NativeSelect>
      <NativeSelect size="sm" value={doc.end} onChange={(e) => commitDoc({ ...doc, end: e.target.value as SeqEnd })} className="h-6 w-[148px] text-[11px]" aria-label={st('seq.end')} data-seq-end="">
        {(['disarm', 'hold'] as const).map((m) => (
          <NativeSelectOption key={m} value={m}>
            {st(`seq.end.${m}` as SeqKey)}
          </NativeSelectOption>
        ))}
      </NativeSelect>
      <label className="flex items-center gap-1 text-hud-sub text-[var(--g400)]">
        {st('seq.length')}
        <NumberInput value={doc.duration_s} step={0.5} min={0.1} max={600} onCommit={(n) => commitDoc(setDocDuration(doc, n))} className="w-16" testId="seq-length" />
        <span>{st('seq.unit.s')}</span>
      </label>
      <span className="flex-1" />
      <Button variant="ghost" size="icon-sm" disabled={!past} onClick={undo} title={`${st('seq.undo')} ${MOD}+Z`} aria-label={st('seq.undo')}>
        <Icon icon="seq.undo" />
      </Button>
      <Button variant="ghost" size="icon-sm" disabled={!future} onClick={redo} title={`${st('seq.redo')} ${MOD}+Shift+Z`} aria-label={st('seq.redo')}>
        <Icon icon="seq.redo" />
      </Button>
      <button type="button" className="seq-toggle" data-on={prefs.ripple ? '' : undefined} onClick={() => seq().setPrefs({ ripple: !prefs.ripple })} title={st('seq.ripple.hint')} data-seq-ripple={prefs.ripple ? 'on' : 'off'}>
        {st('seq.ripple')}
      </button>
      <button type="button" className="seq-toggle" data-on={prefs.fit ? '' : undefined} onClick={() => seq().setPrefs({ fit: true })} title={st('seq.fit')} data-seq-fit-button="">
        <Icon icon="scan.frame" className="size-3" />
      </button>
      <div className="seq-seg" role="group" aria-label={st('seq.view')}>
        {(['tracks', 'split', 'code'] as const).map((id) =>
          id === 'split' && !wide ? null : (
            <button key={id} type="button" data-on={view === id ? '' : undefined} onClick={() => seq().setPrefs({ view: id })} title={`Alt+${id === 'tracks' ? 'T' : id === 'split' ? 'S' : 'C'}`} data-seq-view-button={id}>
              {st(`seq.view.${id}` as SeqKey)}
            </button>
          ),
        )}
      </div>
      <Button variant="ghost" size="icon-sm" onClick={onKeys} title={`${st('seq.keysheet')} ?`} aria-label={st('seq.keysheet')} data-seq-keys="">
        <Icon icon="keyboard" />
      </Button>
    </div>
  )
}

// ---------------------------------------------------------------- B + C: the stage and the monitor

function Band({ v, doc, at }: { v: Vehicle | null; doc: ArsDoc; at?: number }) {
  const st = useST()
  const band = useSequence((s) => s.prefs.band)
  const cursor = useSequence((s) => s.cursor)
  const drag = React.useRef<{ y0: number; h0: number } | null>(null)
  const deg = (r: number | undefined) => (r == null ? '—' : `${((r * 180) / Math.PI).toFixed(1)}°`)
  return (
    <>
      {band > 0 ? (
        <div className="seqws-band" style={{ height: band }} data-seq-band="">
          <div className="relative min-w-[320px] flex-1 overflow-hidden bg-[var(--g950)]" data-seq-stage="">
            <Stage bare />
          </div>
          <aside className="seqws-monitor" data-seq-monitor="">
            <StickPads doc={doc} at={at ?? cursor} commit={commitDoc} />
            <div className="flex min-w-0 flex-1 flex-col">
              <div className="seq-panel" data-seq-live="">
                <span className="cap flex justify-between text-[10px] text-[var(--g400)]">
                  {st('seq.live.title')}
                  {v ? <span className={v.armed ? 'text-[var(--r400)]' : ''}>{st(v.armed ? 'seq.live.armed' : 'seq.live.disarmed')}</span> : null}
                </span>
                {v ? (
                  <dl className="seq-live">
                    <dt>{st('seq.live.alt')}</dt>
                    <dd>{v.altitude_m != null ? `${v.altitude_m.toFixed(2)} m` : '—'}</dd>
                    <dt>{st('seq.live.vs')}</dt>
                    <dd>{v.climb_ms != null ? `${v.climb_ms >= 0 ? '+' : ''}${v.climb_ms.toFixed(2)} m/s` : '—'}</dd>
                    <dt>{st('seq.live.att')}</dt>
                    <dd>
                      {deg(v.attitude?.roll)} {deg(v.attitude?.pitch)} {deg(v.attitude?.yaw)}
                    </dd>
                    <dt>{st('seq.live.bat')}</dt>
                    <dd>{v.battery.voltage != null ? `${v.battery.voltage.toFixed(1)} V` : '—'}</dd>
                    <dt>{st('seq.live.mode')}</dt>
                    <dd>{v.mode ?? '—'}</dd>
                  </dl>
                ) : (
                  <p className="text-hud-sub text-[var(--g500)]">{st('seq.noAircraft')}</p>
                )}
              </div>
              <TrimPanel
                doc={doc}
                onChange={(next, done) => {
                  if (!done) seq().edit(next, { transient: true })
                  else {
                    seq().edit(next)
                    pushTrim(next.trim)
                  }
                }}
              />
            </div>
          </aside>
        </div>
      ) : null}
      <div
        className="seq-gutter-y"
        data-folded={band > 0 ? undefined : ''}
        onPointerDown={(e) => {
          if (band <= 0) return
          drag.current = { y0: e.clientY, h0: band }
          e.currentTarget.setPointerCapture(e.pointerId)
        }}
        onPointerMove={(e) => {
          const d = drag.current
          if (!d) return
          const max = Math.max(200, window.innerHeight * 0.5)
          seq().setPrefs({ band: Math.round(Math.min(max, Math.max(160, d.h0 + e.clientY - d.y0))) })
        }}
        onPointerUp={() => (drag.current = null)}
        onDoubleClick={() => seq().setPrefs({ band: 260 })}
      >
        <button type="button" onClick={() => seq().setPrefs({ band: band > 0 ? 0 : 260 })} title={st(band > 0 ? 'seq.band.fold' : 'seq.band.open')} aria-label={st(band > 0 ? 'seq.band.fold' : 'seq.band.open')} data-seq-band-toggle="">
          <Icon icon={band > 0 ? 'chevron.up' : 'chevron.down'} className="size-3" />
        </button>
      </div>
    </>
  )
}

// ---------------------------------------------------------------- D: transport

function Transport({ v, doc, flight }: { v: Vehicle | null; doc: ArsDoc; flight: Flight }) {
  const st = useST()
  const vehicles = useRelay((s) => s.fleet?.vehicles) ?? []
  const cursor = useSequence((s) => s.cursor)
  const prefs = useSequence((s) => s.prefs)
  const problems = useSequence((s) => s.problems.length)
  const lastRun = useSequence((s) => s.lastRun)
  const { busy, ask, error, syncedAt } = useRun()
  const [recordingSince, setRecordingSince] = React.useState<number | null>(null)
  const [recBusy, setRecBusy] = React.useState(false)
  const [word, setWord] = React.useState('')
  const [synced, setSynced] = React.useState(false)
  const human = v?.controller === 'human'
  const stuck = blocked(v, doc)
  const total = doc.duration_s
  const now = flight.progress

  // the "sent" chip fades a second after an edit reached the aircraft
  React.useEffect(() => {
    if (!syncedAt) return
    const on = window.setTimeout(() => setSynced(true), 0)
    const off = window.setTimeout(() => setSynced(false), 1000)
    return () => {
      window.clearTimeout(on)
      window.clearTimeout(off)
    }
  }, [syncedAt])
  // an ask belongs to one aircraft
  const [askFor, setAskFor] = React.useState(v?.id)
  if (askFor !== v?.id) {
    setAskFor(v?.id)
    setWord('')
  }
  React.useEffect(() => useRun.getState().setAsk(null), [v?.id])

  const toggleRecord = async () => {
    if (!v) return
    setRecBusy(true)
    try {
      if (recordingSince === null) {
        await api.recordSequence(v.id, 'start', doc.name)
        setRecordingSince(Date.now())
      } else {
        try {
          const out = (await api.recordSequence(v.id, 'stop')) as { sequence?: unknown }
          const next = parseArs(JSON.stringify(out.sequence))
          seq().load(next)
          notify(st('seq.recorded', { t: fmtS(next.duration_s) }))
        } finally {
          setRecordingSince(null)
        }
      }
    } catch (e) {
      notifyError(e)
    } finally {
      setRecBusy(false)
    }
  }

  let status: string | null = null
  if (flight.flying) {
    if (flight.phase === 'mode') status = st('seq.state.mode')
    else if (flight.phase === 'arm') status = st('seq.state.armThrottle', { pct: Math.round(armWaitThrottle(doc.mode, v?.autopilot) * 100) })
    else if (flight.phase === 'end') status = st('seq.state.end')
    else if (flight.state === 'done') status = st('seq.state.hold')
    else status = st('seq.state.play', { t: fmtS(now ?? 0), total: fmtS(total) })
  } else if (flight.state === 'done') status = st('seq.state.done', { t: fmtS(total) })
  else if (flight.state === 'failed' && flight.detail) status = st('seq.state.failed', { detail: flight.detail })

  const picker = (
    <NativeSelect
      size="sm"
      value={v?.id ?? ''}
      onChange={(e) => useUi.getState().select(e.target.value || null)}
      className="h-6 w-[148px] text-[11px]"
      aria-label={st('seq.target')}
      data-seq-target=""
    >
      {v ? null : <NativeSelectOption value="">{st('seq.target')}</NativeSelectOption>}
      {vehicles.map((x) => (
        <NativeSelectOption key={x.id} value={x.id}>
          {x.kind === 'sim' ? 'SIM · ' : ''}
          {x.label}
          {x.armed ? ' ●' : ''}
        </NativeSelectOption>
      ))}
    </NativeSelect>
  )

  if (!vehicles.length) {
    return (
      <div className="seqws-transport" data-seq-transport="empty">
        <span className="text-hud-sub text-[var(--g400)]">{st('seq.noFleet')}</span>
        <Button variant="outline" size="sm" onClick={addSim} data-seq-add-sim="">
          <Icon icon="plus" />
          {st('seq.addSim')}
        </Button>
      </div>
    )
  }

  if (ask && v) {
    // the bar itself asks, so nothing is pushed off screen
    const go = () => void runSequence(ask.startAt, ask.kind === 'confirm' ? { confirm: 'ARM' } : { takeover: true })
    return (
      <div className="seqws-transport is-ask" data-seq-transport="ask" data-seq-ask={ask.kind}>
        {picker}
        {ask.kind === 'confirm' ? (
          <>
            <span className="text-hud-sub text-[var(--g100)]">{st('seq.confirmInline', { aircraft: v.label })}</span>
            <Input
              autoFocus
              value={word}
              onChange={(e) => setWord(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && word.trim().toUpperCase() === 'ARM') {
                  e.preventDefault()
                  setWord('')
                  go()
                } else if (e.key === 'Escape') useRun.getState().setAsk(null)
              }}
              placeholder="ARM"
              aria-label="ARM"
              className="num h-6 w-20 px-1.5 font-mono text-[12px] uppercase"
              data-seq-arm=""
            />
            <Button variant="outline" size="sm" disabled={busy || word.trim().toUpperCase() !== 'ARM'} onClick={() => { setWord(''); go() }}>
              <Icon icon="tl.play" />
              {st('seq.run')}
            </Button>
          </>
        ) : (
          <>
            <span className="text-hud-sub text-[var(--g100)]">{st(ask.kind === 'takeover' ? 'seq.takeover' : 'seq.possessReal', { aircraft: v.label })}</span>
            <Button variant="outline" size="sm" disabled={busy} onClick={go} autoFocus data-seq-ask-go="">
              {st(ask.kind === 'takeover' ? 'seq.takeoverGo' : 'seq.possess')}
            </Button>
          </>
        )}
        <Button variant="ghost" size="sm" onClick={() => useRun.getState().setAsk(null)}>
          {st('seq.cancel')}
        </Button>
        {error ? <span className="truncate text-hud-sub font-[560] text-[var(--g50)]">{error}</span> : null}
      </div>
    )
  }

  const cantRun = !v || busy || problems > 0 || stuck
  return (
    <div className="seqws-transport" data-seq-transport={flight.flying ? 'flying' : 'idle'}>
      {picker}
      {v ? <ControllerPill vehicle={v} className="shrink-0" /> : null}
      {v && !human && v.kind === 'real' && v.controller !== 'agent' ? (
        <Button variant="ghost" size="sm" onClick={() => possess(v.id)} title="P" data-seq-possess="">
          <Icon icon="possess" />
          {st('seq.possess')}
        </Button>
      ) : null}
      {flight.flying ? (
        <Button variant="outline" size="sm" disabled={!human} onClick={stopSequence} title="Space · Esc" data-seq-stop="">
          <Icon icon="stop" />
          {st('seq.stop')}
          <kbd className="seq-kbd">Space</kbd>
        </Button>
      ) : (
        <>
          <Button variant="outline" size="sm" disabled={cantRun} onClick={() => void runSequence(0)} title={problems ? st('seq.fixFirst') : undefined} data-seq-run="">
            <Icon icon="tl.play" />
            {st('seq.run')}
            <kbd className="seq-kbd">{RUN_KEY}</kbd>
          </Button>
          <Button variant="ghost" size="sm" disabled={cantRun || cursor <= 0 || cursor >= total} onClick={() => void runSequence(cursor)} title={`${MOD}+Shift+↵`} data-seq-run-cursor="">
            {st('seq.runAt', { t: fmtS(cursor) })}
          </Button>
        </>
      )}
      <div className="seq-seg" role="group" aria-label={st('seq.speed')} title={st('seq.speed')}>
        {([0.5, 1, 2] as const).map((x) => (
          <button key={x} type="button" data-on={prefs.speed === x ? '' : undefined} disabled={flight.flying} onClick={() => seq().setPrefs({ speed: x })} data-seq-speed={x}>
            {x}×
          </button>
        ))}
      </div>
      {v?.kind === 'sim' ? (
        <button type="button" className="seq-toggle" data-on={prefs.reset ? '' : undefined} onClick={() => seq().setPrefs({ reset: !prefs.reset })} title={st('seq.reset.hint')} data-seq-reset={prefs.reset ? 'on' : 'off'}>
          <Icon icon="reset" className="size-3" />
          {st('seq.reset')}
        </button>
      ) : null}
      {status ? (
        <span className="num truncate text-hud-sub text-[var(--g200)]" data-seq-status={flight.flying ? (flight.phase ?? 'play') : (flight.state ?? '')}>
          {status}
        </span>
      ) : null}
      {flight.flying ? <span className="seq-chip" title={st('seq.live')}>{synced ? st('seq.synced') : st('seq.liveChip')}</span> : null}
      {stuck ? <span className="truncate text-hud-sub text-[var(--g400)]">{st('seq.assistOff')}</span> : null}
      <span className="flex-1" />
      {lastRun && lastRun !== doc && !flight.flying ? (
        <Button variant="ghost" size="sm" onClick={() => { seq().load(lastRun); notify(st('seq.restored')) }} data-seq-restore="">
          <Icon icon="seq.undo" />
          {st('seq.restore')}
        </Button>
      ) : null}
      {v ? (
        <span className="num hidden items-center gap-2 text-hud-sub text-[var(--g400)] min-[1500px]:flex" data-seq-sticks="">
          {TRACKS.map((n) => (
            <span key={n}>
              {st(`seq.track.${n}` as SeqKey)} <span className="text-[var(--g100)]">{fmtStick(n, sampleTrack(doc, n, now ?? cursor))}</span>
            </span>
          ))}
        </span>
      ) : null}
      {v ? (
        <Button variant={recordingSince !== null ? 'destructive' : 'ghost'} size="sm" disabled={!human || recBusy} onClick={() => void toggleRecord()} title={st('seq.recordHint')} data-seq-record={recordingSince !== null ? 'on' : 'off'}>
          <Icon icon={recordingSince !== null ? 'square' : 'seq.record'} />
          {recordingSince !== null ? st('seq.recordStop') : st('seq.record')}
        </Button>
      ) : null}
      {recordingSince !== null ? <RecordClock since={recordingSince} /> : null}
      {flight.flying && now != null ? <i className="seq-progress" style={{ width: `${Math.min(100, (now / total) * 100)}%` }} /> : null}
    </div>
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

// ---------------------------------------------------------------- E: block strip

interface StripHandle {
  typeValue(first?: string): void
  editLabel(): void
}

const BlockStrip = React.forwardRef<StripHandle, { doc: ArsDoc; readOnly: boolean; onDone(): void }>(function BlockStrip({ doc, readOnly, onDone }, ref) {
  const st = useST()
  const sel = useSequence((s) => s.sel)
  const more = useSequence((s) => s.more)
  const cursor = useSequence((s) => s.cursor)
  const ripple = useSequence((s) => s.prefs.ripple)
  const block = sel ? (blocks(doc, sel.track)[sel.index] ?? null) : null
  const many = more.length > 0
  const [text, setText] = React.useState<string | null>(null)
  const valueRef = React.useRef<HTMLInputElement | null>(null)
  const wrap = React.useRef<HTMLDivElement | null>(null)
  React.useImperativeHandle(ref, () => ({
    typeValue(first) {
      if (!valueRef.current) return
      setText(first ?? null)
      valueRef.current.focus()
      if (first === undefined) window.setTimeout(() => valueRef.current?.select(), 0)
    },
    editLabel() {
      const el = wrap.current?.querySelector('[data-seq-label]') as HTMLInputElement | null
      el?.focus()
      el?.select()
    },
  }))
  // another block: forget a half-typed value
  const key = sel ? `${sel.track}:${sel.index}:${more.length}` : ''
  const [shown, setShown] = React.useState(key)
  if (shown !== key) {
    setShown(key)
    setText(null)
  }
  if (!sel || !block) {
    return (
      <div className="seqws-strip" data-seq-strip-panel="none">
        <span className="truncate text-hud-sub text-[var(--g500)]">{st('seq.sel.none', { run: RUN_KEY })}</span>
      </div>
    )
  }
  const [lo, hi] = range(sel.track)
  const commitValue = () => {
    if (text !== null) setValues(text)
    setText(null)
  }
  return (
    <div className={cn('seqws-strip', readOnly && 'pointer-events-none opacity-50')} ref={wrap} data-seq-strip-panel={`${sel.track}:${sel.index}`}>
      <span className="cap shrink-0 text-[10px] text-[var(--g300)]">
        {many ? st('seq.sel.many', { n: more.length + 1 }) : `${st(`seq.track.${sel.track}` as SeqKey)} · ${st('seq.block')} ${sel.index + 1}`}
      </span>
      <label className="seq-field">
        {many ? st('seq.sel.relative') : st('seq.block.value')}
        <Input
          ref={valueRef}
          value={text ?? (many ? '' : String(block.v))}
          placeholder={many ? '+0.02' : undefined}
          inputMode="decimal"
          onChange={(e) => setText(e.target.value)}
          onBlur={commitValue}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              commitValue()
              onDone()
            } else if (e.key === 'Escape') {
              e.stopPropagation()
              setText(null)
              onDone()
            } else if ((e.key === 'ArrowUp' || e.key === 'ArrowDown') && text === null) {
              e.preventDefault()
              nudgeValue((e.shiftKey ? 0.05 : 0.01) * (e.key === 'ArrowUp' ? 1 : -1))
            }
          }}
          title={`${lo} … ${hi}`}
          className="num h-6 w-[68px] px-1.5 font-mono text-[11px]"
          data-testid="seq-value"
        />
      </label>
      {many ? null : (
        <>
          <label className="seq-field">
            {st('seq.block.seconds')}
            <NumberInput value={Math.round((block.end - block.start) * 1000) / 1000} step={0.1} min={0.1} max={600} onCommit={(n) => commitDoc(resize(doc, sel.track, sel.index, n, ripple))} className="w-[64px]" testId="seq-seconds" />
            <span>{st('seq.unit.s')}</span>
          </label>
          <span className="num shrink-0 text-hud-sub text-[var(--g500)]">
            {st('seq.block.start')} {fmtS(block.start)}
          </span>
          <label className="seq-field min-w-0">
            {st('seq.block.label')}
            <span className="w-[120px]">
              <LabelInput value={block.label ?? ''} onCommit={(t) => { commitDoc(setLabel(doc, sel.track, sel.index, t)); onDone() }} />
            </span>
          </label>
        </>
      )}
      <div className="seq-seg" role="group">
        {(['hold', 'ramp'] as const).map((e) => (
          <button key={e} type="button" data-on={block.ease === e ? '' : undefined} onClick={() => block.ease !== e && toggleEase()} title="R" data-seq-ease={e}>
            {st(e === 'hold' ? 'seq.ease.hold' : 'seq.ease.ramp')}
          </button>
        ))}
      </div>
      <span className="flex-1" />
      <Button variant="ghost" size="sm" disabled={cursor <= block.start || cursor >= block.end} onClick={() => splitHere(false)} title="S" data-seq-split="">
        <Icon icon="seq.split" />
        {st('seq.split')}
      </Button>
      <Button variant="ghost" size="sm" onClick={insertBlock} title="I" data-seq-insert="">
        <Icon icon="seq.insert" />
        {st('seq.insert')}
      </Button>
      <Button variant="ghost" size="sm" onClick={duplicate} title={`${MOD}+D`} data-seq-duplicate="">
        <Icon icon="duplicate" />
        {st('seq.duplicate')}
      </Button>
      <Button variant="ghost" size="sm" disabled={(doc.tracks[sel.track]?.length ?? 0) <= 1} onClick={removeSelected} title="Delete" data-seq-remove="">
        <Icon icon="trash" />
        {st('seq.remove')}
      </Button>
    </div>
  )
})

// ---------------------------------------------------------------- key sheet

const SHEET: [string, SeqKey][] = [
  [`${MOD}+↵`, 'seq.k.run'],
  [`${MOD}+Shift+↵`, 'seq.k.runCursor'],
  ['Space', 'seq.k.stop'],
  ['Esc', 'seq.k.esc'],
  ['← →', 'seq.k.select'],
  ['Tab · Shift+Tab', 'seq.k.track'],
  ['↑ ↓', 'seq.k.value'],
  ['Alt+← →', 'seq.k.duration'],
  [`${MOD}+← →`, 'seq.k.start'],
  ['0–9 . + −  ·  Enter', 'seq.k.type'],
  ['L · F2', 'seq.k.label'],
  ['R', 'seq.k.ease'],
  ['S · Shift+S', 'seq.k.split'],
  ['I', 'seq.k.insert'],
  [`${MOD}+D`, 'seq.k.duplicate'],
  ['Delete', 'seq.k.remove'],
  [`${MOD}+A · ${MOD}+Shift+A`, 'seq.k.selectAll'],
  [`${MOD}+Z · ${MOD}+Shift+Z`, 'seq.k.undo'],
  ['[ ]', 'seq.k.cursor'],
  ['Alt+= · Alt+−', 'seq.k.zoom'],
  ['\\', 'seq.k.fit'],
  ['Z', 'seq.k.zoomSel'],
  ['G', 'seq.k.ripple'],
  ['Alt+Shift+↑↓ · ←→', 'seq.k.trim'],
  ['Alt+T · Alt+S · Alt+C', 'seq.k.view'],
  [`${MOD}+E`, 'seq.k.focus'],
  [`${MOD}+S`, 'seq.k.save'],
  ['Alt+↑ ↓', 'seq.k.nudge'],
  ['Tab · Shift+Tab', 'seq.k.cell'],
]

function KeySheet({ open, onOpenChange }: { open: boolean; onOpenChange(open: boolean): void }) {
  const st = useST()
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="l3 max-h-[calc(100vh-48px)] overflow-y-auto sm:max-w-[720px]" data-seq-keysheet="">
        <DialogHeader>
          <DialogTitle className="text-[15px] font-[560] text-[var(--g50)]">
            {st('seq.title')} · {st('seq.keysheet')}
          </DialogTitle>
        </DialogHeader>
        <dl className="seq-keys">
          {SHEET.map(([combo, key], i) => (
            <React.Fragment key={i}>
              <dt>{combo}</dt>
              <dd>{st(key)}</dd>
            </React.Fragment>
          ))}
        </dl>
      </DialogContent>
    </Dialog>
  )
}
