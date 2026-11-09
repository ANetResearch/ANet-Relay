// View models of the firmware twin, runtime pack, serial and HITL surfaces (11 §8.5, §11). Pure functions over the
// API shapes, no React, so they are unit-tested in node (twin.test.ts). Copy keys refer to ./strings.
import type { AutopilotPack, FirmwareMatch, FrameBody, HitlState, HitlStatus, HitlStep, HitlStepKey, HomeBody, SerialPort, TwinProfileSummary, Vehicle, VehicleFirmware } from '@/api'
import { hitlOf, linkKind, RelayApiError } from '@/api'
import type { TwinKey } from './strings'

export type MatchLevel = FirmwareMatch['match']
export type Family = 'ardupilot' | 'px4'
export const MATCH_LEVELS: readonly MatchLevel[] = ['exact', 'version', 'series', 'nearest', 'none']
/** A reason with its copy key and placeholders. */
export type Because = { key: TwinKey; params?: Record<string, string | number> }

// ---------------------------------------------------------------- firmware names

/** ArduCopter for ArduPilot's copter build (the only vehicle a twin flies), PX4 for PX4. */
export function familyName(family: Family | null | undefined, vehicle?: string | null): string {
  if (family === 'px4') return 'PX4'
  if (family !== 'ardupilot') return ''
  const v = (vehicle ?? 'copter').toLowerCase()
  return v === 'copter' ? 'ArduCopter' : v === 'plane' ? 'ArduPlane' : v === 'rover' ? 'ArduRover' : 'ArduPilot'
}

/** "ArduCopter 4.6.3", "PX4 1.16.2". */
export function firmwareTitle(family: Family | null | undefined, version: string | null | undefined, vehicle?: string | null): string {
  return [familyName(family, vehicle), version ?? ''].filter(Boolean).join(' ')
}

/** The family of a vehicle: firmware identity first, then the snapshot's autopilot name. */
export function vehicleFamily(v: Pick<Vehicle, 'firmware_info' | 'autopilot'>, fw?: VehicleFirmware | null): Family | null {
  return fw?.identity.family ?? v.firmware_info?.family ?? (v.autopilot === 'PX4' ? 'px4' : v.autopilot === 'ArduPilot' ? 'ardupilot' : null)
}

/** Numeric comparison of dotted versions ("4.10.1" after "4.9.0"); non-numeric parts compare as text. */
export function compareVersions(a: string, b: string): number {
  const pa = a.replace(/^v/i, '').split(/[.-]/)
  const pb = b.replace(/^v/i, '').split(/[.-]/)
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const x = pa[i] ?? '0'
    const y = pb[i] ?? '0'
    const nx = Number(x)
    const ny = Number(y)
    const d = Number.isFinite(nx) && Number.isFinite(ny) ? nx - ny : x.localeCompare(y)
    if (d) return d
  }
  return 0
}

// ---------------------------------------------------------------- engine badge (11 §8.5)

export interface EngineBadge {
  kind: 'twin' | 'hitl'
  /** "ArduCopter 4.6.3" */
  firmware: string
  /** twins against their profile; null without a profile and for HITL */
  match: MatchLevel | null
  /** the twin process state, or the HITL session state */
  state: string
}

const HITL_SESSION: readonly HitlState[] = ['backing_up', 'writing', 'rebooting', 'running', 'restoring', 'verifying']

/** The badge of a firmware twin or of a real aircraft in a HITL session; null for the fast simulator and plain aircraft. */
export function engineBadge(v: Vehicle): EngineBadge | null {
  const hitl = hitlOf(v)
  const engine = (v.sim?.engine ?? null) as string | null
  if ((hitl && HITL_SESSION.includes(hitl.state)) || engine === 'hitl') {
    const fi = v.firmware_info
    return { kind: 'hitl', firmware: firmwareTitle(fi?.family ?? 'px4', fi?.version, fi?.vehicle), match: null, state: hitl?.state ?? 'running' }
  }
  const f = v.sim?.firmware
  if (v.kind === 'sim' && engine === 'sitl' && f) return { kind: 'twin', firmware: firmwareTitle(f.family, f.version), match: f.match, state: f.state }
  return null
}

/** "固件孪生 ArduCopter 4.6.3 · 精确匹配", "硬件在环 PX4 1.16.2". */
export function engineBadgeText(b: EngineBadge, tt: (k: TwinKey) => string): string {
  const head = `${tt(b.kind === 'twin' ? 'eng.twin' : 'eng.hitl')} ${b.firmware}`.trim()
  return b.kind === 'twin' && b.match ? `${head} · ${tt(`match.${b.match}`)}` : head
}

// ---------------------------------------------------------------- simulator reset (v0.5.4 operator request)

/** Disarmed or crashed simulators reset (the backend allows armed and crashed, fleet.py reset_sim). */
export function canResetSim(v: Pick<Vehicle, 'kind' | 'armed' | 'crashed'> | null | undefined): boolean {
  return Boolean(v && v.kind === 'sim' && (!v.armed || v.crashed))
}

export function resetBlock(v: Pick<Vehicle, 'kind' | 'armed' | 'crashed'>): Because | null {
  if (v.kind !== 'sim') return { key: 'reset.block.sim' }
  if (v.armed && !v.crashed) return { key: 'reset.block.armed' }
  return null
}

/** A twin's firmware is (re)starting: the reset is in progress until the twin runs again. */
export function twinRestarting(v: Pick<Vehicle, 'sim'>): boolean {
  const s = v.sim?.firmware?.state
  return s === 'restarting' || s === 'booting' || s === 'new'
}

// ---------------------------------------------------------------- runtime packs (11 §3)

/** Decimal megabytes as the pack sizes are quoted ("7.2 MB"). */
export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return '—'
  if (n < 1000) return `${Math.round(n)} B`
  if (n < 1e6) return `${(n / 1e3).toFixed(n < 1e4 ? 1 : 0)} kB`
  if (n < 1e9) return `${(n / 1e6).toFixed(n < 1e8 ? 1 : 0)} MB`
  return `${(n / 1e9).toFixed(1)} GB`
}

/** ArduPilot first, newest version first; bundled before imported at the same version. */
export function sortPacks(packs: readonly AutopilotPack[]): AutopilotPack[] {
  return [...packs].sort(
    (a, b) => (a.family === b.family ? 0 : a.family === 'ardupilot' ? -1 : 1) || compareVersions(b.version, a.version) || (a.origin === b.origin ? 0 : a.origin === 'bundled' ? -1 : 1) || a.id.localeCompare(b.id),
  )
}

/** Why a pack cannot be deleted here; null when it can. */
export function packDeleteBlock(p: Pick<AutopilotPack, 'origin' | 'twins'>, operator: boolean): Because | null {
  if (p.origin === 'bundled') return { key: 'packs.del.bundled' }
  if (p.twins > 0) return { key: 'packs.del.inUse', params: { n: p.twins } }
  if (!operator) return { key: 'packs.del.operator' }
  return null
}

export const isSha256 = (s: string) => /^[0-9a-f]{64}$/i.test(s.trim())

/** Why the import form cannot be sent; null when it can. */
export function importBlock(path: string, sha256: string, operator: boolean): Because | null {
  if (!operator) return { key: 'packs.import.operator' }
  if (!path.trim()) return { key: 'packs.import.needPath' }
  if (sha256.trim() && !isSha256(sha256)) return { key: 'packs.import.badSha' }
  return null
}

// ---------------------------------------------------------------- frames (11 §8.1)

export type FramePreset = 'micro-120' | 'small-250' | 'large-450'
export const FRAME_PRESETS: readonly FramePreset[] = ['micro-120', 'small-250', 'large-450']
export interface FrameValues {
  mass_kg: number
  wheelbase_m: number
  prop_in: number
  cells: number
  capacity_mah: number
}
/** Initial values of the presets (backend sim/frame.py PRESETS). */
export const PRESET_VALUES: Record<FramePreset, FrameValues> = {
  'micro-120': { mass_kg: 0.12, wheelbase_m: 0.25, prop_in: 5.1, cells: 3, capacity_mah: 850 },
  'small-250': { mass_kg: 0.42, wheelbase_m: 0.25, prop_in: 5, cells: 3, capacity_mah: 850 },
  'large-450': { mass_kg: 1.5, wheelbase_m: 0.45, prop_in: 10, cells: 4, capacity_mah: 5000 },
}
/** Validation ranges (backend sim/frame.py LIMITS). */
export const FRAME_LIMITS: Record<keyof FrameValues, [number, number]> = {
  mass_kg: [0.05, 30],
  wheelbase_m: [0.08, 1.5],
  prop_in: [1.5, 32],
  cells: [1, 14],
  capacity_mah: [50, 100000],
}
export const FRAME_FIELDS: readonly (keyof FrameValues)[] = ['mass_kg', 'wheelbase_m', 'prop_in', 'cells', 'capacity_mah']

export const isPreset = (s: string): s is FramePreset => (FRAME_PRESETS as readonly string[]).includes(s)

/** Why a simulator's frame cannot change now (11 §6.4: disarmed and on the ground). */
export function frameBlock(v: Pick<Vehicle, 'armed' | 'landed' | 'kind'>): Because | null {
  if (v.kind !== 'sim') return { key: 'reset.block.sim' }
  if (v.armed) return { key: 'frame.block.armed' }
  if (v.landed === 'air' || v.landed === 'takeoff' || v.landed === 'landing') return { key: 'frame.block.air' }
  return null
}

export type FrameForm = { preset: FramePreset } & Record<keyof FrameValues, string>

export function frameForm(preset: FramePreset, values: Partial<FrameValues> = {}): FrameForm {
  const v = { ...PRESET_VALUES[preset], ...values }
  return { preset, mass_kg: String(v.mass_kg), wheelbase_m: String(v.wheelbase_m), prop_in: String(v.prop_in), cells: String(v.cells), capacity_mah: String(v.capacity_mah) }
}

/** A field outside its range or not a number; cells must be whole. */
export function frameErrors(f: Partial<FrameForm>): Partial<Record<keyof FrameValues, Because>> {
  const out: Partial<Record<keyof FrameValues, Because>> = {}
  for (const k of FRAME_FIELDS) {
    const raw = f[k]
    if (raw === undefined) continue
    const n = Number(raw)
    const [lo, hi] = FRAME_LIMITS[k]
    if (raw.trim() === '' || !Number.isFinite(n) || n < lo || n > hi || (k === 'cells' && !Number.isInteger(n))) out[k] = { key: 'frame.err.range', params: { lo, hi } }
  }
  return out
}

/** The PUT /sims/{id}/frame or POST /sims frame body: the preset plus the values that differ from `base`. */
export function frameBody(f: Partial<FrameForm> & { preset: FramePreset }, base: Partial<FrameValues> = PRESET_VALUES[f.preset]): FrameBody {
  const body: FrameBody = { preset: f.preset }
  for (const k of FRAME_FIELDS) {
    const raw = f[k]
    if (raw === undefined || raw.trim() === '') continue
    const n = Number(raw)
    const was = base[k] ?? PRESET_VALUES[f.preset][k]
    if (Number.isFinite(n) && Math.abs(n - was) > 1e-9) body[k] = k === 'cells' ? Math.round(n) : n
  }
  return body
}

// ---------------------------------------------------------------- add simulator dialog (11 §11)

export type Engine = 'relay' | 'sitl'

export interface SimForm {
  engine: Engine
  count: string
  /** fast simulator only */
  autopilot: Family
  /** twin source: "profile:<pid>" or "pack:<id>" */
  source: string
  /** "profile": the profile's own frame (twin with a profile only) */
  frame: FramePreset | 'profile'
  mass: string
  wheelbase: string
  homeOn: boolean
  lat: string
  lon: string
  alt: string
}

export type SimField = 'count' | 'source' | 'mass' | 'wheelbase' | 'home'

export function defaultSimForm(engine: Engine = 'relay'): SimForm {
  const p = PRESET_VALUES['micro-120']
  return { engine, count: '1', autopilot: 'ardupilot', source: '', frame: 'micro-120', mass: String(p.mass_kg), wheelbase: String(p.wheelbase_m), homeOn: false, lat: '', lon: '', alt: '0' }
}

/** Choosing a preset starts from its values (11 §5). */
export function withFrame(f: SimForm, frame: SimForm['frame']): SimForm {
  if (frame === 'profile') return { ...f, frame }
  const p = PRESET_VALUES[frame]
  return { ...f, frame, mass: String(p.mass_kg), wheelbase: String(p.wheelbase_m) }
}

/** A new source: a profile brings its own frame; a pack falls back to a preset. */
export function withSource(f: SimForm, source: string): SimForm {
  if (source.startsWith('profile:')) return { ...f, source, frame: 'profile' }
  return f.frame === 'profile' ? withFrame({ ...f, source }, 'micro-120') : { ...f, source }
}

const inRange = (raw: string, lo: number, hi: number) => {
  const n = Number(raw)
  return raw.trim() !== '' && Number.isFinite(n) && n >= lo && n <= hi
}

export function validateSimForm(f: SimForm): Partial<Record<SimField, Because>> {
  const out: Partial<Record<SimField, Because>> = {}
  const n = Number(f.count)
  if (!Number.isInteger(n) || n < 1 || n > 8) out.count = { key: 'sim.err.count' }
  if (f.engine === 'sitl' && !f.source) out.source = { key: 'sim.err.source' }
  if (f.frame !== 'profile') {
    if (!inRange(f.mass, ...FRAME_LIMITS.mass_kg)) out.mass = { key: 'frame.err.range', params: { lo: FRAME_LIMITS.mass_kg[0], hi: FRAME_LIMITS.mass_kg[1] } }
    if (!inRange(f.wheelbase, ...FRAME_LIMITS.wheelbase_m)) out.wheelbase = { key: 'frame.err.range', params: { lo: FRAME_LIMITS.wheelbase_m[0], hi: FRAME_LIMITS.wheelbase_m[1] } }
  }
  if (f.engine === 'sitl' && f.homeOn && !(inRange(f.lat, -89, 89) && inRange(f.lon, -180, 180) && inRange(f.alt, -500, 9000))) out.home = { key: 'sim.err.home' }
  return out
}

/** The POST /sims body (11 §10). A fast simulator on the default frame sends what v0.5.3 sent. */
export function simBody(f: SimForm): Record<string, unknown> {
  const count = Number(f.count)
  const body: Record<string, unknown> = { count }
  const frame = f.frame === 'profile' ? null : frameBody({ preset: f.frame, mass_kg: f.mass, wheelbase_m: f.wheelbase })
  const plain = frame && Object.keys(frame).length === 1 && frame.preset === 'micro-120'
  if (f.engine === 'relay') {
    body.autopilot = f.autopilot
    if (frame && !plain) body.frame = frame
    return body
  }
  body.engine = 'sitl'
  if (f.source.startsWith('profile:')) body.profile = f.source.slice(8)
  else if (f.source.startsWith('pack:')) body.pack = f.source.slice(5)
  if (frame) body.frame = frame
  if (f.homeOn) {
    const home: HomeBody = { lat: Number(f.lat), lon: Number(f.lon), alt_m: Number(f.alt) }
    body.home = home
  }
  return body
}

export interface Recommendation {
  value: string
  /** the aircraft label the recommendation matches */
  vehicle: string
  match: MatchLevel
}

const MATCH_RANK: Record<MatchLevel, number> = { exact: 0, version: 1, series: 2, nearest: 3, none: 4 }

/**
 * The recommended twin source: a profile captured from a connected aircraft (the selected one first, then the
 * best match, then the newest), else the pack the identity match recommends for the selected aircraft.
 */
export function recommendSource(
  profiles: readonly TwinProfileSummary[],
  connected: readonly { id: string; label: string }[],
  selectedId: string | null,
  firmware: { vehicle: { id: string; label: string }; match: FirmwareMatch } | null,
): Recommendation | null {
  const byId = new Map(connected.map((c) => [c.id, c]))
  const ranked = profiles
    .filter((p) => byId.has(p.source.vehicle) && p.pack)
    .sort((a, b) => Number(b.source.vehicle === selectedId) - Number(a.source.vehicle === selectedId) || MATCH_RANK[a.match] - MATCH_RANK[b.match] || b.updated_at - a.updated_at)
  if (ranked.length) {
    const p = ranked[0]
    return { value: `profile:${p.id}`, vehicle: byId.get(p.source.vehicle)?.label ?? p.source.label, match: p.match }
  }
  if (firmware?.match.recommended) return { value: `pack:${firmware.match.recommended}`, vehicle: firmware.vehicle.label, match: firmware.match.match }
  return null
}

export interface SourceOption {
  value: string
  kind: 'profile' | 'pack'
  /** "Pixracer 小机 · ArduCopter 4.6.3", "ArduCopter 4.7.2" */
  label: string
  family: Family
  disabled: boolean
  /** why a pack cannot run here, in both languages */
  problem: { en: string; zh: string } | null
}

/** Profiles (newest first) then usable packs (ArduPilot first, newest first); the recommended source leads. */
export function sourceOptions(profiles: readonly TwinProfileSummary[], packs: readonly AutopilotPack[], rec: Recommendation | null): SourceOption[] {
  const out: SourceOption[] = [
    ...[...profiles]
      .sort((a, b) => b.updated_at - a.updated_at)
      .map((p): SourceOption => ({ value: `profile:${p.id}`, kind: 'profile', label: `${p.name} · ${firmwareTitle(p.firmware.family, p.firmware.version, p.firmware.vehicle)}`, family: p.firmware.family, disabled: !p.pack, problem: null })),
    ...sortPacks(packs).map((p): SourceOption => ({
      value: `pack:${p.id}`,
      kind: 'pack',
      label: firmwareTitle(p.family, p.version, p.vehicle),
      family: p.family,
      disabled: !p.usable,
      problem: p.usable ? null : { en: p.problem ?? '', zh: p.problem_zh ?? p.problem ?? '' },
    })),
  ]
  if (!rec) return out
  const i = out.findIndex((o) => o.value === rec.value)
  if (i > 0) out.unshift(...out.splice(i, 1))
  return out
}

/** The recommended source, else the first usable profile, else the newest usable ArduPilot pack. */
export function defaultSource(options: readonly SourceOption[], rec: Recommendation | null): string {
  if (rec && options.some((o) => o.value === rec.value && !o.disabled)) return rec.value
  return (options.find((o) => o.kind === 'profile' && !o.disabled) ?? options.find((o) => o.kind === 'pack' && !o.disabled && o.family === 'ardupilot') ?? options.find((o) => !o.disabled))?.value ?? ''
}

// ---------------------------------------------------------------- twin errors (11 §6.3, §10)

const ERROR_TITLES: Record<string, TwinKey> = {
  twin_start_failed: 'err.twin_start_failed',
  twin_limit: 'err.twin_limit',
  pack_missing: 'err.pack_missing',
  pack_unsupported: 'err.pack_unsupported',
  frame_unsupported: 'err.frame_unsupported',
  twin_unsupported: 'err.twin_unsupported',
  profile_not_found: 'err.profile_not_found',
  params_incomplete: 'err.params_incomplete',
  serial_unavailable: 'err.serial_unavailable',
  hitl_unavailable: 'err.hitl_unavailable',
  hitl_restore_mismatch: 'err.hitl_restore_mismatch',
}

export interface TwinErrorView {
  code: string
  title: TwinKey
  /** the backend's message in the window language */
  message: string
  /** the firmware's last output lines (twin_start_failed) */
  lines: string[]
  /** simulators the same request created before the failure */
  added: string[]
}

export function twinErrorView(err: unknown, lang: 'en' | 'zh'): TwinErrorView {
  if (err instanceof RelayApiError) {
    const d = (err.detail && typeof err.detail === 'object' ? err.detail : {}) as { lines?: unknown; added?: unknown }
    const strings = (x: unknown) => (Array.isArray(x) ? x.filter((s): s is string => typeof s === 'string') : [])
    return { code: err.code, title: ERROR_TITLES[err.code] ?? 'err.generic', message: err.text(lang), lines: strings(d.lines), added: strings(d.added) }
  }
  return { code: 'error', title: 'err.generic', message: err instanceof Error ? err.message : String(err), lines: [], added: [] }
}

// ---------------------------------------------------------------- real aircraft firmware block (11 §4, §5)

/** Why a profile cannot be captured yet; null when it can. */
export function captureBlock(fw: VehicleFirmware | null): Because | null {
  if (!fw) return { key: 'fw.loading' }
  if (fw.kind !== 'real') return { key: 'fw.block.real' }
  if (!fw.identity.identified) return { key: 'fw.block.unidentified' }
  if (!fw.params.complete) return { key: 'fw.block.params', params: { received: fw.params.received, total: fw.params.total } }
  return null
}

/** Why a twin of this aircraft cannot start; null when it can (capturing first when there is no profile). */
export function startBlock(fw: VehicleFirmware | null, profile: TwinProfileSummary | null): Because | null {
  if (fw?.frame_problem) return { key: 'fw.block.frame' }
  if (profile) return profile.pack ? null : { key: 'fw.block.noPack' }
  const c = captureBlock(fw)
  if (c) return c
  if (!fw?.match.recommended) return { key: 'fw.block.noPack' }
  return null
}

/** The newest profile captured from this aircraft. */
export function profileOf(profiles: readonly TwinProfileSummary[], vehicleId: string): TwinProfileSummary | null {
  return [...profiles].filter((p) => p.source.vehicle === vehicleId).sort((a, b) => b.updated_at - a.updated_at)[0] ?? null
}

/** Whether a profile's source aircraft is connected (recapture is offered then). */
export function sourceConnected(vehicles: readonly Vehicle[], sourceId: string | null | undefined): Vehicle | null {
  if (!sourceId) return null
  const v = vehicles.find((x) => x.id === sourceId)
  return v && v.kind === 'real' && (v.link.state === 'ok' || v.link.state === 'delayed') ? v : null
}

export type HitlEntry = { mode: 'button'; block: Because | null } | { mode: 'note'; note: Because } | { mode: 'none' }

/** HITL in the firmware block: PX4 gets the button (USB serial required), ArduPilot the reason and the twin. */
export function hitlEntry(v: Vehicle, fw: VehicleFirmware | null): HitlEntry {
  if (v.kind !== 'real') return { mode: 'none' }
  const family = vehicleFamily(v, fw)
  if (family === 'ardupilot') return { mode: 'note', note: { key: 'hitl.ardupilot' } }
  if (family !== 'px4') return { mode: 'none' }
  if (hitlOf(v) && HITL_SESSION.includes(hitlOf(v)!.state)) return { mode: 'button', block: null }
  return { mode: 'button', block: linkKind(v) === 'serial' ? null : { key: 'hitl.block.serial' } }
}

// ---------------------------------------------------------------- HITL session (11 §7.2)

export const HITL_STEPS: readonly HitlStepKey[] = ['confirm', 'backup', 'write', 'reboot', 'run', 'restore', 'verify']

/** Every step in order; a step the backend has not reported yet is pending. */
export function hitlSteps(status: Pick<HitlStatus, 'steps'> | null): HitlStep[] {
  const got = new Map((status?.steps ?? []).map((s) => [s.key, s]))
  return HITL_STEPS.map((key) => got.get(key) ?? { key, state: 'pending', detail: '', detail_zh: '' })
}

export const hitlBusy = (s: HitlState) => s === 'backing_up' || s === 'writing' || s === 'rebooting' || s === 'restoring' || s === 'verifying'
export const hitlActive = (s: HitlState) => hitlBusy(s) || s === 'running'
export const hitlCanStart = (st: Pick<HitlStatus, 'available' | 'state' | 'pending_restore'>) =>
  st.available && !st.pending_restore && (st.state === 'idle' || st.state === 'restored' || st.state === 'failed')
/** Stop while it sets up or runs; also restores a previous session's parameters still pending. */
export const hitlCanStop = (st: Pick<HitlStatus, 'state' | 'pending_restore'>) =>
  st.state === 'backing_up' || st.state === 'writing' || st.state === 'rebooting' || st.state === 'running' || (st.pending_restore && !hitlBusy(st.state))
export const hitlPollMs = (s: HitlState | null) => (s && hitlActive(s) ? 500 : 2000)

export function changedRows(changed: HitlStatus['changed']): { name: string; from: number | null; to: number | null }[] {
  return Object.entries(changed)
    .filter((e): e is [string, { from: number; to: number }] => e[1] !== undefined)
    .map(([name, c]) => ({ name, from: c.from, to: c.to }))
    .sort((a, b) => a.name.localeCompare(b.name))
}

/** Parameter values as the firmware stores them: integers plain, reals to at most 6 significant digits. */
export function paramValue(x: number | null | undefined): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return '—'
  return Number.isInteger(x) ? String(x) : String(Number(x.toPrecision(6)))
}

// ---------------------------------------------------------------- serial ports (11 §7.1)

export const BAUDS: readonly ('auto' | number)[] = ['auto', 57600, 115200, 230400, 460800, 921600]

/** Flight controllers first, then by device name with numbers in order (ttyACM2 before ttyACM10). */
export function sortPorts(ports: readonly SerialPort[]): SerialPort[] {
  return [...ports].sort((a, b) => Number(b.autopilot) - Number(a.autopilot) || a.device.localeCompare(b.device, undefined, { numeric: true }))
}

const hex4 = (n: number | null) => (n === null ? '----' : n.toString(16).toUpperCase().padStart(4, '0'))
export const vidPid = (p: Pick<SerialPort, 'vid' | 'pid'>) => (p.vid === null && p.pid === null ? null : `${hex4(p.vid)}:${hex4(p.pid)}`)

export function portTitle(p: SerialPort): string {
  return p.description && p.description !== 'n/a' ? p.description : (p.name ?? p.device)
}
