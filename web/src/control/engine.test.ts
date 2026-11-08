import { describe, expect, it } from 'vitest'
import type { Vehicle } from '@/api'
import type { PadProfile } from './devices'
import { InputEngine, type EngineDeps, type EngineNotice } from './engine'
import type { GamepadLike, StickFrame } from './mapping'
import type { ControlPrefs } from './shaping'

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
    prefs: {} as Partial<ControlPrefs>,
    profiles: {} as Record<string, PadProfile>,
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
    profiles: () => h.profiles,
    // sport feel and no detent keep the v0.2 stick values these tests were written for
    prefs: () => ({ deadzone: 0.08, expo: 0, feel: 'sport', throttleDetent: false, ...h.prefs }),
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
      simAssist: async (id, on) => void h.calls.push(`assist ${id} ${on}`),
      fire: (id, on) => void h.calls.push(`fire ${id} ${on} @${h.t}`),
      setFeel: (f) => void h.calls.push(`feel ${f}`),
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
    h.prefs = { feel: 'standard' } // W reaches 0.8 of full stick in the Standard feel (05 §6.1)
    possess('sim-1', { input: 'keyboard', pad: null, since: 0 })
    engine.keyDown('w')
    step(20, 10)
    expect(h.sent.every((s) => s.vehicle === 'sim-1')).toBe(true)
    expect(last(h, 'sim-1')!.pitch).toBeCloseTo(0.8, 6)
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

// ---------------------------------------------------------------------------------------------- WP-C (05 §6)

const sim = (id: string, over: Partial<Vehicle> = {}) => vehicle(id, { sim: { hover_assist: true, turbulence: 0.3, airframe: { preset: 'ideal' as const, seed: 0 } }, ...over })
const tx = (index: number, axes: number[], buttons: number[] = []) => gp(index, axes, buttons, 'EdgeTX RadioMaster TX16S Joystick')
const nonStd = (p: GamepadLike): GamepadLike => ({ ...p, mapping: '' })
const fires = (h: { calls: string[] }) => h.calls.filter((c) => c.startsWith('fire'))

describe('control feel (05 §6.1)', () => {
  it('scales the pad by the preset and keeps the throttle linear', () => {
    const { h, step } = harness([sim('sim-1', { mode: 'STABILIZE', mode_class: 'manual' })])
    h.prefs = { feel: 'gentle', expo: 0.45 }
    h.pads = [gp(0, [1, -0.5, 1, 0])]
    step(20, 2)
    const f = last(h, 'sim-1')!
    expect(f.roll).toBeCloseTo(0.5, 4)
    expect(f.yaw).toBeCloseTo(0.5, 4)
    // ly -0.5 raw is stick up 0.5: dead zone 0.08 then no expo: linear through the 0.5 hover point
    expect(f.throttle).toBeCloseTo(0.75, 4)
  })
  it('Mode 1 moves the throttle to the right stick', () => {
    const { h, step } = harness([sim('sim-1', { mode: 'STABILIZE', mode_class: 'manual' })])
    h.prefs = { stickMode: 1 }
    h.pads = [gp(0, [0, -1, 0, 0])]
    step(20, 2)
    expect(last(h, 'sim-1')).toMatchObject({ pitch: 1, throttle: 0.5 })
    h.pads = [gp(0, [0, 0, 0, -1])]
    step()
    expect(last(h, 'sim-1')).toMatchObject({ pitch: 0, throttle: 1 })
  })
  it('D-pad up / down steps the preset', () => {
    const { h, step } = harness([sim('sim-1')])
    h.prefs = { feel: 'standard' }
    h.pads = [gp(0)]
    step(20, 2)
    h.pads = [gp(0, [0, 0, 0, 0], [12])]
    step()
    h.pads = [gp(0)]
    step()
    h.pads = [gp(0, [0, 0, 0, 0], [13])]
    step()
    expect(h.calls.filter((c) => c.startsWith('feel'))).toEqual(['feel sport', 'feel gentle'])
  })
  it('keyboard reach follows the preset; Z returns the throttle to the hover point', () => {
    const { h, engine, step, possess } = harness([sim('sim-1', { mode: 'STABILIZE', mode_class: 'manual', armed: true, landed: 'air' })])
    h.prefs = { feel: 'gentle' }
    possess('sim-1', { input: 'keyboard', pad: null, since: 0 })
    engine.keyDown('d')
    engine.keyDown('r')
    step(20, 25)
    engine.keyUp('r')
    expect(last(h, 'sim-1')!.roll).toBeCloseTo(0.6, 6)
    expect(last(h, 'sim-1')!.throttle).toBeGreaterThan(0.6)
    engine.throttleToHover()
    step()
    expect(last(h, 'sim-1')!.throttle).toBe(0.5)
  })
})

describe('hold modes on the keyboard (05 §6.1)', () => {
  it('R / F are momentary: held climbs, released returns to hold', () => {
    const { h, engine, step, possess } = harness([sim('sim-1', { armed: true, landed: 'air' })])
    h.prefs = { feel: 'sport' }
    possess('sim-1', { input: 'keyboard', pad: null, since: 0 })
    step(20, 2)
    engine.keyDown('r')
    step(20, 20)
    expect(last(h, 'sim-1')!.throttle).toBe(1)
    engine.keyUp('r')
    step(20, 20)
    expect(last(h, 'sim-1')!.throttle).toBe(0.5)
    engine.keyDown('f')
    step(20, 20)
    engine.keyUp('f')
    expect(last(h, 'sim-1')!.throttle).toBe(0)
    step(20, 20)
    expect(last(h, 'sim-1')!.throttle).toBe(0.5)
  })
})

describe('throttle continuity (05 §6.2)', () => {
  it('ramps over 300 ms when the mode class changes the throttle meaning', () => {
    const v = sim('sim-1', { mode: 'STABILIZE', mode_class: 'manual', armed: true, landed: 'air', calibration: { signs: { roll: 1, pitch: 1, yaw: 1 }, hover: 0.3 } })
    const { h, step } = harness([v])
    h.pads = [gp(0, [0, -0.6, 0, 0])]
    step(20, 2)
    const before = last(h, 'sim-1')!.throttle
    v.mode = 'ALT_HOLD'
    v.mode_class = 'althold'
    step()
    const first = last(h, 'sim-1')!.throttle
    expect(Math.abs(first - before)).toBeLessThan(0.05)
    step(20, 20)
    const settled = last(h, 'sim-1')!.throttle
    expect(Math.abs(settled - before)).toBeGreaterThan(0.05)
  })
  it('a sim with hover assist off centres the throttle on 0.5', () => {
    const v = sim('sim-1', { mode: 'STABILIZE', mode_class: 'manual', sim: { hover_assist: false, turbulence: 0.3, airframe: { preset: 'ideal' as const, seed: 0 } }, calibration: { signs: { roll: 1, pitch: 1, yaw: 1 }, hover: 0.3 } })
    const { h, step } = harness([v])
    h.pads = [gp(0)]
    step(20, 2)
    expect(last(h, 'sim-1')!.throttle).toBe(0.5)
  })
})

describe('hover assist input (05 §6.3)', () => {
  const press = (h: ReturnType<typeof harness>['h'], step: ReturnType<typeof harness>['step'], b: number) => {
    h.pads = [gp(0, [0, 0, 0, 0], [b])]
    step()
    h.pads = [gp(0)]
    step()
  }
  it('Triangle switches a sim off when it holds, on when it does not; a real aircraft toggles modes', () => {
    const v = sim('sim-1')
    const { h, step } = harness([v])
    h.pads = [gp(0)]
    step(20, 2)
    press(h, step, 3)
    expect(h.calls).toContain('assist sim-1 false')
    v.sim = { hover_assist: false, turbulence: 0.3, airframe: { preset: 'ideal' as const, seed: 0 } }
    v.mode = 'STABILIZE'
    v.mode_class = 'manual'
    press(h, step, 3)
    expect(h.calls).toContain('assist sim-1 true')
    v.sim = { hover_assist: true, turbulence: 0.3, airframe: { preset: 'ideal' as const, seed: 0 } }
    press(h, step, 3)
    expect(h.calls.at(-1)).toBe('mode sim-1 ALT_HOLD')

    const real = harness([vehicle('q1')])
    real.possess('q1', { input: 'gamepad', pad: 1, since: 0 })
    real.h.pads = [gp(0)]
    real.step(20, 2)
    press(real.h, real.step, 3)
    expect(real.h.calls).toEqual(['mode q1 STABILIZE'])
  })
  it('H does the same for the window source', () => {
    const { h, engine, step, possess } = harness([sim('sim-1')])
    possess('sim-1', { input: 'keyboard', pad: null, since: 0 })
    step()
    engine.toggleAssist()
    expect(h.calls).toEqual(['assist sim-1 false'])
  })
})

describe('hold-to-fire hook (05 §7.4)', () => {
  it('R1 calls fire on press, every 100 ms while held and off at release', () => {
    const { h, step } = harness([sim('sim-1', { armed: true, landed: 'air' })])
    h.pads = [gp(0)]
    step(20, 2)
    h.pads = [gp(0, [0, 0, 0, 0], [5])]
    step(20, 10)
    h.pads = [gp(0)]
    step()
    const calls = fires(h)
    expect(calls[0]).toMatch(/^fire sim-1 true/)
    expect(calls.filter((c) => c.includes('true')).length).toBeGreaterThanOrEqual(2)
    expect(calls.at(-1)).toMatch(/^fire sim-1 false/)
    const ons = calls.filter((c) => c.includes('true')).map((c) => Number(c.split('@')[1]))
    for (let i = 1; i < ons.length; i++) expect(ons[i] - ons[i - 1]).toBe(100)
  })
  it('X fires for the window source and blur releases it', () => {
    const { h, engine, step, possess } = harness([sim('sim-1', { armed: true, landed: 'air' })])
    possess('sim-1', { input: 'keyboard', pad: null, since: 0 })
    engine.keyDown('x')
    step(20, 3)
    expect(fires(h)[0]).toMatch(/true/)
    h.focused = false
    step()
    expect(fires(h).at(-1)).toMatch(/false/)
  })
  it('never fires for a real aircraft or while sticks wait', () => {
    const real = harness([vehicle('q1', { armed: true, landed: 'air' })])
    real.possess('q1', { input: 'gamepad', pad: 1, since: 0 })
    real.h.pads = [gp(0, [0, 0, 0, 0], [5])]
    real.step(20, 10)
    expect(fires(real.h)).toEqual([])
  })
})

describe('transmitters (05 §6.3)', () => {
  it('reads AETR with an absolute throttle and enforces H5 in self-levelling modes', () => {
    const { h, step, possess } = harness([sim('sim-1', { mode: 'STABILIZE', mode_class: 'manual' })])
    possess('sim-1', { input: 'gamepad', pad: 1, since: 0 })
    h.pads = [nonStd(tx(0, [0, 0, 0.2, 0]))] // throttle at 60 %
    const out = step(20, 3)
    expect(out.pads[0].kind).toBe('transmitter')
    expect(out.awaiting['sim-1']).toBe('throttle')
    expect(h.sent).toEqual([])
    h.pads = [nonStd(tx(0, [0.5, 0, -1, 0]))] // throttle lowest
    const ok = step()
    expect(ok.awaiting['sim-1']).toBeUndefined()
    expect(last(h, 'sim-1')).toMatchObject({ throttle: 0 })
    expect(last(h, 'sim-1')!.roll).toBeGreaterThan(0)
  })
  it('H5 in hold modes wants the throttle centred within 10 %', () => {
    const { h, step, possess } = harness([sim('sim-1')])
    possess('sim-1', { input: 'gamepad', pad: 1, since: 0 })
    h.pads = [nonStd(tx(0, [0, 0, -1, 0]))]
    expect(step(20, 2).awaiting['sim-1']).toBe('throttle')
    h.pads = [nonStd(tx(0, [0, 0, 0.15, 0]))] // 57.5 %
    expect(step().awaiting['sim-1']).toBeUndefined()
  })
  it('switch bindings: stop at once, hover assist by position, arm held 1 s, fire', () => {
    const { h, step, possess } = harness([sim('sim-1', { mode: 'STABILIZE', mode_class: 'manual', sim: { hover_assist: false, turbulence: 0.3, airframe: { preset: 'ideal' as const, seed: 0 } } })])
    h.profiles = {
      'EdgeTX RadioMaster TX16S Joystick': {
        id: 'EdgeTX RadioMaster TX16S Joystick',
        kind: 'transmitter',
        axes: { roll: { index: 0, invert: false, min: -1, center: 0, max: 1 }, pitch: { index: 1, invert: false, min: -1, center: 0, max: 1 }, throttle: { index: 2, invert: false, min: -1, center: 0, max: 1 }, yaw: { index: 3, invert: false, min: -1, center: 0, max: 1 } },
        switches: [
          { action: 'assist', source: { type: 'axis', index: 4, above: true } },
          { action: 'arm', source: { type: 'axis', index: 5, above: true } },
          { action: 'stop', source: { type: 'axis', index: 6, above: true } },
          { action: 'fire', source: { type: 'axis', index: 7, above: true } },
        ],
        updatedAt: 1,
      },
    }
    possess('sim-1', { input: 'gamepad', pad: 1, since: 0 })
    const axes = (a: number, arm: number, stop: number, fire: number) => [0, 0, -1, 0, a, arm, stop, fire]
    h.pads = [nonStd(tx(0, axes(-1, -1, -1, -1)))]
    const out = step(20, 2)
    expect(out.pads[0].profiled).toBe(true)
    h.pads = [nonStd(tx(0, axes(1, -1, -1, -1)))]
    step()
    expect(h.calls).toContain('assist sim-1 true')
    h.pads = [nonStd(tx(0, axes(1, 1, -1, -1)))]
    step(100, 11)
    expect(h.calls).toContain('arm sim-1 true')
    h.pads = [nonStd(tx(0, axes(1, -1, -1, 1)))]
    step(20, 2)
    expect(fires(h)[0]).toMatch(/true/)
    h.pads = [nonStd(tx(0, axes(1, -1, 1, -1)))]
    step()
    expect(h.calls.filter((c) => c === 'stop sim-1')).toHaveLength(1)
    step(20, 3)
    expect(h.calls.filter((c) => c === 'stop sim-1')).toHaveLength(1)
  })
  it('ignores gamepad buttons on a transmitter (no accidental arm or possession)', () => {
    const { h, step } = harness([sim('sim-1')])
    h.pads = [nonStd(tx(0, [0, 0, -1, 0], [4, 5, 9]))]
    step(100, 15)
    expect(h.calls.filter((c) => c.startsWith('arm') || c.startsWith('release'))).toEqual([])
  })
})

describe('mapping wizard isolation (05 §6.3)', () => {
  it('a controller in setup sends nothing and its buttons act on nothing', () => {
    const { h, engine, step } = harness([sim('sim-1')])
    h.pads = [gp(0)]
    step(20, 2)
    const n = h.sent.length
    engine.setLearning(1, true)
    h.pads = [gp(0, [1, 1, 1, 1], [3, 5])]
    step(20, 5)
    expect(h.sent.length).toBe(n)
    expect(h.calls.filter((c) => c.startsWith('assist') || c.startsWith('fire'))).toEqual([])
    engine.setLearning(1, false)
    h.pads = [gp(0)]
    step(20, 2)
    expect(h.sent.length).toBeGreaterThan(n)
  })
})
