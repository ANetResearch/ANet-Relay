// Graphite tokens as three.js colours. Values are never written here: each token is resolved by the browser
// (custom property -> computed colour -> one canvas pixel), so relative colours such as
// `oklch(from var(--g700) l c h / 0.5)` resolve exactly as CSS paints them.
import * as THREE from 'three'

export interface TokenColor {
  color: THREE.Color
  alpha: number
}

const cache = new Map<string, TokenColor>()
let probe: HTMLSpanElement | null = null
let ctx: CanvasRenderingContext2D | null = null

function resolve(name: string): TokenColor {
  const out: TokenColor = { color: new THREE.Color(), alpha: 1 }
  if (typeof document === 'undefined') return out
  if (!probe) {
    probe = document.createElement('span')
    probe.style.display = 'none'
    document.body.appendChild(probe)
  }
  probe.style.color = `var(--${name})`
  const css = getComputedStyle(probe).color
  if (!ctx) {
    const c = document.createElement('canvas')
    c.width = 1
    c.height = 1
    ctx = c.getContext('2d', { willReadFrequently: true })
  }
  if (!ctx) return out
  ctx.clearRect(0, 0, 1, 1)
  ctx.fillStyle = css
  ctx.fillRect(0, 0, 1, 1)
  const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data
  // getImageData returns unpremultiplied sRGB bytes
  out.color.setRGB(r / 255, g / 255, b / 255, THREE.SRGBColorSpace)
  out.alpha = a / 255
  return out
}

/** Token colour (linear working space) and alpha, cached per name. */
export function token(name: string): TokenColor {
  let c = cache.get(name)
  if (!c) {
    c = resolve(name)
    cache.set(name, c)
  }
  return c
}

/** A fresh THREE.Color of a token (callers may mutate it). */
export function tokenColor(name: string): THREE.Color {
  return token(name).color.clone()
}

export type StageToken =
  | 'g50' | 'g100' | 'g200' | 'g300' | 'g400' | 'g500' | 'g600' | 'g700' | 'g800' | 'g950' | 'r500' | 'white'
  | 'scene-clear' | 'scene-grid-minor' | 'scene-grid-major' | 'trail-default' | 'trail-selected'
