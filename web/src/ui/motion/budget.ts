// Resident loop budget (ADR-029, recipes.css): at most LOOP_SLOTS looping animations run on screen at once. A
// component asks for a slot while it is mounted; the first LOOP_SLOTS requests (in order) hold one, later ones show
// their static form until a slot frees up. Reduced and off motion never grant a slot.
import * as React from 'react'
import { useMotionTier } from './tier'

export const LOOP_SLOTS = 2

let queue: number[] = []
let nextId = 1
const listeners = new Set<() => void>()
const emit = () => {
  for (const l of listeners) l()
}

/** A fresh request id (not yet queued). */
export function newLoopId(): number {
  return nextId++
}

/** Queues `id` for a slot. */
export function joinLoop(id: number): void {
  if (queue.includes(id)) return
  queue = [...queue, id]
  emit()
}

/** Joins the queue with a fresh id; returns it. */
export function acquireLoop(): number {
  const id = newLoopId()
  joinLoop(id)
  return id
}

export function releaseLoop(id: number): void {
  if (!queue.includes(id)) return
  queue = queue.filter((x) => x !== id)
  emit()
}

/** True while `id` is among the first LOOP_SLOTS requests. */
export function holdsLoop(id: number): boolean {
  const i = queue.indexOf(id)
  return i >= 0 && i < LOOP_SLOTS
}

export function subscribeLoops(l: () => void): () => void {
  listeners.add(l)
  return () => {
    listeners.delete(l)
  }
}

/** A loop slot for as long as `wanted` holds and the component is mounted (false in reduced and off motion). */
export function useLoopSlot(wanted = true): boolean {
  const tier = useMotionTier()
  const allowed = wanted && tier !== 'reduced' && tier !== 'off'
  const [id] = React.useState(newLoopId)
  React.useEffect(() => {
    if (!allowed) return
    joinLoop(id)
    return () => releaseLoop(id)
  }, [allowed, id])
  const held = React.useSyncExternalStore(subscribeLoops, () => holdsLoop(id), () => false)
  return allowed && held
}
