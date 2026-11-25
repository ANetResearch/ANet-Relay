// v0.6.0 fixture (?fixture=du7, 12): a mixed fleet around the ANetDU7. SIM-1, an ANetDU7 simulator, circles SIM-2 at
// 2.2 m with its pod tracking it; SIM-2, a second ANetDU7, waits at the origin; SIM-3 is a Micro 120; DU7-01 is
// a real ANetDU7 with a DYT pod on the aircraft network; DU7-02 a real ANetDU7 without a position or a pod; Quad 03
// a real micro without a position. The REST stand-in answers the gimbal routes (every action of 12 §5.2 that the
// window sends, with the pod's motion integrated at 10 Hz), the captures log, the model route and the ready-made
// plans, so the stage, the picture-in-picture and the inspector can be exercised without a backend.
import { RelayApiError, type GimbalView, type RelayEvent, type Vehicle } from '@/api'
import { fovDeg, isFixed, LENS_TABLE, type Lens } from '@/gimbal/model'

type V = Vehicle
type Base = (over: Partial<V> & Pick<V, 'id' | 'label' | 'kind'>) => V

/** The simulated pod of an ANetDU7 (12 §5.1) at rest. */
export function simGimbal(over: Partial<GimbalView> = {}): GimbalView {
  const lens: Lens = over.lens ?? 'visible1'
  const zoom = over.zoom ?? 1
  const [h, v] = fovDeg(lens, zoom)
  return {
    kind: 'sim', link: 'ok', address: null,
    yaw_deg: 0, pitch_deg: 0, roll_deg: 0, yaw_dps: 0, pitch_dps: 0,
    mode: 'follow', lens, pip: null, zoom, zoom_range: [...LENS_TABLE[lens].zoom], digital_zoom: false,
    fov_deg: [Math.round(h * 100) / 100, Math.round(v * 100) / 100], palette: 'white_hot',
    tracking: { state: 'off', algorithm: 'auto', x_deg: 0, y_deg: 0, box_px: [0, 0] },
    laser: { on: false, range_m: null }, recording: false, photos: 0,
    motors: true, enhance: false, osd: true, low_light: false,
    selftest: { done: true, gyro_ok: true, servo_ok: true, image_ok: true },
    target: null, focus: { mode: 'auto', position: null }, debug: null, age_s: 0.02,
    ...over,
  }
}

const nav = (x: number, y: number, z: number, yawDeg = 0): V['nav'] => ({
  frame: 'flu', position: [x, y, z], velocity: [0, 0, 0], body_velocity: [0, 0, 0], yaw_deg: yawDeg, yaw_rate_dps: 0,
})

const PX4_MODES = ['MANUAL', 'STABILIZED', 'ALTCTL', 'POSCTL', 'AUTO.LAND', 'OFFBOARD']
const DU7_BATTERY: V['battery'] = { voltage: 16.4, current: 1.1, remaining_pct: 94, cells: 4, level: 'ok' }
const DU7_SIM = (over: Partial<NonNullable<V['sim']>> = {}): NonNullable<V['sim']> => ({
  hover_assist: true, turbulence: 0.2, airframe: { preset: 'typical', seed: 707 }, engine: 'relay', firmware: null, frame: 'anet-du7', ...over,
})

/** The aircraft of ?fixture=du7. */
export function du7Vehicles(base: Base, now: number): V[] {
  return [
    base({
      id: 'sim-1', label: 'SIM-1', kind: 'sim', model: 'anet-du7', sysid: 11, autopilot: 'PX4', firmware: 'Relay SIM 0.6', armed: true,
      mode: 'OFFBOARD', mode_class: 'auto', modes: PX4_MODES, landed: 'air', sim: DU7_SIM(),
      position: { frame: 'sim', x: 1.4, y: 0, z: 2.2, vx: 0, vy: 0.5, vz: 0, age_s: 0.02 },
      attitude: { roll: 0.02, pitch: -0.03, yaw: 0 }, altitude_m: 2.2, heading_deg: 0, throttle_pct: 46, groundspeed_ms: 0.5,
      battery: { voltage: 15.8, current: 27.4, remaining_pct: 81, cells: 4, level: 'ok' }, flight_time_s: 92, armed_time_s: 97,
      nav: nav(1.4, 0, 2.2, 90), record_session: 51,
      // the pod tracks SIM-2 on the ground while SIM-1 orbits it
      gimbal: simGimbal({ yaw_deg: -90, pitch_deg: -57, mode: 'track', laser: { on: true, range_m: 2.6 }, photos: 2, tracking: { state: 'locked', algorithm: 'vehicle', x_deg: 0, y_deg: 0, box_px: [120, 90] } }),
    }),
    base({
      id: 'sim-2', label: 'SIM-2', kind: 'sim', model: 'anet-du7', sysid: 12, autopilot: 'PX4', firmware: 'Relay SIM 0.6', mode: 'POSCTL', mode_class: 'position',
      modes: PX4_MODES, sim: DU7_SIM({ airframe: { preset: 'typical', seed: 708 } }),
      position: { frame: 'sim', x: 0, y: 0, z: 0, vx: 0, vy: 0, vz: 0, age_s: 0.02 }, attitude: { roll: 0, pitch: 0, yaw: 0.6 }, heading_deg: 34,
      battery: DU7_BATTERY, nav: nav(0, 0, 0, 56), gimbal: simGimbal({ yaw_deg: 0, pitch_deg: -12 }),
    }),
    base({
      id: 'sim-3', label: 'SIM-3', kind: 'sim', model: 'micro', sysid: 13,
      position: { frame: 'sim', x: -2, y: 0, z: 0, vx: 0, vy: 0, vz: 0, age_s: 0.02 }, attitude: { roll: 0, pitch: 0, yaw: 2.2 }, heading_deg: 126,
      nav: nav(-2, 0, 0, -36),
    }),
    base({
      id: 'du7-01', label: 'DU7-01', kind: 'real', model: 'anet-du7', host: '10.0.0.21', sysid: 21, autopilot: 'PX4', firmware: 'PX4 1.15.4', mode: 'POSCTL',
      mode_class: 'position', modes: PX4_MODES, bridge: null,
      firmware_info: { family: 'px4', vehicle: 'copter', version: '1.15.4', type: 'official', git: 'c4e1b5d2', board: 'CUAV X7+' },
      capabilities: { manual: true, althold: true, position: true }, battery: DU7_BATTERY, gps: { fix: 0, sats: null, hdop: null },
      position: { frame: 'local', x: 0.3, y: -0.2, z: 0, vx: 0, vy: 0, vz: 0, age_s: 0.05 }, attitude: { roll: 0.003, pitch: -0.004, yaw: 1.57 }, heading_deg: 90,
      nav: nav(0.3, -0.2, 0, 0), confirmed: { signs: true, props: true }, preflight: { ok: true, blocking: [] },
      gimbal: simGimbal({ kind: 'dyt', address: '10.0.0.5:2000', pitch_deg: -20, yaw_deg: 15, zoom: 4, fov_deg: [16.43, 9.31], age_s: 0.03 }),
      statustext: [{ t: now - 40, severity: 6, text: 'Vision pose fused (FAST-LIO)' }],
    }),
    base({
      id: 'du7-02', label: 'DU7-02', kind: 'real', model: 'anet-du7', host: '10.0.0.22', sysid: 22, autopilot: 'PX4', firmware: 'PX4 1.15.4', mode: 'STABILIZED',
      modes: PX4_MODES, bridge: null, battery: DU7_BATTERY,
      firmware_info: { family: 'px4', vehicle: 'copter', version: '1.15.4', type: 'official', git: 'c4e1b5d2', board: 'CUAV X7+' },
    }),
    base({ id: 'q03', label: 'Quad 03', kind: 'real', host: '10.7.7.38', sysid: 3, model_confirmed: false }),
  ]
}

const ACTIONS_NEED_MOTION = new Set(['rate', 'angle', 'center', 'follow', 'lock', 'track'])

interface Deps {
  now(): number
  vehicles(): V[]
  mutate(id: string, fn: (v: V) => V): void
  emit(kind: string, vehicle: string | null, data?: Record<string, unknown>): void
}

/** Built-in plans of GET /plans/builtin (backend plans.BUILTIN). */
const BUILTIN = [
  {
    id: 'du7_box', title: 'ANetDU7 box test', title_zh: 'ANetDU7 方框测试',
    description: 'Take off to 2 m, 1 m forward and back, 1 m left and back, land; 0.5 m/s.',
    description_zh: '起飞到 2 米，前进 1 米并返回，左移 1 米并返回，降落；限速 0.5 m/s。',
    steps: [
      { action: 'takeoff', alt: 2, speed: 0.5 }, { action: 'move', forward: 1, speed: 0.5 }, { action: 'move', forward: -1, speed: 0.5 },
      { action: 'move', left: 1, speed: 0.5 }, { action: 'move', left: -1, speed: 0.5 }, { action: 'land' },
    ],
  },
  {
    id: 'du7_square10', title: 'ANetDU7 10 m square', title_zh: 'ANetDU7 10 米方形航线',
    description: 'Outdoors: take off to 10 m, fly a 10 m square (forward, left, back, right), land; 2.5 m/s level, 0.5 m/s vertical.',
    description_zh: '室外：起飞到 10 米，飞 10 米见方的航线（前、左、后、右），降落；水平限速 2.5 m/s，垂直 0.5 m/s。',
    steps: [
      { action: 'takeoff', alt: 10, speed: 2.5 }, { action: 'move', forward: 10, speed: 2.5 }, { action: 'move', left: 10, speed: 2.5 },
      { action: 'move', forward: -10, speed: 2.5 }, { action: 'move', left: -10, speed: 2.5 }, { action: 'land' },
    ],
  },
]

const err = (status: number, code: string, en: string, zh: string) => new RelayApiError(status, code, en, zh)

/** Gimbal, model and plan routes; motion of the pods and of SIM-1. */
export function startDu7Fixture(d: Deps, active: boolean) {
  const captures = new Map<string, Record<string, unknown>[]>()
  const zoomGoal = new Map<string, number>()
  // tracked points (ENU metres) the pods of these aircraft keep in the centre while their mode is track
  const locks = new Map<string, [number, number, number]>(active ? [['sim-1', [0, 0, 0.1]]] : [])
  let seq = 900
  if (active) {
    const shot = (n: number, dt: number, yaw: number, pitch: number, zoom: number, range: number) => ({
      seq: 800 + n, kind: 'photo', index: n, t: d.now() - dt, gimbal: { yaw_deg: yaw, pitch_deg: pitch, roll_deg: 0 }, lens: 'visible1', zoom, laser_range_m: range, target: null, nav: null,
    })
    captures.set('sim-1', [shot(1, 64, -88.2, -56.1, 2, 2.62), shot(2, 31, -91.4, -57.3, 4, 2.58)])
  }
  const setG = (id: string, fn: (g: GimbalView) => GimbalView) =>
    d.mutate(id, (v) => (v.gimbal ? { ...v, gimbal: fn(v.gimbal) } : v))

  function command(id: string, body: Record<string, unknown>): unknown {
    const v = d.vehicles().find((x) => x.id === id)
    if (!v) throw err(404, 'vehicle_not_found', 'No such aircraft.', '没有这架飞机。')
    const g = v.gimbal
    if (!g) throw err(404, 'gimbal_missing', 'This aircraft has no gimbal pod.', '这架飞机没有云台吊舱。')
    const a = String(body.action)
    if (ACTIONS_NEED_MOTION.has(a) && !g.motors) throw err(409, 'gimbal_busy', 'The gimbal motors are off.', '云台电机已关闭。')
    const num = (k: string, def = 0) => (typeof body[k] === 'number' ? (body[k] as number) : def)
    switch (a) {
      case 'rate':
        locks.delete(id)
        setG(id, (x) => ({ ...x, mode: 'rate', yaw_dps: num('yaw_dps'), pitch_dps: num('pitch_dps') }))
        break
      case 'stop':
        setG(id, (x) => ({ ...x, yaw_dps: 0, pitch_dps: 0, mode: x.mode === 'rate' ? 'follow' : x.mode }))
        break
      case 'center':
        locks.delete(id)
        setG(id, (x) => ({ ...x, yaw_deg: 0, pitch_deg: 0, yaw_dps: 0, pitch_dps: 0, mode: 'follow', tracking: { ...x.tracking, state: 'off', box_px: [0, 0] } }))
        break
      case 'angle':
        locks.delete(id)
        setG(id, (x) => ({ ...x, mode: 'angle', yaw_deg: num('yaw_deg', x.yaw_deg), pitch_deg: num('pitch_deg', x.pitch_deg) }))
        break
      case 'follow':
      case 'lock':
        setG(id, (x) => ({ ...x, mode: body.on === false ? (a === 'lock' ? 'follow' : 'lock') : a }))
        break
      case 'zoom': {
        const [lo, hi] = LENS_TABLE[g.lens].zoom
        if (isFixed(g.lens)) throw err(400, 'gimbal_unsupported', `the ${g.lens} lens has a fixed field of view`, `${g.lens} 镜头视场固定，不能变焦`)
        zoomGoal.set(id, Math.min(hi, Math.max(lo, num('level', 1))))
        break
      }
      case 'lens': {
        const lens = body.lens as Lens
        if (!LENS_TABLE[lens]) throw err(400, 'invalid_request', 'Unknown lens.', '未知镜头。')
        zoomGoal.delete(id)
        setG(id, (x) => {
          const [h, vv] = fovDeg(lens, 1)
          return { ...x, lens, zoom: 1, zoom_range: [...LENS_TABLE[lens].zoom], fov_deg: [h, vv], pip: body.pip === undefined ? x.pip : ((body.pip as Lens | null) ?? null) }
        })
        d.emit('gimbal_lens', id, { lens })
        break
      }
      case 'palette':
        setG(id, (x) => ({ ...x, palette: (body.palette as GimbalView['palette']) ?? x.palette }))
        break
      case 'photo': {
        const n = g.photos + 1
        seq++
        const c = { seq, kind: 'photo', index: n, t: d.now(), gimbal: { yaw_deg: g.yaw_deg, pitch_deg: g.pitch_deg, roll_deg: 0 }, lens: g.lens, zoom: g.zoom, laser_range_m: g.laser.range_m, target: null, nav: v.nav }
        captures.set(id, [...(captures.get(id) ?? []), c].slice(-500))
        setG(id, (x) => ({ ...x, photos: n }))
        d.emit('gimbal_capture', id, { seq, kind: 'photo', index: n, t: c.t, gimbal: c.gimbal, lens: g.lens, zoom: g.zoom, target: null })
        break
      }
      case 'record': {
        const on = body.on === true
        seq++
        captures.set(id, [...(captures.get(id) ?? []), { seq, kind: on ? 'record_start' : 'record_stop', t: d.now(), gimbal: { yaw_deg: g.yaw_deg, pitch_deg: g.pitch_deg, roll_deg: 0 }, lens: g.lens, zoom: g.zoom, laser_range_m: g.laser.range_m, target: null, nav: v.nav }])
        setG(id, (x) => ({ ...x, recording: on }))
        d.emit('gimbal_record', id, { on })
        break
      }
      case 'track': {
        const [hf, vf] = fovDeg(g.lens, g.zoom)
        const xd = (Math.atan((num('x') / 640) * Math.tan((hf / 2) * (Math.PI / 180))) * 180) / Math.PI
        const yd = (Math.atan((num('y') / 360) * Math.tan((vf / 2) * (Math.PI / 180))) * 180) / Math.PI
        const lock = groundPoint(v, g.yaw_deg + xd, g.pitch_deg - yd)
        if (lock) locks.set(id, lock)
        else locks.delete(id)
        setG(id, (x) => ({ ...x, mode: 'track', yaw_deg: x.yaw_deg + xd, pitch_deg: x.pitch_deg - yd, tracking: { state: 'locked', algorithm: (body.algorithm as GimbalView['tracking']['algorithm']) ?? 'auto', x_deg: 0, y_deg: 0, box_px: [112, 84] } }))
        break
      }
      case 'track_stop':
        locks.delete(id)
        setG(id, (x) => ({ ...x, mode: 'follow', tracking: { ...x.tracking, state: 'off', x_deg: 0, y_deg: 0, box_px: [0, 0] } }))
        break
      case 'laser':
        setG(id, (x) => ({ ...x, laser: { on: body.on === true, range_m: body.on === true ? rangeOf(v, x) : null } }))
        break
      default:
        throw err(400, 'invalid_request', `action ${a} is not in the fixture`, `演示数据不支持 ${a}`)
    }
    return { id, gimbal: d.vehicles().find((x) => x.id === id)?.gimbal }
  }

  /** Where a line of sight (frame angles of the pod) meets the ground, or 50 m out when it looks above it. */
  function groundPoint(v: V, yawDeg: number, pitchDeg: number): [number, number, number] | null {
    const p = v.position
    if (!p) return null
    const yaw = ((v.heading_deg ?? 0) + yawDeg) * (Math.PI / 180)
    const pitch = (pitchDeg + ((v.attitude?.pitch ?? 0) * 180) / Math.PI) * (Math.PI / 180)
    const dist = pitch < -0.02 ? Math.min(50, p.z / Math.tan(-pitch)) : 50
    return [p.x + dist * Math.sin(yaw), p.y + dist * Math.cos(yaw), pitch < -0.02 ? 0 : p.z + dist * Math.tan(pitch)]
  }

  /** Frame angles that put a point in the centre of a pod's picture. */
  function aimAt(v: V, q: [number, number, number]): { yaw: number; pitch: number } | null {
    const p = v.position
    if (!p) return null
    const dx = q[0] - p.x
    const dy = q[1] - p.y
    const dz = q[2] - p.z
    const bearing = (Math.atan2(dx, dy) * 180) / Math.PI
    const yaw = ((((bearing - (v.heading_deg ?? 0) + 180) % 360) + 360) % 360) - 180
    const pitch = (Math.atan2(dz, Math.hypot(dx, dy)) * 180) / Math.PI - ((v.attitude?.pitch ?? 0) * 180) / Math.PI
    return { yaw, pitch }
  }

  /** Range along the line of sight to the ground (z = 0). */
  function rangeOf(v: V, g: GimbalView): number | null {
    const z = v.position?.z ?? v.altitude_m ?? 0
    const down = -(g.pitch_deg + ((v.attitude?.pitch ?? 0) * 180) / Math.PI)
    if (down <= 1 || z < 0.05) return null
    return Math.round((Math.min(1200, (z + 0.12) / Math.sin((down * Math.PI) / 180)) * 100)) / 100
  }

  function handle(method: string, path: string, body: unknown): unknown {
    const b = (body ?? {}) as Record<string, unknown>
    if (path === '/plans/builtin') return { plans: BUILTIN }
    let m = path.match(/^\/vehicles\/([^/]+)\/gimbal(\/attach|\/captures)?(\?.*)?$/)
    if (m) {
      const id = decodeURIComponent(m[1])
      if (m[2] === '/captures') {
        const limit = Number(new URLSearchParams(m[3]?.slice(1) ?? '').get('limit') ?? 100)
        const list = captures.get(id) ?? []
        return { id, total: list.length, captures: list.slice(-limit) }
      }
      if (m[2] === '/attach' && method === 'POST') {
        const address = b.transport === 'tcp' ? `${String(b.host)}:${String(b.port ?? 2000)}` : String(b.device ?? '')
        d.mutate(id, (v) => ({ ...v, gimbal: simGimbal({ kind: 'dyt', link: 'connecting', address, age_s: null }) }))
        setTimeout(() => setG(id, (x) => ({ ...x, link: 'ok', age_s: 0.03 })), 1200)
        d.emit('gimbal_attached', id, { transport: b.transport, address })
        return { id, gimbal: d.vehicles().find((x) => x.id === id)?.gimbal }
      }
      if (method === 'DELETE') {
        d.mutate(id, (v) => ({ ...v, gimbal: null }))
        d.emit('gimbal_detached', id)
        return { id, detached: true }
      }
      if (method === 'POST') return command(id, b)
      return d.vehicles().find((x) => x.id === id)?.gimbal ?? null
    }
    m = path.match(/^\/vehicles\/([^/]+)\/model$/)
    if (m && method === 'POST') {
      const id = decodeURIComponent(m[1])
      const model = b.model === 'micro' ? 'micro' : 'anet-du7'
      d.mutate(id, (v) => ({ ...v, model, model_confirmed: true }))
      d.emit('model_changed', id, { model })
      return { id, model }
    }
    m = path.match(/^\/vehicles\/([^/]+)\/plan$/)
    if (m && method === 'POST' && typeof b.builtin === 'string') {
      const id = decodeURIComponent(m[1])
      const plan = BUILTIN.find((p) => p.id === b.builtin)
      if (!plan) throw err(400, 'invalid_request', 'builtin must be du7_box or du7_square10', 'builtin 必须是 du7_box 或 du7_square10')
      d.mutate(id, (v) => ({ ...v, plan: { id: 1, name: plan.id, state: 'running', index: 0, count: plan.steps.length, steps: plan.steps, results: plan.steps.map(() => null), detail: null, tuning: {} } }))
      return { id, plan: plan.id }
    }
    return undefined
  }

  /** One 100 ms step: SIM-1's circle, then the pods: rates, zoom, tracked points, laser range. */
  function tick(dt: number, t: number): void {
    if (active) {
      // SIM-1 circles the origin at 2.2 m, 1.4 m out, 0.5 m/s, nose along the track (counter-clockwise from above)
      const w = 0.5 / 1.4
      const a = t * w
      const x = 1.4 * Math.cos(a)
      const y = 1.4 * Math.sin(a)
      const vx = -0.5 * Math.sin(a)
      const vy = 0.5 * Math.cos(a)
      const heading = Math.atan2(vx, vy) // compass, radians
      const z = 2.2 + 0.05 * Math.sin(t * 0.7)
      d.mutate('sim-1', (v) => ({
        ...v,
        position: { frame: 'sim', x, y, z, vx, vy, vz: 0, age_s: 0.02 },
        attitude: { roll: -0.06, pitch: -0.05, yaw: heading },
        heading_deg: ((heading * 180) / Math.PI + 360) % 360,
        altitude_m: z,
        groundspeed_ms: 0.5,
        flight_time_s: v.flight_time_s + dt,
        nav: { frame: 'flu', position: [x, y, z], velocity: [vx, vy, 0], body_velocity: [0.5, 0, 0], yaw_deg: 90 - (heading * 180) / Math.PI, yaw_rate_dps: (w * 180) / Math.PI },
      }))
    }
    for (const v of d.vehicles()) {
      const g = v.gimbal
      if (!g) continue
      const goal = zoomGoal.get(v.id)
      let next = g
      const lock = g.mode === 'track' ? locks.get(v.id) : undefined
      const aim = lock ? aimAt(v, lock) : null
      if (aim) next = { ...next, yaw_deg: Math.round(aim.yaw * 100) / 100, pitch_deg: Math.round(aim.pitch * 100) / 100 }
      else if (g.yaw_dps || g.pitch_dps) {
        let yaw = g.yaw_deg + g.yaw_dps * dt
        yaw = ((((yaw + 180) % 360) + 360) % 360) - 180
        next = { ...next, yaw_deg: yaw, pitch_deg: Math.max(-120, Math.min(90, g.pitch_deg + g.pitch_dps * dt)) }
      }
      if (goal !== undefined) {
        const z = g.zoom * Math.pow(2, Math.sign(goal - g.zoom) * dt)
        const zoom = Math.abs(goal - g.zoom) < 0.05 || (goal > g.zoom ? z > goal : z < goal) ? goal : z
        if (zoom === goal) zoomGoal.delete(v.id)
        const [h, vv] = fovDeg(g.lens, zoom)
        next = { ...next, zoom, fov_deg: [Math.round(h * 100) / 100, Math.round(vv * 100) / 100] }
      }
      if (next.laser.on) next = { ...next, laser: { on: true, range_m: rangeOf(v, next) } }
      if (next !== g) d.mutate(v.id, (x) => ({ ...x, gimbal: next }))
    }
  }

  return { handle, tick }
}

export type Du7Fixture = ReturnType<typeof startDu7Fixture>
export type EmitKind = RelayEvent['kind']
