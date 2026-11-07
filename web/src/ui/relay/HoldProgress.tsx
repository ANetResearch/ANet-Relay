// Hold-to-confirm (02 §7.5): a 1 px g50 line that fills left to right over the hold duration; releasing cancels it
// with no residue. HoldTimer is the pure timing core (unit-tested) shared by gamepad and pointer holds.
import type { CSSProperties } from 'react'
import { cn } from '@/lib/utils'

export function HoldProgress({ active, durationMs = 1000, className }: { active: boolean; durationMs?: number; className?: string }) {
  return (
    <div
      className={cn('hold-line', className)}
      data-active={active ? '' : undefined}
      style={{ '--hold-ms': `${durationMs}ms` } as CSSProperties}
      aria-hidden="true"
      // remount the fill on every press so the animation restarts from zero
      key={active ? 'on' : 'off'}
    >
      <i />
    </div>
  )
}

/** Pure hold timer: press(t) .. release(t); fired once when held for durationMs. */
export class HoldTimer {
  private startedAt: number | null = null
  private fired = false
  constructor(readonly durationMs: number) {}

  press(now: number): void {
    if (this.startedAt === null) {
      this.startedAt = now
      this.fired = false
    }
  }

  release(): void {
    this.startedAt = null
    this.fired = false
  }

  /** 0..1 progress, 0 when not pressed */
  progress(now: number): number {
    if (this.startedAt === null) return 0
    return Math.min(1, Math.max(0, (now - this.startedAt) / this.durationMs))
  }

  /** true exactly once per press, when the hold completes */
  poll(now: number): boolean {
    if (this.startedAt === null || this.fired) return false
    if (now - this.startedAt >= this.durationMs) {
      this.fired = true
      return true
    }
    return false
  }

  get pressed(): boolean {
    return this.startedAt !== null
  }
}
