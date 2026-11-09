// Firmware twin, runtime pack, serial and HITL view models (11 §11, §13 界面) and their copy.
import { describe, expect, it } from 'vitest'
import { RelayApiError, linkKind, type AutopilotPack, type HitlStatus, type TwinProfileSummary, type Vehicle, type VehicleFirmware } from '@/api'
import {
  canResetSim, captureBlock, changedRows, compareVersions, defaultSimForm, defaultSource, engineBadge, engineBadgeText, firmwareTitle, formatBytes, frameBlock, frameBody,
  frameErrors, frameForm, hitlCanStart, hitlCanStop, hitlEntry, hitlPollMs, hitlSteps, importBlock, packDeleteBlock, paramValue, portTitle, recommendSource,
  resetBlock, simBody, sortPacks, sortPorts, sourceOptions, startBlock, twinErrorView, twinRestarting, validateSimForm, vidPid, withFrame, withSource,
} from './model'
import { TWIN_DICTS, translateTwin, type TwinKey } from './strings'

const veh = (over: Partial<Vehicle> = {}): Vehicle =>
  ({
    id: 'sim-1', kind: 'sim', label: 'SIM-1', armed: false, crashed: false, landed: 'ground', autopilot: 'ArduPilot', firmware_info: null,
    link: { state: 'ok', heartbeat_age_s: 0.1, rx_hz: 30, tx_hz: 50, loss_pct: 0, rtt_ms: null },
    sim: { hover_assist: true, turbulence: 0, airframe: { preset: 'ideal', seed: 1 }, engine: 'relay', firmware: null, frame: 'micro-120' },
    ...over,
  }) as Vehicle

const twin = (state: 'running' | 'restarting' = 'running', match: 'exact' | 'series' | null = 'exact') =>
  veh({ sim: { hover_assist: true, turbulence: 0, airframe: { preset: 'ideal', seed: 1 }, engine: 'sitl', frame: 'small-250', firmware: { family: 'ardupilot', version: '4.6.3', pack: 'ardupilot-copter-4.6.3', match, state } } })

const pack = (id: string, family: 'ardupilot' | 'px4', version: string, over: Partial<AutopilotPack> = {}): AutopilotPack => ({
  id, family, vehicle: 'copter', version, git: 'abcdef12', platform: 'linux-x64', size_bytes: 7_200_000, license: 'GPL-3.0', source: '', built: 'official',
  origin: 'bundled', sha256: null, usable: true, problem: null, problem_zh: null, twins: 0, ...over,
})

const profile = (id: string, vehicle: string, match: TwinProfileSummary['match'], updated: number): TwinProfileSummary => ({
  id, name: `P ${id}`, captured_at: updated, updated_at: updated, source: { vehicle, label: vehicle.toUpperCase() },
  firmware: { family: 'ardupilot', vehicle: 'copter', version: '4.6.3', type: 'official', git: '3fc7011a', board: 'Pixracer', board_id: 11 },
  pack: 'ardupilot-copter-4.6.3', match, frame: { preset: 'small-250', mass_kg: 0.42, wheelbase_m: 0.25, prop_in: 5, cells: 3, capacity_mah: 850 }, home: null,
  params_total: 900, params_applied: 880, params_excluded: 20,
})

const fw = (over: Partial<VehicleFirmware> = {}): VehicleFirmware => ({
  id: 'q1', kind: 'real', label: 'Quad 01', firmware: 'ArduCopter 4.6.3',
  identity: { family: 'ardupilot', vehicle: 'copter', frame: 'quad', version: '4.6.3', type: 'official', git: '3fc7011a', board: 'Pixracer', board_id: 11, identified: true },
  params: { received: 900, total: 900, complete: true }, frame_problem: null, frame_problem_zh: null,
  match: { recommended: 'ardupilot-copter-4.6.3', match: 'exact', warning: false, warning_en: null, warning_zh: null, candidates: [] },
  ...over,
})

const zh = (k: TwinKey) => translateTwin('zh', k)
const en = (k: TwinKey) => translateTwin('en', k)
const placeholders = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()

describe('twin copy', () => {
  it('has identical keys and placeholders in both languages, no empty strings or exclamation marks', () => {
    expect(Object.keys(TWIN_DICTS.zh).sort()).toEqual(Object.keys(TWIN_DICTS.en).sort())
    for (const k of Object.keys(TWIN_DICTS.en) as TwinKey[]) {
      expect(placeholders(TWIN_DICTS.zh[k]), k).toEqual(placeholders(TWIN_DICTS.en[k]))
      expect(TWIN_DICTS.zh[k].trim().length, k).toBeGreaterThan(0)
      expect(`${TWIN_DICTS.en[k]}${TWIN_DICTS.zh[k]}`, k).not.toMatch(/[!！]/)
    }
  })
})

describe('engine badge (11 §8.5)', () => {
  it('names a twin with its firmware and match level, a HITL aircraft with its firmware', () => {
    expect(engineBadge(veh())).toBeNull()
    const b = engineBadge(twin())!
    expect(b).toMatchObject({ kind: 'twin', firmware: 'ArduCopter 4.6.3', match: 'exact', state: 'running' })
    expect(engineBadgeText(b, zh)).toBe('固件孪生 ArduCopter 4.6.3 · 精确匹配')
    expect(engineBadgeText(b, en)).toBe('Firmware twin ArduCopter 4.6.3 · exact match')
    expect(engineBadgeText(engineBadge(twin('running', null))!, zh)).toBe('固件孪生 ArduCopter 4.6.3')
    const real = veh({ id: 'q5', kind: 'real', sim: null, autopilot: 'PX4', firmware_info: { family: 'px4', vehicle: 'copter', version: '1.16.2', type: 'official', git: null, board: null } })
    expect(engineBadge(real)).toBeNull()
    expect(engineBadge({ ...real, hitl: { state: 'idle' } } as Vehicle)).toBeNull()
    const running = engineBadge({ ...real, hitl: { state: 'running' } } as Vehicle)!
    expect(engineBadgeText(running, zh)).toBe('硬件在环 PX4 1.16.2')
    expect(engineBadgeText(running, en)).toBe('Hardware in the loop PX4 1.16.2')
  })
  it('formats firmware names', () => {
    expect(firmwareTitle('ardupilot', '4.7.2')).toBe('ArduCopter 4.7.2')
    expect(firmwareTitle('ardupilot', '4.5.0', 'plane')).toBe('ArduPlane 4.5.0')
    expect(firmwareTitle('px4', '1.16.2')).toBe('PX4 1.16.2')
    expect(firmwareTitle(null, null)).toBe('')
  })
})

describe('simulator reset (v0.5.4)', () => {
  it('allows disarmed or crashed simulators only', () => {
    expect(canResetSim(veh())).toBe(true)
    expect(canResetSim(veh({ armed: true }))).toBe(false)
    expect(canResetSim(veh({ armed: true, crashed: true }))).toBe(true)
    expect(canResetSim(veh({ kind: 'real' }))).toBe(false)
    expect(canResetSim(null)).toBe(false)
    expect(resetBlock(veh({ armed: true }))?.key).toBe('reset.block.armed')
    expect(resetBlock(veh({ kind: 'real' }))?.key).toBe('reset.block.sim')
    expect(resetBlock(veh({ armed: true, crashed: true }))).toBeNull()
  })
  it('reports a twin restarting its firmware', () => {
    expect(twinRestarting(twin('restarting'))).toBe(true)
    expect(twinRestarting(twin('running'))).toBe(false)
    expect(twinRestarting(veh())).toBe(false)
  })
})

describe('runtime packs (11 §3)', () => {
  it('formats sizes and orders packs: ArduPilot first, newest first', () => {
    expect(formatBytes(7_200_000)).toBe('7.2 MB')
    expect(formatBytes(140_000)).toBe('140 kB')
    expect(formatBytes(512)).toBe('512 B')
    expect(compareVersions('4.10.1', '4.9.0')).toBeGreaterThan(0)
    const sorted = sortPacks([pack('px4-1.16.2', 'px4', '1.16.2'), pack('ardupilot-copter-4.5.7', 'ardupilot', '4.5.7'), pack('ardupilot-copter-4.7.2', 'ardupilot', '4.7.2'), pack('px4-1.17.0', 'px4', '1.17.0')])
    expect(sorted.map((p) => p.id)).toEqual(['ardupilot-copter-4.7.2', 'ardupilot-copter-4.5.7', 'px4-1.17.0', 'px4-1.16.2'])
  })
  it('deletes imported packs not in use, by the operator only', () => {
    expect(packDeleteBlock({ origin: 'bundled', twins: 0 }, true)?.key).toBe('packs.del.bundled')
    expect(packDeleteBlock({ origin: 'imported', twins: 2 }, true)).toEqual({ key: 'packs.del.inUse', params: { n: 2 } })
    expect(packDeleteBlock({ origin: 'imported', twins: 0 }, false)?.key).toBe('packs.del.operator')
    expect(packDeleteBlock({ origin: 'imported', twins: 0 }, true)).toBeNull()
  })
  it('checks the import form', () => {
    expect(importBlock('', '', true)?.key).toBe('packs.import.needPath')
    expect(importBlock('/tmp/p.tar.gz', 'xyz', true)?.key).toBe('packs.import.badSha')
    expect(importBlock('/tmp/p.tar.gz', 'a'.repeat(64), true)).toBeNull()
    expect(importBlock('/tmp/p.tar.gz', '', false)?.key).toBe('packs.import.operator')
  })
})

describe('frames (11 §8.1)', () => {
  it('sends the preset plus the changed values; checks ranges', () => {
    expect(frameBody(frameForm('small-250'))).toEqual({ preset: 'small-250' })
    expect(frameBody({ ...frameForm('small-250'), mass_kg: '0.5', cells: '4' })).toEqual({ preset: 'small-250', mass_kg: 0.5, cells: 4 })
    expect(frameErrors({ ...frameForm('large-450'), mass_kg: '40', cells: '3.5', wheelbase_m: '' })).toMatchObject({ mass_kg: { key: 'frame.err.range' }, cells: { key: 'frame.err.range' }, wheelbase_m: { key: 'frame.err.range' } })
    expect(frameErrors(frameForm('large-450'))).toEqual({})
  })
  it('changes only disarmed and on the ground', () => {
    expect(frameBlock(veh())).toBeNull()
    expect(frameBlock(veh({ armed: true }))?.key).toBe('frame.block.armed')
    expect(frameBlock(veh({ landed: 'air' }))?.key).toBe('frame.block.air')
  })
})

describe('add simulator dialog (11 §11)', () => {
  it('keeps the v0.5.3 body for a default fast simulator; adds a frame when changed', () => {
    expect(simBody(defaultSimForm())).toEqual({ count: 1, autopilot: 'ardupilot' })
    const small = withFrame({ ...defaultSimForm(), count: '2', autopilot: 'px4' }, 'small-250')
    expect(simBody(small)).toEqual({ count: 2, autopilot: 'px4', frame: { preset: 'small-250' } })
    expect(simBody({ ...small, mass: '0.5' })).toEqual({ count: 2, autopilot: 'px4', frame: { preset: 'small-250', mass_kg: 0.5 } })
  })
  it('builds twin bodies from a profile (its own frame) or a pack with frame and home', () => {
    const f = withSource(defaultSimForm('sitl'), 'profile:tp-1')
    expect(f.frame).toBe('profile')
    expect(simBody(f)).toEqual({ count: 1, engine: 'sitl', profile: 'tp-1' })
    const p = withFrame(withSource(f, 'pack:px4-1.16.2'), 'large-450')
    expect(simBody({ ...p, homeOn: true, lat: '47.39', lon: '8.54', alt: '488' })).toEqual({
      count: 1, engine: 'sitl', pack: 'px4-1.16.2', frame: { preset: 'large-450' }, home: { lat: 47.39, lon: 8.54, alt_m: 488 },
    })
  })
  it('validates count, source, frame and home', () => {
    expect(validateSimForm(defaultSimForm())).toEqual({})
    const bad = { ...defaultSimForm('sitl'), count: '9', mass: '0', homeOn: true, lat: '95', lon: '0', alt: '0' }
    expect(Object.keys(validateSimForm(bad)).sort()).toEqual(['count', 'home', 'mass', 'source'])
  })
  it('recommends a profile of a connected aircraft, the selected one first, else the identity match', () => {
    const profiles = [profile('a', 'q1', 'series', 10), profile('b', 'q2', 'exact', 5), profile('c', 'gone', 'exact', 50)]
    const connected = [{ id: 'q1', label: 'Quad 01' }, { id: 'q2', label: 'Quad 02' }]
    expect(recommendSource(profiles, connected, null, null)).toEqual({ value: 'profile:b', vehicle: 'Quad 02', match: 'exact' })
    expect(recommendSource(profiles, connected, 'q1', null)).toEqual({ value: 'profile:a', vehicle: 'Quad 01', match: 'series' })
    const ident = { vehicle: { id: 'q3', label: 'Quad 03' }, match: fw().match }
    expect(recommendSource([], connected, 'q3', ident)).toEqual({ value: 'pack:ardupilot-copter-4.6.3', vehicle: 'Quad 03', match: 'exact' })
    expect(recommendSource([], [], null, null)).toBeNull()
    expect(translateTwin('zh', 'sim.recommended', { match: zh('match.exact'), aircraft: 'Quad 02' })).toBe('推荐：与 Quad 02 精确匹配')
  })
  it('lists the recommended source first and defaults to it; unusable packs are disabled with the reason', () => {
    const packs = [pack('ardupilot-copter-4.7.2', 'ardupilot', '4.7.2'), pack('ardupilot-copter-4.2.3', 'ardupilot', '4.2.3', { usable: false, problem: 'glibc', problem_zh: 'glibc 版本' }), pack('px4-1.16.2', 'px4', '1.16.2')]
    const opts = sourceOptions([profile('a', 'q1', 'exact', 1)], packs, { value: 'pack:px4-1.16.2', vehicle: 'Quad 05', match: 'version' })
    expect(opts.map((o) => o.value)).toEqual(['pack:px4-1.16.2', 'profile:a', 'pack:ardupilot-copter-4.7.2', 'pack:ardupilot-copter-4.2.3'])
    expect(opts[3]).toMatchObject({ disabled: true, problem: { en: 'glibc', zh: 'glibc 版本' } })
    expect(opts[1].label).toBe('P a · ArduCopter 4.6.3')
    expect(defaultSource(opts, { value: 'pack:px4-1.16.2', vehicle: 'Quad 05', match: 'version' })).toBe('pack:px4-1.16.2')
    expect(defaultSource(opts, null)).toBe('profile:a')
    expect(defaultSource(sourceOptions([], packs, null), null)).toBe('pack:ardupilot-copter-4.7.2')
  })
  it('shows twin_start_failed with the firmware output and partial results', () => {
    const err = new RelayApiError(503, 'twin_start_failed', 'The firmware twin did not start: no heartbeat.', '固件孪生未能启动：没有心跳。', { reason: 'no heartbeat', lines: ['a', 'b'], added: ['sim-3'] })
    expect(twinErrorView(err, 'zh')).toEqual({ code: 'twin_start_failed', title: 'err.twin_start_failed', message: '固件孪生未能启动：没有心跳。', lines: ['a', 'b'], added: ['sim-3'] })
    expect(twinErrorView(new RelayApiError(409, 'twin_limit', 'At most 8.', '最多 8 个。'), 'en')).toMatchObject({ title: 'err.twin_limit', message: 'At most 8.', lines: [] })
    expect(twinErrorView(new Error('boom'), 'en')).toMatchObject({ title: 'err.generic', message: 'boom' })
  })
})

describe('firmware block (11 §4, §5, §7)', () => {
  it('captures once the parameter table is complete; starts with a profile or a pack', () => {
    expect(captureBlock(null)?.key).toBe('fw.loading')
    expect(captureBlock(fw({ params: { received: 300, total: 900, complete: false } }))).toEqual({ key: 'fw.block.params', params: { received: 300, total: 900 } })
    expect(captureBlock(fw({ identity: { ...fw().identity, identified: false } }))?.key).toBe('fw.block.unidentified')
    expect(captureBlock(fw())).toBeNull()
    expect(startBlock(fw(), null)).toBeNull()
    expect(startBlock(fw({ match: { ...fw().match, recommended: null, match: 'none' } }), null)?.key).toBe('fw.block.noPack')
    expect(startBlock(fw({ frame_problem: 'hexa', frame_problem_zh: '六旋翼' }), profile('a', 'q1', 'exact', 1))?.key).toBe('fw.block.frame')
    expect(startBlock(fw({ params: { received: 3, total: 9, complete: false } }), profile('a', 'q1', 'exact', 1))).toBeNull()
  })
  it('offers HITL to PX4 on USB serial; explains it for ArduPilot', () => {
    const px4 = veh({ id: 'q5', kind: 'real', sim: null, autopilot: 'PX4', firmware_info: { family: 'px4', vehicle: 'copter', version: '1.16.2', type: 'official', git: null, board: null } })
    expect(hitlEntry(px4, null)).toEqual({ mode: 'button', block: { key: 'hitl.block.serial' } })
    expect(hitlEntry({ ...px4, serial: { device: '/dev/ttyACM0', baud: 115200 } } as Vehicle, null)).toEqual({ mode: 'button', block: null })
    expect(hitlEntry(veh({ id: 'q2', kind: 'real', sim: null }), null)).toEqual({ mode: 'note', note: { key: 'hitl.ardupilot' } })
    expect(hitlEntry(veh(), null)).toEqual({ mode: 'none' })
    expect(linkKind({ ...px4, serial: { device: 'COM5', baud: null } } as Vehicle)).toBe('serial')
    expect(linkKind({ ...px4, link: { ...px4.link, kind: 'serial' } } as unknown as Vehicle)).toBe('serial')
    expect(linkKind(px4)).toBe('udp')
  })
})

describe('HITL session (11 §7.2)', () => {
  const status = (over: Partial<HitlStatus> = {}): HitlStatus => ({
    id: 'q5', available: true, reason: null, reason_zh: null, state: 'idle', steps: [], backup: null, changed: {}, mismatches: [], error: null, run: null, pending_restore: false, ...over,
  })
  it('lists all seven steps in order, pending until reported', () => {
    const steps = hitlSteps(status({ steps: [{ key: 'backup', state: 'active', detail: 'Reading', detail_zh: '读取' }] }))
    expect(steps.map((s) => s.key)).toEqual(['confirm', 'backup', 'write', 'reboot', 'run', 'restore', 'verify'])
    expect(steps.map((s) => s.state)).toEqual(['pending', 'active', 'pending', 'pending', 'pending', 'pending', 'pending'])
  })
  it('starts when available and idle; stops while it sets up or runs, or to restore a pending session', () => {
    expect(hitlCanStart(status())).toBe(true)
    expect(hitlCanStart(status({ available: false }))).toBe(false)
    expect(hitlCanStart(status({ pending_restore: true }))).toBe(false)
    expect(hitlCanStart(status({ state: 'running' }))).toBe(false)
    expect(hitlCanStop(status({ state: 'running' }))).toBe(true)
    expect(hitlCanStop(status({ state: 'rebooting' }))).toBe(true)
    expect(hitlCanStop(status({ state: 'verifying' }))).toBe(false)
    expect(hitlCanStop(status({ pending_restore: true }))).toBe(true)
    expect(hitlCanStop(status())).toBe(false)
    expect(hitlPollMs('running')).toBe(500)
    expect(hitlPollMs('idle')).toBe(2000)
  })
  it('tabulates the changed parameters and formats values', () => {
    expect(changedRows({ SYS_HITL: { from: 0, to: 1 }, SYS_AUTOSTART: { from: 4001, to: 1001 } })).toEqual([
      { name: 'SYS_AUTOSTART', from: 4001, to: 1001 },
      { name: 'SYS_HITL', from: 0, to: 1 },
    ])
    expect(paramValue(0.15000000596)).toBe('0.15')
    expect(paramValue(4001)).toBe('4001')
    expect(paramValue(null)).toBe('—')
  })
})

describe('serial ports (11 §7.1)', () => {
  it('lists flight controllers first and names the USB ids', () => {
    const ports = [
      { device: '/dev/ttyUSB0', name: 'ttyUSB0', description: 'CP2102', manufacturer: null, vid: 0x10c4, pid: 0xea60, serial_number: null, autopilot: false },
      { device: '/dev/ttyACM10', name: 'ttyACM10', description: 'n/a', manufacturer: null, vid: null, pid: null, serial_number: null, autopilot: true },
      { device: '/dev/ttyACM2', name: 'ttyACM2', description: 'PX4 FMU', manufacturer: null, vid: 0x26ac, pid: 0x0038, serial_number: null, autopilot: true },
    ]
    expect(sortPorts(ports).map((p) => p.device)).toEqual(['/dev/ttyACM2', '/dev/ttyACM10', '/dev/ttyUSB0'])
    expect(vidPid(ports[2])).toBe('26AC:0038')
    expect(vidPid(ports[1])).toBeNull()
    expect(portTitle(ports[1])).toBe('ttyACM10')
  })
})
