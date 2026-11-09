// USB serial link and PX4 hardware in the loop (11 §7). The models come from the backend schema (types.ts); this
// file names the pieces the window uses and reads the snapshot additions through small accessors.
import type { HitlStatus, Vehicle, VehicleHitl } from './types'

export type { SerialPort, SerialPorts, SerialLinkView, SerialUsb, HitlStatus, HitlStep, HitlBackup, HitlChange, HitlMismatch, HitlError, HitlRun, HitlPose, HitlFirmware, VehicleHitl } from './types'

/** POST /vehicles {serial}: "auto" tries 115200, 57600 and 921600 until a HEARTBEAT arrives. */
export type SerialBaud = number | 'auto'

export type HitlState = HitlStatus['state']
export type HitlStepKey = HitlStatus['steps'][number]['key']
export type HitlStepState = HitlStatus['steps'][number]['state']
export type HitlAction = 'start' | 'stop'

/** The snapshot's view of a HITL session on a real aircraft. */
export type VehicleHitlRef = Pick<VehicleHitl, 'state'> & Partial<VehicleHitl>

/** Vehicle as the snapshot sends it (kept as a name for the earlier hand-written extension). */
export type VehicleLinkExt = Vehicle

/** udp or serial: the snapshot's transport (simulators count as udp for the connect and HITL paths). */
export function linkKind(v: Vehicle): 'udp' | 'serial' {
  return v.transport === 'serial' || v.serial ? 'serial' : 'udp'
}

export function serialOf(v: Vehicle) {
  return v.serial ?? null
}

export function hitlOf(v: Vehicle): VehicleHitl | null {
  return v.hitl ?? null
}
