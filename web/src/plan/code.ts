// The code view of a flight plan (14 §3): one step per line, `action key value key value`, aligned in columns.
//
//   plan    10 m square
//   speed   2.5
//
//   takeoff  alt 10
//   move     forward 10
//   move     left 10       # along the fence
//   hover    5
//   land
//
// Header lines (all optional): plan <name>, speed <m/s>, settle <s>, frame flu|frd. Steps: takeoff alt, move
// forward / left / up (also back / right / down), goto x y z heading, hover <s>, yaw <deg>, land, rtl; takeoff,
// move and goto also take speed and settle. A lone number is the step's main value (takeoff 10, hover 5, yaw 90).
// `#` starts a comment; comment and blank lines stay where they are.
import { ACTIONS, PARAMS, RANGE, type Action, type ParamKey, type PlanDoc, type Step } from './model'

export interface CodeCell {
  line: number
  from: number
  to: number
  step: number
  key: ParamKey
}
export interface CodeLayout {
  /** 1-based line of each step */
  lines: number[]
  cells: CodeCell[]
}
export interface CodeProblem {
  line: number
  from: number
  to: number
  en: string
  zh: string
}

const num = (n: number) => String(Math.round(n * 1000) / 1000 + 0)
const BARE: Partial<Record<Action, ParamKey>> = { hover: 'seconds', yaw: 'deg' }
const ALIAS: Record<string, [ParamKey, 1 | -1]> = {
  back: ['forward', -1], right: ['left', -1], down: ['up', -1], settle_s: ['settle', 1], yaw: ['heading', 1], wait: ['seconds', 1], s: ['seconds', 1],
}
const ACTION_ALIAS: Record<string, Action> = { hold: 'hover', wait: 'hover', turn: 'yaw', return: 'rtl' }

export function toCode(doc: PlanDoc): CodeLayout & { text: string } {
  const out: string[] = [...(doc.head ?? [])]
  out.push(`plan    ${doc.name}`)
  if (doc.speed !== null) out.push(`speed   ${num(doc.speed)}`)
  if (doc.settle !== null) out.push(`settle  ${num(doc.settle)}`)
  if (doc.frame) out.push(`frame   ${doc.frame}`)
  // slots: the n-th "key value" of each step line shares a column
  const slots = doc.steps.map((s) => {
    const bare = BARE[s.action]
    return PARAMS[s.action].filter((k) => s.p[k] !== undefined).map((k) => ({ key: k, text: k === bare ? num(s.p[k]!) : `${k} ${num(s.p[k]!)}` }))
  })
  const widths: number[] = []
  for (const row of slots) row.forEach((c, i) => (widths[i] = Math.max(widths[i] ?? 0, c.text.length)))
  const head = Math.max(...doc.steps.map((s) => s.action.length), 4)
  const lines: number[] = []
  const cells: CodeCell[] = []
  doc.steps.forEach((s, index) => {
    const before = s.before ?? (index === 0 ? [''] : [])
    out.push(...before)
    let text = s.action.padEnd(head)
    slots[index].forEach((c, i) => {
      text += '  '
      const value = c.text.slice(c.text.lastIndexOf(' ') + 1)
      const from = text.length + c.text.length - value.length
      cells.push({ line: out.length + 1, from, to: from + value.length, step: index, key: c.key })
      text += i < slots[index].length - 1 || s.note ? c.text.padEnd(widths[i]) : c.text
    })
    if (s.note) text += `  # ${s.note}`
    lines.push(out.length + 1)
    out.push(text.trimEnd())
  })
  out.push(...(doc.tail ?? []))
  return { text: out.join('\n') + '\n', lines, cells }
}

export type Parsed = ({ ok: true; doc: PlanDoc } & CodeLayout) | { ok: false; problems: CodeProblem[] }

export function parseCode(text: string): Parsed {
  const problems: CodeProblem[] = []
  const doc: PlanDoc = { name: '', speed: null, settle: null, frame: null, steps: [] }
  const lines: number[] = []
  const cells: CodeCell[] = []
  let pending: string[] = []
  const rows = text.replace(/\r/g, '').split('\n')
  if (rows.length && rows[rows.length - 1] === '') rows.pop()
  rows.forEach((raw, i) => {
    const line = i + 1
    const bad = (from: number, to: number, en: string, zh: string) => problems.push({ line, from, to: Math.max(to, from + 1), en, zh })
    const hash = raw.indexOf('#')
    const body = hash >= 0 ? raw.slice(0, hash) : raw
    const note = hash >= 0 ? raw.slice(hash + 1).trim() : ''
    if (!body.trim()) {
      // a comment or blank line belongs to the step below it (the header keeps none)
      if (doc.steps.length || raw.trim()) pending.push(raw.trimEnd())
      return
    }
    const tokens = [...body.matchAll(/\S+/g)].map((m) => ({ text: m[0], from: m.index!, to: m.index! + m[0].length }))
    const word = tokens[0].text.toLowerCase()
    if (!doc.steps.length && ['plan', 'name', 'speed', 'settle', 'frame'].includes(word)) {
      const rest = body.slice(tokens[0].to).trim()
      if (word === 'plan' || word === 'name') doc.name = (rest + (hash >= 0 ? raw.slice(hash) : '')).trim().slice(0, 80)
      else if (word === 'frame') {
        if (rest === 'flu' || rest === 'frd') doc.frame = rest
        else bad(tokens[0].to, body.length, 'frame is flu or frd', 'frame 只能是 flu 或 frd')
      } else {
        const key = word as 'speed' | 'settle'
        const v = Number(rest)
        const [lo, hi] = RANGE[key]
        if (rest === '' || !Number.isFinite(v) || v < lo || v > hi) bad(tokens[0].to, body.length, `${key} is a number from ${lo} to ${hi}`, `${key} 是 ${lo} 到 ${hi} 之间的数`)
        else doc[key] = v
      }
      const kept = pending.filter((l) => l.trim())
      if (kept.length) doc.head = [...(doc.head ?? []), ...kept]
      pending = []
      return
    }
    const action = (ACTION_ALIAS[word] ?? word) as Action
    if (!ACTIONS.includes(action)) {
      bad(tokens[0].from, tokens[0].to, `Unknown step "${tokens[0].text}": use ${ACTIONS.join(', ')}`, `未知的步骤“${tokens[0].text}”，可用：${ACTIONS.join('、')}`)
      return
    }
    const allowed = PARAMS[action]
    const step: Step = { action, p: {}, ...(note ? { note: note.slice(0, 60) } : {}), ...(pending.length ? { before: pending } : {}) }
    pending = []
    const index = doc.steps.length
    const tried = new Set<ParamKey>() // a value that was given but refused is not also "missing"
    let k = 1
    while (k < tokens.length) {
      const t = tokens[k]
      const bare = Number(t.text)
      let key: ParamKey
      let sign: 1 | -1 = 1
      let valueTok = t
      if (Number.isFinite(bare) && t.text !== '') {
        // a lone number: the step's main value
        const main = BARE[action] ?? (action === 'takeoff' ? 'alt' : undefined)
        if (!main || step.p[main] !== undefined) {
          bad(t.from, t.to, `A value needs a name here: ${allowed.join(', ') || 'this step takes none'}`, `这里的数值要带名称：${allowed.join('、') || '该步骤没有参数'}`)
          k++
          continue
        }
        key = main
        k++
      } else {
        const name = t.text.toLowerCase()
        const alias = ALIAS[name]
        const resolved = (allowed as readonly string[]).includes(name) ? ([name as ParamKey, 1] as const) : alias && allowed.includes(alias[0]) ? alias : null
        if (!resolved) {
          bad(t.from, t.to, `${action} does not take "${t.text}"${allowed.length ? `: use ${allowed.join(', ')}` : ''}`, `${action} 没有参数“${t.text}”${allowed.length ? `，可用：${allowed.join('、')}` : ''}`)
          k += 2
          continue
        }
        ;[key, sign] = resolved
        valueTok = tokens[k + 1]
        if (!valueTok || !Number.isFinite(Number(valueTok.text))) {
          bad(t.from, valueTok ? valueTok.to : t.to, `${t.text} needs a number`, `${t.text} 后面要有数值`)
          k += valueTok ? 2 : 1
          continue
        }
        k += 2
      }
      tried.add(key)
      const v = Number(valueTok.text) * sign + 0
      const [lo, hi] = RANGE[key]
      if (v < lo || v > hi) {
        bad(valueTok.from, valueTok.to, `${key} must be ${lo} to ${hi}`, `${key} 须在 ${lo} 到 ${hi} 之间`)
        continue
      }
      if (step.p[key] !== undefined) {
        bad(valueTok.from, valueTok.to, `${key} is given twice`, `${key} 重复了`)
        continue
      }
      step.p[key] = v
      cells.push({ line, from: valueTok.from, to: valueTok.to, step: index, key })
    }
    const main = action === 'takeoff' ? 'alt' : action === 'hover' ? 'seconds' : action === 'yaw' ? 'deg' : null
    const missing = main && !tried.has(main) ? main : null
    if (missing) bad(tokens[0].from, body.trimEnd().length, `${action} needs ${missing}, e.g. "${action} ${action === 'takeoff' ? 'alt 1.5' : action === 'hover' ? '5' : '90'}"`, `${action} 需要 ${missing}，例如“${action} ${action === 'takeoff' ? 'alt 1.5' : action === 'hover' ? '5' : '90'}”`)
    if (action === 'goto') for (const need of ['x', 'y', 'z'] as const) if (!tried.has(need)) bad(tokens[0].from, body.trimEnd().length, `goto needs x, y and z`, `goto 需要 x、y、z`)
    if (action === 'move' && !(step.p.forward || step.p.left || step.p.up) && !(['forward', 'left', 'up'] as const).some((d) => tried.has(d) && step.p[d] === undefined)) bad(tokens[0].from, body.trimEnd().length, 'move needs a distance: forward, left or up', 'move 需要距离：forward、left 或 up')
    doc.steps.push(step)
    lines.push(line)
  })
  if (pending.length) doc.tail = pending
  if (!doc.steps.length && !problems.length) problems.push({ line: Math.max(1, rows.length), from: 0, to: 1, en: 'A plan needs at least one step, e.g. "takeoff alt 1.5"', zh: '计划至少要有一步，例如“takeoff alt 1.5”' })
  if (doc.steps.length > 24) problems.push({ line: lines[24], from: 0, to: 1, en: 'A plan has at most 24 steps', zh: '一个计划最多 24 步' })
  if (problems.length) return { ok: false, problems: problems.filter((p, i, all) => all.findIndex((q) => q.line === p.line && q.en === p.en) === i) }
  if (!doc.name) doc.name = 'Plan'
  return { ok: true, doc, lines, cells }
}

/** Change the number under the caret by `d` steps of its last shown digit; null when there is none. */
export function nudgeNumber(line: string, col: number, dir: 1 | -1, big: boolean): { line: string; col: number } | null {
  const hash = line.indexOf('#')
  for (const m of line.matchAll(/(?<![\w.])[+-]?(?:\d+\.?\d*|\.\d+)(?![\w.])/g)) {
    const from = m.index!
    const to = from + m[0].length
    if (hash >= 0 && from > hash) break
    if (col < from || col > to) continue
    const decimals = (m[0].split('.')[1] ?? '').length
    const unit = decimals ? 10 ** -decimals : 1
    const next = (Number(m[0]) + dir * unit * (big ? 10 : 1)).toFixed(decimals)
    // keep the columns: pad to the old width when the number got shorter
    const text = next.length < m[0].length && /\s/.test(line[to] ?? '') ? next.padEnd(m[0].length) : next
    return { line: line.slice(0, from) + text + line.slice(to), col: from + next.length }
  }
  return null
}

export const sameDoc = (a: PlanDoc, b: PlanDoc): boolean => toCode(a).text === toCode(b).text
