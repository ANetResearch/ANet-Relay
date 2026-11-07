// Fleet ordering (02 §4.3): REAL above SIMULATED, each group sorted by the chosen key. Pure, unit-tested.
import type { Vehicle } from '@/api'

export type SortKey = 'name' | 'battery' | 'link' | 'controller'
export const SORT_KEYS: readonly SortKey[] = ['name', 'battery', 'link', 'controller']

type V = Pick<Vehicle, 'id' | 'label' | 'battery' | 'link' | 'controller' | 'kind'>

const collator = typeof Intl !== 'undefined' ? new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' }) : null
const byName = (a: V, b: V) => (collator ? collator.compare(a.label, b.label) : a.label.localeCompare(b.label)) || a.id.localeCompare(b.id)

/** Worst link first: the aircraft that needs attention leads the list. */
const LINK_RANK: Record<Vehicle['link']['state'], number> = { lost: 0, delayed: 1, waiting: 2, ok: 3, offline: 4 }
/** Human first, then agent, then idle: who is flying what. */
const CTL_RANK: Record<Vehicle['controller'], number> = { human: 0, agent: 1, none: 2 }

/** Lowest battery first; unknown battery last. */
function batteryValue(v: V): number {
  const b = v.battery
  if (typeof b.remaining_pct === 'number') return b.remaining_pct
  if (typeof b.voltage === 'number' && typeof b.cells === 'number' && b.cells > 0) return ((b.voltage / b.cells - 3.3) / 0.9) * 100
  return Number.POSITIVE_INFINITY
}

export function compareVehicles(key: SortKey): (a: V, b: V) => number {
  switch (key) {
    case 'battery':
      return (a, b) => batteryValue(a) - batteryValue(b) || byName(a, b)
    case 'link':
      return (a, b) => LINK_RANK[a.link.state] - LINK_RANK[b.link.state] || byName(a, b)
    case 'controller':
      return (a, b) => CTL_RANK[a.controller] - CTL_RANK[b.controller] || byName(a, b)
    default:
      return byName
  }
}

export function groupAndSort<T extends V>(vehicles: readonly T[], key: SortKey): { real: T[]; sim: T[] } {
  const cmp = compareVehicles(key)
  return {
    real: vehicles.filter((v) => v.kind === 'real').sort(cmp),
    sim: vehicles.filter((v) => v.kind === 'sim').sort(cmp),
  }
}
