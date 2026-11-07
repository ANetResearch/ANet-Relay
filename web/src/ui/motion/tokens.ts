// Duration tokens for JS and canvas motion (MOT-01): the values live in styles/motion/*.css only; code reads the
// computed custom property and keeps a fallback for node tests and for the moments before the stylesheet applies.

/** "250ms" / "0.25s" in milliseconds; `fallback` for an empty or unparsable value. */
export function parseMs(value: string | null | undefined, fallback: number): number {
  const s = (value ?? '').trim()
  if (!s) return fallback
  const n = parseFloat(s)
  if (!Number.isFinite(n)) return fallback
  if (s.endsWith('ms')) return n
  if (s.endsWith('s')) return n * 1000
  return fallback
}

/** The computed value of a duration token on :root, in milliseconds. */
export function msToken(name: string, fallback: number): number {
  if (typeof document === 'undefined' || typeof getComputedStyle !== 'function') return fallback
  try {
    return parseMs(getComputedStyle(document.documentElement).getPropertyValue(name), fallback)
  } catch {
    return fallback
  }
}

/** The computed value of any token on :root (colours for canvas code), or `fallback`. */
export function cssToken(name: string, fallback = ''): string {
  if (typeof document === 'undefined' || typeof getComputedStyle !== 'function') return fallback
  try {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
  } catch {
    return fallback
  }
}
