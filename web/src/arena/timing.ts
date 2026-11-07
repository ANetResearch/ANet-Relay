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
