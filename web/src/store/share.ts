// Structural sharing for 10 Hz snapshots: parts of `next` that are deep-equal to `prev` keep the previous object
// identity, so narrow zustand selectors (useVehicle(id), useAlerts()) only re-render when their slice changed.
export function share<T>(prev: unknown, next: T): T {
  if (Object.is(prev, next)) return prev as T
  if (typeof prev !== 'object' || typeof next !== 'object' || prev === null || next === null) return next
  if (Array.isArray(prev) !== Array.isArray(next)) return next
  if (Array.isArray(next)) {
    const p = prev as unknown[]
    let same = p.length === next.length
    const out = next.map((v, i) => {
      const s = share(p[i], v)
      if (s !== p[i]) same = false
      return s
    })
    return (same ? p : out) as T
  }
  const p = prev as Record<string, unknown>
  const n = next as Record<string, unknown>
  const nk = Object.keys(n)
  let same = Object.keys(p).length === nk.length
  const out: Record<string, unknown> = {}
  for (const k of nk) {
    const s = share(p[k], n[k])
    out[k] = s
    if (s !== p[k] || !(k in p)) same = false
  }
  return (same ? p : out) as T
}
