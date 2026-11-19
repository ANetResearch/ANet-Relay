// Gimbal window state (12 §5.5): the picture-in-picture source per aircraft and the thumbnails of the last captures,
// kept in memory only (never written to disk). A store subscription turns every gimbal_capture event into a
// thumbnail: the frame the picture-in-picture shows when it is open on that aircraft, else one rendered frame
// (feed.ts snapshot); a real pod without a rendered picture keeps the entry without an image.
import { create } from 'zustand'
import { useRelay } from '@/store/relay'
import { snapshot, type FeedFrame } from './feed'
import type { Lens, PipSource } from './model'

export type { PipSource }


export interface Thumb {
  seq: number | null
  /** capture time, epoch seconds */
  t: number
  /** JPEG data URL, or null without a rendered picture */
  url: string | null
  lens: Lens | null
  zoom: number | null
}

/** Thumbnails kept per aircraft. */
export const THUMBS_KEPT = 8

interface GimbalUi {
  source: Record<string, PipSource>
  thumbs: Record<string, Thumb[]>
  setSource(id: string, s: PipSource): void
  addThumb(id: string, t: Thumb): void
}

export const useGimbalUi = create<GimbalUi>()((set) => ({
  source: {},
  thumbs: {},
  setSource: (id, s) => set((st) => ({ source: { ...st.source, [id]: s } })),
  addThumb: (id, t) =>
    set((st) => {
      const list = (st.thumbs[id] ?? []).filter((x) => x.seq === null || x.seq !== t.seq)
      return { thumbs: { ...st.thumbs, [id]: [...list, t].slice(-THUMBS_KEPT) } }
    }),
}))

/** Thumbnail of a frame: 160 x 90 JPEG. */
export function thumbUrl(frame: Pick<FeedFrame, 'image'> | HTMLCanvasElement): string | null {
  try {
    const c = document.createElement('canvas')
    c.width = 160
    c.height = 90
    const ctx = c.getContext('2d')
    if (!ctx) return null
    if (frame instanceof HTMLCanvasElement) ctx.drawImage(frame, 0, 0, 160, 90)
    else {
      const src = document.createElement('canvas')
      src.width = frame.image.width
      src.height = frame.image.height
      src.getContext('2d')?.putImageData(frame.image, 0, 0)
      ctx.drawImage(src, 0, 0, 160, 90)
    }
    return c.toDataURL('image/jpeg', 0.82)
  } catch {
    return null
  }
}

/** The picture-in-picture canvas currently showing an aircraft's rendered pod picture (registered by the PiP). */
export const shownCanvas = new Map<string, HTMLCanvasElement>()

let started = false

/** Watches gimbal_capture events (once per window). */
export function watchCaptures(): void {
  if (started) return
  started = true
  let last = useRelay.getState().lastSeq
  useRelay.subscribe((s) => {
    if (s.lastSeq < last) last = 0
    if (s.lastSeq === last) return
    const fresh = s.events.filter((e) => e.seq > last && e.kind === 'gimbal_capture')
    last = s.lastSeq
    const now = s.fleet?.t ?? Date.now() / 1000
    for (const e of fresh) {
      if (!e.vehicle || now - e.t > 5) continue
      const id = e.vehicle
      const d = e.data ?? {}
      if (d.kind !== undefined && d.kind !== 'photo') continue
      const base: Thumb = {
        seq: typeof d.seq === 'number' ? d.seq : null,
        t: typeof d.t === 'number' ? d.t : e.t,
        url: null,
        lens: typeof d.lens === 'string' ? (d.lens as Lens) : null,
        zoom: typeof d.zoom === 'number' ? d.zoom : null,
      }
      const shown = shownCanvas.get(id)
      if (shown) {
        useGimbalUi.getState().addThumb(id, { ...base, url: thumbUrl(shown) })
        continue
      }
      const v = s.fleet?.vehicles.find((x) => x.id === id)
      if (v?.gimbal?.kind !== 'sim') {
        useGimbalUi.getState().addThumb(id, base)
        continue
      }
      void snapshot(id).then((f) => useGimbalUi.getState().addThumb(id, { ...base, url: f ? thumbUrl(f) : null }))
    }
  })
}
