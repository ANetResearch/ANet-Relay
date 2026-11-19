// Aircraft models of the stage (12 §2.2): the micro keeps the pre-0.6 sizes, the ANetDU7 scales, unknown models are
// micros, pad rows make room for the largest aircraft.
import { describe, expect, it } from 'vitest'
import { largest, metrics, MODELS, modelOf, modelUrl, presence } from './models'
import { SLOT_SPACING, slotPosition, slotRow } from './placement'

describe('models', () => {
  it('mirrors GET /capabilities -> models', () => {
    expect(MODELS['anet-du7']).toMatchObject({ span: 0.684, height: 0.211, gimbal: true, file: 'anet_du7.glb' })
    expect(MODELS.micro).toMatchObject({ span: 0.166, height: 0.045, gimbal: false, file: 'caddxfpv_gofilm20.glb' })
  })

  it('reads the model of a vehicle, a record or a trace craft; anything unknown is the micro', () => {
    expect(modelOf({ model: 'anet-du7' })).toBe('anet-du7')
    expect(modelOf({ model: 'micro' })).toBe('micro')
    expect(modelOf({ model: 'x500' })).toBe('micro')
    expect(modelOf({})).toBe('micro')
    expect(modelOf(null)).toBe('micro')
    expect(modelUrl('anet-du7', './')).toBe('./models/anet_du7.glb')
  })

  it('keeps the micro exactly at the GoFilm 20 sizes of 0.5', () => {
    const m = metrics('micro')
    expect(presence(0.166)).toBe(1)
    expect(m).toMatchObject({ ring: 0.24, possess: 0.3, pulseTo: 0.5, flyTo: 1.6, single: 0.8, minDistance: 1.2, spawnBox: 0.4, slotBox: 0.4 })
    expect(m.base).toBeCloseTo(0.18, 9)
    expect(m.hit).toBeCloseTo(0.11, 9)
    expect(m.label).toBeCloseTo(0.12, 9)
    expect(m.altLabel).toBeCloseTo(0.3, 9)
    expect(m.nose).toEqual({ size: 0.02, gap: 0.03 })
    expect(m.chase).toEqual({ back: 1.2, up: 0.45, ahead: 2 })
    expect(m.slotPitch).toBeCloseTo(SLOT_SPACING, 9)
  })

  it('scales the ANetDU7: footprint with its span, marks with its presence', () => {
    const d = metrics('anet-du7')
    const k = presence(0.684)
    expect(k).toBeGreaterThan(2)
    expect(k).toBeLessThan(4.12) // slower than the span ratio
    expect(d.base).toBeCloseTo(0.18 * (0.684 / 0.166), 9) // the dashed base square frames the cage
    expect(d.hit).toBeGreaterThan(0.684 / 2 - 0.05)
    expect(d.ring).toBeGreaterThan(0.684 / 2) // the selection ring clears the cage
    expect(d.label).toBeGreaterThan(0.211)
    expect(d.spawnBox).toBeGreaterThan(0.684)
    expect(d.slotPitch).toBeGreaterThan(d.slotBox) // squares never touch
    expect(d.flyTo / d.span).toBeLessThan(metrics('micro').flyTo / 0.166) // the camera comes relatively closer
    expect(largest(['micro', 'anet-du7', 'micro']).model).toBe('anet-du7')
    expect(largest([]).model).toBe('micro')
  })

  it('spaces a pad row for the largest aircraft without moving a micro row', () => {
    const plain = slotRow([], { x: 3, y: 3 })
    expect(plain).not.toHaveProperty('pitch')
    expect(slotPosition(plain, 1, 3)[0] - slotPosition(plain, 0, 3)[0]).toBeCloseTo(SLOT_SPACING, 9)
    const d = metrics('anet-du7')
    const big = slotRow([], { x: 3, y: 3 }, { pitch: d.slotPitch, box: d.slotBox })
    expect(slotPosition(big, 1, 3)[0] - slotPosition(big, 0, 3)[0]).toBeCloseTo(d.slotPitch, 9)
    // the bigger squares grow southwards, away from the scene
    expect(big.y).toBeLessThan(plain.y)
  })
})
