// Dev fixture (?fixture=busy|empty|alerts): realistic snapshots and five minutes of event history so the shell can
// be reviewed without a backend. busy = 6 aircraft (4 real incl. one lost and one armed under a human, 2 sims), one
// agent session, one critical alert; alerts = busy plus more alerts; empty = nothing connected. A 10 Hz ticker keeps
// the data moving; a small REST stand-in handles acknowledge, stop, possess and release so interactions can be tried.
import type { Alert, Fleet, FixtureName, RelayEvent, Vehicle } from '@/api'
import { installStream, setTransport, type RestTransport } from '@/api'
import { useRelay } from '@/store/relay'
import { useBoot } from '@/app/boot'
// WP-D: LAN arena stand-in (&arena=1 starts it enabled)
import { startArenaFixture } from './arenaFixture'
// WP-2a: airframe routes stand-in
import { startAirframeFixture } from './airframeFixture'
// WP-3: Lab stand-in (programs, tests, deployment, challenges, duels, traces)
import { startLabFixture } from './labFixture'
// WP-C v0.5: six-aircraft dogfight (?fixture=dogfight) and the busy fixture's leaderboard
import { busyBoard, startDogfightFixture } from './dogfightFixture'

type V = Vehicle

/** WP-C v0.5: the cage loop's rings (ENU m, yaw degrees, radius m) for the course layer. */
const CAGE_LOOP = {
  id: 'cage-loop', name_en: 'Cage loop', name_zh: '笼内环线', preset: true,
  rings: [
    { c: [0, 1, 1], yaw_deg: 90, r: 0.45 },
    { c: [1.2, 0, 1.2], yaw_deg: 0, r: 0.45 },
    { c: [0, -1, 1], yaw_deg: 90, r: 0.45 },
    { c: [-1.2, 0, 1.2], yaw_deg: 0, r: 0.45 },
  ],
}

const sensors = (baro = true): V['sensors'] => [
  { name: 'gyro', present: true, enabled: true, healthy: true },
  { name: 'accel', present: true, enabled: true, healthy: true },
  { name: 'mag', present: true, enabled: true, healthy: true },
  { name: 'baro', present: baro, enabled: baro, healthy: baro },
  { name: 'optical_flow', present: false, enabled: false, healthy: false },
]

function base(over: Partial<V> & Pick<V, 'id' | 'label' | 'kind'>): V {
  const sim = over.kind === 'sim'
  return {
    host: null,
    autopilot: 'ArduPilot',
    firmware: sim ? 'Relay SIM 0.1' : 'ArduCopter 4.5.7',
    sysid: 1,
    bridge: sim ? null : { version: 'mavesp8266 1.2.2', source: 'udp' },
    link: { state: 'ok', heartbeat_age_s: 0.4, rx_hz: 42, tx_hz: 51, loss_pct: 0.4, rtt_ms: 18 },
    armed: false,
    mode: 'STABILIZE',
    mode_class: 'manual',
    modes: ['STABILIZE', 'ALT_HOLD', 'LOITER', 'GUIDED', 'LAND', 'RTL'],
    landed: 'ground',
    crashed: false,
    attitude: { roll: 0.004, pitch: -0.006, yaw: 1.2 },
    rates: { roll: 0, pitch: 0, yaw: 0 },
    position: null,
    global: null,
    altitude_m: 0,
    climb_ms: 0,
    groundspeed_ms: 0,
    heading_deg: 69,
    throttle_pct: 0,
    battery: { voltage: 12.4, current: 0.4, remaining_pct: 92, cells: 3, level: 'ok' },
    gps: sim ? null : { fix: 0, sats: null, hdop: null },
    sensors: sensors(),
    statustext: [],
    rc: null,
    servo: null,
    flight_time_s: 0,
    armed_time_s: 0,
    capabilities: { manual: true, althold: true, position: sim },
    claim: null,
    possession: null,
    controller: 'none',
    sticks: null,
    skill: null,
    plan: null,
    health: { state: 'idle', window_s: 5, airborne_samples: 0, items: [] },
    calibration: { signs: { roll: 1, pitch: 1, yaw: 1 }, hover: sim ? 0.5 : 0.35 },
    confirmed: { signs: sim, props: sim },
    preflight: { ok: sim, blocking: sim ? [] : ['signs', 'props'] },
    course: null,
    procedure: null, // bench procedure (WP-B)
    record_session: null,
    sim: sim ? { hover_assist: true, turbulence: 0.3, airframe: { preset: 'typical', seed: 4242 } } : null, // WP-2a airframe
    program: null, // WP-1 onboard program
    programs_allowed: sim,
    ...over,
  }
}

function busyVehicles(now: number): V[] {
  const claimA = (since: number, suspended = false) => ({ session_id: 's-7f3a', name: 'harness-a', since, suspended })
  return [
    base({ id: 'q01', label: 'Quad 01', kind: 'real', host: '10.7.7.31', autopilot: 'PX4', firmware: 'PX4 1.15.2', sysid: 1, mode: 'STABILIZED', modes: ['MANUAL', 'STABILIZED', 'ALTCTL', 'POSCTL', 'AUTO.LAND', 'OFFBOARD'], record_session: 41 }),
    base({
      id: 'q02', label: 'Quad 02', kind: 'real', host: '10.7.7.36', sysid: 2, armed: true, mode: 'ALT_HOLD', mode_class: 'althold', landed: 'air',
      attitude: { roll: 0.0244, pitch: -0.014, yaw: 3.21 }, altitude_m: 1.26, climb_ms: 0.2, heading_deg: 184, throttle_pct: 41,
      battery: { voltage: 11.6, current: 6.8, remaining_pct: 62, cells: 3, level: 'ok' }, flight_time_s: 134, armed_time_s: 151,
      claim: claimA(now - 280, true), possession: { input: 'gamepad', pad: 1, since: now - 140 }, controller: 'human',
      sticks: { roll: 0.02, pitch: -0.05, yaw: 0, throttle: 0.41, age_s: 0.02, source: 'human' },
      confirmed: { signs: true, props: true }, preflight: { ok: true, blocking: [] }, record_session: 42,
      statustext: [{ t: now - 151, severity: 6, text: 'Arming motors' }, { t: now - 150, severity: 6, text: 'Throttle armed' }],
    }),
    base({
      id: 'q03', label: 'Quad 03', kind: 'real', host: '10.7.7.38', sysid: 3, armed: true, mode: 'ALT_HOLD', mode_class: 'althold', landed: 'unknown',
      link: { state: 'lost', heartbeat_age_s: 7.2, rx_hz: 0, tx_hz: 1, loss_pct: null, rtt_ms: null },
      attitude: { roll: -0.031, pitch: 0.02, yaw: 0.6 }, altitude_m: 0.94, climb_ms: null, heading_deg: 34,
      battery: { voltage: 10.9, current: null, remaining_pct: 31, cells: 3, level: 'warn' }, flight_time_s: 96, armed_time_s: 120,
      confirmed: { signs: true, props: true }, preflight: { ok: false, blocking: ['link'] }, record_session: 43,
      statustext: [{ t: now - 20, severity: 4, text: 'Battery 1 is low 10.9V' }],
    }),
    base({
      id: 'q04', label: 'Quad 04', kind: 'real', host: '10.7.7.41', autopilot: 'PX4', firmware: 'PX4 1.15.2', sysid: 4, mode: 'POSCTL', mode_class: 'position',
      modes: ['MANUAL', 'STABILIZED', 'ALTCTL', 'POSCTL', 'AUTO.LAND', 'OFFBOARD'],
      link: { state: 'delayed', heartbeat_age_s: 3.4, rx_hz: 6, tx_hz: 51, loss_pct: 12.5, rtt_ms: 240 },
      battery: { voltage: 12.1, current: 0.5, remaining_pct: 80, cells: 3, level: 'ok' },
      capabilities: { manual: true, althold: true, position: true },
      position: { frame: 'local', x: 0.1, y: -0.2, z: 0, vx: 0, vy: 0, vz: 0, age_s: 0.1 },
      claim: claimA(now - 200), controller: 'agent', record_session: 44,
    }),
    base({
      id: 'sim-1', label: 'SIM-1', kind: 'sim', sysid: 11,
      position: { frame: 'sim', x: -1, y: 0, z: 0, vx: 0, vy: 0, vz: 0, age_s: 0.02 },
      battery: { voltage: 12.5, current: 0.4, remaining_pct: 98, cells: 3, level: 'ok' }, record_session: 45,
    }),
    base({
      id: 'sim-2', label: 'SIM-2', kind: 'sim', sysid: 12, armed: true, mode: 'GUIDED', mode_class: 'auto', landed: 'air',
      position: { frame: 'sim', x: 0.6, y: 0.8, z: 1.0, vx: 0.8, vy: -0.6, vz: 0, age_s: 0.02 },
      attitude: { roll: 0.09, pitch: -0.12, yaw: 2.4 }, altitude_m: 1.0, climb_ms: 0.0, groundspeed_ms: 1.0, heading_deg: 137, throttle_pct: 52,
      battery: { voltage: 11.9, current: 7.4, remaining_pct: 74, cells: 3, level: 'ok' }, flight_time_s: 58, armed_time_s: 60,
      claim: claimA(now - 240), controller: 'agent',
      sticks: null, skill: { id: 3, name: 'goto', state: 'running', detail: 'ring 3' },
      course: { course_id: 'cage-loop', name_en: 'Cage loop', name_zh: '笼内环线', rings: [], state: 'running', next: 2, total: 4, started_at: now - 12.48, elapsed_s: 12.48, splits: [4.21, 3.42], best_s: 18.2, reason: null, origin: [0, 0, 0, 0] },
      record_session: 46,
    }),
  ]
}

function alertsFor(name: FixtureName, now: number): Alert[] {
  if (name === 'empty') return []
  const list: Alert[] = [
    { id: 'a-lost-q03', vehicle: 'q03', level: 'critical', code: 'link_lost', text_en: 'Link lost · Quad 03 · armed, sticks stopped', text_zh: '链路丢失 · Quad 03 · 已解锁，已停止发送杆量', since: now - 7, acked: false },
    { id: 'a-bat-q03', vehicle: 'q03', level: 'warn', code: 'battery_warn', text_en: 'Quad 03 battery low · 10.9 V', text_zh: 'Quad 03 电池电压低 · 10.9 V', since: now - 20, acked: false },
    { id: 'a-delay-q04', vehicle: 'q04', level: 'warn', code: 'link_delayed', text_en: 'Quad 04 link delayed · heartbeat 3.4 s', text_zh: 'Quad 04 链路延迟 · 心跳 3.4 秒', since: now - 3, acked: false },
  ]
  if (name === 'alerts') {
    list.push(
      { id: 'a-port', vehicle: null, level: 'critical', code: 'listen_error', text_en: 'UDP 14550 is in use by another ground station. Close it and retry.', text_zh: 'UDP 14550 已被其他地面站占用。请关闭后重试。', since: now - 42, acked: false },
      { id: 'a-crash', vehicle: 'sim-1', level: 'critical', code: 'crashed', text_en: 'SIM-1 crashed · hard landing at 3.1 m/s', text_zh: 'SIM-1 坠毁 · 以 3.1 m/s 硬着陆', since: now - 65, acked: false },
      { id: 'a-air', vehicle: 'q02', level: 'warn', code: 'airborne_no_controller', text_en: 'Quad 02 was airborne with no controller', text_zh: 'Quad 02 曾在空中且无人控制', since: now - 150, acked: true },
    )
  }
  return list
}

function history(name: FixtureName, now: number): RelayEvent[] {
  if (name === 'empty' || name === 'dogfight') return []
  let seq = 1000
  const ev = (dt: number, kind: RelayEvent['kind'], vehicle: string | null, data: Record<string, unknown> = {}): RelayEvent => ({
    seq: ++seq, t: now - dt, kind, vehicle, session: (data.name as string | undefined) ?? null, text_en: kind, text_zh: kind, data,
  })
  const h = { name: 'harness-a', session: 's-7f3a' }
  return [
    ev(295, 'vehicle_online', 'q01'), ev(294, 'vehicle_online', 'q02'), ev(293, 'vehicle_online', 'q03'), ev(292, 'vehicle_online', 'q04'),
    ev(291, 'sim_added', 'sim-1'), ev(290, 'sim_added', 'sim-2'),
    ev(285, 'agent_session_started', null, h),
    ev(280, 'claim_started', 'q02', h),
    ev(262, 'possession_started', 'q03', { input: 'screen' }),
    ev(255, 'possession_started', 'q02', { input: 'keyboard', suspended_claim: h }),
    ev(240, 'claim_started', 'sim-2', h),
    ev(231, 'possession_released', 'q03'),
    ev(222, 'relay_handback', 'q02', h),
    ev(200, 'claim_started', 'q04', h),
    ev(196, 'armed', 'q02'),
    ev(184, 'possession_started', 'sim-1', { input: 'gamepad', pad: 2 }),
    ev(172, 'stop_all', null),
    ev(140, 'possession_started', 'q02', { input: 'gamepad', pad: 1, suspended_claim: h }),
    ev(120, 'armed', 'q03'),
    ev(101, 'stop', 'sim-1'),
    ev(100, 'possession_released', 'sim-1'),
    ev(60, 'armed', 'sim-2'),
    ev(20, 'course_started', 'sim-2', { course_id: 'cage-loop' }),
    ev(7, 'link_lost', 'q03'),
    ev(3, 'link_delayed', 'q04'),
  ]
}

function makeFleet(name: FixtureName, now: number): Fleet {
  const vehicles = name === 'empty' || name === 'dogfight' ? [] : busyVehicles(now)
  return {
    t: now,
    version: '0.1.0',
    listen: name === 'alerts' ? { port: 14550, ok: false, error: 'address in use' } : { port: 14550, ok: true, error: null },
    scan: { running: false, done: 0, total: 0, networks: ['10.7.7.0/24'], found: 0, error: null },
    sessions: name === 'empty' ? [] : [{ session_id: 's-7f3a', name: 'harness-a', expires_in_s: 12, vehicles: name === 'dogfight' ? ['sim-4'] : ['q02', 'q04', 'sim-2'] }],
    safety_volume: { x: 3, y: 3, z: 2 },
    vehicles,
    alerts: name === 'dogfight' ? [] : alertsFor(name, now),
  }
}

/** Starts the fixture: installs stand-in transports, seeds history, ticks at 10 Hz. Returns a stop function. */
export function startFixture(name: FixtureName): () => void {
  let now = Date.now() / 1000
  let fleet = makeFleet(name, now)
  let seq = 5000
  const store = useRelay.getState()
  const emit = (kind: RelayEvent['kind'], vehicle: string | null, data: Record<string, unknown> = {}) =>
    useRelay.getState().applyEvents([{ seq: ++seq, t: now, kind, vehicle, session: null, text_en: kind, text_zh: kind, data }])
  const mutate = (id: string, fn: (v: V) => V) => {
    fleet = { ...fleet, vehicles: fleet.vehicles.map((v) => (v.id === id ? fn(v) : v)) }
    useRelay.getState().applyFleet({ ...fleet, t: now })
  }

  // WP-D: arena stand-in (WP-C v0.5: the dogfight fixture drives the arena view itself)
  const arena = name === 'dogfight' ? { settings: () => {}, stop: () => {} } : startArenaFixture()
  // WP-C v0.5: the dogfight moves the local simulators, the arena view and the event effects
  const dogfight = name === 'dogfight'
    ? startDogfightFixture({
        base,
        setVehicles: (vs) => {
          fleet = { ...fleet, vehicles: vs }
        },
        emit: (kind, vehicle, data) => emit(kind as RelayEvent['kind'], vehicle, data),
      })
    : null
  if (dogfight) fleet = { ...fleet, vehicles: dogfight.vehicles(now) }
  // WP-2a: airframe stand-in; a change updates the snapshot reference and emits the event
  const airframe = startAirframeFixture((id, preset, seed) => {
    mutate(id, (x) => (x.sim ? { ...x, sim: { ...x.sim, airframe: { preset, seed } } } : x))
    emit('sim_airframe_changed', id, { preset, seed, by: 'operator' })
  })

  // WP-3: Lab stand-in
  const lab = startLabFixture({
    now: () => now,
    vehicle: (id) => fleet.vehicles.find((x) => x.id === id),
    mutate,
    emit: (kind, vehicle, data) => emit(kind as RelayEvent['kind'], vehicle, data),
  })

  const transport: RestTransport = {
    async request(method, path, body) {
      const labbed = lab.handle(method, path, body) // WP-3
      if (labbed !== undefined) return labbed
      // WP-C v0.5: leaderboard, match and dogfight scenario routes
      const fought = dogfight?.handle(method, path, body)
      if (fought !== undefined) return fought
      if (path.startsWith('/arena/leaderboard')) return busyBoard((new URLSearchParams(path.split('?')[1] ?? '').get('by') ?? 'downs') as Parameters<typeof busyBoard>[0])
      if (path === '/scenarios/dogfight') return { running: false, started_at: null, ends_at: null, config: null, aircraft: [] }
      // WP-C v0.5: ring geometry of SIM-2's course, so the voxel rings show in the fixture
      if (path === '/courses') return { courses: [CAGE_LOOP] }
      // WP-D: the arena toggle and settings
      if (path === '/settings' && method === 'PUT') {
        arena.settings(body)
        return { ok: true }
      }
      const m = path.match(/^\/vehicles\/([^/]+)\/(\w+)/)
      if (path === '/health') return { ok: true, version: '0.1.0-fixture', uptime_s: 1 }
      if (path === '/fleet') return fleet
      if (path.startsWith('/events')) return []
      const ack = path.match(/^\/alerts\/([^/]+)\/ack$/)
      if (ack) {
        fleet = { ...fleet, alerts: fleet.alerts.map((a) => (a.id === decodeURIComponent(ack[1]) ? { ...a, acked: true } : a)) }
        useRelay.getState().applyFleet({ ...fleet, t: now })
        return { ok: true }
      }
      if (path === '/stop-all') {
        emit('stop_all', null)
        return { ok: true }
      }
      const frame = airframe.handle(method, path, body, (id) => fleet.vehicles.find((x) => x.id === id)?.sim?.hover_assist ?? true) // WP-2a
      if (frame) return frame
      const assist = path.match(/^\/sims\/([^/]+)\/assist$/)
      if (assist && method === 'POST') {
        const id = decodeURIComponent(assist[1])
        const on = Boolean((body as { on?: unknown } | undefined)?.on)
        mutate(id, (x) => (x.sim ? { ...x, sim: { ...x.sim, hover_assist: on }, ...(on ? {} : { mode: 'STABILIZE', mode_class: 'manual' as const }) } : x))
        emit('sim_assist_changed', id, { hover_assist: on, by: 'operator' })
        return { id, hover_assist: on }
      }
      if (m && method === 'POST') {
        const id = decodeURIComponent(m[1])
        if (m[2] === 'stop') emit('stop', id)
        if (m[2] === 'possess') {
          const v = fleet.vehicles.find((x) => x.id === id)
          mutate(id, (x) => ({ ...x, possession: { input: 'keyboard', pad: null, since: now }, controller: 'human', claim: x.claim ? { ...x.claim, suspended: true } : null }))
          emit('possession_started', id, { input: 'keyboard', suspended_claim: v?.claim ? { name: v.claim.name } : null })
        }
        if (m[2] === 'release') {
          const v = fleet.vehicles.find((x) => x.id === id)
          if (v?.claim) {
            mutate(id, (x) => ({ ...x, possession: null, controller: 'agent', claim: x.claim ? { ...x.claim, suspended: false } : null }))
            emit('relay_handback', id, { name: v.claim.name })
          } else {
            mutate(id, (x) => ({ ...x, possession: null, controller: 'none' }))
            emit('possession_released', id)
          }
        }
      }
      return { ok: true }
    },
  }
  setTransport(transport)
  installStream({ sendSticks: () => true, cancelSticks: () => {}, reconnect: () => {} })

  store.setConn('open', { attempt: 0, nextRetryMs: null })
  store.setHello({ type: 'hello', version: '0.1.0-fixture', operator: true, session: null })
  store.applyEvents(history(name, now))

  const boot = useBoot.getState()
  const timers = [
    setTimeout(() => boot.setStage(1), 120),
    setTimeout(() => boot.setStage(2), 360),
    setTimeout(() => {
      useRelay.getState().applyFleet(fleet)
      useBoot.getState().setStage(3)
    }, 640),
  ]

  let k = 0
  const tick = setInterval(() => {
    now = Date.now() / 1000
    k++
    // WP-C v0.5: SIM-2 grazes the tube of its next ring every 4 s (the ring flashes white)
    // ring numbers are 1-based as the backend sends them (course.py): the second ring
    if (name !== 'dogfight' && name !== 'empty' && k % 40 === 0) emit('ring_touch' as RelayEvent['kind'], 'sim-2', { vehicle: 'sim-2', ring: 2, point: [0.4, 1.2, 1.1] })
    const w = Math.sin(k / 9)
    fleet = {
      ...fleet,
      t: now,
      sessions: fleet.sessions.map((s) => ({ ...s, expires_in_s: 15 - ((k / 10) % 15) })),
      vehicles: fleet.vehicles.map((v) => {
        const airborne = v.landed === 'air'
        const link =
          v.link.state === 'lost' || v.link.state === 'delayed'
            ? { ...v.link, heartbeat_age_s: (v.link.heartbeat_age_s ?? 0) + 0.1 }
            : { ...v.link, heartbeat_age_s: 0.2 + Math.abs(w) * 0.3 }
        const course = v.course && v.course.state === 'running' ? { ...v.course, elapsed_s: v.course.elapsed_s + 0.1 } : v.course
        return {
          ...v,
          link,
          course,
          flight_time_s: airborne ? v.flight_time_s + 0.1 : v.flight_time_s,
          attitude: v.attitude && airborne && v.link.state !== 'lost'
            ? { roll: v.attitude.roll + w * 0.002, pitch: v.attitude.pitch - w * 0.0015, yaw: v.attitude.yaw + 0.002 }
            : v.attitude,
        }
      }),
    }
    useRelay.getState().applyFleet(fleet)
  }, 100)

  return () => {
    timers.forEach(clearTimeout)
    clearInterval(tick)
    arena.stop() // WP-D
    dogfight?.stop() // WP-C v0.5
    lab.stop() // WP-3
  }
}
