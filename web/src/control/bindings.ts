// Controller binding rules (02 §7.3) and small decisions of the input engine, as pure functions over the fleet
// snapshot. Unit-tested in bindings.test.ts.
import type { Vehicle } from '@/api'

export const MAX_PADS = 4
/** A disconnected pad keeps its possession this long, waiting for the same controller to come back (02 §7.3). */
export const RECONNECT_GRACE_MS = 3000
export const HOLD_MS = 1000

type V = Pick<Vehicle, 'id' | 'kind' | 'label' | 'possession' | 'controller'>

/** Input source key: 'keyboard' | 'screen' | 'pad:1'..'pad:4'. */
export type SourceKey = 'keyboard' | 'screen' | `pad:${number}`

export function sourceOf(p: NonNullable<Vehicle['possession']>): SourceKey {
  return p.input === 'gamepad' ? `pad:${p.pad ?? 1}` : p.input
}

/** Keyboard and on-screen sticks are one window source: either may fly a vehicle possessed by the other. */
export function isWindowSource(s: SourceKey): boolean {
  return s === 'keyboard' || s === 'screen'
}

/** Vehicle currently possessed by `source`, if any. */
export function possessedBy<T extends V>(vehicles: readonly T[], source: SourceKey): T | null {
  for (const v of vehicles) if (v.possession && sourceOf(v.possession) === source) return v
  return null
}

/** Lowest free controller number 1..4, or null when four are in use. */
export function allocateIndex(used: Iterable<number>): number | null {
  const taken = new Set(used)
  for (let i = 1; i <= MAX_PADS; i++) if (!taken.has(i)) return i
  return null
}

/** Natural order of simulated aircraft: sim-2 before sim-10. */
export function simOrder(a: Pick<V, 'id'>, b: Pick<V, 'id'>): number {
  return a.id.localeCompare(b.id, undefined, { numeric: true })
}

/**
 * Auto-binding (02 §7.3): a newly connected controller possesses the lowest-numbered simulated aircraft that no
 * input possesses. Real aircraft are never bound automatically. We also skip sims an agent is flying: plugging in
 * a controller should not silently suspend an agent's work (possessing it stays one Options hold away).
 */
export function autoBindTarget<T extends V>(vehicles: readonly T[], exclude: ReadonlySet<string> = new Set()): T | null {
  const free = vehicles.filter((v) => v.kind === 'sim' && !v.possession && v.controller === 'none' && !exclude.has(v.id))
  free.sort(simOrder)
  return free[0] ?? null
}

export type OptionsDecision =
  | { action: 'release'; vehicle: string }
  | { action: 'possess'; vehicle: string }
  | { action: 'blocked'; reason: 'no_selection' }
  | { action: 'blocked'; reason: 'taken'; vehicle: string; by: SourceKey }

/**
 * Options held 1 s (02 §7.3): release when this pad possesses an aircraft, otherwise possess the selected one,
 * provided no other input source possesses it.
 */
export function optionsDecision(vehicles: readonly V[], pad: number, selected: string | null): OptionsDecision {
  const mine = possessedBy(vehicles, `pad:${pad}`)
  if (mine) return { action: 'release', vehicle: mine.id }
  const target = selected ? vehicles.find((v) => v.id === selected) : undefined
  if (!target) return { action: 'blocked', reason: 'no_selection' }
  if (target.possession) return { action: 'blocked', reason: 'taken', vehicle: target.id, by: sourceOf(target.possession) }
  return { action: 'possess', vehicle: target.id }
}

/** The aircraft a hold of Options would act on, for the hold progress line. */
export function optionsHoldVehicle(vehicles: readonly V[], pad: number, selected: string | null): string | null {
  const d = optionsDecision(vehicles, pad, selected)
  return d.action === 'release' || d.action === 'possess' ? d.vehicle : null
}

const STAB = ['STABILIZE', 'STABILIZED', 'MANUAL']
const ALTHOLD = ['ALT_HOLD', 'ALTCTL']

/** Triangle (02 §7.5): self-levelling <-> altitude hold, chosen from the modes the autopilot reports. */
export function modeToggleTarget(v: Pick<Vehicle, 'mode_class' | 'modes'>): string | null {
  const pick = (names: string[]) => names.find((n) => v.modes.includes(n)) ?? null
  return v.mode_class === 'manual' ? pick(ALTHOLD) : pick(STAB)
}

/** Live link: sticks are sent only while the link is ok or delayed (03 §5.1, H3). */
export function linkUsable(state: Vehicle['link']['state']): boolean {
  return state === 'ok' || state === 'delayed'
}

/** Modes in which the autopilot reads pilot sticks (03 §5.4), by class. */
export function acceptsSticks(modeClass: Vehicle['mode_class']): boolean {
  return modeClass === 'manual' || modeClass === 'althold' || modeClass === 'position'
}
