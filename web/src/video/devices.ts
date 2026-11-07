// Video sources (02 §9, R-17): local UVC inputs (HDMI capture cards) listed by navigator.mediaDevices, bound to an
// aircraft and remembered by device label; one shared MediaStream per device for the inspector thumbnail and the
// stage picture-in-picture; "No signal" when frames stay black for more than 2 s. Pure helpers are unit-tested.
import { create } from 'zustand'

export interface VideoDevice {
  deviceId: string
  label: string
}

export interface VideoBinding {
  label: string
  deviceId: string
}

export type PipCorner = 'br' | 'bl' | 'tr' | 'tl'

export const PIP_MIN_W = 320
export const PIP_MAX_W = 640
export const NO_SIGNAL_MS = 2000
/** Mean luma (0..255) below which a frame counts as black. */
export const BLACK_LUMA = 10

const BIND_KEY = 'relay.video'

interface Persisted {
  bindings: Record<string, VideoBinding>
  corner: PipCorner
  width: number
}

function load(): Persisted {
  const empty: Persisted = { bindings: {}, corner: 'br', width: PIP_MIN_W }
  try {
    const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(BIND_KEY) : null
    if (!raw) return empty
    const p = JSON.parse(raw) as Partial<Persisted>
    return {
      bindings: p.bindings && typeof p.bindings === 'object' ? p.bindings : {},
      corner: p.corner === 'bl' || p.corner === 'tr' || p.corner === 'tl' ? p.corner : 'br',
      width: clampWidth(Number(p.width) || PIP_MIN_W),
    }
  } catch {
    return empty
  }
}

export function clampWidth(w: number, max = PIP_MAX_W): number {
  return Math.round(Math.min(Math.max(w, PIP_MIN_W), Math.max(PIP_MIN_W, max)))
}

/** One device feeds one aircraft: binding it here removes it from any other aircraft. */
export function bindDevice(bindings: Record<string, VideoBinding>, vehicle: string, device: VideoBinding): Record<string, VideoBinding> {
  const next: Record<string, VideoBinding> = {}
  for (const [v, b] of Object.entries(bindings)) if (v !== vehicle && b.label !== device.label) next[v] = b
  next[vehicle] = device
  return next
}

/** The bound device among the present ones: by label first (ids change between sessions), then by id. */
export function resolveDevice(binding: VideoBinding | undefined, devices: readonly VideoDevice[]): VideoDevice | null {
  if (!binding) return null
  return devices.find((d) => d.label && d.label === binding.label) ?? devices.find((d) => d.deviceId === binding.deviceId) ?? null
}

/** Mean luma of an RGBA buffer (Rec. 601). */
export function meanLuma(data: ArrayLike<number>): number {
  let sum = 0
  let n = 0
  for (let i = 0; i + 2 < data.length; i += 4) {
    sum += 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2]
    n++
  }
  return n ? sum / n : 0
}

/** Signal state from the time black frames began (null = not black). */
export function signalState(blackSince: number | null, now: number): 'live' | 'nosignal' {
  return blackSince !== null && now - blackSince > NO_SIGNAL_MS ? 'nosignal' : 'live'
}

/** Nearest corner of the stage for a PiP centre at (cx, cy) in a w x h frame. */
export function nearestCorner(cx: number, cy: number, w: number, h: number): PipCorner {
  const v = cy < h / 2 ? 't' : 'b'
  const hz = cx < w / 2 ? 'l' : 'r'
  return `${v}${hz}` as PipCorner
}

interface VideoState extends Persisted {
  /** PiP closed for these aircraft until shown again */
  hidden: Record<string, boolean>
  bind(vehicle: string, device: VideoBinding): void
  unbind(vehicle: string): void
  setHidden(vehicle: string, hidden: boolean): void
  setPip(p: Partial<Pick<Persisted, 'corner' | 'width'>>): void
}

function save(s: Persisted): void {
  try {
    localStorage.setItem(BIND_KEY, JSON.stringify({ bindings: s.bindings, corner: s.corner, width: s.width }))
  } catch {
    /* storage unavailable: bindings stay for this session */
  }
}

export const useVideo = create<VideoState>()((set, get) => ({
  ...load(),
  hidden: {},
  bind: (vehicle, device) => {
    set({ bindings: bindDevice(get().bindings, vehicle, device), hidden: { ...get().hidden, [vehicle]: false } })
    save(get())
  },
  unbind: (vehicle) => {
    const bindings = { ...get().bindings }
    delete bindings[vehicle]
    set({ bindings })
    save(get())
  },
  setHidden: (vehicle, hidden) => set({ hidden: { ...get().hidden, [vehicle]: hidden } }),
  setPip: (p) => {
    set({ ...p, width: p.width === undefined ? get().width : clampWidth(p.width) })
    save(get())
  },
}))

// ------------------------------------------------------------------------------------------------ devices

export type DeviceListState = { status: 'loading' | 'ok' | 'unsupported'; devices: VideoDevice[] }

let deviceCache: DeviceListState = { status: 'loading', devices: [] }
const listeners = new Set<() => void>()
let watching = false

export async function refreshDevices(): Promise<void> {
  const md = typeof navigator !== 'undefined' ? navigator.mediaDevices : undefined
  if (!md?.enumerateDevices) {
    deviceCache = { status: 'unsupported', devices: [] }
  } else {
    try {
      const all = await md.enumerateDevices()
      deviceCache = { status: 'ok', devices: all.filter((d) => d.kind === 'videoinput').map((d) => ({ deviceId: d.deviceId, label: d.label })) }
    } catch {
      deviceCache = { status: 'unsupported', devices: [] }
    }
    if (!watching) {
      watching = true
      md.addEventListener?.('devicechange', () => void refreshDevices())
    }
  }
  for (const l of listeners) l()
}

export function subscribeDevices(l: () => void): () => void {
  listeners.add(l)
  if (deviceCache.status === 'loading' && listeners.size === 1) void refreshDevices()
  return () => listeners.delete(l)
}

export function getDevices(): DeviceListState {
  return deviceCache
}

// ------------------------------------------------------------------------------------------------ streams

export type StreamError = 'denied' | 'missing' | 'failed'

interface Held {
  stream: Promise<MediaStream>
  refs: number
}

const streams = new Map<string, Held>()

export function classifyMediaError(e: unknown): StreamError {
  const name = (e as { name?: string } | null)?.name ?? ''
  if (name === 'NotAllowedError' || name === 'SecurityError' || name === 'PermissionDeniedError') return 'denied'
  if (name === 'NotFoundError' || name === 'OverconstrainedError' || name === 'DevicesNotFoundError') return 'missing'
  return 'failed'
}

/** Opens (or shares) the stream of a device. Release with releaseStream(deviceId). */
export function acquireStream(deviceId: string): Promise<MediaStream> {
  const held = streams.get(deviceId)
  if (held) {
    held.refs++
    return held.stream
  }
  const stream = navigator.mediaDevices.getUserMedia({ video: { deviceId: { exact: deviceId }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false })
  streams.set(deviceId, { stream, refs: 1 })
  stream.then(
    () => void refreshDevices(), // labels become readable once permission is granted
    () => streams.delete(deviceId),
  )
  return stream
}

export function releaseStream(deviceId: string): void {
  const held = streams.get(deviceId)
  if (!held) return
  held.refs--
  if (held.refs > 0) return
  streams.delete(deviceId)
  void held.stream.then((s) => s.getTracks().forEach((tr) => tr.stop())).catch(() => undefined)
}
