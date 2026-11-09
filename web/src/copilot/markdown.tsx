// Markdown of the Copilot's replies (GitHub-flavoured subset): headings, paragraphs, nested and task lists, tables
// with column alignment, fenced code, quotes, rules; inline bold, italic, strikethrough, code, links and <br>.
// Everything is built as React elements (no HTML is injected); links open in the system browser and only http(s)
// and mailto are followed. The parser tolerates a reply that is still streaming: an unclosed fence is code to the
// end, a table grows row by row.
import * as React from 'react'
import { copyText, openExternal } from '@/agents/CopyButton'
import { cn } from '@/lib/utils'
import { StateIcon } from '@/ui/icons'

export type Align = 'left' | 'center' | 'right' | null

export type MdBlock =
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'paragraph'; text: string }
  | { kind: 'code'; lang: string; text: string }
  | { kind: 'list'; ordered: boolean; start: number; items: MdItem[] }
  | { kind: 'table'; align: Align[]; head: string[]; rows: string[][] }
  | { kind: 'quote'; blocks: MdBlock[] }
  | { kind: 'hr' }

export interface MdItem {
  blocks: MdBlock[]
  /** null: not a task item; else whether the box is ticked */
  task: boolean | null
}

// ---------------------------------------------------------------- blocks

const FENCE = /^ {0,3}(`{3,}|~{3,})\s*([^`\s]*)[^`]*$/
const HEADING = /^ {0,3}(#{1,6})(?:\s+(.*?))?\s*$/
const RULE = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/
const QUOTE = /^ {0,3}>\s?/
const MARKER = /^(\s*)([-*+]|\d{1,9}[.)])(\s+|$)(.*)$/
const DELIMITER = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/

const indentOf = (line: string) => {
  let n = 0
  for (const ch of line) {
    if (ch === ' ') n += 1
    else if (ch === '\t') n += 4 - (n % 4)
    else break
  }
  return n
}

/** Removes up to `n` columns of leading whitespace. */
function dedent(line: string, n: number): string {
  let col = 0
  let i = 0
  while (i < line.length && col < n) {
    if (line[i] === ' ') col += 1
    else if (line[i] === '\t') col += 4 - (col % 4)
    else break
    i += 1
  }
  return line.slice(i)
}

const blank = (line: string) => line.trim() === ''

/** Splits a table row on unescaped pipes outside code spans; the outer pipes are optional. */
export function splitRow(line: string): string[] {
  let s = line.trim()
  if (s.startsWith('|')) s = s.slice(1)
  if (s.endsWith('|') && !s.endsWith('\\|')) s = s.slice(0, -1)
  const cells: string[] = []
  let cur = ''
  let tick = 0
  for (let i = 0; i < s.length; i++) {
    const ch = s[i]
    if (ch === '\\' && s[i + 1] === '|') {
      cur += '|'
      i += 1
    } else if (ch === '`') {
      let run = 1
      while (s[i + run] === '`') run += 1
      tick = tick === 0 ? run : tick === run ? 0 : tick
      cur += '`'.repeat(run)
      i += run - 1
    } else if (ch === '|' && tick === 0) {
      cells.push(cur.trim())
      cur = ''
    } else cur += ch
  }
  cells.push(cur.trim())
  return cells
}

function isTableStart(lines: string[], i: number): boolean {
  const head = lines[i]
  const delim = lines[i + 1]
  if (head === undefined || delim === undefined || !head.includes('|') || !DELIMITER.test(delim)) return false
  // a lone "---" under a line is a heading underline, not a table
  return delim.includes('|') || splitRow(head).length > 1
}

function alignOf(cell: string): Align {
  const c = cell.trim()
  const left = c.startsWith(':')
  const right = c.endsWith(':')
  return left && right ? 'center' : right ? 'right' : left ? 'left' : null
}

function startsBlock(lines: string[], i: number): boolean {
  const line = lines[i]
  return FENCE.test(line) || HEADING.test(line) || RULE.test(line) || QUOTE.test(line) || MARKER.test(line) || isTableStart(lines, i)
}

export function parseMarkdown(text: string): MdBlock[] {
  return parseBlocks(text.replace(/\r\n?/g, '\n').split('\n'))
}

function parseBlocks(lines: string[]): MdBlock[] {
  const out: MdBlock[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (blank(line)) {
      i += 1
      continue
    }
    const fence = FENCE.exec(line)
    if (fence) {
      const mark = fence[1]
      const pad = indentOf(line)
      const body: string[] = []
      i += 1
      while (i < lines.length) {
        const close = new RegExp(`^ {0,3}${mark[0] === '`' ? '`' : '~'}{${mark.length},}\\s*$`)
        if (close.test(lines[i])) {
          i += 1
          break
        }
        body.push(dedent(lines[i], pad))
        i += 1
      }
      out.push({ kind: 'code', lang: fence[2] ?? '', text: body.join('\n') })
      continue
    }
    const heading = HEADING.exec(line)
    if (heading) {
      out.push({ kind: 'heading', level: heading[1].length, text: (heading[2] ?? '').replace(/\s+#+$/, '') })
      i += 1
      continue
    }
    if (RULE.test(line)) {
      out.push({ kind: 'hr' })
      i += 1
      continue
    }
    if (isTableStart(lines, i)) {
      const head = splitRow(lines[i])
      const align = splitRow(lines[i + 1]).map(alignOf)
      const cols = Math.max(head.length, align.length)
      const fit = (row: string[]) => Array.from({ length: cols }, (_, k) => row[k] ?? '')
      const rows: string[][] = []
      i += 2
      while (i < lines.length && !blank(lines[i]) && lines[i].includes('|') && !FENCE.test(lines[i])) {
        rows.push(fit(splitRow(lines[i])))
        i += 1
      }
      out.push({ kind: 'table', align: Array.from({ length: cols }, (_, k) => align[k] ?? null), head: fit(head), rows })
      continue
    }
    if (QUOTE.test(line)) {
      const body: string[] = []
      while (i < lines.length && !blank(lines[i])) {
        body.push(QUOTE.test(lines[i]) ? lines[i].replace(QUOTE, '') : lines[i])
        i += 1
      }
      out.push({ kind: 'quote', blocks: parseBlocks(body) })
      continue
    }
    if (MARKER.test(line)) {
      i = parseList(lines, i, out)
      continue
    }
    const para: string[] = [line.trim()]
    i += 1
    while (i < lines.length && !blank(lines[i]) && !startsBlock(lines, i)) {
      para.push(lines[i].trim())
      i += 1
    }
    // "---" right under text would be a setext heading underline; replies mean a rule there, so it stays a rule
    out.push({ kind: 'paragraph', text: para.join('\n') })
  }
  return out
}

function parseList(lines: string[], from: number, out: MdBlock[]): number {
  const first = MARKER.exec(lines[from])!
  const base = indentOf(first[1])
  const ordered = /\d/.test(first[2])
  const start = ordered ? parseInt(first[2], 10) : 1
  const items: MdItem[] = []
  let i = from
  while (i < lines.length) {
    const m = MARKER.exec(lines[i])
    if (!m || indentOf(m[1]) > base + 1 || /\d/.test(m[2]) !== ordered || RULE.test(lines[i])) break
    const content = indentOf(m[1]) + m[2].length + Math.min(Math.max(m[3].length, 1), 4)
    const body: string[] = [m[4]]
    i += 1
    while (i < lines.length) {
      const line = lines[i]
      if (blank(line)) {
        let j = i + 1
        while (j < lines.length && blank(lines[j])) j += 1
        if (j < lines.length && indentOf(lines[j]) > base && !(MARKER.test(lines[j]) && indentOf(lines[j]) <= base + 1)) {
          for (; i < j; i++) body.push('')
          continue
        }
        break
      }
      const ind = indentOf(line)
      if (ind > base + 1) {
        body.push(dedent(line, Math.min(ind, content)))
        i += 1
        continue
      }
      if (MARKER.test(line) || startsBlock(lines, i) || blank(body[body.length - 1] ?? '')) break
      body.push(line.trim()) // lazy continuation of the item's paragraph
      i += 1
    }
    let task: boolean | null = null
    const box = /^\[([ xX])\]\s+/.exec(body[0])
    if (box) {
      task = box[1] !== ' '
      body[0] = body[0].slice(box[0].length)
    }
    items.push({ blocks: parseBlocks(body), task })
    // a blank line between items keeps the list going
    let j = i
    while (j < lines.length && blank(lines[j])) j += 1
    const next = j < lines.length ? MARKER.exec(lines[j]) : null
    if (j > i && next && indentOf(next[1]) <= base + 1 && /\d/.test(next[2]) === ordered) i = j
  }
  out.push({ kind: 'list', ordered, start, items })
  return i
}

// ---------------------------------------------------------------- inline

export type MdInline =
  | { kind: 'text'; text: string }
  | { kind: 'br' }
  | { kind: 'code'; text: string }
  | { kind: 'strong' | 'em' | 'del'; children: MdInline[] }
  | { kind: 'link'; href: string; children: MdInline[] }

const PUNCT = /[!"#$%&'()*+,\-./:;<=>?@[\\\]^_`{|}~]/
const WORD = /[\p{L}\p{N}]/u
const SAFE_URL = /^(https?:\/\/|mailto:)/i
const AUTOLINK = /^https?:\/\/[^\s<>]*[^\s<>.,;:!?'")\]）。，；：！？、]/

/** Closing delimiter `d` at or after `from`, with text in between that does not start or end with a space. */
function closing(s: string, d: string, from: number): number {
  let k = s.indexOf(d, from)
  while (k !== -1) {
    const inner = s.slice(from, k)
    const ok = inner.length > 0 && !/^\s/.test(inner) && !/\s$/.test(inner)
    // single * or _ must not be half of a double one
    const single = d.length === 1 && (s[k + 1] === d || s[k - 1] === d)
    if (ok && !single) return k
    k = s.indexOf(d, k + 1)
  }
  return -1
}

export function parseInline(s: string): MdInline[] {
  const out: MdInline[] = []
  let text = ''
  const flush = () => {
    if (text) out.push({ kind: 'text', text })
    text = ''
  }
  let i = 0
  while (i < s.length) {
    const ch = s[i]
    const rest = s.slice(i)
    if (ch === '\\' && i + 1 < s.length && PUNCT.test(s[i + 1])) {
      text += s[i + 1]
      i += 2
      continue
    }
    if (ch === '\n') {
      flush()
      out.push({ kind: 'br' })
      i += 1
      continue
    }
    const br = /^<br\s*\/?>/i.exec(rest)
    if (br) {
      flush()
      out.push({ kind: 'br' })
      i += br[0].length
      continue
    }
    if (ch === '`') {
      let run = 1
      while (s[i + run] === '`') run += 1
      const fence = '`'.repeat(run)
      let k = s.indexOf(fence, i + run)
      while (k !== -1 && s[k + run] === '`') k = s.indexOf(fence, k + run + 1)
      if (k !== -1) {
        flush()
        let code = s.slice(i + run, k).replace(/\n/g, ' ')
        if (code.length > 2 && code.startsWith(' ') && code.endsWith(' ') && code.trim()) code = code.slice(1, -1)
        out.push({ kind: 'code', text: code })
        i = k + run
        continue
      }
      text += fence
      i += run
      continue
    }
    if (ch === '[') {
      const link = /^\[((?:\\.|[^\]\\])+)\]\(\s*<?([^\s)>]+)>?(?:\s+"[^"]*")?\s*\)/.exec(rest)
      if (link) {
        flush()
        const children = parseInline(link[1])
        if (SAFE_URL.test(link[2])) out.push({ kind: 'link', href: link[2], children })
        else out.push(...children)
        i += link[0].length
        continue
      }
    }
    if ((ch === 'h' || ch === 'H') && (i === 0 || !WORD.test(s[i - 1]))) {
      const auto = AUTOLINK.exec(rest)
      if (auto) {
        flush()
        out.push({ kind: 'link', href: auto[0], children: [{ kind: 'text', text: auto[0] }] })
        i += auto[0].length
        continue
      }
    }
    const two = s.slice(i, i + 2)
    if (two === '**' || two === '__' || two === '~~') {
      const leftOk = two !== '__' || i === 0 || !WORD.test(s[i - 1])
      const k = leftOk ? closing(s, two, i + 2) : -1
      if (k !== -1 && (two !== '__' || k + 2 >= s.length || !WORD.test(s[k + 2]))) {
        flush()
        out.push({ kind: two === '~~' ? 'del' : 'strong', children: parseInline(s.slice(i + 2, k)) })
        i = k + 2
        continue
      }
    } else if (ch === '*' || ch === '_') {
      const leftOk = ch === '*' || i === 0 || !WORD.test(s[i - 1])
      const k = leftOk ? closing(s, ch, i + 1) : -1
      if (k !== -1 && (ch === '*' || k + 1 >= s.length || !WORD.test(s[k + 1]))) {
        flush()
        out.push({ kind: 'em', children: parseInline(s.slice(i + 1, k)) })
        i = k + 1
        continue
      }
    }
    text += ch
    i += 1
  }
  flush()
  return out
}

/** Plain text of inline content (a table copied as tab-separated values). */
export function inlineText(nodes: MdInline[]): string {
  return nodes
    .map((n) => (n.kind === 'text' || n.kind === 'code' ? n.text : n.kind === 'br' ? ' ' : 'children' in n ? inlineText(n.children) : ''))
    .join('')
}

// ---------------------------------------------------------------- rendering

function Inline({ text }: { text: string }) {
  return <>{renderInline(parseInline(text), '')}</>
}

function renderInline(nodes: MdInline[], key: string): React.ReactNode[] {
  return nodes.map((n, i) => {
    const k = `${key}${i}`
    switch (n.kind) {
      case 'text':
        return <React.Fragment key={k}>{n.text}</React.Fragment>
      case 'br':
        return <br key={k} />
      case 'code':
        return <code key={k}>{n.text}</code>
      case 'strong':
        return <strong key={k}>{renderInline(n.children, `${k}.`)}</strong>
      case 'em':
        return <em key={k}>{renderInline(n.children, `${k}.`)}</em>
      case 'del':
        return <del key={k}>{renderInline(n.children, `${k}.`)}</del>
      case 'link':
        return (
          <a
            key={k}
            href={n.href}
            title={n.href}
            onClick={(e) => {
              e.preventDefault()
              openExternal(n.href)
            }}
          >
            {renderInline(n.children, `${k}.`)}
          </a>
        )
    }
  })
}

/** A small copy control: the copy icon morphs into a check for 1.6 s. */
export function CopyIcon({ text, label, className }: { text: string | (() => string); label: string; className?: string }) {
  const [done, setDone] = React.useState(false)
  React.useEffect(() => {
    if (!done) return
    const tm = setTimeout(() => setDone(false), 1600)
    return () => clearTimeout(tm)
  }, [done])
  return (
    <button
      type="button"
      className={cn('cp-copy', done && 'text-[var(--g50)]', className)}
      title={label}
      aria-label={label}
      onClick={() => void copyText(typeof text === 'function' ? text() : text).then((ok) => ok && setDone(true))}
      data-copy=""
    >
      <StateIcon icon="copy" alt={done} className="size-3" />
    </button>
  )
}

function CodeBlock({ block, copyLabel }: { block: Extract<MdBlock, { kind: 'code' }>; copyLabel: string }) {
  return (
    <div className="cp-md-code">
      <div className="cp-md-code-head">
        <span>{block.lang}</span>
        <CopyIcon text={block.text} label={copyLabel} />
      </div>
      <pre>
        <code>{block.text}</code>
      </pre>
    </div>
  )
}

function Table({ block, copyLabel }: { block: Extract<MdBlock, { kind: 'table' }>; copyLabel: string }) {
  const style = (c: number): React.CSSProperties | undefined => (block.align[c] ? { textAlign: block.align[c]! } : undefined)
  const tsv = () => [block.head, ...block.rows].map((r) => r.map((c) => inlineText(parseInline(c))).join('\t')).join('\n')
  return (
    <div className="cp-md-table" data-md-table="">
      <div className="cp-md-table-scroll">
        <table>
          <thead>
            <tr>
              {block.head.map((h, c) => (
                <th key={c} style={style(c)}>
                  <Inline text={h} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => (
                  <td key={c} style={style(c)}>
                    <Inline text={cell} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <CopyIcon text={tsv} label={copyLabel} className="cp-md-table-copy" />
    </div>
  )
}

function Blocks({ blocks, copyLabel }: { blocks: MdBlock[]; copyLabel: string }) {
  return (
    <>
      {blocks.map((b, i) => {
        switch (b.kind) {
          case 'heading': {
            const H = `h${Math.min(6, Math.max(1, b.level))}` as 'h1'
            return (
              <H key={i} className={cn('cp-md-h', b.level <= 2 && 'cp-md-h-major')}>
                <Inline text={b.text} />
              </H>
            )
          }
          case 'paragraph':
            return (
              <p key={i}>
                <Inline text={b.text} />
              </p>
            )
          case 'code':
            return <CodeBlock key={i} block={b} copyLabel={copyLabel} />
          case 'table':
            return <Table key={i} block={b} copyLabel={copyLabel} />
          case 'quote':
            return (
              <blockquote key={i}>
                <Blocks blocks={b.blocks} copyLabel={copyLabel} />
              </blockquote>
            )
          case 'hr':
            return <hr key={i} />
          case 'list': {
            const L = b.ordered ? 'ol' : 'ul'
            return (
              <L key={i} start={b.ordered && b.start !== 1 ? b.start : undefined} className={cn(b.items.some((it) => it.task !== null) && 'cp-md-tasks')}>
                {b.items.map((it, j) => (
                  <li key={j} data-task={it.task === null ? undefined : it.task ? 'done' : 'open'}>
                    {it.task !== null ? <span className="cp-md-box" aria-hidden="true" /> : null}
                    <Blocks blocks={it.blocks} copyLabel={copyLabel} />
                  </li>
                ))}
              </L>
            )
          }
        }
      })}
    </>
  )
}

/** The rendered reply; `copyLabel` names the copy buttons of code blocks and tables. */
export function Markdown({ text, copyLabel, className }: { text: string; copyLabel: string; className?: string }) {
  const blocks = React.useMemo(() => parseMarkdown(text), [text])
  return (
    <div className={cn('cp-md', className)}>
      <Blocks blocks={blocks} copyLabel={copyLabel} />
    </div>
  )
}
