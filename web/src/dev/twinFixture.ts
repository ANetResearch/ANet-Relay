// Dev fixture stand-in for the firmware twin, runtime pack, serial and HITL routes (11 §3-§7, §10), so every state
// of the new surfaces can be reviewed without a backend: bundled and imported packs (one not usable), two twin
// profiles, the identity match per aircraft (a PX4 aircraft still reading its parameters), twins that boot,
// restart and refuse to start (px4-1.14.4 prints its output and fails; more than three twins hit the limit),
// serial ports with a flight controller and an adapter that never sends a heartbeat, and a HITL session that walks
// backup → write → reboot → run → restore → verify with one restore mismatch.
import {
  RelayApiError, serialOf, type AutopilotPack, type FrameBody, type HitlStatus, type HitlStep, type HitlStepKey, type SerialPorts, type SimTwin,
  type TwinProfile, type Vehicle, type VehicleFirmware,
} from '@/api'
import { compareVersions, PRESET_VALUES, isPreset, type FramePreset } from '@/twin/model'

type V = Vehicle

export interface TwinFixtureDeps {
  now(): number
  vehicles(): V[]
  mutate(id: string, fn: (v: V) => V): void
  add(v: V): void
  remove(id: string): void
  emit(kind: string, vehicle: string | null, data?: Record<string, unknown>): void
  base(over: Partial<V> & Pick<V, 'id' | 'label' | 'kind'>): V
}

export interface TwinFixture {
  /** Handles the twin, pack, serial and HITL routes; undefined for any other path. */
  handle(method: string, path: string, body: unknown): unknown
  stop(): void
}

const fail = (status: number, code: string, en: string, zh: string, detail?: unknown) => new RelayApiError(status, code, en, zh, detail)

const PLATFORM = 'linux-x64' as const

function pack(id: string, family: 'ardupilot' | 'px4', version: string, git: string, mb: number, over: Partial<AutopilotPack> = {}): AutopilotPack {
  return {
    id, family, vehicle: 'copter', version, git, platform: PLATFORM, size_bytes: Math.round(mb * 1e6),
    license: family === 'ardupilot' ? 'GPL-3.0' : 'BSD-3-Clause',
    source: family === 'ardupilot' ? `https://github.com/ArduPilot/ardupilot/tree/Copter-${version}` : `https://github.com/PX4/PX4-Autopilot/tree/v${version}`,
    built: family === 'ardupilot' ? 'official' : 'ci', origin: 'bundled', sha256: null, usable: true, problem: null, problem_zh: null, twins: 0, ...over,
  }
}

const BUNDLED: AutopilotPack[] = [
  pack('ardupilot-copter-4.7.2', 'ardupilot', '4.7.2', 'fb773567', 7.2),
  pack('ardupilot-copter-4.6.3', 'ardupilot', '4.6.3', '3fc7011a', 7.1),
  pack('ardupilot-copter-4.5.7', 'ardupilot', '4.5.7', '2a3dc4b7', 6.9),
  pack('ardupilot-copter-4.4.4', 'ardupilot', '4.4.4', '1bd84c1f', 6.6),
  pack('ardupilot-copter-4.3.8', 'ardupilot', '4.3.8', '0f9d22e4', 6.4),
  pack('px4-1.17.0', 'px4', '1.17.0', '7c1e5a90', 4.9),
  pack('px4-1.16.2', 'px4', '1.16.2', 'b5f3e2a1', 4.8),
  pack('px4-1.15.4', 'px4', '1.15.4', 'e9a0c3d7', 4.7),
  pack('px4-1.14.4', 'px4', '1.14.4', '9d1b7e02', 4.6),
]

const IMPORTED: AutopilotPack[] = [
  pack('ardupilot-copter-4.2.3', 'ardupilot', '4.2.3', '4cd7a1f0', 6.1, {
    origin: 'imported', built: 'ci', sha256: 'c0ffee'.padEnd(64, '0'), usable: false,
    problem: 'built for glibc 2.38; this computer has 2.35', problem_zh: '为 glibc 2.38 构建；本机为 2.35',
  }),
]

const EXCLUDED_AP = [
  { name: 'SERIAL1_PROTOCOL', reason_en: 'serial port wiring of the board', reason_zh: '飞控板的串口接线' },
  { name: 'INS_ACC_ID', reason_en: 'sensor identity of the board', reason_zh: '飞控板的传感器标识' },
  { name: 'COMPASS_OFS_X', reason_en: 'compass calibration of the board', reason_zh: '飞控板的磁罗盘校准' },
  { name: 'BRD_SAFETYENABLE', reason_en: 'board setting', reason_zh: '飞控板设置' },
  { name: 'SYSID_THISMAV', reason_en: 'MAVLink system id of the aircraft', reason_zh: '飞机的 MAVLink 系统号' },
]
const EXCLUDED_PX4 = [
  { name: 'CAL_ACC0_ID', reason_en: 'sensor calibration of the board', reason_zh: '飞控板的传感器校准' },
  { name: 'SYS_AUTOSTART', reason_en: 'airframe selection of the board', reason_zh: '飞控板的机架选择' },
  { name: 'MAV_0_CONFIG', reason_en: 'MAVLink port wiring', reason_zh: 'MAVLink 端口接线' },
]

const FRAME_SMALL = { preset: 'small-250' as const, mass_kg: 0.42, wheelbase_m: 0.25, prop_in: 5, cells: 3, capacity_mah: 850 }
const FRAME_LARGE = { preset: 'large-450' as const, mass_kg: 1.5, wheelbase_m: 0.45, prop_in: 10, cells: 4, capacity_mah: 5000 }

function profileFor(id: string, name: string, source: { vehicle: string; label: string }, family: 'ardupilot' | 'px4', version: string, packId: string, match: TwinProfile['match'], at: number): TwinProfile {
  const excluded = family === 'ardupilot' ? EXCLUDED_AP : EXCLUDED_PX4
  const params: Record<string, number> = family === 'ardupilot' ? { ATC_RAT_RLL_P: 0.08, ATC_RAT_PIT_P: 0.08, MOT_THST_HOVER: 0.31, ANGLE_MAX: 3000 } : { MC_ROLLRATE_P: 0.15, MPC_THR_HOVER: 0.42, MPC_XY_VEL_MAX: 5 }
  return {
    id, name, captured_at: at, updated_at: at, source,
    firmware: { family, vehicle: 'copter', version, type: 'official', git: family === 'ardupilot' ? '2a3dc4b7' : 'ea0ef1a1', board: family === 'ardupilot' ? 'Cube Orange' : 'Pixhawk 6C (fmu-v6c)', board_id: family === 'ardupilot' ? 140 : 56 },
    pack: packId, match, frame: family === 'ardupilot' ? FRAME_SMALL : FRAME_LARGE, home: null,
    params_total: 1043 + excluded.length, params_applied: 1043, params_excluded: excluded.length, params, excluded,
  }
}

const LINES_PX4_FAIL = [
  'INFO  [px4] startup script: /bin/sh etc/init.d-posix/rcS 0',
  'INFO  [param] selected parameter default file eeprom/parameters_10016',
  'INFO  [simulator_mavlink] Waiting for simulator to accept connection on TCP port 4560',
  'ERROR [simulator_mavlink] HIL_SENSOR timeout',
  'ERROR [px4] Startup script returned with return value: 256',
]

const SERIAL: SerialPorts = {
  ports: [
    { device: '/dev/ttyUSB0', name: 'ttyUSB0', description: 'CP2102 USB to UART Bridge Controller', manufacturer: 'Silicon Labs', vid: 0x10c4, pid: 0xea60, serial_number: '0001', autopilot: false },
    { device: '/dev/ttyACM0', name: 'ttyACM0', description: 'PX4 FMU v6C.x', manufacturer: '3D Robotics', vid: 0x26ac, pid: 0x0038, serial_number: '0', autopilot: true },
    { device: '/dev/ttyACM1', name: 'ttyACM1', description: 'CubeOrange', manufacturer: 'CubePilot', vid: 0x2dae, pid: 0x1016, serial_number: '1E0034', autopilot: true },
  ],
}

const HITL_ORDER: HitlStepKey[] = ['confirm', 'backup', 'write', 'reboot', 'run', 'restore', 'verify']

function frameView(preset: FramePreset, over: Partial<FrameBody> = {}) {
  const p = { ...PRESET_VALUES[preset], ...over }
  return { preset, mass_kg: p.mass_kg, wheelbase_m: p.wheelbase_m, prop_in: p.prop_in, cells: p.cells, capacity_mah: p.capacity_mah, thrust_to_weight: preset === 'large-450' ? 2.2 : preset === 'small-250' ? 3.5 : 2.4 }
}

export function startTwinFixture(d: TwinFixtureDeps): TwinFixture {
  const timers = new Set<ReturnType<typeof setTimeout>>()
  const later = (ms: number, fn: () => void) => {
    const tm = setTimeout(() => {
      timers.delete(tm)
      fn()
    }, ms)
    timers.add(tm)
  }
  let packs = [...BUNDLED, ...IMPORTED]
  const t0 = d.now()
  const profiles = new Map<string, TwinProfile>([
    ['tp-1a2b3c4d', profileFor('tp-1a2b3c4d', 'Quad 02 小机', { vehicle: 'q02', label: 'Quad 02' }, 'ardupilot', '4.5.7', 'ardupilot-copter-4.5.7', 'exact', t0 - 86400)],
    ['tp-5e6f7a8b', profileFor('tp-5e6f7a8b', 'Quad 01 大机', { vehicle: 'q01', label: 'Quad 01' }, 'px4', '1.15.2', 'px4-1.15.4', 'series', t0 - 3600)],
  ])
  const twins = new Map<string, SimTwin>()
  const frames = new Map<string, ReturnType<typeof frameView>>()
  const hitl = new Map<string, HitlStatus>()
  const paramStart = d.now()
  let instance = 1

  const vehicle = (id: string) => d.vehicles().find((v) => v.id === id)
  const nextSimId = () => {
    const used = new Set(d.vehicles().map((v) => v.id))
    let n = 1
    while (used.has(`sim-${n}`)) n++
    return `sim-${n}`
  }
  const twinCount = () => d.vehicles().filter((v) => v.sim?.engine === 'sitl').length
  const withTwins = (p: AutopilotPack): AutopilotPack => ({ ...p, twins: d.vehicles().filter((v) => v.sim?.firmware?.pack === p.id).length })

  const setTwinState = (id: string, state: SimTwin['state']) => {
    const tw = twins.get(id)
    if (tw) twins.set(id, { ...tw, state, boot_s: state === 'running' ? 1.4 : tw.boot_s, pid: state === 'running' ? 40000 + (tw.instance ?? 0) : tw.pid })
    d.mutate(id, (x) => (x.sim?.firmware ? { ...x, sim: { ...x.sim, firmware: { ...x.sim.firmware, state } }, link: { ...x.link, state: state === 'running' ? 'ok' : 'waiting' } } : x))
  }

  const registerTwin = (id: string, p: AutopilotPack, profile: TwinProfile | null, fv: ReturnType<typeof frameView>, state: SimTwin['state']): number => {
    const n = instance++
    frames.set(id, fv)
    const excluded = profile ? profile.excluded : []
    twins.set(id, {
      id, label: id.toUpperCase(), engine: 'sitl', state, pack: withTwins(p),
      firmware: { family: p.family, version: p.version, pack: p.id, match: profile ? profile.match : null },
      profile: profile ? { id: profile.id, name: profile.name } : null, instance: n,
      home: profile?.home ?? { lat: 47.397742, lon: 8.545594, alt_m: 488 },
      frame: { ...fv, inertia: [0.0021, 0.0021, 0.0039] },
      params: { applied: profile ? profile.params_applied : 0, excluded, forced: p.family === 'ardupilot' ? { SIM_RATE_HZ: 1000 } : {} },
      ports: p.family === 'ardupilot' ? { gcs_tcp: 5760 + 10 * n, json_udp: 9002 + 10 * n } : { hil_tcp: 4560 + n, gcs_udp: 24550 + n },
      boot_s: state === 'running' ? 1.4 : null, pid: state === 'running' ? 40000 + n : null, restarts: 0, exit: null, lockstep: { steps: 0, stalls: 0, frames: 0, ms_per_step: null },
    })
    return n
  }

  const makeTwin = (packId: string, profile: TwinProfile | null, frame: FrameBody | undefined): string => {
    const p = packs.find((x) => x.id === packId)!
    const id = nextSimId()
    const preset: FramePreset = frame?.preset && isPreset(frame.preset) ? frame.preset : (profile?.frame.preset ?? 'micro-120')
    const n = registerTwin(id, p, profile, frameView(preset, frame ?? profile?.frame ?? {}), 'booting')
    const v = d.base({
      id, label: id.toUpperCase(), kind: 'sim', sysid: 20 + n,
      autopilot: p.family === 'ardupilot' ? 'ArduPilot' : 'PX4',
      firmware: p.family === 'ardupilot' ? `ArduCopter V${p.version}` : `PX4 ${p.version}`,
      link: { state: 'waiting', heartbeat_age_s: null, rx_hz: 0, tx_hz: 0, loss_pct: null, rtt_ms: null },
      position: { frame: 'sim', x: -1 + n * 0.8, y: -1.2, z: 0, vx: 0, vy: 0, vz: 0, age_s: 0.02 },
    })
    d.add({ ...v, sim: { ...v.sim!, engine: 'sitl', frame: preset, firmware: { family: p.family, version: p.version, pack: p.id, match: profile ? profile.match : null, state: 'booting' } } })
    d.emit('sim_added', id, { engine: 'sitl', firmware: { family: p.family, version: p.version, pack: p.id } })
    later(1500, () => {
      setTwinState(id, 'running')
      d.emit('twin_started', id, { family: p.family, version: p.version, pack: p.id, match: profile?.match ?? null, restart: false, pid: 40000 + n, boot_s: 1.4 })
    })
    return id
  }

  // twins already in the fleet (the busy fixture's SIM-3) get their details
  for (const v of d.vehicles()) {
    const f = v.sim?.engine === 'sitl' ? v.sim.firmware : null
    const p = f ? packs.find((x) => x.id === f.pack) : undefined
    if (f && p) registerTwin(v.id, p, [...profiles.values()].find((x) => x.pack === p.id) ?? null, frameView(isPreset(v.sim!.frame) ? v.sim!.frame : 'micro-120'), f.state)
  }

  const firmwareOf = (v: V): VehicleFirmware => {
    const fi = v.firmware_info
    const family = fi?.family ?? null
    const version = fi?.version ?? null
    const same = packs.filter((p) => p.usable && p.family === family)
    const ranked = [...same].sort((a, b) => {
      const exact = (p: AutopilotPack) => (p.version === version ? 0 : version && p.version.split('.').slice(0, 2).join('.') === version.split('.').slice(0, 2).join('.') ? 1 : 2)
      return exact(a) - exact(b) || compareVersions(b.version, a.version)
    })
    const best = ranked[0] ?? null
    const level = (p: AutopilotPack | null): VehicleFirmware['match']['match'] =>
      !p || !version ? 'none' : p.version === version ? (p.git === fi?.git ? 'exact' : 'version') : p.version.split('.').slice(0, 2).join('.') === version.split('.').slice(0, 2).join('.') ? 'series' : 'nearest'
    const match = level(best)
    const warning = match === 'series' || match === 'nearest' || match === 'none'
    // the PX4 aircraft on USB is still reading its parameter table: 20 s from the fixture start
    const reading = serialOf(v) !== null
    const total = family === 'px4' ? 1210 : 1048
    const received = reading ? Math.min(total, Math.round(380 + ((d.now() - paramStart) / 20) * (total - 380))) : total
    return {
      id: v.id, kind: v.kind, label: v.label, firmware: v.firmware,
      identity: { family, vehicle: fi?.vehicle ?? null, frame: 'quad', version, type: fi?.type ?? null, git: fi?.git ?? null, board: fi?.board ?? null, board_id: family === 'px4' ? 56 : 140, identified: Boolean(fi) },
      params: { received, total, complete: received >= total },
      frame_problem: null, frame_problem_zh: null,
      match: {
        recommended: best?.id ?? null, match, warning,
        warning_en: warning ? 'The control loop and parameter meanings may differ from the aircraft.' : null,
        warning_zh: warning ? '控制回路与参数含义可能与真机不同。' : null,
        candidates: ranked.map((p) => ({ pack: p.id, version: p.version, git: p.git, match: level(p) })),
      },
    }
  }

  // ---------------------------------------------------------------- HITL session
  const hitlFor = (v: V): HitlStatus => {
    const cur = hitl.get(v.id)
    const px4 = v.firmware_info?.family === 'px4' || v.autopilot === 'PX4'
    const serial = serialOf(v) !== null
    const reason: [string, string] | null = !px4
      ? ['ArduPilot removed HIL support and the firmware cannot be changed; use the firmware twin', 'ArduPilot 已移除 HIL，且固件不能更改；请使用固件孪生']
      : !serial
        ? ['the flight controller must be connected over USB serial', '飞控须通过 USB 串口连接']
        : v.armed
          ? ['disarm the aircraft first', '须先给飞机上锁']
          : !(v.controller === 'human' && v.possession)
            ? ['possess the aircraft first: the operator starts hardware in the loop', '须先附身：硬件在环由操作员启动']
            : null
    const base: HitlStatus = cur ?? {
      id: v.id, available: false, reason: null, reason_zh: null, state: 'idle',
      steps: HITL_ORDER.map((key) => ({ key, state: 'pending', detail: null, detail_zh: null })), backup: null, changed: {}, mismatches: [], error: null, run: null, pending_restore: false,
    }
    const ran = base.run ? Math.round((d.now() - base.run.since) * 250) : 0
    const run = base.run && base.state === 'running' ? { ...base.run, steps: ran, actuator_frames: ran } : base.run
    return { ...base, run, available: reason === null, reason: reason?.[0] ?? null, reason_zh: reason?.[1] ?? null }
  }
  const setHitl = (id: string, patch: Partial<HitlStatus>, steps?: Partial<Record<HitlStepKey, Partial<HitlStep>>>) => {
    const v = vehicle(id)
    if (!v) return
    const cur = hitlFor(v)
    const nextSteps = cur.steps.map((s) => (steps?.[s.key] ? { ...s, ...steps[s.key] } : s))
    const next = { ...cur, ...patch, steps: nextSteps }
    hitl.set(id, next)
    d.mutate(id, (x) => ({ ...x, hitl: { state: next.state } }) as V)
  }
  const hitlStart = (id: string) => {
    const now = d.now()
    setHitl(id, { state: 'backing_up', mismatches: [], error: null, run: null }, {
      confirm: { state: 'done', detail: 'Propellers removed, confirmed by the operator', detail_zh: '操作员已确认拆桨' },
      backup: { state: 'active', detail: 'Reading 1210 parameters', detail_zh: '正在读取 1210 项参数' },
      write: { state: 'pending', detail: null, detail_zh: null }, reboot: { state: 'pending', detail: null, detail_zh: null }, run: { state: 'pending', detail: null, detail_zh: null },
      restore: { state: 'pending', detail: null, detail_zh: null }, verify: { state: 'pending', detail: null, detail_zh: null },
    })
    const file = `hitl-backups/${id}-${new Date(now * 1000).toISOString().replace(/[:.]/g, '').slice(0, 15)}.json`
    later(1200, () =>
      setHitl(id, { state: 'writing', backup: { file, count: 1210, captured_at: now + 1.2 }, changed: { SYS_HITL: { from: 0, to: 1 }, SYS_AUTOSTART: { from: 4001, to: 1001 } } }, {
        backup: { state: 'done', detail: `1210 parameters saved to ${file}`, detail_zh: `已保存 1210 项参数到 ${file}` },
        write: { state: 'active', detail: 'SYS_HITL 1, SYS_AUTOSTART 1001', detail_zh: 'SYS_HITL 1，SYS_AUTOSTART 1001' },
      }),
    )
    later(2200, () =>
      setHitl(id, { state: 'rebooting' }, {
        write: { state: 'done', detail: '2 parameters written', detail_zh: '已写入 2 项参数' },
        reboot: { state: 'active', detail: 'Waiting for the flight controller to reconnect', detail_zh: '等待飞控重新连接' },
      }),
    )
    later(3700, () => {
      setHitl(id, { state: 'running', run: { sensor_hz: 250, gps_hz: 10, actuator_frames: 0, steps: 0, since: d.now() } }, {
        reboot: { state: 'done', detail: 'Reconnected after 1.5 s', detail_zh: '1.5 秒后重新连接' },
        run: { state: 'active', detail: 'HIL_SENSOR 250 Hz, HIL_GPS 10 Hz', detail_zh: 'HIL_SENSOR 250 Hz，HIL_GPS 10 Hz' },
      })
      d.emit('hitl_started', id, { vehicle: id, family: 'px4', version: vehicle(id)?.firmware_info?.version ?? null, backup: file })
    })
  }
  const hitlStop = (id: string) => {
    setHitl(id, { state: 'restoring', run: null }, {
      run: { state: 'done', detail: 'Stopped by the operator', detail_zh: '操作员已停止' },
      write: { state: hitlFor(vehicle(id)!).steps.find((s) => s.key === 'write')?.state === 'pending' ? 'skipped' : 'done' },
      restore: { state: 'active', detail: 'Writing 2 parameters back and rebooting', detail_zh: '正在写回 2 项参数并重启' },
    })
    later(1500, () => setHitl(id, { state: 'verifying' }, { restore: { state: 'done', detail: '2 parameters restored', detail_zh: '已恢复 2 项参数' }, verify: { state: 'active', detail: 'Reading back 1210 parameters', detail_zh: '正在读回 1210 项参数' } }))
    later(2700, () => {
      const mismatches = [{ name: 'COM_RC_IN_MODE', expected: 1, actual: 0 }]
      setHitl(id, { state: 'restored', mismatches, pending_restore: false }, { verify: { state: 'failed', detail: '1 parameter differs from the backup', detail_zh: '1 项参数与备份不一致' } })
      d.emit('hitl_restored', id, { vehicle: id, restored: 2, mismatches })
    })
  }

  return {
    handle(method, path, body) {
      const b = (body ?? {}) as Record<string, unknown>
      // runtime packs
      if (path === '/autopilots' && method === 'GET') return { platform: PLATFORM, bundled_dir: '/opt/ANet Relay/resources/autopilots', imported_dir: '~/.local/share/anet-relay/autopilots', packs: packs.map(withTwins) }
      if (path === '/autopilots/import' && method === 'POST') {
        const p = String(b.path ?? '')
        const m = p.match(/(ardupilot-copter|px4)-(\d+\.\d+\.\d+)/)
        if (!m) throw fail(400, 'pack_unsupported', 'The autopilot pack cannot be used: not a pack tarball.', '无法使用该飞控运行包：不是运行包文件。', { reason: 'not a pack tarball', reason_zh: '不是运行包文件' })
        const id = `${m[1]}-${m[2]}`
        if (packs.some((x) => x.id === id)) throw fail(409, 'pack_unsupported', `The autopilot pack cannot be used: ${id} is already installed.`, `无法使用该飞控运行包：${id} 已安装。`)
        const added = pack(id, m[1] === 'px4' ? 'px4' : 'ardupilot', m[2], 'a1b2c3d4', 6.8, { origin: 'imported', sha256: typeof b.sha256 === 'string' ? b.sha256 : null })
        packs = [...packs, added]
        d.emit('autopilot_pack_imported', null, { pack: id, family: added.family, version: added.version })
        return added
      }
      const del = path.match(/^\/autopilots\/([^/]+)$/)
      if (del && method === 'DELETE') {
        const id = decodeURIComponent(del[1])
        const p = packs.find((x) => x.id === id)
        if (!p) throw fail(404, 'pack_missing', `Autopilot pack ${id} is not installed.`, `未安装飞控运行包 ${id}。`)
        if (p.origin === 'bundled') throw fail(400, 'pack_unsupported', 'The autopilot pack cannot be used: bundled packs stay.', '无法使用该飞控运行包：内置包不能删除。')
        packs = packs.filter((x) => x.id !== id)
        return { id, deleted: true }
      }
      // identity
      const fw = path.match(/^\/vehicles\/([^/]+)\/firmware$/)
      if (fw && method === 'GET') {
        const v = vehicle(decodeURIComponent(fw[1]))
        if (!v) throw fail(404, 'not_found', 'Unknown aircraft.', '未知飞机。')
        return firmwareOf(v)
      }
      // profiles
      if (path === '/twins/profiles' && method === 'GET') return { profiles: [...profiles.values()].map(({ params: _p, excluded: _e, ...s }) => s) }
      if (path === '/twins/profiles' && method === 'POST') {
        const v = vehicle(String(b.from_vehicle ?? ''))
        if (!v || v.kind !== 'real') throw fail(409, 'real_only', 'This action is only available for real aircraft.', '此操作仅适用于真机。')
        const f = firmwareOf(v)
        if (!f.params.complete) throw fail(409, 'params_incomplete', `Read the aircraft's whole parameter table first (${f.params.received} of ${f.params.total} received).`, `须先读完飞机的全部参数（已收到 ${f.params.received}/${f.params.total}）。`, { received: f.params.received, total: f.params.total })
        const prev = typeof b.profile === 'string' ? profiles.get(b.profile) : undefined
        const id = prev?.id ?? `tp-${Math.floor(d.now() * 1000).toString(16).slice(-8)}`
        const family = f.identity.family ?? 'ardupilot'
        const p = profileFor(id, prev?.name ?? (typeof b.name === 'string' ? b.name : `${f.identity.board ?? v.label} ${family === 'px4' ? 'PX4' : 'ArduCopter'} ${f.identity.version ?? ''}`.trim()), { vehicle: v.id, label: v.label }, family, f.identity.version ?? '', f.match.recommended ?? '', f.match.match, d.now())
        const out = prev ? { ...p, frame: prev.frame, home: prev.home, captured_at: d.now() } : p
        profiles.set(id, out)
        return out
      }
      const pr = path.match(/^\/twins\/profiles\/([^/]+)$/)
      if (pr) {
        const id = decodeURIComponent(pr[1])
        const p = profiles.get(id)
        if (!p) throw fail(404, 'profile_not_found', `There is no twin profile ${id}.`, `没有孪生配置 ${id}。`)
        if (method === 'DELETE') {
          profiles.delete(id)
          return { id, deleted: true }
        }
        if (method === 'PUT') {
          const next = { ...p, ...(typeof b.name === 'string' ? { name: b.name } : {}), updated_at: d.now() }
          profiles.set(id, next)
          return next
        }
        return p
      }
      // simulators
      if (path === '/sims' && method === 'POST') {
        const count = Math.max(1, Math.min(8, Number(b.count ?? 1)))
        const frame = b.frame as FrameBody | undefined
        if (b.engine !== 'sitl') {
          const added: string[] = []
          for (let i = 0; i < count; i++) {
            const id = nextSimId()
            const preset: FramePreset = frame?.preset && isPreset(frame.preset) ? frame.preset : 'micro-120'
            frames.set(id, frameView(preset, frame))
            const v = d.base({ id, label: id.toUpperCase(), kind: 'sim', sysid: 30 + i, autopilot: b.autopilot === 'px4' ? 'PX4' : 'ArduPilot', position: { frame: 'sim', x: 1 + i * 0.6, y: -1.6, z: 0, vx: 0, vy: 0, vz: 0, age_s: 0.02 } })
            d.add({ ...v, sim: { ...v.sim!, frame: preset } })
            added.push(id)
          }
          return { added }
        }
        const profile = typeof b.profile === 'string' ? (profiles.get(b.profile) ?? null) : null
        if (typeof b.profile === 'string' && !profile) throw fail(404, 'profile_not_found', `There is no twin profile ${b.profile}.`, `没有孪生配置 ${b.profile}。`)
        const packId = profile?.pack ?? (typeof b.pack === 'string' ? b.pack : 'ardupilot-copter-4.7.2')
        const p = packs.find((x) => x.id === packId)
        if (!p) throw fail(404, 'pack_missing', `Autopilot pack ${packId} is not installed.`, `未安装飞控运行包 ${packId}。`, { pack: packId })
        if (!p.usable) throw fail(400, 'pack_unsupported', `The autopilot pack cannot be used: ${p.problem}.`, `无法使用该飞控运行包：${p.problem_zh}。`, { reason: p.problem, reason_zh: p.problem_zh })
        if (twinCount() + count > 3) throw fail(409, 'twin_limit', 'Relay runs at most 3 firmware twins at a time.', 'Relay 同时最多运行 3 个固件孪生。', { limit: 3 })
        if (p.id === 'px4-1.14.4') {
          throw fail(503, 'twin_start_failed', 'The firmware twin did not start: no heartbeat within 15 s.', '固件孪生未能启动：15 秒内没有心跳。', {
            reason: 'no heartbeat within 15 s', reason_zh: '15 秒内没有心跳', lines: LINES_PX4_FAIL, pack: p.id, added: [],
          })
        }
        const added: string[] = []
        for (let i = 0; i < count; i++) added.push(makeTwin(p.id, profile, frame))
        return { added }
      }
      const tw = path.match(/^\/sims\/([^/]+)\/(twin|frame|reset)$/)
      if (tw) {
        const id = decodeURIComponent(tw[1])
        const v = vehicle(id)
        if (!v || v.kind !== 'sim') throw fail(409, 'sim_only', 'This action is only available for simulated aircraft.', '此操作仅适用于模拟机。')
        if (tw[2] === 'twin' && method === 'GET') {
          const t = twins.get(id)
          if (!t) throw fail(409, 'twin_unsupported', 'A firmware twin has no details here.', '固件孪生不支持此操作。', { operation: 'details', operation_zh: '详情' })
          const steps = t.state === 'running' ? t.lockstep.steps + 500 : t.lockstep.steps
          const next = { ...t, frame: { ...t.frame, ...(frames.get(id) ?? {}) }, lockstep: { steps, stalls: t.lockstep.stalls + (steps % 7 === 0 ? 1 : 0), frames: steps * 4, ms_per_step: t.state === 'running' ? 1.06 : null } }
          twins.set(id, next)
          return next
        }
        if (tw[2] === 'frame' && method === 'PUT') {
          if (v.armed || v.landed === 'air') throw fail(409, 'armed', 'Disarm the aircraft first.', '须先给飞机上锁。')
          const f = (b ?? {}) as FrameBody
          const cur = frames.get(id) ?? frameView(isPreset(v.sim?.frame ?? '') ? (v.sim!.frame as FramePreset) : 'micro-120')
          const preset: FramePreset = f.preset && isPreset(f.preset) ? f.preset : (cur.preset as FramePreset)
          const fv = f.preset && f.preset !== cur.preset ? frameView(preset, f) : { ...cur, ...f, preset }
          frames.set(id, fv)
          d.mutate(id, (x) => (x.sim ? { ...x, sim: { ...x.sim, frame: preset } } : x))
          return { id, engine: v.sim?.engine ?? 'relay', frame: fv }
        }
        if (tw[2] === 'reset' && method === 'POST') {
          if (v.armed && !v.crashed) throw fail(409, 'armed', 'Disarm the aircraft first.', '须先给飞机上锁。')
          d.mutate(id, (x) => ({
            ...x, armed: false, crashed: false, landed: 'ground', mode: 'STABILIZE', attitude: { roll: 0, pitch: 0, yaw: 0 }, altitude_m: 0, throttle_pct: 0,
            position: x.position ? { ...x.position, z: 0, vx: 0, vy: 0, vz: 0 } : x.position,
          }))
          if (twins.has(id)) {
            const t = twins.get(id)!
            twins.set(id, { ...t, restarts: t.restarts + 1, lockstep: { steps: 0, stalls: 0, frames: 0, ms_per_step: null } })
            setTwinState(id, 'restarting')
            later(1800, () => {
              setTwinState(id, 'running')
              d.emit('twin_started', id, { family: t.firmware.family, version: t.firmware.version, pack: t.firmware.pack, match: t.firmware.match, restart: true, pid: 40100 + (t.instance ?? 0), boot_s: 1.6 })
            })
          }
          return { id, reset: true }
        }
      }
      // serial
      if (path === '/serial/ports' && method === 'GET') return SERIAL
      if (path === '/vehicles' && method === 'POST' && b.serial && typeof b.serial === 'object') {
        const s = b.serial as { device?: string; baud?: number | 'auto' }
        const device = String(s.device ?? '')
        const port = SERIAL.ports.find((p) => p.device === device)
        const reason = (en: string, zh: string) => fail(409, 'serial_unavailable', `The serial port is not available: ${en}.`, `串口不可用：${zh}。`, { reason: en, reason_zh: zh, device })
        if (!port) throw reason('device not found', '找不到设备')
        if (d.vehicles().some((v) => serialOf(v)?.device === device)) throw reason('device busy', '设备被占用')
        if (!port.autopilot) throw reason('no heartbeat at 115200, 57600 or 921600 baud', '115200、57600 与 921600 波特率下均无心跳')
        const id = `fc-${device.replace(/\W+/g, '')}`
        const px4 = /PX4/.test(port.description ?? '')
        const v = d.base({
          id, label: px4 ? 'FMU v6C' : 'CubeOrange', kind: 'real', host: null, sysid: 1, bridge: null,
          autopilot: px4 ? 'PX4' : 'ArduPilot', firmware: px4 ? 'PX4 1.16.2' : 'ArduCopter 4.6.3',
          firmware_info: px4 ? { family: 'px4', vehicle: 'copter', version: '1.16.2', type: 'official', git: 'b5f3e2a1', board: 'Pixhawk 6C (fmu-v6c)' } : { family: 'ardupilot', vehicle: 'copter', version: '4.6.3', type: 'official', git: '3fc7011a', board: 'CubeOrange' },
        })
        d.add(Object.assign(v, { serial: { device, baud: s.baud === 'auto' || s.baud === undefined ? 115200 : s.baud }, hitl: px4 ? { state: 'idle' } : null }))
        return { id }
      }
      const rm = path.match(/^\/vehicles\/([^/]+)$/)
      if (rm && method === 'DELETE') {
        const id = decodeURIComponent(rm[1])
        twins.delete(id)
        d.remove(id)
        return { id, removed: true }
      }
      // hardware in the loop
      const hl = path.match(/^\/vehicles\/([^/]+)\/hitl$/)
      if (hl) {
        const id = decodeURIComponent(hl[1])
        const v = vehicle(id)
        if (!v) throw fail(404, 'not_found', 'Unknown aircraft.', '未知飞机。')
        const st = hitlFor(v)
        if (method === 'GET') return st
        if (b.action === 'start') {
          if (b.confirm !== 'HITL') throw fail(400, 'confirm_required', 'Type HITL to confirm the propellers are removed.', '请输入 HITL 确认已拆桨。')
          if (!st.available) throw fail(409, 'hitl_unavailable', `Hardware in the loop is not available: ${st.reason}.`, `硬件在环不可用：${st.reason_zh}。`, { reason: st.reason, reason_zh: st.reason_zh })
          hitlStart(id)
        } else if (b.action === 'stop') hitlStop(id)
        return hitlFor(vehicle(id)!)
      }
      return undefined
    },
    stop() {
      for (const tm of timers) clearTimeout(tm)
      timers.clear()
    },
  }
}
