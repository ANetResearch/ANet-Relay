import { describe, expect, it } from 'vitest'
import { Text } from '@codemirror/state'
import type { Program } from '@/api'
import { normChallenges, normJob, normLadder, jobFinished } from './api'
import { LAB_DICTS, reasonKey, type LabKey } from './copy'
import { diffStats, lineDiff, withContext } from './diff'
import { parseTrace, recentEvent, sampleAt, sampleIndex, shotsAt, traceStageId } from './trace'
import { isDirty, NEW_SOURCE, testsOf } from './store'
import { intLiteral, paramRange, paramStep } from './ParamsTab'
import { parseObject } from './TestsTab'
import { toDiagnostics } from './CodeEditor'
import { revisionTime } from './HistoryTab'
import { craftsAt } from './traceReplay'
import { fixtureTrace, fixtureValidate } from '@/dev/labFixture'

describe('lab copy', () => {
  const ph = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()
  it('has identical key sets and placeholders in English and Chinese, none empty, no exclamation marks', () => {
    const en = Object.keys(LAB_DICTS.en).sort()
    expect(Object.keys(LAB_DICTS.zh).sort()).toEqual(en)
    for (const k of en as LabKey[]) {
      expect(LAB_DICTS.en[k].trim(), k).not.toBe('')
      expect(LAB_DICTS.zh[k].trim(), k).not.toBe('')
      expect(ph(LAB_DICTS.zh[k]), k).toEqual(ph(LAB_DICTS.en[k]))
      for (const lang of ['en', 'zh'] as const) expect(LAB_DICTS[lang][k], k).not.toMatch(/[!！]/)
    }
  })
  it('names every program stop reason of the backend', () => {
    for (const code of ['stopped', 'possessed', 'emergency_stop', 'link_lost', 'crashed', 'removed', 'reset', 'shutdown', 'superseded', 'claim_ended', 'replaced', 'not_allowed', 'faults', 'host', 'memory']) {
      expect(reasonKey(code), code).not.toBeNull()
    }
    expect(reasonKey('something_new')).toBeNull()
    expect(reasonKey(null)).toBeNull()
  })
  it('keeps the confirm word and the contract labels', () => {
    expect(LAB_DICTS.en['lab.grp.allow']).toBe('Allow programs on this aircraft')
    expect(LAB_DICTS.en['lab.grp.allowPrompt']).toContain('PROGRAM')
    expect(LAB_DICTS.zh['lab.grp.allowPrompt']).toContain('PROGRAM')
    expect(LAB_DICTS.en['lab.tab.params']).toBe('Params')
    expect(LAB_DICTS.zh['lab.tab.history']).toBe('历史')
  })
})

describe('line diff', () => {
  it('marks added and removed lines with old and new numbers', () => {
    const rows = lineDiff('a\nb\nc\nd', 'a\nB\nc\nd\ne')
    expect(rows.map((r) => `${r.kind}:${r.text}:${r.a}:${r.b}`)).toEqual(['same:a:1:1', 'add:B:null:2', 'del:b:2:null', 'same:c:3:3', 'same:d:4:4', 'add:e:null:5'])
    expect(diffStats(rows)).toEqual({ added: 2, removed: 1 })
  })
  it('reports no change for equal texts and folds distant unchanged lines', () => {
    expect(diffStats(lineDiff('x\ny', 'x\ny'))).toEqual({ added: 0, removed: 0 })
    const a = Array.from({ length: 30 }, (_, i) => `l${i}`).join('\n')
    const b = a.replace('l15', 'L15')
    const shown = withContext(lineDiff(a, b), 2)
    expect(shown[0]).toBeNull()
    expect(shown.filter((r) => r && r.kind !== 'same')).toHaveLength(2)
    expect(shown.at(-1)).toBeNull()
  })
  it('handles empty texts', () => {
    expect(diffStats(lineDiff('', 'a\nb'))).toEqual({ added: 2, removed: 1 })
  })
})

describe('trace', () => {
  const raw = {
    id: 'tr-1',
    kind: 'duel',
    title: 'duel_easy · seed 3',
    hz: 10,
    arena: { radius: 8, floor: 0.3, ceiling: 4 },
    aircraft: [
      { id: 'a', name: 'Hunter', program: 'hunter_easy', rev: 1, samples: { t: [0, 1, 2], x: [0, 1, 2], y: [0, 0, 0], z: [1, 1, 1], roll: [0, 10, 0], pitch: [0, 0, 0], yaw: [350, 10, 30], throttle: [0.5, 0.6, 2], hp: [5, 4, 4], state: ['search', 'fire', 'evade'] } },
      { id: 'b', samples: [[0, 5, 5, 1, 0, 0, 90, 0.5, 5, 'search', true], [2, 5, 6, 1, 0, 0, 90, 0.5, 3, 'evade', true]] },
      { id: 'broken', samples: { t: [] } },
    ],
    shots: [{ id: 's1', owner: 'a', t0: 0.5, p: [0, 0, 1], v: [35, 0, 0], t_end: 0.6 }, { owner: 'a', t0: 1.5, p: [1, 0, 1], v: [35, 0, 0] }, { t0: 'x' }],
    events: [{ t: 0.6, kind: 'hit', vehicle: 'b', by: 'a' }],
  }
  it('parses columns and rows, drops aircraft without samples and converts degrees', () => {
    const tr = parseTrace(raw)!
    expect(tr.aircraft.map((c) => c.id)).toEqual(['a', 'b'])
    expect(tr.aircraft[1].name).toBe('b')
    expect(tr.aircraft[0].yaw[0]).toBeCloseTo((350 * Math.PI) / 180)
    expect(tr.aircraft[0].throttle[2]).toBe(1)
    expect(tr.duration_s).toBe(2)
    expect(tr.shots).toHaveLength(2)
    expect(tr.arena?.radius).toBe(8)
    expect(parseTrace({ trace: raw })?.id).toBe('tr-1')
    expect(parseTrace({ aircraft: [] })).toBeNull()
    expect(parseTrace(null)).toBeNull()
  })
  it('interpolates position and takes yaw the short way round', () => {
    const tr = parseTrace(raw)!
    const s = sampleAt(tr.aircraft[0], 0.5)
    expect(s.x).toBeCloseTo(0.5)
    expect((s.yaw * 180) / Math.PI).toBeCloseTo(360, 0)
    expect(s.state).toBe('search')
    expect(sampleAt(tr.aircraft[0], 1.5).hp).toBe(4)
    expect(sampleAt(tr.aircraft[0], -1).x).toBe(0)
    expect(sampleAt(tr.aircraft[0], 9).x).toBe(2)
    expect(sampleIndex([0, 1, 2], 1)).toBe(1)
    expect(sampleIndex([0, 1, 2], -0.1)).toBe(-1)
  })
  it('keeps a shot in the air until its hit or its lifetime', () => {
    const tr = parseTrace(raw)!
    expect(shotsAt(tr.shots, 0.55).map((s) => s.id)).toEqual(['s1'])
    expect(shotsAt(tr.shots, 0.6)).toEqual([])
    expect(shotsAt(tr.shots, 1.6)).toHaveLength(1)
    expect(shotsAt(tr.shots, 4.1)).toHaveLength(0)
    expect(recentEvent(tr.events, 1)?.kind).toBe('hit')
    expect(recentEvent(tr.events, 3)).toBeNull()
  })
  it('publishes prefixed stage ids so live aircraft never collide', () => {
    const crafts = craftsAt(parseTrace(raw)!, 1)
    expect(crafts.map((c) => c.id)).toEqual([traceStageId('a'), traceStageId('b')])
    expect(crafts[0].frame.controller).toBe('program')
    expect(crafts[0].frame.throttle).toBeCloseTo(60)
    expect(crafts[0].state).toBe('fire')
  })
  it('reads the fixture traces', () => {
    const tr = parseTrace(fixtureTrace('t', ['A', 'B'], 3, 'x'))!
    expect(tr.aircraft).toHaveLength(2)
    expect(tr.shots.length).toBeGreaterThan(0)
    expect(tr.events.some((e) => e.kind === 'hit')).toBe(true)
  })
})

describe('judge normalisers', () => {
  it('reads challenges with and without a best score', () => {
    const list = normChallenges({ challenges: [{ id: 'hover', name: 'Hover', best: { score: 64, passed: false } }, { id: 'rings' }, { name: 'no id' }] })
    expect(list.map((c) => c.id)).toEqual(['hover', 'rings'])
    expect(list[0].best?.score).toBe(64)
    expect(list[0].name_zh).toBe('Hover')
    expect(list[1].best).toBeNull()
  })
  it('reads a challenge job and a duel job, wrapped or not', () => {
    const a = normJob({ job: { id: 'j1', state: 'running', progress: 40 } })
    expect(a).toMatchObject({ id: 'j1', state: 'running', progress: 0.4, kind: 'challenge', result: null })
    expect(jobFinished(a)).toBe(false)
    const b = normJob({ id: 'j1', state: 'done', result: { score: 88.5, passed: true, metrics: { drift_m: 0.3, ok: true, x: [1] }, seeds: [{ seed: 1, hidden: true, score: 90, passed: true, trace_id: 't1' }], trace_id: 't0' } })
    expect(b.result).toMatchObject({ kind: 'challenge', score: 88.5, passed: true, trace_id: 't0' })
    expect(b.result?.kind === 'challenge' && b.result.metrics).toEqual([['drift_m', 0.3], ['ok', true], ['x', null]])
    expect(jobFinished(b)).toBe(true)
    const c = normJob({ id: 'j2', kind: 'duel', state: 'done', result: { rounds: [{ round: 1, winner: null, entries: [{ program: 'a', hits: 2, survived: true }] }], ladder: [{ program: 'a', elo: 1100 }, { program: 'b', elo: 1300, wins: 2 }] } })
    expect(c.result?.kind).toBe('duel')
    if (c.result?.kind === 'duel') {
      expect(c.result.rounds[0].winner).toBeNull()
      expect(c.result.ladder.map((r) => r.program)).toEqual(['b', 'a'])
      expect(c.result.ladder[0].played).toBe(2)
    }
    expect(normJob({ id: 'j3', state: 'failed', error: 'boom' }).error?.code).toBe('boom')
    expect(normJob({ state: 'weird' }).state).toBe('queued')
  })
  it('sorts the ladder by Elo', () => {
    expect(normLadder([{ id: 'x', rating: 900 }, { program: 'y', elo: 1000 }]).map((r) => r.program)).toEqual(['y', 'x'])
  })
})

describe('lab store helpers', () => {
  const program = (over: Partial<Program> = {}): Program => ({
    id: 'p', name: 'P', name_zh: 'P', description: '', description_zh: '', builtin: false, rev: 2, author: 'operator', created: 0, updated: 0, test_count: 1,
    source: 'x = 1', params: { a: 1 }, tests: [{ name: 't', obs: { alt: 1 }, expect: { throttle: [0, 1] } }], ...over,
  })
  it('detects unsaved source, parameters and tests; examples are never dirty', () => {
    const p = program()
    const base = { program: p, source: p.source, params: { a: 1 }, tests: testsOf(p) }
    expect(isDirty(base)).toBe(false)
    expect(isDirty({ ...base, source: 'x = 2' })).toBe(true)
    expect(isDirty({ ...base, params: {} })).toBe(true)
    expect(isDirty({ ...base, tests: [] })).toBe(true)
    expect(isDirty({ ...base, program: program({ builtin: true }), source: 'changed' })).toBe(false)
    expect(isDirty({ ...base, program: null })).toBe(false)
  })
  it('starts new programs from a source that validates', () => {
    expect(fixtureValidate(NEW_SOURCE).ok).toBe(true)
    expect(fixtureValidate(NEW_SOURCE).params).toMatchObject({ target_alt: 1.0 })
  })
})

describe('params, tests and diagnostics helpers', () => {
  it('derives ranges and steps like the backend', () => {
    expect(paramRange(0.5, [0.2, 0.8], false)).toEqual([0.2, 0.8])
    expect(paramRange(0.5, undefined, false)).toEqual([-5, 5])
    expect(paramRange(0, undefined, false)).toEqual([-1, 1])
    expect(paramRange(3, undefined, true)).toEqual([-30, 30])
    expect(paramRange(0, undefined, true)).toEqual([-10, 10])
    expect(paramStep(0, 1, false)).toBeCloseTo(0.005)
    expect(paramStep(-30, 30, true)).toBe(1)
  })
  it('tells integer literals from floats in PARAMS', () => {
    const src = 'PARAMS = {"target_alt": 1.0, "count": 3, \'n\': -2}\n'
    expect(intLiteral(src, 'target_alt')).toBe(false)
    expect(intLiteral(src, 'count')).toBe(true)
    expect(intLiteral(src, 'n')).toBe(true)
    expect(intLiteral(src, 'missing')).toBe(false)
  })
  it('parses test fields as JSON objects only', () => {
    expect(parseObject('{"alt": 1}')).toEqual({ alt: 1 })
    expect(parseObject('')).toEqual({})
    expect(parseObject('[1]')).toBeNull()
    expect(parseObject('{bad')).toBeNull()
  })
  it('maps 1-based problems into the document, clamped', () => {
    const doc = Text.of(['import os', 'x = 1'])
    const p = { line: 1, col: 8, end_line: 1, end_col: 10, code: 'import', message_en: 'Only math.', message_zh: '只能 math。' }
    const [d] = toDiagnostics(doc, [p], 'zh')
    expect([d.from, d.to, d.message, d.severity]).toEqual([7, 9, '只能 math。', 'error'])
    const [far] = toDiagnostics(doc, [{ ...p, line: 99, col: 50, end_line: 99, end_col: 60 }], 'en')
    expect(far.from).toBeLessThanOrEqual(doc.length)
    expect(far.to).toBeLessThanOrEqual(doc.length)
  })
  it('formats revision times as clock today and date earlier', () => {
    const now = new Date(2026, 10, 8, 15, 0, 0).getTime() / 1000
    expect(revisionTime(now - 60, now)).toMatch(/^14:59:00$/)
    expect(revisionTime(now - 86400 * 2, now)).toMatch(/^2026-11-06 15:00$/)
    expect(revisionTime(0, now)).toBe('—')
  })
})

describe('lab fixture validator', () => {
  it('reports imports other than math and a missing step', () => {
    const v = fixtureValidate('import os\nPARAMS = {"a": 1}\n')
    expect(v.ok).toBe(false)
    expect(v.errors.map((e) => e.code).sort()).toEqual(['import', 'no_step'])
    expect(v.params).toEqual({ a: 1 })
  })
})
