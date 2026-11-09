import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import type { SimAirframe } from '@/api'
import { fixtureParams, startAirframeFixture } from '@/dev/airframeFixture'
import { AIRFRAME_GROUPS, AirframeEditor, presetBody, randomizeBody } from './AirframeGroup'
import { AIRFRAME_DICTS } from './airframe.strings'

const LIMITS: SimAirframe['limits'] = {
  mass_kg: [0.08, 0.3],
  payload_kg: [0, 0.2],
  cg_offset_cm: [-3, 3],
  level_trim_deg: [-5, 5],
  motor_gain: [-0.08, 0.08],
  prop_damage: [0, 1],
  damaged_prop: [1, 4],
  imu_noise: [0, 5], arm_bias_deg: [0, 3],
}

const frame = (over: Partial<SimAirframe> = {}): SimAirframe => ({
  id: 'sim-1',
  preset: 'rough',
  seed: 9,
  params: { mass_kg: 0.12, payload_kg: 0.015, cg_offset_cm: [0.8, -0.4], level_trim_deg: [1.2, -0.5], motor_gain: [0.03, -0.02, 0.05, 0], prop_damage: 0.3, damaged_prop: 2, imu_noise: 2, arm_bias_deg: 0, with_assist: false },
  active: true,
  level_now_deg: [1.2, -0.5],
  hover_throttle: 0.545,
  limits: LIMITS,
  ...over,
})

const placeholders = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort()

describe('airframe group (06 §4)', () => {
  it('has identical keys and placeholders in both languages', () => {
    expect(Object.keys(AIRFRAME_DICTS.zh).sort()).toEqual(Object.keys(AIRFRAME_DICTS.en).sort())
    for (const k of Object.keys(AIRFRAME_DICTS.en) as (keyof (typeof AIRFRAME_DICTS)['en'])[]) {
      expect(AIRFRAME_DICTS.zh[k]).toBeTruthy()
      expect(placeholders(AIRFRAME_DICTS.zh[k])).toEqual(placeholders(AIRFRAME_DICTS.en[k]))
    }
  })

  it('builds the request bodies', () => {
    expect(presetBody('rough', 5)).toEqual({ preset: 'rough', seed: 5 })
    expect(presetBody('custom', 5)).toEqual({ preset: 'custom' })
    expect(randomizeBody('rough', 77)).toEqual({ preset: 'rough', seed: 77 })
    expect(randomizeBody('ideal', 77)).toEqual({ preset: 'typical', seed: 77 })
    expect(randomizeBody('custom', 3)).toEqual({ preset: 'typical', seed: 3 })
  })

  it('patches only the moved component of a vector value', () => {
    const p = frame().params
    const fields = AIRFRAME_GROUPS.flatMap((g) => g.fields)
    const motor3 = fields.find((f) => f.id === 'motor_3')!
    expect(motor3.get(p)).toBe(0.05)
    expect(motor3.patch(p, -0.01)).toEqual({ motor_gain: [0.03, -0.02, -0.01, 0] })
    expect(fields.find((f) => f.id === 'cg_y')!.patch(p, 1)).toEqual({ cg_offset_cm: [0.8, 1] })
    expect(fields.find((f) => f.id === 'trim_pitch')!.text(-0.5)).toBe('-0.5 °')
    expect(motor3.text(0.05)).toBe('+5.0 %')
    for (const f of fields) expect(LIMITS[f.limit]).toBeDefined()
  })

  it('renders every value and the hover throttle', () => {
    let html = renderToStaticMarkup(<AirframeEditor data={frame()} busy={false} send={() => {}} defaultOpen />)
    for (const s of ['Preset', 'Seed', 'Randomize', '55 %', 'Payload', 'Motor 4 efficiency', 'IMU noise', '+0.80 cm', '15 g']) expect(html).toContain(s)
    expect(html).toContain('data-airframe="rough"')
    expect((html.match(/data-airframe-field=/g) ?? []).length).toBe(AIRFRAME_GROUPS.flatMap((g) => g.fields).length)
    html = renderToStaticMarkup(<AirframeEditor data={frame({ hover_throttle: null })} busy={false} send={() => {}} />)
    expect(html).toContain('Cannot lift')
    // server rendering reads the store's initial language; the Chinese copy is covered by the parity test
    for (const s of ['预设', '种子', '随机生成', '无法起飞', '载重', 'IMU 噪声']) expect(Object.values(AIRFRAME_DICTS.zh)).toContain(s)
  })

  it('dev fixture follows the request semantics', () => {
    expect(fixtureParams('rough', 3)).toEqual(fixtureParams('rough', 3))
    expect(fixtureParams('rough', 3)).not.toEqual(fixtureParams('rough', 4))
    const changes: string[] = []
    const fx = startAirframeFixture((id, preset, seed) => changes.push(`${id}:${preset}:${seed}`))
    expect(fx.handle('GET', '/fleet', undefined, () => true)).toBeUndefined()
    expect(fx.handle('GET', '/sims/sim-1/airframe', undefined, () => true)?.preset).toBe('typical')
    const rough = fx.handle('PUT', '/sims/sim-1/airframe', { preset: 'rough', seed: 8 }, () => false)!
    expect(rough.params).toEqual(fixtureParams('rough', 8))
    expect(rough.active).toBe(true)
    const tuned = fx.handle('PUT', '/sims/sim-1/airframe', { params: { payload_kg: 0.25 } }, () => false)!
    expect(tuned.preset).toBe('custom')
    expect(tuned.hover_throttle).toBeNull()
    expect(changes).toEqual(['sim-1:rough:8', 'sim-1:custom:8'])
  })
})
