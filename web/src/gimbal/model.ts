// Gimbal pod, pure parts (12 §5): the lens table (mirror of backend relay/gimbal/lens.py), the joint angles of the
// stage model, picture pixels <-> angles, the HUD texts, the thermal palettes and the request bodies of
// POST /vehicles/{id}/gimbal and /gimbal/attach. No React, no three.js: unit-tested in node.
//
// Angle conventions (12 §5.1): the pod's frame angles, yaw right +, pitch up + (looking down is negative), independent
// of control_frame. The ANetDU7 file (tools/models/build_anet_du7.mjs) has +Y up, the nose along +Z and the pilot's
// LEFT along +X; Gimbal_Yaw turns about its local +Y, Gimbal_Pitch tilts about its local +X and the camera looks
// along local +Z. A positive rotation about +Y turns +Z towards +X (left), so a right yaw is a negative node angle;
// a positive rotation about +X turns +Z towards -Y (down), so an upward pitch is a negative node angle too.
import type { GimbalView } from '@/api'

export type Lens = GimbalView['lens']
export type Palette = GimbalView['palette']
export const LENSES: readonly Lens[] = ['visible1', 'visible2', 'ir1', 'ir2']
export const PALETTES: readonly Palette[] = ['white_hot', 'black_hot', 'pseudo']

export interface LensSpec {
  /** horizontal field of view at 1x, degrees */
  hfov: number
  zoom: [number, number]
  infrared: boolean
}

/** backend relay/gimbal/lens.py LENS_TABLE */
export const LENS_TABLE: Record<Lens, LensSpec> = {
  visible1: { hfov: 60, zoom: [1, 30], infrared: false },
  visible2: { hfov: 90, zoom: [1, 1], infrared: false },
  ir1: { hfov: 40, zoom: [1, 8], infrared: true },
  ir2: { hfov: 24, zoom: [1, 1], infrared: true },
}

/** The picture `track` pixels refer to: 1280 x 720, centre (0, 0), x right, y down (lens.py IMAGE_PX). */
export const IMAGE_PX: readonly [number, number] = [1280, 720]
export const ASPECT = 16 / 9
const DEG = Math.PI / 180

export const isInfrared = (lens: Lens | null | undefined): boolean => !!lens && LENS_TABLE[lens]?.infrared === true
export const isFixed = (lens: Lens): boolean => LENS_TABLE[lens].zoom[1] <= LENS_TABLE[lens].zoom[0]

export function clampZoom(lens: Lens, zoom: number): number {
  const [lo, hi] = LENS_TABLE[lens].zoom
  return Math.min(hi, Math.max(lo, Number.isFinite(zoom) ? zoom : lo))
}

/** [horizontal, vertical] field of view in degrees at `zoom` on `lens` (lens.py fov_deg). */
export function fovDeg(lens: Lens, zoom: number): [number, number] {
  const half = Math.tan((LENS_TABLE[lens].hfov / 2) * DEG) / clampZoom(lens, zoom)
  return [(2 * Math.atan(half)) / DEG, (2 * Math.atan(half / ASPECT)) / DEG]
}

/**
 * Vertical field of view for the render camera: the view's own fov_deg when it is sane (the backend estimates it
 * for a DYT pod too), else the lens table's.
 */
export function verticalFov(g: Pick<GimbalView, 'fov_deg' | 'lens' | 'zoom'>): number {
  const v = g.fov_deg?.[1]
  if (typeof v === 'number' && Number.isFinite(v) && v > 0.1 && v < 170) return v
  return fovDeg(g.lens, g.zoom)[1]
}

/** Node rotations (radians) of Gimbal_Yaw (about its local Y) and Gimbal_Pitch (about its local X). */
export function gimbalNodeAngles(yawDeg: number, pitchDeg: number): { y: number; x: number } {
  return { y: -yawDeg * DEG, x: -pitchDeg * DEG }
}

/**
 * The camera's viewing direction in the model file's frame (+Y up, nose +Z, left +X) for frame angles: the yaw node
 * then the pitch node applied to the camera's +Z. Used by the tests and the stage's line of sight.
 */
export function lookDirection(yawDeg: number, pitchDeg: number): [number, number, number] {
  const { y, x } = gimbalNodeAngles(yawDeg, pitchDeg)
  // Rx(x) * (0, 0, 1) = (0, -sin x, cos x); then Ry(y)
  const vy = -Math.sin(x)
  const vz0 = Math.cos(x)
  return [Math.sin(y) * vz0, vy, Math.cos(y) * vz0]
}

/** Picture pixel (1280 x 720, centre 0) of a pointer at (px, py) in a w x h box showing the picture. */
export function pictureFromPointer(px: number, py: number, w: number, h: number): { x: number; y: number } {
  const x = (px / Math.max(1, w) - 0.5) * IMAGE_PX[0]
  const y = (py / Math.max(1, h) - 0.5) * IMAGE_PX[1]
  return { x: Math.round(Math.max(-IMAGE_PX[0] / 2, Math.min(IMAGE_PX[0] / 2, x))), y: Math.round(Math.max(-IMAGE_PX[1] / 2, Math.min(IMAGE_PX[1] / 2, y))) }
}

/** Picture pixel of a direction (degrees from the optical axis, right and down +; lens.py angles_pixel). */
export function pixelFromAngles(lens: Lens, zoom: number, axDeg: number, ayDeg: number): { x: number; y: number } {
  const [h, v] = fovDeg(lens, zoom)
  return {
    x: (Math.tan(axDeg * DEG) / Math.tan((h / 2) * DEG)) * (IMAGE_PX[0] / 2),
    y: (Math.tan(ayDeg * DEG) / Math.tan((v / 2) * DEG)) * (IMAGE_PX[1] / 2),
  }
}

/**
 * The tracking gate as fractions of the picture (left, top, width, height), or null when nothing is locked. The
 * centre is the miss distance (x right, y down) projected through the current lens.
 */
export function trackBox(g: Pick<GimbalView, 'tracking' | 'lens' | 'zoom'>): { left: number; top: number; width: number; height: number } | null {
  const tr = g.tracking
  if (!tr || tr.state !== 'locked') return null
  const c = pixelFromAngles(g.lens, g.zoom, tr.x_deg, tr.y_deg)
  const bw = Math.max(24, tr.box_px?.[0] || 96)
  const bh = Math.max(24, tr.box_px?.[1] || 96)
  const cx = (c.x + IMAGE_PX[0] / 2) / IMAGE_PX[0]
  const cy = (c.y + IMAGE_PX[1] / 2) / IMAGE_PX[1]
  const width = bw / IMAGE_PX[0]
  const height = bh / IMAGE_PX[1]
  return { left: cx - width / 2, top: cy - height / 2, width, height }
}

// ---------------------------------------------------------------- texts

/** Short lens names of the HUD and the lens switch: EO (zoom), EO W (wide), IR, IR 2. */
export const LENS_SHORT: Record<Lens, string> = { visible1: 'EO', visible2: 'EO W', ir1: 'IR', ir2: 'IR 2' }

export function zoomText(zoom: number | null | undefined): string {
  if (typeof zoom !== 'number' || !Number.isFinite(zoom)) return '—'
  return `${zoom >= 10 ? zoom.toFixed(0) : zoom.toFixed(1)}×`
}

/** "EO 4.0×" for a zoom lens, "EO W" for a fixed one. */
export function lensText(g: Pick<GimbalView, 'lens' | 'zoom'>): string {
  return isFixed(g.lens) ? LENS_SHORT[g.lens] : `${LENS_SHORT[g.lens]} ${zoomText(g.zoom)}`
}

/** Signed angle with one decimal and a real minus sign: "+12.0°", "−30.0°". */
export function signedDeg(d: number | null | undefined, digits = 1): string {
  if (typeof d !== 'number' || !Number.isFinite(d)) return '—'
  const r = Number(d.toFixed(digits))
  return `${r > 0 ? '+' : r < 0 ? '−' : ''}${Math.abs(r).toFixed(digits)}°`
}

export function rangeText(m: number | null | undefined): string | null {
  if (typeof m !== 'number' || !Number.isFinite(m) || m <= 0) return null
  return m >= 100 ? `${m.toFixed(0)} m` : `${m.toFixed(1)} m`
}

/** "00:12" of a recording that started at `since` (seconds). */
export function recTime(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

/** Zoom presets of the stepper within a lens's range (1, 2, 4, 8, 15, 30 on the EO zoom lens). */
export function zoomSteps(lens: Lens): number[] {
  const [lo, hi] = LENS_TABLE[lens].zoom
  const out = [1, 2, 4, 8, 15, 30].filter((z) => z >= lo && z <= hi)
  if (!out.includes(hi)) out.push(hi)
  return out
}

/** The next preset up (+1) or down (-1) from the current zoom. */
export function stepZoom(lens: Lens, zoom: number, dir: 1 | -1): number {
  const steps = zoomSteps(lens)
  if (dir > 0) return steps.find((z) => z > zoom + 0.05) ?? steps[steps.length - 1]
  return [...steps].reverse().find((z) => z < zoom - 0.05) ?? steps[0]
}

// ---------------------------------------------------------------- picture-in-picture and controls

export type PipSource = 'gimbal' | 'video'

/**
 * What the picture-in-picture of an aircraft shows (12 §5.5): a simulated pod's rendered picture (with a GIMBAL |
 * VIDEO switch when a video source is bound too; the pod's picture unless the operator chose the video); for a real
 * (DYT) pod the bound video, or the "no video source" state, under the gimbal HUD; without a pod the bound video.
 */
export function pipSource(pod: 'sim' | 'dyt' | null, bound: boolean, chosen: PipSource | undefined): { source: PipSource | null; switchable: boolean; hud: boolean } {
  if (pod === 'sim') {
    const source = bound ? (chosen ?? 'gimbal') : 'gimbal'
    return { source, switchable: bound, hud: source === 'gimbal' }
  }
  if (pod === 'dyt') return { source: 'video', switchable: false, hud: true }
  return { source: bound ? 'video' : null, switchable: false, hud: false }
}

/** Rate of the pad in deg/s: 30 at 1x, finer as the lens zooms in, 3 at the least. */
export function padSpeed(zoom: number): number {
  return Math.max(3, Math.min(30, 30 / Math.sqrt(Math.max(1, Number.isFinite(zoom) ? zoom : 1))))
}

/** Zoom slider position (0..1000, logarithmic over the lens's range) and back (one decimal). */
export function zoomToSlider(lens: Lens, zoom: number): number {
  const [lo, hi] = LENS_TABLE[lens].zoom
  return hi <= lo ? 0 : Math.round((Math.log(clampZoom(lens, zoom) / lo) / Math.log(hi / lo)) * 1000)
}
export function sliderToZoom(lens: Lens, x: number): number {
  const [lo, hi] = LENS_TABLE[lens].zoom
  return Math.round(lo * Math.pow(hi / lo, Math.min(1000, Math.max(0, x)) / 1000) * 10) / 10
}

// ---------------------------------------------------------------- thermal imagery

/** Ironbow-like control points (position 0..1, sRGB 0..255) of the pseudo-colour palette. */
const PSEUDO: readonly [number, number, number, number][] = [
  [0, 4, 4, 18],
  [0.18, 38, 12, 92],
  [0.38, 128, 18, 120],
  [0.58, 206, 52, 54],
  [0.76, 240, 128, 22],
  [0.9, 252, 206, 70],
  [1, 255, 250, 228],
]

/** 256-entry RGB lookup table of a palette. */
export function paletteLut(p: Palette): Uint8Array {
  const lut = new Uint8Array(256 * 3)
  for (let i = 0; i < 256; i++) {
    const t = i / 255
    if (p === 'white_hot' || p === 'black_hot') {
      const g = Math.round((p === 'white_hot' ? t : 1 - t) * 255)
      lut[i * 3] = g
      lut[i * 3 + 1] = g
      lut[i * 3 + 2] = g
      continue
    }
    let k = 1
    while (k < PSEUDO.length - 1 && PSEUDO[k][0] < t) k++
    const a = PSEUDO[k - 1]
    const b = PSEUDO[k]
    const f = b[0] === a[0] ? 0 : (t - a[0]) / (b[0] - a[0])
    for (let c = 0; c < 3; c++) lut[i * 3 + c] = Math.round(a[c + 1] + (b[c + 1] - a[c + 1]) * f)
  }
  return lut
}

/**
 * Apparent temperature 0..255 of a rendered pixel (sRGB bytes): luminance lifted off the floor so the cold ground is
 * not black, the ground kept cool and lit airframes pushed towards hot.
 */
export function heat(r: number, g: number, b: number): number {
  const l = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
  const t = 0.1 + 0.9 * Math.pow(Math.min(1, l * 1.45), 1.1)
  return Math.round(Math.min(1, t) * 255)
}

/**
 * Copies a WebGL readback (rows bottom-up, RGBA) into canvas ImageData order (rows top-down), through a thermal
 * palette when `lut` is given.
 */
export function toImage(src: ArrayLike<number>, w: number, h: number, out: Uint8ClampedArray | Uint8Array, lut: Uint8Array | null): void {
  const row = w * 4
  for (let y = 0; y < h; y++) {
    const s0 = (h - 1 - y) * row
    const d0 = y * row
    if (!lut) {
      for (let i = 0; i < row; i++) out[d0 + i] = src[s0 + i]
      continue
    }
    for (let x = 0; x < row; x += 4) {
      const t = heat(src[s0 + x], src[s0 + x + 1], src[s0 + x + 2]) * 3
      out[d0 + x] = lut[t]
      out[d0 + x + 1] = lut[t + 1]
      out[d0 + x + 2] = lut[t + 2]
      out[d0 + x + 3] = 255
    }
  }
}

// ---------------------------------------------------------------- requests (12 §5.2)

export type GimbalAction = 'rate' | 'stop' | 'angle' | 'center' | 'follow' | 'lock' | 'zoom' | 'lens' | 'palette' | 'photo' | 'record' | 'track' | 'track_stop' | 'laser'
export type GimbalBody = { action: GimbalAction } & Record<string, unknown>

/** Rate of the pad: a direction (-1..1 each, x right, y up) at `speed` deg/s, clamped to the pod's +-100. */
export function rateBody(dx: number, dy: number, speed: number): GimbalBody {
  const c = (v: number) => Math.round(Math.max(-100, Math.min(100, v)) * 10) / 10
  return { action: 'rate', yaw_dps: c(dx * speed), pitch_dps: c(dy * speed) }
}

export const zoomBody = (lens: Lens, level: number): GimbalBody => ({ action: 'zoom', level: Math.round(clampZoom(lens, level) * 10) / 10 })
export const lensBody = (lens: Lens, pip?: Lens | null): GimbalBody => (pip === undefined ? { action: 'lens', lens } : { action: 'lens', lens, pip })
export const trackBody = (x: number, y: number): GimbalBody => ({ action: 'track', x: Math.round(x), y: Math.round(y) })
export const onBody = (action: 'follow' | 'lock' | 'record' | 'laser', on: boolean): GimbalBody => ({ action, on })

export type AttachForm = { transport: 'tcp' | 'serial'; host: string; port: string; device: string; baud: string }
export const DEFAULT_ATTACH: AttachForm = { transport: 'tcp', host: '', port: '2000', device: '', baud: '115200' }

/** Problems of the attach form by field (keys of the i18n `gimbal.attach.err.*`), empty when it can be sent. */
export function attachErrors(f: AttachForm): Partial<Record<'host' | 'port' | 'device' | 'baud', 'host' | 'port' | 'device' | 'baud'>> {
  const out: Partial<Record<'host' | 'port' | 'device' | 'baud', 'host' | 'port' | 'device' | 'baud'>> = {}
  if (f.transport === 'tcp') {
    const host = f.host.trim()
    if (!host || host.length > 253 || /[\s/\\]/.test(host)) out.host = 'host'
    const port = Number(f.port)
    if (!Number.isInteger(port) || port < 1 || port > 65535) out.port = 'port'
  } else {
    if (!f.device.trim()) out.device = 'device'
    const baud = Number(f.baud)
    if (!Number.isInteger(baud) || baud < 1200 || baud > 3000000) out.baud = 'baud'
  }
  return out
}

/** The POST /vehicles/{id}/gimbal/attach body of the form. */
export function attachBody(f: AttachForm): Record<string, unknown> {
  return f.transport === 'tcp'
    ? { transport: 'tcp', host: f.host.trim(), port: Number(f.port) }
    : { transport: 'serial', device: f.device.trim(), baud: Number(f.baud) }
}
