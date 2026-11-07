// Motion tier (02 §18): html[data-motion] = "full" | "reduced" | "off". full unless the OS asks for reduced motion;
// `?motion=off` (dev and screenshots) freezes every transition at its end state. styles/motion/tiers.css reads it.
import { useSyncExternalStore } from 'react'

export type MotionTier = 'full' | 'lite' | 'reduced' | 'off'

const mq = typeof matchMedia === 'function' ? matchMedia('(prefers-reduced-motion: reduce)') : null
let forced: MotionTier | null = null
let current: MotionTier = 'full'
const listeners = new Set<() => void>()

function apply(): void {
  const next: MotionTier = forced ?? (mq?.matches ? 'reduced' : 'full')
  if (next === current && typeof document !== 'undefined' && document.documentElement.dataset.motion === next) return
  current = next
  if (typeof document !== 'undefined') document.documentElement.dataset.motion = next
  for (const l of listeners) l()
}

export function initMotionTier(search: string): void {
  const v = new URLSearchParams(search).get('motion')
  forced = v === 'off' || v === 'reduced' || v === 'full' ? v : null
  mq?.addEventListener('change', apply)
  apply()
}

export function getMotionTier(): MotionTier {
  return current
}

/** true when transitions should be skipped (reduced or off) */
export function motionReduced(): boolean {
  return current === 'reduced' || current === 'off'
}

export function useMotionTier(): MotionTier {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    () => current,
    () => current,
  )
}
