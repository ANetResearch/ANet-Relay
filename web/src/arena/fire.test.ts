// The WP-D fire binding of the input engine (05 §7.4): R1 alone on the pad that flies a simulator, X for the
// window source, refreshed every 100 ms while held, released on letting go, on blur and for real aircraft never.
import { describe, expect, it } from 'vitest'
import type { Vehicle } from '@/api'
import { FIRE_REFRESH_MS, InputEngine, type EngineDeps } from '@/control/engine'
import type { GamepadLike } from '@/control/mapping'

function vehicle(id: string, possession: Vehicle['possession']): Vehicle {
  return {
    id, kind: id.startsWith('sim') ? 'sim' : 'real', label: id, host: null, autopilot: 'ArduPilot', firmware: null, sysid: 1, bridge: null,
    link: { state: 'ok', heartbeat_age_s: 0.1, rx_hz: 30, tx_hz: 50, loss_pct: 0, rtt_ms: null },
    armed: true, mode: 'ALT_HOLD', mode_class: 'althold', modes: ['ALT_HOLD'], landed: 'air', crashed: false, attitude: null, rates: null,
    position: null, global: null, altitude_m: 1, climb_ms: 0, groundspeed_ms: 0, heading_deg: 0, throttle_pct: 50,
    battery: { voltage: 12, current: 1, remaining_pct: 90, cells: 3, level: 'ok' }, gps: null, sensors: [], statustext: [], rc: null, servo: null,
    flight_time_s: 0, armed_time_s: 0, capabilities: { manual: true, althold: true, position: true }, claim: null, possession,
    controller: possession ? 'human' : 'none', sticks: null, skill: null, calibration: { signs: { roll: 1, pitch: 1, yaw: 1 }, hover: 0.5 },
    confirmed: { signs: true, props: true }, preflight: { ok: true, blocking: [] }, course: null, record_session: null,
  } as unknown as Vehicle
}

function pad(pressed: number[]): GamepadLike {
  return { id: 'DualSense', index: 0, connected: true, mapping: 'standard', axes: [0, 0, 0, 0], buttons: Array.from({ length: 18 }, (_, i) => ({ pressed: pressed.includes(i), value: pressed.includes(i) ? 1 : 0 })) }
}

function harness(vehicles: Vehicle[]) {
  const h = { t: 0, pads: [] as GamepadLike[], focused: true, fire: [] as string[] }
  const noop = async () => undefined
  const deps: EngineDeps = {
    now: () => h.t,
    gamepads: () => h.pads,
    vehicles: () => vehicles,
    selected: () => null,
    focused: () => h.focused,
    send: () => {},
    cancel: () => {},
    prefs: () => ({ deadzone: 0.08, expo: 0 }),
    act: {
      possess: noop, release: noop, arm: noop, stop: () => {}, mode: noop, course: () => {}, resetSim: noop,
      moveSelection: () => {}, viewCycle: () => {}, notice: () => {},
      fire: (v, on) => void h.fire.push(`${v} ${on}`),
    },
  }
  const e = new InputEngine(deps)
  const tick = (ms: number) => {
    h.t += ms
    return e.tick()
  }
  return { h, e, tick }
}

const R1 = 5
const L1 = 4
const gamepad = (n: number) => ({ input: 'gamepad' as const, pad: n, since: 0 })
const keyboard = { input: 'keyboard' as const, pad: null, since: 0 }

describe('fire binding', () => {
  it('R1 alone fires the pad aircraft, refreshed every 100 ms, and releases', () => {
    const { h, tick } = harness([vehicle('sim-1', gamepad(1))])
    h.pads = [pad([])]
    tick(20)
    tick(20)
    h.pads = [pad([R1])]
    tick(20)
    expect(h.fire).toEqual(['sim-1 true'])
    for (let i = 0; i < 5; i++) tick(20)
    expect(h.fire).toEqual(['sim-1 true', 'sim-1 true'])
    expect(FIRE_REFRESH_MS).toBe(100)
    h.pads = [pad([L1, R1])] // the arm chord is not a trigger
    tick(20)
    expect(h.fire.at(-1)).toBe('sim-1 false')
  })

  it('X fires the window-driven aircraft and blur lets go', () => {
    const { h, e, tick } = harness([vehicle('sim-2', keyboard)])
    tick(20)
    e.fireKey(true)
    tick(20)
    expect(h.fire).toEqual(['sim-2 true'])
    e.blur()
    expect(h.fire).toEqual(['sim-2 true', 'sim-2 false'])
    h.focused = false
    e.fireKey(true)
    tick(20)
    expect(h.fire).toEqual(['sim-2 true', 'sim-2 false'])
    h.focused = true
    tick(20)
    e.keyUp('x')
    tick(20)
    expect(h.fire.filter((x) => x.endsWith('true'))).toHaveLength(1)
  })

  it('never fires a real aircraft', () => {
    const { h, e, tick } = harness([vehicle('real-1', keyboard)])
    tick(20)
    e.fireKey(true)
    tick(20)
    tick(200)
    expect(h.fire).toEqual([])
  })
})
