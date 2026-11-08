// Ring touches (07 §2, §5): an aircraft grazed a ring's tube and still passed. The arena effects write them from the
// `ring_touch` event; the course layer's frame loop reads them to flash that ring white. Non-reactive on purpose (no
// React render per touch); entries older than a few seconds are dropped on write.
const KEEP_MS = 3000

const touches = new Map<string, number>()

const keyOf = (vehicle: string, ring: number) => `${vehicle}\u0001${ring}`

/** Records a touch of ring `ring` (index in the run) by `vehicle` now (performance.now()). */
export function ringTouch(vehicle: string, ring: number, at = performance.now()): void {
  for (const [k, t] of touches) if (at - t > KEEP_MS) touches.delete(k)
  touches.set(keyOf(vehicle, ring), at)
}

/** performance.now() of the latest touch of that ring, or null. */
export function ringTouchedAt(vehicle: string, ring: number): number | null {
  return touches.get(keyOf(vehicle, ring)) ?? null
}
