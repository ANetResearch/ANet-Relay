// Shared twin and simulator actions for the inspector, fleet card, stage card, command palette and gamepad layer.
import { create } from 'zustand'
import { api } from '@/api'
import { notify, notifyError } from '@/shell/notify'
import { getVehicle } from '@/store/selectors'
import type { Engine } from './model'
import { tt } from './strings'

/** POST /sims/{id}/reset: a twin restarts its firmware (the snapshot's sim.firmware.state shows the progress). */
export function resetSim(id: string): Promise<boolean> {
  const v = getVehicle(id)
  const label = v?.label ?? id
  const twin = v?.sim?.engine === 'sitl'
  return api.resetSim(id).then(
    () => {
      notify(tt(twin ? 'reset.twinSent' : 'reset.sent', { aircraft: label }))
      return true
    },
    (err: unknown) => {
      notifyError(err)
      return false
    },
  )
}

export interface SimDialogOpen {
  engine?: Engine
  /** "profile:<pid>" or "pack:<id>" to preselect */
  source?: string
}

/** The add-simulator dialog (11 §11), opened from the fleet panel, the command palette and the Lab. */
export const useSimDialog = create<{ open: boolean; preset: SimDialogOpen; setOpen(open: boolean, preset?: SimDialogOpen): void }>()((set) => ({
  open: false,
  preset: {},
  setOpen: (open, preset) => set((s) => ({ open, preset: preset ?? (open ? {} : s.preset) })),
}))

export function openSimDialog(preset?: SimDialogOpen): void {
  useSimDialog.getState().setOpen(true, preset ?? {})
}
