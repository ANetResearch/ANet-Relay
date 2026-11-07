// WP-D dev fixture for the LAN arena (?fixture=busy&arena=1): one peer terminal (seat 6, two simulators circling)
// whose rounds fly towards us, our own magazine and health for the fixture sims, and PUT /settings peer_enabled so
// the top bar toggle can be tried. Without arena=1 the arena starts off and the toggle turns it on.
import type { ArenaView } from '@/api'
import { useArena } from '@/arena/store'

const PEER = '00000000000000bb'
const MUZZLE = 35

function peerVehicles(t: number): ArenaView['peers'][number]['vehicles'] {
  return [0, 1].map((i) => {
    const a = t * 0.4 + i * Math.PI
    const yaw = -a - Math.PI / 2
    return {
      key: `${PEER}/sim-${i + 1}`, label: `Bravo sim-${i + 1}`,
      x: 4 * Math.cos(a), y: 14 + 4 * Math.sin(a), z: 1.2 + 0.2 * i,
      vx: -1.6 * Math.sin(a), vy: 1.6 * Math.cos(a), vz: 0,
      roll: 0.12, pitch: -0.05, yaw, throttle: 0.52, armed: true, down: false, hp: i ? 60 : 100,
    }
  })
}

export function arenaFixtureView(t: number, enabled: boolean, k: number): ArenaView {
  const vehicles = peerVehicles(t)
  const shots: ArenaView['shots'] = enabled && k % 4 === 0
    ? [{ id: `${PEER}:${k}`, owner: PEER, mine: false, t0: t, p: [vehicles[0].x, vehicles[0].y, vehicles[0].z], v: [0, -MUZZLE, 0.4] }]
    : []
  return {
    t,
    enabled,
    instance: '00000000000000aa',
    seat: enabled ? 0 : null,
    callsign: 'Alpha',
    error: null,
    peers: enabled ? [{ id: PEER, callsign: 'Bravo', seat: 6, rtt_ms: 2.4, stale: false, age_s: 0.05, score: { hits: 3, downs: 1, deaths: 0 }, vehicles }] : [],
    shots,
    local: {
      ammo: { 'sim-1': 24 - (k % 25), 'sim-2': 24 }, hp: { 'sim-1': 100, 'sim-2': 80 },
      reloading: { 'sim-1': k % 25 === 24, 'sim-2': false }, down: { 'sim-1': false, 'sim-2': false },
      max_ammo: 24, max_hp: 100, score: { hits: 1, downs: 0, deaths: 1 },
    },
  }
}

/** Starts the arena stand-in at 20 Hz; returns the PUT /settings hook and a stop function. */
export function startArenaFixture(): { settings: (body: unknown) => void; stop: () => void } {
  let enabled = new URLSearchParams(globalThis.location?.search ?? '').get('arena') === '1'
  let k = 0
  const push = () => useArena.getState().applyArena(arenaFixtureView(Date.now() / 1000, enabled, k++))
  push()
  const timer = setInterval(push, 50)
  return {
    settings: (body) => {
      const on = (body as { peer_enabled?: unknown } | null)?.peer_enabled
      if (typeof on === 'boolean') enabled = on
    },
    stop: () => clearInterval(timer),
  }
}
