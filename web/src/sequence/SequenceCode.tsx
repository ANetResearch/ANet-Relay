// The code view of the Timeline workspace (0.6.1): the working copy as a time table (./code.ts) in the Lab's
// CodeMirror set-up. One document sits behind both views. Typing here replaces it on every valid parse, so the
// blocks move as you type; an edit on the tracks rewrites only the lines that changed, so a number counts up here
// while a block is dragged. The caret selects the block of its cell; selected blocks are marked here. A text with
// problems never becomes the document (and never flies): the tracks keep the last valid one.
import * as React from 'react'
import { Annotation, EditorState, StateEffect, StateField, type Extension } from '@codemirror/state'
import { Decoration, EditorView, drawSelection, highlightActiveLine, highlightActiveLineGutter, keymap, lineNumbers, type DecorationSet } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { StreamLanguage } from '@codemirror/language'
import { highlightSelectionMatches, search, searchKeymap } from '@codemirror/search'
import { lintGutter, setDiagnostics } from '@codemirror/lint'
import { useLang } from '@/i18n'
import { labTheme } from '@/lab/editorTheme'
import { Button } from '@/ui/components/ui/button'
import { nudgeNumber, parseCode, toCode, type CodeLayout, type CodeProblem } from './code'
import { serialiseArs, TRACKS, type ArsDoc } from './model'
import { pushLive } from './run'
import { sameSel, useSequence, type Selection } from './store'
import { useST } from './strings'

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
const cellMark = Decoration.mark({ class: 'cm-seq-sel' })
const primaryMark = Decoration.mark({ class: 'cm-seq-sel cm-seq-primary' })
const playLine = Decoration.line({ class: 'cm-seq-play' })
const flownLine = Decoration.line({ class: 'cm-seq-flown' })

/** Keywords, numbers, the ramp mark, names and comments of the time table. */
const language = StreamLanguage.define<{ head: boolean }>({
  startState: () => ({ head: true }),
  token(stream, state) {
    if (stream.sol()) state.head = true
    if (stream.eatSpace()) return null
    if (stream.peek() === '#' && /^\s*$/.test(stream.string.slice(0, stream.pos))) {
      stream.skipToEnd()
      return 'comment'
    }
    if (stream.eat('|')) {
      state.head = false
      return 'punctuation'
    }
    if (state.head && stream.match(/^(name|mode|trim|time)\b/)) return 'keyword'
    if (stream.match(/^(end|disarm|hold|stabilize|acro|althold|poshold|throttle|pitch|roll|yaw)\b/)) return 'keyword'
    if (stream.match(/^[+-]?(?:\d+\.?\d*|\.\d+)/)) return 'number'
    if (stream.eat('~')) return 'operator'
    if (!stream.match(/^(?:\\.|[^\s|~])+/)) stream.next()
    return 'string'
  },
})

const theme = EditorView.theme({
  '.cm-seq-sel': { backgroundColor: 'oklch(from var(--g500) l c h / 0.35)', borderRadius: '2px' },
  '.cm-seq-primary': { outline: '1px solid var(--g200)' },
  '.cm-seq-play': { boxShadow: 'inset 2px 0 0 var(--r400)' },
  '.cm-seq-flown': { opacity: '0.5' },
  '.cm-lineNumbers .cm-gutterElement': { minWidth: '52px' },
})

/** Replace `from` by `to` in the editor touching only what differs (keeps the caret and the scroll). */
function minimalChange(a: string, b: string): { from: number; to: number; insert: string } | null {
  if (a === b) return null
  let i = 0
  const n = Math.min(a.length, b.length)
  while (i < n && a[i] === b[i]) i++
  let j = 0
  while (j < n - i && a[a.length - 1 - j] === b[b.length - 1 - j]) j++
  return { from: i, to: a.length - j, insert: b.slice(i, b.length - j) }
}

function useCodeEditor(host: React.RefObject<HTMLDivElement | null>, playhead: number | null) {
  const lang = useLang()
  const view = React.useRef<EditorView | null>(null)
  const layout = React.useRef<CodeLayout>({ rows: [], cells: [] })
  /** the document the editor's text stands for; a store document equal to it never rewrites the text */
  const mirrored = React.useRef<ArsDoc | null>(null)
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
    const store = useSequence.getState()
    const out = parseCode(v.state.doc.toString(), store.doc)
    if (!out.ok) {
      store.setProblems(out.problems)
      showProblems(v, out.problems)
      return
    }
    layout.current = out
    showProblems(v, [])
    if (store.problems.length) store.setProblems([])
    if (serialiseArs(out.doc) === serialiseArs(store.doc)) return void (mirrored.current = store.doc)
    mirrored.current = out.doc
    store.edit(out.doc, { transient: true }) // one undo step per typing burst: the commit below ends it
    pushLive(300)
    if (timers.current.commit) window.clearTimeout(timers.current.commit)
    timers.current.commit = window.setTimeout(() => useSequence.getState().edit(useSequence.getState().doc), 1000)
  }, [])

  /** Write the store's document into the editor, formatted. */
  const mirror = React.useCallback((v: EditorView, doc: ArsDoc) => {
    const out = toCode(doc, useSequence.getState().prefs.timeStyle)
    layout.current = out
    mirrored.current = doc
    const change = minimalChange(v.state.doc.toString(), out.text)
    if (change) v.dispatch({ changes: change, annotations: external.of(true) })
    showProblems(v, [])
  }, [])

  const format = React.useCallback(() => {
    const v = view.current
    const s = useSequence.getState()
    if (!v || s.problems.length) return
    if (timers.current.parse) {
      window.clearTimeout(timers.current.parse)
      timers.current.parse = null
      parseNow(v)
    }
    if (!useSequence.getState().problems.length) mirror(v, useSequence.getState().doc)
  }, [mirror, parseNow])

  React.useEffect(() => {
    if (!host.current) return
    const nudge = (dir: 1 | -1, big: boolean) => (v: EditorView) => {
      const pos = v.state.selection.main.head
      const line = v.state.doc.lineAt(pos)
      const k = big ? 5 : 1
      const out = nudgeNumber(line.text, pos - line.from, 0.01 * k * dir, 0.1 * k * dir)
      if (!out) return false
      v.dispatch({ changes: { from: line.from, to: line.to, insert: out.line }, selection: { anchor: line.from + out.col }, userEvent: 'input' })
      return true
    }
    const cell = (dir: 1 | -1) => (v: EditorView) => {
      const pos = v.state.selection.main.head
      const text = v.state.doc.toString()
      let at: number
      if (dir > 0) {
        const pipe = text.indexOf('|', pos)
        const nl = text.indexOf('\n', pos)
        at = pipe >= 0 && (nl < 0 || pipe < nl) ? pipe + 1 : nl >= 0 ? nl + 1 : text.length
      } else {
        const before = text.lastIndexOf('|', pos - 1)
        const prev = text.lastIndexOf('|', before - 1)
        const nl = text.lastIndexOf('\n', before >= 0 ? before - 1 : pos - 1)
        at = before < 0 ? 0 : Math.max(prev, nl) + 1
      }
      while (text[at] === ' ' && text[at + 1] !== '|' && text[at + 1] !== '\n' && at < text.length) at++
      v.dispatch({ selection: { anchor: Math.min(at, text.length) }, scrollIntoView: true })
      return true
    }
    const extensions: Extension[] = [
      lineNumbers({
        formatNumber: (n) => {
          const rows = layout.current.rows
          const i = rows.findIndex((r) => r.line === n)
          if (i < 0) return ''
          if (useSequence.getState().prefs.timeStyle === 'rel') return rows[i].t.toFixed(2)
          return i > 0 ? `+${(rows[i].t - rows[i - 1].t).toFixed(2)}` : rows[i].t.toFixed(2)
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
        { key: 'Tab', run: cell(1), shift: cell(-1) },
        ...defaultKeymap.filter((b) => b.key !== 'Mod-Enter' && b.key !== 'Alt-ArrowUp' && b.key !== 'Alt-ArrowDown'),
        ...historyKeymap,
        ...searchKeymap,
      ]),
      EditorView.updateListener.of((u) => {
        if (u.docChanged && !u.transactions.some((tr) => tr.annotation(external))) {
          if (timers.current.parse) window.clearTimeout(timers.current.parse)
          timers.current.parse = window.setTimeout(() => {
            timers.current.parse = null
            parseNow(u.view)
          }, 150)
        }
        if (u.selectionSet && u.view.hasFocus && !u.transactions.some((tr) => tr.annotation(external))) {
          // the caret picks the block of its cell and puts the timeline cursor on the row
          const store = useSequence.getState()
          if (store.problems.length) return
          const head = u.state.selection.main.head
          const line = u.state.doc.lineAt(head)
          const col = head - line.from
          const row = layout.current.rows.find((r) => r.line === line.number)
          if (!row) return
          if (Math.abs(store.cursor - row.t) > 1e-6) store.setCursor(row.t)
          const c = layout.current.cells.find((x) => x.line === line.number && col >= x.from && col <= x.to)
          const next: Selection | null = c ? { track: c.track, index: c.index } : null
          if (next && (!sameSel(store.sel, next) || store.more.length)) store.select(next)
        }
      }),
      EditorView.domEventHandlers({
        blur: () => {
          // leaving the editor aligns the table (never while a number is half typed)
          window.setTimeout(() => {
            if (!view.current?.hasFocus) format()
          }, 0)
        },
      }),
      labTheme,
      theme,
      EditorState.tabSize.of(2),
      EditorView.contentAttributes.of({ 'aria-label': 'Sequence code', spellcheck: 'false', autocapitalize: 'off' }),
    ]
    const s = useSequence.getState()
    const first = toCode(s.doc, s.prefs.timeStyle)
    layout.current = first
    mirrored.current = s.doc
    const v = new EditorView({ parent: host.current, state: EditorState.create({ doc: first.text, extensions }) })
    view.current = v
    if (import.meta.env.DEV) Object.assign(window, { __relaySeqCode: v })
    // the tracks (or undo, a template, an import) changed the document: rewrite the lines that differ
    const off = useSequence.subscribe((now, before) => {
      if (now.doc !== before.doc && now.doc !== mirrored.current) mirror(v, now.doc)
      else if (now.prefs.timeStyle !== before.prefs.timeStyle && !now.problems.length) mirror(v, now.doc)
    })
    const t = timers.current
    return () => {
      off()
      if (t.parse) window.clearTimeout(t.parse)
      if (t.commit) {
        window.clearTimeout(t.commit)
        useSequence.getState().edit(useSequence.getState().doc)
      }
      useSequence.getState().setProblems([])
      v.destroy()
      view.current = null
    }
  }, [host, mirror, parseNow, format])

  // selected blocks and the row being flown, marked in the text
  const sel = useSequence((s) => s.sel)
  const more = useSequence((s) => s.more)
  const doc = useSequence((s) => s.doc)
  React.useEffect(() => {
    const v = view.current
    if (!v) return
    const { cells, rows } = layout.current
    const total = v.state.doc.lines
    const ranges = []
    let reveal: number | null = null
    if (playhead != null) {
      let cur = -1
      rows.forEach((r, i) => {
        if (r.t <= playhead + 1e-6) cur = i
      })
      rows.forEach((r, i) => {
        if (r.line > total) return
        const at = v.state.doc.line(r.line).from
        if (i < cur) ranges.push(flownLine.range(at))
        else if (i === cur) ranges.push(playLine.range(at))
      })
    }
    for (const c of cells) {
      const me = { track: c.track, index: c.index }
      const primary = sameSel(sel, me)
      if (!primary && !more.some((m) => sameSel(m, me))) continue
      if (c.line > total) continue
      const line = v.state.doc.line(c.line)
      const from = Math.min(line.to, line.from + c.from)
      const to = Math.min(line.to, line.from + c.to)
      if (to > from) ranges.push((primary ? primaryMark : cellMark).range(from, to))
      if (primary) reveal = from
    }
    v.dispatch({
      effects: [setMarks.of(Decoration.set(ranges, true)), ...(reveal != null && !v.hasFocus ? [EditorView.scrollIntoView(reveal, { y: 'nearest' })] : [])],
      annotations: external.of(true),
    })
  }, [sel, more, doc, playhead])

  return { view, format }
}

export function SequenceCode({ playhead, focusRef }: { playhead: number | null; focusRef?: React.RefObject<(() => void) | null> }) {
  const st = useST()
  const lang = useLang()
  const host = React.useRef<HTMLDivElement | null>(null)
  const { view, format } = useCodeEditor(host, playhead)
  const problems = useSequence((s) => s.problems)
  const style = useSequence((s) => s.prefs.timeStyle)
  const doc = useSequence((s) => s.doc)
  React.useEffect(() => {
    if (focusRef) focusRef.current = () => view.current?.focus()
  })
  const keys = TRACKS.reduce((n, name) => n + (doc.tracks[name]?.length ?? 0), 0)
  const first = problems[0]
  return (
    <div className="seq-code" data-seq-code="">
      <div className="seq-code-head">
        <div className="seq-seg" role="group" aria-label={st('seq.code.rel')}>
          {(['rel', 'abs'] as const).map((s) => (
            <button key={s} type="button" data-on={style === s ? '' : undefined} onClick={() => useSequence.getState().setPrefs({ timeStyle: s })} data-seq-timestyle={s}>
              {st(s === 'rel' ? 'seq.code.rel' : 'seq.code.abs')}
            </button>
          ))}
        </div>
        <Button variant="ghost" size="sm" disabled={problems.length > 0} onClick={format} data-seq-format="">
          {st('seq.code.format')}
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
            data-seq-problem=""
          >
            {lang === 'zh' ? first.zh : first.en}
            {problems.length > 1 ? ` (+${problems.length - 1})` : ''}
          </button>
        ) : (
          <span className="num text-hud-sub text-[var(--g500)]" title={st('seq.code.hint')} data-seq-code-ok="">
            {st('seq.code.ok', { rows: keysRows(doc), keys })}
          </span>
        )}
      </div>
      <div ref={host} className="seq-code-host" />
    </div>
  )
}

function keysRows(doc: ArsDoc): number {
  const set = new Set<number>()
  for (const name of TRACKS) for (const k of doc.tracks[name] ?? []) set.add(k.t)
  return set.size
}
