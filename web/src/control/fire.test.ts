import { describe, expect, it } from 'vitest'
import { FIRE_REFRESH_MS, FireTracker } from './fire'

function run() {
  const calls: string[] = []
  const tracker = new FireTracker()
  const fire = (id: string, on: boolean) => void calls.push(`${id} ${on ? 'on' : 'off'}`)
  return { calls, tracker, fire }
}

describe('hold-to-fire timing (05 §7.4)', () => {
  it('fires on the press, refreshes every 100 ms while held and stops on the release', () => {
    const { calls, tracker, fire } = run()
    const held = new Set(['sim-1'])
    for (let t = 0; t <= 250; t += 20) tracker.tick(held, t, fire)
    // press at 0, refreshes at 100 and 200 (ticks of 20 ms)
    expect(calls).toEqual(['sim-1 on', 'sim-1 on', 'sim-1 on'])
    tracker.tick(new Set(), 260, fire)
    expect(calls.at(-1)).toBe('sim-1 off')
    tracker.tick(new Set(), 280, fire)
    expect(calls).toHaveLength(4)
  })
  it('never lets the refresh gap reach 300 ms under a slow tick', () => {
    const { calls, tracker, fire } = run()
    const times: number[] = []
    const record = (id: string, on: boolean) => {
      if (on) times.push(t)
      fire(id, on)
    }
    let t = 0
    for (; t <= 1000; t += 45) tracker.tick(new Set(['sim-1']), t, record)
    for (let i = 1; i < times.length; i++) expect(times[i] - times[i - 1]).toBeLessThan(FIRE_REFRESH_MS + 45)
    expect(calls.every((c) => c === 'sim-1 on')).toBe(true)
  })
  it('handles several aircraft and releases all at once', () => {
    const { calls, tracker, fire } = run()
    tracker.tick(new Set(['sim-1', 'sim-2']), 0, fire)
    expect(tracker.firing('sim-2')).toBe(true)
    tracker.releaseAll(fire)
    expect(calls).toEqual(['sim-1 on', 'sim-2 on', 'sim-1 off', 'sim-2 off'])
    expect(tracker.firing('sim-1')).toBe(false)
  })
})
