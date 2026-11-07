// Hold-to-fire timing (05 §6.3, §7.4; WP-C side of the arena input). Sources (R1, keyboard X, a transmitter switch)
// say each tick which aircraft they want to fire; the tracker turns that into EngineActions.fire(vehicle, on) calls:
// on at the press, again every 100 ms while held (the backend treats 300 ms without a refresh as released), and off
// at the release, on blur, on possession end or when the aircraft stops being eligible. Unit-tested in fire.test.ts.

export const FIRE_REFRESH_MS = 100

export type FireCall = (vehicle: string, on: boolean) => void

export class FireTracker {
  /** vehicle -> time of the last "on" call */
  private held = new Map<string, number>()

  /** `wanted`: aircraft some source fires this tick. Returns nothing; calls `fire` as needed. */
  tick(wanted: ReadonlySet<string>, now: number, fire: FireCall): void {
    for (const [id, at] of this.held) {
      if (!wanted.has(id)) {
        this.held.delete(id)
        fire(id, false)
      } else if (now - at >= FIRE_REFRESH_MS) {
        this.held.set(id, now)
        fire(id, true)
      }
    }
    for (const id of wanted) {
      if (this.held.has(id)) continue
      this.held.set(id, now)
      fire(id, true)
    }
  }

  /** Release everything now (blur, unmount). */
  releaseAll(fire: FireCall): void {
    for (const id of this.held.keys()) fire(id, false)
    this.held.clear()
  }

  firing(id: string): boolean {
    return this.held.has(id)
  }
}
