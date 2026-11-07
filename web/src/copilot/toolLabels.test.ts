import { describe, expect, it } from 'vitest'
import { TOOL_LABELS, toolLabel } from './toolLabels'

describe('tool labels', () => {
  it('names every tool in both languages', () => {
    for (const [id, [en, zh]] of Object.entries(TOOL_LABELS)) {
      expect(en, id).toBeTruthy()
      expect(zh, id).toBeTruthy()
    }
  })
  it('falls back to the tool id', () => {
    expect(toolLabel('relay_land', 'zh')).toBe('降落')
    expect(toolLabel('relay_land', 'en')).toBe('Land')
    expect(toolLabel('mystery_tool', 'en')).toBe('mystery_tool')
  })
})
