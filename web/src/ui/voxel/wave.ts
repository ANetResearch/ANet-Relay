// Voxel language (05 §2.3, §2.5), the pure part: the diagonal wave shared by the boot cut (act 2), the boot reveal
// (act 5) and VoxelWipe; the logo dot matrix and its landing order (act 1); deterministic scatter vectors. No DOM,
// so it is unit-tested in node; ui/voxel/paint.ts draws with it.
import { cubicBezier } from '@/stage/math'

/** The wave travels from bottom left to top right, tilted 18° above the horizontal (05 §2.3 act 2). */
export const WAVE_ANGLE_DEG = 18

/** --ease-spring-snappy of the motion tokens: cubic-bezier(0.34, 1.36, 0.64, 1). */
export const easeSpringSnappy = cubicBezier(0.34, 1.36, 0.64, 1)

export interface Grid {
  size: number
  cols: number
  rows: number
}

/** Cells of `size` px covering a w x h box (the last row and column may be partial). */
export function grid(w: number, h: number, size: number): Grid {
  const s = Math.max(1, size)
  return { size: s, cols: Math.max(1, Math.ceil(Math.max(0, w) / s)), rows: Math.max(1, Math.ceil(Math.max(0, h) / s)) }
}

/** Projection of (x, y) on the wave direction (screen y points down, so "up" is -y). */
function project(x: number, y: number, angleDeg: number): number {
  const a = (angleDeg * Math.PI) / 180
  return x * Math.cos(a) - y * Math.sin(a)
}

/** Where (x, y) of a w x h box sits along the wave: 0 at the bottom-left corner, 1 at the top-right corner. */
export function diagKey(x: number, y: number, w: number, h: number, angleDeg = WAVE_ANGLE_DEG): number {
  const lo = project(0, h, angleDeg)
  const hi = project(w, 0, angleDeg)
  if (hi - lo <= 0) return 0
  return Math.min(1, Math.max(0, (project(x, y, angleDeg) - lo) / (hi - lo)))
}

/** Diagonal key of every cell centre, row-major. */
export function cellKeys(g: Grid, w: number, h: number, angleDeg = WAVE_ANGLE_DEG): Float32Array {
  const out = new Float32Array(g.cols * g.rows)
  for (let r = 0; r < g.rows; r++) {
    for (let c = 0; c < g.cols; c++) out[r * g.cols + c] = diagKey((c + 0.5) * g.size, (r + 0.5) * g.size, w, h, angleDeg)
  }
  return out
}

/**
 * Flip progress of one cell, 0 (untouched) .. 1 (done): the cell starts at `start + key * spread` and takes `cell`.
 * A zero cell time jumps straight to done.
 */
export function cellProgress(t: number, key: number, start: number, spread: number, cell: number): number {
  const s = start + key * spread
  if (t < s) return 0
  if (cell <= 0) return 1
  return Math.min(1, (t - s) / cell)
}

export type FrontTone = 'cover' | 'dark' | 'light' | 'clear'

/** Act 2 / VoxelWipe front: a cell turns g800 -> g50 -> transparent while it flips (2-3 columns wide in motion). */
export function frontTone(p: number): FrontTone {
  if (p <= 0) return 'cover'
  if (p >= 1) return 'clear'
  return p < 1 / 3 ? 'dark' : p < 2 / 3 ? 'light' : 'clear'
}

/** Vertical scale of a flipping cell (a card turning about its horizontal axis): 1 -> 0. */
export function flipScale(p: number): number {
  if (p <= 0) return 1
  if (p >= 1) return 0
  return Math.abs(Math.cos((p * Math.PI) / 2))
}

// ------------------------------------------------------------------------------------------------ logo matrix

export type LogoTone = 'red' | 'light' | 'dark'

export interface LogoCell {
  c: number
  r: number
  tone: LogoTone
}

/**
 * The logo as a dot matrix: `rgba` is the logo drawn at one pixel per cell (cols x rows). Transparent pixels are
 * empty; red-dominant ones are the brand red; bright ones g50; the black body of the badge a dark voxel.
 */
export function logoCells(rgba: ArrayLike<number>, cols: number, rows: number, alphaMin = 90): LogoCell[] {
  const out: LogoCell[] = []
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const i = (r * cols + c) * 4
      const a = rgba[i + 3]
      if (a < alphaMin) continue
      const R = rgba[i]
      const G = rgba[i + 1]
      const B = rgba[i + 2]
      const tone: LogoTone = R > 70 && R > G * 1.6 && R > B * 1.6 ? 'red' : (R + G + B) / 3 > 150 ? 'light' : 'dark'
      out.push({ c, r, tone })
    }
  }
  return out
}

/** Landing order of act 1: by diagonal distance to the logo centre, nearest first (ties by plain distance). */
export function landingOrder(cells: LogoCell[], cols: number, rows: number, angleDeg = WAVE_ANGLE_DEG): LogoCell[] {
  const cx = cols / 2
  const cy = rows / 2
  const pc = project(cx, cy, angleDeg)
  const d = (k: LogoCell) => Math.abs(project(k.c + 0.5, k.r + 0.5, angleDeg) - pc)
  const e = (k: LogoCell) => Math.hypot(k.c + 0.5 - cx, k.r + 0.5 - cy)
  return [...cells].sort((a, b) => d(a) - d(b) || e(a) - e(b) || a.r - b.r || a.c - b.c)
}

/** A stable 0..1 value per integer (no Math.random, so the opening and the tests are repeatable). */
export function hash01(n: number): number {
  let x = (n | 0) ^ 0x9e3779b9
  x = Math.imul(x ^ (x >>> 16), 0x85ebca6b)
  x = Math.imul(x ^ (x >>> 13), 0xc2b2ae35)
  x ^= x >>> 16
  return (x >>> 0) / 4294967296
}

/**
 * Start point of a voxel flying in from beyond the window edge: along the ray from the logo centre through its
 * landing point (with a small angular jitter), pushed outside the w x h viewport.
 */
export function flightStart(target: { x: number; y: number }, centre: { x: number; y: number }, w: number, h: number, seed: number): { x: number; y: number } {
  let dx = target.x - centre.x
  let dy = target.y - centre.y
  if (Math.hypot(dx, dy) < 1e-3) {
    dx = hash01(seed) - 0.5
    dy = hash01(seed + 7) - 0.5
  }
  const jitter = (hash01(seed * 3 + 1) - 0.5) * 0.6
  const a = Math.atan2(dy, dx) + jitter
  const ux = Math.cos(a)
  const uy = Math.sin(a)
  // distance to leave the viewport from the target along (ux, uy), plus a margin
  const tx = ux > 0 ? (w - target.x) / ux : ux < 0 ? -target.x / ux : Infinity
  const ty = uy > 0 ? (h - target.y) / uy : uy < 0 ? -target.y / uy : Infinity
  const dist = Math.min(tx, ty) + 48 + hash01(seed * 5 + 3) * 120
  return { x: target.x + ux * dist, y: target.y + uy * dist }
}

/** Unit vectors for `n` scattering voxels (cut flash, StageLoading), spread evenly with a stable jitter. */
export function scatterVectors(n: number): { x: number; y: number }[] {
  return Array.from({ length: n }, (_, i) => {
    const a = ((i + hash01(i * 11) * 0.5) / Math.max(1, n)) * Math.PI * 2
    return { x: Math.cos(a), y: Math.sin(a) }
  })
}
