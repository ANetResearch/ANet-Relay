// Line diff for the History tab (06 §7): longest common subsequence over lines, emitted as kept / added / removed
// rows with old and new line numbers. Programs are at most 64 KB, so the O(n x m) table is bounded; past
// MAX_CELLS the diff falls back to "everything removed, everything added" rather than freezing the window.
export type DiffKind = 'same' | 'add' | 'del'

export interface DiffRow {
  kind: DiffKind
  text: string
  /** 1-based line in the old text (null for an added line) */
  a: number | null
  /** 1-based line in the new text (null for a removed line) */
  b: number | null
}

const MAX_CELLS = 4_000_000

export function lineDiff(oldText: string, newText: string): DiffRow[] {
  const a = oldText.split('\n')
  const b = newText.split('\n')
  // trim the common head and tail first: most revisions change a few lines
  let head = 0
  while (head < a.length && head < b.length && a[head] === b[head]) head++
  let tail = 0
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++
  const am = a.slice(head, a.length - tail)
  const bm = b.slice(head, b.length - tail)
  const rows: DiffRow[] = []
  for (let i = 0; i < head; i++) rows.push({ kind: 'same', text: a[i], a: i + 1, b: i + 1 })
  const n = am.length
  const m = bm.length
  if (n * m > MAX_CELLS) {
    am.forEach((text, i) => rows.push({ kind: 'del', text, a: head + i + 1, b: null }))
    bm.forEach((text, j) => rows.push({ kind: 'add', text, a: null, b: head + j + 1 }))
  } else {
    // lcs[i][j]: length of the LCS of am[i..] and bm[j..]
    const w = m + 1
    const lcs = new Uint32Array((n + 1) * w)
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        lcs[i * w + j] = am[i] === bm[j] ? lcs[(i + 1) * w + j + 1] + 1 : Math.max(lcs[(i + 1) * w + j], lcs[i * w + j + 1])
      }
    }
    let i = 0
    let j = 0
    while (i < n || j < m) {
      if (i < n && j < m && am[i] === bm[j]) {
        rows.push({ kind: 'same', text: am[i], a: head + i + 1, b: head + j + 1 })
        i++
        j++
      } else if (j < m && (i >= n || lcs[i * w + j + 1] >= lcs[(i + 1) * w + j])) {
        rows.push({ kind: 'add', text: bm[j], a: null, b: head + j + 1 })
        j++
      } else {
        rows.push({ kind: 'del', text: am[i], a: head + i + 1, b: null })
        i++
      }
    }
  }
  for (let k = 0; k < tail; k++) {
    const ai = a.length - tail + k
    const bi = b.length - tail + k
    rows.push({ kind: 'same', text: a[ai], a: ai + 1, b: bi + 1 })
  }
  return rows
}

export function diffStats(rows: readonly DiffRow[]): { added: number; removed: number } {
  let added = 0
  let removed = 0
  for (const r of rows) {
    if (r.kind === 'add') added++
    else if (r.kind === 'del') removed++
  }
  return { added, removed }
}

/** Rows to show: changes with `context` unchanged lines around them; gaps become a single fold marker (null). */
export function withContext(rows: readonly DiffRow[], context = 3): (DiffRow | null)[] {
  const keep = new Uint8Array(rows.length)
  rows.forEach((r, i) => {
    if (r.kind === 'same') return
    for (let k = Math.max(0, i - context); k <= Math.min(rows.length - 1, i + context); k++) keep[k] = 1
  })
  const out: (DiffRow | null)[] = []
  let gap = false
  rows.forEach((r, i) => {
    if (keep[i]) {
      out.push(r)
      gap = false
    } else if (!gap) {
      out.push(null)
      gap = true
    }
  })
  return out
}
