import { describe, expect, it } from 'vitest'
import { COPILOT_DICTS } from './strings'
import { useCopilot } from './store'

describe('copilot strings', () => {
  it('has identical keys in both languages', () => {
    expect(Object.keys(COPILOT_DICTS.zh).sort()).toEqual(Object.keys(COPILOT_DICTS.en).sort())
  })
})

describe('copilot store', () => {
  it('folds agent events into a transcript', () => {
    const s = useCopilot.getState()
    s.clear()
    s.pushUser('take off')
    s.apply({ type: 'busy', busy: true })
    s.apply({ type: 'assistant_start' })
    s.apply({ type: 'assistant_end', error: null, aborted: false }) // empty turn before a tool call is dropped
    s.apply({ type: 'tool_start', id: 't1', tool: 'relay_plan', args: { vehicle: 'sim-1', steps: [{ action: 'takeoff' }] } })
    s.apply({ type: 'tool_update', id: 't1', text: 'step 1 of 1: takeoff' })
    s.apply({ type: 'approval', id: 'ap-1', toolCallId: 't1', tool: 'relay_plan', vehicle: 'real-1', label: 'Quad 01', request: { action: 'plan' } })
    s.apply({ type: 'approval_closed', id: 'ap-1', approved: true })
    s.apply({ type: 'tool_end', id: 't1', tool: 'relay_plan', error: false, text: '{"state":"done"}' })
    s.apply({ type: 'assistant_start' })
    s.apply({ type: 'text', delta: 'Done' })
    s.apply({ type: 'assistant_end', error: null, aborted: false })
    s.apply({ type: 'busy', busy: false })
    const items = useCopilot.getState().items
    expect(items.map((i) => i.kind)).toEqual(['user', 'tool', 'approval', 'assistant'])
    expect(items[1]).toMatchObject({ state: 'ok', progress: 'step 1 of 1: takeoff' })
    expect(items[2]).toMatchObject({ state: 'approved' })
    expect(items[3]).toMatchObject({ text: 'Done', streaming: false })
    expect(useCopilot.getState().busy).toBe(false)
  })
})
