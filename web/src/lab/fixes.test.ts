// Lab and inspector fixes: the program log across deployments, saving while typing, a failed open, the trace
// replay under StrictMode, and the airframe editor across its own refetch.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { createElement } from 'react'
import type { Program, ProgramLogLine, SimAirframe } from '@/api'

vi.mock('@/shell/notify', () => ({ notify: vi.fn(), notifyError: vi.fn() }))

const { labApi } = await import('./api')
const { useLab } = await import('./store')
const { nextLog } = await import('./RunTab')
const { attachReplayBar, useTraceReplay } = await import('./traceReplay')
const { useUi } = await import('@/store/ui')
const { AirframeEditor, airframeShown } = await import('@/inspector/AirframeGroup')

const line = (seq: number, text = `l${seq}`): ProgramLogLine => ({ seq, t: seq, level: 'log', text })

const program = (over: Partial<Program> = {}): Program =>
  ({ id: 'p1', name: 'P', description: '', source: 'a', params: {}, tests: [], rev: 1, builtin: false, revisions: [], ...over }) as unknown as Program

afterEach(() => {
  vi.restoreAllMocks()
  useLab.setState({ program: null, selectedId: null, source: '', params: {}, tests: [], saving: false, loading: false })
})

describe('program log', () => {
  it('appends new lines once and replaces them when the ring restarts', () => {
    let log = { after: 0, lines: [] as ProgramLogLine[] }
    log = nextLog(log, { lines: [line(1), line(2)], last_seq: 2 })
    expect(log.after).toBe(2)
    log = nextLog(log, { lines: [line(2), line(3)], last_seq: 3 }) // an overlapping answer
    expect(log.lines.map((l) => l.seq)).toEqual([1, 2, 3])
    const same = nextLog(log, { lines: [], last_seq: 3 })
    expect(same).toBe(log)
    // a new deployment: its ring starts again below what was read
    log = nextLog(log, { lines: [], last_seq: 1 })
    expect(log).toEqual({ after: 0, lines: [] })
    log = nextLog(log, { lines: [line(1, 'new')], last_seq: 1 })
    expect(log.lines).toEqual([line(1, 'new')])
  })
})

describe('lab store', () => {
  it('keeps what was typed while a save was in flight', async () => {
    const p = program()
    useLab.setState({ program: p, selectedId: 'p1', source: 'b', params: {}, tests: [] })
    let resolve!: (v: unknown) => void
    vi.spyOn(labApi, 'update').mockReturnValue(new Promise((r) => (resolve = r)) as never)
    vi.spyOn(labApi, 'programs').mockResolvedValue({ programs: [] } as never)
    const seq = useLab.getState().loadSeq
    const saving = useLab.getState().save()
    useLab.getState().setSource('bc') // typed during the round trip
    resolve({ program: program({ source: 'b', rev: 2 }), validation: { ok: true, errors: [] } })
    expect(await saving).toBe(true)
    const s = useLab.getState()
    expect(s.source).toBe('bc')
    expect(s.program?.rev).toBe(2)
    expect(s.loadSeq).toBe(seq) // the editor document was not replaced
  })

  it('takes the saved text when nothing changed meanwhile', async () => {
    useLab.setState({ program: program(), selectedId: 'p1', source: 'b', params: {}, tests: [] })
    vi.spyOn(labApi, 'update').mockResolvedValue({ program: program({ source: 'b', rev: 2 }), validation: { ok: true, errors: [] } } as never)
    vi.spyOn(labApi, 'programs').mockResolvedValue({ programs: [] } as never)
    const seq = useLab.getState().loadSeq
    expect(await useLab.getState().save()).toBe(true)
    expect(useLab.getState().loadSeq).toBe(seq + 1)
    expect(useLab.getState().program?.rev).toBe(2)
  })

  it('points the selection back at the open program when another one fails to open', async () => {
    useLab.setState({ program: program(), selectedId: 'p1' })
    vi.spyOn(labApi, 'program').mockRejectedValue(new Error('gone'))
    await useLab.getState().open('p2')
    expect(useLab.getState().selectedId).toBe('p1')
    expect(useLab.getState().loading).toBe(false)
  })
})

describe('trace replay bar', () => {
  const trace = { id: 'tr', duration_s: 3, aircraft: [], shots: [], events: [] } as never
  const flush = () => new Promise<void>((r) => queueMicrotask(r))

  it('survives the StrictMode mount, cleanup, mount and closes on a real unmount', async () => {
    useUi.setState({ workspace: 'fly' })
    useTraceReplay.setState({ trace })
    const first = attachReplayBar()
    first()
    const second = attachReplayBar() // StrictMode mounts again in the same task
    await flush()
    expect(useTraceReplay.getState().trace).toBe(trace)
    second()
    await flush()
    expect(useTraceReplay.getState().trace).toBeNull()
  })

  it('closes when the Fly workspace goes away', () => {
    useUi.setState({ workspace: 'fly' })
    useTraceReplay.setState({ trace })
    const detach = attachReplayBar()
    useUi.setState({ workspace: 'lab' })
    expect(useTraceReplay.getState().trace).toBeNull()
    detach()
  })
})

describe('airframe editor', () => {
  const af = (preset: SimAirframe['preset']): SimAirframe =>
    ({
      id: 'sim-1',
      preset,
      seed: 4,
      params: { mass_kg: 0.12, payload_kg: 0, cg_offset_cm: [0, 0], level_trim_deg: [0, 0], motor_gain: [0, 0, 0, 0], prop_damage: 0, damaged_prop: 1, imu_noise: 0, arm_bias_deg: 0, with_assist: false },
      active: true,
      level_now_deg: [0, 0],
      hover_throttle: 0.5,
      limits: { mass_kg: [0.08, 0.3], payload_kg: [0, 0.2], cg_offset_cm: [-3, 3], level_trim_deg: [-5, 5], motor_gain: [-0.08, 0.08], prop_damage: [0, 1], damaged_prop: [1, 4], imu_noise: [0, 5] },
    }) as SimAirframe

  it('keeps showing our own answer while the poll refetches after the preset turned custom', () => {
    const mine = af('custom')
    const own = { key: 'sim-1|typical:4|false', base: 'sim-1|false', data: mine }
    expect(airframeShown(own, 'sim-1|typical:4|false', 'sim-1|false', af('typical'))).toBe(mine)
    expect(airframeShown(own, 'sim-1|custom:4|false', 'sim-1|false', null)).toBe(mine) // refetching
    const polled = af('custom')
    expect(airframeShown(own, 'sim-1|custom:4|false', 'sim-1|false', polled)).toBe(polled)
    expect(airframeShown(own, 'sim-2|ideal:1|false', 'sim-2|false', null)).toBeNull() // another aircraft
  })

  it('takes the Values section state from outside', () => {
    const html = renderToStaticMarkup(createElement(AirframeEditor, { data: af('custom'), busy: false, send: () => {}, open: true, onOpenChange: () => {} }))
    expect(html).toContain('data-airframe-prop')
    const closed = renderToStaticMarkup(createElement(AirframeEditor, { data: af('custom'), busy: false, send: () => {}, open: false, onOpenChange: () => {} }))
    expect(closed).not.toContain('data-airframe-prop')
  })
})
