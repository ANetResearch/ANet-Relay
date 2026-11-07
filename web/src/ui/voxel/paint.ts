// Voxel painting on a single 2D canvas (05 §2.3: no WebGL, so the boot never competes with the stage for a GPU
// context). Colours arrive as resolved token values; geometry and timing come from ui/voxel/wave.ts.
import { cellProgress, flipScale, frontTone, WAVE_ANGLE_DEG, type FrontTone, type Grid } from './wave'

export interface Surface {
  canvas: HTMLCanvasElement
  ctx: CanvasRenderingContext2D
  w: number
  h: number
}

/** Sizes the canvas backing store for the device pixel ratio and returns a context in CSS pixels. */
export function surface(canvas: HTMLCanvasElement, w: number, h: number, maxDpr = 2): Surface | null {
  const ctx = canvas.getContext('2d')
  if (!ctx) return null
  const dpr = Math.min(maxDpr, Math.max(1, typeof devicePixelRatio === 'number' ? devicePixelRatio : 1))
  const bw = Math.max(1, Math.round(w * dpr))
  const bh = Math.max(1, Math.round(h * dpr))
  if (canvas.width !== bw) canvas.width = bw
  if (canvas.height !== bh) canvas.height = bh
  canvas.style.width = `${w}px`
  canvas.style.height = `${h}px`
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  return { canvas, ctx, w, h }
}

export function clear(s: Surface): void {
  s.ctx.clearRect(0, 0, s.w, s.h)
}

/** Front width in cells: the act 2 / VoxelWipe front is 2-3 columns of flipping cells. */
export const FRONT_CELLS = 2.5

/**
 * Splits a wave of `duration` over a grid into the per-cell flip time and the spread of start times, so that the
 * front is FRONT_CELLS cells wide and the last cell finishes exactly at the end.
 */
export function waveTiming(g: Grid, duration: number): { spread: number; cell: number } {
  // cells met along the wave direction (wave.ts WAVE_ANGLE_DEG)
  const a = (WAVE_ANGLE_DEG * Math.PI) / 180
  const across = g.cols * Math.cos(a) + g.rows * Math.sin(a)
  const cell = (duration * FRONT_CELLS) / (across + FRONT_CELLS)
  return { spread: Math.max(0, duration - cell), cell }
}

/**
 * One frame of a diagonal wave. `tones` maps each front tone to a colour, or null to leave the cell transparent
 * ('cover' is the untouched area ahead of the front, 'clear' the area behind it).
 */
export function paintWave(
  s: Surface,
  g: Grid,
  keys: Float32Array,
  t: number,
  start: number,
  spread: number,
  cell: number,
  tones: Record<FrontTone, string | null>,
): void {
  const { ctx } = s
  clear(s)
  const buckets = new Map<string, number[]>()
  for (let i = 0; i < keys.length; i++) {
    const p = cellProgress(t, keys[i], start, spread, cell)
    const tone = frontTone(p)
    const color = tones[tone]
    if (!color) continue
    const c = i % g.cols
    const r = (i - c) / g.cols
    const k = tone === 'cover' || tone === 'clear' ? 1 : flipScale(p)
    if (k <= 0) continue
    const hh = g.size * k
    let b = buckets.get(color)
    if (!b) buckets.set(color, (b = []))
    b.push(c * g.size, r * g.size + (g.size - hh) / 2, g.size, hh)
  }
  for (const [color, rects] of buckets) {
    ctx.fillStyle = color
    ctx.beginPath()
    for (let i = 0; i < rects.length; i += 4) ctx.rect(rects[i], rects[i + 1], rects[i + 2], rects[i + 3])
    ctx.fill()
  }
}

/**
 * One frame of the reveal (act 5): `snap` (same CSS size as the surface, drawn at `scale`) is cut into cells that
 * flip away along the wave; finished cells leave the window underneath visible.
 */
export function paintDissolve(s: Surface, snap: HTMLCanvasElement, scale: number, g: Grid, keys: Float32Array, t: number, start: number, spread: number, cell: number): boolean {
  const { ctx } = s
  clear(s)
  let left = false
  for (let i = 0; i < keys.length; i++) {
    const p = cellProgress(t, keys[i], start, spread, cell)
    if (p >= 1) continue
    left = true
    const k = flipScale(p)
    if (k <= 0) continue
    const c = i % g.cols
    const r = (i - c) / g.cols
    const x = c * g.size
    const y = r * g.size
    // a flipping cell turns about its horizontal axis and pulls in from its neighbours, so the frame reads as
    // separate voxels rather than lines
    const hh = g.size * k
    const ww = p > 0 ? g.size * (0.7 + 0.3 * k) - 1 : g.size
    ctx.drawImage(snap, x * scale, y * scale, g.size * scale, g.size * scale, x + (g.size - ww) / 2, y + (g.size - hh) / 2, ww, hh)
  }
  return left
}
