// USB serial link and PX4 hardware in the loop (11 §7). Hand-written in the style of the generated types until the
// backend exports these models into schema.json; the integrator then regenerates types.ts and this file re-exports
// the generated names. Snapshot additions are read through the accessors below, so the window works with a backend
// that does not send them yet (and with one that names the link kind differently).
import type { Vehicle } from './types'

/** GET /serial/ports: one serial device of this computer. */
export interface SerialPort {
  /** /dev/ttyACM0, COM5 */
  device: string
  name: string | null
  description: string | null
  manufacturer: string | null
  vid: number | null
  pid: number | null
  serial_number: string | null
  /** VID / PID or description of a known flight controller USB interface (not a generic USB serial adapter) */
  autopilot: boolean
}

export interface SerialPorts {
  ports: SerialPort[]
}

/** POST /vehicles {serial}: "auto" tries 115200, 57600 and 921600 until a HEARTBEAT arrives. */
export type SerialBaud = number | 'auto'

/** The serial link of a vehicle in the snapshot; null on UDP. */
export interface SerialLinkView {
  device: string
  baud: number | null
}

export type HitlState = 'idle' | 'backing_up' | 'writing' | 'rebooting' | 'running' | 'restoring' | 'verifying' | 'restored' | 'failed'
export type HitlStepKey = 'confirm' | 'backup' | 'write' | 'reboot' | 'run' | 'restore' | 'verify'
export type HitlStepState = 'pending' | 'active' | 'done' | 'failed' | 'skipped'
export type HitlAction = 'start' | 'stop'

export interface HitlStep {
  key: HitlStepKey
  state: HitlStepState
  detail: string | null
  detail_zh: string | null
}

export interface HitlBackup {
  file: string
  count: number
  captured_at: number
}

export interface HitlMismatch {
  name: string
  expected: number | null
  actual: number | null
}

export interface HitlError {
  code: string
  message: string
  message_zh: string
}

export interface HitlRun {
  sensor_hz: number
  gps_hz: number
  actuator_frames: number
  steps: number
  /** wall clock seconds (UTC epoch) */
  since: number
}

/** GET / POST /vehicles/{id}/hitl. */
export interface HitlStatus {
  id: string
  available: boolean
  /** why it is not available (English); null when available */
  reason: string | null
  reason_zh: string | null
  state: HitlState
  steps: HitlStep[]
  backup: HitlBackup | null
  changed: { [name: string]: { from: number | null; to: number | null } | undefined }
  mismatches: HitlMismatch[]
  error: HitlError | null
  run: HitlRun | null
  /** a previous session's parameters still wait to be restored */
  pending_restore: boolean
}

/** The snapshot's view of a HITL session on a real aircraft. */
export interface VehicleHitlRef {
  state: HitlState
}

/** Vehicle with the 11 §7 snapshot additions (optional until the backend sends them). */
export type VehicleLinkExt = Vehicle & {
  serial?: SerialLinkView | null
  hitl?: VehicleHitlRef | null
  link_kind?: 'udp' | 'serial'
  transport?: 'udp' | 'serial'
}

/** udp or serial: the serial view, a link kind field, or a kind inside the link object. */
export function linkKind(v: Vehicle): 'udp' | 'serial' {
  const x = v as VehicleLinkExt
  if (x.serial) return 'serial'
  const kind = x.link_kind ?? x.transport ?? (x.link as { kind?: unknown } | null)?.kind ?? (typeof (x as { link: unknown }).link === 'string' ? (x as { link: unknown }).link : null)
  return kind === 'serial' ? 'serial' : 'udp'
}

export function serialOf(v: Vehicle): SerialLinkView | null {
  return (v as VehicleLinkExt).serial ?? null
}

export function hitlOf(v: Vehicle): VehicleHitlRef | null {
  return (v as VehicleLinkExt).hitl ?? null
}
