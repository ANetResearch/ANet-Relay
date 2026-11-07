import { describe, expect, it } from 'vitest'
import { allocateIndex, assistAction, autoBindTarget, modeToggleTarget, optionsDecision, possessedBy, simOrder, sourceOf, throttleReady } from './bindings'

type P = { input: 'keyboard' | 'screen' | 'gamepad'; pad: number | null; since: number } | null
const v = (id: string, kind: 'real' | 'sim', possession: P = null, controller: 'none' | 'human' | 'agent' = possession ? 'human' : 'none') => ({
  id,
  kind,
  label: id,
  possession,
  controller,
})
const pad = (n: number): P => ({ input: 'gamepad', pad: n, since: 0 })

describe('controller numbers', () => {
  it('takes the lowest free number up to four', () => {
    expect(allocateIndex([])).toBe(1)
    expect(allocateIndex([1, 2])).toBe(3)
    expect(allocateIndex([2, 3])).toBe(1)
    expect(allocateIndex([1, 2, 3, 4])).toBeNull()
  })
})

describe('auto-binding (02 §7.3)', () => {
  it('binds only to simulated aircraft, the lowest-numbered free one', () => {
    const fleet = [v('real-10-7-7-36', 'real'), v('sim-10', 'sim'), v('sim-2', 'sim'), v('sim-1', 'sim', pad(1))]
    expect(autoBindTarget(fleet)?.id).toBe('sim-2')
  })
  it('never binds a real aircraft', () => {
    expect(autoBindTarget([v('real-a', 'real'), v('real-b', 'real')])).toBeNull()
  })
  it('skips sims an agent flies and sims already being possessed', () => {
    const fleet = [v('sim-1', 'sim', null, 'agent'), v('sim-2', 'sim'), v('sim-3', 'sim')]
    expect(autoBindTarget(fleet)?.id).toBe('sim-2')
    expect(autoBindTarget(fleet, new Set(['sim-2']))?.id).toBe('sim-3')
  })
  it('orders sims naturally', () => {
    expect(['sim-10', 'sim-9', 'sim-1'].map((id) => ({ id })).sort(simOrder).map((x) => x.id)).toEqual(['sim-1', 'sim-9', 'sim-10'])
  })
})

describe('Options hold', () => {
  const fleet = [v('q1', 'real'), v('q2', 'real', { input: 'keyboard', pad: null, since: 0 }), v('sim-1', 'sim', pad(2))]
  it('releases what this pad possesses', () => {
    expect(optionsDecision(fleet, 2, 'q1')).toEqual({ action: 'release', vehicle: 'sim-1' })
  })
  it('possesses the selected aircraft, real ones included', () => {
    expect(optionsDecision(fleet, 1, 'q1')).toEqual({ action: 'possess', vehicle: 'q1' })
  })
  it('refuses an aircraft another input flies, and no selection', () => {
    expect(optionsDecision(fleet, 1, 'q2')).toEqual({ action: 'blocked', reason: 'taken', vehicle: 'q2', by: 'keyboard' })
    expect(optionsDecision(fleet, 1, null)).toEqual({ action: 'blocked', reason: 'no_selection' })
  })
  it('one pad possesses one aircraft', () => {
    expect(possessedBy(fleet, 'pad:2')?.id).toBe('sim-1')
    expect(possessedBy(fleet, 'pad:1')).toBeNull()
    expect(sourceOf({ input: 'gamepad', pad: 3, since: 0 })).toBe('pad:3')
  })
})

describe('mode toggle', () => {
  it('switches between self-levelling and altitude hold from the reported list', () => {
    expect(modeToggleTarget({ mode_class: 'manual', modes: ['STABILIZE', 'ALT_HOLD', 'LOITER'] })).toBe('ALT_HOLD')
    expect(modeToggleTarget({ mode_class: 'althold', modes: ['STABILIZE', 'ALT_HOLD'] })).toBe('STABILIZE')
    expect(modeToggleTarget({ mode_class: 'position', modes: ['STABILIZED', 'ALTCTL', 'POSCTL'] })).toBe('STABILIZED')
    expect(modeToggleTarget({ mode_class: 'manual', modes: ['STABILIZE'] })).toBeNull()
  })
})

describe('hover assist action and H5 (05 §6.3)', () => {
  const modes = ['STABILIZE', 'ALT_HOLD', 'LOITER']
  it('switches a sim by whether it holds altitude now', () => {
    const s = (hover_assist: boolean, mode_class: 'manual' | 'althold' | 'position') => ({ kind: 'sim' as const, mode_class, modes, sim: { hover_assist, turbulence: 0 } })
    expect(assistAction(s(true, 'althold'))).toEqual({ kind: 'assist', on: false })
    expect(assistAction(s(true, 'position'))).toEqual({ kind: 'assist', on: false })
    expect(assistAction(s(false, 'manual'))).toEqual({ kind: 'assist', on: true })
    expect(assistAction(s(true, 'manual'))).toEqual({ kind: 'mode', name: 'ALT_HOLD' })
  })
  it('keeps the mode toggle on a real aircraft, and nothing in autonomous modes', () => {
    expect(assistAction({ kind: 'real', mode_class: 'althold', modes, sim: null })).toEqual({ kind: 'mode', name: 'STABILIZE' })
    expect(assistAction({ kind: 'real', mode_class: 'auto', modes, sim: null })).toBeNull()
  })
  it('H5 thresholds', () => {
    expect(throttleReady(0.05, false)).toBe(true)
    expect(throttleReady(0.06, false)).toBe(false)
    expect(throttleReady(0.4, true)).toBe(true)
    expect(throttleReady(0.61, true)).toBe(false)
    expect(throttleReady(Number.NaN, true)).toBe(false)
  })
})
