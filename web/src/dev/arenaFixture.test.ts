import { describe, expect, it } from 'vitest'
import { arenaFixtureView } from './arenaFixture'

describe('arena dev fixture', () => {
  it('is off without peers and shows a peer with rounds when on', () => {
    expect(arenaFixtureView(1, false, 0)).toMatchObject({ enabled: false, seat: null, peers: [], shots: [] })
    const v = arenaFixtureView(1, true, 4)
    expect(v.peers[0].vehicles).toHaveLength(2)
    expect(v.shots).toHaveLength(1)
    expect(v.local.max_ammo).toBe(24)
  })
})
