// Timeline workspace state (0.6.1): the document being edited (kept per viewer), the selected blocks, the cursor,
// undo, the view preferences and the library of saved sequences.
import { create } from 'zustand'
import type { CodeProblem, TimeStyle } from './code'
import { parseArs, serialiseArs, templateHover, type ArsDoc, type TrackName } from './model'

const KEY = 'relay.sequence.doc'
const LIB_KEY = 'relay.sequence.library'
const VIEW_KEY = 'relay.sequence.view'
const UNDO_LIMIT = 80
const LIB_LIMIT = 50

export interface Selection {
  track: TrackName
  index: number
}
export type SeqView = 'tracks' | 'split' | 'code'

export interface SeqPrefs {
  view: SeqView
  /** a length change, insert or duplicate on one track moves the later keys of the others too */
  ripple: boolean
  speed: 0.5 | 1 | 2
  /** a disarmed or crashed simulator goes back to its spawn point before each run */
  reset: boolean
  timeStyle: TimeStyle
  /** the time axis fills the width (a state: it re-fits on every resize) */
  fit: boolean
  /** pixels per second when not fitted */
  zoom: number
  /** height of the stage and monitor band in pixels; 0 = folded */
  band: number
  /** share of the width the code takes in the split view */
  split: number
}

export interface LibraryEntry {
  name: string
  saved: number
  doc: ArsDoc
}

export interface SequenceState {
  doc: ArsDoc
  /** the primary block (the last one touched) */
  sel: Selection | null
  /** the other selected blocks */
  more: Selection[]
  cursor: number
  past: ArsDoc[]
  future: ArsDoc[]
  prefs: SeqPrefs
  library: LibraryEntry[]
  /** the document of the last run, for "restore the previous run's sequence" */
  lastRun: ArsDoc | null
  /** problems of the code view's text; while there are any, the tracks show the last valid document */
  problems: CodeProblem[]
  /** an edit: kept for undo unless `transient` (a drag or typing in progress; its end commits) */
  edit(doc: ArsDoc, opts?: { transient?: boolean; sel?: Selection | null }): void
  /** replace the document (import, recording, template, library): undo goes back to the previous one */
  load(doc: ArsDoc): void
  select(sel: Selection | null, mode?: 'one' | 'toggle' | 'add'): void
  selectMany(all: Selection[], primary: Selection | null): void
  setCursor(t: number): void
  setPrefs(patch: Partial<SeqPrefs>): void
  setProblems(p: CodeProblem[]): void
  setLastRun(doc: ArsDoc): void
  undo(): void
  redo(): void
  saveToLibrary(): void
  removeFromLibrary(name: string): void
}

function read<T>(key: string, parse: (raw: string) => T, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    if (raw) return parse(raw)
  } catch {
    /* fall back */
  }
  return fallback
}
function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* per-viewer convenience only */
  }
}

const DEFAULT_PREFS: SeqPrefs = { view: 'tracks', ripple: true, speed: 1, reset: true, timeStyle: 'rel', fit: true, zoom: 48, band: 260, split: 0.42 }
const saveDoc = (doc: ArsDoc) => write(KEY, serialiseArs(doc))

function loadLibrary(): LibraryEntry[] {
  return read(
    LIB_KEY,
    (raw) => {
      const out: LibraryEntry[] = []
      for (const e of JSON.parse(raw) as LibraryEntry[]) {
        try {
          out.push({ name: String(e.name), saved: Number(e.saved) || 0, doc: parseArs(JSON.stringify(e.doc)) })
        } catch {
          /* skip a broken entry */
        }
      }
      return out
    },
    [],
  )
}

export const sameSel = (a: Selection | null, b: Selection | null) => !!a && !!b && a.track === b.track && a.index === b.index
/** Is the working copy different from the library entry of its name (or not saved at all)? */
export function isDirty(s: Pick<SequenceState, 'doc' | 'library'>): boolean {
  const e = s.library.find((x) => x.name === s.doc.name)
  return !e || serialiseArs(e.doc) !== serialiseArs(s.doc)
}

let dragBase: ArsDoc | null = null

/** The selection that still exists in `doc` (undo and redo keep the block being tuned selected). */
function kept(s: Pick<SequenceState, 'sel' | 'more'>, doc: ArsDoc): Pick<SequenceState, 'sel' | 'more'> {
  const has = (x: Selection) => x.index < (doc.tracks[x.track]?.length ?? 0)
  return { sel: s.sel && has(s.sel) ? s.sel : null, more: s.sel && has(s.sel) ? s.more.filter(has) : [] }
}

export const useSequence = create<SequenceState>((set, get) => ({
  doc: read(KEY, parseArs, templateHover()),
  sel: null,
  more: [],
  cursor: 0,
  past: [],
  future: [],
  prefs: { ...DEFAULT_PREFS, ...read(VIEW_KEY, (raw) => JSON.parse(raw) as Partial<SeqPrefs>, {}) },
  library: loadLibrary(),
  lastRun: null,
  problems: [],
  edit: (doc, opts) => {
    const s = get()
    const keep = !(opts && 'sel' in opts)
    const sel = keep ? s.sel : (opts!.sel ?? null)
    const more = keep ? s.more : []
    if (opts?.transient) {
      if (!dragBase) dragBase = s.doc
      set({ doc, sel, more })
      return
    }
    if (!dragBase && doc === s.doc) {
      if (!keep) set({ sel, more })
      return // nothing changed (a blur with no typing)
    }
    const base = dragBase ?? s.doc
    dragBase = null
    saveDoc(doc)
    set({ doc, sel, more, past: [...s.past, base].slice(-UNDO_LIMIT), future: [] })
  },
  load: (doc) => {
    const s = get()
    dragBase = null
    saveDoc(doc)
    set({ doc, sel: null, more: [], cursor: 0, problems: [], past: [...s.past, s.doc].slice(-UNDO_LIMIT), future: [] })
  },
  select: (sel, mode = 'one') => {
    const s = get()
    if (!sel || mode === 'one') return set({ sel, more: [] })
    const all = [...s.more, ...(s.sel ? [s.sel] : [])]
    const had = all.some((x) => sameSel(x, sel))
    if (mode === 'toggle' && had) {
      const rest = all.filter((x) => !sameSel(x, sel))
      return set({ sel: rest[rest.length - 1] ?? null, more: rest.slice(0, -1) })
    }
    set({ sel, more: all.filter((x) => !sameSel(x, sel)) })
  },
  selectMany: (all, primary) => set({ sel: primary, more: all.filter((x) => !sameSel(x, primary)) }),
  setCursor: (cursor) => set({ cursor }),
  setPrefs: (patch) => {
    const prefs = { ...get().prefs, ...patch }
    if ('zoom' in patch) prefs.zoom = Math.min(400, Math.max(4, prefs.zoom))
    write(VIEW_KEY, JSON.stringify(prefs))
    set({ prefs })
  },
  setProblems: (problems) => set({ problems }),
  setLastRun: (lastRun) => set({ lastRun }),
  undo: () => {
    const s = get()
    const prev = s.past[s.past.length - 1]
    if (!prev) return
    dragBase = null
    saveDoc(prev)
    set({ doc: prev, past: s.past.slice(0, -1), future: [s.doc, ...s.future], ...kept(s, prev), problems: [] })
  },
  redo: () => {
    const s = get()
    const next = s.future[0]
    if (!next) return
    dragBase = null
    saveDoc(next)
    set({ doc: next, past: [...s.past, s.doc], future: s.future.slice(1), ...kept(s, next), problems: [] })
  },
  saveToLibrary: () => {
    const s = get()
    const name = s.doc.name.trim() || 'Untitled'
    const doc = name === s.doc.name ? s.doc : { ...s.doc, name }
    const library = [{ name, saved: Date.now(), doc }, ...s.library.filter((e) => e.name !== name)].slice(0, LIB_LIMIT)
    write(LIB_KEY, JSON.stringify(library))
    if (doc !== s.doc) saveDoc(doc)
    set({ library, doc })
  },
  removeFromLibrary: (name) => {
    const library = get().library.filter((e) => e.name !== name)
    write(LIB_KEY, JSON.stringify(library))
    set({ library })
  },
}))
