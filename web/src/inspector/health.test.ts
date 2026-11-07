import { describe, expect, it } from 'vitest'
import type { HealthItem } from '@/api'
import { fixFor } from './HealthBlock'
import { HEALTH_DICTS } from './health.strings'

describe('flight health', () => {
  it('has identical keys in both languages', () => {
    expect(Object.keys(HEALTH_DICTS.zh).sort()).toEqual(Object.keys(HEALTH_DICTS.en).sort())
  })

  it('picks the note that matches the finding', () => {
    const item = (x: Partial<HealthItem>): HealthItem => ({ key: 'motors', level: 'bad', value: 200, unit: 'us', detail: null, ...x })
    expect(fixFor(item({ level: 'ok' }))).toBeNull()
    expect(fixFor(item({ detail: { means: [1700, 1450, 1460, 1450], high: 1, saturated: 0 } }))).toEqual({ key: 'fh.fix.motors', params: { n: 1, means: '1700 / 1450 / 1460 / 1450' } })
    expect(fixFor(item({ detail: { means: [1960], high: 1, saturated: 0.5 } }))?.key).toBe('fh.fix.motors.saturated')
    expect(fixFor(item({ key: 'hover_throttle', level: 'warn', value: 15, unit: '%', detail: { low: true } }))).toEqual({ key: 'fh.fix.hover_throttle.low', params: { v: 15 } })
    expect(fixFor(item({ key: 'vibration', value: 9, unit: 'm/s2', detail: { clipping: 4 } }))).toEqual({ key: 'fh.fix.vibration.clip', params: { n: 4 } })
    expect(fixFor(item({ key: 'lean', level: 'warn', value: 4.5, unit: 'deg', detail: { roll: 4, pitch: -2 } }))?.params).toEqual({ roll: '4.0', pitch: '-2.0' })
    expect(fixFor(item({ key: 'oscillation', value: 50, unit: 'deg/s' }))?.key).toBe('fh.fix.oscillation')
    for (const lang of ['en', 'zh'] as const) {
      for (const k of Object.keys(HEALTH_DICTS.en)) expect(HEALTH_DICTS[lang][k as keyof (typeof HEALTH_DICTS)['en']]).toBeTruthy()
    }
  })
})
