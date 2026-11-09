// Timeline dock state (10 §2): the document being edited (kept per viewer), the selected block, zoom, undo.
import { create } from 'zustand'
import { parseArs, serialiseArs, templateHover, type ArsDoc, type TrackName } from './model'

const KEY = 'relay.sequence.doc'
const OPEN_KEY = 'relay.sequence.open'
const UNDO_LIMIT = 60

export interface Selection {
  track: TrackName
  index: number
}

export interface SequenceState {
  open: boolean
  doc: ArsDoc
  sel: Selection | null
  /** pixels per second on the time axis */
  zoom: number
  past: ArsDoc[]
  future: ArsDoc[]
  setOpen(open: boolean): void
  /** an edit: kept for undo unless `transient` (a drag in progress; the drag's end commits) */
  edit(doc: ArsDoc, opts?: { transient?: boolean; sel?: Selection | null }): void
  /** replace the document (import, recording, template): undo goes back to the previous one */
  load(doc: ArsDoc): void
  select(sel: Selection | null): void
  setZoom(z: number): void
  undo(): void
  redo(): void
}

function loadDoc(): ArsDoc {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) return parseArs(raw)
  } catch {
    /* fall back to the template */
  }
  return templateHover()
}

function saveDoc(doc: ArsDoc): void {
  try {
    localStorage.setItem(KEY, serialiseArs(doc))
  } catch {
    /* per-viewer convenience only */
  }
}

function loadOpen(): boolean {
  try {
    return localStorage.getItem(OPEN_KEY) === '1'
  } catch {
    return false
  }
}

let dragBase: ArsDoc | null = null

export const useSequence = create<SequenceState>((set, get) => ({
  open: loadOpen(),
  doc: loadDoc(),
  sel: null,
  zoom: 48,
  past: [],
  future: [],
  setOpen: (open) => {
    try {
      localStorage.setItem(OPEN_KEY, open ? '1' : '0')
    } catch {
      /* ignore */
    }
    set({ open })
  },
  edit: (doc, opts) => {
    const s = get()
    const sel = opts && 'sel' in opts ? (opts.sel ?? null) : s.sel
    if (opts?.transient) {
      if (!dragBase) dragBase = s.doc
      set({ doc, sel })
      return
    }
    if (!dragBase && doc === s.doc) return // nothing changed (a blur with no typing)
    const base = dragBase ?? s.doc
    dragBase = null
    saveDoc(doc)
    set({ doc, sel, past: [...s.past, base].slice(-UNDO_LIMIT), future: [] })
  },
  load: (doc) => {
    const s = get()
    dragBase = null
    saveDoc(doc)
    set({ doc, sel: null, past: [...s.past, s.doc].slice(-UNDO_LIMIT), future: [] })
  },
  select: (sel) => set({ sel }),
  setZoom: (z) => set({ zoom: Math.min(240, Math.max(8, z)) }),
  undo: () => {
    const s = get()
    const prev = s.past[s.past.length - 1]
    if (!prev) return
    saveDoc(prev)
    set({ doc: prev, past: s.past.slice(0, -1), future: [s.doc, ...s.future], sel: null })
  },
  redo: () => {
    const s = get()
    const next = s.future[0]
    if (!next) return
    saveDoc(next)
    set({ doc: next, past: [...s.past, s.doc], future: s.future.slice(1), sel: null })
  },
}))
