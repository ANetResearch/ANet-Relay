import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import type { ProcedureRun, Vehicle } from '@/api'
import { MotorLayout } from './MotorLayout'
import { ReportView } from './ReportView'
import { startBlocker } from './BenchTab'
import type { MotorLayoutData } from './model'

const LAYOUTS: Record<string, MotorLayoutData> = {
  quad: { name: 'quad', count: 4, motors: [
    { n: 1, angle_deg: 45, dir: 'ccw' }, { n: 2, angle_deg: -135, dir: 'ccw' }, { n: 3, angle_deg: -45, dir: 'cw' }, { n: 4, angle_deg: 135, dir: 'cw' },
  ] },
  hex: { name: 'hex', count: 6, motors: [90, -90, -30, 150, 30, -150].map((a, i) => ({ n: i + 1, angle_deg: a, dir: i % 2 ? 'ccw' : 'cw' })) },
  octo: { name: 'octo', count: 8, motors: [22.5, -157.5, 67.5, 157.5, -22.5, -112.5, -67.5, 112.5].map((a, i) => ({ n: i + 1, angle_deg: a, dir: i % 2 ? 'ccw' : 'cw' })) },
}

const count = (html: string, re: RegExp) => (html.match(re) ?? []).length

describe('motor layout', () => {
  for (const [name, layout] of Object.entries(LAYOUTS)) {
    it(`draws every motor of a ${name} from the layout`, () => {
      const html = renderToStaticMarkup(<MotorLayout layout={layout} target={2} spinning={[3]} done={[1]} />)
      expect(count(html, /data-motor="/g)).toBe(layout.count)
      expect(html).toContain(`data-bench-layout="${name}"`)
      expect(html).toMatch(/data-motor="2"[^>]*data-state="target"/)
      expect(html).toMatch(/data-motor="3"[^>]*data-state="spinning"/)
      expect(html).toMatch(/data-motor="1"[^>]*data-state="done"/)
      expect(count(html, /class="pulse"/g)).toBe(1)
    })
  }
})

const RUN: ProcedureRun = {
  run_id: 9,
  procedure: 'hover_tune',
  title_en: 'Hover and stability',
  title_zh: '悬停与稳定性',
  vehicle: 'sim-1',
  label: 'SIM-1',
  kind: 'sim',
  host: null,
  autopilot: 'PX4',
  firmware: '1.15.4',
  simulated: true,
  state: 'warned',
  step: null,
  index: 6,
  total: 6,
  verdict: 'warn',
  reason: null,
  reason_en: null,
  reason_zh: null,
  paused: false,
  by: { kind: 'session', name: 'agent' },
  options: {},
  started: 1.8e9,
  ended: 1.8e9 + 95,
  steps: [
    {
      id: 'S9', title_en: 'Hover test', title_zh: '悬停测试', description_en: '', description_zh: '', kind: 'flight', state: 'done', verdict: 'warn',
      metrics: [{ key: 'vibration', label_en: 'Vibration', label_zh: '振动', value: 18.2, unit: 'm/s2', level: 'warn', mean: 12, peak: 18.2, warn: 15, bad: 30 }],
      findings: [{ code: 'vib', level: 'warn', text_en: 'Balance the propellers.', text_zh: '请平衡桨叶。' }],
      suggestions: [],
      prompt: null, activity_en: null, activity_zh: null, live: null, started: 1.8e9, ended: 1.8e9 + 30,
    },
    {
      id: 'S11', title_en: 'Hover learning', title_zh: '悬停学习', description_en: '', description_zh: '', kind: 'operator_device', state: 'done', verdict: 'pass',
      metrics: [],
      findings: [],
      suggestions: [{ kind: 'param', name: 'MPC_THR_HOVER', current: 0.5, suggested: 0.42, direction: 'set', change_pct: -16, reason_en: 'Measured', reason_zh: '实测', state: 'kept' }],
      prompt: null, activity_en: null, activity_zh: null, live: null, started: 1.8e9 + 60, ended: 1.8e9 + 70,
    },
  ],
}

describe('report view', () => {
  it('lists every step, metric, finding and suggestion', () => {
    const html = renderToStaticMarkup(<ReportView run={RUN} />)
    expect(html).toContain('Bench report #9')
    expect(html).toContain('data-lf-table')
    expect(html).toContain('18.2 m/s²')
    expect(html).toContain('Balance the propellers.')
    expect(html).toContain('MPC_THR_HOVER')
    expect(html).toContain('Kept')
    expect(html).toContain('Suggestions are not written automatically.')
    expect(html).toContain('data-export="md"')
    expect(html).toContain('warn 15 · fail 30')
    expect(html).toContain('Pass 1 · Warning 1 · Fail 0')
  })
})

describe('start rules', () => {
  const base = { armed: false, link: { state: 'ok' }, controller: 'human', possession: { input: 'keyboard', pad: null, since: 0 }, crashed: false } as unknown as Vehicle
  it('needs a live, disarmed aircraft that the operator holds', () => {
    expect(startBlocker(base)).toBeNull()
    expect(startBlocker({ ...base, armed: true })).toBe('bench.unavailable.armed')
    expect(startBlocker({ ...base, link: { ...base.link, state: 'lost' } })).toBe('bench.unavailable.link')
    expect(startBlocker({ ...base, controller: 'agent', possession: null })).toBe('bench.unavailable.control')
    expect(startBlocker({ ...base, crashed: true })).toBe('bench.unavailable.crashed')
  })
})
