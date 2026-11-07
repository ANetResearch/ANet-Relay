// The run of one aircraft (05 §4.4): GET /vehicles/{id}/procedure, every 500 ms while the snapshot's brief says a run
// is active (the step view carries the 5 Hz live series), once otherwise, and again whenever the brief changes (a step
// ends, a prompt opens). 404 procedure_not_found means the aircraft never ran one: the hook returns null.
import * as React from 'react'
import { api, RelayApiError, type ProcedureCatalogue, type ProcedureRun } from '@/api'
import { useVehicleField } from '@/store/selectors'
import { usePoll } from '@/inspector/usePoll'
import { isActive } from './model'

const ACTIVE_MS = 500

export function useBrief(vehicleId: string) {
  return useVehicleField(vehicleId, (v) => v.procedure ?? null)
}

export function useProcedure(vehicleId: string): { run: ProcedureRun | null; loading: boolean; error: unknown; reload(): void; set(run: ProcedureRun): void } {
  const brief = useBrief(vehicleId)
  const active = isActive(brief?.state)
  const key = `${vehicleId}|${brief?.run_id ?? '-'}|${brief?.state ?? '-'}|${brief?.step ?? '-'}`
  const poll = usePoll(
    async () => {
      try {
        return await api.procedure(vehicleId)
      } catch (e) {
        if (e instanceof RelayApiError && e.status === 404) return null
        throw e
      }
    },
    active ? ACTIVE_MS : 0,
    key,
  )
  // an answer or a start returns the new view at once; it shows until the next poll result replaces it
  const [local, setLocal] = React.useState<{ vid: string; base: ProcedureRun | null; run: ProcedureRun } | null>(null)
  // the key changes with every step: keep the last view while the next request is in flight (no empty flash)
  const polled = poll.data ?? null
  const [sticky, setSticky] = React.useState<{ vid: string; run: ProcedureRun | null }>({ vid: vehicleId, run: null })
  const settled = !poll.loading || polled !== null
  if (settled && (sticky.vid !== vehicleId || sticky.run !== polled)) setSticky({ vid: vehicleId, run: polled })
  const base = sticky.vid === vehicleId ? (settled ? polled : sticky.run) : null
  const run = local && local.vid === vehicleId && local.base === base ? local.run : base
  const set = React.useCallback((r: ProcedureRun) => setLocal({ vid: vehicleId, base, run: r }), [vehicleId, base])
  return { run, loading: poll.loading, error: poll.error, reload: poll.reload, set }
}

let catalogue: Promise<ProcedureCatalogue> | null = null

/** The catalogue, fetched once per window (it is static data). */
export function useCatalogue(): ProcedureCatalogue | null {
  const [cat, setCat] = React.useState<ProcedureCatalogue | null>(null)
  React.useEffect(() => {
    let alive = true
    catalogue ??= api.procedures().catch((e) => {
      catalogue = null
      throw e
    })
    catalogue.then(
      (c) => alive && setCat(c),
      () => undefined,
    )
    return () => {
      alive = false
    }
  }, [])
  return cat
}
