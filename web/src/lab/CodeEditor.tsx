// Program editor (06 §7): CodeMirror 6 with Python highlighting, line numbers, bracket matching and closing,
// indentation, folding, search (Mod-F) and a lint gutter fed by POST /programs/{id}/validate. The one sanctioned
// non-shadcn component; its theme comes entirely from tokens (./editorTheme). The view is created once per mount;
// `value` replaces the document only when `loadSeq` changes (a program was opened or a revision restored), so typing
// never fights the store.
import * as React from 'react'
import { EditorState, Compartment, type Extension } from '@codemirror/state'
import {
  EditorView, crosshairCursor, drawSelection, dropCursor, highlightActiveLine, highlightActiveLineGutter, highlightSpecialChars, keymap,
  lineNumbers, rectangularSelection,
} from '@codemirror/view'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { bracketMatching, foldGutter, foldKeymap, indentOnInput, indentUnit } from '@codemirror/language'
import { python } from '@codemirror/lang-python'
import { closeBrackets, closeBracketsKeymap } from '@codemirror/autocomplete'
import { highlightSelectionMatches, search, searchKeymap } from '@codemirror/search'
import { lintGutter, setDiagnostics, type Diagnostic } from '@codemirror/lint'
import type { ValidationProblem } from '@/api'
import { labTheme } from './editorTheme'

export interface CodeEditorProps {
  value: string
  /** changes when `value` must replace the document */
  loadSeq: number
  readOnly: boolean
  problems: readonly ValidationProblem[]
  lang: 'en' | 'zh'
  onChange(source: string): void
  onSave(): void
  onCursor?(line: number, col: number): void
  label: string
}

/** Validation problems (1-based line and column) as CodeMirror diagnostics, clamped to the document. */
export function toDiagnostics(doc: EditorState['doc'], problems: readonly ValidationProblem[], lang: 'en' | 'zh'): Diagnostic[] {
  const out: Diagnostic[] = []
  for (const p of problems) {
    if (doc.lines === 0) break
    const line = doc.line(Math.min(Math.max(1, p.line), doc.lines))
    const from = Math.min(line.to, line.from + Math.max(0, p.col - 1))
    let to = from
    if (p.end_line >= 1) {
      const endLine = doc.line(Math.min(Math.max(1, p.end_line), doc.lines))
      to = Math.min(endLine.to, endLine.from + Math.max(0, p.end_col - 1))
    }
    if (to <= from) to = Math.min(line.to, from + 1)
    if (to <= from) to = from
    out.push({ from, to, severity: 'error', message: lang === 'zh' ? p.message_zh || p.message_en : p.message_en, source: p.code })
  }
  return out
}

export function CodeEditor({ value, loadSeq, readOnly, problems, lang, onChange, onSave, onCursor, label }: CodeEditorProps) {
  const host = React.useRef<HTMLDivElement>(null)
  const view = React.useRef<EditorView | null>(null)
  const ro = React.useRef(new Compartment())
  const cb = React.useRef({ onChange, onSave, onCursor })
  React.useLayoutEffect(() => {
    cb.current = { onChange, onSave, onCursor }
  })

  // create once
  React.useEffect(() => {
    if (!host.current) return
    const extensions: Extension[] = [
      lineNumbers(),
      highlightActiveLineGutter(),
      foldGutter({ openText: '▾', closedText: '▸' }),
      lintGutter(),
      highlightSpecialChars(),
      history(),
      drawSelection(),
      dropCursor(),
      EditorState.allowMultipleSelections.of(true),
      indentOnInput(),
      indentUnit.of('    '),
      EditorState.tabSize.of(4),
      bracketMatching(),
      closeBrackets(),
      rectangularSelection(),
      crosshairCursor(),
      highlightActiveLine(),
      highlightSelectionMatches(),
      search({ top: false }),
      python(),
      labTheme,
      keymap.of([
        { key: 'Mod-s', preventDefault: true, run: () => (cb.current.onSave(), true) },
        ...closeBracketsKeymap,
        ...defaultKeymap,
        ...searchKeymap,
        ...historyKeymap,
        ...foldKeymap,
        indentWithTab,
      ]),
      ro.current.of([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)]),
      EditorView.contentAttributes.of({ 'aria-label': label, spellcheck: 'false', autocorrect: 'off', autocapitalize: 'off' }),
      EditorView.updateListener.of((u) => {
        if (u.docChanged) cb.current.onChange(u.state.doc.toString())
        if (u.selectionSet || u.docChanged) {
          const head = u.state.selection.main.head
          const line = u.state.doc.lineAt(head)
          cb.current.onCursor?.(line.number, head - line.from + 1)
        }
      }),
    ]
    const v = new EditorView({ parent: host.current, state: EditorState.create({ doc: value, extensions }) })
    view.current = v
    return () => {
      v.destroy()
      view.current = null
    }
    // the view is created once; value, readOnly and label are applied by the effects below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // take the store's text when a program is opened or a revision restored
  const seenSeq = React.useRef(loadSeq)
  React.useEffect(() => {
    const v = view.current
    if (!v || seenSeq.current === loadSeq) return
    seenSeq.current = loadSeq
    if (v.state.doc.toString() !== value) {
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value }, selection: { anchor: 0 }, scrollIntoView: true })
    }
  }, [loadSeq, value])

  React.useEffect(() => {
    const v = view.current
    if (!v) return
    v.dispatch({ effects: ro.current.reconfigure([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)]) })
    if (readOnly) v.dom.setAttribute('data-readonly', '')
    else v.dom.removeAttribute('data-readonly')
  }, [readOnly])

  React.useEffect(() => {
    const v = view.current
    if (!v) return
    v.dispatch(setDiagnostics(v.state, toDiagnostics(v.state.doc, problems, lang)))
  }, [problems, lang, loadSeq])

  return <div ref={host} className="lab-editor h-full min-h-0" data-lab-editor="" data-readonly={readOnly ? '' : undefined} />
}

/** Moves the caret to a problem and focuses the editor (status line and problem list). */
export function focusLine(root: HTMLElement | null, line: number, col: number): void {
  const el = root?.querySelector('.cm-editor')
  const v = el ? EditorView.findFromDOM(el as HTMLElement) : null
  if (!v) return
  const l = v.state.doc.line(Math.min(Math.max(1, line), v.state.doc.lines))
  const pos = Math.min(l.to, l.from + Math.max(0, col - 1))
  v.dispatch({ selection: { anchor: pos }, scrollIntoView: true })
  v.focus()
}
