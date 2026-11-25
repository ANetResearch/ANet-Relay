// The code view of a stick timeline (0.6.1): a time table. One row per moment at which any stick changes, one
// column per track; a row is a vertical slice of the timeline and a cell is a block. Times are written as the
// interval after the previous row ("+0.5", the default: "this phase 0.3 s longer" is one number and everything
// after it moves) or as absolute seconds; both are read, row by row.
//
//   name  Takeoff, hover, land
//   mode  althold
//   trim  pitch -0.04  roll 0  yaw 0  throttle 0
//
//   time | throttle     | pitch  | roll | yaw
//   0    | 0.50 spool   |  0.00  |      |
//   +0.5 | 0.75 climb   |        |      |
//   +3.5 |              | -0.08~ |      |          ~ ramps to this track's next key
//   +15  | end disarm
//
// toCode(parseCode(text)) formats; parseCode(toCode(doc)) gives the document back (model.test.ts).
import {
  MAX_DURATION_S, MIN_BLOCK_S, SEQ_MODES, TRACKS, TRIM_LIMITS, keyTimes, range, zeroTrim,
  type ArsDoc, type ArsKey, type SeqEnd, type SeqMode, type TrackName,
} from './model'

export type TimeStyle = 'rel' | 'abs'

export interface CodeCell {
  line: number // 1-based
  from: number // 0-based columns in the line, [from, to)
  to: number
  track: TrackName
  index: number
  t: number
}
export interface CodeRow {
  line: number
  t: number
}
export interface CodeProblem {
  line: number
  from: number
  to: number
  en: string
  zh: string
}
export interface CodeLayout {
  rows: CodeRow[]
  cells: CodeCell[]
}
export type ParseResult = ({ ok: true; doc: ArsDoc } & CodeLayout) | { ok: false; problems: CodeProblem[] }

const r3 = (x: number) => Math.round(x * 1000) / 1000
const r4 = (x: number) => Math.round(x * 10000) / 10000
const TRACK_ZH: Record<TrackName, string> = { throttle: '油门', pitch: '俯仰', roll: '横滚', yaw: '偏航' }

const fmtTime = (x: number) => String(r3(x))
function fmtValue(track: TrackName, v: number): string {
  const x = r4(v)
  let s = Math.abs(x).toFixed(4).replace(/0{1,2}$/, '')
  if (track === 'throttle') return s
  s = (x > 0 ? '+' : x < 0 ? '-' : ' ') + s
  return s
}
const fmtTrim = (v: number) => (v > 0 ? `+${r4(v)}` : String(r4(v)))
const escLabel = (s: string) => s.replace(/([|#\\])/g, '\\$1')
const unescLabel = (s: string) => s.replace(/\\([|#\\])/g, '$1')

interface Comment {
  t: number
  text: string
}
function commentsOf(doc: ArsDoc): Comment[] {
  const raw = (doc.source as { comments?: unknown } | undefined)?.comments
  if (!Array.isArray(raw)) return []
  return raw.filter((c): c is Comment => !!c && typeof c.t === 'number' && typeof c.text === 'string')
}

/** The document as a formatted time table, with where every row and cell landed. */
export function toCode(doc: ArsDoc, style: TimeStyle = 'rel'): { text: string } & CodeLayout {
  const times = keyTimes(doc)
  const comments = commentsOf(doc)
  const head = [`name  ${doc.name}`, `mode  ${doc.mode}`, `trim  ${(['pitch', 'roll', 'yaw', 'throttle'] as const).map((n) => `${n} ${fmtTrim(doc.trim[n] ?? 0)}`).join('  ')}`, '']
  for (const c of comments) if (c.t < 0) head.push(`# ${c.text}`)
  // cell texts first: the widths come from them
  const grid = times.map((t) =>
    TRACKS.map((name) => {
      const keys = doc.tracks[name] ?? []
      const index = keys.findIndex((k) => Math.abs(k.t - t) < 1e-6)
      if (index < 0) return { text: '', index }
      const k = keys[index]
      return { text: `${fmtValue(name, k.v)}${k.ease === 'ramp' ? '~' : ''}${k.label ? ` ${escLabel(k.label)}` : ''}`, index }
    }),
  )
  const timeText = times.map((t, i) => (i === 0 || style === 'abs' ? fmtTime(t) : `+${fmtTime(t - times[i - 1])}`))
  const lastT = times.length ? times[times.length - 1] : 0
  const endTime = style === 'abs' || !times.length ? fmtTime(doc.duration_s) : `+${fmtTime(doc.duration_s - lastT)}`
  const wTime = Math.max(4, endTime.length, ...timeText.map((s) => s.length))
  const widths = TRACKS.map((name, c) => Math.max(name.length, ...grid.map((row) => row[c].text.length)))
  const lines = [...head, `${'time'.padEnd(wTime)} | ${TRACKS.map((n, c) => n.padEnd(widths[c])).join(' | ')}`.trimEnd()]
  const rows: CodeRow[] = []
  const cells: CodeCell[] = []
  times.forEach((t, i) => {
    for (const c of comments) if (c.t >= 0 && Math.abs(c.t - t) < 1e-6) lines.push(`# ${c.text}`)
    let line = timeText[i].padEnd(wTime)
    const lineNo = lines.length + 1
    grid[i].forEach((cell, c) => {
      line += ' | '
      if (cell.index >= 0) {
        const lead = cell.text.length - cell.text.trimStart().length
        cells.push({ line: lineNo, from: line.length + lead, to: line.length + cell.text.length, track: TRACKS[c], index: cell.index, t })
      }
      line += cell.text.padEnd(widths[c])
    })
    rows.push({ line: lineNo, t })
    lines.push(line.trimEnd())
  })
  for (const c of comments) if (c.t >= 0 && !times.some((t) => Math.abs(c.t - t) < 1e-6)) lines.push(`# ${c.text}`)
  lines.push(`${endTime.padEnd(wTime)} | end ${doc.end}`)
  rows.push({ line: lines.length, t: doc.duration_s })
  return { text: `${lines.join('\n')}\n`, rows, cells }
}

/** Split a row at its unescaped pipes: the parts with their start columns. */
function splitPipes(line: string): { text: string; at: number }[] {
  const out: { text: string; at: number }[] = []
  let start = 0
  for (let i = 0; i <= line.length; i++) {
    if (i === line.length || (line[i] === '|' && line[i - 1] !== '\\')) {
      out.push({ text: line.slice(start, i), at: start })
      start = i + 1
    }
  }
  return out
}

const NUM = /^[+-]?(?:\d+\.?\d*|\.\d+)$/

/**
 * Read a time table. `base` gives what the text does not carry (format, version, source). Every problem names its
 * row; a text with problems never becomes a document (and never flies).
 */
export function parseCode(text: string, base?: ArsDoc): ParseResult {
  const problems: CodeProblem[] = []
  const bad = (line: number, from: number, to: number, en: string, zh: string) => {
    problems.push({ line, from, to: Math.max(to, from + 1), en: `Row ${line}: ${en}`, zh: `第 ${line} 行：${zh}` })
  }
  let name = base?.name ?? ''
  let mode: SeqMode = 'althold'
  const trim = zeroTrim()
  const tracks: Record<TrackName, ArsKey[]> = { throttle: [], pitch: [], roll: [], yaw: [] }
  const rows: CodeRow[] = []
  const cells: CodeCell[] = []
  const comments: Comment[] = []
  let pending: string[] = []
  let prev: number | null = null
  let end: SeqEnd | null = null
  let duration = 0
  let endLine = 0
  const lines = text.split('\n')
  lines.forEach((raw, i) => {
    const no = i + 1
    const line = raw.replace(/\s+$/, '')
    const body = line.trim()
    const col = line.length - line.trimStart().length
    if (!body) return
    if (body.startsWith('#')) {
      pending.push(body.slice(1).trim())
      return
    }
    if (end !== null) return bad(no, col, line.length, 'nothing may follow the end row', 'end 行之后不能再有内容')
    const word = body.split(/\s+/, 1)[0]
    if (prev === null && !body.includes('|')) {
      const rest = body.slice(word.length).trim()
      if (word === 'name') name = rest.slice(0, 80)
      else if (word === 'mode') {
        if ((SEQ_MODES as readonly string[]).includes(rest)) mode = rest as SeqMode
        else bad(no, col, line.length, 'mode must be stabilize, acro, althold or poshold', '模式须为 stabilize、acro、althold 或 poshold')
      } else if (word === 'trim') {
        const parts = rest.split(/\s+/).filter(Boolean)
        for (let k = 0; k < parts.length; k += 2) {
          const axis = parts[k] as TrackName
          const v = Number(parts[k + 1])
          if (!(axis in trim) || !NUM.test(parts[k + 1] ?? '') || Math.abs(v) > TRIM_LIMITS[axis] + 1e-9) {
            bad(no, col, line.length, `trim ${parts[k]}: must be within ±${TRIM_LIMITS[axis] ?? 0.3}`, `微调 ${parts[k]}：须在 ±${TRIM_LIMITS[axis] ?? 0.3} 以内`)
          } else trim[axis] = r4(v)
        }
      } else bad(no, col, line.length, `unknown line "${word}"`, `无法识别 “${word}”`)
      return
    }
    const parts = splitPipes(line)
    if (parts[0].text.trim() === 'time') return // the column head
    const tText = parts[0].text.trim()
    const tFrom = parts[0].at + (parts[0].text.length - parts[0].text.trimStart().length)
    if (!NUM.test(tText) || tText.startsWith('-')) return bad(no, tFrom, tFrom + tText.length, 'the time must be seconds, or +seconds after the row above', '时间须为秒数，或以 + 开头表示距上一行的间隔')
    const rel = tText.startsWith('+')
    const t = r3(rel ? (prev ?? 0) + Number(tText.slice(1)) : Number(tText))
    if (prev !== null && t <= prev + 1e-9) return bad(no, tFrom, tFrom + tText.length, 'the time must be after the row above', '时间须晚于上一行')
    if (t > MAX_DURATION_S) return bad(no, tFrom, tFrom + tText.length, `a sequence is at most ${MAX_DURATION_S} s long`, `序列最长 ${MAX_DURATION_S} 秒`)
    const first = (parts[1]?.text ?? '').trim()
    if (first === 'end' || first.startsWith('end ')) {
      const what = first.slice(3).trim() || 'disarm'
      if (what !== 'disarm' && what !== 'hold') return bad(no, parts[1].at, line.length, 'the end is "end disarm" or "end hold"', '结尾须为 end disarm 或 end hold')
      end = what
      duration = t
      endLine = no
      rows.push({ line: no, t })
      return
    }
    if (parts.length > 5) bad(no, parts[5].at, line.length, 'a row has four cells: throttle, pitch, roll, yaw', '每行四格：油门、俯仰、横滚、偏航')
    for (const c of pending) comments.push({ t: prev === null && !rows.length ? (t === 0 ? 0 : t) : t, text: c })
    pending = []
    rows.push({ line: no, t })
    parts.slice(1, 5).forEach((part, c) => {
      const cell = part.text.trim()
      if (!cell) return
      const track = TRACKS[c]
      const from = part.at + (part.text.length - part.text.trimStart().length)
      const m = /^([+-]?(?:\d+\.?\d*|\.\d+))(~)?(?:\s+(.*))?$/.exec(cell)
      const [lo, hi] = range(track)
      if (!m) return bad(no, from, from + cell.length, `${track}: a cell is a number, ~ for a ramp, then a name`, `${TRACK_ZH[track]}：格内依次为杆量、表示渐变的 ~、名称`)
      const v = Number(m[1])
      if (v < lo || v > hi) return bad(no, from, from + m[1].length, `${track}: the stick must be within ${lo} to ${hi}`, `${TRACK_ZH[track]}：杆量须在 ${lo} 到 ${hi} 之间`)
      const keys = tracks[track]
      if (keys.length && t - keys[keys.length - 1].t < MIN_BLOCK_S - 1e-9) return bad(no, from, from + cell.length, `${track}: two keys need at least ${MIN_BLOCK_S} s between them`, `${TRACK_ZH[track]}：两个键之间至少间隔 ${MIN_BLOCK_S} 秒`)
      const label = m[3] ? unescLabel(m[3]).slice(0, 40) : ''
      cells.push({ line: no, from, to: from + cell.length, track, index: keys.length, t })
      keys.push({ t, v: r4(v), ease: m[2] ? 'ramp' : 'hold', ...(label ? { label } : {}) })
    })
    prev = t
  })
  const lastLine = Math.max(1, lines.length)
  if (end === null) bad(lastLine, 0, 1, 'the last row must be "<time> | end disarm" or "end hold"', '最后一行须为 “<时间> | end disarm” 或 “end hold”')
  else if (prev !== null && duration <= prev) bad(endLine, 0, 1, 'the end must be after the last key', '结尾须晚于最后一个键')
  if (!TRACKS.some((n) => tracks[n].length)) bad(lastLine, 0, 1, 'at least one cell needs a value', '至少要有一格填了杆量')
  if (problems.length) return { ok: false, problems }
  for (const c of pending) comments.push({ t: duration, text: c })
  const used: ArsDoc['tracks'] = {}
  for (const n of TRACKS) if (tracks[n].length) used[n] = tracks[n]
  const { comments: _old, ...rest } = (base?.source ?? {}) as Record<string, unknown>
  void _old
  const source = { ...rest, ...(comments.length ? { comments } : {}) }
  const doc: ArsDoc = {
    format: 'ars', version: 1, name, mode, duration_s: duration, trim, tracks: used, end: end ?? 'disarm',
    ...(Object.keys(source).length ? { source } : {}),
  }
  return { ok: true, doc, rows, cells }
}

/** Nudge the number under column `col` of a row: a stick by `dv`, a time by `dt`; null when there is none. */
export function nudgeNumber(line: string, col: number, dv: number, dt: number): { line: string; col: number } | null {
  const re = /[+-]?(?:\d+\.?\d*|\.\d+)/g
  const firstPipe = line.search(/(?<!\\)\|/)
  if (firstPipe < 0 && !/^\s*trim\b/.test(line)) return null
  let m: RegExpExecArray | null
  while ((m = re.exec(line))) {
    const from = m.index
    const to = from + m[0].length
    if (col < from || col > to) continue
    const inTime = firstPipe >= 0 && from < firstPipe
    const old = Number(m[0])
    let text: string
    if (inTime) {
      const next = Math.max(m[0].startsWith('+') ? MIN_BLOCK_S : 0, r3(old + dt))
      text = (m[0].startsWith('+') ? '+' : '') + String(next)
    } else {
      const next = r4(old + dv)
      const signed = /^[+-]/.test(m[0]) || next < 0
      text = (signed ? (next > 0 ? '+' : next < 0 ? '-' : ' ') : '') + Math.abs(next).toFixed(Math.max(2, (m[0].split('.')[1] ?? '').length))
      if (text.startsWith(' ')) text = text.slice(1)
    }
    return { line: line.slice(0, from) + text + line.slice(to), col: from + text.length }
  }
  return null
}
