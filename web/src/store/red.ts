// Single-red rule (02 §2.3). Every component that might paint r500 asks useRedOwner() and nothing else.
//   1. an unacknowledged critical alert exists -> only critical alerts are red; ARMED becomes bold g50
//   2. otherwise, if any aircraft is armed     -> only ARMED text and card bars are red
//   3. otherwise no red on screen (the boot logo excepted)
import type { Alert, Vehicle } from '@/api'

export type RedOwner = 'critical' | 'armed' | 'none'

export function computeRedOwner(
  alerts: readonly Pick<Alert, 'id' | 'level' | 'acked'>[] | undefined,
  vehicles: readonly Pick<Vehicle, 'armed'>[] | undefined,
  localAcks: ReadonlySet<string> = new Set(),
): RedOwner {
  if (alerts?.some((a) => a.level === 'critical' && !a.acked && !localAcks.has(a.id))) return 'critical'
  if (vehicles?.some((v) => v.armed)) return 'armed'
  return 'none'
}

/** Treatment of an ARMED mark (text, card bar, nose marker, timeline line) under the current owner. */
export function armedTone(owner: RedOwner): 'red' | 'strong' {
  return owner === 'armed' ? 'red' : 'strong'
}

/** Treatment of a critical-alert mark (alert bar, card bar, stage label, battery) under the current owner. */
export function criticalTone(owner: RedOwner): 'red' | 'strong' {
  return owner === 'critical' ? 'red' : 'strong'
}
