// The gimbal picture feed (12 §5.5): who wants a rendered picture of which aircraft's pod, at what size and rate.
// The picture-in-picture opens a feed while it shows a simulated pod's view; a capture event asks for one frame
// (snapshot) when no feed is open. The stage's scene (stage/scene/GimbalCamera.tsx) serves the requests from its
// own frame loop and never renders a picture nobody asked for. No React; no three.js.

export interface FeedFrame {
  /** the main picture, already through the thermal palette for an infrared lens */
  image: ImageData
  /** the pod's small picture (gimbal.pip), when it has one */
  pip: ImageData | null
  /** performance.now() when it was rendered */
  at: number
}

export interface FeedRequest {
  vehicleId: string
  /** picture size in pixels (16:9) */
  width: number
  height: number
  /** frames per second at most */
  fps: number
  draw(frame: FeedFrame): void
  /** one frame only (snapshots); removed after it is drawn */
  once?: boolean
}

interface Slot extends FeedRequest {
  lastAt: number
  busy: boolean
  n: number
}

const slots = new Set<Slot>()
const listeners = new Set<() => void>()

/** Opens a feed; returns the function that closes it. */
export function openFeed(req: FeedRequest): () => void {
  const slot: Slot = { ...req, lastAt: -Infinity, busy: false, n: 0 }
  slots.add(slot)
  listeners.forEach((l) => l())
  return () => {
    slots.delete(slot)
    listeners.forEach((l) => l())
  }
}

/** The open requests (read by the renderer every frame). */
export function feedSlots(): ReadonlySet<Slot> {
  return slots
}

export type { Slot as FeedSlot }

/** Whether the stage can render pictures at all (a WebGL stage is mounted). Set by the renderer. */
export const feedHost = { mounted: false, software: false }

/** Size of a feed for a picture-in-picture `cssWidth` pixels wide: at most 640 x 360, 320 x 180 on software GL. */
export function feedSize(cssWidth: number, dpr: number, software: boolean): { width: number; height: number } {
  const max = software ? 320 : 640
  const w = Math.max(160, Math.min(max, Math.round((cssWidth * Math.min(dpr, 1.5)) / 16) * 16))
  return { width: w, height: Math.round((w * 9) / 16) }
}

/** Frame rate of a feed: 15 per second on a GPU, 4 on a software renderer (the stage's own frames come first). */
export const feedFps = (software: boolean) => (software ? 4 : 15)

/** One rendered frame of an aircraft's pod (capture thumbnails); null when the stage cannot render it in time. */
export function snapshot(vehicleId: string, width = 320, height = 180, timeoutMs = 1500): Promise<FeedFrame | null> {
  if (!feedHost.mounted) return Promise.resolve(null)
  return new Promise((resolve) => {
    let done = false
    const close = openFeed({
      vehicleId, width, height, fps: 60, once: true,
      draw: (f) => {
        if (done) return
        done = true
        close()
        resolve(f)
      },
    })
    setTimeout(() => {
      if (done) return
      done = true
      close()
      resolve(null)
    }, timeoutMs)
  })
}
