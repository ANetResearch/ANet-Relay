// Timeline workspace, flight plans (0.6.2, design 14): what the Timeline edits by default. A plan is a list of
// steps with their settings, not stick movements; the loop is "change a setting, run, watch, hold, change again".
// Top to bottom: document bar, the stage with the path preview and live values, the transport (never scrolls
// away), the time strip, then the step list and the code side by side. One document sits behind the list and the
// code; Run works from either (Ctrl/⌘+Enter anywhere in the workspace).
import * as React from 'react'
import { api, type BuiltinPlan, type Vehicle } from '@/api'
import { useLang, useLangStore } from '@/i18n'
import { cn } from '@/lib/utils'
import { registerDismisser, registerHotkey } from '@/shell/keyboard'
import { notify, notifyError } from '@/shell/notify'
import { addSim, possess } from '@/shell/actions'
import { useRelay } from '@/store/relay'
import { useSafetyVolume, useSelectedVehicle } from '@/store/selectors'
import { useUi } from '@/store/ui'
import { Button } from '@/ui/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/ui/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from '@/ui/components/ui/dropdown-menu'
import { Input } from '@/ui/components/ui/input'
import { NativeSelect, NativeSelectOption } from '@/ui/components/ui/native-select'
import { Icon } from '@/ui/icons'
import { ControllerPill } from '@/ui/relay'
import { Stage } from '@/stage/Stage'
import { parseCode, toCode } from './code'
import { KindSwitch } from './KindSwitch'
import {
  ACTIONS, PARAMS, RANGE, describe, duplicateStep, estimate, fits, fromApi, insertStep, moveStep, newStep, nudge, removeSteps, setAction, setNote, setParam,
  templateHop, templateOutAndBack, templateSquare, volumeFor, type Action, type Estimate, type ParamKey, type PlanDoc, type Volume,
} from './model'
import { PlanCode } from './PlanCode'
import { fmtT, PathPreview, PlanLanes } from './PlanLanes'
import { blocked, flightOf, runPlan, stopPlan, useRun, watchRuns, type PlanFlight } from './run'
import { isDirty, planId, usePlan, type PlanViewMode, type RunRecord } from './store'
import { translatePlan, usePT, type PlanKey } from './strings'
import '@/sequence/sequence.css'
import './plan.css'

const MAC = typeof navigator !== 'undefined' && /mac/i.test(navigator.platform || navigator.userAgent)
const MOD = MAC ? '⌘' : 'Ctrl'
const RUN_KEY = `${MOD}+↵`
const plan = () => usePlan.getState()
const vol = (v: Volume | null) => (v ? `${v.x} × ${v.y} × ${v.z}` : '—')
const INSERT_KEYS: Record<string, Action> = { t: 'takeoff', m: 'move', h: 'hover', y: 'yaw', g: 'goto', l: 'land', r: 'rtl' }

// ---------------------------------------------------------------- edits shared by the keys, the list and the strip

const commit = (doc: PlanDoc, sel?: number | null) => plan().edit(doc, sel === undefined ? undefined : { sel })
const selected = (): number[] => {
  const s = plan()
  return s.sel === null ? [] : [...s.more, s.sel]
}
function nudgeSelected(dir: 1 | -1, big: boolean): void {
  const s = plan()
  if (s.sel === null || !s.param) return
  let next = s.doc
  // the same setting of every selected step that has it
  for (const i of selected()) if (PARAMS[next.steps[i].action].includes(s.param)) next = nudge(next, i, s.param, dir, big)
  commit(next)
}
function addStep(action: Action): void {
  const s = plan()
  const r = insertStep(s.doc, s.sel ?? s.doc.steps.length - 1, newStep(action))
  if (r.doc !== s.doc) commit(r.doc, r.index)
}
function removeSelected(): void {
  const s = plan()
  if (s.sel === null) return
  const next = removeSteps(s.doc, selected())
  if (next !== s.doc) commit(next, Math.min(s.sel, next.steps.length - 1))
}
function moveSelected(dir: 1 | -1): void {
  const s = plan()
  if (s.sel === null) return
  const r = moveStep(s.doc, s.sel, dir)
  if (r.doc !== s.doc) commit(r.doc, r.index)
}
function duplicateSelected(): void {
  const s = plan()
  if (s.sel === null) return
  const r = duplicateStep(s.doc, s.sel)
  if (r.doc !== s.doc) commit(r.doc, r.index)
}
function cycleParam(dir: 1 | -1): void {
  const s = plan()
  const step = s.sel === null ? null : s.doc.steps[s.sel]
  if (!step) return
  const keys = PARAMS[step.action]
  if (!keys.length) return
  const i = s.param ? keys.indexOf(s.param) : -1
  s.setParam(keys[(i + dir + keys.length) % keys.length])
}
async function save(): Promise<void> {
  const s = plan()
  if (s.problems.length) return
  const id = planId(s.doc.name)
  try {
    await s.save(id)
    notify(translatePlan(useLangStore.getState().lang, 'plan.saved', { id }))
  } catch (e) {
    notifyError(e)
  }
}
function download(doc: PlanDoc): void {
  const url = URL.createObjectURL(new Blob([toCode(doc).text], { type: 'text/plain' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `${planId(doc.name)}.plan.txt`
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}
/** Open a plan of the API in the editor; the saved text (comments, alignment) wins when it still parses. */
export function openPlan(name: string, steps: readonly Record<string, unknown>[], source?: string | null): void {
  const parsed = source ? parseCode(source) : null
  plan().load(parsed?.ok ? { ...parsed.doc, name } : fromApi(name, steps))
}

let builtinCache: Promise<BuiltinPlan[]> | null = null
function useBuiltin(): BuiltinPlan[] {
  const [list, setList] = React.useState<BuiltinPlan[]>([])
  React.useEffect(() => {
    let alive = true
    builtinCache ??= api.plansBuiltin().then(
      (r) => (Array.isArray(r?.plans) ? r.plans : []),
      () => {
        builtinCache = null
        return []
      },
    )
    void builtinCache.then((p) => alive && setList(p))
    return () => {
      alive = false
    }
  }, [])
  return list
}

// ---------------------------------------------------------------- the workspace

export function PlanWorkspace() {
  const pt = usePT()
  const v = useSelectedVehicle()
  const doc = usePlan((s) => s.doc)
  const prefs = usePlan((s) => s.prefs)
  const problems = usePlan((s) => s.problems)
  const runs = usePlan((s) => s.runs)
  const active = useRun((s) => s.active)
  const volume = useSafetyVolume()
  const flight = flightOf(v, active)
  const [sheet, setSheet] = React.useState(false)
  const [wide, setWide] = React.useState(true)
  const root = React.useRef<HTMLElement | null>(null)
  const listRef = React.useRef<HTMLDivElement | null>(null)
  const table = React.useRef<TableHandle | null>(null)
  const focusCode = React.useRef<(() => void) | null>(null)
  const fileRef = React.useRef<HTMLInputElement | null>(null)
  const view: PlanViewMode = !wide && prefs.view === 'split' ? 'steps' : prefs.view
  const focusList = () => listRef.current?.focus({ preventScroll: true })
  const readOnly = problems.length > 0

  // go-to targets are placed from where the aircraft is now (rounded, so the estimate does not jitter)
  const hasGoto = doc.steps.some((s) => s.action === 'goto')
  const ox = hasGoto && v?.position ? Math.round(v.position.x * 5) / 5 : null
  const oy = hasGoto && v?.position ? Math.round(v.position.y * 5) / 5 : null
  const oz = hasGoto && v?.position ? Math.round(v.position.z * 5) / 5 : null
  const oh = hasGoto && v?.heading_deg != null ? Math.round(v.heading_deg / 5) * 5 : null
  const est = React.useMemo(
    () => estimate(doc, { origin: ox !== null && oy !== null && oz !== null ? { x: ox, y: oy, z: oz, headingDeg: oh ?? 0 } : null, speed: v?.model === 'micro' ? 1 : 0.5 }),
    [doc, ox, oy, oz, oh, v?.model],
  )

  const vehicles = useRelay((s) => s.fleet?.vehicles)
  React.useEffect(() => {
    if (!useUi.getState().selectedId && vehicles?.length) useUi.getState().select((vehicles.find((x) => x.kind === 'sim') ?? vehicles[0]).id)
  }, [vehicles])

  React.useEffect(() => {
    focusList()
    void plan().refreshSaved().catch(() => undefined)
    const el = root.current
    if (!el) return
    const ro = new ResizeObserver(() => setWide(el.clientWidth >= 1200))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  React.useEffect(() => watchRuns(), [])

  // Esc clears the step selection before it means "stop the selected aircraft"
  React.useEffect(
    () =>
      registerDismisser(() => {
        if (plan().sel === null) return false
        plan().select(null)
        return true
      }),
    [],
  )

  React.useEffect(() => {
    const own = (fn: (e: KeyboardEvent) => void) => (e: KeyboardEvent) => {
      e.stopPropagation()
      ;(document.activeElement as HTMLElement | null)?.blur?.()
      fn(e)
      if (!root.current?.contains(document.activeElement)) focusList()
    }
    const keep = (fn: (e: KeyboardEvent) => void) => (e: KeyboardEvent) => {
      e.stopPropagation()
      fn(e)
    }
    const offs = [
      registerHotkey({ combo: 'mod+enter', workspace: 'sequence', inText: true, handler: keep(() => void runPlan(0)) }),
      registerHotkey({ combo: 'mod+shift+enter', workspace: 'sequence', inText: true, handler: keep(() => void runPlan(plan().sel ?? 0)) }),
      registerHotkey({ combo: 'alt+mod+enter', workspace: 'sequence', inText: true, handler: keep(() => void runPlan(0, { unlimited: true })) }),
      registerHotkey({ combo: 'mod+s', workspace: 'sequence', inText: true, handler: keep((e) => (e.shiftKey ? download(plan().doc) : void save())) }),
      registerHotkey({ combo: 'mod+o', workspace: 'sequence', inText: true, handler: keep(() => (document.querySelector('[data-plan-menu]') as HTMLElement | null)?.click()) }),
      registerHotkey({ combo: 'alt+t', workspace: 'sequence', inText: true, handler: own(() => plan().setPrefs({ view: 'steps' })) }),
      registerHotkey({ combo: 'alt+s', workspace: 'sequence', inText: true, handler: own(() => plan().setPrefs({ view: 'split' })) }),
      registerHotkey({ combo: 'alt+c', workspace: 'sequence', inText: true, handler: keep(() => { plan().setPrefs({ view: 'code' }); window.setTimeout(() => focusCode.current?.(), 30) }) }),
      registerHotkey({
        combo: 'mod+e',
        workspace: 'sequence',
        inText: true,
        handler: keep(() => {
          if ((document.activeElement as HTMLElement | null)?.closest('.cm-editor')) focusList()
          else {
            if (plan().prefs.view === 'steps') plan().setPrefs({ view: 'split' })
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
    const s = plan()
    const key = e.key
    const done = () => e.preventDefault()
    if (mod && key.toLowerCase() === 'z') return done(), big ? s.redo() : s.undo()
    if (key === ' ') return done(), stopPlan()
    if (key === '?') return done(), setSheet(true)
    if (readOnly) return // the code has problems: the list is read-only
    if (mod && key.toLowerCase() === 'a') {
      done()
      s.select(0)
      return plan().select(s.doc.steps.length - 1, 'range')
    }
    const count = s.doc.steps.length
    if (!mod && !e.altKey && INSERT_KEYS[key.toLowerCase()] && key.length === 1) return done(), addStep(INSERT_KEYS[key.toLowerCase()])
    if (s.sel === null) {
      if (key.startsWith('Arrow') || key === 'Tab' || key === 'Home') return done(), s.select(0)
      if (key === 'End') return done(), s.select(count - 1)
      return
    }
    if (key === 'ArrowLeft' || key === 'ArrowRight') {
      done()
      const dir = key === 'ArrowRight' ? 1 : -1
      if (e.altKey) return moveSelected(dir)
      const i = s.sel + dir
      if (i >= 0 && i < count) s.select(i, big ? 'range' : 'one')
      return
    }
    if (key === 'ArrowUp' || key === 'ArrowDown') return done(), nudgeSelected(key === 'ArrowUp' ? 1 : -1, big)
    if (key === 'Tab') return done(), cycleParam(big ? -1 : 1)
    if (key === 'Home') return done(), s.select(0)
    if (key === 'End') return done(), s.select(count - 1)
    if (key === 'Delete' || key === 'Backspace') return done(), removeSelected()
    if (mod && key.toLowerCase() === 'd') return done(), duplicateSelected()
    if (mod || e.altKey) return
    if (key === 'Enter' || key === 'F2') return done(), table.current?.type()
    if (/^[0-9.+-]$/.test(key)) return done(), table.current?.type(key)
    if (key.toLowerCase() === 'n') return done(), table.current?.note()
  }

  const onImport = async (file: File) => {
    const out = parseCode(await file.text())
    if (out.ok) {
      plan().load(out.doc)
      notify(pt('plan.imported', { name: out.doc.name }))
    } else notify(pt('plan.importFailed', { line: out.problems[0].line, reason: useLangStore.getState().lang === 'zh' ? out.problems[0].zh : out.problems[0].en }), { level: 'warn' })
  }

  // how the steps of the latest run of exactly this plan ended
  const text = React.useMemo(() => toCode(doc).text, [doc])
  const last: RunRecord | null = runs.find((r) => r.text === text) ?? null
  const live = flight.ours && v?.plan ? v.plan.results : null
  const from = active?.from ?? 0
  const results: (string | null)[] = doc.steps.map((_, i) => (live ? (i >= from && i - from < live.length ? (live[i - from] ?? 'done') : null) : (last?.results[i] ?? null)))
  const marks = results.map((r) => (r === null ? null : r.startsWith('✗') ? ('bad' as const) : ('ok' as const)))
  const flyingIndex = flight.ours ? flight.index : null

  return (
    <section
      ref={root}
      className="seqws l1"
      data-plan-workspace=""
      data-own-keys=""
      onKeyDown={onKeyDown}
      aria-label={pt('plan.title')}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault()
        const f = e.dataTransfer.files?.[0]
        if (f) void onImport(f)
      }}
    >
      <DocBar doc={doc} est={est} flying={flight.running} view={view} wide={wide} onImport={() => fileRef.current?.click()} onKeys={() => setSheet(true)} />
      <input
        ref={fileRef}
        type="file"
        accept=".txt,.plan,text/plain"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0]
          e.target.value = ''
          if (f) void onImport(f)
        }}
      />
      <Band v={v} est={est} volume={volume} />
      <Transport v={v} doc={doc} est={est} flight={flight} last={last} volume={volume} />
      {readOnly && view !== 'code' ? <div className="seq-banner" data-plan-banner="">{pt('plan.codeErrors', { n: problems.length })}</div> : null}
      <div className="plan-strip" style={{ height: view === 'code' ? 64 : 150 }}>
        <PlanLanes doc={doc} est={est} flying={flyingIndex} readOnly={readOnly} marks={marks} />
      </div>
      <div className="seqws-main" data-plan-view={view}>
        {view === 'code' ? (
          <PlanCode flying={flyingIndex} focusRef={focusCode} />
        ) : (
          <>
            <StepTable ref={table} doc={doc} est={est} results={results} seconds={live ? null : (last?.seconds ?? null)} flying={flyingIndex} readOnly={readOnly} rootRef={listRef} onDone={focusList} />
            {view === 'split' ? (
              <>
                <SplitGutter />
                <div className="flex min-h-0 min-w-[320px] flex-col border-l border-[var(--border)]" style={{ width: `${Math.round(prefs.split * 100)}%` }}>
                  <PlanCode flying={flyingIndex} focusRef={focusCode} />
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
        drag.current = { x0: e.clientX, s0: plan().prefs.split, w }
        e.currentTarget.setPointerCapture(e.pointerId)
      }}
      onPointerMove={(e) => {
        const d = drag.current
        if (d) plan().setPrefs({ split: Math.min(0.7, Math.max(0.25, d.s0 - (e.clientX - d.x0) / d.w)) })
      }}
      onPointerUp={() => (drag.current = null)}
      onDoubleClick={() => plan().setPrefs({ split: 0.42 })}
    />
  )
}

// ---------------------------------------------------------------- A: document bar

/** An optional number of the plan header: empty means "the aircraft's default". */
function OptionalNumber({ value, k, onCommit, testId }: { value: number | null; k: 'speed' | 'settle'; onCommit(n: number | null): void; testId: string }) {
  const pt = usePT()
  const [text, setText] = React.useState<string | null>(null)
  const done = () => {
    if (text === null) return
    const n = Number(text)
    if (text.trim() === '') onCommit(null)
    else if (Number.isFinite(n)) onCommit(Math.min(RANGE[k][1], Math.max(RANGE[k][0], n)))
    setText(null)
  }
  return (
    <Input
      value={text ?? (value === null ? '' : String(value))}
      placeholder={pt('plan.auto')}
      inputMode="decimal"
      onChange={(e) => setText(e.target.value)}
      onBlur={done}
      onKeyDown={(e) => {
        if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
        else if (e.key === 'Escape') {
          e.stopPropagation()
          setText(null)
        }
      }}
      className="num h-6 w-14 px-1.5 font-mono text-[11px]"
      data-testid={testId}
    />
  )
}

function DocBar({ doc, est, flying, view, wide, onImport, onKeys }: { doc: PlanDoc; est: Estimate; flying: boolean; view: PlanViewMode; wide: boolean; onImport(): void; onKeys(): void }) {
  const pt = usePT()
  const past = usePlan((s) => s.past.length)
  const future = usePlan((s) => s.future.length)
  const saved = usePlan((s) => s.saved)
  const runs = usePlan((s) => s.runs)
  const dirty = usePlan(isDirty)
  const builtin = useBuiltin()
  const lang = useLang()
  const templates: [PlanKey, () => PlanDoc][] = [
    ['plan.tpl.hop', templateHop],
    ['plan.tpl.square', () => templateSquare(2, 2)],
    ['plan.tpl.outback', templateOutAndBack],
  ]
  return (
    <div className="seqws-bar" data-plan-docbar="">
      <KindSwitch />
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button variant="outline" size="sm" disabled={flying} data-plan-menu="" />}>
          <Icon icon="folder" />
          {pt('plan.menu')}
          <Icon icon="chevron.down" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="l3 max-h-[70vh] w-80 overflow-y-auto">
          <DropdownMenuGroup>
            <DropdownMenuLabel>{pt('plan.menu.saved')}</DropdownMenuLabel>
            {saved.length ? (
              saved.map((p) => (
                <DropdownMenuItem key={p.id} onClick={() => openPlan(p.name, p.steps as unknown as Record<string, unknown>[], p.source)} data-plan-saved={p.id} className="gap-2">
                  <span className="min-w-0 flex-1 truncate">{p.name}</span>
                  <span className="num text-[10px] text-[var(--g500)]">{p.id} · {p.steps.length}</span>
                  <button
                    type="button"
                    className="rounded-sm px-1 text-[var(--g500)] hover:text-[var(--g50)]"
                    title={pt('plan.delete')}
                    aria-label={`${pt('plan.delete')} ${p.name}`}
                    onClick={(ev) => {
                      ev.stopPropagation()
                      void plan().removeSaved(p.id).catch(notifyError)
                    }}
                  >
                    <Icon icon="trash" className="size-3" />
                  </button>
                </DropdownMenuItem>
              ))
            ) : (
              <p className="px-2 py-1 text-hud-sub text-[var(--g500)]">{pt('plan.menu.savedEmpty', { key: `${MOD}+S` })}</p>
            )}
          </DropdownMenuGroup>
          {builtin.length ? (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuGroup>
                <DropdownMenuLabel>{pt('plan.menu.builtin')}</DropdownMenuLabel>
                {builtin.map((p) => (
                  <DropdownMenuItem key={p.id} onClick={() => openPlan(lang === 'zh' ? (p.title_zh ?? p.title) : p.title, p.steps as unknown as Record<string, unknown>[])} data-plan-builtin={p.id} className="gap-2">
                    <span className="min-w-0 flex-1 truncate">{lang === 'zh' ? (p.title_zh ?? p.title) : p.title}</span>
                    <span className="num text-[10px] text-[var(--g500)]">{p.id}</span>
                  </DropdownMenuItem>
                ))}
              </DropdownMenuGroup>
            </>
          ) : null}
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuLabel>{pt('plan.menu.templates')}</DropdownMenuLabel>
            {templates.map(([key, make]) => (
              <DropdownMenuItem key={key} onClick={() => plan().load({ ...make(), name: pt(key) })} data-plan-template={key}>
                {pt(key)}
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
          {runs.length ? (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuGroup>
                <DropdownMenuLabel>{pt('plan.menu.runs')}</DropdownMenuLabel>
                {runs.slice(0, 6).map((r) => (
                  <DropdownMenuItem
                    key={r.at}
                    onClick={() => {
                      const out = parseCode(r.text)
                      if (out.ok) plan().load(out.doc)
                    }}
                    data-plan-run-record={r.state}
                    className="gap-2"
                  >
                    <span className="num text-[10px] text-[var(--g500)]">{new Date(r.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                    <span className="min-w-0 flex-1 truncate">{r.name}</span>
                    <span className="text-[10px] text-[var(--g500)]">{r.aircraft} · {r.state === 'done' ? '✓' : '✗'}</span>
                  </DropdownMenuItem>
                ))}
              </DropdownMenuGroup>
            </>
          ) : null}
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuLabel>{pt('plan.menu.files')}</DropdownMenuLabel>
            <DropdownMenuItem onClick={onImport} data-plan-import="">
              <Icon icon="seq.import" />
              {pt('plan.import')}
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => download(doc)} data-plan-export="">
              <Icon icon="seq.export" />
              {pt('plan.export')}
            </DropdownMenuItem>
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
      <Input
        value={doc.name}
        onChange={(e) => plan().edit({ ...doc, name: e.target.value.slice(0, 80) }, { transient: true })}
        onBlur={() => plan().edit(plan().doc)}
        placeholder={pt('plan.name')}
        aria-label={pt('plan.name')}
        className="h-6 w-48 px-1.5 text-[12px]"
        data-plan-name=""
      />
      <button type="button" className="seq-dirty" data-dirty={dirty ? '' : undefined} title={dirty ? `${pt('plan.unsaved')} · ${pt('plan.save')} ${MOD}+S` : `${pt('plan.save')} ${MOD}+S`} onClick={() => void save()} aria-label={pt('plan.save')} data-plan-save="">
        <Icon icon="save" className="size-3.5" />
      </button>
      <label className="seq-field" title={pt('plan.speed.hint')}>
        {pt('plan.speed')}
        <OptionalNumber value={doc.speed} k="speed" onCommit={(n) => commit({ ...doc, speed: n })} testId="plan-speed" />
        <span>m/s</span>
      </label>
      <label className="seq-field" title={pt('plan.settle.hint')}>
        {pt('plan.settle')}
        <OptionalNumber value={doc.settle} k="settle" onCommit={(n) => commit({ ...doc, settle: n })} testId="plan-settle" />
        <span>s</span>
      </label>
      <span className="num truncate text-hud-sub text-[var(--g500)]" data-plan-total="">
        {pt('plan.total', { n: doc.steps.length, t: fmtT(est.seconds) })}
      </span>
      <span className="flex-1" />
      <Button variant="ghost" size="icon-sm" disabled={!past} onClick={() => plan().undo()} title={`${pt('plan.undo')} ${MOD}+Z`} aria-label={pt('plan.undo')}>
        <Icon icon="seq.undo" />
      </Button>
      <Button variant="ghost" size="icon-sm" disabled={!future} onClick={() => plan().redo()} title={`${pt('plan.redo')} ${MOD}+Shift+Z`} aria-label={pt('plan.redo')}>
        <Icon icon="seq.redo" />
      </Button>
      <div className="seq-seg" role="group" aria-label={pt('plan.view')}>
        {(['steps', 'split', 'code'] as const).map((id) =>
          id === 'split' && !wide ? null : (
            <button key={id} type="button" data-on={view === id ? '' : undefined} onClick={() => plan().setPrefs({ view: id })} title={`Alt+${id === 'steps' ? 'T' : id === 'split' ? 'S' : 'C'}`} data-plan-view-button={id}>
              {pt(`plan.view.${id}` as PlanKey)}
            </button>
          ),
        )}
      </div>
      <Button variant="ghost" size="icon-sm" onClick={onKeys} title={`${pt('plan.keysheet')} ?`} aria-label={pt('plan.keysheet')} data-plan-keys="">
        <Icon icon="keyboard" />
      </Button>
    </div>
  )
}

// ---------------------------------------------------------------- B + C: the stage, the path and the live values

function Band({ v, est, volume }: { v: Vehicle | null; est: Estimate; volume: Volume | null }) {
  const pt = usePT()
  const band = usePlan((s) => s.prefs.band)
  const active = useRun((s) => s.active)
  const drag = React.useRef<{ y0: number; h0: number } | null>(null)
  const need = volumeFor(est)
  const ok = fits(need, volume)
  // the aircraft against the start of the run, in the start heading
  let live: { f: number; l: number } | null = null
  if (v?.position && active?.origin && active.vehicle === v.id) {
    const de = v.position.x - active.origin.x
    const dn = v.position.y - active.origin.y
    const h = active.origin.heading
    live = { f: de * Math.sin(h) + dn * Math.cos(h), l: -(de * Math.cos(h) - dn * Math.sin(h)) }
  }
  return (
    <>
      {band > 0 ? (
        <div className="seqws-band" style={{ height: band }} data-plan-band="">
          <div className="relative min-w-[320px] flex-1 overflow-hidden bg-[var(--g950)]" data-plan-stage="">
            <Stage bare />
          </div>
          <aside className="seqws-monitor" data-plan-monitor="">
            <PathPreview est={est} volume={volume} live={live} />
            <div className="flex min-w-0 flex-1 flex-col">
              <div className="seq-panel" data-plan-live="">
                <span className="cap flex justify-between text-[10px] text-[var(--g400)]">
                  {pt('plan.live.title')}
                  {v ? <span className={v.armed ? 'text-[var(--r400)]' : ''}>{pt(v.armed ? 'plan.live.armed' : 'plan.live.disarmed')}</span> : null}
                </span>
                {v ? (
                  <dl className="seq-live">
                    <dt>{pt('plan.live.alt')}</dt>
                    <dd>{v.altitude_m != null ? `${v.altitude_m.toFixed(2)} m` : '—'}</dd>
                    <dt>{pt('plan.live.speed')}</dt>
                    <dd>{v.groundspeed_ms != null ? `${v.groundspeed_ms.toFixed(2)} m/s` : '—'}</dd>
                    <dt>{pt('plan.live.heading')}</dt>
                    <dd>{v.heading_deg != null ? `${v.heading_deg.toFixed(0)}°` : '—'}</dd>
                    <dt>{pt('plan.live.bat')}</dt>
                    <dd>{v.battery.voltage != null ? `${v.battery.voltage.toFixed(1)} V` : '—'}</dd>
                    <dt>{pt('plan.live.mode')}</dt>
                    <dd>{v.mode ?? '—'}</dd>
                  </dl>
                ) : (
                  <p className="text-hud-sub text-[var(--g500)]">{pt('plan.noAircraft')}</p>
                )}
              </div>
              <div className="seq-panel" data-plan-volume={ok ? 'ok' : 'small'}>
                <span className={cn('text-hud-sub', ok ? 'text-[var(--g400)]' : 'font-[560] text-[var(--g50)]')}>{pt(ok ? 'plan.volume.ok' : 'plan.volume.small', { have: vol(volume) })}</span>
                <span className="num text-hud-sub text-[var(--g500)]">{pt('plan.volume.need', { need: vol(need) })}</span>
              </div>
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
          plan().setPrefs({ band: Math.round(Math.min(max, Math.max(160, d.h0 + e.clientY - d.y0))) })
        }}
        onPointerUp={() => (drag.current = null)}
        onDoubleClick={() => plan().setPrefs({ band: 260 })}
      >
        <button type="button" onClick={() => plan().setPrefs({ band: band > 0 ? 0 : 260 })} title={pt(band > 0 ? 'plan.band.fold' : 'plan.band.open')} aria-label={pt(band > 0 ? 'plan.band.fold' : 'plan.band.open')} data-plan-band-toggle="">
          <Icon icon={band > 0 ? 'chevron.up' : 'chevron.down'} className="size-3" />
        </button>
      </div>
    </>
  )
}

// ---------------------------------------------------------------- D: transport

function Transport({ v, doc, est, flight, last, volume }: { v: Vehicle | null; doc: PlanDoc; est: Estimate; flight: PlanFlight; last: RunRecord | null; volume: Volume | null }) {
  const pt = usePT()
  const vehicles = useRelay((s) => s.fleet?.vehicles) ?? []
  const sel = usePlan((s) => s.sel)
  const prefs = usePlan((s) => s.prefs)
  const problems = usePlan((s) => s.problems.length)
  const runs = usePlan((s) => s.runs)
  const { busy, ask, error, indexAt } = useRun()
  const [word, setWord] = React.useState('')
  const [now, setNow] = React.useState(0)
  const human = v?.controller === 'human'
  const stuck = blocked(v)
  React.useEffect(() => {
    if (!flight.running) return
    const id = window.setInterval(() => setNow(Date.now()), 250)
    return () => window.clearInterval(id)
  }, [flight.running])
  const [askFor, setAskFor] = React.useState(v?.id)
  if (askFor !== v?.id) {
    setAskFor(v?.id)
    setWord('')
  }
  React.useEffect(() => useRun.getState().setAsk(null), [v?.id])

  let status: string | null = null
  let statusKind = ''
  if (flight.running && flight.ours && flight.index !== null) {
    const step = doc.steps[flight.index]
    status = pt('plan.state.step', { n: flight.index + 1, total: doc.steps.length, what: step ? describe(step) : '', t: fmtT(Math.max(0, (now || indexAt) - indexAt) / 1000) })
    statusKind = 'running'
  } else if (flight.running) {
    status = pt('plan.state.other', { name: flight.name ?? '' })
    statusKind = 'other'
  } else if (last) {
    const flown = last.seconds.reduce<number>((a, b) => a + (b ?? 0), 0)
    const at = last.results.findIndex((r) => r?.startsWith('✗'))
    const stopped = at >= 0 ? at : Math.max(0, last.results.findIndex((r) => r === null))
    status = last.state === 'done' ? pt('plan.state.done', { t: fmtT(flown) }) : last.state === 'failed' ? pt('plan.state.failed', { n: stopped + 1, detail: last.detail ?? '' }) : pt('plan.state.cancelled', { n: stopped + 1 })
    statusKind = last.state
  }

  const picker = (
    <NativeSelect size="sm" value={v?.id ?? ''} onChange={(e) => useUi.getState().select(e.target.value || null)} className="h-6 w-[148px] text-[11px]" aria-label={pt('plan.target')} data-plan-target="">
      {v ? null : <NativeSelectOption value="">{pt('plan.target')}</NativeSelectOption>}
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
      <div className="seqws-transport" data-plan-transport="empty">
        <span className="text-hud-sub text-[var(--g400)]">{pt('plan.noFleet')}</span>
        <Button variant="outline" size="sm" onClick={addSim} data-plan-add-sim="">
          <Icon icon="plus" />
          {pt('plan.addSim')}
        </Button>
      </div>
    )
  }

  if (ask && v) {
    const unlimited = Boolean(ask.unlimited)
    const go = () => void runPlan(ask.from, ask.kind === 'confirm' ? { confirm: 'ARM', takeover: true, volume: true, unlimited } : ask.kind === 'volume' ? { volume: true, takeover: true } : { takeover: true, unlimited })
    return (
      <div className="seqws-transport is-ask" data-plan-transport="ask" data-plan-ask={ask.kind}>
        {picker}
        {ask.kind === 'confirm' ? (
          <>
            <span className="text-hud-sub text-[var(--g100)]">{pt('plan.confirmInline', { aircraft: v.label })}</span>
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
              data-plan-arm=""
            />
            <Button variant="outline" size="sm" disabled={busy || word.trim().toUpperCase() !== 'ARM'} onClick={() => { setWord(''); go() }}>
              <Icon icon="tl.play" />
              {pt('plan.run')}
            </Button>
          </>
        ) : (
          <>
            <span className="text-hud-sub text-[var(--g100)]">
              {ask.kind === 'volume' ? pt('plan.volume.ask', { need: vol(ask.need ?? null), have: vol(volume) }) : pt(ask.kind === 'takeover' ? 'plan.takeover' : 'plan.possessReal', { aircraft: v.label })}
            </span>
            <Button variant="outline" size="sm" disabled={busy} onClick={go} autoFocus data-plan-ask-go="">
              {pt(ask.kind === 'volume' ? 'plan.volume.go' : ask.kind === 'takeover' ? 'plan.takeoverGo' : 'plan.possess')}
            </Button>
            {ask.kind === 'volume' ? (
              <Button variant="ghost" size="sm" disabled={busy} onClick={() => void runPlan(ask.from, { takeover: true, unlimited: true })} title={pt('plan.unlimited.hint')} data-plan-ask-unlimited="">
                {pt('plan.unlimited')}
              </Button>
            ) : null}
          </>
        )}
        <Button variant="ghost" size="sm" onClick={() => useRun.getState().setAsk(null)}>
          {pt('plan.cancel')}
        </Button>
        {error ? <span className="truncate text-hud-sub font-[560] text-[var(--g50)]">{error}</span> : null}
      </div>
    )
  }

  const cantRun = !v || busy || problems > 0 || Boolean(stuck)
  const leg = flight.ours && flight.index !== null ? est.legs[flight.index] : null
  const progress = leg ? ((leg.t0 + Math.min(leg.seconds, Math.max(0, (now || indexAt) - indexAt) / 1000)) / Math.max(1, est.seconds)) * 100 : null
  const prev = runs[0]
  return (
    <div className="seqws-transport" data-plan-transport={flight.running ? 'flying' : 'idle'}>
      {picker}
      {v ? <ControllerPill vehicle={v} className="shrink-0" /> : null}
      {v && !human && v.kind === 'real' && v.controller !== 'agent' ? (
        <Button variant="ghost" size="sm" onClick={() => possess(v.id)} title="P" data-plan-possess="">
          <Icon icon="possess" />
          {pt('plan.possess')}
        </Button>
      ) : null}
      {flight.running ? (
        <Button variant="outline" size="sm" disabled={!human} onClick={stopPlan} title={pt('plan.stop.hint')} data-plan-stop="">
          <Icon icon="stop" />
          {pt('plan.stop')}
          <kbd className="seq-kbd">Space</kbd>
        </Button>
      ) : (
        <>
          <Button variant="outline" size="sm" disabled={cantRun} onClick={() => void runPlan(0)} title={problems ? pt('plan.fixFirst') : undefined} data-plan-run="">
            <Icon icon="tl.play" />
            {pt('plan.run')}
            <kbd className="seq-kbd">{RUN_KEY}</kbd>
          </Button>
          <Button variant="ghost" size="sm" disabled={cantRun} onClick={() => void runPlan(0, { unlimited: true })} title={`${pt('plan.unlimited.hint')} · ${MOD}+Alt+↵`} data-plan-run-unlimited="">
            <Icon icon="tl.play" />
            {pt('plan.unlimited')}
          </Button>
          <Button variant="ghost" size="sm" disabled={cantRun || sel === null || sel === 0} onClick={() => void runPlan(sel ?? 0)} title={`${MOD}+Shift+↵ · ${pt('plan.k.runFrom')}`} data-plan-run-from="">
            {pt('plan.runFrom', { n: (sel ?? 0) + 1 })}
          </Button>
        </>
      )}
      {v?.kind === 'sim' ? (
        <button type="button" className="seq-toggle" data-on={prefs.reset ? '' : undefined} onClick={() => plan().setPrefs({ reset: !prefs.reset })} title={pt('plan.reset.hint')} data-plan-reset={prefs.reset ? 'on' : 'off'}>
          <Icon icon="reset" className="size-3" />
          {pt('plan.reset')}
        </button>
      ) : null}
      {status ? (
        <span className="num truncate text-hud-sub text-[var(--g200)]" data-plan-status={statusKind}>
          {status}
        </span>
      ) : null}
      {flight.running && v?.plan?.unlimited ? <span className="seq-chip" title={pt('plan.unlimited.hint')} data-plan-unlimited-chip="">{pt('plan.unlimited.chip')}</span> : null}
      {stuck ? <span className="truncate text-hud-sub text-[var(--g400)]">{pt('plan.noSkills')}</span> : null}
      <span className="flex-1" />
      {prev && !last && !flight.running ? (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            const out = parseCode(prev.text)
            if (out.ok) {
              plan().load(out.doc)
              notify(pt('plan.restored'))
            }
          }}
          data-plan-restore=""
        >
          <Icon icon="seq.undo" />
          {pt('plan.restore')}
        </Button>
      ) : null}
      {progress !== null ? <i className="seq-progress" style={{ width: `${Math.min(100, progress)}%` }} /> : null}
    </div>
  )
}

// ---------------------------------------------------------------- E: the step list

interface TableHandle {
  type(first?: string): void
  note(): void
}

const StepTable = React.forwardRef<TableHandle, { doc: PlanDoc; est: Estimate; results: (string | null)[]; seconds: (number | null)[] | null; flying: number | null; readOnly: boolean; rootRef: React.RefObject<HTMLDivElement | null>; onDone(): void }>(function StepTable(
  { doc, est, results, seconds, flying, readOnly, rootRef, onDone },
  ref,
) {
  const pt = usePT()
  const sel = usePlan((s) => s.sel)
  const more = usePlan((s) => s.more)
  const param = usePlan((s) => s.param)
  const [draft, setDraft] = React.useState<{ id: string; text: string } | null>(null)
  const cell = (id: string) => rootRef.current?.querySelector(`[data-plan-cell="${id}"]`) as HTMLInputElement | null
  React.useImperativeHandle(ref, () => ({
    type(first) {
      const s = plan()
      if (s.sel === null || !s.param) return
      const id = `${s.sel}:${s.param}`
      // keys that arrive before the field has the focus are appended, not lost
      if (first !== undefined) setDraft((d) => (d?.id === id ? { id, text: d.text + first } : { id, text: first }))
      const el = cell(id)
      el?.focus()
      if (first === undefined) window.setTimeout(() => el?.select(), 0)
    },
    note() {
      const s = plan()
      if (s.sel === null) return
      const el = rootRef.current?.querySelector(`[data-plan-note="${s.sel}"]`) as HTMLInputElement | null
      el?.focus()
      el?.select()
    },
  }))
  // keep the selected row in view
  React.useEffect(() => {
    if (sel === null) return
    rootRef.current?.querySelector(`[data-plan-row="${sel}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [sel, rootRef])

  const commitCell = (index: number, key: ParamKey, text: string) => {
    const n = Number(text.replace('−', '-'))
    if (text.trim() === '') commit(setParam(plan().doc, index, key, null))
    else if (Number.isFinite(n)) commit(setParam(plan().doc, index, key, n))
  }
  return (
    <div ref={rootRef} className={cn('plan-table', readOnly && 'is-readonly')} tabIndex={0} data-plan-steps="" aria-label={pt('plan.col.step')}>
      <div className="plan-row plan-head-row">
        <span>#</span>
        <span>{pt('plan.col.step')}</span>
        <span>{pt('plan.col.settings')}</span>
        <span className="text-right">{pt('plan.col.time')}</span>
        <span>{pt('plan.col.result')}</span>
        <span>{pt('plan.col.note')}</span>
      </div>
      {doc.steps.map((s, i) => {
        const on = sel === i
        const leg = est.legs[i]
        const took = seconds?.[i]
        return (
          <div
            key={i}
            className="plan-row"
            data-on={on ? '' : undefined}
            data-more={more.includes(i) ? '' : undefined}
            data-flying={flying === i ? '' : undefined}
            data-flown={flying !== null && i < flying ? '' : undefined}
            data-plan-row={i}
            onMouseDown={(e) => {
              if ((e.target as HTMLElement).closest('input,select,button')) {
                if (plan().sel !== i) plan().select(i)
                return
              }
              plan().select(i, e.shiftKey ? 'range' : e.metaKey || e.ctrlKey ? 'toggle' : 'one')
            }}
          >
            <span className="num text-[var(--g500)]">{i + 1}</span>
            <NativeSelect size="sm" value={s.action} disabled={readOnly} onChange={(e) => commit(setAction(plan().doc, i, e.target.value as Action), i)} className="h-6 w-[92px] text-[11px]" aria-label={pt('plan.col.step')} data-plan-action={i}>
              {ACTIONS.map((a) => (
                <NativeSelectOption key={a} value={a}>
                  {pt(`plan.action.${a}` as PlanKey)}
                </NativeSelectOption>
              ))}
            </NativeSelect>
            <span className="plan-cells">
              {PARAMS[s.action].map((k) => {
                const id = `${i}:${k}`
                const value = s.p[k]
                const fallback = k === 'speed' ? (doc.speed ?? null) : k === 'settle' ? (doc.settle ?? null) : null
                return (
                  <label key={k} className="plan-cell" data-on={on && param === k ? '' : undefined} data-set={value !== undefined ? '' : undefined}>
                    {pt(`plan.p.${k}` as PlanKey)}
                    <Input
                      value={draft?.id === id ? draft.text : value === undefined ? '' : String(value)}
                      placeholder={k === 'speed' || k === 'settle' ? (fallback === null ? pt('plan.auto') : String(fallback)) : '0'}
                      inputMode="decimal"
                      disabled={readOnly}
                      onFocus={() => {
                        if (plan().sel !== i) plan().select(i)
                        plan().setParam(k)
                      }}
                      onChange={(e) => setDraft({ id, text: e.target.value })}
                      onBlur={() => {
                        if (draft?.id === id) commitCell(i, k, draft.text)
                        setDraft(null)
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault()
                          ;(e.target as HTMLInputElement).blur()
                          onDone()
                        } else if (e.key === 'Escape') {
                          e.stopPropagation()
                          setDraft(null)
                          onDone()
                        } else if ((e.key === 'ArrowUp' || e.key === 'ArrowDown') && draft?.id !== id) {
                          e.preventDefault()
                          commit(nudge(plan().doc, i, k, e.key === 'ArrowUp' ? 1 : -1, e.shiftKey))
                        }
                      }}
                      title={`${RANGE[k][0]} … ${RANGE[k][1]} ${RANGE[k][4]}`}
                      className="num h-6 w-[58px] px-1.5 font-mono text-[11px]"
                      data-plan-cell={id}
                    />
                    <i>{RANGE[k][4]}</i>
                  </label>
                )
              })}
            </span>
            <span className="num text-right text-[var(--g400)]" title={took != null ? fmtT(took) : undefined}>
              {took != null ? fmtT(took) : leg ? `~${fmtT(leg.seconds)}` : ''}
            </span>
            <span className="plan-result" data-bad={results[i]?.startsWith('✗') ? '' : undefined} data-plan-result={i} title={results[i] ?? undefined}>
              {flying === i ? '…' : results[i] ? (results[i]!.startsWith('✗') ? results[i] : `✓ ${results[i] === 'done' ? '' : results[i]}`) : ''}
            </span>
            <Input
              defaultValue={s.note ?? ''}
              key={`${i}:${s.note ?? ''}`}
              disabled={readOnly}
              placeholder="—"
              onBlur={(e) => {
                if ((e.target.value.trim() || undefined) !== s.note) commit(setNote(plan().doc, i, e.target.value))
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  ;(e.target as HTMLInputElement).blur()
                  onDone()
                } else if (e.key === 'Escape') {
                  e.stopPropagation()
                  ;(e.target as HTMLInputElement).value = s.note ?? ''
                  onDone()
                }
              }}
              aria-label={pt('plan.note')}
              className="h-6 min-w-0 px-1.5 text-[11px]"
              data-plan-note={i}
            />
          </div>
        )
      })}
      <div className="plan-add" data-plan-add="">
        <span className="cap text-[10px] text-[var(--g500)]">{pt('plan.add')}</span>
        {ACTIONS.map((a) => (
          <button key={a} type="button" className="seq-toggle" disabled={readOnly || doc.steps.length >= 24} onClick={() => addStep(a)} title={Object.entries(INSERT_KEYS).find(([, x]) => x === a)?.[0].toUpperCase()} data-plan-add-action={a}>
            {pt(`plan.action.${a}` as PlanKey)}
            <kbd className="seq-kbd">{Object.entries(INSERT_KEYS).find(([, x]) => x === a)?.[0].toUpperCase()}</kbd>
          </button>
        ))}
        <span className="flex-1" />
        <span className="truncate text-hud-sub text-[var(--g500)]" data-plan-hint="">
          {sel === null ? pt('plan.sel.none', { run: RUN_KEY }) : more.length ? pt('plan.sel.many', { n: more.length + 1 }) : pt('plan.sel.one', { n: sel + 1, what: doc.steps[sel] ? describe(doc.steps[sel]) : '' })}
        </span>
        <Button variant="ghost" size="sm" disabled={readOnly || sel === null || sel === 0} onClick={() => moveSelected(-1)} title="Alt+←" data-plan-earlier="">
          ← {pt('plan.earlier')}
        </Button>
        <Button variant="ghost" size="sm" disabled={readOnly || sel === null || sel >= doc.steps.length - 1} onClick={() => moveSelected(1)} title="Alt+→" data-plan-later="">
          {pt('plan.later')} →
        </Button>
        <Button variant="ghost" size="sm" disabled={readOnly || sel === null} onClick={duplicateSelected} title={`${MOD}+D`} data-plan-duplicate="">
          <Icon icon="duplicate" />
          {pt('plan.duplicate')}
        </Button>
        <Button variant="ghost" size="sm" disabled={readOnly || sel === null || doc.steps.length <= 1} onClick={removeSelected} title="Delete" data-plan-remove="">
          <Icon icon="trash" />
          {pt('plan.remove')}
        </Button>
      </div>
    </div>
  )
})

// ---------------------------------------------------------------- key sheet

const SHEET: [string, PlanKey][] = [
  [`${MOD}+↵`, 'plan.k.run'],
  [`${MOD}+Shift+↵`, 'plan.k.runFrom'],
  [`${MOD}+Alt+↵`, 'plan.k.unlimited'],
  ['Space', 'plan.k.stop'],
  ['Esc', 'plan.k.esc'],
  ['← →', 'plan.k.select'],
  ['↑ ↓', 'plan.k.value'],
  ['Tab · Shift+Tab', 'plan.k.param'],
  ['0–9 . −  ·  Enter', 'plan.k.type'],
  ['Alt+← →', 'plan.k.move'],
  ['T M H Y G L R', 'plan.k.insert'],
  [`${MOD}+D`, 'plan.k.duplicate'],
  ['Delete', 'plan.k.remove'],
  ['N', 'plan.k.note'],
  [`${MOD}+Z · ${MOD}+Shift+Z`, 'plan.k.undo'],
  ['Alt+T · Alt+S · Alt+C', 'plan.k.view'],
  [`${MOD}+E`, 'plan.k.focus'],
  [`${MOD}+S`, 'plan.k.save'],
  [`${MOD}+O`, 'plan.k.open'],
  ['Alt+↑ ↓', 'plan.k.nudge'],
  ['Alt+K', 'plan.k.kind'],
]

function KeySheet({ open, onOpenChange }: { open: boolean; onOpenChange(open: boolean): void }) {
  const pt = usePT()
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="l3 max-h-[calc(100vh-48px)] overflow-y-auto sm:max-w-[720px]" data-plan-keysheet="">
        <DialogHeader>
          <DialogTitle className="text-[15px] font-[560] text-[var(--g50)]">
            {pt('plan.title')} · {pt('plan.keysheet')}
          </DialogTitle>
        </DialogHeader>
        <dl className="seq-keys">
          {SHEET.map(([combo, key], i) => (
            <React.Fragment key={i}>
              <dt>{combo}</dt>
              <dd>{pt(key)}</dd>
            </React.Fragment>
          ))}
        </dl>
      </DialogContent>
    </Dialog>
  )
}
