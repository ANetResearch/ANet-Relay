// Narrow selectors. Snapshots are structurally shared (./share), so a selector that returns a sub-object only
// re-renders its component when that sub-object changed. Derived lists use useShallow. Frame loops (3D, canvas)
// must not use these hooks: read useRelay.getState() inside the loop instead (CONTRACTS.md).
import { useMemo } from 'react'
import { useShallow } from 'zustand/react/shallow'
import type { Alert, Vehicle } from '@/api'
import { computeRedOwner, type RedOwner } from './red'
import { useRelay } from './relay'
import { useUi } from './ui'

const EMPTY: never[] = []

export const useConn = () => useRelay((s) => s.conn)
export const useHello = () => useRelay((s) => s.hello)
export const useHasFleet = () => useRelay((s) => s.fleet !== null)
export const useListen = () => useRelay((s) => s.fleet?.listen ?? null)
export const useScan = () => useRelay((s) => s.fleet?.scan ?? null)
export const useSessions = () => useRelay((s) => s.fleet?.sessions ?? EMPTY)
export const useSafetyVolume = () => useRelay((s) => s.fleet?.safety_volume ?? null)
export const usePads = () => useRelay((s) => s.inputs.pads)
export const useInputGate = () => useRelay((s) => s.inputs.gate)

/** Vehicle ids in snapshot order (real first is the backend's order; FleetPanel may re-sort). */
export const useVehicleIds = () => useRelay(useShallow((s) => (s.fleet?.vehicles ?? EMPTY).map((v) => v.id)))

/** Ids grouped as 02 §4.3: REAL above SIMULATED. */
export function useVehicleGroups(): { real: string[]; sim: string[] } {
  // a primitive key keeps the selector stable; the arrays are rebuilt only when membership or order changes
  const key = useRelay((s) => {
    const vs = s.fleet?.vehicles ?? EMPTY
    const real = vs.filter((v) => v.kind === 'real').map((v) => v.id)
    const sim = vs.filter((v) => v.kind === 'sim').map((v) => v.id)
    return JSON.stringify([real, sim])
  })
  return useMemo(() => {
    const [real, sim] = JSON.parse(key) as [string[], string[]]
    return { real, sim }
  }, [key])
}

export function findVehicle(vehicles: readonly Vehicle[] | undefined, id: string | null): Vehicle | null {
  if (!id || !vehicles) return null
  for (const v of vehicles) if (v.id === id) return v
  return null
}

export const useVehicle = (id: string | null): Vehicle | null => useRelay((s) => findVehicle(s.fleet?.vehicles, id))

/** Single field of one vehicle, e.g. useVehicleField(id, (v) => v.link.state). */
export function useVehicleField<T>(id: string | null, pick: (v: Vehicle) => T): T | null {
  return useRelay((s) => {
    const v = findVehicle(s.fleet?.vehicles, id)
    return v ? pick(v) : null
  })
}

export const useSelectedId = () => useUi((s) => s.selectedId)
export function useSelectedVehicle(): Vehicle | null {
  const id = useSelectedId()
  return useVehicle(id)
}

/** Non-reactive lookup for event handlers and loops. */
export function getVehicle(id: string | null): Vehicle | null {
  return findVehicle(useRelay.getState().fleet?.vehicles, id)
}

export const useAlerts = () => useRelay((s) => s.fleet?.alerts ?? (EMPTY as Alert[]))

/** Alerts not yet acknowledged (backend or locally), newest first. */
export function useOpenAlerts(level?: Alert['level']): Alert[] {
  return useRelay(
    useShallow((s) =>
      (s.fleet?.alerts ?? EMPTY)
        .filter((a) => !a.acked && !s.localAcks.has(a.id) && (!level || a.level === level))
        .sort((a, b) => b.since - a.since),
    ),
  )
}

export function useAlertsForVehicle(id: string | null): Alert[] {
  return useRelay(useShallow((s) => (id ? (s.fleet?.alerts ?? EMPTY).filter((a) => a.vehicle === id) : EMPTY)))
}

/** 02 §4.2 fleet summary: "6 aircraft · 2 armed · 1 lost". */
export function useFleetSummary(): { total: number; armed: number; lost: number; sims: number } {
  return useRelay(
    useShallow((s) => {
      const vs = s.fleet?.vehicles ?? EMPTY
      return {
        total: vs.length,
        armed: vs.filter((v) => v.armed).length,
        lost: vs.filter((v) => v.link.state === 'lost').length,
        sims: vs.filter((v) => v.kind === 'sim').length,
      }
    }),
  )
}

/** 02 §4.5 timeline title: "2 agent · 1 human · 3 idle". */
export function useControllerSummary(): { agent: number; human: number; idle: number } {
  return useRelay(
    useShallow((s) => {
      const vs = s.fleet?.vehicles ?? EMPTY
      return {
        agent: vs.filter((v) => v.controller === 'agent').length,
        human: vs.filter((v) => v.controller === 'human').length,
        idle: vs.filter((v) => v.controller === 'none').length,
      }
    }),
  )
}

/** The one owner of r500 on screen right now (02 §2.3). */
export function useRedOwner(): RedOwner {
  return useRelay((s) => computeRedOwner(s.fleet?.alerts, s.fleet?.vehicles, s.localAcks))
}

/** Vehicle possessed by the keyboard input source, if any. */
export const useKeyboardPossessed = () =>
  useRelay((s) => (s.fleet?.vehicles ?? EMPTY).find((v) => v.possession?.input === 'keyboard')?.id ?? null)
