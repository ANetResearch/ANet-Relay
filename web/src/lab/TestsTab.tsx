// Tests tab (06 §3.1, §7): the program's unit tests, edited as name + observation + expectation (JSON objects), and
// run in one click through POST /programs/{id}/test against the editor's current source. Results read like
// LeetCode samples: one row per test with a pass mark, the actual sticks and state, and each expectation missed.
// Tests change in place (no revision); they are saved with the program's Save.
import * as React from 'react'
import type { ProgramTestResult, ProgramTestResults } from '@/api'
import { cn } from '@/lib/utils'
import { notifyError } from '@/shell/notify'
import { Button } from '@/ui/components/ui/button'
import { Input } from '@/ui/components/ui/input'
import { Textarea } from '@/ui/components/ui/textarea'
import { Icon } from '@/ui/icons'
import { labApi, type ProgramTestCase } from './api'
import { useTL } from './copy'
import { useLab } from './store'

const OUT_KEYS = ['roll', 'pitch', 'yaw', 'throttle', 'fire'] as const

/** Parses a JSON object field of the test editor; null when it is not an object. */
export function parseObject(text: string): Record<string, unknown> | null {
  try {
    const v: unknown = JSON.parse(text.trim() || '{}')
    return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null
  } catch {
    return null
  }
}

const show = (x: unknown): string => (typeof x === 'number' ? String(Math.round(x * 1000) / 1000) : JSON.stringify(x))

function TestEditor({ test, onChange, onRemove, onDone }: { test: ProgramTestCase; onChange(t: ProgramTestCase): void; onRemove(): void; onDone(): void }) {
  const tl = useTL()
  const [obs, setObs] = React.useState(() => JSON.stringify(test.obs, null, 1))
  const [expect, setExpect] = React.useState(() => JSON.stringify(test.expect, null, 1))
  const obsOk = parseObject(obs) !== null
  const expectOk = parseObject(expect) !== null
  const commit = (field: 'obs' | 'expect', text: string) => {
    const v = parseObject(text)
    if (v) onChange({ ...test, [field]: v })
  }
  return (
    <div className="flex flex-col gap-2 rounded-md border border-[var(--border)] bg-[var(--g950)] p-2.5" data-lab-test-editor="">
      <label className="flex flex-col gap-1">
        <span className="cap">{tl('lab.tests.name')}</span>
        <Input value={test.name} maxLength={64} onChange={(e) => onChange({ ...test, name: e.target.value })} className="h-7" />
      </label>
      <label className="flex flex-col gap-1">
        <span className="cap">{tl('lab.tests.obs')}</span>
        <Textarea
          value={obs}
          onChange={(e) => {
            setObs(e.target.value)
            commit('obs', e.target.value)
          }}
          rows={3}
          spellCheck={false}
          className="num min-h-16 text-[11px]"
          aria-invalid={!obsOk || undefined}
        />
        <span className={cn('text-hud-sub', obsOk ? 'text-[var(--g500)]' : 'font-bold text-[var(--g50)]')}>{obsOk ? tl('lab.tests.obsHint') : tl('lab.tests.badJson', { field: tl('lab.tests.obs') })}</span>
      </label>
      <label className="flex flex-col gap-1">
        <span className="cap">{tl('lab.tests.expect')}</span>
        <Textarea
          value={expect}
          onChange={(e) => {
            setExpect(e.target.value)
            commit('expect', e.target.value)
          }}
          rows={3}
          spellCheck={false}
          className="num min-h-16 text-[11px]"
          aria-invalid={!expectOk || undefined}
        />
        <span className={cn('text-hud-sub', expectOk ? 'text-[var(--g500)]' : 'font-bold text-[var(--g50)]')}>
          {expectOk ? tl('lab.tests.expectHint') : tl('lab.tests.badJson', { field: tl('lab.tests.expect') })}
        </span>
      </label>
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" className="gap-1.5 text-[var(--g300)]" onClick={onRemove}>
          <Icon icon="trash" />
          {tl('lab.tests.remove')}
        </Button>
        <span className="flex-1" />
        <Button variant="outline" size="sm" onClick={onDone} disabled={!obsOk || !expectOk}>
          {tl('lab.tests.done')}
        </Button>
      </div>
    </div>
  )
}

function ResultLine({ r }: { r: ProgramTestResult }) {
  const tl = useTL()
  const out = (r.out ?? {}) as Record<string, unknown>
  const failures = r.failures as { field?: string; expected?: unknown; actual?: unknown }[]
  return (
    <div className="flex flex-col gap-0.5 pl-5">
      {r.error ? <span className="addr break-words text-[var(--g100)]">{r.error}</span> : null}
      {r.out ? (
        <span className="num text-[11px] text-[var(--g400)]">
          {tl('lab.tests.output')}: {OUT_KEYS.filter((k) => k in out).map((k) => `${k} ${show(out[k])}`).join('  ')}
          {r.state ? `  state ${r.state}` : ''}
        </span>
      ) : null}
      {failures.map((f, i) => (
        <span key={i} className="num text-[11px] font-bold text-[var(--g50)]">
          {tl('lab.tests.mismatch', { field: f.field ?? '?', expected: show(f.expected), actual: show(f.actual) })}
        </span>
      ))}
    </div>
  )
}

export function TestsTab() {
  const tl = useTL()
  const program = useLab((s) => s.program)
  const tests = useLab((s) => s.tests)
  const source = useLab((s) => s.source)
  const [results, setResults] = React.useState<{ res: ProgramTestResults; source: string; tests: string } | null>(null)
  const [running, setRunning] = React.useState(false)
  const [editing, setEditing] = React.useState<number | null>(null)
  const pid = program?.id
  const [seenPid, setSeenPid] = React.useState(pid)
  if (seenPid !== pid) {
    setSeenPid(pid)
    setResults(null)
    setEditing(null)
  }
  if (!program) return null
  const readOnly = program.builtin
  const run = async () => {
    setRunning(true)
    try {
      const res = await labApi.test(program.id, { source, tests })
      setResults({ res, source, tests: JSON.stringify(tests) })
    } catch (e) {
      notifyError(e)
    } finally {
      setRunning(false)
    }
  }
  const setTests = (next: ProgramTestCase[]) => useLab.getState().setTests(next)
  const stale = results !== null && (results.source !== source || results.tests !== JSON.stringify(tests))
  const byIndex = results?.res.results ?? []
  return (
    <div className="flex flex-col gap-3" data-lab-tests="">
      <div className="flex items-center gap-2 px-3">
        <Button variant="outline" size="sm" className="gap-1.5" disabled={running || !tests.length} onClick={() => void run()} data-lab-run-tests="">
          <Icon icon="tl.play" />
          {running ? tl('lab.tests.running') : tl('lab.tests.run')}
        </Button>
        {results ? (
          <span className={cn('num text-[12px]', results.res.passed === results.res.total ? 'font-bold text-[var(--g50)]' : 'text-[var(--g200)]')} data-lab-test-summary="">
            {tl('lab.tests.summary', { passed: results.res.passed, total: results.res.total })}
          </span>
        ) : null}
        <span className="flex-1" />
        {!readOnly ? (
          <Button
            variant="ghost"
            size="sm"
            className="gap-1.5 text-[var(--g300)]"
            onClick={() => {
              setTests([...tests, { name: tl('lab.tests.newName', { n: tests.length + 1 }), obs: { alt: 0.5 }, expect: { throttle: [0.5, 1] } }])
              setEditing(tests.length)
            }}
          >
            <Icon icon="plus" />
            {tl('lab.tests.add')}
          </Button>
        ) : null}
      </div>
      {stale ? <p className="px-3 text-hud-sub text-[var(--g500)]">{tl('lab.tests.stale')}</p> : null}
      {tests.length === 0 ? (
        <p className="px-3 text-hud-sub text-[var(--g500)]">{tl('lab.tests.none')}</p>
      ) : (
        <ol className="lab-tests flex flex-col">
          {tests.map((test, i) => {
            const r = byIndex[i]
            const verdict = r ? (r.error ? 'error' : r.passed ? 'pass' : 'fail') : null
            return (
              <li key={i} className="flex flex-col gap-1 px-3 py-2" data-lab-test={i} data-verdict={verdict ?? undefined}>
                {editing === i && !readOnly ? (
                  <TestEditor
                    test={test}
                    onChange={(t) => setTests(tests.map((x, j) => (j === i ? t : x)))}
                    onRemove={() => {
                      setTests(tests.filter((_, j) => j !== i))
                      setEditing(null)
                    }}
                    onDone={() => setEditing(null)}
                  />
                ) : (
                  <>
                    <div className="flex items-center gap-2">
                      <span className={cn('lab-verdict', verdict ? `lab-verdict-${verdict}` : null)} aria-hidden>
                        {verdict === 'pass' ? <Icon icon="check" className="size-3" /> : verdict ? <Icon icon="close" className="size-3" /> : null}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-[12px] text-[var(--g100)]">{test.name}</span>
                      {verdict ? (
                        <span className={cn('shrink-0 text-[11px]', verdict === 'pass' ? 'text-[var(--g300)]' : 'font-bold text-[var(--g50)]')}>
                          {tl(verdict === 'pass' ? 'lab.tests.pass' : verdict === 'error' ? 'lab.tests.error' : 'lab.tests.fail')}
                        </span>
                      ) : null}
                      {!readOnly ? (
                        <Button variant="ghost" size="sm" className="h-6 px-1.5 text-[var(--g400)]" onClick={() => setEditing(i)}>
                          {tl('lab.tests.edit')}
                        </Button>
                      ) : null}
                    </div>
                    <span className="num truncate pl-5 text-[11px] text-[var(--g500)]">
                      {JSON.stringify(test.obs)} → {JSON.stringify(test.expect)}
                    </span>
                    {r && !stale ? <ResultLine r={r} /> : null}
                  </>
                )}
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}
