// Gimbal pod, pure parts (12 §5): lens table and fields of view against backend relay/gimbal/lens.py, the stage's
// joint angles (yaw right +, pitch up +), picture pixels, HUD texts, thermal palettes, the request bodies and the
// picture-in-picture source rule.
import { describe, expect, it } from 'vitest'
import {
  attachBody, attachErrors, clampZoom, DEFAULT_ATTACH, fovDeg, gimbalNodeAngles, heat, isFixed, isInfrared, lensBody, lensText, lookDirection, onBody,
  padSpeed, paletteLut, pictureFromPointer, pipSource, pixelFromAngles, rangeText, rateBody, recTime, signedDeg, sliderToZoom, stepZoom, toImage,
  trackBody, trackBox, verticalFov, zoomBody, zoomSteps, zoomText, zoomToSlider,
} from './model'

const r2 = (n: number) => Math.round(n * 100) / 100 + 0 // + 0: no negative zero

describe('lenses', () => {
  it('matches lens.py fov_deg', () => {
    // values printed by relay.gimbal.lens.fov_deg
    expect(fovDeg('visible1', 1).map(r2)).toEqual([60, 35.98])
    expect(fovDeg('visible1', 4).map(r2)).toEqual([16.43, 9.28])
    expect(fovDeg('ir1', 1).map(r2)).toEqual([40, 23.14])
    expect(fovDeg('visible2', 1).map(r2)).toEqual([90, 58.72])
    // the wide lens is visibly wider than the zoom lens at 1x
    expect(fovDeg('visible2', 1)[1]).toBeGreaterThan(fovDeg('visible1', 1)[1] * 1.5)
  })

  it('clamps zoom to the lens and knows fixed and infrared lenses', () => {
    expect(clampZoom('visible1', 50)).toBe(30)
    expect(clampZoom('ir1', 0.2)).toBe(1)
    expect(clampZoom('visible1', Number.NaN)).toBe(1)
    expect(isFixed('visible2')).toBe(true)
    expect(isFixed('ir2')).toBe(true)
    expect(isFixed('visible1')).toBe(false)
    expect(isInfrared('ir1')).toBe(true)
    expect(isInfrared('visible1')).toBe(false)
    expect(isInfrared(null)).toBe(false)
  })

  it('uses the view field of view when it is sane, else the table', () => {
    expect(verticalFov({ lens: 'visible1', zoom: 1, fov_deg: [60, 20] })).toBe(20)
    expect(r2(verticalFov({ lens: 'visible1', zoom: 4, fov_deg: [0, 0] }))).toBe(9.28)
  })
})

describe('joint angles', () => {
  it('maps yaw right + and pitch up + onto the model file axes (nose +Z, left +X, up +Y)', () => {
    const [x0, y0, z0] = lookDirection(0, 0)
    expect([r2(x0), r2(y0), r2(z0)]).toEqual([0, 0, 1])
    // yaw 90° right: looks along -X (the pilot's right)
    const right = lookDirection(90, 0)
    expect(right[0]).toBeCloseTo(-1, 9)
    expect(right[2]).toBeCloseTo(0, 9)
    // pitch 30° down: looks below the horizon, still ahead
    const down = lookDirection(0, -30)
    expect(down[1]).toBeCloseTo(-0.5, 9)
    expect(down[2]).toBeGreaterThan(0)
    // straight down
    expect(lookDirection(0, -90)[1]).toBeCloseTo(-1, 9)
    const n = gimbalNodeAngles(45, -20)
    expect(n.y).toBeCloseTo((-45 * Math.PI) / 180, 12)
    expect(n.x).toBeCloseTo((20 * Math.PI) / 180, 12)
  })
})

describe('picture pixels', () => {
  it('maps a pointer in the PiP onto the 1280 x 720 track picture, centre 0', () => {
    expect(pictureFromPointer(160, 90, 320, 180)).toEqual({ x: 0, y: 0 })
    expect(pictureFromPointer(0, 0, 320, 180)).toEqual({ x: -640, y: -360 })
    expect(pictureFromPointer(320, 180, 320, 180)).toEqual({ x: 640, y: 360 })
    expect(pictureFromPointer(400, -10, 320, 180)).toEqual({ x: 640, y: -360 })
    expect(trackBody(12.4, -7.6)).toEqual({ action: 'track', x: 12, y: -8 })
  })

  it('puts the tracking gate where the miss distance points', () => {
    const half = fovDeg('visible1', 1)[0] / 2
    expect(pixelFromAngles('visible1', 1, half, 0).x).toBeCloseTo(640, 6)
    const g = { lens: 'visible1' as const, zoom: 1, tracking: { state: 'locked' as const, algorithm: 'auto' as const, x_deg: 0, y_deg: 0, box_px: [128, 72] as [number, number] } }
    const b = trackBox(g)!
    expect(b.left + b.width / 2).toBeCloseTo(0.5, 9)
    expect(b.top + b.height / 2).toBeCloseTo(0.5, 9)
    expect(b.width).toBeCloseTo(0.1, 9)
    expect(trackBox({ ...g, tracking: { ...g.tracking, x_deg: 5 } })!.left).toBeGreaterThan(b.left)
    expect(trackBox({ ...g, tracking: { ...g.tracking, state: 'off' } })).toBeNull()
    expect(trackBox({ ...g, tracking: { ...g.tracking, state: 'searching' } })).toBeNull()
  })
})

describe('texts', () => {
  it('writes lens, zoom, angles, range and recording time', () => {
    expect(lensText({ lens: 'visible1', zoom: 4 })).toBe('EO 4.0×')
    expect(lensText({ lens: 'visible1', zoom: 12 })).toBe('EO 12×')
    expect(lensText({ lens: 'visible2', zoom: 1 })).toBe('EO W')
    expect(lensText({ lens: 'ir1', zoom: 2 })).toBe('IR 2.0×')
    expect(lensText({ lens: 'ir2', zoom: 1 })).toBe('IR 2')
    expect(zoomText(null)).toBe('—')
    expect(signedDeg(12)).toBe('+12.0°')
    expect(signedDeg(-30)).toBe('−30.0°')
    expect(signedDeg(-0.01)).toBe('0.0°')
    expect(rangeText(3.57)).toBe('3.6 m')
    expect(rangeText(1250)).toBe('1250 m')
    expect(rangeText(null)).toBeNull()
    expect(rangeText(0)).toBeNull()
    expect(recTime(75.9)).toBe('01:15')
  })
})

describe('zoom controls', () => {
  it('steps through presets within the lens range', () => {
    expect(zoomSteps('visible1')).toEqual([1, 2, 4, 8, 15, 30])
    expect(zoomSteps('ir1')).toEqual([1, 2, 4, 8])
    expect(stepZoom('visible1', 1, 1)).toBe(2)
    expect(stepZoom('visible1', 3, 1)).toBe(4)
    expect(stepZoom('visible1', 3, -1)).toBe(2)
    expect(stepZoom('visible1', 30, 1)).toBe(30)
    expect(stepZoom('visible1', 1, -1)).toBe(1)
  })

  it('maps the slider logarithmically and back', () => {
    expect(zoomToSlider('visible1', 1)).toBe(0)
    expect(zoomToSlider('visible1', 30)).toBe(1000)
    expect(sliderToZoom('visible1', zoomToSlider('visible1', 8))).toBe(8)
    expect(zoomToSlider('visible2', 1)).toBe(0)
    expect(sliderToZoom('ir1', 2000)).toBe(8)
  })

  it('slows the rate pad as the lens zooms in', () => {
    expect(padSpeed(1)).toBe(30)
    expect(padSpeed(4)).toBe(15)
    expect(padSpeed(30)).toBeLessThan(6)
    expect(padSpeed(1000)).toBe(3)
    expect(padSpeed(Number.NaN)).toBe(30)
  })
})

describe('request bodies', () => {
  it('builds the gimbal actions', () => {
    expect(rateBody(1, 0, 30)).toEqual({ action: 'rate', yaw_dps: 30, pitch_dps: 0 })
    expect(rateBody(0, -1, 250)).toEqual({ action: 'rate', yaw_dps: 0, pitch_dps: -100 })
    expect(zoomBody('visible1', 44)).toEqual({ action: 'zoom', level: 30 })
    expect(zoomBody('visible1', 3.14159)).toEqual({ action: 'zoom', level: 3.1 })
    expect(lensBody('ir1')).toEqual({ action: 'lens', lens: 'ir1' })
    expect(lensBody('visible1', 'ir1')).toEqual({ action: 'lens', lens: 'visible1', pip: 'ir1' })
    expect(onBody('laser', true)).toEqual({ action: 'laser', on: true })
  })

  it('validates and builds the attach form', () => {
    expect(attachErrors(DEFAULT_ATTACH)).toEqual({ host: 'host' })
    const tcp = { ...DEFAULT_ATTACH, host: ' 10.0.0.5 ' }
    expect(attachErrors(tcp)).toEqual({})
    expect(attachBody(tcp)).toEqual({ transport: 'tcp', host: '10.0.0.5', port: 2000 })
    expect(attachErrors({ ...tcp, port: '70000' })).toEqual({ port: 'port' })
    const serial = { ...DEFAULT_ATTACH, transport: 'serial' as const }
    expect(attachErrors(serial)).toEqual({ device: 'device' })
    expect(attachBody({ ...serial, device: '/dev/ttyUSB0' })).toEqual({ transport: 'serial', device: '/dev/ttyUSB0', baud: 115200 })
    expect(attachErrors({ ...serial, device: '/dev/ttyUSB0', baud: '12' })).toEqual({ baud: 'baud' })
  })
})

describe('thermal imagery', () => {
  it('builds 256-entry palettes', () => {
    const white = paletteLut('white_hot')
    const black = paletteLut('black_hot')
    const pseudo = paletteLut('pseudo')
    expect(white.length).toBe(768)
    expect([white[0], white[765]]).toEqual([0, 255])
    expect([black[0], black[765]]).toEqual([255, 0])
    // pseudo colour runs from a dark cold end to a near-white hot end
    expect(pseudo[0] + pseudo[1] + pseudo[2]).toBeLessThan(40)
    expect(pseudo[765] + pseudo[766] + pseudo[767]).toBeGreaterThan(700)
  })

  it('keeps the ground cool and lit airframes hot', () => {
    expect(heat(0, 0, 0)).toBeGreaterThan(0) // the cold ground is not black
    expect(heat(36, 36, 38)).toBeLessThan(96)
    expect(heat(200, 200, 200)).toBeGreaterThan(230)
    expect(heat(120, 120, 120)).toBeGreaterThan(heat(60, 60, 60))
  })

  it('flips the readback rows and maps through the palette', () => {
    // 1 x 2 picture: bottom row (first in the readback) black, top row white
    const src = new Uint8Array([0, 0, 0, 255, 255, 255, 255, 255])
    const plain = new Uint8Array(8)
    toImage(src, 1, 2, plain, null)
    expect([...plain]).toEqual([255, 255, 255, 255, 0, 0, 0, 255])
    const out = new Uint8Array(8)
    toImage(src, 1, 2, out, paletteLut('black_hot'))
    expect(out[0]).toBe(0) // the hot top row is black in black-hot
    expect(out[4]).toBeGreaterThan(200)
    expect(out[7]).toBe(255)
  })
})

describe('picture-in-picture source', () => {
  it('shows a simulated pod, with a switch when a video source is bound too', () => {
    expect(pipSource('sim', false, undefined)).toEqual({ source: 'gimbal', switchable: false, hud: true })
    expect(pipSource('sim', false, 'video')).toEqual({ source: 'gimbal', switchable: false, hud: true })
    expect(pipSource('sim', true, undefined)).toEqual({ source: 'gimbal', switchable: true, hud: true })
    expect(pipSource('sim', true, 'video')).toEqual({ source: 'video', switchable: true, hud: false })
  })

  it('puts the HUD of a real pod over its bound video or the no-source state', () => {
    expect(pipSource('dyt', true, undefined)).toEqual({ source: 'video', switchable: false, hud: true })
    expect(pipSource('dyt', false, undefined)).toEqual({ source: 'video', switchable: false, hud: true })
  })

  it('keeps the plain video PiP of an aircraft without a pod', () => {
    expect(pipSource(null, true, undefined)).toEqual({ source: 'video', switchable: false, hud: false })
    expect(pipSource(null, false, 'gimbal')).toEqual({ source: null, switchable: false, hud: false })
  })
})
