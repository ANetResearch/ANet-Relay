// Motion durations of the arena effects, read from the motion tokens (MOT-01: no literal time outside the token
// files). A token that cannot be read yet (no document, styles not loaded) yields 0, which disables the effect
// rather than inventing a duration; readable values are cached.
const cache = new Map<string, number>()

export function tokenMs(name: string): number {
  const hit = cache.get(name)
  if (hit !== undefined) return hit
  if (typeof document === 'undefined' || typeof getComputedStyle !== 'function') return 0
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  const n = parseFloat(raw)
  const ms = !Number.isFinite(n) ? 0 : raw.endsWith('ms') ? n : raw.endsWith('s') ? n * 1000 : 0
  if (ms > 0) cache.set(name, ms)
  return ms
}

export const ARENA_TOKENS = {
  flash: '--duration-micro',
  debris: '--duration-medium',
  downDebris: '--duration-very-slow',
  trail: '--duration-arena-trail',
  hitMark: '--duration-arena-hit-mark',
  hitDir: '--duration-arena-hit-dir',
} as const

/**
 * Milliseconds a HUD mark made at `at` stays mounted after `now` (0: unmount it). The overlay unmounts on this
 * timer rather than on its CSS animation, because reduced and off motion play no animation at all and would
 * otherwise leave the mark on screen.
 */
export function markRemainingMs(at: number, now: number, holdMs: number): number {
  return Math.max(0, holdMs - Math.max(0, now - at))
}
