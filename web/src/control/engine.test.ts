import { describe, expect, it } from 'vitest'
import type { Vehicle } from '@/api'
import { InputEngine, type EngineDeps, type EngineNotice } from './engine'
import type { GamepadLike, StickFrame } from './mapping'

type Possession = Vehicle['possession']

function vehicle(id: string, over: Partial<Vehicle> = {}): Vehicle {
  return {
    id, kind: id.startsWith('sim') ? 'sim' : 'real', label: id.toUpperCase(), host: null, autopilot: 'ArduPilot', firmware: null, sysid: 1,
    bridge: null, link: { state: 'ok', heartbeat_age_s: 0.1, rx_hz: 30, tx_hz: 50, loss_pct: 0, rtt_ms: null },
    armed: false, mode: 'ALT_HOLD', mode_class: 'althold', modes: ['STABILIZE', 'ALT_HOLD', 'LOITER', 'GUIDED', 'LAND', 'RTL'],
    landed: 'ground', crashed: false, attitude: null, rates: null, position: null, global: null, altitude_m: 0, climb_ms: 0,
    groundspeed_ms: 0, heading_deg: 0, throttle_pct: 0,
    battery: { voltage: 12.4, current: 0.4, remaining_pct: 98, cells: 3, level: 'ok' }, gps: null, sensors: [], statustext: [],
    rc: null, servo: null, flight_time_s: 0, armed_time_s: 0, capabilities: { manual: true, althold: true, position: true },
    claim: null, possession: null, controller: 'none', sticks: null, skill: null,
    calibration: { signs: { roll: 1, pitch: 1, yaw: 1 }, hover: 0.5 }, confirmed: { signs: true, props: true },
    preflight: { ok: true, blocking: [] }, course: null, record_session: null,
    ...over,
  } as Vehicle
}

function gp(index: number, axes = [0, 0, 0, 0], pressed: number[] = [], id = 'DualSense Wireless Controller'): GamepadLike {
  return {
    id, index, connected: true, mapping: 'standard', axes,
    buttons: Array.from({ length: 18 }, (_, i) => ({ pressed: pressed.includes(i), value: pressed.includes(i) ? 1 : 0 })),
  }
}

function harness(vehicles: Vehicle[]) {
  const h = {
    t: 0,
    vehicles,
    pads: [] as (GamepadLike | null)[],
    selected: null as string | null,
    focused: true,
    sent: [] as { vehicle: string; f: StickFrame }[],
    cancelled: [] as string[],
    calls: [] as string[],
    notices: [] as EngineNotice[],
  }
  const possess = (id: string, input: Possession) => {
    for (const v of h.vehicles) {
      if (v.possession && input && v.possession.input === input.input && v.possession.pad === input.pad) {
        v.possession = null
        v.controller = 'none'
      }
    }
    const v = h.vehicles.find((x) => x.id === id)!
    v.possession = input
    v.controller = input ? 'human' : v.claim ? 'agent' : 'none'
  }
  const deps: EngineDeps = {
    now: () => h.t,
    gamepads: () => h.pads,
    vehicles: () => h.vehicles,
    selected: () => h.selected,
    focused: () => h.focused,
    send: (vehicle, f) => h.sent.push({ vehicle, f }),
    cancel: (vehicle) => h.cancelled.push(vehicle),
    prefs: () => ({ deadzone: 0.08, expo: 0 }),
    act: {
      possess: async (id, pad) => {
        h.calls.push(`possess ${id} ${pad ?? 'kb'}`)
        possess(id, pad === null ? { input: 'keyboard', pad: null, since: h.t } : { input: 'gamepad', pad, since: h.t })
      },
      release: async (id) => {
        h.calls.push(`release ${id}`)
        possess(id, null)
      },
      arm: async (id, on) => void h.calls.push(`arm ${id} ${on}`),
      stop: (id) => void h.calls.push(`stop ${id}`),
      mode: async (id, name) => void h.calls.push(`mode ${id} ${name}`),
      course: (id) => void h.calls.push(`course ${id}`),
      resetSim: async (id) => void h.calls.push(`reset ${id}`),
      moveSelection: (d) => void h.calls.push(`select ${d}`),
      viewCycle: () => void h.calls.push('view'),
      notice: (n) => void h.notices.push(n),
    },
  }
  const engine = new InputEngine(deps)
  const step = (ms = 20, n = 1) => {
    let out = engine.tick()
    const hints = [...out.hints]
    for (let i = 0; i < n; i++) {
      h.t += ms
      out = engine.tick()
      hints.push(...out.hints)
    }
    return { ...out, hints }
  }
  return { h, engine, step, possess }
}

const last = (h: { sent: { vehicle: string; f: StickFrame }[] }, id: string) => [...h.sent].reverse().find((s) => s.vehicle === id)?.f

describe('keyboard source', () => {
  it('sends frames for the keyboard-possessed aircraft only', () => {
    const { h, engine, step, possess } = harness([vehicle('sim-1'), vehicle('sim-2')])
    possess('sim-1', { input: 'keyboard', pad: null, since: 0 })
    engine.keyDown('w')
    step(20, 10)
    expect(h.sent.every((s) => s.vehicle === 'sim-1')).toBe(true)
    expect(last(h, 'sim-1')!.pitch).toBeCloseTo(0.5, 6)
    expect(last(h, 'sim-1')!.throttle).toBe(0.5)
  })
  it('seeds a low throttle on the ground in a self-levelling mode so the aircraft can arm', () => {
    const { h, step, possess } = harness([vehicle('q1', { mode: 'STABILIZE', mode_class: 'manual', calibration: { signs: { roll: 1, pitch: 1, yaw: 1 }, hover: 0.35 } })])
    possess('q1', { input: 'keyboard', pad: null, since: 0 })
    step()
    expect(last(h, 'q1')!.throttle).toBe(0)
  })
  it('keeps the hover throttle when possessing an airborne aircraft', () => {
    const { h, step, possess } = harness([vehicle('q1', { armed: true, landed: 'air', mode: 'STABILIZE', mode_class: 'manual', calibration: { signs: { roll: 1, pitch: 1, yaw: 1 }, hover: 0.35 } })])
    possess('q1', { input: 'keyboard', pad: null, since: 0 })
    step()
    expect(last(h, 'q1')!.throttle).toBe(0.35)
  })
})

describe('blur and focus (R-26, 02 §5.3)', () => {
  it('stops at once on blur and resumes only after a stick movement', () => {
    const { h, engine, step, possess } = harness([vehicle('sim-1')])
    possess('sim-1', { input: 'keyboard', pad: null, since: 0 })
    step(20, 3)
    const before = h.sent.length
    h.focused = false
    const out = step(20, 5)
    expect(out.paused).toBe(true)
    expect(h.cancelled).toContain('sim-1')
    expect(h.sent.length).toBe(before)
    h.focused = true
    const back = step(20, 5)
    expect(back.paused).toBe(false)
    expect(back.awaiting['sim-1']).toBe('focus')
    expect(h.sent.length).toBe(before)
    engine.keyDown('d')
    const resumed = step(20, 2)
    expect(resumed.awaiting['sim-1']).toBeUndefined()
    expect(h.sent.length).toBeGreaterThan(before)
  })
  it('holds a gamepad until it moves after focus returns', () => {
    const { h, step, possess } = harness([vehicle('sim-1')])
    h.pads = [gp(0)]
    step(20, 2)
    expect(h.vehicles[0].possession?.pad).toBe(1)
    step(20, 2)
    h.focused = false
    step(20, 2)
    h.focused = true
    step(20, 3)
    const n = h.sent.length
    h.pads = [gp(0, [0, -0.1, 0, 0])]
    step(20, 2)
    expect(h.sent.length).toBe(n)
    h.pads = [gp(0, [0, -0.6, 0, 0])]
    step(20, 2)
    expect(h.sent.length).toBeGreaterThan(n)
    void possess
  })
})

describe('link loss (H3)', () => {
  it('stops while lost and waits for a movement after recovery', () => {
    const { h, engine, step, possess } = harness([vehicle('sim-1')])
    possess('sim-1', { input: 'keyboard', pad: null, since: 0 })
    step(20, 2)
    h.vehicles[0].link = { ...h.vehicles[0].link, state: 'lost' }
    const n0 = h.sent.length
    step(20, 5)
    expect(h.sent.length).toBe(n0)
    expect(h.cancelled).toContain('sim-1')
    h.vehicles[0].link = { ...h.vehicles[0].link, state: 'ok' }
    const out = step(20, 3)
    expect(out.awaiting['sim-1']).toBe('link')
    expect(h.sent.length).toBe(n0)
    engine.keyDown('w')
    step(20, 2)
    expect(h.sent.length).toBeGreaterThan(n0)
  })
  it('keeps sending while delayed', () => {
    const { h, step, possess } = harness([vehicle('sim-1')])
    possess('sim-1', { input: 'keyboard', pad: null, since: 0 })
    h.vehicles[0].link = { ...h.vehicles[0].link, state: 'delayed' }
    step(20, 3)
    expect(h.sent.length).toBeGreaterThan(0)
  })
})

describe('gamepads (02 §7.3)', () => {
  it('auto-binds a new pad to the lowest free sim and never to a real aircraft', () => {
    const { h, step } = harness([vehicle('real-1'), vehicle('sim-2'), vehicle('sim-1')])
    h.pads = [gp(0)]
    const out = step()
    expect(h.calls).toEqual(['possess sim-1 1'])
    expect(out.hints[0]).toMatchObject({ pad: 1, state: 'flying', vehicle: 'sim-1', model: 'dualsense' })
    h.pads = [gp(0), gp(1)]
    const out2 = step()
    expect(h.calls).toEqual(['possess sim-1 1', 'possess sim-2 2'])
    expect(out2.pads.map((p) => [p.index, p.vehicle])).toEqual([[1, 'sim-1'], [2, 'sim-2']])
  })
  it('a third pad with no free sim asks for Options', () => {
    const { h, step } = harness([vehicle('real-1'), vehicle('sim-1')])
    h.pads = [gp(0)]
    step()
    h.pads = [gp(0), gp(1)]
    const out = step()
    expect(out.hints[0]).toMatchObject({ pad: 2, state: 'holdOptions', vehicle: null })
    expect(h.calls).toEqual(['possess sim-1 1'])
  })
  it('the fifth controller is refused with a notice', () => {
    const { h, step } = harness([])
    h.pads = [0, 1, 2, 3, 4].map((i) => gp(i, [0, 0, 0, 0], [], `pad ${i}`))
    const out = step()
    expect(out.pads.map((p) => p.index)).toEqual([1, 2, 3, 4])
    expect(h.notices).toEqual([{ kind: 'maxPads' }])
  })
  it('maps the sticks with the throttle curve', () => {
    const { h, step } = harness([vehicle('sim-1', { mode: 'STABILIZE', mode_class: 'manual', calibration: { signs: { roll: 1, pitch: 1, yaw: 1 }, hover: 0.4 } })])
    h.pads = [gp(0)]
    step(20, 2)
    expect(last(h, 'sim-1')).toEqual({ roll: 0, pitch: 0, yaw: 0, throttle: 0.4 })
    h.pads = [gp(0, [0, -1, 1, 0])]
    step()
    expect(last(h, 'sim-1')).toEqual({ roll: 1, pitch: 0, yaw: 0, throttle: 1 })
  })
})

describe('controller disconnect (02 §7.3)', () => {
  it('stops at once, keeps the possession for 3 s and releases after', () => {
    const { h, step } = harness([vehicle('sim-1')])
    h.pads = [gp(0)]
    step(20, 3)
    const n = h.sent.length
    h.pads = [null]
    const out = step()
    expect(h.cancelled).toContain('sim-1')
    expect(out.padLost['sim-1']).toMatchObject({ pad: 1 })
    step(100, 25)
    expect(h.sent.length).toBe(n)
    expect(h.calls).not.toContain('release sim-1')
    step(100, 6)
    expect(h.calls).toContain('release sim-1')
  })
  it('restores the same controller when it comes back within 3 s', () => {
    const { h, step } = harness([vehicle('sim-1')])
    h.pads = [gp(0)]
    step(20, 2)
    h.pads = [null]
    step(100, 10)
    h.pads = [gp(1)]
    const out = step(20, 3)
    expect(out.padLost['sim-1']).toBeUndefined()
    expect(out.pads).toMatchObject([{ index: 1, id: 'DualSense Wireless Controller', model: 'dualsense', connected: true, vehicle: 'sim-1' }])
    step(100, 40)
    expect(h.calls.filter((c) => c.startsWith('release'))).toEqual([])
  })
  it('switches an airborne sim to LOITER while it waits', () => {
    const { h, step } = harness([vehicle('sim-1', { armed: true, landed: 'air' })])
    h.pads = [gp(0)]
    step(20, 2)
    h.pads = []
    step()
    expect(h.calls).toContain('mode sim-1 LOITER')
  })
})

describe('buttons', () => {
  it('L2 + R2 stops this controller aircraft once, instantly', () => {
    const { h, step } = harness([vehicle('sim-1', { armed: true, landed: 'air' })])
    h.pads = [gp(0)]
    step(20, 2)
    h.pads = [gp(0, [0, 0, 0, 0], [6, 7])]
    step(20, 5)
    expect(h.calls.filter((c) => c.startsWith('stop'))).toEqual(['stop sim-1'])
    h.pads = [gp(0)]
    step()
    h.pads = [gp(0, [0, 0, 0, 0], [6, 7])]
    step()
    expect(h.calls.filter((c) => c.startsWith('stop'))).toHaveLength(2)
  })
  it('Options held 1 s possesses the selected aircraft; a shorter press does nothing', () => {
    const { h, step } = harness([vehicle('q1')])
    h.selected = 'q1'
    h.pads = [gp(0)]
    step(20, 2)
    h.pads = [gp(0, [0, 0, 0, 0], [9])]
    const mid = step(100, 5)
    expect(mid.holds['q1']).toMatchObject({ kind: 'options', pad: 1, durationMs: 1000 })
    h.pads = [gp(0)]
    step(100, 2)
    expect(h.calls).toEqual([])
    h.pads = [gp(0, [0, 0, 0, 0], [9])]
    step(100, 11)
    expect(h.calls).toEqual(['possess q1 1'])
    h.pads = [gp(0)]
    step()
    h.pads = [gp(0, [0, 0, 0, 0], [9])]
    step(100, 11)
    expect(h.calls).toEqual(['possess q1 1', 'release q1'])
  })
  it('Options refuses an aircraft flown by another input', () => {
    const { h, step, possess } = harness([vehicle('q1')])
    possess('q1', { input: 'keyboard', pad: null, since: 0 })
    h.selected = 'q1'
    h.pads = [gp(0)]
    step(20, 2)
    h.pads = [gp(0, [0, 0, 0, 0], [9])]
    step(100, 12)
    expect(h.calls).toEqual([])
    expect(h.notices[0]).toEqual({ kind: 'taken', vehicle: 'q1', by: 'keyboard' })
  })
  it('L1 + R1 arms a sim after 1 s and only tells a real aircraft to type ARM', () => {
    const sim = harness([vehicle('sim-1')])
    sim.h.pads = [gp(0)]
    sim.step(20, 2)
    sim.h.pads = [gp(0, [0, 0, 0, 0], [4, 5])]
    sim.step(100, 11)
    expect(sim.h.calls).toContain('arm sim-1 true')

    const real = harness([vehicle('q1')])
    real.possess('q1', { input: 'gamepad', pad: 1, since: 0 })
    real.h.pads = [gp(0)]
    real.step(20, 2)
    real.h.pads = [gp(0, [0, 0, 0, 0], [4, 5])]
    real.step(100, 15)
    expect(real.h.calls.some((c) => c.startsWith('arm'))).toBe(false)
    expect(real.h.notices).toEqual([{ kind: 'realArm' }])
  })
  it('single presses: mode, course, selection, view', () => {
    const { h, step } = harness([vehicle('sim-1'), vehicle('q1')])
    h.pads = [gp(0)]
    step(20, 2)
    const press = (b: number) => {
      h.pads = [gp(0, [0, 0, 0, 0], [b])]
      step()
      h.pads = [gp(0)]
      step()
    }
    press(3)
    press(2)
    press(17)
    expect(h.calls).toEqual(['possess sim-1 1', 'mode sim-1 STABILIZE', 'course sim-1', 'view'])
    press(15)
    expect(h.calls).not.toContain('select 1')
  })
})
