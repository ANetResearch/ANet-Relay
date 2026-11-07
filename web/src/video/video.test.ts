import { describe, expect, it } from 'vitest'
import { bindDevice, clampWidth, classifyMediaError, meanLuma, nearestCorner, resolveDevice, signalState } from './devices'

describe('video binding (02 §9)', () => {
  it('one device feeds one aircraft', () => {
    const a = bindDevice({}, 'q1', { label: 'USB Video', deviceId: 'd1' })
    const b = bindDevice(a, 'q2', { label: 'USB Video', deviceId: 'd1' })
    expect(b).toEqual({ q2: { label: 'USB Video', deviceId: 'd1' } })
    const c = bindDevice(b, 'q1', { label: 'Capture 2', deviceId: 'd2' })
    expect(Object.keys(c).sort()).toEqual(['q1', 'q2'])
  })
  it('resolves by label first, then by id', () => {
    const devices = [{ deviceId: 'new-id', label: 'USB Video' }, { deviceId: 'd2', label: 'Other' }]
    expect(resolveDevice({ label: 'USB Video', deviceId: 'old-id' }, devices)?.deviceId).toBe('new-id')
    expect(resolveDevice({ label: '', deviceId: 'd2' }, devices)?.label).toBe('Other')
    expect(resolveDevice({ label: 'Gone', deviceId: 'gone' }, devices)).toBeNull()
    expect(resolveDevice(undefined, devices)).toBeNull()
  })
})

describe('signal', () => {
  it('measures luma and reports no signal after 2 s of black', () => {
    expect(meanLuma([0, 0, 0, 255, 0, 0, 0, 255])).toBe(0)
    expect(meanLuma([255, 255, 255, 255])).toBeCloseTo(255, 6)
    expect(signalState(null, 10_000)).toBe('live')
    expect(signalState(9_000, 10_000)).toBe('live')
    expect(signalState(7_900, 10_000)).toBe('nosignal')
  })
  it('classifies media errors', () => {
    expect(classifyMediaError({ name: 'NotAllowedError' })).toBe('denied')
    expect(classifyMediaError({ name: 'NotFoundError' })).toBe('missing')
    expect(classifyMediaError(new Error('x'))).toBe('failed')
  })
})

describe('picture-in-picture geometry', () => {
  it('snaps to the nearest corner and keeps 320..640', () => {
    expect(nearestCorner(100, 100, 800, 600)).toBe('tl')
    expect(nearestCorner(700, 500, 800, 600)).toBe('br')
    expect(nearestCorner(700, 100, 800, 600)).toBe('tr')
    expect(clampWidth(100)).toBe(320)
    expect(clampWidth(900)).toBe(640)
    expect(clampWidth(500, 420)).toBe(420)
  })
})
