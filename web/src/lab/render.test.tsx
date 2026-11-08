import { afterEach, describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import type { Fleet, ProgramRunView, Vehicle } from '@/api'
import { useRelay } from '@/store/relay'
import { useLangStore } from '@/i18n'
import { exitReplay, useReplay } from '@/records/replayState'
import { live, startLive } from '@/stage/live'
import { ProgramGroup } from './ProgramGroup'
import { RunReadout } from './parts'
import { closeTrace, openTrace, seekTrace, setTracePlaying, stepTrace, useTraceReplay } from './traceReplay'
import { parseTrace, traceStageId } from './trace'
import { fixtureTrace } from '@/dev/labFixture'
import { useUi } from '@/store/ui'

const RUN: ProgramRunView = {
  id: 'hover_trim', name: 'Hover trim', rev: 2, state: 'hold', status: 'running', reason: null, mode: 'pilot', fps: 49.6, frames: 900, faults: 0, overruns: 1,
  last_error: null, params: { target_alt: 1 }, seed: 7, rates_ok: true, sm: null, log_seq: 3, owner: { kind: 'operator', name: 'operator' }, started_at: 0,
}

function vehicle(over: Partial<Vehicle>): Vehicle {
  return {
    id: 'sim-1', label: 'SIM-1', kind: 'sim', link: { state: 'ok', heartbeat_age_s: 0.2 }, controller: 'none', claim: null, possession: null, program: null, programs_allowed: true,
    ...over,
  } as unknown as Vehicle
}

function setFleet(vehicles: Vehicle[]): void {
  seed(useRelay, { fleet: { t: 1, vehicles, alerts: [], sessions: [], safety_volume: { x: 3, y: 3, z: 2 } } as unknown as Fleet })
}

// renderToStaticMarkup reads zustand stores through their server snapshot (the store's initial state object), so the
// tests write their state into both
function seed<T extends object>(store: { setState(p: Partial<T>): void; getInitialState(): T }, patch: Partial<T>): void {
  store.setState(patch)
  Object.assign(store.getInitialState(), patch)
}

afterEach(() => {
  seed(useRelay, { fleet: null })
  seed(useLangStore, { lang: 'en' })
  closeTrace()
  exitReplay()
})

describe('inspector Program group', () => {
  it('asks the operator to allow programs on a real aircraft before deploying', () => {
    setFleet([vehicle({ id: 'q01', label: 'Quad 01', kind: 'real', programs_allowed: false })])
    const html = renderToStaticMarkup(<ProgramGroup vehicleId="q01" />)
    expect(html).toContain('Allow programs on this aircraft')
    expect(html).toContain('No program deployed.')
    expect(html).toMatch(/data-program-deploy=""[^>]*disabled|disabled[^>]*data-program-deploy=""/)
  })
  it('shows the running program with its state, frame rate and Stop', () => {
    setFleet([vehicle({ controller: 'program', program: RUN })])
    const html = renderToStaticMarkup(<ProgramGroup vehicleId="sim-1" />)
    expect(html).toContain('Hover trim')
    expect(html).toContain('data-program-state=""')
    expect(html).toContain('>hold<')
    expect(html).toContain('50 Hz')
    expect(html).toContain('data-program-stop=""')
    expect(html).not.toContain('data-programs-allow')
  })
  it('is bilingual', () => {
    seed(useLangStore, { lang: 'zh' })
    setFleet([vehicle({ controller: 'program', program: { ...RUN, status: 'paused', reason: 'possessed' } })])
    const html = renderToStaticMarkup(<ProgramGroup vehicleId="sim-1" />)
    expect(html).toContain('已暂停')
    expect(html).toContain('操作员已接管')
    expect(html).toContain('data-program-resume=""')
  })
})

describe('run readout', () => {
  it('states a fault in bold g50, never red, with the reason and last error', () => {
    const html = renderToStaticMarkup(<RunReadout run={{ ...RUN, status: 'faulted', reason: 'faults', faults: 25, last_error: 'ZeroDivisionError at line 12' }} />)
    expect(html).toContain('Faulted')
    expect(html).toContain('Too many failed frames')
    expect(html).toContain('ZeroDivisionError at line 12')
    expect(html).not.toMatch(/--r\d00/)
  })
})

describe('trace replay', () => {
  it('opens on the Fly stage with every aircraft of the trace and steps, seeks and closes', () => {
    const trace = parseTrace(fixtureTrace('tr-x', ['Duelist', 'Hunter (easy)'], 2, 'duel · round 1'))!
    useUi.getState().setWorkspace('lab')
    openTrace(trace)
    expect(useUi.getState().workspace).toBe('fly')
    const r = useReplay.getState()
    expect(r.active).toBe(true)
    expect(r.crafts?.map((c) => c.id)).toEqual([traceStageId('p1'), traceStageId('p2')])
    expect(r.recordId).toBeNull()
    // the stage places every replayed aircraft by its arena position
    const stop = startLive()
    expect([...live.targets.keys()]).toEqual([traceStageId('p1'), traceStageId('p2')])
    expect(live.targets.get(traceStageId('p1'))?.kind).toBe('position')
    stepTrace(0.5)
    expect(useTraceReplay.getState().t).toBeCloseTo(0.5)
    useTraceReplay.setState({ speed: 4 })
    stepTrace(0.5)
    expect(useTraceReplay.getState().t).toBeCloseTo(2.5)
    seekTrace(999)
    expect(useTraceReplay.getState().t).toBe(trace.duration_s)
    setTracePlaying(true)
    expect(useTraceReplay.getState().t).toBe(0)
    closeTrace()
    expect(useReplay.getState().active).toBe(false)
    expect(live.targets.size).toBe(0)
    stop()
  })
})
