import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import type { Vehicle } from '@/api'
import type { AwaitReason } from '@/control/store'
import { DrivingHudView, hudModel, type HudSlot } from './DrivingHud'

function flown(over: Partial<Vehicle> = {}): Vehicle {
  return {
    id: 'sim-1', kind: 'sim', label: 'SIM-1', mode: 'STABILIZE', mode_class: 'manual',
    possession: { input: 'gamepad', pad: 1, since: 0 }, sim: { hover_assist: false, turbulence: 0.3 },
    ...over,
  } as Vehicle
}

const render = (v: Vehicle, lang: 'en' | 'zh' = 'en', awaiting: AwaitReason | null = null, slot?: HudSlot) =>
  renderToStaticMarkup(<DrivingHudView model={hudModel(v, 'standard', awaiting, slot)} lang={lang} bottom={140} />)

describe('driving state HUD (05 §6.4)', () => {
  it('shows identity, mode, hover assist and feel; no arena items without data', () => {
    const html = render(flown())
    for (const s of ['SIM-1', 'YOU', 'PAD 1', 'STABILIZE', 'ASSIST OFF', 'STANDARD', 'THR']) expect(html).toContain(s)
    expect(html).not.toContain('data-dhud-hp')
    expect(html).not.toContain('data-dhud-ammo')
  })
  it('names the keyboard source and a real aircraft altitude hold state', () => {
    const html = render(flown({ id: 'q1', kind: 'real', label: 'Quad 01', sim: null, mode: 'ALT_HOLD', mode_class: 'althold', possession: { input: 'keyboard', pad: null, since: 0 } }))
    expect(html).toContain('KEYS')
    expect(html).toContain('ALT HOLD')
    expect(html).not.toContain('ASSIST')
    expect(render(flown({ kind: 'real', sim: null }))).toContain('MANUAL THR')
  })
  it('renders the arena slot when fed', () => {
    const full = render(flown(), 'en', null, { hp: 80, ammo: { n: 24, max: 24, reloading: false } })
    expect(full).toContain('data-dhud-hp="80"')
    expect(full).toContain('24 / 24')
    const reloading = render(flown(), 'en', null, { hp: 40, ammo: { n: 0, max: 24, reloading: true, progress: 0.5 } })
    expect(reloading).toContain('data-dhud-ammo="reloading"')
    expect((reloading.match(/data-on=""/g) ?? []).length).toBe(3)
  })
  it('replaces row 3 with the H5 and resume notices', () => {
    expect(render(flown(), 'en', 'throttle')).toContain('Lower the throttle to start')
    expect(render(flown({ mode_class: 'althold', mode: 'ALT_HOLD' }), 'en', 'throttle')).toContain('Centre the throttle to start')
    const focus = render(flown(), 'en', 'focus', { hp: 10 })
    expect(focus).toContain('Move a stick to resume')
    expect(focus).not.toContain('data-dhud-hp')
  })
  it('uses the Chinese wording of the contract', () => {
    const zh = render(flown(), 'zh', 'throttle')
    for (const s of ['我', '手柄 1', '自稳', '辅助关', '标准', '请先将油门拉到最低']) expect(zh).toContain(s)
  })
})
