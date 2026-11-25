// The code view of a flight plan (14 §3) in the Lab's CodeMirror set-up. One document sits behind the step list
// and this text: typing here replaces the document on every valid parse, an edit in the list rewrites only the
// lines that changed. The caret selects the step (and the value) it is on; the step in flight is marked. A text
// with problems never becomes the plan and never flies.
import * as React from 'react'
import { Annotation, EditorState, StateEffect, StateField, type Extension, type Range } from '@codemirror/state'
import { Decoration, EditorView, drawSelection, highlightActiveLine, highlightActiveLineGutter, keymap, lineNumbers, type DecorationSet } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { StreamLanguage } from '@codemirror/language'
import { highlightSelectionMatches, search, searchKeymap } from '@codemirror/search'
import { lintGutter, setDiagnostics } from '@codemirror/lint'
import { useLang } from '@/i18n'
import { labTheme } from '@/lab/editorTheme'
import { Button } from '@/ui/components/ui/button'
import { nudgeNumber, parseCode, sameDoc, toCode, type CodeLayout, type CodeProblem } from './code'
import type { PlanDoc } from './model'
import { usePlan } from './store'
import { usePT } from './strings'

const external = Annotation.define<boolean>()
const setMarks = StateEffect.define<DecorationSet>()
const marks = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(value, tr) {
    value = value.map(tr.changes)
    for (const e of tr.effects) if (e.is(setMarks)) value = e.value
    return value
  },
  provide: (f) => EditorView.decorations.from(f),
})
const selLine = Decoration.line({ class: 'cm-plan-sel' })
const cellMark = Decoration.mark({ class: 'cm-plan-cell' })
const playLine = Decoration.line({ class: 'cm-plan-play' })
const flownLine = Decoration.line({ class: 'cm-plan-flown' })

const language = StreamLanguage.define<{ first: boolean }>({
  startState: () => ({ first: true }),
  token(stream, state) {
    if (stream.sol()) state.first = true
    if (stream.eatSpace()) return null
    if (stream.peek() === '#') {
      stream.skipToEnd()
      return 'comment'
    }
    if (state.first) {
      state.first = false
      if (stream.match(/^(plan|name)\b/)) {
        stream.skipToEnd()
        return 'keyword'
      }
      if (stream.match(/^[A-Za-z_]+/)) return 'keyword'
    }
    if (stream.match(/^[+-]?(?:\d+\.?\d*|\.\d+)/)) return 'number'
    if (stream.match(/^[A-Za-z_]+/)) return 'propertyName'
    stream.next()
    return null
  },
})

const theme = EditorView.theme({
  '.cm-plan-sel': { backgroundColor: 'oklch(from var(--g500) l c h / 0.18)' },
  '.cm-plan-cell': { backgroundColor: 'oklch(from var(--g500) l c h / 0.4)', borderRadius: '2px', outline: '1px solid var(--g300)' },
  '.cm-plan-play': { boxShadow: 'inset 2px 0 0 var(--r400)' },
  '.cm-plan-flown': { opacity: '0.5' },
  '.cm-lineNumbers .cm-gutterElement': { minWidth: '30px' },
})

function minimalChange(a: string, b: string): { from: number; to: number; insert: string } | null {
  if (a === b) return null
  let i = 0
  const n = Math.min(a.length, b.length)
  while (i < n && a[i] === b[i]) i++
  let j = 0
  while (j < n - i && a[a.length - 1 - j] === b[b.length - 1 - j]) j++
  return { from: i, to: a.length - j, insert: b.slice(i, b.length - j) }
}

function useEditor(host: React.RefObject<HTMLDivElement | null>, flying: number | null) {
  const lang = useLang()
  const view = React.useRef<EditorView | null>(null)
  const layout = React.useRef<CodeLayout>({ lines: [], cells: [] })
  const mirrored = React.useRef<PlanDoc | null>(null)
  const timers = React.useRef<{ parse: number | null; commit: number | null }>({ parse: null, commit: null })
  const langRef = React.useRef(lang)
  React.useLayoutEffect(() => {
    langRef.current = lang
  }, [lang])

  const showProblems = (v: EditorView, problems: CodeProblem[]) => {
    const doc = v.state.doc
    v.dispatch(
      setDiagnostics(
        v.state,
        problems.map((p) => {
          const line = doc.line(Math.min(Math.max(1, p.line), doc.lines))
          const from = Math.min(line.to, line.from + p.from)
          return { from, to: Math.max(from, Math.min(line.to, line.from + p.to)), severity: 'error' as const, message: langRef.current === 'zh' ? p.zh : p.en }
        }),
      ),
    )
  }

  const parseNow = React.useCallback((v: EditorView) => {
    const store = usePlan.getState()
    const out = parseCode(v.state.doc.toString())
    if (!out.ok) {
      store.setProblems(out.problems)
      showProblems(v, out.problems)
      return
    }
    layout.current = out
    showProblems(v, [])
    if (store.problems.length) store.setProblems([])
    if (sameDoc(out.doc, store.doc) && JSON.stringify(out.doc) === JSON.stringify(store.doc)) return void (mirrored.current = store.doc)
    mirrored.current = out.doc
    store.edit(out.doc, { transient: true })
    if (timers.current.commit) window.clearTimeout(timers.current.commit)
    timers.current.commit = window.setTimeout(() => usePlan.getState().edit(usePlan.getState().doc), 1000)
  }, [])

  const mirror = React.useCallback((v: EditorView, doc: PlanDoc) => {
    const out = toCode(doc)
    layout.current = out
    mirrored.current = doc
    const change = minimalChange(v.state.doc.toString(), out.text)
    if (change) v.dispatch({ changes: change, annotations: external.of(true) })
    showProblems(v, [])
  }, [])

  const format = React.useCallback(() => {
    const v = view.current
    if (!v || usePlan.getState().problems.length) return
    if (timers.current.parse) {
      window.clearTimeout(timers.current.parse)
      timers.current.parse = null
      parseNow(v)
    }
    if (!usePlan.getState().problems.length) mirror(v, usePlan.getState().doc)
  }, [mirror, parseNow])

  React.useEffect(() => {
    if (!host.current) return
    const nudge = (dir: 1 | -1, big: boolean) => (v: EditorView) => {
      const pos = v.state.selection.main.head
      const line = v.state.doc.lineAt(pos)
      const out = nudgeNumber(line.text, pos - line.from, dir, big)
      if (!out) return false
      v.dispatch({ changes: { from: line.from, to: line.to, insert: out.line }, selection: { anchor: line.from + out.col }, userEvent: 'input' })
      return true
    }
    const extensions: Extension[] = [
      lineNumbers({
        formatNumber: (n) => {
          const i = layout.current.lines.indexOf(n)
          return i < 0 ? '' : String(i + 1)
        },
      }),
      highlightActiveLineGutter(),
      history(),
      drawSelection(),
      highlightActiveLine(),
      highlightSelectionMatches(),
      search({ top: true }),
      lintGutter(),
      language,
      marks,
      keymap.of([
        { key: 'Alt-ArrowUp', run: nudge(1, false), shift: nudge(1, true) },
        { key: 'Alt-ArrowDown', run: nudge(-1, false), shift: nudge(-1, true) },
        ...defaultKeymap.filter((b) => b.key !== 'Mod-Enter' && b.key !== 'Alt-ArrowUp' && b.key !== 'Alt-ArrowDown'),
        ...historyKeymap,
        ...searchKeymap,
      ]),
      EditorView.updateListener.of((u) => {
        const own = !u.transactions.some((tr) => tr.annotation(external))
        if (u.docChanged && own) {
          if (timers.current.parse) window.clearTimeout(timers.current.parse)
          timers.current.parse = window.setTimeout(() => {
            timers.current.parse = null
            parseNow(u.view)
          }, 150)
        }
        if (u.selectionSet && u.view.hasFocus && own) {
          const store = usePlan.getState()
          if (store.problems.length) return
          const head = u.state.selection.main.head
          const line = u.state.doc.lineAt(head)
          const step = layout.current.lines.indexOf(line.number)
          if (step < 0) return
          const col = head - line.from
          const cell = layout.current.cells.find((c) => c.line === line.number && col >= c.from && col <= c.to)
          if (store.sel !== step || store.more.length) store.select(step)
          if (cell && usePlan.getState().param !== cell.key) usePlan.getState().setParam(cell.key)
        }
      }),
      EditorView.domEventHandlers({
        blur: () => {
          window.setTimeout(() => {
            if (!view.current?.hasFocus) format()
          }, 0)
        },
      }),
      labTheme,
      theme,
      EditorState.tabSize.of(2),
      EditorView.contentAttributes.of({ 'aria-label': 'Plan code', spellcheck: 'false', autocapitalize: 'off' }),
    ]
    const first = toCode(usePlan.getState().doc)
    layout.current = first
    mirrored.current = usePlan.getState().doc
    const v = new EditorView({ parent: host.current, state: EditorState.create({ doc: first.text, extensions }) })
    view.current = v
    if (import.meta.env.DEV) Object.assign(window, { __relayPlanCode: v })
    const off = usePlan.subscribe((now, before) => {
      if (now.doc !== before.doc && now.doc !== mirrored.current) mirror(v, now.doc)
    })
    const t = timers.current
    return () => {
      off()
      if (t.parse) window.clearTimeout(t.parse)
      if (t.commit) {
        window.clearTimeout(t.commit)
        usePlan.getState().edit(usePlan.getState().doc)
      }
      usePlan.getState().setProblems([])
      v.destroy()
      view.current = null
    }
  }, [host, mirror, parseNow, format])

  const sel = usePlan((s) => s.sel)
  const more = usePlan((s) => s.more)
  const param = usePlan((s) => s.param)
  const doc = usePlan((s) => s.doc)
  React.useEffect(() => {
    const v = view.current
    if (!v) return
    const { lines, cells } = layout.current
    const total = v.state.doc.lines
    const ranges: Range<Decoration>[] = []
    let reveal: number | null = null
    lines.forEach((n, step) => {
      if (n > total) return
      const line = v.state.doc.line(n)
      if (flying != null && step < flying) ranges.push(flownLine.range(line.from))
      if (flying != null && step === flying) ranges.push(playLine.range(line.from))
      if (step === sel || more.includes(step)) ranges.push(selLine.range(line.from))
      if (step === sel) {
        reveal = line.from
        const c = cells.find((x) => x.step === step && x.key === param)
        if (c && line.from + c.to <= line.to && c.to > c.from) ranges.push(cellMark.range(line.from + c.from, line.from + c.to))
      }
    })
    v.dispatch({
      effects: [setMarks.of(Decoration.set(ranges, true)), ...(reveal != null && !v.hasFocus ? [EditorView.scrollIntoView(reveal, { y: 'nearest' })] : [])],
      annotations: external.of(true),
    })
  }, [sel, more, param, doc, flying])

  return { view, format }
}

export function PlanCode({ flying, focusRef }: { flying: number | null; focusRef?: React.RefObject<(() => void) | null> }) {
  const pt = usePT()
  const lang = useLang()
  const host = React.useRef<HTMLDivElement | null>(null)
  const { view, format } = useEditor(host, flying)
  const problems = usePlan((s) => s.problems)
  const steps = usePlan((s) => s.doc.steps.length)
  React.useEffect(() => {
    if (focusRef) focusRef.current = () => view.current?.focus()
  })
  const first = problems[0]
  return (
    <div className="seq-code" data-plan-code="">
      <div className="seq-code-head">
        <Button variant="ghost" size="sm" disabled={problems.length > 0} onClick={format} data-plan-format="">
          {pt('plan.code.format')}
        </Button>
        <span className="flex-1" />
        {first ? (
          <button
            type="button"
            className="truncate text-left text-hud-sub font-[560] text-[var(--g50)] underline decoration-dotted"
            onClick={() => {
              const v = view.current
              if (!v) return
              const line = v.state.doc.line(Math.min(first.line, v.state.doc.lines))
              v.dispatch({ selection: { anchor: Math.min(line.to, line.from + first.from) }, scrollIntoView: true })
              v.focus()
            }}
            data-plan-problem=""
          >
            {pt('plan.code.line', { n: first.line })} {lang === 'zh' ? first.zh : first.en}
            {problems.length > 1 ? ` (+${problems.length - 1})` : ''}
          </button>
        ) : (
          <span className="num text-hud-sub text-[var(--g500)]" title={pt('plan.code.hint')} data-plan-code-ok="">
            {pt('plan.code.ok', { n: steps })}
          </span>
        )}
      </div>
      <div ref={host} className="seq-code-host" />
    </div>
  )
}
