// Dev fixture stand-in for GET / PUT /sims/{vid}/airframe (06 §4, WP-2a). Values are generated from the preset
// and seed with a small deterministic generator (not the backend's), enough to review the inspector group.
import type { AirframeBody, SimAirframe } from '@/api'

type Params = SimAirframe['params']
type Preset = SimAirframe['preset']

const LIMITS: SimAirframe['limits'] = {
  mass_kg: [0.08, 0.3],
  payload_kg: [0, 0.2],
  cg_offset_cm: [-3, 3],
  level_trim_deg: [-5, 5],
  motor_gain: [-0.08, 0.08],
  prop_damage: [0, 1],
  damaged_prop: [1, 4],
  imu_noise: [0, 5],
}

const IDEAL: Params = { mass_kg: 0.12, payload_kg: 0, cg_offset_cm: [0, 0], level_trim_deg: [0, 0], motor_gain: [0, 0, 0, 0], prop_damage: 0, damaged_prop: 1, imu_noise: 0 }

function rng(seed: number): () => number {
  let s = seed >>> 0 || 1
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0
    return s / 2 ** 32
  }
}

const r3 = (x: number) => Math.round(x * 1000) / 1000

export function fixtureParams(preset: Preset, seed: number): Params {
  if (preset !== 'typical' && preset !== 'rough') return { ...IDEAL, cg_offset_cm: [0, 0], level_trim_deg: [0, 0], motor_gain: [0, 0, 0, 0] }
  const u = rng(seed + (preset === 'rough' ? 7919 : 0))
  const span = (a: number, b: number) => a + (b - a) * u()
  const rough = preset === 'rough'
  const cg = span(rough ? 0.7 : 0, rough ? 1.4 : 0.25)
  const angle = span(0, 2 * Math.PI)
  const gain = rough ? 0.08 : 0.02
  return {
    mass_kg: 0.12,
    payload_kg: rough ? r3(span(0.005, 0.02)) : 0,
    cg_offset_cm: [r3(cg * Math.cos(angle)), r3(cg * Math.sin(angle))],
    level_trim_deg: [r3(span(rough ? -2 : -0.4, rough ? 2 : 0.4)), r3(span(rough ? -2 : -0.4, rough ? 2 : 0.4))],
    motor_gain: [0, 1, 2, 3].map(() => r3(span(-gain, gain))),
    prop_damage: rough ? r3(span(0.2, 0.5)) : 0,
    damaged_prop: 1 + Math.floor(u() * 4),
    imu_noise: rough ? 2 : 1,
  }
}

function hoverThrottle(p: Params): number | null {
  const need = (p.mass_kg + p.payload_kg) / (0.12 * 2.4)
  // thrust curve f(u) = a u + b u^2 with f(1) = 1, f(0.5) = 1 / 2.4 (physics.py)
  const b = (1 / 2.4 - 0.5) / (0.25 - 0.5)
  const a = 1 - b
  if (need > 1) return null
  return Math.round(((-a + Math.sqrt(a * a + 4 * b * need)) / (2 * b)) * 1000) / 1000
}

export interface AirframeFixture {
  /** Handles the airframe routes; undefined for any other path. */
  handle(method: string, path: string, body: unknown, assist: (id: string) => boolean): SimAirframe | undefined
}

export function startAirframeFixture(onChange: (id: string, preset: Preset, seed: number) => void): AirframeFixture {
  const frames = new Map<string, { preset: Preset; seed: number; params: Params }>()
  const view = (id: string, assist: boolean): SimAirframe => {
    const f = frames.get(id) ?? { preset: 'typical', seed: 4242, params: fixtureParams('typical', 4242) }
    frames.set(id, f)
    return { id, preset: f.preset, seed: f.seed, params: f.params, active: !assist, hover_throttle: hoverThrottle(f.params), limits: LIMITS }
  }
  return {
    handle(method, path, body, assist) {
      const m = path.match(/^\/sims\/([^/]+)\/airframe$/)
      if (!m) return undefined
      const id = decodeURIComponent(m[1])
      const cur = view(id, assist(id))
      if (method !== 'PUT') return cur
      const b = (body ?? {}) as AirframeBody
      const seed = b.seed ?? cur.seed
      let preset: Preset = b.preset ?? (b.params ? 'custom' : cur.preset === 'custom' ? 'custom' : cur.preset)
      let params = preset === 'custom' ? cur.params : fixtureParams(preset, seed)
      if (b.params) {
        params = { ...(b.preset && b.preset !== 'custom' ? fixtureParams(b.preset, seed) : cur.params), ...b.params } as Params
        preset = 'custom'
      }
      frames.set(id, { preset, seed, params })
      onChange(id, preset, seed)
      return view(id, assist(id))
    },
  }
}
