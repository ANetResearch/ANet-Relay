import { describe, expect, it } from 'vitest'
import type { Vehicle } from '@/api'
import { groupAndSort } from './sort'

type V = Pick<Vehicle, 'id' | 'label' | 'battery' | 'link' | 'controller' | 'kind'>
const v = (id: string, over: Partial<V> = {}): V => ({
  id,
  label: id,
  kind: 'real',
  controller: 'none',
  link: { state: 'ok', heartbeat_age_s: 0, rx_hz: 0, tx_hz: 0, loss_pct: null, rtt_ms: null },
  battery: { voltage: null, current: null, remaining_pct: null, cells: null, level: 'unknown' },
  ...over,
})

describe('fleet sort', () => {
  it('puts real aircraft above simulated ones and sorts names naturally', () => {
    const r = groupAndSort([v('Quad 10'), v('SIM-2', { kind: 'sim' }), v('Quad 2'), v('SIM-1', { kind: 'sim' })], 'name')
    expect(r.real.map((x) => x.id)).toEqual(['Quad 2', 'Quad 10'])
    expect(r.sim.map((x) => x.id)).toEqual(['SIM-1', 'SIM-2'])
  })
  it('orders by battery, lowest first, unknown last', () => {
    const bat = (pct: number | null): V['battery'] => ({ voltage: null, current: null, remaining_pct: pct, cells: null, level: 'ok' })
    const r = groupAndSort([v('a', { battery: bat(null) }), v('b', { battery: bat(80) }), v('c', { battery: bat(20) })], 'battery')
    expect(r.real.map((x) => x.id)).toEqual(['c', 'b', 'a'])
  })
  it('orders by link, worst first', () => {
    const link = (state: V['link']['state']): V['link'] => ({ state, heartbeat_age_s: 0, rx_hz: 0, tx_hz: 0, loss_pct: null, rtt_ms: null })
    const r = groupAndSort([v('a', { link: link('ok') }), v('b', { link: link('lost') }), v('c', { link: link('delayed') })], 'link')
    expect(r.real.map((x) => x.id)).toEqual(['b', 'c', 'a'])
  })
  it('orders by controller: human, agent, idle', () => {
    const r = groupAndSort([v('a'), v('b', { controller: 'agent' }), v('c', { controller: 'human' })], 'controller')
    expect(r.real.map((x) => x.id)).toEqual(['c', 'b', 'a'])
  })
})
