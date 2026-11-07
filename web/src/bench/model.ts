// Pure helpers of the Bench tab (05 §4.5): run state, step tones, metric formatting, the motor layout geometry
// and the mini-chart paths. No React, no store: model.test.ts covers them.
import type { ProcedureMetric, ProcedureRun, ProcedureStep, ProcedurePrompt } from '@/api'

export type RunState = ProcedureRun['state']
export type Verdict = NonNullable<ProcedureStep['verdict']>
export type Tone = 'ok' | 'warn' | 'bad' | 'muted' | 'active'

export interface LayoutMotor {
  n: number
  angle_deg: number
  dir: 'cw' | 'ccw'
}
export interface MotorLayoutData {
  name: string
  count: number
  motors: LayoutMotor[]
}

export const ACTIVE_STATES: readonly RunState[] = ['running', 'waiting']

export function isActive(state: RunState | null | undefined): boolean {
  return state === 'running' || state === 'waiting'
}

export function currentStep(run: ProcedureRun | null): ProcedureStep | null {
  if (!run || !run.step) return null
  return run.steps.find((s) => s.id === run.step) ?? null
}

/** Done share of the run, 0..1 (a finished run is 1). */
export function progress(run: ProcedureRun): number {
  if (!isActive(run.state)) return 1
  const finished = run.steps.filter((s) => s.state === 'done' || s.state === 'skipped').length
  return run.total ? Math.min(1, finished / run.total) : 0
}

export function stateTone(state: RunState): Tone {
  if (state === 'passed') return 'ok'
  if (state === 'warned') return 'warn'
  if (state === 'failed' || state === 'aborted') return 'bad'
  return 'active'
}

export function stepTone(step: Pick<ProcedureStep, 'state' | 'verdict'>): Tone {
  if (step.state === 'running' || step.state === 'waiting') return 'active'
  if (step.verdict === 'fail' || step.state === 'aborted') return 'bad'
  if (step.verdict === 'warn') return 'warn'
  if (step.verdict === 'pass') return 'ok'
  return 'muted'
}

export function levelTone(level: ProcedureMetric['level']): Tone {
  return level === 'bad' ? 'bad' : level === 'warn' ? 'warn' : level === 'ok' ? 'ok' : 'muted'
}

function num(v: number): string {
  if (!Number.isFinite(v)) return '—'
  if (Number.isInteger(v)) return String(v)
  const a = Math.abs(v)
  if (a >= 1000) return v.toFixed(0)
  if (a >= 100) return v.toFixed(0)
  if (a >= 10) return v.toFixed(1)
  if (a >= 1) return v.toFixed(2)
  return Number(v.toPrecision(2)).toString()
}

/** Value with unit, e.g. "7.69 m/s²"; strings pass through. */
export function formatValue(value: ProcedureMetric['value'], unit: string): string {
  if (value === null || value === undefined || value === '') return '—'
  const text = typeof value === 'number' ? num(value) : String(value)
  const u = unit === 'm/s2' ? 'm/s²' : unit
  return u ? `${text} ${u}` : text
}

export function formatNumber(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : num(v)
}

/** Point of a motor on the layout diagram: angle clockwise from the nose (up), radius r around (cx, cy). */
export function motorPoint(angleDeg: number, r: number, cx: number, cy: number): { x: number; y: number } {
  const a = (angleDeg * Math.PI) / 180
  return { x: cx + r * Math.sin(a), y: cy - r * Math.cos(a) }
}

/** Layout of a prompt (S6, S7, S8 carry it in data.layout), or null. */
export function promptLayout(prompt: ProcedurePrompt | null | undefined): MotorLayoutData | null {
  const d = (prompt?.data ?? {}) as { layout?: MotorLayoutData }
  const l = d.layout
  return l && Array.isArray(l.motors) && l.motors.length ? l : null
}

/** Motors whose output is above idle in the snapshot's servo outputs (1-based motor numbers). */
export function spinningMotors(servo: readonly number[] | null | undefined, count: number, idleUs = 1100): number[] {
  if (!servo) return []
  const out: number[] = []
  for (let i = 0; i < Math.min(count, servo.length); i++) if ((servo[i] ?? 0) > idleUs) out.push(i + 1)
  return out
}

/** SVG polyline points of a series scaled into w x h; nulls break nothing (they are skipped). */
export function sparkline(values: readonly (number | null)[], w: number, h: number, pad = 2): { points: string; min: number; max: number; last: number | null } {
  const finite = values.filter((v): v is number => v !== null && Number.isFinite(v))
  if (finite.length === 0) return { points: '', min: 0, max: 0, last: null }
  let min = Math.min(...finite)
  let max = Math.max(...finite)
  if (max - min < 1e-6) {
    min -= 1
    max += 1
  }
  const n = values.length
  const xs = (i: number) => (n <= 1 ? w / 2 : (i / (n - 1)) * w)
  const ys = (v: number) => pad + (1 - (v - min) / (max - min)) * (h - 2 * pad)
  const pts: string[] = []
  values.forEach((v, i) => {
    if (v !== null && Number.isFinite(v)) pts.push(`${xs(i).toFixed(1)},${ys(v).toFixed(1)}`)
  })
  return { points: pts.join(' '), min, max, last: finite[finite.length - 1] ?? null }
}

/** Seconds between two wall times, or null. */
export function elapsed(start: number | null | undefined, end: number | null | undefined): number | null {
  return start === null || start === undefined || end === null || end === undefined ? null : Math.max(0, end - start)
}

/** Counts by verdict over the steps of a run. */
export function counts(run: Pick<ProcedureRun, 'steps'>): { pass: number; warn: number; fail: number; skipped: number } {
  const c = { pass: 0, warn: 0, fail: 0, skipped: 0 }
  for (const s of run.steps) if (s.verdict) c[s.verdict] += 1
  return c
}

/** The answer a TypeToConfirm guards: the primary answer when the prompt carries a confirm word. */
export function guardedAnswer(prompt: ProcedurePrompt): string | null {
  if (!prompt.confirm_word) return null
  return (prompt.answers.find((a) => a.tone === 'primary') ?? prompt.answers[0])?.id ?? null
}

/** File name of an exported report. */
export function reportFile(runId: number, fmt: 'md' | 'json'): string {
  return `anet-relay-bench-${runId}.${fmt}`
}
