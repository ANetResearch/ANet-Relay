import { describe, expect, it } from 'vitest'
import type { ProcedurePrompt, ProcedureRun, ProcedureStep } from '@/api'
import { counts, currentStep, formatValue, guardedAnswer, isActive, motorPoint, progress, promptLayout, reportFile, sparkline, spinningMotors, stateTone, stepTone } from './model'

function step(id: string, over: Partial<ProcedureStep> = {}): ProcedureStep {
  return {
    id,
    title_en: id,
    title_zh: id,
    description_en: '',
    description_zh: '',
    kind: 'auto',
    state: 'pending',
    verdict: null,
    metrics: [],
    findings: [],
    suggestions: [],
    prompt: null,
    activity_en: null,
    activity_zh: null,
    live: null,
    started: null,
    ended: null,
    ...over,
  }
}

function run(over: Partial<ProcedureRun> = {}): ProcedureRun {
  return {
    run_id: 1,
    procedure: 'motor_check',
    title_en: 'Motor order and direction',
    title_zh: '电机顺序与转向',
    vehicle: 'sim-1',
    label: 'SIM-1',
    kind: 'sim',
    host: null,
    autopilot: 'ArduPilot',
    firmware: '4.5.7',
    simulated: true,
    state: 'running',
    step: 'S6',
    index: 1,
    total: 3,
    verdict: null,
    reason: null,
    reason_en: null,
    reason_zh: null,
    paused: false,
    by: { kind: 'operator', name: null },
    options: {},
    steps: [step('S1', { state: 'done', verdict: 'pass' }), step('S6', { state: 'waiting' }), step('S12')],
    started: 1000,
    ended: null,
    ...over,
  }
}

const prompt = (over: Partial<ProcedurePrompt> = {}): ProcedurePrompt => ({
  id: 'props_removed',
  text_en: 'Remove the propellers',
  text_zh: '拆桨',
  detail_en: null,
  detail_zh: null,
  answers: [
    { id: 'confirm', label_en: 'Confirm', label_zh: '确认', tone: 'primary' },
    { id: 'skip', label_en: 'Skip', label_zh: '跳过', tone: 'default' },
  ],
  confirm_word: null,
  operator_only: false,
  data: {},
  ...over,
})

describe('bench model', () => {
  it('tracks the run state, current step and progress', () => {
    const r = run()
    expect(isActive(r.state)).toBe(true)
    expect(isActive('passed')).toBe(false)
    expect(currentStep(r)?.id).toBe('S6')
    expect(progress(r)).toBeCloseTo(1 / 3)
    expect(progress(run({ state: 'failed' }))).toBe(1)
    expect(currentStep(run({ step: null }))).toBeNull()
  })
  it('maps states and verdicts to tones', () => {
    expect(stateTone('passed')).toBe('ok')
    expect(stateTone('warned')).toBe('warn')
    expect(stateTone('aborted')).toBe('bad')
    expect(stateTone('waiting')).toBe('active')
    expect(stepTone({ state: 'done', verdict: 'fail' })).toBe('bad')
    expect(stepTone({ state: 'waiting', verdict: null })).toBe('active')
    expect(stepTone({ state: 'skipped', verdict: 'skipped' })).toBe('muted')
  })
  it('formats metric values with their units', () => {
    expect(formatValue(7.691, 'm/s2')).toBe('7.69 m/s²')
    expect(formatValue(49.856, '%')).toBe('49.9 %')
    expect(formatValue('4 / 4', '')).toBe('4 / 4')
    expect(formatValue(null, 's')).toBe('—')
    expect(formatValue(0.004, '')).toBe('0.004')
    expect(formatValue(1500, 'us')).toBe('1500 us')
  })
  it('places motors clockwise from the nose', () => {
    const front = motorPoint(0, 10, 50, 50)
    expect(front.x).toBeCloseTo(50)
    expect(front.y).toBeCloseTo(40)
    const right = motorPoint(90, 10, 50, 50)
    expect(right.x).toBeCloseTo(60)
    expect(right.y).toBeCloseTo(50)
  })
  it('reads the layout from a prompt and the spinning motors from the outputs', () => {
    expect(promptLayout(prompt())).toBeNull()
    const layout = { name: 'hex', count: 6, motors: [1, 2, 3, 4, 5, 6].map((n) => ({ n, angle_deg: n * 60, dir: n % 2 ? 'cw' : 'ccw' })) }
    expect(promptLayout(prompt({ data: { layout } }))?.count).toBe(6)
    expect(spinningMotors([1000, 1080, 1180, 1000, 1500, 1000, 1900, 1900], 6)).toEqual([3, 5])
    expect(spinningMotors(null, 4)).toEqual([])
  })
  it('guards the primary answer with the confirm word', () => {
    expect(guardedAnswer(prompt())).toBeNull()
    expect(guardedAnswer(prompt({ confirm_word: 'MOTOR' }))).toBe('confirm')
  })
  it('scales a sparkline and skips gaps', () => {
    const s = sparkline([0, null, 10, 5], 100, 20, 0)
    expect(s.points.split(' ')).toHaveLength(3)
    expect(s.points.startsWith('0.0,20.0')).toBe(true)
    expect(s.last).toBe(5)
    expect(sparkline([], 100, 20).points).toBe('')
    expect(sparkline([3, 3], 100, 20).min).toBe(2)
  })
  it('counts verdicts and names the export', () => {
    expect(counts(run())).toEqual({ pass: 1, warn: 0, fail: 0, skipped: 0 })
    expect(reportFile(7, 'md')).toBe('anet-relay-bench-7.md')
  })
})
