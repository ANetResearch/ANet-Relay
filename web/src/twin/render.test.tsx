// Rendered twin pieces (HITL steps and tables, the engine badge, a refused twin start) and the dev fixture's
// behaviour, so the UI can be exercised without a backend in every new state.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { RelayApiError, type HitlStatus, type Vehicle } from '@/api'
import { startTwinFixture } from '@/dev/twinFixture'
import { HitlSteps, HitlTables } from './HitlDialog'
import { EngineBadge, TwinFailure } from './parts'

const status = (over: Partial<HitlStatus> = {}): HitlStatus => ({
  id: 'q5', available: true, reason: null, reason_zh: null, state: 'running',
  steps: [
    { key: 'confirm', state: 'done', detail: 'Propellers removed', detail_zh: '已拆桨' },
    { key: 'backup', state: 'done', detail: '1210 parameters saved', detail_zh: '已保存 1210 项参数' },
    { key: 'write', state: 'done', detail: null, detail_zh: null },
    { key: 'reboot', state: 'done', detail: null, detail_zh: null },
    { key: 'run', state: 'active', detail: 'HIL_SENSOR 250 Hz', detail_zh: null },
  ],
  backup: { file: 'hitl-backups/q5.json', count: 1210, captured_at: 1 }, changed: { SYS_HITL: { from: 0, to: 1 } },
  mismatches: [{ name: 'COM_RC_IN_MODE', expected: 1, actual: 0 }], error: null, run: { sensor_hz: 250, gps_hz: 10, actuator_frames: 900, steps: 900, since: 1 }, pending_restore: false,
  ...over,
})

describe('HITL dialog pieces', () => {
  it('renders every step with its state and detail', () => {
    const html = renderToStaticMarkup(<HitlSteps status={status()} />)
    expect((html.match(/data-hitl-step=/g) ?? []).length).toBe(7)
    expect(html).toContain('data-hitl-step="run" data-state="active"')
    expect(html).toContain('data-hitl-step="verify" data-state="pending"')
    for (const s of ['Confirm propellers removed', 'Back up parameters', 'Reboot flight controller', 'Verify parameters', '1210 parameters saved', 'In progress']) expect(html).toContain(s)
  })
  it('tabulates the changed parameters and the restore mismatches', () => {
    const html = renderToStaticMarkup(<HitlTables status={status()} />)
    expect(html).toContain('data-hitl-changed')
    expect(html).toContain('SYS_HITL')
    expect(html).toContain('data-hitl-mismatches')
    expect(html).toContain('COM_RC_IN_MODE')
    expect(renderToStaticMarkup(<HitlTables status={status({ changed: {}, mismatches: [] })} />)).toBe('')
  })
})

describe('engine badge and twin failure', () => {
  it('shows the twin badge and nothing for a fast simulator', () => {
    const sim = { id: 'sim-1', kind: 'sim', firmware_info: null, sim: { engine: 'sitl', firmware: { family: 'ardupilot', version: '4.6.3', pack: 'p', match: 'exact', state: 'running' } } } as unknown as Vehicle
    const html = renderToStaticMarkup(<EngineBadge vehicle={sim} />)
    expect(html).toContain('data-engine-badge="twin"')
    expect(html).toContain('Firmware twin ArduCopter 4.6.3 · exact match')
    expect(renderToStaticMarkup(<EngineBadge vehicle={{ ...sim, sim: { engine: 'relay', firmware: null } } as unknown as Vehicle} />)).toBe('')
  })
  it('shows a refused start with the output lines', () => {
    const html = renderToStaticMarkup(<TwinFailure failure={{ code: 'twin_start_failed', title: 'err.twin_start_failed', message: 'no heartbeat', lines: ['ERROR [px4] boot'], added: [] }} />)
    expect(html).toContain('The firmware twin did not start')
    expect(html).toContain('ERROR [px4] boot')
    expect(html).toContain('data-output-lines')
  })
})

describe('dev fixture (twins, packs, serial, HITL)', () => {
  afterEach(() => {
    vi.useRealTimers()
  })
  const setup = () => {
    let vehicles: Vehicle[] = [
      { id: 'q05', kind: 'real', label: 'Quad 05', armed: false, controller: 'human', possession: { input: 'keyboard', pad: null, since: 0 }, autopilot: 'PX4', firmware: 'PX4 1.16.2', firmware_info: { family: 'px4', vehicle: 'copter', version: '1.16.2', type: 'official', git: 'b5f3e2a1', board: null }, serial: { device: '/dev/ttyACM0', baud: 115200 }, link: { state: 'ok' } } as unknown as Vehicle,
    ]
    let now = 1000
    const events: string[] = []
    const fx = startTwinFixture({
      now: () => now,
      vehicles: () => vehicles,
      mutate: (id, fn) => (vehicles = vehicles.map((v) => (v.id === id ? fn(v) : v))),
      add: (v) => (vehicles = [...vehicles, v]),
      remove: (id) => (vehicles = vehicles.filter((v) => v.id !== id)),
      emit: (kind) => void events.push(kind),
      base: (over) => ({ link: { state: 'ok' }, armed: false, crashed: false, sim: over.kind === 'sim' ? { hover_assist: true, turbulence: 0, airframe: { preset: 'ideal', seed: 1 }, engine: 'relay', firmware: null, frame: 'micro-120' } : null, ...over }) as Vehicle,
    })
    return { fx, events, get vehicles() { return vehicles }, tick: (s: number) => (now += s) }
  }

  it('lists packs and refuses a twin start with the output lines, then the limit', () => {
    const s = setup()
    expect((s.fx.handle('GET', '/autopilots', undefined) as { packs: unknown[] }).packs.length).toBe(10)
    expect(s.fx.handle('GET', '/fleet', undefined)).toBeUndefined()
    let err: unknown
    try {
      s.fx.handle('POST', '/sims', { engine: 'sitl', pack: 'px4-1.14.4' })
    } catch (e) {
      err = e
    }
    expect(err).toBeInstanceOf(RelayApiError)
    expect((err as RelayApiError).code).toBe('twin_start_failed')
    expect(((err as RelayApiError).detail as { lines: string[] }).lines.length).toBeGreaterThan(2)
    vi.useFakeTimers()
    expect(s.fx.handle('POST', '/sims', { engine: 'sitl', count: 3 })).toEqual({ added: ['sim-1', 'sim-2', 'sim-3'] })
    expect(() => s.fx.handle('POST', '/sims', { engine: 'sitl' })).toThrow(/at most 3/)
    expect(s.vehicles.find((v) => v.id === 'sim-1')?.sim?.firmware?.state).toBe('booting')
    vi.advanceTimersByTime(1600)
    expect(s.vehicles.find((v) => v.id === 'sim-1')?.sim?.firmware?.state).toBe('running')
    expect(s.events).toContain('twin_started')
    s.fx.stop()
  })

  it('walks a HITL session to restored with a mismatch', () => {
    vi.useFakeTimers()
    const s = setup()
    const get = () => s.fx.handle('GET', '/vehicles/q05/hitl', undefined) as HitlStatus
    expect(get()).toMatchObject({ available: true, state: 'idle' })
    expect(() => s.fx.handle('POST', '/vehicles/q05/hitl', { action: 'start' })).toThrow(/HITL/)
    s.fx.handle('POST', '/vehicles/q05/hitl', { action: 'start', confirm: 'HITL' })
    expect(get().state).toBe('backing_up')
    vi.advanceTimersByTime(4000)
    expect(get().state).toBe('running')
    expect((s.vehicles[0] as Vehicle & { hitl: { state: string } }).hitl.state).toBe('running')
    s.fx.handle('POST', '/vehicles/q05/hitl', { action: 'stop' })
    vi.advanceTimersByTime(3000)
    expect(get()).toMatchObject({ state: 'restored', mismatches: [{ name: 'COM_RC_IN_MODE' }] })
    expect(s.events).toEqual(['hitl_started', 'hitl_restored'])
    s.fx.stop()
  })
})
