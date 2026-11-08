import { beforeEach, describe, expect, it } from 'vitest'
import type { CopilotEvent } from '@/api'
import { fmtCount, fmtMs, phaseText } from './RunTrace'
import { applyRunEvent, RUNS_KEPT, runEnded, turnNotice, useCopilot, type CopilotRun, type ToolStep, type TurnStep } from './store'
import { translateCopilot, type CopilotKey } from './strings'

const usage = { input: 1200, output: 300, reasoning: 250, cacheRead: 0, total: 1500 }

function fold(events: CopilotEvent[], userKey: number | null = 1): CopilotRun[] {
  return events.reduce<CopilotRun[]>((runs, e, i) => applyRunEvent(runs, e, e.type === 'run_start' ? userKey : null, 1000 + i * 10), [])
}

const RUN: CopilotEvent[] = [
  { type: 'run_start', run: 7, at: 1000, model: 'deepseek-flash', thinking: 'auto' },
  { type: 'turn_start', run: 7, turn: 1, at: 1000 },
  { type: 'thinking', run: 7, turn: 1, delta: 'The pilot needs gps. ' },
  { type: 'thinking', run: 7, turn: 1, delta: 'Two attackers on team B.' },
  { type: 'draft', run: 7, turn: 1, tool: 'dogfight_start', chars: 180 },
  {
    type: 'assistant_end',
    run: 7,
    turn: 1,
    error: null,
    aborted: false,
    stopReason: 'toolUse',
    usage,
    ms: 4200,
    toolCalls: [{ id: 'c1', name: 'dogfight_start', args: '{"entrants":[]}' }],
    empty: false,
  },
  { type: 'tool_start', run: 7, turn: 1, id: 'c1', tool: 'dogfight_start', args: { practice: true }, at: 5300 },
  { type: 'tool_end', id: 'c1', tool: 'dogfight_start', error: false, text: 'ok', full: '{"ok":true}', ms: 900 },
  { type: 'turn_start', run: 7, turn: 2, at: 6300 },
  { type: 'text', run: 7, turn: 2, delta: 'Three aircraft are up.' },
  { type: 'assistant_end', run: 7, turn: 2, error: null, aborted: false, stopReason: 'stop', usage, ms: 1500, toolCalls: [], empty: false },
  { type: 'run_end', run: 7, at: 7800, ms: 6800, turns: 2, tools: 1, usage: { ...usage, total: 3000 }, outcome: 'done' },
]

describe('run trace (08 §3)', () => {
  it('folds a run into turns and tool steps', () => {
    const [run] = fold(RUN)
    expect(run.id).toBe(7)
    expect(run.userKey).toBe(1)
    expect(run.phase).toBe('done')
    expect(runEnded(run)).toBe(true)
    expect(run.turns).toBe(2)
    expect(run.tools).toBe(1)
    expect(run.usage?.total).toBe(3000)
    expect(run.steps.map((s) => s.kind)).toEqual(['turn', 'tool', 'turn'])
    const t1 = run.steps[0] as TurnStep
    expect(t1.thinking).toBe('The pilot needs gps. Two attackers on team B.')
    expect(t1.stopReason).toBe('toolUse')
    expect(t1.endedAt! - t1.startedAt).toBe(4200)
    expect(t1.toolCalls[0].name).toBe('dogfight_start')
    const tool = run.steps[1] as ToolStep
    expect(tool.state).toBe('ok')
    expect(tool.result).toBe('{"ok":true}')
    expect(tool.endedAt! - tool.startedAt).toBe(900)
    expect((run.steps[2] as TurnStep).text).toBe('Three aircraft are up.')
  })

  it('tracks the phase while the run goes', () => {
    const phases = RUN.slice(0, 7).map((_, i) => fold(RUN.slice(0, i + 1))[0].phase)
    expect(phases).toEqual(['waiting', 'waiting', 'thinking', 'thinking', 'drafting', 'waiting', 'tool'])
    const drafting = fold(RUN.slice(0, 5))[0]
    expect(drafting.tool).toBe('dogfight_start')
    expect(drafting.draftChars).toBe(180)
    expect(drafting.thinkingChars).toBe(45)
  })

  it('ends a run the main process never closed when it goes idle', () => {
    const runs = fold([...RUN.slice(0, 3), { type: 'busy', busy: false }])
    expect(runs[0].phase).toBe('error')
    expect(runs[0].endedAt).not.toBeNull()
  })

  it('shows approvals as a phase', () => {
    const runs = fold([
      ...RUN.slice(0, 2),
      { type: 'approval', id: 'a1', toolCallId: 'c1', tool: 'relay_motor_test', vehicle: 'sim-1', label: 'sim-1', request: {} },
    ])
    expect(runs[0].phase).toBe('approval')
    expect(runs[0].tool).toBe('relay_motor_test')
  })

  it('keeps the newest runs only', () => {
    let runs: CopilotRun[] = []
    for (let i = 0; i < RUNS_KEPT + 5; i++) runs = applyRunEvent(runs, { type: 'run_start', run: i, at: i, model: 'm', thinking: 'auto' }, i, i)
    expect(runs).toHaveLength(RUNS_KEPT)
    expect(runs[0].id).toBe(5)
  })

  it('names a turn that ran out of output or said nothing', () => {
    const base = { type: 'assistant_end' as const, error: null, aborted: false }
    expect(turnNotice({ ...base, stopReason: 'length', empty: true })).toBe('length')
    expect(turnNotice({ ...base, stopReason: 'stop', empty: true })).toBe('empty')
    expect(turnNotice({ ...base, stopReason: 'stop', empty: false })).toBeNull()
    expect(turnNotice({ ...base, aborted: true, stopReason: 'aborted', empty: true })).toBeNull()
  })
})

describe('run trace in the store', () => {
  beforeEach(() => useCopilot.getState().clear())

  it('attaches a run to the newest user message and keeps an empty turn as a notice', () => {
    const s = useCopilot.getState()
    s.pushUser('three aircraft')
    const apply = (e: CopilotEvent) => useCopilot.getState().apply(e)
    apply({ type: 'run_start', run: 1, at: 1, model: 'deepseek-flash', thinking: 'auto' })
    apply({ type: 'turn_start', run: 1, turn: 1, at: 1 })
    apply({ type: 'assistant_start', run: 1, turn: 1 })
    apply({ type: 'assistant_end', run: 1, turn: 1, error: null, aborted: false, stopReason: 'length', usage, ms: 30000, toolCalls: [], empty: true })
    apply({ type: 'run_end', run: 1, at: 30001, ms: 30000, turns: 1, tools: 0, usage, outcome: 'length' })
    const st = useCopilot.getState()
    const user = st.items.find((it) => it.kind === 'user')!
    expect(st.runs[0].userKey).toBe(user.key)
    expect(st.runs[0].phase).toBe('length')
    expect(st.items.some((it) => it.kind === 'assistant')).toBe(false)
    expect(st.items.find((it) => it.kind === 'notice')).toMatchObject({ code: 'length', tone: 'error' })
    st.toggleRun(1)
    expect(useCopilot.getState().openRun).toBe(1)
    useCopilot.getState().toggleRun(1)
    expect(useCopilot.getState().openRun).toBeNull()
  })
})

describe('run chip text', () => {
  const t = (k: CopilotKey, p?: Record<string, string | number>) => translateCopilot('en', k, p)
  it('formats durations and counts', () => {
    expect(fmtMs(420)).toBe('420 ms')
    expect(fmtMs(4200)).toBe('4.2 s')
    expect(fmtMs(125_000)).toBe('2 min 5 s')
    expect(fmtMs(null)).toBe('—')
    expect(fmtCount(950)).toBe('950')
    expect(fmtCount(12_345)).toBe('12.3k')
  })
  it('describes each phase', () => {
    const runs = fold(RUN.slice(0, 5))
    expect(phaseText(runs[0], t, 'en')).toBe('Preparing Start dogfight · 180 chars')
    expect(phaseText(fold(RUN.slice(0, 3))[0], t, 'en')).toBe('Thinking · 21 chars')
    expect(phaseText(fold(RUN)[0], t, 'en')).toBe('Done')
    expect(translateCopilot('zh', 'cp.notice.length', { max: 16384 })).toContain('16384')
  })
})
