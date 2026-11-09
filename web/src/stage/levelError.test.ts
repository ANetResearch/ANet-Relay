// The stage's level error of a simulator (10 §3) follows airframe edits that keep the preset and seed.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type RelayEvent, type SimAirframe, type Vehicle } from '@/api'
import { useRelay } from '@/store/relay'
import { levelError } from './live'

const sim = { id: 'sim-7', kind: 'sim', armed: false, sim: { hover_assist: false, airframe: { preset: 'custom', seed: 3 } } } as unknown as Vehicle

describe('level error cache', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    useRelay.getState().reset()
  })
  it('refetches a disarmed simulator after an airframe change event', async () => {
    const fetch = vi.spyOn(api, 'simAirframe').mockResolvedValue({ active: true, level_now_deg: [2, 0] } as unknown as SimAirframe)
    levelError(sim, 0)
    await Promise.resolve()
    levelError(sim, 10_000)
    expect(fetch).toHaveBeenCalledTimes(1) // disarmed: no periodic refresh
    const changed = { seq: 41, t: 1, kind: 'sim_airframe_changed', vehicle: 'sim-7', session: null, text_en: '', text_zh: '', data: { preset: 'custom', seed: 3 } } as unknown as RelayEvent
    useRelay.getState().applyEvents([changed])
    levelError(sim, 10_001)
    expect(fetch).toHaveBeenCalledTimes(2)
    levelError(sim, 10_002)
    expect(fetch).toHaveBeenCalledTimes(2)
  })
})
