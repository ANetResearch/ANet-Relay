// CodeMirror theme of the Lab editor (06 §7): dark, built only from the CSS tokens of styles/theme.css (no hex, no
// literal durations). Syntax is told apart by lightness, weight and style inside the graphite ramp; red is never
// used: a lint problem is not an armed aircraft or a critical alert (single-red rule, store/red.ts), so problems are
// marked in bold g50 with a dotted underline and a square gutter mark.
import { EditorView } from '@codemirror/view'
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { tags as t } from '@lezer/highlight'
import type { Extension } from '@codemirror/state'

const mono = 'var(--font-mono)'

export const labEditorTheme = EditorView.theme(
  {
    '&': { height: '100%', backgroundColor: 'var(--g950)', color: 'var(--g200)', fontSize: '12px' },
    '&.cm-focused': { outline: 'none' },
    '.cm-scroller': { fontFamily: mono, lineHeight: '19px', fontFeatureSettings: '"zero" 1' },
    '.cm-content': { caretColor: 'var(--g50)', padding: '8px 0' },
    '.cm-line': { padding: '0 12px 0 8px' },
    '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--g50)', borderLeftWidth: '1.5px' },
    '&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection': {
      backgroundColor: 'oklch(from var(--g500) l c h / 0.35)',
    },
    '.cm-activeLine': { backgroundColor: 'oklch(from var(--g800) l c h / 0.45)' },
    '.cm-selectionMatch': { backgroundColor: 'oklch(from var(--g600) l c h / 0.35)' },
    '&.cm-focused .cm-matchingBracket': { backgroundColor: 'oklch(from var(--g600) l c h / 0.55)', color: 'var(--g50)', outline: '1px solid var(--g500)' },
    '&.cm-focused .cm-nonmatchingBracket': { color: 'var(--g50)', textDecoration: 'underline dotted var(--g50)' },
    '.cm-gutters': { backgroundColor: 'var(--g950)', color: 'var(--g600)', border: 'none', borderRight: '1px solid var(--border)' },
    '.cm-lineNumbers .cm-gutterElement': { padding: '0 8px 0 12px', minWidth: '36px', fontVariantNumeric: 'tabular-nums' },
    '.cm-activeLineGutter': { backgroundColor: 'oklch(from var(--g800) l c h / 0.45)', color: 'var(--g200)' },
    '.cm-foldPlaceholder': { backgroundColor: 'var(--g850)', border: '1px solid var(--border)', color: 'var(--g300)' },
    '.cm-tooltip': { backgroundColor: 'var(--popover)', color: 'var(--g100)', border: '1px solid var(--border)', borderRadius: '6px', fontFamily: 'var(--font-sans)' },
    '.cm-tooltip-autocomplete > ul > li[aria-selected]': { backgroundColor: 'var(--g800)', color: 'var(--g50)' },
    // lint: bold g50 marks, never red
    '.cm-lintRange-error, .cm-lintRange-warning, .cm-lintRange-info': { backgroundImage: 'none', textDecoration: 'underline dotted var(--g50)', textUnderlineOffset: '3px', textDecorationThickness: '1.5px' },
    '.cm-lintRange-error': { color: 'var(--g50)', fontWeight: '700' },
    '.cm-lint-marker': { width: '8px', height: '8px', content: 'none' },
    '.cm-lint-marker-error, .cm-lint-marker-warning, .cm-lint-marker-info': { content: 'none', backgroundColor: 'var(--g50)', borderRadius: '1px' },
    '.cm-gutter-lint': { width: '14px' },
    '.cm-gutter-lint .cm-gutterElement': { display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '0' },
    '.cm-diagnostic': { padding: '4px 8px', borderLeft: '2px solid var(--g50)', fontFamily: 'var(--font-sans)', fontSize: '12px' },
    '.cm-diagnostic-error': { borderLeftColor: 'var(--g50)' },
    '.cm-tooltip-lint': { maxWidth: '420px' },
    // search panel
    '.cm-panels': { backgroundColor: 'var(--g900)', color: 'var(--g200)', borderColor: 'var(--border)' },
    '.cm-panels.cm-panels-bottom': { borderTop: '1px solid var(--border)' },
    '.cm-search': { fontFamily: 'var(--font-sans)', fontSize: '12px', padding: '6px 8px', display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '4px' },
    '.cm-search label': { display: 'inline-flex', alignItems: 'center', gap: '4px', color: 'var(--g400)' },
    '.cm-textfield': { backgroundColor: 'var(--g950)', color: 'var(--g100)', border: '1px solid var(--input)', borderRadius: '6px', padding: '2px 6px', fontFamily: mono, fontSize: '12px' },
    '.cm-textfield:focus': { outline: 'none', borderColor: 'var(--ring)' },
    '.cm-button': { backgroundImage: 'none', backgroundColor: 'var(--g850)', color: 'var(--g100)', border: '1px solid var(--border)', borderRadius: '6px', padding: '2px 8px', fontSize: '12px' },
    '.cm-button:active': { backgroundColor: 'var(--g800)' },
    '.cm-panel.cm-search [name=close]': { color: 'var(--g400)', fontSize: '16px', right: '6px', top: '4px' },
    '.cm-searchMatch': { backgroundColor: 'oklch(from var(--g500) l c h / 0.3)', outline: '1px solid var(--g500)' },
    '.cm-searchMatch.cm-searchMatch-selected': { backgroundColor: 'oklch(from var(--g300) l c h / 0.35)' },
    // read-only: the caret disappears and the text dims slightly
    '&[data-readonly] .cm-content': { color: 'var(--g300)' },
  },
  { dark: true },
)

export const labHighlight = HighlightStyle.define([
  { tag: [t.keyword, t.controlKeyword, t.definitionKeyword, t.moduleKeyword, t.operatorKeyword], color: 'var(--g50)', fontWeight: '650' },
  { tag: [t.function(t.definition(t.variableName)), t.function(t.variableName)], color: 'var(--g50)' },
  { tag: [t.definition(t.variableName)], color: 'var(--g100)' },
  { tag: [t.variableName, t.propertyName], color: 'var(--g200)' },
  { tag: [t.string, t.special(t.string)], color: 'var(--g300)' },
  { tag: [t.number, t.bool, t.null, t.atom], color: 'var(--g100)', fontWeight: '560' },
  { tag: [t.comment, t.lineComment, t.blockComment], color: 'var(--g500)', fontStyle: 'italic' },
  { tag: [t.operator, t.punctuation, t.bracket, t.separator], color: 'var(--g400)' },
  { tag: [t.self, t.className, t.typeName], color: 'var(--g100)', fontStyle: 'italic' },
  { tag: t.invalid, color: 'var(--g50)', textDecoration: 'underline dotted' },
])

export const labTheme: Extension = [labEditorTheme, syntaxHighlighting(labHighlight)]
