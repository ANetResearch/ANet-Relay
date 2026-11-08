// Trail sampling helpers (pure). A trail restarts when the aircraft jumps further than TRAIL_JUMP_M between two
// 100 ms samples: a respawn or a reset moves it, flying does not (v0.5 dogfights respawn aircraft every few seconds).

/** Metres between two trail samples beyond which the trail restarts (above 10 m/s at 10 Hz sampling). */
export const TRAIL_JUMP_M = 3

/** True when `p` is more than TRAIL_JUMP_M from the sample at `at` (x, y, z) in `arr`. */
export function trailJump(arr: ArrayLike<number>, at: number, p: { x: number; y: number; z: number }): boolean {
  const dx = p.x - arr[at]
  const dy = p.y - arr[at + 1]
  const dz = p.z - arr[at + 2]
  return dx * dx + dy * dy + dz * dz > TRAIL_JUMP_M * TRAIL_JUMP_M
}
